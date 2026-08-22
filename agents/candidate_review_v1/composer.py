"""Pure leads-to-document composer for Candidate Review v1 (Chunk 6 core).

This module turns the outputs of the collection engines into ONE validated
``CandidateReviewDocument`` that ``renderer.py`` can render.  It is a pure
library boundary: it invokes no engine, reads no clock, touches no disk, and
makes no network or model call.  The DRIVER (``run_candidate_review.py``) calls
``build_candidate_inventory`` / ``build_calendar`` / the verification lane and
hands their typed results in; the composer only *places* and *binds* them.

Design (from the reviewed build spec):

- ONE bound evidence universe.  ``document.evidence`` is the referenced closure
  over every surviving object, deduped and sorted by ``evidence_id``.  Nothing
  is fabricated: a date or money keeps the exact proving record the verification
  lane attached, because ``all_evidence_ids`` carries date/money evidence into
  the closure.
- STRICT no-invention drop authority.  The composer's own explicit checks
  (evidence-reference resolution, candidate-id subset, id-namespace collisions,
  block-id uniqueness) decide what drops; the document validator is a final
  gate, never a pruning oracle.  Each drop is recorded as a ``ComposerDrop``.
- DETERMINISTIC.  No wall clock, no randomness; every emitted tuple is ordered
  by a stable content key; the baseline hash is a two-pass content digest taken
  AFTER the optional watch-ticker merge.
- ``compose_candidate_review_document`` either returns a document that has
  re-passed the full document validator, or raises ``ComposerError``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Sequence

from pydantic import ValidationError

from agents.candidate_review_v1.candidate_engine import CandidateBuildResult
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    AssetSlot,
    CandidateReviewDocument,
    CoverageRecord,
    EditableTextSlot,
    EditorState,
    EventRecord,
    EvidenceBoundClaim,
    EvidenceRecord,
    ExecutionFramework,
    KpiKind,
    KpiTile,
    MoneyBasis,
    SearchConcepts,
    TickerItem,
    VehicleSignal,
    VehicleWatchRecord,
)
from agents.candidate_review_v1.watch_diff import (
    append_verified_watch_ticker,
    diff_verified_watch,
)

COMPOSER_SCHEMA_VERSION = "candidate_review_v1.composer.v1"

_ZERO_HASH = "0" * 64

_MONEY_KPI_KIND = {
    MoneyBasis.OBLIGATED_TO_DATE: KpiKind.OBLIGATION,
    MoneyBasis.AGGREGATE_HISTORY: KpiKind.RESEARCH_SCALE,
    MoneyBasis.AWARD_CEILING: KpiKind.AWARD_VALUE,
    MoneyBasis.PUBLISHED_EXACT_ESTIMATE: KpiKind.FORECAST_RANGE,
    MoneyBasis.PUBLISHED_RANGE: KpiKind.FORECAST_RANGE,
}

DropTarget = Literal[
    "candidate", "vehicle_signal", "vehicle_watch", "event",
    "claim", "search_concepts", "execution_framework", "coverage",
]


class ComposerError(ValueError):
    """Non-prunable input corruption: binding mismatch, evidence identity

    collision, cross-lane id collision, or a residual validator failure the
    composer's explicit checks did not anticipate.
    """


@dataclass(frozen=True)
class ComposerDrop:
    target: DropTarget
    entity_id: str
    reason: str


@dataclass(frozen=True)
class CandidateReviewComposition:
    document: CandidateReviewDocument
    baseline_document_sha256: str
    dropped: tuple[ComposerDrop, ...]
    reconciled_coverage: tuple[CoverageRecord, ...]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def compose_candidate_review_document(
    *,
    binding: ArtifactBinding,
    document_id: str,
    as_of: datetime,
    generated_at: datetime,
    candidate_build: CandidateBuildResult,
    evidence: Sequence[EvidenceRecord] = (),
    vehicle_signals: Sequence[VehicleSignal] = (),
    vehicle_watch_records: Sequence[VehicleWatchRecord] = (),
    calendar_events: Sequence[EventRecord] = (),
    pattern_claims: Sequence[EvidenceBoundClaim] = (),
    market_signals: Sequence[EvidenceBoundClaim] = (),
    past_awards: Sequence[EvidenceBoundClaim] = (),
    search_concepts: Optional[SearchConcepts] = None,
    execution_framework: Optional[ExecutionFramework] = None,
    coverage: Sequence[CoverageRecord] = (),
    kpi_tiles: Optional[Sequence[KpiTile]] = None,
    ticker_items: Optional[Sequence[TickerItem]] = None,
    text_slots: Sequence[EditableTextSlot] = (),
    asset_slots: Sequence[AssetSlot] = (),
    previous_document: Optional[CandidateReviewDocument] = None,
    strict: bool = False,
) -> CandidateReviewComposition:
    """Assemble one validated CandidateReviewDocument from collection outputs."""

    drops: list[ComposerDrop] = []

    def drop(target: DropTarget, entity_id: str, reason: str) -> None:
        if strict:
            raise ComposerError(f"{target} {entity_id} dropped under strict mode: {reason}")
        drops.append(ComposerDrop(target=target, entity_id=entity_id, reason=reason))

    # 0. Guards.
    _require_aware(as_of, "as_of")
    _require_aware(generated_at, "generated_at")
    if generated_at > as_of:
        raise ComposerError("generated_at cannot postdate as_of")
    if binding.client_name != _client_name(binding):
        raise ComposerError("binding client_name is not the canonical client")

    # 1. Evidence pool: union(candidate_build.evidence, evidence arg) by id.
    pool: dict[str, EvidenceRecord] = {}
    for record in (*candidate_build.evidence, *evidence):
        existing = pool.get(record.evidence_id)
        if existing is not None:
            if existing != record:
                raise ComposerError(
                    f"evidence id {record.evidence_id} bound to two distinct records")
            continue
        if (record.client_id, record.run_id, record.scope_sha256) != (
            binding.client_id, binding.run_id, binding.scope_sha256,
        ):
            raise ComposerError(
                f"evidence {record.evidence_id} is cross-client/run/scope")
        if record.retrieved_at > as_of:
            raise ComposerError(f"evidence {record.evidence_id} retrieved after as_of")
        if record.verified_at is not None and record.verified_at > as_of:
            raise ComposerError(f"evidence {record.evidence_id} verified after as_of")
        pool[record.evidence_id] = record

    def resolves(evidence_ids: Sequence[str]) -> bool:
        return all(eid in pool for eid in evidence_ids)

    # 2. Candidates come from ONE build; they are already document-valid units.
    candidates = tuple(candidate_build.candidates)
    for candidate in candidates:
        if candidate.client_id != binding.client_id:
            raise ComposerError(f"candidate {candidate.candidate_id} belongs to another client")
        if not resolves(candidate.all_evidence_ids):
            raise ComposerError(
                f"candidate {candidate.candidate_id} references evidence absent from the pool")
    visible_ids = tuple(c.candidate_id for c in candidates)
    visible_set = set(visible_ids)

    # Global id namespaces (money_id / date_id) start from the candidates.
    money_ids: set[str] = set()
    date_ids: set[str] = set()
    money_by_id: dict[str, object] = {}
    for candidate in candidates:
        for money in candidate.money:
            money_ids.add(money.money_id)
            money_by_id[money.money_id] = money
        for date_value in candidate.dates:
            date_ids.add(date_value.date_id)

    def claim_money(money_id: str, owner: str) -> None:
        if money_id in money_ids:
            raise ComposerError(f"money id {money_id} collides across {owner}")
        money_ids.add(money_id)

    def claim_date(date_id: str, owner: str) -> None:
        if date_id in date_ids:
            raise ComposerError(f"date id {date_id} collides across {owner}")
        date_ids.add(date_id)

    # 3. Vehicle signals (pass-through with subset + resolution gating).
    kept_signals: list[VehicleSignal] = []
    seen_signal_ids: set[str] = set()
    for signal in vehicle_signals:
        if signal.signal_id in seen_signal_ids:
            drop("vehicle_signal", signal.signal_id, "duplicate signal id")
            continue
        if not resolves(signal.all_evidence_ids):
            drop("vehicle_signal", signal.signal_id, "evidence not in pool")
            continue
        if any(cid not in visible_set for cid in signal.candidate_ids):
            drop("vehicle_signal", signal.signal_id, "references a non-visible candidate")
            continue
        for date_value in signal.dates:
            claim_date(date_value.date_id, f"vehicle signal {signal.signal_id}")
        seen_signal_ids.add(signal.signal_id)
        kept_signals.append(signal)

    # 4. Vehicle watch records (pass-through; ceilings join the money namespace).
    kept_watch: list[VehicleWatchRecord] = []
    seen_watch_keys: set[str] = set()
    for record in vehicle_watch_records:
        key = _watch_key(record)
        if key in seen_watch_keys:
            drop("vehicle_watch", record.watch_record_id, "duplicate vehicle identity")
            continue
        watch_evidence = _watch_evidence_ids(record)
        if watch_evidence is not None and not resolves(watch_evidence):
            drop("vehicle_watch", record.watch_record_id, "evidence not in pool")
            continue
        ceiling = getattr(record, "ceiling_or_value", None)
        if ceiling is not None and getattr(ceiling, "money_id", None):
            claim_money(ceiling.money_id, f"vehicle watch {record.watch_record_id}")
            money_by_id[ceiling.money_id] = ceiling
        for date_value in getattr(record, "dates", ()) or ():
            claim_date(date_value.date_id, f"vehicle watch {record.watch_record_id}")
        seen_watch_keys.add(key)
        kept_watch.append(record)

    # 5. Claims (forecast / signals-claim / accounts), globally unique block ids.
    kept_pattern, kept_market, kept_past = [], [], []
    seen_blocks: set[str] = set()
    for group, kept, label in (
        (pattern_claims, kept_pattern, "pattern"),
        (market_signals, kept_market, "market"),
        (past_awards, kept_past, "past-award"),
    ):
        for block in group:
            if block.client_id != binding.client_id:
                drop("claim", block.block_id, f"{label} claim belongs to another client")
                continue
            if block.block_id in seen_blocks:
                drop("claim", block.block_id, "duplicate block id")
                continue
            if not resolves(block.evidence_ids):
                drop("claim", block.block_id, "evidence not in pool")
                continue
            seen_blocks.add(block.block_id)
            kept.append(block)

    # 6. Concepts + framework (optional singletons).
    if search_concepts is not None:
        if search_concepts.client_id != binding.client_id or not resolves(search_concepts.evidence_ids):
            drop("search_concepts", "search_concepts", "client/evidence mismatch")
            search_concepts = None
    if execution_framework is not None:
        if execution_framework.client_id != binding.client_id or not resolves(execution_framework.evidence_ids):
            drop("execution_framework", "execution_framework", "client/evidence mismatch")
            execution_framework = None

    # 7. Events (driver already ordered + capped them via build_calendar).
    kept_events: list[EventRecord] = []
    seen_event_ids: set[str] = set()
    for event in calendar_events:
        if event.event_id in seen_event_ids:
            drop("event", event.event_id, "duplicate event id")
            continue
        if not resolves(event.all_evidence_ids):
            drop("event", event.event_id, "evidence not in pool")
            continue
        claim_date(event.timing.date_id, f"event {event.event_id}")
        seen_event_ids.add(event.event_id)
        kept_events.append(event)

    # 8. Coverage: keep upstream census verbatim, deduped by identity_key.
    reconciled_coverage = _reconcile_coverage(coverage, binding, as_of, pool, drop)

    # A vehicle-watch record's coverage_query_ids must point at a surviving
    # coverage row; a row dropped in reconciliation cannot dangle.
    live_query_ids = {row.query_id for row in reconciled_coverage if row.query_id}
    surviving_watch = []
    for record in kept_watch:
        needed = tuple(getattr(record, "coverage_query_ids", None) or ())
        if needed and any(qid not in live_query_ids for qid in needed):
            drop("vehicle_watch", record.watch_record_id,
                 "coverage query id has no surviving coverage row")
            continue
        surviving_watch.append(record)
    kept_watch = surviving_watch

    # 9. Referenced-evidence closure over every surviving content object
    #    (plus any caller-supplied kpi/ticker evidence, so their refs resolve).
    closure_ids: list[str] = []
    seen_ids: set[str] = set()

    def add_refs(ids: Sequence[str]) -> None:
        for eid in ids:
            if eid in pool and eid not in seen_ids:
                seen_ids.add(eid)
                closure_ids.append(eid)

    for candidate in candidates:
        add_refs(candidate.all_evidence_ids)
    for signal in kept_signals:
        add_refs(signal.all_evidence_ids)
    for record in kept_watch:
        add_refs(_watch_evidence_ids(record) or ())
    for block in (*kept_pattern, *kept_market, *kept_past):
        add_refs(block.evidence_ids)
    if search_concepts is not None:
        add_refs(search_concepts.evidence_ids)
    if execution_framework is not None:
        add_refs(execution_framework.evidence_ids)
    for event in kept_events:
        add_refs(event.all_evidence_ids)
    for row in reconciled_coverage:
        add_refs(row.accepted_evidence_ids)
    if kpi_tiles is not None:
        for tile in kpi_tiles:
            add_refs(tile.evidence_ids)
    if ticker_items is not None:
        for item in ticker_items:
            add_refs(item.evidence_ids)

    # 10. Derive KPI tiles + ticker.  The evidence-count tile reports the
    #     closure size (== len(document.evidence)), never the raw pool.
    derived_kpis = (tuple(kpi_tiles) if kpi_tiles is not None
                    else _derive_kpi_tiles(candidates, kept_past, money_by_id, len(closure_ids)))
    derived_ticker = (tuple(ticker_items) if ticker_items is not None
                      else _derive_ticker_items(candidates))
    for tile in derived_kpis:
        add_refs(tile.evidence_ids)
    for item in derived_ticker:
        add_refs(item.evidence_ids)

    closure = tuple(pool[eid] for eid in sorted(closure_ids))
    seen_canonical: dict[str, str] = {}
    for record in closure:
        key = record.source_identity.canonical_key
        prior = seen_canonical.setdefault(key, record.evidence_id)
        if prior != record.evidence_id:
            raise ComposerError(
                f"two evidence records share source identity {key}: {prior}, {record.evidence_id}")

    # 11. Editor state (baseline placeholder; stamped after hashing).
    editor_state = EditorState(
        client_id=binding.client_id,
        document_id=document_id,
        baseline_document_sha256=_ZERO_HASH,
        candidate_order=visible_ids,
    )

    # 12-14. Construct -> optional watch-ticker merge -> two-pass baseline hash
    #        -> restamp -> final validate.  The whole assembly runs the document
    #        validator; any residual failure the composer's explicit checks did
    #        not anticipate surfaces as ComposerError, never a raw pydantic
    #        ValidationError (the module's hard contract).
    try:
        provisional = CandidateReviewDocument(
            document_id=document_id,
            baseline_document_sha256=_ZERO_HASH,
            client_name=binding.client_name,
            binding=binding,
            as_of=as_of,
            generated_at=generated_at,
            pattern_claims=tuple(kept_pattern),
            candidates=candidates,
            vehicle_watch_records=tuple(kept_watch),
            vehicle_signals=tuple(kept_signals),
            market_signals=tuple(kept_market),
            past_awards=tuple(kept_past),
            search_concepts=search_concepts,
            calendar_events=tuple(kept_events),
            execution_framework=execution_framework,
            evidence=closure,
            coverage=reconciled_coverage,
            ticker_items=derived_ticker,
            kpi_tiles=derived_kpis,
            text_slots=tuple(text_slots),
            asset_slots=tuple(asset_slots),
            editor_state=editor_state,
        )
        if previous_document is not None:
            provisional = append_verified_watch_ticker(
                provisional, diff_verified_watch(previous_document, provisional))
        digest = _baseline_digest(provisional)
        body = provisional.model_dump(mode="python")
        body["baseline_document_sha256"] = digest
        body["editor_state"] = {**body["editor_state"], "baseline_document_sha256": digest}
        document = CandidateReviewDocument.model_validate(body)
    except ComposerError:
        raise
    except (ValidationError, ValueError) as exc:
        raise ComposerError(f"assembled document failed validation: {exc}") from exc

    return CandidateReviewComposition(
        document=document,
        baseline_document_sha256=digest,
        dropped=tuple(drops),
        reconciled_coverage=reconciled_coverage,
    )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ComposerError(f"{label} must be timezone-aware")


def _client_name(binding: ArtifactBinding) -> str:
    # ArtifactBinding already validates client_name -> client_id; re-read the
    # bound value so a mismatch is caught as a composer input error, not later.
    return binding.client_name


def _watch_key(record: VehicleWatchRecord) -> str:
    name = (getattr(record, "canonical_name", None)
            or getattr(record, "vehicle_program_id", None)
            or getattr(record, "watch_record_id", ""))
    agency = getattr(record, "managing_agency", "") or ""
    return f"{agency.casefold()}\x00{str(name).casefold()}"


def _watch_evidence_ids(record: VehicleWatchRecord) -> Optional[tuple[str, ...]]:
    for attr in ("all_evidence_ids", "evidence_ids"):
        value = getattr(record, attr, None)
        if value:
            return tuple(value)
    return None


def _reconcile_coverage(coverage, binding, as_of, pool, drop) -> tuple[CoverageRecord, ...]:
    kept: list[CoverageRecord] = []
    seen_keys: set[str] = set()
    seen_query_ids: dict[str, CoverageRecord] = {}
    for row in coverage:
        if (row.client_id, row.run_id, row.scope_sha256) != (
            binding.client_id, binding.run_id, binding.scope_sha256,
        ):
            drop("coverage", row.source, "coverage belongs to another client/run/scope")
            continue
        if not (row.window_start <= as_of.date() <= row.window_end):
            drop("coverage", row.source, "coverage window does not contain as_of")
            continue
        if row.attempted_at is not None and row.attempted_at > as_of:
            drop("coverage", row.source, "coverage attempt postdates as_of")
            continue
        if any(eid not in pool for eid in row.accepted_evidence_ids):
            drop("coverage", row.source, "accepted evidence not in pool")
            continue
        key = row.identity_key
        if key in seen_keys:
            drop("coverage", row.source, "duplicate coverage identity key")
            continue
        # The document validator additionally requires every non-null query_id
        # to map to exactly one row (contracts.py: 'coverage query IDs must be
        # unique'); mirror it here so a collision is a clean drop, not a raw
        # ValidationError.
        if row.query_id is not None:
            prior = seen_query_ids.get(row.query_id)
            if prior is not None and prior != row:
                drop("coverage", row.source, "duplicate coverage query id")
                continue
            seen_query_ids[row.query_id] = row
        seen_keys.add(key)
        kept.append(row)
    return tuple(kept)


def _derive_kpi_tiles(candidates, past_awards, money_by_id, evidence_count) -> tuple[KpiTile, ...]:
    tiles: list[KpiTile] = []
    tiles.append(KpiTile(
        kpi_id="kpi-candidate-count", kind=KpiKind.CANDIDATE_COUNT,
        eyebrow="Candidate opportunities", value=str(len(candidates)),
        title="Cleared for analyst review", note="Count of candidate opportunities in this assessment.",
        computed=True))
    # One money tile per compatible surviving money, distinct kind, filling to cap 4.
    for money_id in sorted(money_by_id):
        if len(tiles) >= 3:
            break
        money = money_by_id[money_id]
        basis = getattr(money, "basis", None)
        kind = _MONEY_KPI_KIND.get(basis)
        if kind is None:
            continue
        evidence_ids = tuple(getattr(money, "evidence_ids", ()) or ())
        if not evidence_ids:
            continue
        tiles.append(KpiTile(
            kpi_id=f"kpi-money-{money_id}", kind=kind,
            eyebrow="Federal spending signal", value=money.display_value,
            title=kind.value.replace("_", " ").title(),
            note="Typed figure derived from its source record.",
            evidence_ids=evidence_ids, money_id=money_id))
    if len(tiles) < 4:
        tiles.append(KpiTile(
            kpi_id="kpi-evidence", kind=KpiKind.SOURCE_COVERAGE,
            eyebrow="Evidence base", value=str(evidence_count),
            title="Source records reviewed",
            note="Distinct official records cited in this assessment."))
    if len(tiles) < 4:
        tiles.append(KpiTile(
            kpi_id="kpi-past-awards", kind=KpiKind.SOURCE_COVERAGE,
            eyebrow="Competitive base", value=str(len(past_awards)),
            title="Past-award patterns",
            note="Competitive award patterns surfaced for review."))
    return tuple(tiles[:4])


def _derive_ticker_items(candidates) -> tuple[TickerItem, ...]:
    items: list[TickerItem] = []
    for candidate in candidates[:24]:
        anchor = candidate.member_evidence_ids[0] if candidate.member_evidence_ids else None
        if anchor is None:
            continue
        items.append(TickerItem(
            ticker_id=f"tk-{candidate.candidate_id}",
            label=candidate.title,
            source_evidence_id=anchor,
            evidence_ids=(anchor,),
            candidate_ids=(candidate.candidate_id,)))
    return tuple(items)


def _baseline_digest(document: CandidateReviewDocument) -> str:
    """Two-pass content digest with both baseline fields normalized to zero."""

    body = document.model_dump(mode="json")
    body["baseline_document_sha256"] = _ZERO_HASH
    body["editor_state"] = {**body["editor_state"], "baseline_document_sha256": _ZERO_HASH}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


__all__ = (
    "COMPOSER_SCHEMA_VERSION",
    "CandidateReviewComposition",
    "ComposerDrop",
    "ComposerError",
    "compose_candidate_review_document",
)
