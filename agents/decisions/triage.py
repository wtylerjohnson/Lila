"""Search triage — Claude screens the raw haul before a human ever sees it.

A NAICS lane like 541512 nets everything from CTI platforms to help-desk
staffing. Volume is not signal. This layer runs at the END of the search step:
the fast model reasons through each notice against the client's actual offering
and rules pursue / monitor / discard with a one-line reason. The review banner
then shows a number worth a human's time, and Qualify inherits a shortlist
instead of a haystack.

Runs on the research engine (Sonnet via the Max-plan route) in adaptive
batches. Both item count and serialized context size are bounded so a small
set of evidence-rich notices cannot become one oversized, timeout-prone call.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from agents.decisions.engine import DecisionEngine, research_engine

LAYER = "search-triage"

# The item cap limits output-schema complexity. The character cap limits input
# size after source and amendment evidence are attached. A 30k context remains
# comfortably below the transport/model window while splitting the live Arista
# evidence set into several independent calls instead of one 100k+ call.
DEFAULT_BATCH_SIZE = 12
DEFAULT_BATCH_CHAR_BUDGET = 30_000

SYSTEM_PROMPT = """\
You are the triage layer of a LILA. You receive the
client's pursuit strategy and a batch of federal contract notices caught by a
broad NAICS/keyword net. Most are noise — that is the nature of the net.

Rule each notice, ruthlessly:
- pursue  : a buyer plausibly purchasing the CLIENT'S offering (the thing they
            sell, not merely their industry). A human should read this today.
- monitor : adjacent or early — right buyer/mission but unclear vehicle, or a
            sources-sought that could shape toward the client.
- discard : wrong lane. Staffing augmentation, hardware lots, facilities,
            unrelated software — even when the NAICS matched.

