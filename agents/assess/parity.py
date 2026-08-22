"""Offline legacy-versus-strict Live SAM parity report.

This helper spends no model tokens and changes no gate. It is intended to be
run before a client's current pointer is backfilled so an operator can inspect
every pursuit/count/verdict delta before strict ledger truth becomes active.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agents.assess.binding import build_binding, digest
from agents.assess.live_report import (
    LiveReportState,
    project_sweep_live_report,
    resolve_current_live_report,
)


@contextmanager
def _isolated_projection_entities():
    """Keep offline report projection from writing entity-resolution traffic.

    The override is deliberately active only while legacy/strict documents are
    projected. CURRENT-pointer validation must hash the operator's real entity
    crosswalk, because that exact reference input is part of the immutable run.
    """
    had_override = "LILA_ENTITIES_DIR" in os.environ
    prior_override = os.environ.get("LILA_ENTITIES_DIR")
    with tempfile.TemporaryDirectory(prefix="lila-parity-entities-") as root:
        os.environ["LILA_ENTITIES_DIR"] = root
        try:
            yield
        finally:
            if had_override and prior_override is not None:
                os.environ["LILA_ENTITIES_DIR"] = prior_override
            else:
                os.environ.pop("LILA_ENTITIES_DIR", None)


def _load_optional(path: Optional[str | Path]) -> Optional[dict]:
    if not path:
        return None
    selected = Path(path)
    if not selected.exists():
        return None
    payload = json.loads(selected.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{selected} is not a JSON object")
    return payload


def _as_of(searches: dict, value: Optional[datetime]) -> datetime:
    if value is not None:
        parsed = value
    else:
        raw = searches.get("generated_at")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("sweep generated_at is required for strict parity")
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("strict parity as_of must be timezone-aware")
    return parsed


def _board_rows(board) -> list[dict[str, Any]]:
    return [{
        "rank": row.rank,
        "source_id": row.source_id,
        "solicitation": row.solicitation,
        "title": row.title,
        "agency": row.agency,
        "response_deadline": row.response_deadline,
        "grade": row.grade.letter,
        "amendment_postings": [
            amendment.get("source_id") for amendment in row.amendments],
    } for row in board.pursuits]


def _raw_notice_rows(searches: dict) -> dict[str, dict[str, Any]]:
    """Mirror the legacy SAM provenance boundary for packet presentation."""
    results = searches.get("results")
    results = results if isinstance(results, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for row in results.get("sam.gov") or []:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or "").strip()
        source = str(row.get("source") or "").strip().lower()
        if source_id and source in {"", "sam.gov"}:
            out[source_id] = row
    return out


def _diagnostics_for(source_id: str, diagnostics: tuple[str, ...]) -> list[str]:
    """Select the ledger adapter's own rejection messages for one posting."""
    markers = (
        f"SAM ledger rejected {source_id}:",
        f"SAM posting {source_id} ",
        f"SAM posting {source_id}:",
    )
    return [message for message in diagnostics
            if any(marker in message for marker in markers)]


def _failure_leg_from_reason(reason: str) -> str:
    """Give an operator label to an existing ledger diagnostic.

    This function does not decide eligibility. The strict adapter already did
    that. It only groups its visible reason text into a stable packet label.
    """
    lowered = reason.casefold()
    if "outside the operator-selected agency scope" in lowered:
        return "operator_scope_boundary"
    if "future deadline" in lowered or "deadline" in lowered and "past" in lowered:
        return "future_deadline"
    if "active status" in lowered or "inactive" in lowered or "closed" in lowered:
        return "active_notice_state"
    if "census" in lowered:
        return "complete_sam_census"
    if "attachment" in lowered:
        return "attachment_inventory_review"
    if "human requirement review" in lowered \
            or "explicit human approval" in lowered \
            or "operator rejected" in lowered:
        return "human_notice_requirement_review"
    if "core capability term" in lowered or "requirement text" in lowered:
        return "notice_requirement_evidence"
    if "metadata-only triage" in lowered:
        return "notice_requirement_evidence"
    if "source/raw identity" in lowered or "notice identity" in lowered \
            or "source url" in lowered or "does not match" in lowered:
        return "notice_identity_and_provenance"
    if "depth" in lowered or "description" in lowered:
        return "current_notice_depth"
    if "ambiguous" in lowered or "conflicting families" in lowered:
        return "effective_amendment_identity"
    if "notice type" in lowered or "not bid_now" in lowered \
            or "market research" in lowered or "presolicitation" in lowered \
            or "special notice" in lowered or "special_notice" in lowered:
        return "bid_now_notice_phase"
    if "missing notice id, title, or agency" in lowered:
        return "required_notice_identity_fields"
    return "strict_ledger_eligibility"


