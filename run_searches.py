#!/usr/bin/env python3
"""Run the approved, coverage-receipted federal source sweep.

    python3 run_searches.py --client "Acme Federal Solutions LLC" \
        --posted-from 09/01/2024 --posted-to 09/30/2024

The standard source catalog defines the structured procurement, award,
forecast, program, budget, oversight, and public-web lanes. Every run emits
source-coverage and decision-coverage verdicts. A partial or stale required
lane cannot be presented as comprehensive.

Output: data/cleaned/searches_<client>.json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()  # pick up SAM_GOV_API_KEY / ANTHROPIC_API_KEY from .env

from agents.review import (  # noqa: E402
    WorkstationBindingError, load_packet_snapshot, packet_binding_lock,
)
from tools.api import SourceQuery  # noqa: E402
from tools.api.sam_gov import SamGovSource  # noqa: E402
from tools.api.source_catalog import (  # noqa: E402
    RESULT_KEY_FOR_TASK,
    SOURCE_BY_IDENTITY,
    validate_standard_tasks,
)
from tools.api.usaspending import UsaSpendingSource  # noqa: E402
from tools.api.web_search import WebSearchSource  # noqa: E402


_SAM_ATTACHMENT_CANDIDATE_HARD_CAP = 12
# The metered ceiling for the attachment stage, and the only bound that is
# allowed to be load-bearing. The SAM pool is 20 calls/day across the
# configured keys and that is permanent; a press must fit inside it twice
# over. 2 leaves room for the 4-call live-search fallback and still lands a
# whole press at 8, so two presses fit in 20 with headroom.
_SAM_ATTACHMENT_CALL_HARD_CAP = 2
_SAM_ATTACHMENT_STAGE_HARD_SECONDS = 180


# ``--skip`` names runner tasks, while several tasks persist under a different
# result-lane key.  Keep this map beside the parser so refresh orchestration can
# preserve an intentionally skipped lane without knowing collector internals.
_RESULT_KEY_FOR_SKIP = {
    **RESULT_KEY_FOR_TASK,
    "picture": "research_picture",
}

# NOT-APPLICABLE IS NOT SUCCESS (2026-07-30). A lane whose entire input is
# the engagement's agency scope has nothing to gather when that scope is
# empty; running it anyway reported "0 sub-organizations across 0 agencies"
# with ok=True, and the client-facing coverage band called that RETURNED.
# A thunk returns this sentinel as its summary to say "structurally nothing
# to do": the result payload lands (with its own not_applicable note), NO
# attempt row is written, and the coverage projection renders the absent
# attempt as NOT RUN THIS REFRESH - which is the truth. The projection's
# fixed status vocabulary is a contract surface; absence is the one honest
# state it already knows how to say.
LANE_NOT_APPLICABLE = "__lane_not_applicable__"


def _preserve_skipped_results(
    previous: dict,
    refreshed: dict,
    skipped: list[str],
) -> list[str]:
    """Copy intentionally skipped result lanes from the prior sweep.

    The new sweep remains the owner of every lane it actually ran.  Only keys
    named by ``--skip`` are carried forward, byte-for-value at the decoded JSON
    boundary.  The return value is the stable list of result keys preserved.
    """
    preserved: list[str] = []
    for task in skipped:
        key = _RESULT_KEY_FOR_SKIP.get(task, task)
        if key in previous and key not in refreshed:
            refreshed[key] = copy.deepcopy(previous[key])
            preserved.append(key)
    return preserved


def _payload_error(value) -> str | None:
    if isinstance(value, dict) and value.get("error"):
        return str(value["error"])
    if isinstance(value, dict):
        from tools.api.provenance import provenance_from_payload
        envelope = provenance_from_payload(value, source="runner-attempt")
        if envelope is not None and envelope.status == "failed":
            return "source provenance reports failed"
    return None


def _payload_partial(value) -> bool:
    if not isinstance(value, dict):
        return False
    from tools.api.provenance import provenance_from_payload
    envelope = provenance_from_payload(value, source="runner-attempt")

    def _nonempty_marker(key: str) -> bool:
        marker = value.get(key)
        if isinstance(marker, bool):
            return marker
        if isinstance(marker, (int, float)):
            return marker > 0
        return bool(marker)

    return bool(
        value.get("partial") is True
        or value.get("complete") is False
        or (
            value.get("truncated") is True
            and value.get("boundary_complete") is not True
        )
        or value.get("errors")
        or any(_nonempty_marker(key) for key in (
            "skipped_terms",
            "omitted_terms",
            "failed_terms",
            "omitted_naics_lanes",
            "failed_naics_lanes",
            "timed_out",
        ))
        or (envelope is not None and envelope.status == "partial")
    )


def _enrich_sam_public_attachments(sam: list[dict], source, query,
                                   taxonomy, engagement_scope,
                                   *, limit: int) -> tuple[list[dict], dict]:
    """Add bounded public requirements-file evidence to SAM candidates."""
    from tools.api.sam_attachment_text import (
        attachment_priority, extract_public_attachment_text)
    from tools.api.sam_notice_detail import fetch_notice_resources
    from tools.relevance.engine import score_record

    try:
        requested_seconds = int(os.environ.get(
            "LILA_SAM_ATTACHMENT_STAGE_SECONDS", "120"))
    except ValueError:
        requested_seconds = 120
    stage_seconds = min(
        _SAM_ATTACHMENT_STAGE_HARD_SECONDS, max(1, requested_seconds))
    stop_at = time.monotonic() + stage_seconds
    # A HARD CALL CAP, and it is the binding one. The time budget stays as a
    # secondary bound but it cannot bound SPEND: a fast network drains more
    # quota in 120 seconds than a slow one, so wall clock made the worst case
    # unknowable. This stage is the only one in the pipeline that can drain
    # the pool without anyone choosing to.
    #
    # The pool is 20 calls a day, permanently. Counting is done against the
    # real ledger rather than against loop iterations, so a cache hit costs
    # nothing and the number below is calls ACTUALLY SPENT, not calls
    # attempted.
    from tools.api import sam_quota
    calls_at_start = sam_quota.calls_today()
    call_cap = _SAM_ATTACHMENT_CALL_HARD_CAP
    try:
        call_cap = min(call_cap, max(0, int(os.environ.get(
            "LILA_SAM_ATTACHMENT_CALL_CAP", str(call_cap)))))
    except ValueError:
        pass
    candidates = source.attachment_candidates(
        query, taxonomy, engagement_scope,
        limit=min(limit, _SAM_ATTACHMENT_CANDIDATE_HARD_CAP))
    original_ids = {str(row.get("source_id") or "") for row in sam}
    by_id = {str(row.get("source_id") or ""): row for row in sam}
    supported = {".pdf", ".docx", ".txt", ".csv", ".md"}
    enriched = relevant = added = 0
    errors: list[str] = []
    for candidate in candidates:
        spent = sam_quota.calls_today() - calls_at_start
        if spent >= call_cap:
            errors.append(
                f"attachment stage reached its {call_cap}-call hard cap "
                f"({spent} SAM calls spent); remaining candidates skipped")
            break
        if time.monotonic() >= stop_at:
            errors.append(
                f"attachment stage reached its {stage_seconds}s hard budget")
            break
        try:
            resources = fetch_notice_resources(
                candidate.source_id,
                timeout_seconds=max(0.25, stop_at - time.monotonic()))
            # An unavailable inventory is not a verified zero-file result.
            # Preserve the adapter's receipt before either early return so
            # the saved sweep explains why file requirements were not inspected.
            resource_errors = resources.get("errors") or []
            errors.extend(
                f"{candidate.source_id}: {str(error)[:240]}"
                for error in resource_errors[:10])
            if resources.get("stale_cache") is True:
                errors.append(
                    f"{candidate.source_id}: stale attachment inventory "
                    "was not used for relevance")
                continue
            if (resources.get("resources_checked") is False
                    or resources.get("attachments") is None):
                if not resource_errors:
                    errors.append(
                        f"{candidate.source_id}: attachment inventory was not "
                        "verified; no attachment search performed")
                continue
            attachments = sorted(
                resources.get("attachments") or [], key=attachment_priority)
            attachments = [
                item for item in attachments
                if os.path.splitext(str(item.get("name") or ""))[1].lower()
                in supported
            ][:3]
            evidence = []
            texts = []
            for attachment in attachments:
                if time.monotonic() >= stop_at:
                    errors.append(
                        f"attachment stage reached its {stage_seconds}s hard budget")
                    break
                extracted = extract_public_attachment_text(
                    attachment, deadline_monotonic=stop_at)
                if extracted.get("text"):
                    texts.append(str(extracted["text"]))
                    evidence.append({
                        key: extracted.get(key)
                        for key in ("resource_id", "name", "sha256",
                                    "retrieved_at", "source_url")
                    })
                elif extracted.get("error"):
                    errors.append(
                        f"{candidate.source_id}/{attachment.get('name')}: "
                        f"{extracted['error']}")
            if not texts:
                continue
            row = json.loads(candidate.model_dump_json())
            raw = row.setdefault("raw_payload", {})
            combined_text = "\n\n".join(texts)[:150000]
            raw["text"] = combined_text
            raw["attachment_evidence"] = evidence
            raw["attachment_inventory_hash"] = resources.get(
                "attachment_inventory_hash")
            fingerprint_basis = {
                "files": sorted(
                    [{"resource_id": str(item.get("resource_id") or ""),
                      "sha256": str(item.get("sha256") or "")}
                     for item in evidence],
                    key=lambda item: (item["resource_id"], item["sha256"])),
                "text_sha256": hashlib.sha256(
                    combined_text.encode("utf-8")).hexdigest(),
            }
            raw["attachment_evidence_sha256"] = hashlib.sha256(
                json.dumps(
                    fingerprint_basis, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            verdict = score_record(
                row, taxonomy, engagement_scope=engagement_scope)
            raw["attachment_relevance_excerpt"] = "\n…\n".join(dict.fromkeys(
                span.context.strip()
                for span in verdict.spans
                if span.field == "raw_payload.text" and span.context.strip()
            ))[:1200]
            raw["attachment_relevance"] = {
                "score": verdict.score,
                "relevant": verdict.relevant,
                "core_terms": verdict.core_terms,
                "adjacent_terms": verdict.adjacent_terms,
                "excluded_by_code": verdict.excluded_by_code,
                "scope_basis": verdict.scope_basis,
            }
            enriched += 1
            relevant += int(verdict.relevant)
            if verdict.relevant:
                source_id = str(row.get("source_id") or "")
                by_id[source_id] = row
                added += int(source_id not in original_ids)
                if relevant >= 3:
                    break
        except Exception as exc:  # noqa: BLE001 - one file never sinks breadth
            errors.append(f"{candidate.source_id}: {str(exc)[:240]}")
    rows = sorted(by_id.values(), key=lambda row: (
        str(row.get("source_id") or "")))
    return rows, {
        "matched": len(rows),
        "description_matched": len(sam),
        "attachment_candidates": source.last_attachment_census,
        "attachment_enriched": enriched,
        "attachment_relevant": relevant,
        "attachment_added": added,
        "attachment_sam_calls": sam_quota.calls_today() - calls_at_start,
        "attachment_call_cap": call_cap,
        "attachment_errors": errors[:10],
    }


def _safe_enrich_sam_public_attachments(
        sam: list[dict], source, query, taxonomy, engagement_scope,
        *, limit: int) -> tuple[list[dict], dict]:
    """An additive depth failure must never discard a complete SAM census."""
    try:
        return _enrich_sam_public_attachments(
            sam, source, query, taxonomy, engagement_scope, limit=limit)
    except Exception as exc:  # noqa: BLE001 - preserve successful breadth
        return sam, {
            "matched": len(sam),
            "description_matched": len(sam),
            "attachment_candidates": dict(
                getattr(source, "last_attachment_census", None) or {}),
            "attachment_enriched": 0,
            "attachment_relevant": 0,
            "attachment_added": 0,
            "attachment_errors": [f"attachment stage failed: {str(exc)[:240]}"],
        }


def _load_program_engagement_scope(client_name: str):
    """Load the sanctioned client boundary once, before any source fan-out."""
    from tools.relevance.scope import load_engagement_scope

    try:
        return load_engagement_scope(client_name)
    except Exception as exc:  # noqa: BLE001 - normalize config/parser failures
        raise RuntimeError(
            f"invalid engagement scope for {client_name!r}: {exc}"
        ) from exc


def _scope_basis(scope) -> str:
    return scope.resolved_basis if scope is not None else "UNSCOPED"


def _program_policy_exclusion(spec, engagement_scope):
    """Return a no-pull payload when policy cannot serve this boundary."""
    if spec.scope_policy == "engagement-scope":
        return None
    if spec.scope_policy != "defense-only":
        raise RuntimeError(
            f"unsupported PROGRAM scope policy for {spec.identity}: "
            f"{spec.scope_policy!r}"
        )

    from tools.relevance.scope import in_scope

    allowed, basis = in_scope(
        {"agency": "Department of Defense"}, engagement_scope)
    # A subtier-only boundary may intentionally name a defense component.
    # It is not safe to reject that adapter without evaluating returned rows.
    if allowed or (
            engagement_scope is not None and engagement_scope.subtiers):
        return None

    from tools.api.provenance import make_provenance_envelope

    detail = f"Not retrieved; excluded by engagement scope ({basis})"
    envelope = make_provenance_envelope(
        spec.identity,
        status="complete",
        mode="scope-excluded",
        retrieval_mode="unknown",
        attempts=[{"source": spec.identity, "status": "not-run"}],
        public_detail=detail,
    )
    return {
        "records": [],
        "_provenance": envelope.model_dump(mode="json"),
        "scope": {
            "policy": spec.scope_policy,
            "basis": basis,
            "excluded_before_retrieval": True,
            "records_retrieved": None,
        },
    }


def _scoped_program_payload(
    identity: str,
    source,
    query: SourceQuery,
    engagement_scope,
) -> dict:
    """Apply source-family policy, then the sanctioned record-level gate."""
    from tools.api.source_catalog import source_spec
    from tools.relevance.scope import in_scope, resolve_department

    spec = source_spec(identity)
    excluded = _program_policy_exclusion(spec, engagement_scope)
    if excluded is not None:
        return excluded

    payload = source.enrich(query)
    if not isinstance(payload, dict):
        raise TypeError(f"{identity} PROGRAM adapter returned a non-object")
    records = payload.get("records")
    if not isinstance(records, list):
        raise TypeError(f"{identity} PROGRAM adapter returned no records list")

    included = []
    excluded_count = 0
    unresolved_excluded = 0
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise TypeError(
                f"{identity} PROGRAM record {index} is not an object")
        inside, basis = in_scope(row, engagement_scope)
        if (
            inside
            and engagement_scope is not None
            and not engagement_scope.unbounded
            and resolve_department(row, engagement_scope) is None
        ):
            inside = False
            unresolved_excluded += 1
            basis = (
                f"{engagement_scope.resolved_basis} · "
                "off-scope:unresolved-agency"
            )
        if not inside:
            excluded_count += 1
            continue
        included.append({**row, "scope_basis": basis})

    scoped = dict(payload)
    scoped["records"] = included
    scoped["scope"] = {
        "policy": spec.scope_policy,
        "basis": _scope_basis(engagement_scope),
        "records_evaluated": len(records),
        "records_included": len(included),
        "records_excluded": excluded_count,
        "records_unresolved_excluded": unresolved_excluded,
        "excluded_before_retrieval": False,
    }
    raw_provenance = payload.get("_provenance")
    provenance = dict(raw_provenance) if isinstance(raw_provenance, dict) else {}
    provenance.update({
        "scope_policy": spec.scope_policy,
        "scope_basis": _scope_basis(engagement_scope),
        "records_scope_evaluated": len(records),
        "records_scope_included": len(included),
        "records_scope_excluded": excluded_count,
        "records_scope_unresolved_excluded": unresolved_excluded,
    })
    detail_parts = []
    existing_detail = str(provenance.get("public_detail") or "").strip()
    if existing_detail:
        detail_parts.append(existing_detail)
    if provenance.get("truncated") is True:
        candidate_total = provenance.get("candidate_total")
        selected_count = provenance.get("selected_count")
        selection_order = str(
            provenance.get("selection_order") or "").strip()
        if (
            type(candidate_total) is not int
            or type(selected_count) is not int
            or selected_count != len(records)
            or not 0 <= selected_count < candidate_total
            or not selection_order
        ):
            raise ValueError(
                f"{identity} truncated PROGRAM provenance is inconsistent"
            )
        detail_parts.append(
            f"Selection cap: selected {selected_count} of {candidate_total} "
            f"matched records · ranking basis: {selection_order}"
        )
    if excluded_count:
        scope_detail = (
            f"Engagement scope retained {len(included)} of "
            f"{len(records)} matched records"
        )
        detail_parts.append(scope_detail)
    if detail_parts:
        provenance["public_detail"] = " · ".join(detail_parts)
    scoped["_provenance"] = provenance
    return scoped


def attempt_row(
    name: str,
    value,
    summary,
    dt: float,
    err,
    *,
    partial_override: bool = False,
) -> dict:
    """One source-attempt manifest row (P3): countable payloads counted,
    failures carried with their error text, wall time always present.

    The fan-out owns isolation, but a few adapters deliberately return an
    ``error`` or ``errors`` payload instead of raising.  Normalize those
    payloads here so the public coverage projection cannot call a soft failure
    a successful return.  A partial payload remains usable (``ok=True``) and is
    marked separately; one imperfect lane still never sinks the fleet.
    """
    count = None
    if isinstance(value, list):
        count = len(value)
    elif isinstance(value, dict):
        for k in ("items", "rows", "records", "buyers", "notices", "matched"):
            if isinstance(value.get(k), list):
                count = len(value[k])
                break
    envelope = None
    if isinstance(value, dict):
        from tools.api.provenance import provenance_from_payload
        envelope = provenance_from_payload(value, source=name)
    scope_excluded = bool(
        envelope is not None and envelope.mode == "scope-excluded")
    if scope_excluded:
        count = None
    soft_error = _payload_error(value) if err is None else None
    partial = bool(
        err is None
        and (_payload_partial(value) or partial_override)
    )
    failure = err if err is not None else soft_error
    row = {"source": name, "ok": failure is None,
           "elapsed_s": round(dt, 1)}
    if scope_excluded and failure is None:
        row["scope_excluded"] = True
    if partial and failure is None:
        row["partial"] = True
    if count is not None:
        row["count"] = count
    if failure is None and summary:
        row["summary"] = str(summary)[:200]
    if failure is not None:
        row["error"] = str(failure)[:200]
    if envelope is not None:
        row["provenance"] = envelope.public_projection()
    spec = SOURCE_BY_IDENTITY.get(name)
    if spec is not None and spec.coverage_class == "required-bounded":
        returned_origins = {
            attempt.source
            for attempt in (envelope.attempts if envelope is not None else ())
            if attempt.status == "success"
        }
        # A single-source bounded collector is itself the declared origin.  A
        # clean return attests that its documented query/cap boundary ran.  A
        # multi-source family must enumerate its child origins in provenance.
        required_origins = set(spec.coverage_origins)
        if (len(required_origins) == 1 and failure is None and not partial
                and not returned_origins.intersection(required_origins)):
            returned_origins.add(spec.coverage_origins[0])
        row["coverage_contract"] = {
            "required_origins": list(spec.coverage_origins),
            "returned_origins": sorted(
                returned_origins.intersection(required_origins)),
            "complete_within_boundary": bool(
                required_origins
                and required_origins.issubset(returned_origins)
                and failure is None
            ),
        }
    return row


def _sam_census_receipt(census) -> dict:
    """Public-safe proof that the list-shaped SAM lane screened its boundary."""
    census = census if isinstance(census, dict) else {}
    manifest = census.get("attempt_manifest")
    manifest = manifest if isinstance(manifest, dict) else {}

    def _count(key: str) -> int:
        rows = manifest.get(key)
        return len(rows) if isinstance(rows, list) else 0

    receipt = {
        "complete": census.get("complete") is True,
        "source": str(census.get("source") or "unidentified"),
        "stale_cache": max(0, int(census.get("stale_cache") or 0)),
        "requested_passes": _count("requested"),
        "executed_passes": _count("executed"),
        "omitted_passes": _count("omitted"),
    }
    for key in ("matched", "active_screened", "retrieved"):
        value = census.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            receipt[key] = value
    return receipt


def _usaspending_evidence_receipt(evidence) -> dict:
    """Surface additive USAspending failures hidden inside list-shaped rows.

    ``market_evidence`` intentionally preserves a useful award bundle when an
    agency aggregate or keyword-addressable query fails.  The sweep manifest
    must still call that lane partial, otherwise a list return can mint a false
    comprehensive verdict.
    """
    rows = evidence if isinstance(evidence, list) else []
    breakdown_failures = 0
    addressable_failures = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        breakdown = row.get("agency_breakdown")
        if isinstance(breakdown, list) and any(
            isinstance(item, dict) and bool(item.get("error"))
            for item in breakdown
        ):
            breakdown_failures += 1
        addressable = row.get("addressable")
        if isinstance(addressable, dict) and addressable.get("error"):
            addressable_failures += 1
    return {
        "complete": bool(rows)
        and breakdown_failures == 0
        and addressable_failures == 0,
        "lanes_returned": len(rows),
        "agency_breakdown_failures": breakdown_failures,
        "addressable_failures": addressable_failures,
    }


def _empty_forecast_family_result(
    source_attempts: list[dict],
    sources_off: list[str],
) -> tuple[dict, str] | None:
    """Name an empty forecast family only when no adapter returned.

    An intentionally all-disabled family is OFF. If one or more adapters were
    enabled and every one failed, the lane is a named failure. A successful or
    partial zero-row screen is valid and continues through normal provenance.
    """
    enabled = [
        row for row in source_attempts if row.get("status") != "not-run"
    ]
    if not enabled:
        return (
            {"disabled": True, "sources_off": list(sources_off)},
            "OFF by configuration (all forecast adapters disabled)",
        )
    if all(row.get("status") == "failed" for row in enabled):
        from tools.api.provenance import make_provenance_envelope

        detail = "All enabled agency forecast adapters failed"
        envelope = make_provenance_envelope(
            "forecasts",
            status="failed",
            mode="multi-source-forecast",
            retrieval_mode="unknown",
            attempts=source_attempts,
            public_detail=detail,
        )
        return ({
            "error": detail,
            "disabled": False,
            "sources_off": list(sources_off),
            "_provenance": envelope.model_dump(mode="json"),
        }, detail)
    return None


def _forecast_snapshot_advances_change_store(provenance: dict) -> bool:
    """Incomplete censuses may inform this run but never create disappearances."""

    return provenance.get("complete") is not False


def _forecast_family_advances_client_snapshot(
    source_attempts: list[dict],
) -> bool:
    """Only a complete family census may replace the client baseline.

    A client snapshot is a cross-source set.  Advancing it when even one
    enabled adapter failed, was disabled, or returned a partial census turns
    omitted records into false ``disappeared`` signals on the next diff.
    The current partial evidence remains usable in the sweep; only the state
    transition is withheld.
    """

    contract = _forecast_contract_receipt(source_attempts)
    return bool(source_attempts) and bool(
        contract["complete_within_boundary"]
    ) and all(
        row.get("status") == "success" and row.get("stale") is not True
        for row in source_attempts
    )


def _forecast_child_status(provenance: dict) -> str:
    """Normalize one adapter's receipt without treating stale as fresh."""

    status = str(provenance.get("status") or "").strip().casefold()
    if status in {"failed", "failure", "error"}:
        return "failed"
    if status in {"partial", "incomplete"} or provenance.get("complete") is False:
        return "partial"
    return "success"