Judge from title, agency, type, NAICS, and supplied official source or
attachment evidence; do not invent details that are not present. Every item
gets a verdict and a reason under 140 characters.
The pursue list is the product: if everything is pursue, the triage failed."""


class TriageItem(BaseModel):
    id: str = Field(description="the notice's source_id, echoed exactly")
    verdict: Literal["pursue", "monitor", "discard"]
    reason: str = Field(max_length=200, description="one line, <=140 chars")


class TriageResult(BaseModel):
    items: list[TriageItem]


_EMPTY_SOLICITATIONS = {"", "-NONE-", "NONE", "N/A", "NA"}


def _raw_payload(notice: dict) -> dict:
    raw = notice.get("raw_payload")
    return raw if isinstance(raw, dict) else {}


def _source_id(notice: dict, fallback: str = "") -> str:
    return str(notice.get("source_id") or notice.get("id") or fallback)


def _solicitation_id(notice: dict) -> str:
    raw = _raw_payload(notice)
    return " ".join(str(
        raw.get("solicitation") or notice.get("solicitation") or ""
    ).split())


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _notice_thread_key(notice: dict, index: int) -> tuple[str, ...]:
    """Return a conservative amendment-thread identity.

    Solicitation numbers are not globally unique.  Different agencies (and
    occasionally different offices inside one agency) reuse short identifiers
    such as ``RFI-01``.  Amendment consolidation therefore requires the
    solicitation, agency, and issuing office to agree.  If agency/office
    identity is absent, fail open to the notice id instead of discarding an
    unrelated record.
    """
    normalized = _solicitation_id(notice).upper()
    if normalized not in _EMPTY_SOLICITATIONS:
        raw = _raw_payload(notice)
        agency = _normalized_text(raw.get("agency") or notice.get("agency"))
        office = _normalized_text(raw.get("office") or notice.get("office"))
        if agency and office:
            return ("solicitation", normalized, agency, office)
    return ("notice", _source_id(notice, f"idx-{index}"))


def _notice_lineage(notice: dict) -> list[dict[str, Any]]:
    """Return complete source and solicitation identity for one candidate."""
    raw = _raw_payload(notice)
    existing = raw.get("triage_notice_lineage")
    if isinstance(existing, list):
        return [dict(row) for row in existing if isinstance(row, dict)]
    return [{
        "source_id": _source_id(notice),
        "solicitation_id": _solicitation_id(notice),
        "posted_date": notice.get("posted_date") or raw.get("posted"),
        "response_deadline": (
            notice.get("response_deadline") or raw.get("deadline")
        ),
    }]


def _combined_lineage(notices: list[dict]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for notice in notices:
        for row in _notice_lineage(notice):
            identity = (
                str(row.get("source_id") or ""),
                str(row.get("solicitation_id") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
    return rows


def _thread_evidence(notices: list[dict]) -> list[dict]:
    """Bounded lineage evidence retained on one representative amendment."""
    out: list[dict] = []
    for notice in notices[-8:]:
        raw = notice.get("raw_payload")
        raw = raw if isinstance(raw, dict) else {}
        contexts = []
        for row in raw.get("screen_evidence_matches") or []:
            if not isinstance(row, dict) or not row.get("context"):
                continue
            contexts.append({
                "term": str(row.get("term") or "")[:120],
                "context": str(row.get("context") or "")[:500],
            })
            if len(contexts) >= 4:
                break
        out.append({
            "source_id": _source_id(notice),
            "solicitation_id": _solicitation_id(notice),
            "posted_date": notice.get("posted_date"),
            "title": str(notice.get("title") or "")[:240],
            "description_snippet": str(raw.get("description_snippet") or "")[:700],
            "matched_contexts": contexts,
        })
    return out


def _latest_notice_threads(
    candidates: list[dict],
) -> tuple[list[dict], dict[str, dict], int]:
    """Select one latest representative per SAM solicitation thread.

    Every source notice remains in the source census. Older revisions receive
    an explicit deterministic disposition and the selected record carries
    bounded lineage evidence, so consolidation reduces duplicate judgment
    without erasing the work that made the thread relevant.
    """
    grouped: dict[tuple[str, ...], list[tuple[int, dict]]] = {}
    for index, notice in enumerate(candidates):
        grouped.setdefault(_notice_thread_key(notice, index), []).append(
            (index, notice))

    selected: list[tuple[int, dict]] = []
    superseded: dict[str, dict] = {}
    for rows in grouped.values():
        if len(rows) == 1:
            selected.append(rows[0])
            continue
        latest_index, latest = max(
            rows,
            key=lambda item: (
                str(item[1].get("posted_date") or ""),
                str(item[1].get("source_id") or item[1].get("id") or ""),
            ),
        )
        representative = dict(latest)
        raw = latest.get("raw_payload")
        representative["raw_payload"] = (
            dict(raw) if isinstance(raw, dict) else {})
        representative["raw_payload"]["triage_thread_members"] = [
            str(row.get("source_id") or row.get("id") or "")
            for _index, row in rows
        ]
        representative["raw_payload"]["triage_thread_evidence"] = (
            _thread_evidence([row for _index, row in rows]))
        lineage = _combined_lineage([row for _index, row in rows])
        representative["raw_payload"]["triage_notice_lineage"] = lineage
        representative["raw_payload"]["triage_thread_solicitations"] = list(
            dict.fromkeys(str(row.get("solicitation_id") or "")
                          for row in lineage)
        )
        selected.append((latest_index, representative))
        selected_id = _source_id(latest)
        for _index, row in rows:
            source_id = _source_id(row)
            if source_id == selected_id:
                continue
            superseded[source_id] = {
                "verdict": "discard",
                "reason": f"superseded SAM revision; screened latest notice {selected_id}",
                "screen": "deterministic-solicitation-thread-v1",
                "superseded_by": selected_id,
            }

    selected.sort(key=lambda item: item[0])
    return [notice for _index, notice in selected], superseded, len(superseded)


def _primary_poc_email(notice: dict) -> str:
    raw = _raw_payload(notice)
    email = str(raw.get("poc_email") or "").strip().casefold()
    if email:
        return email
    contacts = notice.get("contacts")
    if not isinstance(contacts, list):
        return ""
    primary = [row for row in contacts if isinstance(row, dict)
               and str(row.get("contact_type") or "").casefold() == "primary"]
    for row in [*primary, *contacts]:
        if isinstance(row, dict) and row.get("email"):
            return str(row["email"]).strip().casefold()
    return ""


def _deadline_day(notice: dict) -> str:
    raw = _raw_payload(notice)
    value = notice.get("response_deadline") or raw.get("deadline") or ""
    text = str(value).strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else _normalized_text(text)


def _cross_post_key(notice: dict) -> tuple[str, ...] | None:
    """Identify one requirement published as several SAM cross-posts.

    No fuzzy title-only grouping is allowed. A cross-post must agree on the
    normalized title, explicit agency and issuing office, response day,
    primary POC email, notice type, and a substantive description fingerprint.
    Missing evidence disables this consolidation path. This keeps separate
    lots and similarly named requirements independent.
    """
    raw = _raw_payload(notice)
    title = _normalized_text(notice.get("title"))
    agency = _normalized_text(raw.get("agency") or notice.get("agency"))
    office = _normalized_text(raw.get("office") or notice.get("office"))
    deadline = _deadline_day(notice)
    email = _primary_poc_email(notice)
    notice_type = _normalized_text(
        raw.get("type") or notice.get("notice_type") or notice.get("type")
    )
    description = _normalized_text(
        raw.get("description")
        or raw.get("description_snippet")
        or notice.get("description")
    )
    required = (title, agency, office, deadline, email, notice_type)
    if not all(required) or len(description) < 120:
        return None
    description_sha256 = hashlib.sha256(description.encode("utf-8")).hexdigest()
    return (*required, description_sha256)


def _canonical_cross_post(
    rows: list[tuple[int, dict]],
) -> tuple[int, dict]:
    """Prefer the least-suffixed solicitation, then the latest posting."""
    return max(rows, key=lambda item: (
        not bool(re.search(r"[\(\[\{]", _solicitation_id(item[1]))),
        -len(_solicitation_id(item[1]))
        if _solicitation_id(item[1]) else -10_000,
        str(item[1].get("posted_date") or ""),
        _source_id(item[1]),
    ))


def _canonical_cross_posts(
    candidates: list[dict],
) -> tuple[list[dict], dict[str, dict], int]:
    """Consolidate high-confidence SAM cross-posts after amendment grouping."""
    grouped: dict[tuple[str, ...] | tuple[str, str], list[tuple[int, dict]]] = {}
    for index, notice in enumerate(candidates):
        key = _cross_post_key(notice)
        group_key: tuple[str, ...] | tuple[str, str]
        group_key = key if key is not None else ("notice", _source_id(notice))
        grouped.setdefault(group_key, []).append((index, notice))

    selected: list[tuple[int, dict]] = []
    duplicates: dict[str, dict] = {}
    for rows in grouped.values():
        if len(rows) == 1:
            selected.append(rows[0])
            continue
        selected_index, canonical = _canonical_cross_post(rows)
        representative = dict(canonical)
        representative["raw_payload"] = dict(_raw_payload(canonical))
        member_notices = [row for _index, row in rows]
        lineage = _combined_lineage(member_notices)
        representative["raw_payload"]["triage_notice_lineage"] = lineage
        representative["raw_payload"]["triage_cross_post_members"] = [
            _source_id(row) for row in member_notices
        ]
        representative["raw_payload"]["triage_cross_post_solicitations"] = list(
            dict.fromkeys(str(row.get("solicitation_id") or "")
                          for row in lineage)
        )
        representative["raw_payload"]["triage_cross_post_evidence"] = (
            _thread_evidence(member_notices)
        )
        selected.append((selected_index, representative))
        selected_id = _source_id(canonical)
        for _index, row in rows:
            source_id = _source_id(row)
            if source_id == selected_id:
                continue
            duplicates[source_id] = {
                "verdict": "discard",
                "reason": (
                    "SAM cross-post duplicate; screened canonical notice "
                    f"{selected_id}"
                ),
                "screen": "deterministic-sam-cross-post-v1",
                "superseded_by": selected_id,
                "duplicate_of": selected_id,
            }

    selected.sort(key=lambda item: item[0])
    return [notice for _index, notice in selected], duplicates, len(duplicates)


def _bounded_source_evidence(notice: dict) -> list[dict] | None:
    raw = notice.get("raw_payload")
    if not isinstance(raw, dict):
        return None
    evidence: list[dict] = []
    snippet = str(raw.get("description_snippet") or "").strip()
    if snippet:
        evidence.append({
            "field": "description_snippet",
            "context": snippet[:900],
        })
    for row in raw.get("screen_evidence_matches") or []:
        if not isinstance(row, dict) or not row.get("context"):
            continue
        evidence.append({
            "field": str(row.get("field") or "description"),
            "term": str(row.get("term") or "")[:120],
            "matched_text": str(row.get("matched_text") or "")[:160],
            "context": str(row.get("context") or "")[:500],
        })
        if len(evidence) >= 6:
            break
    for row in raw.get("triage_thread_evidence") or []:
        if isinstance(row, dict):
            evidence.append({
                "field": "solicitation_thread",
                "source_id": str(row.get("source_id") or "")[:120],
                "title": str(row.get("title") or "")[:240],
                "context": str(row.get("description_snippet") or "")[:500],
                "matched_contexts": (row.get("matched_contexts") or [])[:4],
            })
        if len(evidence) >= 10:
            break
    for row in raw.get("triage_cross_post_evidence") or []:
        if isinstance(row, dict):
            evidence.append({
                "field": "sam_cross_post",
                "source_id": str(row.get("source_id") or "")[:120],
                "solicitation_id": str(
                    row.get("solicitation_id") or "")[:160],
                "title": str(row.get("title") or "")[:240],
                "context": str(row.get("description_snippet") or "")[:500],
                "matched_contexts": (row.get("matched_contexts") or [])[:4],
            })
        if len(evidence) >= 14:
            break
    return evidence or None


def _triage_context(
    client_name: str,
    strategy_summary: str,
    notices: list[dict],
    directive: Optional[str],
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "client_name": client_name,
        "client_pursuit_strategy": strategy_summary,
        "notices": notices,
    }
    if directive and directive.strip():
        context["operator_review_guidance"] = directive.strip()
        context["instruction"] = (
            "operator_review_guidance is a human reviewer's correction to a "
            "prior screen of these notices — weight it heavily and re-screen "
            "accordingly. NEVER manufacture a fit the notice and the client's "
            "strategy do not support; if the guidance asks for something the "
            "evidence cannot bear, keep the honest verdict and say so in the "
            "reason."
        )
    return context


def _serialized_context_chars(context: dict[str, Any]) -> int:
    """Measure the same indented JSON payload DecisionEngine will render."""
    return len(json.dumps(context, indent=2, default=str, sort_keys=True))


def _adaptive_batches(
    notices: list[dict],
    *,
    client_name: str,
    strategy_summary: str,
    directive: Optional[str],
    max_items: int,
    max_serialized_chars: int,
) -> list[list[dict]]:
    """Greedily batch without dropping or truncating any slim notice.

    A single notice may exceed ``max_serialized_chars`` when its own bounded
    evidence is unusually large. It is still emitted as a singleton so the
    sweep remains complete; all multi-item batches satisfy both limits.
    """
    if max_items < 1:
        raise ValueError("triage batch_size must be at least 1")
    if max_serialized_chars < 1:
        raise ValueError("triage batch_char_budget must be at least 1")

    batches: list[list[dict]] = []
    current: list[dict] = []
    for notice in notices:
        candidate = [*current, notice]
        context = _triage_context(
            client_name,
            strategy_summary,
            candidate,
            directive,
        )
        exceeds_items = len(candidate) > max_items
        exceeds_chars = (
            _serialized_context_chars(context) > max_serialized_chars
        )
        if current and (exceeds_items or exceeds_chars):
            batches.append(current)
            current = [notice]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def deterministic_prefilter(
    notices: list[dict],
    taxonomy: Any,
    *,
    engagement_scope: Any = None,
    attachment_receipts: dict | None = None,
) -> tuple[list[dict], dict[str, dict], dict[str, Any]]:
    """Separate evidence-bearing notices from NAICS-only inventory.

    The SAM extract is a complete retrieval census: it intentionally admits a
    notice when either the approved NAICS boundary or a query term matches.
    Model triage is a judgment layer, not a second retrieval engine, so it
    should never spend one deliberation on every broad-code hit.  The
    deterministic relevance engine first proves which records contain CORE or
    ADJACENT capability evidence in their public text (including bounded
    full-description match contexts and public attachment text).  Evidence-
    bearing records reach model judgment even when the code boundary alone
    would keep them out of a client-facing opportunity board.  Everything
    with no capability evidence stays reconciled in the triage map. Missing
    requirement text or failed attachments remain unscreened research gaps,
    without sending every incomplete record to a model.

    This does not narrow source coverage.  It records how the complete
    candidate census was reduced before LLM judgment.
    """
    from tools.relevance.engine import score_record
    from tools.relevance.screening import no_match_state, screening_evidence

    candidates: list[dict] = []
    ruled: dict[str, dict] = {}
    reasons: dict[str, int] = {}
    candidate_reasons: dict[str, int] = {}
    screening_records: dict[str, dict] = {}
    for index, notice in enumerate(notices):
        source_id = str(
            notice.get("source_id") or notice.get("id") or f"idx-{index}"
        )
        verdict = score_record(
            notice,
            taxonomy,
            engagement_scope=engagement_scope,
        )
        evidence = screening_evidence(
            notice, verdict, (attachment_receipts or {}).get(source_id))
        evidence["source_id"] = source_id
        screening_records[source_id] = evidence
        historical = evidence["notice_type"].casefold() in {"award notice", "award", "a"}
        if not historical and not verdict.off_scope and (
                verdict.core_terms or verdict.adjacent_terms):
            candidates.append(notice)
            reason_key = (
                "core-evidence" if verdict.core_terms else "adjacent-evidence"
            )
            candidate_reasons[reason_key] = (
                candidate_reasons.get(reason_key, 0) + 1
            )
            evidence.update(screen_state="SUPPORTED_CAPABILITY_AWAITING_JUDGMENT", stage="model_review")
            continue

        if historical:
            reason = "award notice retained as historical market evidence; no current buying action established"
            reason_key = "historical-award"
            evidence.update(screen_state="HISTORICAL_MARKET_EVIDENCE", stage="historical_research")
        elif verdict.killed:
            reason = "excluded by a client-specific false-positive rule"
            reason_key = "false-positive-rule"
            functional = any(
                rule.category == "functional" and any(
                    (rule.reason or rule.term) in message for message in verdict.killed)
                for rule in taxonomy.exclude)
            evidence["screen_state"] = (
                "EXPLICIT_FUNCTIONAL_MISMATCH" if functional else "VERIFIED_EXCLUSION")
        elif verdict.off_scope:
            reason = "outside the approved engagement scope"
            reason_key = "outside-scope"
            evidence["screen_state"] = "VERIFIED_SCOPE_EXCLUSION"
        elif verdict.excluded_by_code:
            reason = "outside the approved code boundary without strong capability evidence"
            reason_key = "outside-code"
            evidence["screen_state"] = "CODE_BOUNDARY_EXCLUSION"
        elif verdict.adjacent_terms:
            reason = "adjacent signal without core client capability evidence"
            reason_key = "adjacent-only"
            evidence["screen_state"] = "ADJACENT_ONLY"
        else:
            evidence["screen_state"], reason = no_match_state(evidence)
            reason_key = "no-core-evidence"
        ruled[source_id] = {
            "verdict": "unscreened" if evidence["screen_state"] in {
                "TEXT_INCOMPLETE", "ATTACHMENT_UNAVAILABLE"} else "discard",
            "reason": reason,
            "screen": "deterministic-capability-v3",
            "relevance_score": verdict.score,
            "screen_state": evidence["screen_state"],
            "evidence_ref": source_id,
        }
        if ruled[source_id]["verdict"] == "discard":
            reasons[reason_key] = reasons.get(reason_key, 0) + 1

    pre_thread_candidates = len(candidates)
    candidates, superseded, superseded_count = _latest_notice_threads(
        candidates)
    ruled.update(superseded)
    post_solicitation_candidates = len(candidates)
    candidates, cross_posts, cross_post_count = _canonical_cross_posts(
        candidates)
    ruled.update(cross_posts)
    for source_id, disposition in {**superseded, **cross_posts}.items():
        screening_records[source_id].update(
            screen_state="CONSOLIDATED_POSTING", stage="thread_consolidation",
            consolidation=disposition)
    receipt = {
        "mode": "deterministic-capability-v3",
        "taxonomy_client": taxonomy.client_name,
        "taxonomy_version": taxonomy.version,
        "candidate_census": len(notices),
        "model_candidates_before_thread_consolidation": pre_thread_candidates,
        "model_candidates_after_solicitation_consolidation": (
            post_solicitation_candidates
        ),
        "model_candidates": len(candidates),
        "superseded_revisions": superseded_count,
        "cross_post_duplicates": cross_post_count,
        "model_candidate_reasons": dict(sorted(candidate_reasons.items())),
        "deterministic_discards": sum(v["verdict"] == "discard" for v in ruled.values()),
        "research_gaps": sum(v["verdict"] == "unscreened" for v in ruled.values()),
        "deterministic_dispositions": len(ruled),
        "discard_reasons": dict(sorted(reasons.items())),
        "complete": len(candidates) + len(ruled) == len(notices),
        "screening_records": screening_records,
    }
    return candidates, ruled, receipt


def triage_notices(
    client_name: str,
    strategy_summary: str,
    notices: list[dict],
    engine: Optional[DecisionEngine] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_char_budget: int = DEFAULT_BATCH_CHAR_BUDGET,
    directive: Optional[str] = None,
) -> dict[str, dict]:
    """Return {source_id: {verdict, reason}} for every notice given.

    Failure-isolated per batch: a bad batch leaves its notices unruled
    (they surface as 'unscreened' downstream) rather than sinking the run.

    directive is a human reviewer's correction to a prior screen (the
    review-opportunities loop): weighted heavily, but it can never manufacture
    a fit the notice + strategy do not support — no invented opportunities.
    """
    engine = engine or research_engine()
    slim = [
        {
            "id": n.get("source_id") or n.get("id") or f"idx-{i}",
            "title": n.get("title"),
            "agency": n.get("agency"),
            # RawOpportunity serializes SAM's notice type inside raw_payload.
            # Keep the legacy top-level fallbacks, but do not silently omit
            # the type from the model screen when the canonical shape is used.
            "type": (n.get("notice_type") or n.get("type")
                     or ((n.get("raw_payload") or {}).get("type")
                         if isinstance(n.get("raw_payload"), dict) else None)),
            "naics": n.get("naics_code") or n.get("naics"),
            "deadline": n.get("response_deadline") or n.get("deadline"),
            "official_attachment_excerpt": (
                str((n.get("raw_payload") or {}).get(
                    "attachment_relevance_excerpt") or "")[:1200]
                if isinstance(n.get("raw_payload"), dict)
                and (n.get("raw_payload") or {}).get("attachment_evidence")
                else None
            ),
            "official_source_evidence": _bounded_source_evidence(n),
            "attachment_evidence_sha256": (
                (n.get("raw_payload") or {}).get("attachment_evidence_sha256")
                if isinstance(n.get("raw_payload"), dict) else None
            ),
        }
        for i, n in enumerate(notices)
    ]
    out: dict[str, dict] = {}
    batches = _adaptive_batches(
        slim,
        client_name=client_name,
        strategy_summary=strategy_summary,
        directive=directive,
        max_items=batch_size,
        max_serialized_chars=batch_char_budget,
    )
    for chunk in batches:
        context = _triage_context(
            client_name,
            strategy_summary,
            chunk,
            directive,
        )
        try:
            result = engine.deliberate(
                layer=LAYER,
                system_prompt=SYSTEM_PROMPT,
                context=context,
                schema=TriageResult,
            )
        except Exception as e:  # noqa: BLE001 — isolate the batch
            for n in chunk:
                out[n["id"]] = {"verdict": "unscreened", "reason": f"triage batch failed: {e}"}
                if n.get("attachment_evidence_sha256"):
                    out[n["id"]]["attachment_evidence_sha256"] = n[
                        "attachment_evidence_sha256"]
            continue
        ruled = {it.id: {"verdict": it.verdict, "reason": it.reason} for it in result.items}
        for n in chunk:
            out[n["id"]] = ruled.get(
                n["id"], {"verdict": "unscreened", "reason": "no verdict returned"}
            )
            if n.get("attachment_evidence_sha256"):
                out[n["id"]]["attachment_evidence_sha256"] = n[
                    "attachment_evidence_sha256"]
    return out