def _strict_disposition(projected_notice, diagnostics: tuple[str, ...]) -> dict:
    """Describe a strict record using only ledger-produced state and gaps."""
    from agents.assess.contracts import LiveClassification
    from agents.assess.live_report import report_verdict

    record = projected_notice.record
    strict_state = report_verdict(record)
    reason = ""
    if record.classification == LiveClassification.AWARDED_OR_CLOSED:
        if (record.response_deadline is not None
                and record.response_deadline < record.verified_at.date()):
            reason = (
                "Ledger classified the notice as awarded or closed because "
                "its deadline was past at the UTC evidence cutoff")
        else:
            reason = (
                "Ledger classified the notice as awarded or closed from the "
                "official notice state")
    elif record.classification in {
            LiveClassification.MARKET_RESEARCH,
            LiveClassification.PRESOLICITATION,
            LiveClassification.SPECIAL_NOTICE,
            LiveClassification.AMENDMENT,
    }:
        reason = (
            f"Ledger classification {record.classification.value} is not "
            "BID_NOW")
    else:
        reason = str(record.attachment_gap or "").strip()
    if not reason and strict_state != "pursue":
        matched = _diagnostics_for(record.notice_id, diagnostics)
        reason = matched[0] if matched else (
            f"Ledger classification {record.classification.value} with "
            f"recommendation {record.recommendation.value} is not BID_NOW/PURSUE")
    elif not reason:
        reason = "The strict ledger passed every BID_NOW/PURSUE eligibility leg"
    return {
        "strict_state": strict_state,
        "failed_eligibility_leg": (
            "all_bid_now_legs_passed" if strict_state == "pursue"
            else _failure_leg_from_reason(reason)),
        "ledger_reason": reason or None,
        "record_id": record.record_id,
        "current_notice_id": record.notice_id,
        "classification": record.classification.value,
        "recommendation": record.recommendation.value,
    }


def _rejected_disposition(
    source_id: str,
    diagnostics: tuple[str, ...],
) -> dict[str, Any]:
    matched = _diagnostics_for(source_id, diagnostics)
    reason = (matched[0] if matched else
              "The strict ledger did not accept this SAM posting; inspect the "
              "projection diagnostics for the collection-level rejection")
    return {
        "strict_state": "not_in_strict_ledger",
        "failed_eligibility_leg": _failure_leg_from_reason(reason),
        "ledger_reason": reason,
        "record_id": None,
        "current_notice_id": None,
        "classification": None,
        "recommendation": None,
    }


def _packet_row(
    *,
    surface: str,
    outcome: str,
    source_id: str,
    legacy_state: str,
    raw: Optional[dict],
    disposition: dict[str, Any],
    title: Optional[str] = None,
    agency: Optional[str] = None,
    deadline: Optional[str] = None,
    detail: Optional[str] = None,
) -> dict[str, Any]:
    raw = raw or {}

    return {
        "surface": surface,
        "outcome": outcome,
        "source_id": source_id,
        "title": _packet_clean(title or raw.get("title") or source_id),
        "agency": _packet_clean(agency or raw.get("agency")),
        "deadline": _packet_clean(deadline or raw.get("response_deadline")),
        "legacy_state": legacy_state,
        "strict_state": disposition["strict_state"],
        "failed_eligibility_leg": disposition["failed_eligibility_leg"],
        "ledger_reason": _packet_clean(disposition["ledger_reason"]),
        "legacy_detail": _packet_clean(detail),
        "strict_record_id": disposition["record_id"],
        "strict_current_notice_id": disposition["current_notice_id"],
        "strict_classification": disposition["classification"],
        "strict_recommendation": disposition["recommendation"],
    }


def _packet_clean(value: Any) -> Any:
    return value.replace("\u2014", " - ") if isinstance(value, str) else value