def _forecast_partial_provenance(
    provenance: dict,
    limitation: str,
) -> dict:
    """Turn one usable-but-incomplete child result into an explicit receipt."""

    updated = dict(provenance)
    limitations = _forecast_limitations(updated)
    if limitation not in limitations:
        limitations.append(limitation)
    updated.update({
        "status": "partial",
        "complete": False,
        "limitations": limitations,
        "limitation": "; ".join(limitations),
    })
    return updated


def _collect_forecast_child(
    source,
    *,
    profile,
    capability_terms: list[str],
    taxonomy,
    engagement_scope,
    scope_agencies: list[dict],
) -> dict:
    """Collect and screen one forecast origin without sinking its siblings.

    Materialization is deliberately inside the retrieval guard: an adapter may
    return a lazy iterable whose network error is raised only by ``list()``.
    Optional detail enrichment is additive, so a failure there retains the
    official listing rows but marks the child partial.  A scope or preliminary
    scoring failure cannot safely retain that child's rows, but it likewise
    cannot discard evidence already returned by other official origins.
    """

    from tools.api.forecasts import match_forecasts
    from tools.agencies import matches_record as matches_scope_agency

    try:
        market_rows = list(source.forecasts())
    except Exception as exc:  # noqa: BLE001 - one origin never sinks the family
        return {
            "market_rows": [],
            "rows": [],
            "events": [],
            "status": "failed",
            "error": str(exc)[:200],
            "provenance": {
                "source": str(getattr(source, "name", "forecast-origin")),
                "mode": "failed",
                "status": "failed",
                "complete": False,
                "limitations": [
                    "Official forecast retrieval failed during this sweep."
                ],
            },
        }

    provenance = getattr(source, "last_provenance", None)
    provenance = provenance if isinstance(provenance, dict) else {}
    child_status = _forecast_child_status(provenance)
    if child_status == "failed":
        return {
            "market_rows": market_rows,
            "rows": [],
            "events": [],
            "status": "failed",
            "error": "adapter provenance reports failed",
            "provenance": provenance,
        }

    rows = list(market_rows)
    if scope_agencies:
        try:
            rows = [
                row
                for row in rows
                if any(
                    matches_scope_agency(
                        " ".join(
                            str(value or "")
                            for value in (
                                getattr(row, "agency", None),
                                getattr(row, "component", None),
                                getattr(row, "office", None),
                            )
                        ),
                        selected,
                    )
                    for selected in scope_agencies
                )
            ]
        except Exception as exc:  # noqa: BLE001 - fail this child closed
            detail = (
                "Forecast agency-scope screening failed; this origin's rows "
                "were withheld from client signals."
            )
            return {
                "market_rows": market_rows,
                "rows": [],
                "events": [],
                "status": "partial",
                "error": str(exc)[:200],
                "provenance": _forecast_partial_provenance(
                    provenance, detail
                ),
            }

    enrich_matched = getattr(source, "enrich_matched", None)
    if callable(enrich_matched) and rows:
        try:
            preliminary = match_forecasts(
                rows,
                profile,
                keywords=capability_terms,
                taxonomy=taxonomy,
                engagement_scope=engagement_scope,
            )
        except Exception as exc:  # noqa: BLE001 - fail this child closed
            detail = (
                "Forecast capability pre-screening failed; this origin's "
                "rows were withheld from client signals."
            )
            return {
                "market_rows": market_rows,
                "rows": [],
                "events": [],
                "status": "partial",
                "error": str(exc)[:200],
                "provenance": _forecast_partial_provenance(
                    provenance, detail
                ),
            }
        preliminary_rows = [item["record"] for item in preliminary]
        try:
            enriched = list(enrich_matched(preliminary_rows))
        except Exception as exc:  # noqa: BLE001 - base listing remains useful
            detail = (
                "Optional official forecast detail enrichment failed; "
                "official listing rows were retained."
            )
            provenance = _forecast_partial_provenance(provenance, detail)
            child_status = "partial"
            enrichment_error = str(exc)[:200]
        else:
            replacements = {
                (row.source, row.source_id): row for row in enriched
            }
            rows = [
                replacements.get((row.source, row.source_id), row)
                for row in rows
            ]
            enrichment_error = None
    else:
        enrichment_error = None

    return {
        "market_rows": market_rows,
        "rows": rows,
        "events": [],
        "status": child_status,
        "error": enrichment_error,
        "provenance": provenance,
    }


