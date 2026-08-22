"""Pure editor-to-report projection for Candidate Review v1.

The canonical document is immutable and retains soft-deleted candidates until
clean export.  This module applies editor state without changing analytical
content: it controls visible order, numbering, ticker membership, the visible
evidence dock, and the recovery state carried by a downloadable draft.

Every state-changing function is deterministic.  In particular, soft delete
requires its timestamp and undo token from the caller instead of reading the
clock or generating randomness inside the projection layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from agents.candidate_review_v1.contracts import (
    CandidateReviewDocument,
    CandidateTombstone,
    CandidateUnit,
    CoverageRecord,
    EditorState,
    EvidenceRecord,
    EventRecord,
    KpiKind,
    KpiTile,
    TickerItem,
    UndoState,
    VehicleSignal,
    VehicleWatchRecord,
)
from agents.candidate_review_v1.calendar_engine import (
    CalendarItem,
    project_document_calendar,
)


@dataclass(frozen=True)
class NumberedCandidate:
    """One visible candidate with presentation-only numbering."""

    ordinal: int
    number: str
    candidate: CandidateUnit

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id


@dataclass(frozen=True)
class CandidateReviewProjection:
    """The complete visible state consumed by a renderer or HTML export."""

    candidate_count: int
    candidates: tuple[NumberedCandidate, ...]
    vehicle_watch_records: tuple[VehicleWatchRecord, ...]
    vehicle_signals: tuple[VehicleSignal, ...]
    ticker_items: tuple[TickerItem, ...]
    kpi_tiles: tuple[KpiTile, ...]
    calendar_items: tuple[CalendarItem, ...]
    calendar_events: tuple[EventRecord, ...]
    coverage: tuple[CoverageRecord, ...]
    source_evidence: tuple[EvidenceRecord, ...]
    evidence: tuple[EvidenceRecord, ...]
    editor_state: EditorState
    clean_export: bool = False

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.candidates)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence)

    @property
    def source_evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.source_evidence)


def reorder_candidates(
    document: CandidateReviewDocument,
    candidate_ids: Iterable[str],
) -> CandidateReviewDocument:
    """Return ``document`` with a deterministic, validated candidate order.

    A renderer normally submits the visible candidate IDs.  Tombstoned IDs are
    left in their existing hidden slots so they remain recoverable.  A caller
    may also submit a complete permutation when restoring a saved draft.
    """

    requested = tuple(candidate_ids)
    _require_unique(requested, "candidate reorder")
    state = document.editor_state
    current = state.candidate_order
    deleted = _deleted_candidate_ids(state)
    visible = tuple(candidate_id for candidate_id in current
                    if candidate_id not in deleted)

    if len(requested) == len(current) and set(requested) == set(current):
        replacement = requested
    elif len(requested) == len(visible) and set(requested) == set(visible):
        requested_iter = iter(requested)
        replacement = tuple(
            candidate_id if candidate_id in deleted else next(requested_iter)
            for candidate_id in current
        )
    else:
        raise ValueError(
            "candidate reorder must contain every visible candidate exactly once"
        )

    if replacement == current:
        return document
    updated_state = state.model_copy(update={
        "candidate_order": replacement,
        "revision": state.revision + 1,
    })
    return document.model_copy(update={"editor_state": updated_state})


def soft_delete_candidate(
    document: CandidateReviewDocument,
    candidate_id: str,
    *,
    deleted_at: datetime,
    undo_token: str,
) -> CandidateReviewDocument:
    """Soft-delete one visible candidate while retaining a recovery record."""

    state = document.editor_state
    if candidate_id not in state.candidate_order:
        raise ValueError(f"unknown candidate: {candidate_id}")
    if candidate_id in _deleted_candidate_ids(state):
        raise ValueError(f"candidate is already deleted: {candidate_id}")
    if not undo_token.strip():
        raise ValueError("undo token cannot be blank")
    if undo_token in {item.undo_token for item in state.tombstones}:
        raise ValueError("undo token must be unique")

    tombstone = CandidateTombstone(
        candidate_id=candidate_id,
        deleted_at=deleted_at,
        undo_token=undo_token,
        prior_index=state.candidate_order.index(candidate_id),
    )
    undo = UndoState(
        token=undo_token,
        candidate_id=candidate_id,
        prior_order=state.candidate_order,
        created_at=deleted_at,
    )
    updated_state = state.model_copy(update={
        "revision": state.revision + 1,
        "tombstones": (*state.tombstones, tombstone),
        "undo": undo,
    })
    return document.model_copy(update={"editor_state": updated_state})


def undo_soft_delete(
    document: CandidateReviewDocument,
    *,
    undo_token: Optional[str] = None,
) -> CandidateReviewDocument:
    """Undo the most recent recoverable soft delete.

    Supplying the token is recommended for UI calls; it prevents a stale
    browser action from replaying a newer deletion.
    """

    state = document.editor_state
    undo = state.undo
    if undo is None:
        raise ValueError("there is no soft deletion to undo")
    if undo_token is not None and undo_token != undo.token:
        raise ValueError("undo token does not match the current deletion")

    matching = tuple(
        item for item in state.tombstones
        if item.candidate_id == undo.candidate_id
        and item.undo_token == undo.token
    )
    if len(matching) != 1:
        raise ValueError("undo state has no unique matching tombstone")

    updated_state = state.model_copy(update={
        "revision": state.revision + 1,
        "candidate_order": undo.prior_order,
        "tombstones": tuple(
            item for item in state.tombstones if item not in matching
        ),
        "undo": None,
    })
    return document.model_copy(update={"editor_state": updated_state})


def project_candidate_review(
    document: CandidateReviewDocument,
    *,
    clean_export: bool = False,
) -> CandidateReviewProjection:
    """Project immutable document data through the current editor state.

    Clean export strips tombstones, undo history, and hidden candidate IDs from
    the returned projection.  The canonical input document remains unchanged.
    """

    # ``model_copy(update=...)`` is intentionally available to the editor, but
    # Pydantic does not validate those updates.  Re-enter the strict document
    # boundary before creating any publishable projection so an unsourced
    # legacy vehicle/order relationship cannot ride through a copied model.
    document = CandidateReviewDocument.model_validate(
        document.model_dump(mode="python")
    )

    state = document.editor_state
    deleted = _deleted_candidate_ids(state)
    candidates_by_id = {
        candidate.candidate_id: candidate for candidate in document.candidates
    }
    visible_ids = tuple(
        candidate_id for candidate_id in state.candidate_order
        if candidate_id not in deleted
    )
    numbered = tuple(
        NumberedCandidate(
            ordinal=index,
            number=f"{index:02d}",
            candidate=candidates_by_id[candidate_id],
        )
        for index, candidate_id in enumerate(visible_ids, start=1)
    )
    ticker_items = _visible_ticker_items(document, visible_ids)
    vehicle_signals = _visible_vehicle_signals(document, visible_ids)
    kpi_tiles = _visible_kpi_tiles(document, len(numbered))
    calendar_items = project_document_calendar(
        document,
        candidate_ids=visible_ids,
    )
    visible_event_ids = frozenset(
        item.event_id for item in calendar_items if item.event_id is not None
    )
    evidence = _visible_evidence(
        document,
        visible_ids,
        visible_event_ids,
    )
    source_evidence = _source_evidence(
        document,
        evidence,
        ticker_items,
        calendar_items,
    )

    projected_state = state
    if clean_export:
        projected_state = state.model_copy(update={
            "candidate_order": visible_ids,
            "tombstones": (),
            "undo": None,
        })

    return CandidateReviewProjection(
        candidate_count=len(numbered),
        candidates=numbered,
        vehicle_watch_records=document.vehicle_watch_records,
        vehicle_signals=vehicle_signals,
        ticker_items=ticker_items,
        kpi_tiles=kpi_tiles,
        calendar_items=calendar_items,
        calendar_events=tuple(
            event for event in document.calendar_events
            if event.active and event.event_id in visible_event_ids
        ),
        coverage=document.coverage,
        source_evidence=source_evidence,
        evidence=evidence,
        editor_state=projected_state,
        clean_export=clean_export,
    )


def project_clean_export(
    document: CandidateReviewDocument,
) -> CandidateReviewProjection:
    """Convenience wrapper for a recovery-record-free HTML projection."""

    return project_candidate_review(document, clean_export=True)


def _deleted_candidate_ids(state: EditorState) -> frozenset[str]:
    return frozenset(item.candidate_id for item in state.tombstones)


def _visible_ticker_items(
    document: CandidateReviewDocument,
    visible_ids: tuple[str, ...],
) -> tuple[TickerItem, ...]:
    visible = set(visible_ids)
    candidate_rank = {
        candidate_id: index for index, candidate_id in enumerate(visible_ids)
    }
    linked: list[tuple[int, int, TickerItem]] = []
    unlinked: list[tuple[int, TickerItem]] = []

    for source_index, item in enumerate(document.ticker_items):
        active_candidate_ids = tuple(
            candidate_id for candidate_id in item.candidate_ids
            if candidate_id in visible
        )
        if item.candidate_ids and not active_candidate_ids:
            continue
        projected_item = item
        if active_candidate_ids != item.candidate_ids:
            projected_item = TickerItem.model_validate({
                **item.model_dump(mode="python"),
                "candidate_ids": active_candidate_ids,
            })
        if active_candidate_ids:
            linked.append((
                min(candidate_rank[value] for value in active_candidate_ids),
                source_index,
                projected_item,
            ))
        else:
            unlinked.append((source_index, projected_item))

    linked.sort(key=lambda row: (row[0], row[1]))
    return tuple(
        [row[2] for row in linked]
        + [row[1] for row in unlinked]
    )


def _visible_kpi_tiles(
    document: CandidateReviewDocument,
    candidate_count: int,
) -> tuple[KpiTile, ...]:
    projected: list[KpiTile] = []
    for item in document.kpi_tiles:
        if item.kind != KpiKind.CANDIDATE_COUNT:
            projected.append(item)
            continue
        projected.append(KpiTile.model_validate({
            **item.model_dump(mode="python"),
            "value": str(candidate_count),
        }))
    return tuple(projected)


def _visible_vehicle_signals(
    document: CandidateReviewDocument,
    visible_ids: tuple[str, ...],
) -> tuple[VehicleSignal, ...]:
    """Keep independently evidenced signals while pruning hidden card links."""

    visible = set(visible_ids)
    projected: list[VehicleSignal] = []
    for signal in document.vehicle_signals:
        candidate_ids = tuple(
            candidate_id for candidate_id in signal.candidate_ids
            if candidate_id in visible
        )
        projected.append(
            signal if candidate_ids == signal.candidate_ids
            else signal.model_copy(update={"candidate_ids": candidate_ids})
        )
    return tuple(projected)


def _visible_evidence(
    document: CandidateReviewDocument,
    visible_ids: tuple[str, ...],
    visible_event_ids: frozenset[str],
) -> tuple[EvidenceRecord, ...]:
    candidates_by_id = {
        candidate.candidate_id: candidate for candidate in document.candidates
    }
    ordered_refs: list[str] = []

    def add(values: Iterable[str]) -> None:
        for evidence_id in values:
            if evidence_id not in ordered_refs:
                ordered_refs.append(evidence_id)

    # Follow visible report order.  The Evidence dock and ticker are excluded
    # as derivation inputs so neither can keep its own orphan rows alive.
    for block in document.pattern_claims:
        add(block.evidence_ids)
    for candidate_id in visible_ids:
        add(candidates_by_id[candidate_id].all_evidence_ids)
    for record in document.vehicle_watch_records:
        add(record.all_evidence_ids)
    for signal in document.vehicle_signals:
        add(signal.all_evidence_ids)
    for block in document.market_signals:
        add(block.evidence_ids)
    for block in document.past_awards:
        add(block.evidence_ids)
    if document.search_concepts:
        add(document.search_concepts.evidence_ids)
    for event in document.calendar_events:
        if event.event_id in visible_event_ids:
            add(event.all_evidence_ids)
    if document.execution_framework:
        add(document.execution_framework.evidence_ids)
    for item in document.kpi_tiles:
        add(item.evidence_ids)

    evidence_by_id = {
        item.evidence_id: item for item in document.evidence
    }
    return tuple(evidence_by_id[evidence_id] for evidence_id in ordered_refs)


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate candidate IDs")


def _source_evidence(
    document: CandidateReviewDocument,
    visible_evidence: tuple[EvidenceRecord, ...],
    ticker_items: tuple[TickerItem, ...],
    calendar_items: tuple[CalendarItem, ...],
) -> tuple[EvidenceRecord, ...]:
    """Complete link-resolution manifest for every published projection item.

    Ticker-only source records do not become Evidence-dock rows, but the clean
    standalone projection still retains their immutable identity and URL.
    """

    required = {
        item.evidence_id for item in visible_evidence
    }
    required.update(
        evidence_id
        for item in ticker_items
        for evidence_id in item.evidence_ids
    )
    required.update(
        evidence_id
        for row in document.coverage
        for evidence_id in row.accepted_evidence_ids
    )
    required.update(
        evidence_id
        for item in calendar_items
        for evidence_id in item.evidence_ids
    )
    return tuple(
        item for item in document.evidence
        if item.evidence_id in required
    )


__all__ = (
    "CandidateReviewProjection",
    "NumberedCandidate",
    "project_candidate_review",
    "project_clean_export",
    "reorder_candidates",
    "soft_delete_candidate",
    "undo_soft_delete",
)
