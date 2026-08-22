"""Verified event and vehicle snapshot changes for the preserved tickers.

Raw discovery leads never enter this lane.  It compares fully validated
``CandidateReviewDocument`` snapshots, emits evidence-bound material changes,
and can append those changes to the existing report ticker without deleting
historical ticker rows.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    ArtifactBinding,
    CandidateReviewDocument,
    EventRecord,
    EvidenceRecord,
    TickerItem,
    VehicleSignal,
    VehicleSignalKind,
    assert_candidate_review_language,
)


WATCH_DIFF_SCHEMA_VERSION = "candidate_review_v1.watch_diff.v1"


class _WatchDiffContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class WatchChangeKind(str, Enum):
    NEW_EVENT_EDITION = "new_event_edition"
    EVENT_DATE_CHANGED = "event_date_changed"
    EVENT_LIFECYCLE_CHANGED = "event_lifecycle_changed"
    NEW_VEHICLE_SIGNAL = "new_vehicle_signal"
    NEW_ORDER_ACTIVITY = "new_order_activity"
    VEHICLE_DATE_CHANGED = "vehicle_date_changed"
    VEHICLE_STATUS_CHANGED = "vehicle_status_changed"
    VEHICLE_ACCESS_CHANGED = "vehicle_access_changed"


class VerifiedWatchChange(_WatchDiffContract):
    change_id: str = Field(min_length=1)
    kind: WatchChangeKind
    entity_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_evidence_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    candidate_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _change_is_traceable_and_non_dispositive(self) -> "VerifiedWatchChange":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("watch-change evidence IDs must be unique")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("watch-change candidate IDs must be unique")
        if self.source_evidence_id not in self.evidence_ids:
            raise ValueError("watch-change source must be retained as evidence")
        assert_candidate_review_language(self.label)
        return self

    def to_ticker_item(self) -> TickerItem:
        return TickerItem(
            ticker_id=f"ticker-{self.change_id}",
            label=self.label,
            source_evidence_id=self.source_evidence_id,
            evidence_ids=self.evidence_ids,
            candidate_ids=self.candidate_ids,
            historical_context=False,
        )


class VerifiedWatchDiff(_WatchDiffContract):
    schema_version: str = WATCH_DIFF_SCHEMA_VERSION
    binding: ArtifactBinding
    as_of: datetime
    previous_run_id: Optional[str] = None
    baseline_established: bool = False
    removals_evaluated: Literal[False] = False
    changes: tuple[VerifiedWatchChange, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    ticker_items: tuple[TickerItem, ...] = Field(
        default_factory=tuple,
        max_length=CONTENT_BUDGETS["ticker_items"],
    )

    @model_validator(mode="after")
    def _diff_is_derived(self) -> "VerifiedWatchDiff":
        if self.schema_version != WATCH_DIFF_SCHEMA_VERSION:
            raise ValueError("watch diff schema version is unsupported")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("watch diff as_of must be timezone-aware")
        change_ids = tuple(row.change_id for row in self.changes)
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("watch change IDs must be unique")
        if self.baseline_established != (self.previous_run_id is None):
            raise ValueError("watch diff baseline state is inconsistent")
        if self.baseline_established and self.changes:
            raise ValueError("an initial baseline cannot claim material changes")
        expected = tuple(
            change.to_ticker_item()
            for change in self.changes[:CONTENT_BUDGETS["ticker_items"]]
        )
        if self.ticker_items != expected:
            raise ValueError("watch ticker items are not derived from changes")
        return self


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()[:20]


def _semantic_date_payload(value: object) -> dict[str, object]:
    """Return timing semantics without evidence or presentation metadata."""

    return {
        "kind": value.kind.value,
        "status": value.status.value,
        "precision": value.precision.value,
        "sort_date": value.sort_date.isoformat(),
        "sort_datetime": (
            value.sort_datetime.isoformat()
            if value.sort_datetime is not None else None
        ),
        "start": value.start.isoformat() if value.start is not None else None,
        "end": value.end.isoformat() if value.end is not None else None,
    }


def _date_signature(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, tuple):
        rows = (_semantic_date_payload(row) for row in value)
        payload = sorted(
            rows,
            key=lambda row: json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    else:
        payload = _semantic_date_payload(value)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _official_source(
    entity_evidence_ids: tuple[str, ...],
    evidence_by_id: dict[str, EvidenceRecord],
    *,
    preferred_ids: tuple[str, ...] = (),
) -> tuple[str, tuple[str, ...]]:
    ordered = tuple(dict.fromkeys((*preferred_ids, *entity_evidence_ids)))
    retained = tuple(
        evidence_id for evidence_id in ordered
        if evidence_id in evidence_by_id
    )
    source = next(
        (
            evidence_id for evidence_id in retained
            if evidence_by_id[evidence_id].official_source
            and evidence_by_id[evidence_id].primary_source
        ),
        None,
    )
    if source is None:
        raise ValueError("verified watch change lacks official current evidence")
    return source, retained


def _change(
    *,
    kind: WatchChangeKind,
    entity_id: str,
    label: str,
    entity_evidence_ids: tuple[str, ...],
    evidence_by_id: dict[str, EvidenceRecord],
    preferred_ids: tuple[str, ...] = (),
    candidate_ids: tuple[str, ...] = (),
) -> VerifiedWatchChange:
    source, retained = _official_source(
        entity_evidence_ids,
        evidence_by_id,
        preferred_ids=preferred_ids,
    )
    return VerifiedWatchChange(
        change_id="watch-change-" + _digest({
            "kind": kind.value,
            "entity_id": entity_id,
            "label": label,
            "source_evidence_id": source,
            "evidence_ids": retained,
            "candidate_ids": candidate_ids,
        }),
        kind=kind,
        entity_id=entity_id,
        label=label,
        source_evidence_id=source,
        evidence_ids=retained,
        candidate_ids=candidate_ids,
    )


def _event_changes(
    previous: dict[str, EventRecord],
    current: tuple[EventRecord, ...],
    evidence_by_id: dict[str, EvidenceRecord],
) -> tuple[VerifiedWatchChange, ...]:
    changes: list[VerifiedWatchChange] = []
    for event in sorted(current, key=lambda row: row.event_id):
        prior = previous.get(event.event_id)
        if prior is None:
            changes.append(_change(
                kind=WatchChangeKind.NEW_EVENT_EDITION,
                entity_id=event.event_id,
                label=f"New verified event edition: {event.title}",
                entity_evidence_ids=event.all_evidence_ids,
                evidence_by_id=evidence_by_id,
                preferred_ids=(
                    event.timing.evidence_ids if event.timing is not None else ()
                ),
            ))
            continue
        if _date_signature(prior.timing) != _date_signature(event.timing):
            changes.append(_change(
                kind=WatchChangeKind.EVENT_DATE_CHANGED,
                entity_id=event.event_id,
                label=f"Verified event timing changed: {event.title}",
                entity_evidence_ids=event.all_evidence_ids,
                evidence_by_id=evidence_by_id,
                preferred_ids=(
                    event.timing.evidence_ids if event.timing is not None else ()
                ),
            ))
        if (
            prior.lifecycle_status != event.lifecycle_status
            or prior.active != event.active
        ):
            changes.append(_change(
                kind=WatchChangeKind.EVENT_LIFECYCLE_CHANGED,
                entity_id=event.event_id,
                label=f"Verified event status changed: {event.title}",
                entity_evidence_ids=event.all_evidence_ids,
                evidence_by_id=evidence_by_id,
            ))
    return tuple(changes)


def _vehicle_access(signal: VehicleSignal) -> Optional[str]:
    return (
        signal.relationship.access_posture.value
        if signal.relationship is not None else None
    )


def _vehicle_changes(
    previous: dict[str, VehicleSignal],
    current: tuple[VehicleSignal, ...],
    evidence_by_id: dict[str, EvidenceRecord],
) -> tuple[VerifiedWatchChange, ...]:
    changes: list[VerifiedWatchChange] = []
    for signal in sorted(current, key=lambda row: row.signal_id):
        prior = previous.get(signal.signal_id)
        if prior is None:
            kind = (
                WatchChangeKind.NEW_ORDER_ACTIVITY
                if signal.kind is VehicleSignalKind.TASK_ORDER_ACTIVITY
                else WatchChangeKind.NEW_VEHICLE_SIGNAL
            )
            label = (
                f"New verified order activity: {signal.title}"
                if kind is WatchChangeKind.NEW_ORDER_ACTIVITY
                else f"New verified vehicle signal: {signal.title}"
            )
            changes.append(_change(
                kind=kind,
                entity_id=signal.signal_id,
                label=label,
                entity_evidence_ids=signal.all_evidence_ids,
                evidence_by_id=evidence_by_id,
                candidate_ids=signal.candidate_ids,
            ))
            continue
        if _date_signature(prior.dates) != _date_signature(signal.dates):
            preferred = tuple(
                evidence_id
                for timing in signal.dates
                for evidence_id in timing.evidence_ids
            )
            changes.append(_change(
                kind=WatchChangeKind.VEHICLE_DATE_CHANGED,
                entity_id=signal.signal_id,
                label=f"Verified vehicle timing changed: {signal.title}",
                entity_evidence_ids=signal.all_evidence_ids,
                evidence_by_id=evidence_by_id,
                preferred_ids=preferred,
                candidate_ids=signal.candidate_ids,
            ))
        if prior.status != signal.status:
            changes.append(_change(
                kind=WatchChangeKind.VEHICLE_STATUS_CHANGED,
                entity_id=signal.signal_id,
                label=f"Verified vehicle status changed: {signal.title}",
                entity_evidence_ids=signal.all_evidence_ids,
                evidence_by_id=evidence_by_id,
                candidate_ids=signal.candidate_ids,
            ))
        if _vehicle_access(prior) != _vehicle_access(signal):
            changes.append(_change(
                kind=WatchChangeKind.VEHICLE_ACCESS_CHANGED,
                entity_id=signal.signal_id,
                label=f"Verified vehicle access evidence changed: {signal.title}",
                entity_evidence_ids=signal.all_evidence_ids,
                evidence_by_id=evidence_by_id,
                candidate_ids=signal.candidate_ids,
            ))
    return tuple(changes)


def diff_verified_watch(
    previous: Optional[CandidateReviewDocument],
    current: CandidateReviewDocument,
) -> VerifiedWatchDiff:
    """Diff additions/changes only; absence never proves removal.

    A missing row can mean a provider outage, incomplete query, or scope
    change.  No removal change kind exists, so current positive evidence is
    required for every emitted change.
    """

    # Watch changes feed the preserved ticker, which is publication.  Re-run
    # the strict evidence boundary so a non-validated model copy cannot emit a
    # legacy order change without an exact parent-IDV assertion.
    current = CandidateReviewDocument.model_validate(
        current.model_dump(mode="python")
    )
    if previous is not None:
        previous = CandidateReviewDocument.model_validate(
            previous.model_dump(mode="python")
        )

    if previous is None:
        return VerifiedWatchDiff(
            binding=current.binding,
            as_of=current.as_of,
            previous_run_id=None,
            baseline_established=True,
            changes=(),
            ticker_items=(),
        )
    if (
        previous.binding.client_id != current.binding.client_id
        or previous.binding.client_name != current.binding.client_name
        or previous.binding.scope_designator != current.binding.scope_designator
        or previous.binding.scope_sha256 != current.binding.scope_sha256
    ):
        raise ValueError("watch snapshots cross client or approved scope")
    if previous.as_of > current.as_of:
        raise ValueError("previous watch snapshot postdates current snapshot")
    evidence_by_id = {
        row.evidence_id: row for row in current.evidence
    }
    changes = tuple(sorted(
        (
            *_event_changes(
                {row.event_id: row for row in previous.calendar_events},
                current.calendar_events,
                evidence_by_id,
            ),
            *_vehicle_changes(
                {row.signal_id: row for row in previous.vehicle_signals},
                current.vehicle_signals,
                evidence_by_id,
            ),
        ),
        key=lambda row: (row.kind.value, row.entity_id, row.change_id),
    ))
    ticker_items = tuple(
        change.to_ticker_item()
        for change in changes[:CONTENT_BUDGETS["ticker_items"]]
    )
    return VerifiedWatchDiff(
        binding=current.binding,
        as_of=current.as_of,
        previous_run_id=previous.binding.run_id,
        baseline_established=False,
        changes=changes,
        ticker_items=ticker_items,
    )


def append_verified_watch_ticker(
    document: CandidateReviewDocument,
    watch_diff: VerifiedWatchDiff,
) -> CandidateReviewDocument:
    """Publish verified changes, then retain prior rows within the bound.

    New material changes must not disappear merely because the preserved
    historical ticker already fills its presentation budget.
    """

    if watch_diff.binding != document.binding or watch_diff.as_of != document.as_of:
        raise ValueError("watch diff cannot hydrate another document generation")
    merged: list[TickerItem] = []
    by_id: dict[str, TickerItem] = {}
    for item in (*watch_diff.ticker_items, *document.ticker_items):
        prior = by_id.get(item.ticker_id)
        if prior is not None:
            if prior != item:
                raise ValueError("watch ticker conflicts with an existing ticker ID")
            continue
        by_id[item.ticker_id] = item
        merged.append(item)
    bounded = tuple(merged[:CONTENT_BUDGETS["ticker_items"]])
    return CandidateReviewDocument.model_validate({
        **document.model_dump(mode="python"),
        "ticker_items": bounded,
    })


__all__ = (
    "WATCH_DIFF_SCHEMA_VERSION",
    "VerifiedWatchChange",
    "VerifiedWatchDiff",
    "WatchChangeKind",
    "append_verified_watch_ticker",
    "diff_verified_watch",
)