def _forecast_limitations(provenance: dict) -> list[str]:
    """Return stable, de-duplicated public limitations from legacy or v1 receipts."""

    raw = provenance.get("limitations")
    values = list(raw) if isinstance(raw, (list, tuple)) else []
    singular = provenance.get("limitation")
    if singular:
        values.append(singular)
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _forecast_source_summary(
    identity: str,
    records: list,
    available_records: list,
    provenance: dict,
    *,
    contract_origins: set[str],
    status: str | None = None,
) -> dict:
    """Project exact retrieval evidence and the durable landing route per child."""

    from tools.api.source_catalog import (
        forecast_source_label, forecast_source_url, source_spec,
    )

    official_url = forecast_source_url(identity)
    exact_url = str(provenance.get("source_url") or official_url)
    landing_url = str(provenance.get("landing_url") or official_url or exact_url)
    try:
        spec = source_spec(identity)
        boundary = spec.coverage_description or spec.coverage_boundary
    except KeyError:
        boundary = ""
    data_as_of = max(
        (str(row.data_as_of) for row in records if row.data_as_of),
        default=(provenance.get("data_as_of") or None),
    )
    child_status = status or _forecast_child_status(provenance)
    return {
        "source": identity,
        "label": forecast_source_label(identity),
        # ``url`` remains the backwards-compatible public navigation target.
        # The exact fetched artifact is retained separately for verification.
        "url": landing_url,
        "landing_url": landing_url,
        "source_url": exact_url,
        "record_count": len(records),
        "available_count": len(available_records),
        "retrieved_at": provenance.get("retrieved_at"),
        "data_as_of": data_as_of,
        "mode": str(provenance.get("mode") or "unknown"),
        "status": child_status,
        "complete": child_status == "success",
        "stale": bool(
            provenance.get("stale") is True
            or "stale" in str(provenance.get("mode") or "").casefold()
        ),
        "fallback": bool(provenance.get("fallback") is True),
        "contract_origin": identity in contract_origins,
        "coverage_boundary": boundary,
        "public_detail": provenance.get("public_detail"),
        "limitations": _forecast_limitations(provenance),
    }


def _forecast_contract_receipt(source_attempts: list[dict]) -> dict:
    """Evaluate the declared default forecast boundary from child attempts."""

    from tools.api.source_catalog import source_spec

    spec = source_spec("forecasts")
    statuses = {
        str(row.get("source") or ""): str(row.get("status") or "")
        for row in source_attempts
    }
    required = list(spec.coverage_origins)
    missing = [origin for origin in required if statuses.get(origin) != "success"]
    return {
        "coverage_class": spec.coverage_class,
        "boundary": spec.coverage_boundary,
        "required_origins": required,
        "returned_origins": [
            origin for origin in required if statuses.get(origin) == "success"
        ],
        "incomplete_origins": missing,
        "complete_within_boundary": not missing,
    }


def _spec(strategy, source):
    for s in strategy.searches:
        if s.source == source:
            return s
    return None


def _parse_date(s: str):
    return datetime.strptime(s, "%m/%d/%Y").date()


def _decision_coverage_receipt(
    notices: list[dict],
    verdicts: dict[str, dict],
    *,
    prefilter_receipt: dict | None = None,
) -> dict:
    """Reconcile one explicit decision to every harvested SAM notice.

    Source coverage and decision coverage answer different questions.  The
    former proves which federal surfaces were screened; this receipt proves
    that every notice in the resulting SAM candidate census reached a named
    disposition.  Unknown verdict vocabulary and model ``unscreened`` rows
    fail closed instead of allowing a partial decision pass to look complete.
    """
    notice_ids = []
    for row in notices:
        if not isinstance(row, dict):
            continue
        raw = row.get("raw_payload")
        raw_notice_id = raw.get("notice_id") if isinstance(raw, dict) else None
        notice_ids.append(
            str(row.get("source_id") or raw_notice_id or "").strip()
        )
    notice_ids = [value for value in notice_ids if value]
    expected = set(notice_ids)
    actual = set(verdicts)
    allowed = {"pursue", "monitor", "discard"}
    counts: dict[str, int] = {}
    invalid_ids: list[str] = []
    for source_id, row in verdicts.items():
        verdict = str(row.get("verdict") or "") if isinstance(row, dict) else ""
        counts[verdict or "missing"] = counts.get(verdict or "missing", 0) + 1
        if verdict not in allowed:
            invalid_ids.append(source_id)
    missing_ids = sorted(expected - actual)
    unexpected_ids = sorted(actual - expected)
    duplicate_notice_ids = len(notice_ids) - len(expected)
    prefilter_complete = (
        not isinstance(prefilter_receipt, dict)
        or prefilter_receipt.get("complete") is True
    )
    complete = bool(
        len(notice_ids) == len(notices)
        and duplicate_notice_ids == 0
        and not missing_ids
        and not unexpected_ids
        and not invalid_ids
        and prefilter_complete
    )
    return {
        "verdict": "complete" if complete else "incomplete",
        "complete": complete,
        "candidate_census": len(notices),
        "identified_candidates": len(notice_ids),
        "dispositioned": len(actual & expected),
        "counts": counts,
        "missing_count": len(missing_ids),
        "unexpected_count": len(unexpected_ids),
        "invalid_or_unscreened_count": len(invalid_ids),
        "duplicate_notice_id_count": duplicate_notice_ids,
        "prefilter_complete": prefilter_complete,
        # Bounded examples make failures diagnosable without allowing a large
        # exception list to bloat every sweep artifact.
        "missing_examples": missing_ids[:20],
        "unexpected_examples": unexpected_ids[:20],
        "invalid_or_unscreened_examples": sorted(invalid_ids)[:20],
    }