def _board_current_posting_transition(row: Any, projected_notice: Any) -> dict:
    record = projected_notice.record
    current = projected_notice.current
    explanation = (
        "The actionable solicitation family remains on the pursuit board; "
        f"strict family {record.record_id} selects current posting "
        f"{record.notice_id} instead of legacy posting {row.source_id}")
    return {
        "surface": "board_current_posting",
        "outcome": "current_posting_changed",
        "source_id": row.source_id,
        "title": _packet_clean(row.title),
        "agency": _packet_clean(row.agency),
        "deadline": _packet_clean(row.response_deadline),
        "legacy_state": "pursue",
        "strict_state": "pursue",
        "failed_eligibility_leg": "superseded_by_current_family_posting",
        "identity_basis": "strict_solicitation_family",
        "identity_cause": "superseded_by_current_family_posting",
        "ledger_reason": explanation,
        "legacy_detail": None,
        "strict_record_id": record.record_id,
        "strict_current_notice_id": record.notice_id,
        "strict_title": _packet_clean(record.title),
        "strict_agency": _packet_clean(record.agency),
        "strict_deadline": (
            record.response_deadline.isoformat()
            if record.response_deadline is not None else None),
        "strict_classification": record.classification.value,
        "strict_recommendation": record.recommendation.value,
        "strict_current_source_id": current.get("source_id"),
    }