def main() -> int:
    from datetime import date, timedelta

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--posted-from", help="MM/DD/YYYY (SAM; default: 364 days back)")
    ap.add_argument("--posted-to", help="MM/DD/YYYY (SAM; default: today)")
    ap.add_argument("--skip", nargs="*", default=[], help="steps to skip: any source name, plus 'triage' and 'picture'")
    ap.add_argument(
        "--preserve-skipped",
        action="store_true",
        help="carry intentionally skipped lanes forward from the prior "
             "designated sweep",
    )
    args = ap.parse_args()

    # HARD GATE: raises unless a human approved this client's strategy.
    bound_workstation = os.environ.get("LILA_EXPECT_WORKSTATION_ID")
    native_flag = os.environ.get("LILA_EXPECT_NATIVE_WORKSTATION")
    if native_flag not in (None, "0", "1"):
        raise WorkstationBindingError(
            "invalid native-workstation binding in child environment")
    if bound_workstation and native_flag is None:
        raise WorkstationBindingError(
            "workstation binding has no owner-mode flag in child environment")
    native_workstation = native_flag == "1"
    if native_workstation and not bound_workstation:
        raise WorkstationBindingError(
            "native workstation binding has no workstation id")
    expected_packet_sha = os.environ.get("LILA_EXPECT_PACKET_SHA256")
    expected_revision = os.environ.get("LILA_EXPECT_STRATEGY_REVISION")
    if (expected_packet_sha is None) != (expected_revision is None):
        raise WorkstationBindingError(
            "incomplete strategy fingerprint in child environment")
    if native_workstation and expected_packet_sha is None:
        raise WorkstationBindingError(
            "native workstation binding has no strategy fingerprint")
    run_snapshot = load_packet_snapshot(
        args.client, workstation_id=bound_workstation)
    run_packet = run_snapshot.packet
    if not run_packet.is_approved:
        raise PermissionError(
            f"strategy for {args.client!r} is no longer approved")
    run_packet_sha = run_snapshot.sha256 if native_workstation else None
    if expected_packet_sha is not None:
        if (len(expected_packet_sha) != 64
                or any(char not in "0123456789abcdef"
                       for char in expected_packet_sha)):
            raise WorkstationBindingError(
                "invalid strategy fingerprint in child environment")
        try:
            expected_revision_value = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise WorkstationBindingError(
                "invalid strategy revision in child environment") from exc
        if (run_snapshot.sha256 != expected_packet_sha
                or run_packet.revision_count != expected_revision_value):
            raise WorkstationBindingError(
                "strategy packet changed after job authorization")
    strategy = run_packet.strategy
    run_revision = run_packet.revision_count
    run_scope = run_packet.search_scope

    prior_results: dict = {}
    if args.preserve_skipped and args.skip:
        from agents.review import sweep_artifact_path
        try:
            prior_path = sweep_artifact_path(args.client)
        except FileNotFoundError as exc:
            print(f"[preserve] no prior designated sweep: {exc}",
                  file=sys.stderr)
        else:
            if os.path.exists(prior_path):
                with open(prior_path, encoding="utf-8") as prior_file:
                    prior_sweep = json.load(prior_file)
                if (not isinstance(prior_sweep, dict)
                        or not isinstance(prior_sweep.get("results"), dict)):
                    raise ValueError(
                        "prior designated sweep has no results object; "
                        "cannot preserve skipped lanes")
                prior_results = prior_sweep["results"]
            else:
                print("[preserve] no prior designated sweep; skipped lanes "
                      "start empty", file=sys.stderr)
    # HARD GATE (2026-07-09 inversion): no profile, no sweep. A NAICS-only
    # sweep produces client-invariant findings; capability terms are the
    # PRIMARY evidence layer and must exist before any quota is spent.
    from tools.capability import require_profile
    profile = require_profile(args.client)
    print(f"[profile] capability profile loaded: "
          f"{len(profile.capability_terms.core)} core / "
          f"{len(profile.capability_terms.adjacent)} adjacent terms, "
          f"{len(profile.named_competitors_and_incumbents)} named products, "
          f"{len(profile.mission_components)} mission components",
          file=sys.stderr)
    # Existing approval is retained as audit history. Its content binding makes
    # it automatically ineligible once the new sweep is durably committed.
    print(f"[gate] approved strategy loaded for {args.client}", file=sys.stderr)
    try:
        program_engagement_scope = _load_program_engagement_scope(args.client)
    except RuntimeError as exc:
        print(f"[scope:failure] {exc}", file=sys.stderr)
        return 2
    print(
        "[scope] PROGRAM engagement boundary: "
        f"{_scope_basis(program_engagement_scope)}",
        file=sys.stderr,
    )
    from tools.relevance.scope import load_engagement_scope
    from tools.relevance.taxonomy import derived_taxonomy, load_taxonomy
    curated_taxonomy = load_taxonomy(args.client)
    sam_taxonomy = curated_taxonomy or derived_taxonomy(args.client)
    # A curated taxonomy carries explicit evidence and false-positive rules,
    # so it may strictly gate forecast promotion.  Until a client has that
    # reviewed artifact, retain the approved strategy keyword path instead of
    # silently narrowing historical forecast behavior.
    forecast_taxonomy = curated_taxonomy
    sam_engagement_scope = load_engagement_scope(args.client)

    # THE BUSINESS RULE: opportunities are forward-looking. Posted window is API
    # plumbing (SAM caps it at a year back); what matters is that the response
    # deadline is today or later — never surface a deal the client can't act on.
    today = date.today()
    posted_from = _parse_date(args.posted_from) if args.posted_from else today - timedelta(days=364)
    posted_to = _parse_date(args.posted_to) if args.posted_to else today
    print(f"[window] active opportunities: responses due {today} → forward", file=sys.stderr)

    naics = strategy.inferred_naics
    out: dict = {
        "client": args.client,
        "strategy_revision": run_revision,
        "results": {},
    }
    if run_packet_sha is not None:
        out["strategy_packet_sha256"] = run_packet_sha

    kw = [k.term for k in strategy.keywords]
    # capability/technology terms only — for matchers where set-aside/agency
    # terms false-hit (forecasts, the DoD award wire: every announcement says
    # "small business")
    from tools.api.usaspending import addressable_terms
    cap_terms = addressable_terms([k.model_dump() if hasattr(k, "model_dump")
                                   else k for k in strategy.keywords])
    taxonomy_terms = (
        [term.term for term in [*sam_taxonomy.core, *sam_taxonomy.adjacent]]
        if sam_taxonomy is not None else []
    )

    def _dedupe_terms(values):
        seen = set()
        out_terms = []
        for value in values:
            term = str(value or "").strip()
            key = term.casefold()
            if not term or key in seen:
                continue
            seen.add(key)
            out_terms.append(term)
        return out_terms

    # The curated taxonomy is the source-of-truth capability surface.  The
    # approved search strategy remains additive context, but a taxonomy term
    # can no longer score downstream after being absent from source retrieval.
    cap_terms = _dedupe_terms([*cap_terms, *taxonomy_terms])

    # ── PARALLEL FAN-OUT: every source queried at once. Each task is isolated —
    # one source failing or hanging never blocks the rest — and results print as
    # they land, with timing, so a slow API is visible instead of mysterious.
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # L17: the SAM screen's census (ground truth behind screened-N claims)
    # travels beside the notice list as results["sam_census"]
    sam_census_box: dict = {}
    # term, count, up to three matched TITLES verbatim - nothing more
    # (operator spec 2026-07-31). Travels as results["term_yield"], appends
    # one JSONL line to the corpus log, and carries NO verdict: a true zero
    # (Riverbed) must read as a true zero, not an alarm.
    term_yield_box: list = []

    # Assigned from the final locked snapshot immediately before submission.
    # Closures below resolve these locals only when their task starts.
    scope_agencies: list[dict] = []
    scope_query_agencies: list[str] = []

    def _t_sam():
        spec = _spec(strategy, "sam.gov")
        # The approved capability/technology/search vocabulary is the SAM
        # opportunity text screen, even against a stale packet whose spec
        # predates the edit. One owner: agents.review.effective_query_terms.
        from agents.review import effective_query_terms
        q = SourceQuery(
            naics_codes=spec.naics_codes or naics if spec else naics,
            # Do not fall back to the unclassified keyword list here: an
            # intentional empty capability vocabulary must not reintroduce
            # agency, set-aside, or NAICS phrases into the text screen.
            keywords=_dedupe_terms([
                *effective_query_terms(strategy, spec, source="sam.gov"),
                *taxonomy_terms,
            ]),
            set_asides=spec.set_asides if spec else strategy.set_aside_angles,
            agencies=[a["name"] for a in scope_agencies],
            posted_from=posted_from, posted_to=posted_to,
            deadline_from=today, deadline_to=today + timedelta(days=364),
            limit=1000,
        )
        # THE VOCABULARY RECEIPT (2026-07-31): what each wire term actually
        # finds in the durable store, titles included, computed before the
        # extract path so the receipt exists even on a broken-upstream
        # morning. Additive; never sinks the lane.
        try:
            from tools.query_terms import term_yield, title_glance
            term_yield_box.extend(term_yield(list(q.keywords or [])))
            for row in term_yield_box:
                first = row["sample_titles"][0] if row["sample_titles"] else ""
                print(f"[term-yield] {row['term']!r}: {row['count']}"
                      + (f" · e.g. {title_glance(first, 76)!r}"
                         if first else ""),
                      file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - a receipt never sinks a sweep
            print(f"[term-yield] receipt failed ({type(exc).__name__}); "
                  "sweep continues without it", file=sys.stderr)
        # PRIMARY: the daily extract — zero quota, and it screens EVERY keyword
        # AND every NAICS lane against every active notice in one local pass
        # (the live API rations one NAICS or one title substring per call).
        ex = None
        try:
            from tools.api.sam_extract import SamExtractSource
            ex = SamExtractSource()
            sam = [json.loads(o.model_dump_json()) for o in ex.search(q)]
            c = ex.last_census or {}
            description_market_matched = int(c.get("market_matched") or 0)
            # A bounded, keyless depth pass closes the common SAM gap where
            # the description is generic and the actual client evidence lives
            # in a public RFP/PWS/SOW attachment. Retrieval anchors nominate;
            # the existing relevance scorer alone decides what may publish.
            attachment_enriched = attachment_relevant = 0
            if sam_taxonomy is not None:
                try:
                    max_candidates = min(
                        _SAM_ATTACHMENT_CANDIDATE_HARD_CAP,
                        max(0, int(os.environ.get(
                            "LILA_SAM_ATTACHMENT_CANDIDATES", "8"))))
                except ValueError:
                    max_candidates = 8
                sam, attachment_stats = _safe_enrich_sam_public_attachments(
                    sam, ex, q, sam_taxonomy, sam_engagement_scope,
                    limit=max_candidates)
                c.update(attachment_stats)
                attachment_added = int(
                    attachment_stats.get("attachment_added") or 0)
                c["description_market_matched"] = description_market_matched
                c["market_matched"] = (
                    description_market_matched + attachment_added)
                attachment_enriched = int(
                    attachment_stats.get("attachment_enriched") or 0)
                attachment_relevant = int(
                    attachment_stats.get("attachment_relevant") or 0)
            sam_census_box.update(c)
            if not native_workstation:
                from tools.api.forecasts.coverage import record_pull
                record_pull(
                    "sam_extract", "govwide", len(sam),
                    ok=bool(c.get("complete", True)),
                    note=f"matched {c.get('matched')} of "
                         f"{c.get('active_screened')} active notices "
                         "(census, no cap)")
            return sam, (f"{len(sam)} notices via daily extract — quota-free census: "
                         f"{c.get('matched')} matched of {c.get('active_screened')} active, "
                         f"{len(q.naics_codes or [])} NAICS + {len(q.keywords or [])} "
                         f"capability terms screened at once · "
                         f"{attachment_enriched} attachment-enriched / "
                         f"{attachment_relevant} newly relevant")
        except Exception as e:  # noqa: BLE001 — extract down: fall back to live API
            if ex is not None and ex.last_census:
                sam_census_box["failed_extract_census"] = dict(ex.last_census)
            print(f"[sam.gov] extract path failed ({e}) — falling back to live API",
                  file=sys.stderr)
        q.limit = 1000  # page size at the API max; pagination reaches totalRecords
        src = SamGovSource()
        sam = [json.loads(o.model_dump_json()) for o in src.search(q)]
        st = src.last_status or {}
        api_census = st.get("census") or {}
        total = sum((c.get("total_records") or 0) for c in api_census.values())
        retrieved = sum(c.get("retrieved", 0) for c in api_census.values())
        sam_census_box.update({
            "matched": len(sam), "active_screened": total or len(sam),
            "retrieved": retrieved, "complete": bool(st.get("complete")),
            "stale_cache": int(st.get("stale_cache") or 0),
            "source": "sam.gov live API", "passes": api_census,
            "attempt_manifest": st.get("attempt_manifest"),
        })
        if not native_workstation:
            from tools.api.forecasts.coverage import record_pull
            record_pull(
                "sam.gov", "govwide", retrieved,
                ok=bool(st.get("complete")),
                note=f"retrieved {retrieved} of {total} totalRecords across "
                     f"{len(api_census)} passes"
                     + ("" if st.get("complete") else " · INCOMPLETE"))
        bits = [f"{len(sam)} notices via live API"]
        if st.get("fresh_cache"):
            bits.append(f"{st['fresh_cache']} pass(es) from fresh cache — zero quota spent")
        if st.get("stale_cache"):
            bits.append(f"{st['stale_cache']} pass(es) from STALE cache (SAM throttled)")
        if st.get("failed"):
            bits.append(f"{st['failed']} pass(es) failed")
        bits.append(f"{st.get('calls_today', '?')} SAM calls today")
        return sam, " · ".join(bits)

    def _t_usaspending():
        from tools.api.usaspending import addressable_terms
        usa = UsaSpendingSource()
        # SCOPE IS A LENS (2026-07-10): the canonical sweep sizes the WHOLE
        # market — the scoreboard shows everything that is out there. Agency
        # focus applies at report time (the agency-report projection pulls
        # its own scoped slice); target agencies never filter collection.
        # capability/technology terms only — the keyword-matched (addressable)
        # slice that grounds the TAM; agency/set-aside terms would over-match
        terms = addressable_terms(strategy.keywords)
        market_agencies = scope_query_agencies or [None]
        evidence = [
            usa.market_evidence(code, agency=agency, keywords=terms)
            for code in naics for agency in market_agencies
        ]
        return evidence, (f"evidence for {len(evidence)} NAICS"
                          + (f" + addressable slice ({len(terms)} terms)" if terms else ""))

    def _t_web():
        spec = _spec(strategy, "web")
        q = SourceQuery(
            keywords=(spec.query_terms if spec else kw),
            naics_codes=naics, agencies=scope_query_agencies, limit=15,
        )
        web = [json.loads(o.model_dump_json()) for o in WebSearchSource().search(q)]
        return web, f"{len(web)} leads"

    def _t_federal_register():
        from tools.api.federal_register import FederalRegisterSource
        fr = FederalRegisterSource().enrich(SourceQuery(
            keywords=kw, agencies=scope_query_agencies))
        return fr, f"{sum(len(v) for v in fr.values())} docs across {len(fr)} keyword lanes"

    def _t_news():
        from tools.api.trade_rss import TradeRssSource
        news = TradeRssSource().enrich(SourceQuery(
            keywords=kw, agencies=scope_query_agencies))
        return news, f"{len(news.get('items', []))} keyword-matched items"

    def _t_subawards():
        from agents.partnering.config import SUBCONTRACTING_PLAN_THRESHOLD_USD
        from tools.api.usaspending_subawards import SubawardsSource
        src = SubawardsSource()
        subs = src.enrich(SourceQuery(
            naics_codes=naics, agencies=scope_query_agencies))
        # subcontracting-demand rows (sourced size via the recipient
        # business-category filter); unverified threshold -> no fetch, the
        # summarizer degrades to N/A downstream
        demand: dict = {}
        if SUBCONTRACTING_PLAN_THRESHOLD_USD is not None:
            for code in naics[:6]:
                try:
                    demand[code] = src.demand_awards(
                        code, SUBCONTRACTING_PLAN_THRESHOLD_USD)
                except Exception as e:  # noqa: BLE001 — additive, never fatal
                    demand[code] = [{"error": str(e)}]
        subs["demand"] = {"threshold_usd": SUBCONTRACTING_PLAN_THRESHOLD_USD,
                          "rows_by_naics": demand}
        small: dict = {}
        for code in naics[:6]:
            try:
                small[code] = src.small_awards(code)
            except Exception as e:  # noqa: BLE001 — additive, never fatal
                small[code] = [{"error": str(e)}]
        subs["small_awards"] = small
        lane_errors = {}
        for family, rows_by_naics in (
            ("edges", subs.get("edges") or {}),
            ("demand", demand),
            ("small_awards", small),
        ):
            for code, rows in rows_by_naics.items():
                failures = [
                    str(row.get("error"))
                    for row in rows if isinstance(row, dict) and row.get("error")
                ] if isinstance(rows, list) else []
                if failures:
                    lane_errors[f"{family}:{code}"] = failures[0][:200]
        if lane_errors:
            subs["errors"] = lane_errors
        n_edges = sum(len(v) for v in (subs.get("edges") or {}).values()
                      if isinstance(v, list))
        return subs, (f"{len(subs.get('primes', []))} primes ranked, "
                      f"{n_edges} sub edges, demand rows for {len(demand)} lanes, "
                      f"small-sourced rows for {len(small)} lanes")

    def _t_contract_awards():
        from tools.api.contract_awards import ContractAwardsSource
        awards = ContractAwardsSource().enrich(SourceQuery(
            naics_codes=naics, agencies=scope_query_agencies))
        if awards.get("fallback_used"):
            returned = awards.get("successful_naics_lanes", 0)
            attempted = awards.get("attempted_naics_lanes", 0)
            omitted = awards.get("omitted_naics_lanes", 0)
            origin = (f"USAspending fallback ({returned}/{attempted} NAICS "
                      f"lanes returned"
                      + (f", {omitted} omitted" if omitted else "") + ")")
        else:
            origin = "SAM Contract Awards"
        return awards, (f"{len(awards.get('recompetes', []))} expiring contracts, "
                        f"{len(awards.get('incumbents', []))} incumbents ranked "
                        f"via {origin}")

    def _t_kev():
        from tools.api.cisa_kev import CisaKevSource
        kev = CisaKevSource().enrich(SourceQuery(keywords=kw))
        return kev, (f"{kev.get('recent_count')} added in last {kev.get('window_days')}d, "
                     f"{len(kev.get('matched', []))} keyword-matched")

    def _t_grants():
        return _t_program_source("grants")

    def _t_sbir():
        return _t_program_source("sbir")

    def _t_gdelt():
        from tools.api.gdelt import GdeltSource
        g = GdeltSource().enrich(SourceQuery(keywords=kw))
        lanes = {
            key: value for key, value in g.items()
            if not key.startswith("_") and isinstance(value, list)
        }
        n = sum(len(value) for value in lanes.values())
        return g, f"{n} news articles (14d, global) across {len(lanes)} lanes"

    def _t_treasury():
        from tools.api.treasury_fiscal import TreasuryFiscalSource
        t = TreasuryFiscalSource().enrich(SourceQuery(
            agencies=scope_query_agencies))
        return t, (f"{len(t.get('matched_lines', []))} agency outlay lines "
                   f"(as of {t.get('as_of')})")

    def _t_gao():
        from tools.api.gao_legal import GaoLegalSource
        g = GaoLegalSource().enrich(SourceQuery(keywords=kw))
        n_prot = sum(1 for i in g.get("items", []) if i.get("protest"))
        return g, f"{len(g.get('items', []))} legal decisions ({n_prot} protests)"

    def _t_calc():
        from tools.api.calc_rates import CalcRatesSource
        c = CalcRatesSource().enrich(SourceQuery(keywords=kw))
        return c, f"rate distributions for {len(c)} labor-category lanes"

    def _t_edgar():
        from tools.api.sec_edgar import SecEdgarSource
        e = SecEdgarSource().enrich(SourceQuery(keywords=kw))
        term_rows = [
            value for key, value in e.items()
            if not key.startswith("_") and isinstance(value, dict)
            and isinstance(value.get("companies"), list)
        ]
        n = sum(len(v.get("companies", [])) for v in term_rows)
        return e, f"{n} public companies filing on {len(term_rows)} terms"

    def _t_fedramp():
        from tools.api.fedramp import FedRampSource
        f = FedRampSource().enrich(SourceQuery(keywords=kw + [args.client]))
        return f, (f"{len(f.get('matched', []))} marketplace matches "
                   f"of {f.get('marketplace_total')} entries")

    def _t_hierarchy():
        if not scope_query_agencies:
            # nothing to point the lane at, and it spends METERED SAM calls
            return ({"not_applicable": "no agency scope on this engagement"},
                    LANE_NOT_APPLICABLE)
        from tools.api.federal_hierarchy import FederalHierarchySource
        h = FederalHierarchySource().enrich(SourceQuery(
            agencies=scope_query_agencies))
        n = sum(len(v) for v in h.values())
        return h, f"{n} sub-organizations across {len(h)} agencies (metered SAM calls)"

    def _t_forecasts():
        # Agency-stated intent, routed into the SIGNALS flow only — never the
        # live-opportunity list (L15). Multi-source: every registered forecast
        # adapter pulls; the global store diffs re-pulls into signal events
        # (date moved / value grew / disappeared); per-client snapshots keep
        # the matched-subset diff; the coverage ledger logs what was and was
        # NOT screened, so absence is never silent.
        from agents.schemas import CapabilityProfile
        from tools.api.forecasts import (
            bucket_forecasts, diff_snapshots, forecast_sources, gaps_for,
            load_latest_snapshot, record_payload, record_pull,
            save_snapshot, screen_line, source_is_offline_safe,
            store_upsert, strict_offline,
        )
        from tools.api.provenance import make_provenance_envelope
        from tools.api.source_catalog import source_spec
        slug_local = "".join(ch if ch.isalnum() else "_" for ch in args.client).strip("_").lower()
        profile = CapabilityProfile(
            client_name=args.client, naics_codes=naics,
            set_aside_eligibility=strategy.set_aside_angles or [],
            past_performance_keywords=cap_terms)
        recs, events, pulled_labels, off_labels = [], [], [], []
        source_rows, source_attempts = [], []
        contract_origins = set(source_spec("forecasts").coverage_origins)

        def _record_forecast_pull(*pull_args, **pull_kwargs):
            """Coverage-ledger I/O is additive and cannot sink source data."""

            if native_workstation:
                return None
            try:
                return record_pull(*pull_args, **pull_kwargs)
            except Exception as exc:  # noqa: BLE001 - receipt carries the gap
                return str(exc)[:200]

        for src in forecast_sources():
            agency_label = getattr(src, "forecast_agency", None) or "DHS"
            if not src.enabled:
                off_labels.append(src.name)
                attempt = {
                    "source": src.name, "status": "not-run",
                }
                source_attempts.append(attempt)
                source_rows.append(_forecast_source_summary(
                    src.name,
                    [],
                    [],
                    {"mode": "not_run", "complete": False},
                    contract_origins=contract_origins,
                    status="not-run",
                ))
                continue
            if strict_offline() and not source_is_offline_safe(src):
                off_labels.append(src.name)
                source_attempts.append({
                    "source": src.name,
                    "status": "not-run",
                    "note": "strict offline certification",
                })
                source_rows.append(_forecast_source_summary(
                    src.name,
                    [],
                    [],
                    {"mode": "not_run", "complete": False},
                    contract_origins=contract_origins,
                    status="not-run",
                ))
                continue
            child = _collect_forecast_child(
                src,
                profile=profile,
                capability_terms=cap_terms,
                taxonomy=forecast_taxonomy,
                engagement_scope=sam_engagement_scope,
                scope_agencies=scope_agencies,
            )
            market_rows = child["market_rows"]
            rows = child["rows"]
            child_provenance = child["provenance"]
            child_status = child["status"]
            child_error = child.get("error")
            if child_status == "failed":
                attempt = {
                    "source": src.name,
                    "status": "failed",
                    "count": len(market_rows),
                    "error": child_error or "forecast child failed",
                }
                source_attempts.append(attempt)
                source_rows.append(_forecast_source_summary(
                    src.name,
                    [],
                    market_rows,
                    child_provenance,
                    contract_origins=contract_origins,
                    status="failed",
                ))
                ledger_error = _record_forecast_pull(
                    src.name,
                    agency_label,
                    0,
                    ok=False,
                    note=attempt["error"],
                    error=attempt["error"],
                )
                if ledger_error:
                    source_rows[-1]["limitations"].append(
                        "Forecast coverage-ledger write also failed."
                    )
                continue

            ev = []
            if native_workstation:
                # Tranche 3 owns only this workstation's returned evidence.
                # The global change store and client-global coverage ledger are
                # later-tranche state and must remain byte-inert here.
                pass
            elif not _forecast_snapshot_advances_change_store(child_provenance):
                # Advancing a change store from an incomplete census would
                # turn omitted pages into false "disappeared" events.
                ledger_error = _record_forecast_pull(
                    src.name,
                    agency_label,
                    len(market_rows),
                    ok=True,
                    note="partial snapshot; change store not advanced",
                )
                if ledger_error:
                    child_provenance = _forecast_partial_provenance(
                        child_provenance,
                        "Forecast coverage-ledger write failed; source rows "
                        "remain usable for this sweep.",
                    )
                    child_status = "partial"
            else:
                try:
                    _, ev = store_upsert(src.name, market_rows)
                except Exception:  # noqa: BLE001 - evidence remains usable
                    child_provenance = _forecast_partial_provenance(
                        child_provenance,
                        "Forecast change-store update failed; official rows "
                        "remain usable for this sweep.",
                    )
                    child_status = "partial"
                    ev = []
                ledger_error = _record_forecast_pull(
                    src.name, agency_label, len(market_rows), ok=True)
                if ledger_error:
                    child_provenance = _forecast_partial_provenance(
                        child_provenance,
                        "Forecast coverage-ledger write failed; official rows "
                        "remain usable for this sweep.",
                    )
                    child_status = "partial"
            recs.extend(rows)
            events.extend(ev)
            pulled_labels.append(f"{src.name}:{len(rows)}")
            source_summary = _forecast_source_summary(
                src.name,
                list(rows),
                market_rows,
                child_provenance,
                contract_origins=contract_origins,
                status=child_status,
            )
            source_attempts.append({
                "source": src.name,
                "status": child_status,
                "count": len(market_rows),
                "retrieved_at": child_provenance.get("retrieved_at"),
                "data_as_of": source_summary.get("data_as_of"),
                "stale": source_summary.get("stale") is True,
            })
            if child_error:
                source_attempts[-1]["error"] = child_error
            source_rows.append(source_summary)
        if not recs and not events:
            empty_result = _empty_forecast_family_result(
                source_attempts, off_labels)
            if empty_result is not None:
                return empty_result
        # capability/technology terms ONLY (addressable_terms rule): the full
        # keyword list carries set-aside and agency terms, which false-hit the
        # forecast matcher ("Small Business set-aside" matched as a keyword)
        buckets = bucket_forecasts(
            recs, profile, keywords=cap_terms,
            taxonomy=forecast_taxonomy,
            engagement_scope=sam_engagement_scope)
        # Only capability-evidenced records enter the client signal list.
        # Exact NAICS matches remain counted as lane coverage, never promoted
        # into a vague "planning window" merely because the code matched.
        matched = buckets["capability"]
        if native_workstation:
            # ``gaps_for`` records client-global coverage state.  Preserve the
            # truthful local result without performing that shared write.
            from tools.api.forecasts.coverage import _is_covered
            gaps = [agency for agency in scope_query_agencies
                    if agency and not _is_covered(agency)]
        else:
            gaps = gaps_for(scope_query_agencies, args.client)
        screen = screen_line(buckets, len(recs), "agency", gaps=gaps)
        cur = [record_payload(m["record"])
               | {"reasons": m["reasons"], "score": m["score"]}
               for m in matched]
        returned_sources = [
            row for row in source_attempts
            if row["status"] in {"success", "partial"}]
        retrievals = sorted(
            str(row["retrieved_at"])
            for row in source_rows if row.get("retrieved_at"))
        coverage_contract = _forecast_contract_receipt(source_attempts)
        family_status = (
            "complete"
            if coverage_contract["complete_within_boundary"] else "partial"
        )
        snapshot_complete = _forecast_family_advances_client_snapshot(
            source_attempts)
        prev = (None if native_workstation or not snapshot_complete
                else load_latest_snapshot(slug_local))
        delta = diff_snapshots(prev, cur) if prev is not None else None
        if not native_workstation and snapshot_complete:
            save_snapshot(slug_local, [m["record"] for m in matched])
        family_modes = {
            str(row.get("mode") or "") for row in source_rows
            if row.get("status") in {"success", "partial"}
        }
        retrieval_mode = (
            "official-cache"
            if family_modes and all("cache" in mode for mode in family_modes)
            else "live" if returned_sources else "unknown"
        )
        family_limitations = []
        if coverage_contract["incomplete_origins"]:
            family_limitations.append(
                "Default forecast boundary incomplete for this sweep: "
                + ", ".join(coverage_contract["incomplete_origins"])
                + "."
            )
        if not snapshot_complete:
            family_limitations.append(
                "The cross-source client comparison snapshot was not advanced "
                "because every configured adapter did not return a fresh, "
                "complete result."
            )
        stale_children = [
            row["source"] for row in source_rows if row.get("stale") is True
        ]
        if stale_children:
            family_limitations.append(
                "Official stale-cache evidence used for: "
                + ", ".join(stale_children)
                + "."
            )
        family_provenance = make_provenance_envelope(
            "forecasts",
            status=family_status,
            mode="multi-source-forecast",
            retrieval_mode=retrieval_mode,
            fallback=any(
                row.get("fallback") is True or row.get("stale") is True
                for row in source_rows
            ),
            stale=bool(stale_children),
            retrieved_at=retrievals[0] if retrievals else None,
            record_count=len(recs),
            attempts=source_attempts,
            limitations=family_limitations,
            public_detail=(
                "Complete within the declared default forecast boundary."
                if family_status == "complete"
                else "The declared default forecast boundary was incomplete."
            ),
        )
        payload = {"matched": cur, "total_records": len(recs), "delta": delta,
                   "events": events, "screen_line": screen,
                   "sources": source_rows,
                   "coverage_contract": coverage_contract,
                   "_provenance": family_provenance.model_dump(mode="json"),
                   "scope_agencies": scope_query_agencies,
                   "lane_only_count": len(buckets["lane_only"]),
                   "coverage_gaps": gaps,
                   "note": "agency-stated intent (forecast), not live opportunities"}
        d = (f" · Δ {len(delta['new'])} new / {len(delta['moved'])} moved / "
             f"{len(delta['disappeared'])} gone" if delta else "")
        ev_note = f" · {len(events)} store events" if events else ""
        return payload, (f"{len(matched)} forecast signals from {len(recs)} "
                         f"lines ({', '.join(pulled_labels)}){d}{ev_note}")

    def _t_source_mesh():
        """Run the verified enrichment adapters as one isolated source family."""
        from agents.review import scope_designator
        from tools.api.source_mesh import collect
        from tools.capability import profile_path

        mesh_agencies = list(scope_query_agencies or []) or list(
            getattr(strategy, "target_agencies", []) or [])
        named_firms = [
            str(value).strip()
            for value in (
                getattr(profile, "named_competitors_and_incumbents", None) or []
            )
            if str(value or "").strip()
        ]
        try:
            with open(profile_path(args.client), encoding="utf-8") as profile_file:
                raw_profile = json.load(profile_file)
            named_firms.extend(
                str(row.get("name") or "").strip()
                for row in (
                    (raw_profile.get("product_competitors") or {}).get(
                        "competitors"
                    )
                    or []
                )
                if isinstance(row, dict) and str(row.get("name") or "").strip()
            )
        except (OSError, TypeError, ValueError):
            # The typed profile above remains the approved query surface.
            pass
        own_name = str(getattr(profile, "client_name", "") or "").casefold()
        named_firms = [
            firm
            for firm in _dedupe_terms(named_firms)
            if firm.casefold() != own_name
        ]
        mesh_query = SourceQuery(
            keywords=[*cap_terms[:12], *named_firms[:40]],
            naics_codes=naics,
            agencies=mesh_agencies,
            limit=50,
        )
        payload = collect(
            mesh_query,
            client_name=args.client,
            scope_designator=scope_designator(run_scope),
        )
        records = sum(
            int(row.get("record_count") or 0)
            for row in payload["sources"].values()
        )
        statuses = [
            str(row.get("status") or "unknown")
            for row in payload["sources"].values()
        ]
        complete = statuses.count("success")
        partial = statuses.count("partial")
        not_run = statuses.count("not-run")
        failed = statuses.count("failed")
        return payload, (
            f"{records} scoped records across {len(statuses)} official child "
            f"sources: {complete} complete, {partial} partial, "
            f"{not_run} not run, {failed} failed"
        )

    def _t_dod_contracts():
        # the DoD award wire: who is WINNING in the client's lanes right now.
        # Capability terms only — every announcement says "small business".
        from tools.api.dod_contracts import DodContractsSource
        d = DodContractsSource().enrich(SourceQuery(
            keywords=cap_terms, agencies=scope_query_agencies))
        return d, (f"{len(d['items'])} keyword-matched of "
                   f"{d.get('total_announcements', 0)} announcements")

    def _t_budget():
        # spend pressure: current-FY unobligated balances at the target agencies
        if not scope_query_agencies:
            return ({"rows": [],
                     "not_applicable": "no agency scope on this engagement"},
                    LANE_NOT_APPLICABLE)
        from tools.api.usaspending_budget import BudgetPressureSource
        d = BudgetPressureSource().enrich(
            SourceQuery(agencies=scope_query_agencies))
        return d, f"{len(d['rows'])} agencies with current-FY execution"

    def _t_congress():
        # money forming: recently updated bills hitting capability terms
        from tools.api.congress_gov import CongressGovSource
        d = CongressGovSource().enrich(SourceQuery(
            keywords=cap_terms, agencies=scope_query_agencies))
        return d, (f"{len(d['items'])} keyword-matched of "
                   f"{d.get('total_scanned', 0)} recently updated bills")

    def _t_watchdogs():
        # compelled demand: GAO + all-IG reports hitting capability terms
        from tools.api.watchdog_rss import WatchdogRssSource
        d = WatchdogRssSource().enrich(SourceQuery(
            keywords=cap_terms, agencies=scope_query_agencies))
        return d, (f"{len(d['items'])} keyword-matched of "
                   f"{d.get('total_reports', 0)} watchdog reports")

    def _t_govinfo():
        from tools.api.govinfo import GovInfoSource
        gi = GovInfoSource().enrich(SourceQuery(keywords=kw))
        n = sum(len(v) for v in gi.values())
        return gi, f"{n} legislative docs (bills/reports/laws) across {len(gi)} lanes"

    def _t_regulations():
        from tools.api.regulations_gov import RegulationsGovSource
        regs = RegulationsGovSource().enrich(SourceQuery(keywords=kw))
        n_docs = sum(len(v) for v in regs.values())
        n_open = sum(1 for v in regs.values() for d in v if d.get("comment_open"))
        return regs, f"{n_docs} docs across {len(regs)} lanes, {n_open} with open comment periods"

    def _t_buyer_map():
        # STANDING (2026-07-09): displacement windows refresh every sweep;
        # notices cross-reference from the LOCAL sam.gov slice, never the API
        from tools.api.incumbent_buyers import build_buyer_map
        bm = build_buyer_map(profile, sam_notices=None)
        skipped = bm.get("skipped_terms") or []
        return bm, (f"{len(bm.get('buyers') or [])} buying offices, "
                    f"{len(bm.get('displacement_windows') or [])} displacement "
                    f"windows; calls {bm.get('calls')}"
                    + (f"; BUDGET HIT, {len(skipped)} term(s) skipped: "
                       f"{', '.join(skipped[:4])}" if skipped else ""))

    def _t_funded_demand():
        from tools.api.govinfo import GovInfoSource
        fd = GovInfoSource().funded_demand(profile.capability_terms.core[:8])
        return fd, (f"{sum(len(v) for v in (fd.get('hits') or {}).values())} "
                    f"budget-text hits across {len(fd.get('hits') or {})} core terms")

    def _t_program_source(identity: str):
        """Run one cataloged PROGRAM adapter through the common contract."""
        from tools.api import REGISTRY
        from tools.api.source_catalog import source_spec

        spec = source_spec(identity)
        source = REGISTRY.get(spec.adapter_name or identity)
        payload = _scoped_program_payload(
            identity,
            source,
            SourceQuery(
                keywords=cap_terms,
                agencies=scope_query_agencies,
                limit=100,
            ),
            program_engagement_scope,
        )
        records = payload.get("records") or []
        provenance = payload.get("_provenance") or {}
        if provenance.get("mode") == "scope-excluded":
            return payload, (
                f"scope-excluded before retrieval · "
                f"{spec.label}"
            )
        received = provenance.get("records_received")
        population = (
            f" of {received} official rows" if isinstance(received, int) else ""
        )
        return payload, (
            f"{len(records)} PROGRAM records{population} · "
            f"{spec.label}"
        )

    def _t_reginfo_unified_agenda():
        return _t_program_source("reginfo_unified_agenda")

    def _t_dsip_topics():
        return _t_program_source("dsip_topics")

    def _t_darpa_opportunities():
        return _t_program_source("darpa_opportunities")

    def _t_dod_budget_exhibits():
        return _t_program_source("dod_budget_exhibits")

    def _t_foreign_assistance():
        return _t_program_source("foreign_assistance")

    TASKS = {
        "sam.gov": _t_sam,
        "usaspending.gov": _t_usaspending,
        "web": _t_web,
        "federal_register": _t_federal_register,
        "news": _t_news,
        "subawards": _t_subawards,
        "contract_awards": _t_contract_awards,
        "dod_contracts": _t_dod_contracts,
        "budget_pressure": _t_budget,
        "watchdogs": _t_watchdogs,
        "congress": _t_congress,
        "kev": _t_kev,
        "regulations": _t_regulations,
        "govinfo": _t_govinfo,
        "grants": _t_grants,
        "sbir": _t_sbir,
        "dsip_topics": _t_dsip_topics,
        "gdelt": _t_gdelt,
        "treasury": _t_treasury,
        "gao": _t_gao,
        "calc": _t_calc,
        "edgar": _t_edgar,
        "fedramp": _t_fedramp,
        "hierarchy": _t_hierarchy,
        "forecasts": _t_forecasts,
        "source_mesh": _t_source_mesh,
        "buyer_map": _t_buyer_map,
        "funded_demand": _t_funded_demand,
        "reginfo_unified_agenda": _t_reginfo_unified_agenda,
        "darpa_opportunities": _t_darpa_opportunities,
        "dod_budget_exhibits": _t_dod_budget_exhibits,
        "foreign_assistance": _t_foreign_assistance,
    }
    validate_standard_tasks(TASKS)
    KEYMAP = _RESULT_KEY_FOR_SKIP
    todo = {n: fn for n, fn in TASKS.items() if n not in args.skip}
    print(f"[fan-out] querying {len(todo)} sources in parallel: {', '.join(todo)}",
          file=sys.stderr)
    started = _time.monotonic()

    def _run(name, fn):
        t0 = _time.monotonic()
        try:
            value, summary = fn()
            soft_error = _payload_error(value)
            if soft_error is not None:
                return (name, value, None, _time.monotonic() - t0,
                        RuntimeError(soft_error))
            return name, value, summary, _time.monotonic() - t0, None
        except Exception as e:  # noqa: BLE001 — isolate; never sink the fleet
            return name, {"error": str(e)}, None, _time.monotonic() - t0, e

    import threading as _threading
    _pending = set(todo)
    _stop = _threading.Event()

    def _heartbeat():
        while not _stop.wait(15):
            if _pending:
                print(f"[fan-out] {int(_time.monotonic() - started)}s elapsed — "
                      f"still waiting on: {', '.join(sorted(_pending))}", file=sys.stderr)

    attempts: list[dict] = []
    executor_manager = None
    try:
        # The final exact read and every initial submit share the same
        # cross-process lock as packet writers.  Once the lock is released at
        # least one source task has been submitted, so a mutation can no longer
        # slip through a nominal "pre-fan-out" gap and cause old terms to spend.
        with packet_binding_lock(
                args.client, workstation_id=bound_workstation,
                native_owner=native_workstation):
            current_snapshot = load_packet_snapshot(
                args.client, workstation_id=bound_workstation,
                require_native_owner=native_workstation)
            current_packet = current_snapshot.packet
            current_packet_sha = (
                current_snapshot.sha256 if native_workstation else None)
            if not current_packet.is_approved:
                raise PermissionError(
                    f"strategy for {args.client!r} is no longer approved")
            if ((native_workstation and current_packet_sha != run_packet_sha)
                    or (expected_packet_sha is not None
                        and current_snapshot.sha256 != expected_packet_sha)
                    or current_packet.revision_count != run_revision
                    or current_packet.search_scope != run_scope):
                raise WorkstationBindingError(
                    "strategy packet, boundary, or scope changed before search "
                    "fan-out; rerun from the current workstation")
            scope_agencies = current_packet.scope_agencies()
            scope_query_agencies = [
                agency["name"] for agency in scope_agencies]
            executor_manager = ThreadPoolExecutor(
                max_workers=len(todo) or 1)
            ex = executor_manager.__enter__()
            futures = [ex.submit(_run, n, fn) for n, fn in todo.items()]

        _hb = _threading.Thread(target=_heartbeat, daemon=True)
        _hb.start()
        for fut in as_completed(futures):
            name, value, summary, dt, err = fut.result()
            out["results"][KEYMAP.get(name, name)] = value
            _pending.discard(name)
            if err is None and summary == LANE_NOT_APPLICABLE:
                # No attempt row: the coverage projection renders the absent
                # lane as NOT RUN THIS REFRESH, which is what happened.
                print(f"[{name}] not applicable: "
                      f"{value.get('not_applicable', 'nothing to gather')}; "
                      f"lane not run (coverage reads NOT RUN THIS REFRESH)",
                      file=sys.stderr)
                continue
            # SAM's result remains the long-standing notice-list shape, while
            # its exhaustive-screen receipt travels in ``sam_census_box``.
            # Propagate an interrupted/omitted-pass census into the generic
            # attempt manifest so a partial live fallback cannot mint a
            # comprehensive sweep merely because it returned some notices.
            usa_receipt = (
                _usaspending_evidence_receipt(value)
                if name == "usaspending.gov" and err is None
                else None
            )
            attempt = attempt_row(
                name,
                value,
                summary,
                dt,
                err,
                partial_override=bool(
                    (
                        name == "sam.gov"
                        and (
                            sam_census_box.get("complete") is not True
                            or int(sam_census_box.get("stale_cache") or 0) > 0
                        )
                    )
                    or (
                        name == "usaspending.gov"
                        and usa_receipt is not None
                        and usa_receipt.get("complete") is not True
                    )
                ),
            )
            if name == "sam.gov":
                attempt["census_receipt"] = _sam_census_receipt(
                    sam_census_box
                )
            if usa_receipt is not None:
                attempt["evidence_receipt"] = usa_receipt
            attempts.append(attempt)
            if err is None:
                prefix = "PARTIAL" if attempt.get("partial") else name
                print(f"[{prefix}] {name + ': ' if prefix == 'PARTIAL' else ''}"
                      f"{summary} ({dt:.1f}s)", file=sys.stderr)
            else:
                print(f"[{name}] FAILED after {dt:.1f}s: {err}", file=sys.stderr)
    finally:
        _stop.set()
        if executor_manager is not None:
            executor_manager.__exit__(None, None, None)
    print(f"[fan-out] all sources returned in {_time.monotonic() - started:.1f}s "
          f"(sequential would be the sum)", file=sys.stderr)
    # P3 (2026-07-11): the sweep carries its own source-attempt manifest —
    # which sources were attempted, what came back, what failed, how long —
    # so completeness is defensible per artifact, not reconstructed from logs.
    from tools.api.source_mesh import child_attempt_rows

    attempts.extend(child_attempt_rows(out["results"].get("source_mesh")))
    out["results"]["_attempts"] = attempts
    if not native_workstation:
        try:
            from tools.api.forecasts.coverage import record_pull as _rp
            for a in attempts:
                _rp(f"sweep:{a['source']}", "sweep", a.get("count"), a["ok"],
                    note=(a.get("summary") or a.get("error") or "")[:200],
                    error=a.get("error"), elapsed_s=a.get("elapsed_s"))
        except Exception as e:  # noqa: BLE001 — ledger never blocks a sweep
            print(f"[attempts] ledger write skipped: {e}", file=sys.stderr)
    if sam_census_box:
        out["results"]["sam_census"] = dict(sam_census_box)
    if term_yield_box:
        out["results"]["term_yield"] = list(term_yield_box)
        try:
            from tools.query_terms import append_term_yield_log
            append_term_yield_log({
                "client": args.client,
                "generated_at": out.get("generated_at"),
                "store_terms": term_yield_box,
            })
        except Exception:  # noqa: BLE001 - the corpus log is a bonus
            pass
        fc = sam_census_box.get("focus")
        slice_note = (f"focus {'+'.join(fc)} slice: "
                      f"{sam_census_box.get('matched')} of "
                      f"{sam_census_box.get('market_matched')} matched "
                      f"market-wide · " if fc else
                      f"{sam_census_box.get('matched')} matched · ")
        print(f"[census] SAM screen: {slice_note}"
              f"{sam_census_box.get('active_screened')} active notices screened "
              f"· complete={sam_census_box.get('complete', True)}", file=sys.stderr)

    if args.preserve_skipped and args.skip:
        preserved = _preserve_skipped_results(
            prior_results, out["results"], args.skip)
        if preserved:
            print(f"[preserve] retained skipped lanes: "
                  f"{', '.join(preserved)}", file=sys.stderr)

    # Persist the sweep-level truth claim beside the lane attempts.  This is
    # deliberately computed before model triage: coverage describes what the
    # source fleet screened, not whether the downstream model liked the haul.
    # A partial, stale, failed, or unrun required lane can therefore never be
    # hidden by a polished report or by a large opportunity count.
    from agents.reports.source_coverage import coverage_verdict_from_sweep
    coverage_verdict = coverage_verdict_from_sweep(
        out,
        scope_excluded_sources=(
            ("budget_pressure", "hierarchy")
            if not scope_query_agencies else ()
        ),
    ).as_dict()
    out["results"]["source_coverage_verdict"] = coverage_verdict
    print(
        "[coverage] " + coverage_verdict["verdict"]
        + ("" if coverage_verdict["comprehensive"] else
           " · blocking: " + ", ".join(
               coverage_verdict["blocking_sources"])),
        file=sys.stderr,
    )

    out_dir = os.path.join(os.path.dirname(__file__), "data", "cleaned")
    os.makedirs(out_dir, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in args.client).strip("_").lower()
    # L19: a focused gate mints the designator into the FILENAME so per-agency
    # runs coexist; scope=all writes the unqualified canonical name. The
    # designator grammar (dot-segment 2, 'agency_<abbr>') matches the shelf
    # parser and the agency-report precedent.
    from agents.review import scope_designator as _scope_designator
    _designator = _scope_designator(
        {"agencies": [a["abbr"] for a in scope_agencies]} if scope_agencies else None)
    path = os.path.join(out_dir,
                        f"searches_{slug}.{_designator}.json" if _designator
                        else f"searches_{slug}.json")
    sam_res = out["results"].get("sam.gov")

    # ── SEARCH SCOPE: the operator's gate choice. Specific agencies filter
    # the haul BEFORE triage (cheaper screen, scoped artifact, and downstream
    # reports pick up the designator from the artifact). All-agencies is the
    # default and carries NO tag beyond {"all": true} — a plain Federal
    # Opportunity Assessment IS the all-agencies statement.
    # scope_agencies was read once, before the fan-out (L18 filter-first).
    # FILTER-FIRST, FREE CONTEXT (L18, supersedes L12's collect-everything
    # implementation; keeps its lesson): metered and LLM layers receive the
    # FOCUS SLICE; the whole-market numbers stay in the artifact via the
    # free extract census, so a focused engagement still states the market
    # and a thin slice can never read as a dead market unnoticed.
    if scope_agencies:
        out["search_scope"] = {
            "agencies": [{"abbr": a["abbr"], "name": a["name"]}
                         for a in scope_agencies],
            "mode": "focus"}
        print(f"[scope] focus recorded: "
              f"{' + '.join(a['abbr'] for a in scope_agencies)} — the sweep "
              "contains the agency-focused opportunity slice; free federal "
              "market census totals remain available as context",
              file=sys.stderr)
    else:
        out["search_scope"] = {"all": True}
    n_opps = len(sam_res) if isinstance(sam_res, list) else 0

    # Contact graph and Outreach are still client-global.  A native Tranche-3
    # search may write only its exact sweep/research family; Target-owned
    # growth remains closed until that storage is partitioned in Tranche 4.
    if not native_workstation:
        try:
            from tools.contact_graph.harvest import harvest_from_results
            cg = harvest_from_results(out["results"])
            if cg.enabled:
                print(f"[contact-graph] {cg}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — side effect only
            print(f"[contact-graph] harvest skipped (non-blocking): {e}", file=sys.stderr)
    else:
        print("[contact-graph] native workstation: shared harvest suppressed",
              file=sys.stderr)

    # ── CLAUDE TRIAGE: reason through the haul before a human sees it ──
    if n_opps and "triage" not in args.skip:
        from agents.decisions.triage import (
            deterministic_prefilter,
            triage_notices,
        )
        model_notices = sam_res
        deterministic_verdicts: dict[str, dict] = {}
        prefilter_receipt: dict | None = None
        if sam_taxonomy is not None:
            model_notices, deterministic_verdicts, prefilter_receipt = (
                deterministic_prefilter(
                    sam_res,
                    sam_taxonomy,
                    engagement_scope=sam_engagement_scope,
                )
            )
            out["results"]["triage_prefilter"] = prefilter_receipt
            consolidation_bits = []
            if prefilter_receipt.get("superseded_revisions"):
                consolidation_bits.append(
                    f"{prefilter_receipt['superseded_revisions']} "
                    "superseded revisions"
                )
            if prefilter_receipt.get("cross_post_duplicates"):
                consolidation_bits.append(
                    f"{prefilter_receipt['cross_post_duplicates']} "
                    "SAM cross-post duplicates"
                )
            print(
                f"[triage] deterministic screen: {n_opps} candidates · "
                f"{len(model_notices)} evidence-bearing thread representatives · "
                f"{len(deterministic_verdicts)} deterministic dispositions"
                + (f" ({' · '.join(consolidation_bits)})"
                   if consolidation_bits else ""),
                file=sys.stderr,
            )
        print(f"[triage] model screening {len(model_notices)} notices against "
              "the strategy ...", file=sys.stderr)
        try:
            verdicts = dict(deterministic_verdicts)
            verdicts.update(triage_notices(
                args.client,
                getattr(strategy, "pursuit_strategy", "") or "",
                model_notices,
            ))
            out["results"]["triage"] = verdicts
            decision_coverage = _decision_coverage_receipt(
                sam_res,
                verdicts,
                prefilter_receipt=prefilter_receipt,
            )
            out["results"]["decision_coverage_verdict"] = decision_coverage
            counts = {}
            for v in verdicts.values():
                counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
            print(f"[triage] {counts.get('pursue', 0)} pursue · "
                  f"{counts.get('monitor', 0)} monitor · "
                  f"{counts.get('discard', 0)} discard"
                  + (f" · {counts.get('unscreened', 0)} unscreened"
                     if counts.get('unscreened') else ""), file=sys.stderr)
            print(f"[RESULT] {counts.get('pursue', 0)} PURSUE-GRADE OPPORTUNITIES "
                  f"(screened from {n_opps}; responses due today → forward)",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            out["results"]["triage"] = {"error": str(e)}
            out["results"]["decision_coverage_verdict"] = (
                _decision_coverage_receipt(
                    sam_res,
                    deterministic_verdicts,
                    prefilter_receipt=prefilter_receipt,
                )
            )
            print(f"[triage] failed (non-blocking): {e}", file=sys.stderr)
            print(f"[RESULT] {n_opps} UNSCREENED OPPORTUNITIES (triage failed — "
                  f"review manually or re-run)", file=sys.stderr)
    elif n_opps == 0:
        out["results"]["decision_coverage_verdict"] = {
            "verdict": "screened-zero",
            "complete": True,
            "candidate_census": 0,
            "identified_candidates": 0,
            "dispositioned": 0,
            "counts": {},
            "missing_count": 0,
            "unexpected_count": 0,
            "invalid_or_unscreened_count": 0,
            "duplicate_notice_id_count": 0,
            "prefilter_complete": True,
            "missing_examples": [],
            "unexpected_examples": [],
            "invalid_or_unscreened_examples": [],
        }
        print("[RESULT] 0 OPPORTUNITIES FOUND FOR REVIEW "
              "(complete screened-zero SAM candidate census)",
              file=sys.stderr)
    else:
        out["results"]["decision_coverage_verdict"] = {
            "verdict": "not-run",
            "complete": False,
            "candidate_census": n_opps,
            "identified_candidates": n_opps,
            "dispositioned": 0,
            "counts": {},
            "missing_count": n_opps,
            "unexpected_count": 0,
            "invalid_or_unscreened_count": 0,
            "duplicate_notice_id_count": 0,
            "prefilter_complete": False,
            "missing_examples": [],
            "unexpected_examples": [],
            "invalid_or_unscreened_examples": [],
        }
        print(f"[RESULT] {n_opps} OPPORTUNITIES FOUND FOR REVIEW "
              f"(responses due today → forward)", file=sys.stderr)

    # ── RESEARCH PICTURE: Claude's second pass over EVERYTHING the APIs said.
    # This is what makes the run materially better than a bare Claude search —
    # synthesis over structured ground truth, produced as a standing artifact
    # on every run.
    if "picture" not in args.skip:
        from agents.decisions.research_picture import (
            compose_research_picture, distill, render_markdown,
        )
        print("[picture] Anthropic synthesizing the research picture across "
              "all sources ...", file=sys.stderr)
        try:
            picture = compose_research_picture(
                args.client,
                getattr(strategy, "pursuit_strategy", "") or "",
                out["results"],
                operator_focus=out.get("search_scope"),
            )
            out["results"]["research_picture"] = json.loads(picture.model_dump_json())
            review_dir = os.path.join(os.path.dirname(__file__), "data", "review")
            os.makedirs(review_dir, exist_ok=True)
            packet_suffix = (f".{bound_workstation}"
                             if native_workstation else "")
            md_path = os.path.join(
                review_dir, f"{slug}{packet_suffix}.research_picture.md")
            with open(md_path, "w") as f:
                f.write(render_markdown(picture, sweep=distill(out["results"]),
                                        results=out["results"]))
            print(f"[picture] {len(picture.top_opportunities)} top opportunities · "
                  f"{len(picture.demand_signals)} demand signals · "
                  f"{len(picture.watchlist)} on watchlist", file=sys.stderr)
            print(f"[picture] HEADLINE: {picture.headline}", file=sys.stderr)
            print(f"[picture] artifact -> {md_path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            out["results"]["research_picture"] = {"error": str(e)}
            print(f"[picture] failed (non-blocking): {e}", file=sys.stderr)

    # Single write, AFTER triage + picture, so the artifact the UI reads
    # always carries the verdicts and the synthesis.
    from datetime import datetime as _dt, timezone as _timezone
    out["generated_at"] = _dt.now(_timezone.utc).isoformat(timespec="seconds")
    from tools.artifacts import atomic_write_json
    atomic_write_json(path, out)
    print(f"[done] -> {path}", file=sys.stderr)

    # Additive Assess ledger sidecar: the established sweep/report path never
    # depends on it. Materialization failures remain visible for engineering
    # without taking the operator's assessment capability offline.
    # Creation-free (2026-07-12): the current pointer is the operator's
    # cutover switch; a sweep must refresh an EXISTING strict run, never mint
    # one (the NETSCOUT DHS sweep implicitly cut the client over).
    if not native_workstation:
        from tools.assess_refresh import refresh_current_assess_run_if_active
        print("[assess-ledger] "
              + refresh_current_assess_run_if_active(
                  args.client, sweep_path=path),
              file=sys.stderr)
    else:
        print("[assess-ledger] native workstation: pointer refresh suppressed",
              file=sys.stderr)

    # Sweep snapshot: persist a slim, diffable record of this pull so the next
    # one can say "new since last refresh" (watchlist). Non-blocking side effect.
    try:
        from tools.snapshots import save_snapshot, sweep_slim_records
        _snap_key = (f"{slug}.{bound_workstation}" if native_workstation
                     else f"{slug}.{_designator}" if _designator else slug)
        save_snapshot("sweep", _snap_key, sweep_slim_records(out["results"]))
    except Exception as e:  # noqa: BLE001 — never sink the sweep
        print(f"[snapshot] skipped (non-blocking): {e}", file=sys.stderr)

    # The outreach rail grows with every research pass — AFTER the artifact is
    # written, so growth ranks against THIS sweep's triage verdicts, not the
    # previous run's. Non-blocking side effect, same toggle as the harvest.
    if not native_workstation:
        try:
            from tools.contact_graph.harvest import contact_graph_enabled
            if contact_graph_enabled():
                from tools.contact_graph.outreach import OutreachList
                grown = OutreachList().grow_from_research()
                if grown["added"]:
                    print(f"[outreach] +{grown['added']} new outreach-relevant contacts "
                          f"(list now {grown['total']})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — side effect only
            print(f"[outreach] growth skipped (non-blocking): {e}", file=sys.stderr)
    else:
        print("[outreach] native workstation: shared growth suppressed",
              file=sys.stderr)

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