def _build_cutover_decision_packet(
    *,
    client_name: str,
    searches: dict,
    legacy_document: Any,
    strict_document: Any,
    projected: Any,
    diagnostics: tuple[str, ...],
    as_of: datetime,
    diagnostic_scope_override: Optional[str],
) -> dict[str, Any]:
    """Reconcile every changed legacy Live SAM row against strict state."""
    raw_by_id = _raw_notice_rows(searches)
    strict_by_posting: dict[str, Any] = {}
    if projected is not None:
        for projected_notice in projected.notices:
            for posting in projected_notice.postings:
                source_id = str(posting.get("source_id") or "").strip()
                if source_id:
                    strict_by_posting[source_id] = projected_notice

    def disposition(source_id: str) -> dict[str, Any]:
        strict_notice = strict_by_posting.get(source_id)
        if strict_notice is None:
            return _rejected_disposition(source_id, diagnostics)
        return _strict_disposition(strict_notice, diagnostics)

    strict_board_ids = {
        row.source_id for row in strict_document.board.pursuits
    } if strict_document is not None else set()
    strict_actionable_families = {
        notice.record.record_id: notice
        for notice in (projected.actionable if projected is not None else ())
    }
    board_rows = []
    board_current_posting_transitions = []
    for row in legacy_document.board.pursuits:
        strict_notice = strict_by_posting.get(row.source_id)
        strict_family_id = (
            strict_notice.record.record_id if strict_notice is not None else None)
        surviving = strict_actionable_families.get(strict_family_id)
        if surviving is not None:
            if row.source_id != surviving.record.notice_id:
                board_current_posting_transitions.append(
                    _board_current_posting_transition(row, surviving))
            continue
        board_rows.append(_packet_row(
            surface="board",
            outcome="dropped",
            source_id=row.source_id,
            legacy_state="pursue",
            raw=raw_by_id.get(row.source_id),
            disposition=disposition(row.source_id),
            title=row.title,
            agency=row.agency,
            deadline=row.response_deadline,
        ))

    strict_watch_counts = Counter(
        (row.source, row.id, row.kind)
        for row in strict_document.watchlist.entries
    ) if strict_document is not None else Counter()
    watchlist_rows = []
    for row in legacy_document.watchlist.entries:
        key = (row.source, row.id, row.kind)
        if strict_watch_counts[key] > 0:
            strict_watch_counts[key] -= 1
            continue
        source_id = str(row.id)
        raw = raw_by_id.get(source_id)
        resolved = disposition(source_id)
        if (row.source == "sam.gov" and row.kind == "standing"
                and resolved["strict_state"] == "monitor"
                and resolved["current_notice_id"]
                and resolved["current_notice_id"] != source_id):
            resolved = {
                **resolved,
                "failed_eligibility_leg": (
                    "superseded_by_current_family_posting"),
                "ledger_reason": (
                    "The strict ledger grouped this posting into solicitation "
                    f"family {resolved['record_id']} and uses current posting "
                    f"{resolved['current_notice_id']} for the standing watchlist row"),
            }
        watchlist_rows.append(_packet_row(
            surface="watchlist",
            outcome="dropped",
            source_id=source_id,
            legacy_state=row.kind,
            raw=raw,
            disposition=resolved,
            title=row.title,
            detail=row.detail,
        ))

    results = searches.get("results")
    results = results if isinstance(results, dict) else {}
    triage = results.get("triage")
    triage = triage if isinstance(triage, dict) else {}
    monitor_rows = []
    verdict_rows = []
    for source_id, triage_row in sorted(triage.items()):
        if source_id not in raw_by_id or not isinstance(triage_row, dict):
            continue
        legacy_state = str(triage_row.get("verdict") or "").strip().lower()
        if not legacy_state:
            continue
        resolved = disposition(source_id)
        strict_state = resolved["strict_state"]
        if legacy_state == "monitor" and strict_state != "monitor":
            monitor_rows.append(_packet_row(
                surface="monitor",
                outcome=("dropped" if strict_state == "not_in_strict_ledger"
                         else "reclassified"),
                source_id=source_id,
                legacy_state=legacy_state,
                raw=raw_by_id[source_id],
                disposition=resolved,
                detail=str(triage_row.get("reason") or "") or None,
            ))
        if legacy_state != strict_state:
            verdict_rows.append(_packet_row(
                surface="triage_verdict",
                outcome=("dropped" if strict_state == "not_in_strict_ledger"
                         else "reclassified"),
                source_id=source_id,
                legacy_state=legacy_state,
                raw=raw_by_id[source_id],
                disposition=resolved,
                detail=str(triage_row.get("reason") or "") or None,
            ))

    strict_monitor_ids = {
        source_id for source_id in strict_by_posting
        if disposition(source_id)["strict_state"] == "monitor"
    }
    legacy_monitor_ids = {
        source_id for source_id, row in triage.items()
        if source_id in raw_by_id and isinstance(row, dict)
        and str(row.get("verdict") or "").strip().lower() == "monitor"
    }
    monitor_entered_ids = sorted(strict_monitor_ids - legacy_monitor_ids)
    monitor_exited_ids = sorted(legacy_monitor_ids - strict_monitor_ids)

    transition_counts: Counter = Counter()
    for source_id in sorted(raw_by_id):
        triage_row = triage.get(source_id)
        legacy_state = (
            str(triage_row.get("verdict") or "").strip().lower()
            if isinstance(triage_row, dict) else "") or "unclassified"
        strict_state = disposition(source_id)["strict_state"]
        if legacy_state != strict_state:
            transition_counts[(legacy_state, strict_state)] += 1
    legacy_verdicts = legacy_document.verdict_totals
    strict_verdicts = strict_document.verdict_totals
    verdict_reconciliation: dict[str, dict[str, Any]] = {}
    for verdict in sorted(set(legacy_verdicts) | set(strict_verdicts)):
        computed = legacy_verdicts.get(verdict, 0)
        for (legacy_state, strict_state), count in transition_counts.items():
            if legacy_state == verdict and strict_state != verdict:
                computed -= count
            if legacy_state != verdict and strict_state == verdict:
                computed += count
        verdict_reconciliation[verdict] = {
            "legacy": legacy_verdicts.get(verdict, 0),
            "strict": strict_verdicts.get(verdict, 0),
            "strict_recomputed_from_transitions": computed,
            "reconciled": computed == strict_verdicts.get(verdict, 0),
        }

    legacy_board_ids = {row.source_id for row in legacy_document.board.pursuits}
    board_exited_ids = sorted(legacy_board_ids - strict_board_ids)
    board_packet_ids = sorted(row["source_id"] for row in board_rows)
    board_transition_ids = sorted(
        row["source_id"] for row in board_current_posting_transitions)
    watchlist_entered = sum(strict_watch_counts.values())
    watchlist_exited = len(watchlist_rows)
    strict_watch_count = len(strict_document.watchlist.entries)
    watchlist_recomputed = (
        len(legacy_document.watchlist.entries)
        - watchlist_exited
        + watchlist_entered)
    board_reconciled = sorted(
        [*board_packet_ids, *board_transition_ids]) == board_exited_ids
    monitor_reconciled = (
        sorted(row["source_id"] for row in monitor_rows) == monitor_exited_ids
        and len(legacy_monitor_ids) - len(monitor_exited_ids)
        + len(monitor_entered_ids) == len(strict_monitor_ids)
    )
    watchlist_reconciled = watchlist_recomputed == strict_watch_count
    verdicts_reconciled = all(
        row["reconciled"] for row in verdict_reconciliation.values())

    rows = [
        *board_current_posting_transitions,
        *board_rows,
        *watchlist_rows,
        *monitor_rows,
        *verdict_rows,
    ]
    unexplained = [
        row for row in rows
        if not row["failed_eligibility_leg"]
        or not row["ledger_reason"]
        or row["failed_eligibility_leg"] == "strict_ledger_eligibility"
        or str(row["ledger_reason"]).startswith(
            "The strict ledger did not accept this SAM posting")
    ]
    return {
        "schema_version": 1,
        "client": client_name,
        "as_of": as_of.isoformat(),
        "operation": "offline_projection_only",
        "diagnostic_scope_override": diagnostic_scope_override,
        "summary": {
            "legacy_board_rows": len(legacy_document.board.pursuits),
            "board_rows_explained": len(board_rows),
            "board_current_posting_transitions": len(
                board_current_posting_transitions),
            "legacy_watchlist_rows": len(legacy_document.watchlist.entries),
            "watchlist_rows_explained": len(watchlist_rows),
            "legacy_monitor_rows": len(legacy_monitor_ids),
            "monitor_rows_explained": len(monitor_rows),
            "monitor_rows_entered": len(monitor_entered_ids),
            "triage_rows_reclassified": len(verdict_rows),
            "unexplained_rows": len(unexplained),
            "every_changed_row_explained": not unexplained,
            "board_delta_reconciled": board_reconciled,
            "watchlist_delta_reconciled": watchlist_reconciled,
            "monitor_delta_reconciled": monitor_reconciled,
            "verdict_deltas_reconciled": verdicts_reconciled,
            "all_parity_deltas_reconciled": (
                board_reconciled and watchlist_reconciled
                and monitor_reconciled and verdicts_reconciled
                and not unexplained),
        },
        "reconciliation": {
            "board_exited_ids": board_exited_ids,
            "board_packet_ids": board_packet_ids,
            "board_current_posting_transition_ids": board_transition_ids,
            "watchlist": {
                "legacy": len(legacy_document.watchlist.entries),
                "exited": watchlist_exited,
                "entered": watchlist_entered,
                "strict": strict_watch_count,
                "strict_recomputed": watchlist_recomputed,
            },
            "monitor": {
                "legacy": len(legacy_monitor_ids),
                "exited_ids": monitor_exited_ids,
                "entered_ids": monitor_entered_ids,
                "strict": len(strict_monitor_ids),
            },
            "verdict_transitions": {
                f"{legacy_state}->{strict_state}": count
                for (legacy_state, strict_state), count
                in sorted(transition_counts.items())
            },
            "verdict_totals": verdict_reconciliation,
        },
        "board": board_rows,
        "board_current_posting_transitions": (
            board_current_posting_transitions),
        "watchlist": watchlist_rows,
        "monitors": monitor_rows,
        "triage_reclassifications": verdict_rows,
        "rows": rows,
    }


def _markdown_cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return " ".join(str(value).split()).replace("\u2014", " - ").replace(
        "|", "\\|")


def render_cutover_decision_packet(report: dict[str, Any]) -> str:
    """Render the embedded decision packet as one operator-readable table."""
    packet = report.get("cutover_decision_packet") or {}
    summary = packet.get("summary") or {}
    status = "ELIGIBLE" if report.get("cutover_eligible") else "NOT ELIGIBLE"
    lines = [
        f"# {report.get('client', 'Client')} cutover decision packet",
        "",
        f"- Cutover status: **{status}**",
        f"- Scope: `{report.get('scope_designator', 'unavailable')}`",
        f"- UTC evidence cutoff: `{report.get('as_of', 'unknown')}`",
        "- Operation: offline projection only; no pointer was written or activated.",
    ]
    override = report.get("diagnostic_scope_override")
    if override:
        lines.append(
            f"- Diagnostic scope override: `{override}`; this packet cannot "
            "authorize cutover.")
    lines.extend([
        "",
        "## Reconciliation",
        "",
        f"- Board rows explained: {summary.get('board_rows_explained', 0)}",
        f"- Board current-posting transitions: "
        f"{summary.get('board_current_posting_transitions', 0)}",
        f"- Watchlist rows explained: {summary.get('watchlist_rows_explained', 0)}",
        f"- Monitor rows explained: {summary.get('monitor_rows_explained', 0)}",
        f"- Triage verdicts reclassified: "
        f"{summary.get('triage_rows_reclassified', 0)}",
        f"- Unexplained changed rows: {summary.get('unexplained_rows', 0)}",
    ])
    lines.extend([
        "",
        "## Row decisions",
        "",
        "| Surface | Outcome | Notice | Legacy to strict | Failed leg | Title | Agency | Deadline | Ledger reason |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    rows = packet.get("rows") or []
    if not rows:
        lines.append(
            "| none | unchanged | - | - | - | No changed legacy Live SAM rows | - | - | - |")
    for row in rows:
        transition = f"{row.get('legacy_state')} -> {row.get('strict_state')}"
        values = (
            row.get("surface"), row.get("outcome"), row.get("source_id"),
            transition, row.get("failed_eligibility_leg"), row.get("title"),
            row.get("agency"), row.get("deadline"), row.get("ledger_reason"),
        )
        lines.append("| " + " | ".join(_markdown_cell(v) for v in values) + " |")
    problems = report.get("cutover_problems") or []
    lines.extend(["", "## Cutover holds", ""])
    if problems:
        lines.extend(f"- {_markdown_cell(problem)}" for problem in problems)
    else:
        lines.append("- No parity-level hold was found.")
    return "\n".join(lines) + "\n"


def build_live_parity_report(
    client_name: str,
    searches: dict,
    profile: Any,
    *,
    sweep_artifact: str,
    qualify: Optional[dict] = None,
    requirement_reviews: Optional[dict] = None,
    as_of: Optional[datetime] = None,
    state_dir: Optional[str | Path] = None,
    review_dir: Optional[str | Path] = None,
    diagnostic_scope_override: Optional[str] = None,
) -> dict[str, Any]:
    """Compare the same sweep through legacy and strict Live SAM adapters."""
    from agents.assess.ledger import _scope_designator, scope_from_sweep
    from agents.reports.document import build_document

    effective_as_of = _as_of(searches, as_of)
    projection_problem = None
    projected = None
    diagnostics: tuple[str, ...] = ()
    projection_searches = searches
    if diagnostic_scope_override:
        projection_searches = copy.deepcopy(searches)
        if diagnostic_scope_override == "all":
            projection_searches["search_scope"] = {"all": True}
        elif diagnostic_scope_override.startswith("agency:"):
            agency_spec = diagnostic_scope_override.split(":", 1)[1].strip()
            if not agency_spec:
                raise ValueError("diagnostic agency scope override is empty")
            if ":" in agency_spec:
                abbr, name = agency_spec.split(":", 1)
                agency: Any = {"abbr": abbr.strip(), "name": name.strip()}
                if not agency["abbr"] or not agency["name"]:
                    raise ValueError(
                        "diagnostic agency override requires abbreviation and name")
            else:
                agency = agency_spec
            projection_searches["search_scope"] = {
                "mode": "focus", "agencies": [agency]}
        else:
            raise ValueError(
                "diagnostic scope override must be 'all' or 'agency:<name>'")
    with _isolated_projection_entities():
        legacy_document = build_document(
            client_name,
            searches=searches,
            qualify=qualify,
            as_of=effective_as_of.date(),
            _live_report=False,
            _cap_profile=profile,
        )
        try:
            scope = scope_from_sweep(projection_searches)
            binding = build_binding(
                scope_designator=_scope_designator(scope),
                sweep_artifact=Path(sweep_artifact).name,
                sweep=projection_searches,
                profile=profile,
            )
            projected, diagnostics = project_sweep_live_report(
                client_name,
                projection_searches,
                profile,
                binding,
                as_of=effective_as_of,
                requirement_reviews_payload=requirement_reviews,
            )
            strict_document = build_document(
                client_name,
                searches=projection_searches,
                qualify=qualify,
                as_of=effective_as_of.date(),
                _live_report=projected,
                _cap_profile=profile,
            )
        except (TypeError, ValueError) as exc:
            projection_problem = f"strict projection unavailable: {exc}"
            binding = {
                "scope_designator": "unavailable",
                "sweep_artifact": Path(sweep_artifact).name,
                "sweep_sha256": digest(searches),
                "profile_sha256": digest(profile),
            }
            strict_document = None
    current = resolve_current_live_report(
        client_name,
        searches,
        profile,
        state_dir=state_dir,
        review_dir=review_dir,
        effective_date=effective_as_of.date(),
    )

    legacy_rows = _board_rows(legacy_document.board)
    legacy_counts = legacy_document.counts()
    legacy_verdicts = legacy_document.verdict_totals
    strict_rows = (_board_rows(strict_document.board)
                   if strict_document is not None else None)
    strict_counts = (strict_document.counts()
                     if strict_document is not None else None)
    strict_verdicts = (strict_document.verdict_totals
                       if strict_document is not None else None)
    legacy_ids = {row["source_id"] for row in legacy_rows}
    strict_ids = ({row["source_id"] for row in strict_rows}
                  if strict_rows is not None else set())
    all_count_keys = (sorted(set(legacy_counts) | set(strict_counts or {}))
                      if strict_counts is not None else [])
    all_verdict_keys = (
        sorted(set(legacy_verdicts) | set(strict_verdicts or {}))
        if strict_verdicts is not None else [])

    eligibility_problems: list[str] = []
    if diagnostic_scope_override:
        eligibility_problems.append(
            "diagnostic scope override was used; this comparison cannot "
            "activate or persist a client cutover")
    if projection_problem:
        eligibility_problems.append(projection_problem)
    if current.state == LiveReportState.INVALID:
        eligibility_problems.append(
            current.problem or "current strict ledger pointer is invalid")
    if projected is not None and projected.live_coverage_complete is not True:
        eligibility_problems.append(
            "strict SAM coverage is not complete for this projected sweep")
    decision_packet = (
        _build_cutover_decision_packet(
            client_name=client_name,
            searches=projection_searches,
            legacy_document=legacy_document,
            strict_document=strict_document,
            projected=projected,
            diagnostics=diagnostics,
            as_of=effective_as_of,
            diagnostic_scope_override=diagnostic_scope_override,
        )
        if strict_document is not None and projected is not None
        else {
            "schema_version": 1,
            "available": False,
            "problem": projection_problem,
            "client": client_name,
            "as_of": effective_as_of.isoformat(),
            "operation": "offline_projection_only",
            "diagnostic_scope_override": diagnostic_scope_override,
            "summary": {
                "board_rows_explained": 0,
                "board_current_posting_transitions": 0,
                "watchlist_rows_explained": 0,
                "monitor_rows_explained": 0,
                "triage_rows_reclassified": 0,
                "unexplained_rows": 0,
                "every_changed_row_explained": False,
            },
            "board": [],
            "board_current_posting_transitions": [],
            "watchlist": [],
            "monitors": [],
            "triage_reclassifications": [],
            "rows": [],
        }
    )
    packet_summary = decision_packet.get("summary") or {}
    if not (
        packet_summary.get("all_parity_deltas_reconciled") is True
        and packet_summary.get("every_changed_row_explained") is True
    ):
        eligibility_problems.append(
            "cutover decision packet has unexplained or unreconciled changed rows")
    report = {
        "schema_version": 1,
        "client": client_name,
        "scope_designator": binding["scope_designator"],
        "sweep_artifact": Path(sweep_artifact).name,
        "sweep_sha256": digest(searches),
        "profile_sha256": binding["profile_sha256"],
        "as_of": effective_as_of.isoformat(),
        "diagnostic_scope_override": diagnostic_scope_override,
        "current_pointer": {
            "state": current.state.value,
            "run_id": current.run_id,
            "problem": current.problem,
            "report_truth_active": current.state == LiveReportState.CURRENT,
            "can_release": current.can_release,
        },
        "cutover_eligible": not eligibility_problems,
        "cutover_problems": eligibility_problems,
        "strict_projection": {
            "available": projected is not None,
            "problem": projection_problem,
            "run_id": projected.run_id if projected is not None else None,
            "live_records": (len(projected.notices)
                             if projected is not None else None),
            "actionable_records": (len(projected.actionable)
                                   if projected is not None else None),
            "sam_coverage_complete": (
                projected.live_coverage_complete
                if projected is not None else None),
            "diagnostics": list(diagnostics),
            "projected_sweep_sha256": (
                binding["sweep_sha256"] if projected is not None else None),
        },
        "legacy": {
            "available": True,
            "board_rows": legacy_rows,
            "counts": legacy_counts,
            "verdict_totals": legacy_verdicts,
            "gaps": legacy_document.gaps,
        },
        "strict": {
            "available": strict_document is not None,
            "problem": projection_problem,
            "board_rows": strict_rows,
            "counts": strict_counts,
            "verdict_totals": strict_verdicts,
            "gaps": (strict_document.gaps
                     if strict_document is not None else None),
        },
        "delta": ({
            "board_entered": sorted(strict_ids - legacy_ids),
            "board_exited": sorted(legacy_ids - strict_ids),
            "counts": {
                key: {
                    "legacy": legacy_counts.get(key, 0),
                    "strict": strict_counts.get(key, 0),
                }
                for key in all_count_keys
                if legacy_counts.get(key, 0) != strict_counts.get(key, 0)
            },
            "verdict_totals": {
                key: {
                    "legacy": legacy_verdicts.get(key, 0),
                    "strict": strict_verdicts.get(key, 0),
                }
                for key in all_verdict_keys
                if legacy_verdicts.get(key, 0) != strict_verdicts.get(key, 0)
            },
        } if strict_document is not None else {
            "available": False,
            "problem": projection_problem,
        }),
        "parity": (
            strict_document is not None
            and legacy_rows == strict_rows
            and legacy_counts == strict_counts
            and legacy_verdicts == strict_verdicts
        ),
        "cutover_decision_packet": decision_packet,
    }
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare legacy and strict Live SAM report truth offline")
    parser.add_argument("--client", required=True)
    parser.add_argument("--sweep-path")
    parser.add_argument("--profile-path")
    parser.add_argument("--qualify-path")
    parser.add_argument("--requirements-path")
    parser.add_argument("--state-dir")
    parser.add_argument("--review-dir")
    parser.add_argument(
        "--as-of",
        help="explicit timezone-aware ISO-8601 cutoff for legacy naive sweeps")
    parser.add_argument(
        "--scope-override",
        help=("diagnostic only: 'all' or 'agency:<abbr>:<name>'; never "
              "authorizes cutover"))
    parser.add_argument("--output")
    parser.add_argument(
        "--packet-output",
        help=("operator-facing Markdown packet path; defaults to the JSON "
              "--output path with a .md suffix"))
    args = parser.parse_args(argv)

    from agents.review import REVIEW_DIR, sweep_artifact_path
    from tools.capability import ClientProfile, profile_path

    sweep_path = Path(args.sweep_path or sweep_artifact_path(args.client))
    profile_file = Path(args.profile_path or profile_path(args.client))
    searches = json.loads(sweep_path.read_text(encoding="utf-8"))
    profile = ClientProfile.model_validate(
        json.loads(profile_file.read_text(encoding="utf-8")))
    slug = "".join(
        character if character.isalnum() else "_"
        for character in args.client).strip("_").lower()
    review_root = Path(args.review_dir or REVIEW_DIR)
    qualify = _load_optional(
        args.qualify_path or review_root / f"{slug}.qualify.json")
    requirements = _load_optional(
        args.requirements_path
        or review_root / f"{slug}.live_requirements.json")
    explicit_as_of = (datetime.fromisoformat(
        args.as_of.strip().replace("Z", "+00:00")) if args.as_of else None)
    report = build_live_parity_report(
        args.client,
        searches,
        profile,
        sweep_artifact=sweep_path.name,
        qualify=qualify,
        requirement_reviews=requirements,
        as_of=explicit_as_of,
        state_dir=args.state_dir,
        review_dir=review_root,
        diagnostic_scope_override=args.scope_override,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        from tools.atomic_io import atomic_write_text
        atomic_write_text(args.output, rendered)
        packet_path = Path(args.packet_output) if args.packet_output else \
            Path(args.output).with_suffix(".md")
        atomic_write_text(packet_path, render_cutover_decision_packet(report))
    elif args.packet_output:
        from tools.atomic_io import atomic_write_text
        atomic_write_text(args.packet_output, render_cutover_decision_packet(report))
        print(rendered, end="")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
