"""Evidence-bound federal opportunity calendar for Candidate Review v1.

The calendar is a deterministic projection over two already-normalized inputs:

* typed procurement dates carried by candidate units; and
* event observations that have been verified against official source records.

Discovery results never enter this module as evidence.  They first become
official ``EvidenceRecord`` observations and explicit ``EventSeed`` rows.  The
engine then owns source compatibility, event identity, horizon policy,
coverage closure, chronological order, and the bounded report projection.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    ArtifactBinding,
    CandidateReviewDocument,
    CandidateUnit,
    CoverageRecord,
    CoverageState,
    DateKind,
    DatePrecision,
    DateValue,
    EventKind,
    EventLifecycleStatus,
    EventRecord,
    EvidenceAssertion,
    EvidenceKind,
    EvidenceRecord,
    VehicleSignal,
    VehicleWatchRecord,
    assert_candidate_review_language,
    evidence_supports_date_value,
    validate_event_watch_freshness,
    validate_vehicle_signal_evidence,
)
from agents.candidate_review_v1.event_research import (
    EventQueryAttempt,
    EventQueryManifest,
    EventQuerySpec,
    EventSourceClass,
    normalize_trusted_organizer_domain,
)


MAX_VISIBLE_RESEARCHED_EVENTS = 12


_PROCUREMENT_DATE_KINDS = frozenset({
    DateKind.RESPONSE_DEADLINE,
    DateKind.QA_DEADLINE,
    DateKind.SITE_VISIT,
    DateKind.INDUSTRY_DAY,
    DateKind.FORECAST_SOLICITATION,
    DateKind.FORECAST_AWARD,
    DateKind.PROGRAM_DEADLINE,
    DateKind.CONFIRMED_RECOMPETE,
    DateKind.AWARD_END_RESEARCH_CLOCK,
    DateKind.BUDGET_MILESTONE,
    DateKind.MONITOR_DATE,
})

_TIMING_SOURCE_KINDS = {
    DateKind.RESPONSE_DEADLINE: frozenset({EvidenceKind.NOTICE}),
    DateKind.QA_DEADLINE: frozenset({EvidenceKind.NOTICE}),
    DateKind.SITE_VISIT: frozenset({EvidenceKind.NOTICE}),
    DateKind.INDUSTRY_DAY: frozenset({
        EvidenceKind.NOTICE,
        EvidenceKind.OFFICIAL_EVENT,
        EvidenceKind.ORGANIZER_EVENT,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.FORECAST_SOLICITATION: frozenset({
        EvidenceKind.AGENCY_FORECAST}),
    DateKind.FORECAST_AWARD: frozenset({EvidenceKind.AGENCY_FORECAST}),
    DateKind.PROGRAM_DEADLINE: frozenset({
        EvidenceKind.NOTICE,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
        EvidenceKind.OFFICIAL_EVENT,
    }),
    DateKind.CONFIRMED_RECOMPETE: frozenset({
        EvidenceKind.NOTICE,
        EvidenceKind.AGENCY_FORECAST,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.AWARD_END_RESEARCH_CLOCK: frozenset({EvidenceKind.AWARD}),
    DateKind.BUDGET_MILESTONE: frozenset({EvidenceKind.BUDGET}),
    DateKind.EVENT_START: frozenset({
        EvidenceKind.OFFICIAL_EVENT,
        EvidenceKind.ORGANIZER_EVENT,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.EVENT_END: frozenset({
        EvidenceKind.OFFICIAL_EVENT,
        EvidenceKind.ORGANIZER_EVENT,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.MONITOR_DATE: frozenset({
        EvidenceKind.NOTICE,
        EvidenceKind.AGENCY_FORECAST,
        EvidenceKind.AWARD,
        EvidenceKind.BUDGET,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.ON_RAMP_OPEN: frozenset({
        EvidenceKind.VEHICLE,
        EvidenceKind.NOTICE,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.ON_RAMP_CLOSE: frozenset({
        EvidenceKind.VEHICLE,
        EvidenceKind.NOTICE,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.ORDERING_PERIOD_END: frozenset({
        EvidenceKind.VEHICLE,
        EvidenceKind.NOTICE,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
    DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK: frozenset({
        EvidenceKind.VEHICLE,
        EvidenceKind.AWARD,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }),
}

_ATTENDANCE_CLAIM = re.compile(
    r"(?:\b(?:will|confirmed\s+to|scheduled\s+to|plans?\s+to|"
    r"expected\s+to)\s+(?:be\s+)?"
    r"(?:attend(?:ing)?|speak(?:ing)?|exhibit(?:ing)?|"
    r"participat(?:e|ing)|join(?:ing)?|present(?:ing)?|appear(?:ing)?|"
    r"host(?:ing)?|take\s+part|deliver(?:ing)?\s+remarks)\b|"
    r"\bwill\s+send\s+(?:representatives?|officials?|personnel|"
    r"a\s+delegation)\b|"
    r"\bplans?\s+on\s+(?:attending|speaking|exhibiting|participating)\b|"
    r"\b(?:is|are|was|were)\s+"
    r"(?:(?:attending|speaking|exhibiting|participating|presenting|"
    r"appearing|registered|represented|present)|"
    r"(?:sending\s+(?:representatives?|officials?|personnel|"
    r"a\s+delegation))|"
    r"(?:an?\s+)?(?:listed\s+)?"
    r"(?:attendee|speaker|exhibitor|participant)|"
    r"(?:expected|scheduled)\s+(?:at|in|on)\b|"
    r"(?:scheduled|slated)\s+for\b|"
    r"on\s+(?:the\s+agenda|a\s+panel)\b)|"
    r"\b(?:will|shall)\s+be\s+"
    r"(?:at|in|on|present\b|represented\b)|"
    r"\b(?:attends?|attended)\b|"
    r"\b(?:appears?|sends?)\s+(?:at\b|representatives?\b|officials?\b|"
    r"personnel\b|a\s+delegation\b)|"
    r"\btakes?\s+part\b|"
    r"\bdelivers?\s+remarks\b|"
    r"\b(?:joins?|joined)\s+(?:the\s+)?"
    r"(?:event|summit|conference|forum|meeting|program)\b|"
    r"\bappears?\s+on\s+(?:the\s+)?"
    r"(?:speaker|exhibitor|attendee|participant|agenda)\s*"
    r"(?:list|roster)?\b|"
    r"\b(?:is|are|was|were)\s+listed\s+as\s+(?:an?\s+)?"
    r"(?:speaker|exhibitor|attendee|participant)\b|"
    r"\b(?:is|are|was|were)\s+among\s+the\s+"
    r"(?:participating|attending|exhibiting)\s+"
    r"(?:agencies|organizations|companies|groups)\b|"
    r"\b(?:participating|attending|exhibiting)\s+"
    r"(?:agencies|organizations|companies|groups)\s+include\b|"
    r"\b(?:participant|speaker|exhibitor|attendee)\s+"
    r"(?:list|roster)\s+includes?\b|"
    r"\bremarks\s+by\b[^.\n]{0,80}\b(?:are|were|will\s+be)\s+"
    r"scheduled\b|"
    r"\b(?:spoke|speaks?|exhibits?|exhibited|participates?|participated)\s+"
    r"(?:at|in|on)\b|"
    r"\b(?:has|have)\s+registered\b|"
    r"\b(?:has|have)\s+confirmed\s+(?:its|their)\s+presence\b|"
    r"\b(?:has|have|will\s+have)\s+(?:a\s+)?booth\b|"
    r"\b(?:delegation|representatives?|officials?|personnel)\b"
    r"[^.\n]{0,80}\b(?:present|expected|scheduled|agenda|attend|speak|"
    r"exhibit|participate)\w*\b|"
    r"\bconfirmed\s+(?:attendance|participation|speaker|exhibitor)\b|"
    r"\b(?:attendance|participation)\s+"
    r"(?:is|are|was|were)\s+(?:confirmed|expected|scheduled)\b|"
    r"\b(?:speakers?|exhibitors?|attendees?)\s+from\b|"
    r"\b(?:attendance|participation)\s+(?:by|from)\b|"
    r"\bagency\s+(?:attendance|speaker|exhibitor)\b)",
    re.IGNORECASE,
)


class _CalendarContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def add_calendar_months(day: date, months: int) -> date:
    """Advance by exact calendar months, clamping the day when necessary."""

    if months < 0:
        raise ValueError("calendar horizon months cannot be negative")
    zero_based = day.year * 12 + day.month - 1 + months
    year, month_index = divmod(zero_based, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


class CalendarItemOrigin(str, Enum):
    CANDIDATE_DATE = "candidate_date"
    VEHICLE_WATCH_RECORD = "vehicle_watch_record"
    VEHICLE_SIGNAL = "vehicle_signal"
    VERIFIED_EVENT = "verified_event"


class CalendarDisposition(str, Enum):
    ACTIVE = "active"
    HISTORICAL = "historical"
    OUT_OF_HORIZON = "out_of_horizon"
    CONTENT_LIMIT = "content_limit"


class EventSeed(_CalendarContract):
    """One verified event observation before semantic identity clustering."""

    external_event_id: Optional[str] = None
    title: str = Field(min_length=1)
    kind: EventKind
    timing: Optional[DateValue] = None
    organizer: Optional[str] = None
    agency: Optional[str] = None
    location: Optional[str] = None
    virtual: bool = False
    audience: Optional[str] = None
    relevance: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    registration_url: Optional[HttpUrl] = None
    agenda_url: Optional[HttpUrl] = None
    venue_url: Optional[HttpUrl] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    registration_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    agenda_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    venue_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    attending_agencies: tuple[str, ...] = Field(default_factory=tuple)
    attendance_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    flagship: bool = False
    flagship_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    lifecycle_status: EventLifecycleStatus = EventLifecycleStatus.SCHEDULED
    last_checked_at: datetime
    query_ids: tuple[str, ...] = Field(default_factory=tuple)
    discovery_lead_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _seed_is_explicit_and_non_dispositive(self) -> "EventSeed":
        _require_aware(self.last_checked_at, "event seed last_checked_at")
        if self.validate_next.casefold() == self.relevance.casefold():
            raise ValueError(
                "event validation action must be separate from relevance"
            )
        for label, values in (
            ("event seed evidence IDs", self.evidence_ids),
            ("event seed registration evidence IDs",
             self.registration_evidence_ids),
            ("event seed agenda evidence IDs", self.agenda_evidence_ids),
            ("event seed venue evidence IDs", self.venue_evidence_ids),
            ("event seed attendance evidence IDs",
             self.attendance_evidence_ids),
            ("event seed flagship evidence IDs", self.flagship_evidence_ids),
            ("event seed query IDs", self.query_ids),
            ("event seed discovery lead IDs", self.discovery_lead_ids),
        ):
            _unique(values, label)
        _unique(
            (value.casefold() for value in self.attending_agencies),
            "event seed attending agencies",
        )
        if self.external_event_id is not None \
                and not self.external_event_id.strip():
            raise ValueError("external event identity cannot be blank")
        role_links = (
            ("registration", self.registration_url,
             self.registration_evidence_ids),
            ("agenda", self.agenda_url, self.agenda_evidence_ids),
            ("venue", self.venue_url, self.venue_evidence_ids),
        )
        for label, value, evidence_ids in role_links:
            if bool(value) != bool(evidence_ids):
                raise ValueError(
                    f"event seed {label} link and evidence must be supplied "
                    "together"
                )
        if bool(self.attending_agencies) != bool(self.attendance_evidence_ids):
            raise ValueError(
                "event seed attendance claims and evidence must be supplied "
                "together"
            )
        if self.flagship:
            if self.kind not in (
                EventKind.CONFERENCE,
                EventKind.INDUSTRY_EVENT,
            ):
                raise ValueError("only an industry event seed can be flagship")
            if not self.flagship_evidence_ids:
                raise ValueError("flagship event seed requires explicit evidence")
        elif self.flagship_evidence_ids:
            raise ValueError(
                "non-flagship event seed cannot carry flagship evidence"
            )
        if self.lifecycle_status == EventLifecycleStatus.SCHEDULED \
                and self.timing is None:
            raise ValueError("scheduled event seed requires timing")
        if self.lifecycle_status == EventLifecycleStatus.DATE_TBD \
                and self.timing is not None:
            raise ValueError("date-TBD event seed cannot carry synthetic timing")
        if bool(self.query_ids) != bool(self.discovery_lead_ids):
            raise ValueError(
                "event seed query and discovery-lead lineage must be supplied "
                "together"
            )
        assert_candidate_review_language(
            self.title,
            self.organizer or "",
            self.agency or "",
            self.location or "",
            self.audience or "",
            self.relevance,
            self.validate_next,
            *self.attending_agencies,
        )
        if self.kind in (EventKind.CONFERENCE, EventKind.INDUSTRY_EVENT) \
                and (not self.query_ids or not self.discovery_lead_ids):
            raise ValueError(
                "researched conference or industry event requires query and "
                "discovery-lead lineage"
            )
        return self


class CalendarItem(_CalendarContract):
    calendar_id: str = Field(min_length=1)
    origin: CalendarItemOrigin
    title: str = Field(min_length=1)
    agency: Optional[str] = None
    timing: DateValue
    candidate_id: Optional[str] = None
    vehicle_watch_record_id: Optional[str] = None
    vehicle_signal_id: Optional[str] = None
    event_id: Optional[str] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_urls: tuple[HttpUrl, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _item_has_one_owner_and_resolvable_sources(self) -> "CalendarItem":
        _unique(self.evidence_ids, "calendar item evidence IDs")
        _unique(tuple(str(value) for value in self.source_urls),
                "calendar item source URLs")
        if self.origin == CalendarItemOrigin.CANDIDATE_DATE:
            if (
                not self.candidate_id
                or self.vehicle_watch_record_id
                or self.vehicle_signal_id
                or self.event_id
            ):
                raise ValueError("candidate calendar item has invalid ownership")
        elif self.origin == CalendarItemOrigin.VEHICLE_WATCH_RECORD:
            if (
                not self.vehicle_watch_record_id
                or self.candidate_id
                or self.vehicle_signal_id
                or self.event_id
            ):
                raise ValueError(
                    "vehicle-watch calendar item has invalid ownership"
                )
        elif self.origin == CalendarItemOrigin.VEHICLE_SIGNAL:
            if (
                not self.vehicle_signal_id
                or self.candidate_id
                or self.vehicle_watch_record_id
                or self.event_id
            ):
                raise ValueError("vehicle calendar item has invalid ownership")
        elif (
            not self.event_id
            or self.candidate_id
            or self.vehicle_watch_record_id
            or self.vehicle_signal_id
        ):
            raise ValueError("event calendar item has invalid ownership")
        assert_candidate_review_language(
            self.title,
            self.agency or "",
        )
        return self


class DeferredCalendarItem(_CalendarContract):
    item: CalendarItem
    disposition: CalendarDisposition
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _not_active(self) -> "DeferredCalendarItem":
        if self.disposition == CalendarDisposition.ACTIVE:
            raise ValueError("deferred calendar item cannot be active")
        return self


class CalendarCandidateDateOwner(_CalendarContract):
    """Immutable candidate/date identity captured at calendar build time."""

    candidate_id: str = Field(min_length=1)
    date_id: str = Field(min_length=1)


class CalendarVehicleDateOwner(_CalendarContract):
    """Immutable vehicle-signal/date identity captured at build time."""

    vehicle_signal_id: str = Field(min_length=1)
    date_id: str = Field(min_length=1)


class CalendarEvidenceAttribution(_CalendarContract):
    """One verified event evidence identity traced to one discovery lead."""

    event_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    lead_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)


class CalendarCoverageReceipt(_CalendarContract):
    binding: ArtifactBinding
    as_of: datetime
    query_manifest_id: str = Field(min_length=1)
    window_start: date
    confirmed_event_through: date
    flagship_event_through: date
    procurement_through: date
    rows: tuple[CoverageRecord, ...]
    required_row_keys: tuple[str, ...]
    accepted_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    comprehensive: bool
    public_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _receipt_closes_coverage_without_overclaiming(
        self,
    ) -> "CalendarCoverageReceipt":
        _require_aware(self.as_of, "calendar coverage as_of")
        if self.window_start != self.as_of.date():
            raise ValueError("calendar coverage must begin on the as-of date")
        if not (
            self.window_start <= self.confirmed_event_through
            <= self.flagship_event_through <= self.procurement_through
        ):
            raise ValueError("calendar coverage horizons are inconsistent")
        if self.confirmed_event_through != add_calendar_months(
            self.window_start,
            12,
        ):
            raise ValueError("confirmed event coverage horizon is not 12 months")
        if self.flagship_event_through != add_calendar_months(
            self.window_start,
            18,
        ):
            raise ValueError("flagship event coverage horizon is not 18 months")
        if self.procurement_through != add_calendar_months(
            self.window_start,
            24,
        ):
            raise ValueError("procurement coverage horizon is not 24 months")
        _unique(self.accepted_evidence_ids,
                "calendar coverage accepted evidence IDs")
        row_keys = tuple(row.identity_key for row in self.rows)
        _unique(row_keys, "calendar coverage rows")
        _unique(self.required_row_keys, "required calendar coverage rows")
        derived_required_keys = tuple(
            key for key, row in zip(row_keys, self.rows)
            if row.query_required
        )
        if self.required_row_keys != derived_required_keys:
            raise ValueError(
                "required calendar coverage rows differ from the census"
            )
        if any(
            (row.client_id, row.run_id, row.scope_sha256) != (
                self.binding.client_id,
                self.binding.run_id,
                self.binding.scope_sha256,
            )
            for row in self.rows
        ):
            raise ValueError("calendar coverage row binding mismatch")
        if any(row.query_manifest_id != self.query_manifest_id
               for row in self.rows):
            raise ValueError("calendar coverage manifest mismatch")
        if any(
            row.window_start != self.window_start
            or row.window_end != self.flagship_event_through
            for row in self.rows
        ):
            raise ValueError("calendar coverage row window mismatch")
        row_evidence = {
            evidence_id
            for row in self.rows
            for evidence_id in row.accepted_evidence_ids
        }
        if row_evidence != set(self.accepted_evidence_ids):
            raise ValueError(
                "calendar coverage receipt does not close accepted evidence"
            )
        incomplete = {
            CoverageState.PARTIAL,
            CoverageState.PARTIAL_FALLBACK,
            CoverageState.STALE_SNAPSHOT,
            CoverageState.FAILED,
            CoverageState.NOT_RUN,
            CoverageState.SCOPE_EXCLUDED,
            CoverageState.NOT_USED,
        }
        derived = not any(
            row.state in incomplete
            for key, row in zip(row_keys, self.rows)
            if key in self.required_row_keys
        )
        if self.comprehensive != derived:
            raise ValueError("calendar comprehensive flag is not derived")
        completed_count = sum(
            1 for key, row in zip(row_keys, self.rows)
            if key in self.required_row_keys and row.state not in incomplete
        )
        required_count = len(self.required_row_keys)
        expected_summary = (
            "Event research query/source census complete: "
            f"{completed_count} of {required_count} required lanes attempted."
            if derived else
            "Event research query/source census incomplete: "
            f"{completed_count} of {required_count} required lanes completed; "
            "failed, partial, stale, or not-run lanes remain visible internally."
        )
        if self.public_summary != expected_summary:
            raise ValueError("calendar coverage public summary is not derived")
        if self.comprehensive and "internet" in self.public_summary.casefold():
            raise ValueError("coverage cannot claim the entire internet")
        return self


class CalendarBuildResult(_CalendarContract):
    binding: ArtifactBinding
    as_of: datetime
    maximum_items: int = Field(
        ge=0,
        le=CONTENT_BUDGETS["calendar_events"],
    )
    candidates: tuple[CandidateUnit, ...]
    candidate_ids: tuple[str, ...]
    candidate_date_owners: tuple[CalendarCandidateDateOwner, ...]
    vehicle_signals: tuple[VehicleSignal, ...] = Field(default_factory=tuple)
    vehicle_date_owners: tuple[CalendarVehicleDateOwner, ...] = Field(
        default_factory=tuple)
    evidence: tuple[EvidenceRecord, ...]
    manifest: EventQueryManifest
    attempts: tuple[EventQueryAttempt, ...]
    event_attributions: tuple[CalendarEvidenceAttribution, ...]
    coverage_receipt: CalendarCoverageReceipt
    active_items: tuple[CalendarItem, ...]
    deferred_items: tuple[DeferredCalendarItem, ...]
    events_for_document: tuple[EventRecord, ...]
    audit_events: tuple[EventRecord, ...]

    @model_validator(mode="after")
    def _result_is_one_ordered_audit_universe(self) -> "CalendarBuildResult":
        _require_aware(self.as_of, "calendar build as_of")
        if self.manifest.binding != self.binding \
                or self.coverage_receipt.binding != self.binding:
            raise ValueError("calendar result binding mismatch")
        if self.coverage_receipt.as_of != self.as_of:
            raise ValueError("calendar coverage as-of mismatch")
        if self.coverage_receipt.query_manifest_id != self.manifest.manifest_id:
            raise ValueError("calendar coverage references another manifest")
        if (
            self.coverage_receipt.window_start != self.manifest.window_start
            or self.coverage_receipt.confirmed_event_through
            != self.manifest.confirmed_event_through
            or self.coverage_receipt.flagship_event_through
            != self.manifest.flagship_event_through
            or self.coverage_receipt.procurement_through
            != add_calendar_months(self.manifest.window_start, 24)
        ):
            raise ValueError(
                "calendar coverage horizons differ from the manifest"
            )
        _unique(self.candidate_ids, "calendar build candidate IDs")
        derived_candidate_ids = tuple(
            candidate.candidate_id for candidate in self.candidates)
        if self.candidate_ids != derived_candidate_ids:
            raise ValueError(
                "calendar candidate IDs differ from candidate snapshots"
            )
        if any(
            candidate.client_id != self.binding.client_id
            for candidate in self.candidates
        ):
            raise ValueError(
                "calendar candidate snapshot belongs to another client"
            )
        owner_keys = tuple(
            f"{owner.candidate_id}\x00{owner.date_id}"
            for owner in self.candidate_date_owners
        )
        _unique(owner_keys, "calendar build candidate/date owners")
        _unique(
            (owner.date_id for owner in self.candidate_date_owners),
            "calendar build candidate date IDs",
        )
        if any(owner.candidate_id not in self.candidate_ids
               for owner in self.candidate_date_owners):
            raise ValueError("calendar date owner is absent from candidate universe")
        derived_date_owners = tuple(
            CalendarCandidateDateOwner(
                candidate_id=candidate.candidate_id,
                date_id=timing.date_id,
            )
            for candidate in self.candidates
            for timing in candidate.dates
        )
        if self.candidate_date_owners != derived_date_owners:
            raise ValueError(
                "calendar date owners differ from candidate snapshots"
            )
        vehicle_signal_ids = tuple(
            signal.signal_id for signal in self.vehicle_signals)
        _unique(vehicle_signal_ids, "calendar vehicle signal IDs")
        if any(
            (signal.client_id, signal.run_id, signal.scope_sha256) != (
                self.binding.client_id,
                self.binding.run_id,
                self.binding.scope_sha256,
            )
            or signal.last_checked_at > self.as_of
            for signal in self.vehicle_signals
        ):
            raise ValueError(
                "calendar vehicle signal crosses binding or postdates as-of"
            )
        vehicle_owner_keys = tuple(
            f"{owner.vehicle_signal_id}\x00{owner.date_id}"
            for owner in self.vehicle_date_owners
        )
        _unique(vehicle_owner_keys, "calendar vehicle/date owners")
        _unique(
            tuple(owner.date_id for owner in self.vehicle_date_owners),
            "calendar vehicle date IDs",
        )
        derived_vehicle_owners = tuple(
            CalendarVehicleDateOwner(
                vehicle_signal_id=signal.signal_id,
                date_id=timing.date_id,
            )
            for signal in self.vehicle_signals
            for timing in signal.dates
        )
        if self.vehicle_date_owners != derived_vehicle_owners:
            raise ValueError(
                "calendar vehicle date owners differ from signal snapshots"
            )
        expected_coverage_keys = tuple(
            "\x00".join((
                query.source_class.value.casefold(),
                query.query_family.value.casefold(),
                query.query_id.casefold(),
                (query.watch_target_id or "").casefold(),
            ))
            for query in self.manifest.queries
        )
        actual_coverage_keys = tuple(
            row.identity_key for row in self.coverage_receipt.rows
        )
        if actual_coverage_keys != expected_coverage_keys:
            raise ValueError(
                "calendar coverage does not close the manifest census"
            )
        if tuple(
            row.query_required for row in self.coverage_receipt.rows
        ) != tuple(query.required for query in self.manifest.queries):
            raise ValueError(
                "calendar coverage requirements differ from the manifest"
            )
        attempt_ids = tuple(attempt.query_id for attempt in self.attempts)
        _unique(attempt_ids, "calendar result query attempts")
        manifest_query_ids = tuple(
            query.query_id for query in self.manifest.queries)
        if any(query_id not in manifest_query_ids for query_id in attempt_ids):
            raise ValueError("calendar result attempt is absent from manifest")
        if attempt_ids != tuple(
            query_id for query_id in manifest_query_ids
            if query_id in set(attempt_ids)
        ):
            raise ValueError("calendar result attempts are not in manifest order")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        _unique(evidence_ids, "calendar build evidence IDs")
        for item in self.evidence:
            if (item.client_id, item.run_id, item.scope_sha256) != (
                self.binding.client_id,
                self.binding.run_id,
                self.binding.scope_sha256,
            ):
                raise ValueError(
                    "calendar result evidence crosses client, run, or scope"
                )
            if item.retrieved_at > self.as_of:
                raise ValueError("calendar result evidence postdates as-of")
        source_keys = tuple(
            item.source_identity.canonical_key for item in self.evidence)
        _unique(source_keys, "calendar build source identities")
        _unique((item.calendar_id for item in self.active_items),
                "active calendar IDs")
        _unique((item.item.calendar_id for item in self.deferred_items),
                "deferred calendar IDs")
        if set(item.calendar_id for item in self.active_items) \
                & set(item.item.calendar_id for item in self.deferred_items):
            raise ValueError("calendar item appears in active and deferred output")
        if tuple(_calendar_sort_key(item) for item in self.active_items) \
                != tuple(sorted(_calendar_sort_key(item)
                                for item in self.active_items)):
            raise ValueError("active calendar output is not chronological")
        if sum(
            item.origin == CalendarItemOrigin.VERIFIED_EVENT
            for item in self.active_items
        ) > MAX_VISIBLE_RESEARCHED_EVENTS:
            raise ValueError(
                "active calendar exceeds the researched-event display cap"
            )
        _unique((item.event_id for item in self.audit_events),
                "audit event IDs")
        document_ids = {item.event_id for item in self.events_for_document}
        audit_by_id = {item.event_id: item for item in self.audit_events}
        audit_ids = set(audit_by_id)
        if not document_ids.issubset(audit_ids):
            raise ValueError("document event is absent from the audit inventory")
        active_event_ids = {
            item.event_id
            for item in self.active_items
            if item.origin == CalendarItemOrigin.VERIFIED_EVENT
        }
        if document_ids != active_event_ids:
            raise ValueError(
                "document events differ from active calendar event items"
            )
        if any(
            event != audit_by_id.get(event.event_id)
            for event in self.events_for_document
        ):
            raise ValueError(
                "document event content differs from the audit inventory"
            )
        for event in self.audit_events:
            if (event.client_id, event.run_id, event.scope_sha256) != (
                self.binding.client_id,
                self.binding.run_id,
                self.binding.scope_sha256,
            ):
                raise ValueError(
                    "calendar result event crosses client, run, or scope"
                )
            if event.last_checked_at > self.as_of:
                raise ValueError("calendar result event postdates as-of")
            validate_event_watch_freshness(event, as_of=self.as_of)
        _unique(
            (
                *(owner.date_id for owner in self.candidate_date_owners),
                *(owner.date_id for owner in self.vehicle_date_owners),
                *(event.timing.date_id for event in self.audit_events
                  if event.timing is not None),
            ),
            "calendar build date IDs",
        )
        attribution_keys = tuple(
            "\x00".join((
                item.event_id,
                item.query_id,
                item.lead_id,
                item.evidence_id,
            ))
            for item in self.event_attributions
        )
        _unique(attribution_keys, "calendar evidence attributions")
        _unique(
            tuple(item.lead_id for item in self.event_attributions),
            "calendar accepted discovery leads",
        )
        attempts_by_id = {
            attempt.query_id: attempt for attempt in self.attempts
        }
        queries_by_id = {
            query.query_id: query for query in self.manifest.queries
        }
        evidence_by_id = {
            item.evidence_id: item for item in self.evidence
        }
        for signal in self.vehicle_signals:
            validate_vehicle_signal_evidence(
                signal,
                binding=self.binding,
                as_of=self.as_of,
                evidence_by_id=evidence_by_id,
                candidate_ids=self.candidate_ids,
            )
        accepted_by_query: dict[str, set[str]] = defaultdict(set)
        for item in self.event_attributions:
            event = audit_by_id.get(item.event_id)
            query = queries_by_id.get(item.query_id)
            attempt = attempts_by_id.get(item.query_id)
            evidence = evidence_by_id.get(item.evidence_id)
            if event is None or query is None or attempt is None \
                    or evidence is None:
                raise ValueError(
                    "calendar evidence attribution is unresolved"
                )
            if item.lead_id not in attempt.lead_ids:
                raise ValueError(
                    "calendar evidence attribution is absent from its attempt"
                )
            if item.evidence_id not in event.all_evidence_ids:
                raise ValueError(
                    "calendar attributed evidence is absent from its event"
                )
            if not _attribution_matches_source_class(
                item,
                event=event,
                query=query,
                evidence=evidence,
                trusted_organizer_domains=(
                    self.manifest.trusted_organizer_domains
                ),
            ):
                raise ValueError(
                    "calendar evidence attribution has the wrong source class"
                )
            accepted_by_query[item.query_id].add(item.evidence_id)
        attributed_event_ids = {
            item.event_id for item in self.event_attributions
        }
        researched_event_ids = {
            event.event_id
            for event in self.audit_events
            if event.kind in (
                EventKind.CONFERENCE,
                EventKind.INDUSTRY_EVENT,
            )
        }
        if attributed_event_ids != researched_event_ids:
            raise ValueError(
                "researched events and discovery attributions differ"
            )
        expected_receipt = _build_coverage_receipt(
            binding=self.binding,
            as_of=self.as_of,
            manifest=self.manifest,
            attempts=self.attempts,
            accepted_by_query=accepted_by_query,
        )
        if self.coverage_receipt != expected_receipt:
            raise ValueError(
                "calendar coverage receipt differs from event attributions"
            )
        candidate_owner_keys = {
            (owner.candidate_id, owner.date_id)
            for owner in self.candidate_date_owners
        }
        vehicle_owner_keys = {
            (owner.vehicle_signal_id, owner.date_id)
            for owner in self.vehicle_date_owners
        }
        all_items = (
            *self.active_items,
            *(row.item for row in self.deferred_items),
        )
        for item in all_items:
            if item.origin == CalendarItemOrigin.CANDIDATE_DATE:
                if (item.candidate_id, item.timing.date_id) \
                        not in candidate_owner_keys:
                    raise ValueError(
                        "calendar item is absent from candidate/date universe"
                    )
            elif item.origin == CalendarItemOrigin.VEHICLE_SIGNAL:
                if (item.vehicle_signal_id, item.timing.date_id) \
                        not in vehicle_owner_keys:
                    raise ValueError(
                        "calendar item is absent from vehicle/date universe"
                    )
            elif item.event_id not in audit_ids:
                raise ValueError(
                    "calendar item is absent from the event audit universe"
                )
        unresolved = (
            {
                evidence_id
                for item in all_items
                for evidence_id in item.evidence_ids
            }
            | {
                evidence_id
                for event in self.audit_events
                for evidence_id in event.all_evidence_ids
            }
            | {
                evidence_id
                for candidate in self.candidates
                for evidence_id in candidate.all_evidence_ids
            }
            | {
                evidence_id
                for signal in self.vehicle_signals
                for evidence_id in signal.all_evidence_ids
            }
            | set(self.coverage_receipt.accepted_evidence_ids)
        ) - set(evidence_ids)
        if unresolved:
            raise ValueError(
                "calendar result has unresolved evidence: "
                + ", ".join(sorted(unresolved))
            )
        expected_active, expected_deferred = _calendar_partitions(
            candidates=self.candidates,
            vehicle_signals=self.vehicle_signals,
            audit_events=self.audit_events,
            evidence_by_id=evidence_by_id,
            as_of=self.as_of.date(),
            maximum_items=self.maximum_items,
        )
        if self.active_items != expected_active \
                or self.deferred_items != expected_deferred:
            raise ValueError(
                "calendar result items differ from the evidence-bound projection"
            )
        expected_event_ids = {
            item.event_id
            for item in expected_active
            if item.origin == CalendarItemOrigin.VERIFIED_EVENT
        }
        expected_document_events = tuple(sorted(
            (
                event for event in self.audit_events
                if event.event_id in expected_event_ids
            ),
            key=_event_sort_key,
        ))
        if self.events_for_document != expected_document_events:
            raise ValueError(
                "document events differ from the evidence-bound projection"
            )
        return self

    def hydrate_document(
        self,
        document: CandidateReviewDocument,
    ) -> CandidateReviewDocument:
        """Return the same bound document with this calendar and census."""

        if document.binding != self.binding or document.as_of != self.as_of:
            raise ValueError("calendar cannot hydrate another document binding")
        document_candidate_ids = tuple(
            item.candidate_id for item in document.candidates)
        if set(document_candidate_ids) != set(self.candidate_ids) \
                or len(document_candidate_ids) != len(self.candidate_ids):
            raise ValueError(
                "calendar cannot hydrate a different candidate universe"
            )
        if {
            candidate.candidate_id: candidate
            for candidate in document.candidates
        } != {
            candidate.candidate_id: candidate
            for candidate in self.candidates
        }:
            raise ValueError(
                "calendar cannot hydrate different candidate content"
            )
        document_date_owners = {
            (candidate.candidate_id, timing.date_id)
            for candidate in document.candidates
            for timing in candidate.dates
        }
        result_date_owners = {
            (owner.candidate_id, owner.date_id)
            for owner in self.candidate_date_owners
        }
        if document_date_owners != result_date_owners:
            raise ValueError(
                "calendar cannot hydrate different candidate/date ownership"
            )
        if document.vehicle_signals \
                and document.vehicle_signals != self.vehicle_signals:
            raise ValueError(
                "calendar cannot hydrate conflicting vehicle signal content"
            )
        evidence_by_id = {item.evidence_id: item for item in document.evidence}
        source_owners = {
            item.source_identity.canonical_key: item.evidence_id
            for item in document.evidence
        }
        merged_evidence = list(document.evidence)
        for item in self.evidence:
            prior = evidence_by_id.get(item.evidence_id)
            if prior is not None and prior != item:
                raise ValueError("calendar evidence ID conflicts with document")
            source_owner = source_owners.get(item.source_identity.canonical_key)
            if source_owner is not None and source_owner != item.evidence_id:
                raise ValueError("calendar source identity conflicts with document")
            if prior is None:
                evidence_by_id[item.evidence_id] = item
                source_owners[item.source_identity.canonical_key] = item.evidence_id
                merged_evidence.append(item)

        coverage_by_key = {
            item.identity_key: item for item in document.coverage
        }
        merged_coverage = list(document.coverage)
        for item in self.coverage_receipt.rows:
            key = item.identity_key
            prior = coverage_by_key.get(key)
            if prior is not None and prior != item:
                raise ValueError("calendar coverage conflicts with document")
            if prior is None:
                coverage_by_key[key] = item
                merged_coverage.append(item)

        return CandidateReviewDocument.model_validate({
            **document.model_dump(mode="python"),
            "vehicle_signals": self.vehicle_signals,
            "calendar_events": self.events_for_document,
            "evidence": tuple(merged_evidence),
            "coverage": tuple(merged_coverage),
        })


def build_calendar(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    candidates: Iterable[CandidateUnit],
    evidence: Iterable[EvidenceRecord],
    event_seeds: Iterable[EventSeed],
    manifest: EventQueryManifest,
    attempts: Iterable[EventQueryAttempt] = (),
    trusted_organizer_domains: Iterable[str] = (),
    vehicle_signals: Iterable[VehicleSignal] = (),
    maximum_items: int = CONTENT_BUDGETS["calendar_events"],
) -> CalendarBuildResult:
    """Build a verified, coverage-closed chronological calendar."""

    _require_aware(as_of, "calendar build as_of")
    if isinstance(maximum_items, bool) or not isinstance(maximum_items, int) \
            or maximum_items < 0 \
            or maximum_items > CONTENT_BUDGETS["calendar_events"]:
        raise ValueError(
            "maximum calendar items must be between 0 and "
            f"{CONTENT_BUDGETS['calendar_events']}"
        )
    if manifest.binding != binding or manifest.window_start != as_of.date():
        raise ValueError("event query manifest does not match calendar binding")

    canonical_evidence = tuple(sorted(evidence, key=lambda item: item.evidence_id))
    evidence_by_id = {item.evidence_id: item for item in canonical_evidence}
    if len(evidence_by_id) != len(canonical_evidence):
        raise ValueError("calendar evidence IDs must be unique")
    source_keys = tuple(
        item.source_identity.canonical_key for item in canonical_evidence)
    _unique(source_keys, "calendar evidence source identities")
    for item in canonical_evidence:
        if (item.client_id, item.run_id, item.scope_sha256) != (
            binding.client_id, binding.run_id, binding.scope_sha256,
        ):
            raise ValueError("calendar evidence crosses client, run, or scope")
        if item.retrieved_at > as_of:
            raise ValueError("calendar evidence retrieval postdates as-of")

    candidate_rows = tuple(candidates)
    _unique((item.candidate_id for item in candidate_rows),
            "calendar candidate IDs")
    candidate_ids = tuple(item.candidate_id for item in candidate_rows)
    candidate_date_owners = tuple(
        CalendarCandidateDateOwner(
            candidate_id=candidate.candidate_id,
            date_id=timing.date_id,
        )
        for candidate in candidate_rows
        for timing in candidate.dates
    )
    timed_items: list[tuple[CalendarItem, CalendarDisposition, str]] = []
    global_date_owners: dict[str, str] = {}
    for candidate in candidate_rows:
        if candidate.client_id != binding.client_id:
            raise ValueError("calendar candidate belongs to another client")
        for timing in candidate.dates:
            owner = f"candidate {candidate.candidate_id}"
            prior = global_date_owners.setdefault(timing.date_id, owner)
            if prior != owner:
                raise ValueError("calendar date IDs must be globally unique")
            if timing.kind not in _PROCUREMENT_DATE_KINDS:
                continue
            _validate_timing_evidence(timing, evidence_by_id)
            item = _candidate_calendar_item(candidate, timing, evidence_by_id)
            disposition, reason = _procurement_disposition(timing, as_of.date())
            timed_items.append((item, disposition, reason))

    vehicle_rows = tuple(vehicle_signals)
    _unique(
        (signal.signal_id for signal in vehicle_rows),
        "calendar vehicle signal IDs",
    )
    vehicle_date_owners = tuple(
        CalendarVehicleDateOwner(
            vehicle_signal_id=signal.signal_id,
            date_id=timing.date_id,
        )
        for signal in vehicle_rows
        for timing in signal.dates
    )
    for signal in vehicle_rows:
        validate_vehicle_signal_evidence(
            signal,
            binding=binding,
            as_of=as_of,
            evidence_by_id=evidence_by_id,
            candidate_ids=candidate_ids,
        )
        if (signal.client_id, signal.run_id, signal.scope_sha256) != (
            binding.client_id, binding.run_id, binding.scope_sha256,
        ):
            raise ValueError(
                "calendar vehicle signal crosses client, run, or scope"
            )
        if signal.last_checked_at > as_of:
            raise ValueError("calendar vehicle signal postdates as-of")
        if any(candidate_id not in candidate_ids
               for candidate_id in signal.candidate_ids):
            raise ValueError(
                "calendar vehicle signal references an unknown candidate"
            )
        unresolved = set(signal.all_evidence_ids) - set(evidence_by_id)
        if unresolved:
            raise ValueError(
                "calendar vehicle signal has unresolved evidence: "
                + ", ".join(sorted(unresolved))
            )
        for timing in signal.dates:
            owner = f"vehicle signal {signal.signal_id}"
            prior = global_date_owners.setdefault(timing.date_id, owner)
            if prior != owner:
                raise ValueError("calendar date IDs must be globally unique")
            _validate_timing_evidence(timing, evidence_by_id)
            item = _vehicle_calendar_item(signal, timing, evidence_by_id)
            disposition, reason = _procurement_disposition(
                timing,
                as_of.date(),
            )
            timed_items.append((item, disposition, reason))

    attempt_rows = tuple(attempts)
    attempt_by_id = {item.query_id: item for item in attempt_rows}
    if len(attempt_by_id) != len(attempt_rows):
        raise ValueError("event query attempts must be unique")
    lead_owner: dict[str, str] = {}
    for attempt in attempt_rows:
        for lead_id in attempt.lead_ids:
            prior = lead_owner.setdefault(lead_id, attempt.query_id)
            if prior != attempt.query_id:
                raise ValueError("discovery lead belongs to multiple queries")

    seeds = tuple(event_seeds)
    query_ids = {item.query_id for item in manifest.queries}
    query_specs = {item.query_id: item for item in manifest.queries}
    if any(query_id not in query_ids for seed in seeds
           for query_id in seed.query_ids):
        raise ValueError("event seed references an unknown query")
    for seed in seeds:
        if seed.query_ids and (
            len(seed.query_ids) != 1
            or len(seed.discovery_lead_ids) != 1
        ):
            raise ValueError(
                "one event seed must represent exactly one discovery lead"
            )
        lineage_owners: set[str] = set()
        for lead_id in seed.discovery_lead_ids:
            owner = lead_owner.get(lead_id)
            if owner is None or owner not in seed.query_ids:
                raise ValueError(
                    "event seed discovery lead does not close against its "
                    "query attempt"
                )
            lineage_owners.add(owner)
        if lineage_owners != set(seed.query_ids):
            raise ValueError(
                "every event seed query must be backed by a discovery lead"
            )
    _unique(
        tuple(
            lead_id
            for seed in seeds
            for lead_id in seed.discovery_lead_ids
        ),
        "accepted event seed discovery leads",
    )
    domains = tuple(_normalize_domain(value)
                    for value in trusted_organizer_domains)
    _unique(domains, "trusted organizer domains")
    manifest_domains = tuple(manifest.trusted_organizer_domains)
    if tuple(sorted(domains)) != tuple(sorted(manifest_domains)):
        raise ValueError(
            "calendar trusted domains differ from the approved query manifest"
        )

    merged_events: list[
        tuple[EventRecord, tuple[CalendarEvidenceAttribution, ...]]
    ] = []
    for rows in _group_event_seeds(seeds).values():
        event, event_attributions = _merge_event_seed_group(
            rows,
            binding=binding,
            as_of=as_of,
            evidence_by_id=evidence_by_id,
            query_specs=query_specs,
            trusted_organizer_domains=domains,
        )
        if event.timing is not None:
            owner = f"event {event.event_id}"
            prior = global_date_owners.setdefault(event.timing.date_id, owner)
            if prior != owner:
                raise ValueError("calendar date IDs must be globally unique")
        merged_events.append((event, event_attributions))

    accepted_by_query: dict[str, set[str]] = defaultdict(set)
    event_attributions: list[CalendarEvidenceAttribution] = []
    audit_events: list[EventRecord] = []
    event_by_id: dict[str, EventRecord] = {}
    for event, attributions in sorted(
        merged_events,
        key=lambda row: row[0].event_id,
    ):
        normalized_event = _normalize_past_event(event, as_of.date())
        audit_events.append(normalized_event)
        event_by_id[normalized_event.event_id] = normalized_event
        event_attributions.extend(attributions)
        for attribution in attributions:
            accepted_by_query[attribution.query_id].add(
                attribution.evidence_id)
        if normalized_event.timing is None or not normalized_event.active:
            continue
        _validate_timing_evidence(normalized_event.timing, evidence_by_id)
        item = _event_calendar_item(normalized_event, evidence_by_id)
        disposition, reason = _event_disposition(
            normalized_event, as_of.date())
        timed_items.append((item, disposition, reason))

    active_pool = sorted(
        (item for item, disposition, _reason in timed_items
         if disposition == CalendarDisposition.ACTIVE),
        key=_calendar_sort_key,
    )
    active_items = _bounded_calendar_projection(
        active_pool,
        maximum_items=maximum_items,
    )
    active_ids = {item.calendar_id for item in active_items}
    deferred: list[DeferredCalendarItem] = []
    for item, disposition, reason in timed_items:
        if item.calendar_id in active_ids:
            continue
        if disposition == CalendarDisposition.ACTIVE:
            disposition = CalendarDisposition.CONTENT_LIMIT
            reason = "Valid item retained outside the bounded report projection."
        deferred.append(DeferredCalendarItem(
            item=item,
            disposition=disposition,
            reason=reason,
        ))
    deferred.sort(key=lambda row: (
        _calendar_sort_key(row.item), row.disposition.value))

    visible_event_ids = {
        item.event_id for item in active_items if item.event_id is not None
    }
    events_for_document = tuple(sorted(
        (event_by_id[event_id] for event_id in visible_event_ids),
        key=_event_sort_key,
    ))

    receipt = _build_coverage_receipt(
        binding=binding,
        as_of=as_of,
        manifest=manifest,
        attempts=attempt_rows,
        accepted_by_query=accepted_by_query,
    )
    return CalendarBuildResult(
        binding=binding,
        as_of=as_of,
        maximum_items=maximum_items,
        candidates=candidate_rows,
        candidate_ids=candidate_ids,
        candidate_date_owners=candidate_date_owners,
        vehicle_signals=vehicle_rows,
        vehicle_date_owners=vehicle_date_owners,
        evidence=canonical_evidence,
        manifest=manifest,
        attempts=attempt_rows,
        event_attributions=tuple(sorted(
            event_attributions,
            key=lambda item: (
                item.query_id,
                item.lead_id,
                item.event_id,
                item.evidence_id,
            ),
        )),
        coverage_receipt=receipt,
        active_items=active_items,
        deferred_items=tuple(deferred),
        events_for_document=events_for_document,
        audit_events=tuple(sorted(audit_events, key=_event_sort_key)),
    )


def project_document_calendar(
    document: CandidateReviewDocument,
    *,
    candidate_ids: Optional[Iterable[str]] = None,
    maximum_items: int = CONTENT_BUDGETS["calendar_events"],
) -> tuple[CalendarItem, ...]:
    """Project the document's candidate dates and active events in one order."""

    evidence_by_id = {item.evidence_id: item for item in document.evidence}
    included_candidate_ids = None \
        if candidate_ids is None else frozenset(candidate_ids)
    known_candidate_ids = {item.candidate_id for item in document.candidates}
    if included_candidate_ids is not None \
            and not included_candidate_ids.issubset(known_candidate_ids):
        raise ValueError("calendar projection references an unknown candidate")
    items: list[CalendarItem] = []
    for candidate in document.candidates:
        if included_candidate_ids is not None \
                and candidate.candidate_id not in included_candidate_ids:
            continue
        for timing in candidate.dates:
            if timing.kind not in _PROCUREMENT_DATE_KINDS:
                continue
            disposition, _reason = _procurement_disposition(
                timing, document.as_of.date())
            if disposition == CalendarDisposition.ACTIVE:
                items.append(_candidate_calendar_item(
                    candidate, timing, evidence_by_id))
    for signal in document.vehicle_signals:
        for timing in signal.dates:
            _validate_timing_evidence(timing, evidence_by_id)
            disposition, _reason = _procurement_disposition(
                timing,
                document.as_of.date(),
            )
            if disposition == CalendarDisposition.ACTIVE:
                items.append(_vehicle_calendar_item(
                    signal,
                    timing,
                    evidence_by_id,
                ))
    for record in document.vehicle_watch_records:
        for timing in record.dates:
            _validate_timing_evidence(timing, evidence_by_id)
            disposition, _reason = _procurement_disposition(
                timing,
                document.as_of.date(),
            )
            if disposition == CalendarDisposition.ACTIVE:
                items.append(_vehicle_watch_calendar_item(
                    record,
                    timing,
                    evidence_by_id,
                ))
    for event in document.calendar_events:
        if event.active and event.timing is not None:
            disposition, _reason = _event_disposition(
                event, document.as_of.date())
            if disposition == CalendarDisposition.ACTIVE:
                items.append(_event_calendar_item(event, evidence_by_id))
    return _bounded_calendar_projection(
        sorted(items, key=_calendar_sort_key),
        maximum_items=maximum_items,
    )


def _calendar_partitions(
    *,
    candidates: tuple[CandidateUnit, ...],
    vehicle_signals: tuple[VehicleSignal, ...],
    audit_events: tuple[EventRecord, ...],
    evidence_by_id: dict[str, EvidenceRecord],
    as_of: date,
    maximum_items: int,
) -> tuple[tuple[CalendarItem, ...], tuple[DeferredCalendarItem, ...]]:
    timed_items: list[tuple[CalendarItem, CalendarDisposition, str]] = []
    for candidate in candidates:
        for timing in candidate.dates:
            if timing.kind not in _PROCUREMENT_DATE_KINDS:
                continue
            item = _candidate_calendar_item(candidate, timing, evidence_by_id)
            disposition, reason = _procurement_disposition(timing, as_of)
            timed_items.append((item, disposition, reason))
    for signal in vehicle_signals:
        for timing in signal.dates:
            _validate_timing_evidence(timing, evidence_by_id)
            item = _vehicle_calendar_item(signal, timing, evidence_by_id)
            disposition, reason = _procurement_disposition(timing, as_of)
            timed_items.append((item, disposition, reason))
    for event in audit_events:
        if event.timing is None or not event.active:
            continue
        item = _event_calendar_item(event, evidence_by_id)
        disposition, reason = _event_disposition(event, as_of)
        timed_items.append((item, disposition, reason))

    active_pool = sorted(
        (
            item for item, disposition, _reason in timed_items
            if disposition == CalendarDisposition.ACTIVE
        ),
        key=_calendar_sort_key,
    )
    active_items = _bounded_calendar_projection(
        active_pool,
        maximum_items=maximum_items,
    )
    active_ids = {item.calendar_id for item in active_items}
    deferred: list[DeferredCalendarItem] = []
    for item, disposition, reason in timed_items:
        if item.calendar_id in active_ids:
            continue
        if disposition == CalendarDisposition.ACTIVE:
            disposition = CalendarDisposition.CONTENT_LIMIT
            reason = "Valid item retained outside the bounded report projection."
        deferred.append(DeferredCalendarItem(
            item=item,
            disposition=disposition,
            reason=reason,
        ))
    deferred.sort(key=lambda row: (
        _calendar_sort_key(row.item),
        row.disposition.value,
    ))
    return active_items, tuple(deferred)


def _candidate_calendar_item(
    candidate: CandidateUnit,
    timing: DateValue,
    evidence_by_id: dict[str, EvidenceRecord],
) -> CalendarItem:
    return CalendarItem(
        calendar_id="calendar-candidate-" + _short_digest(
            f"{candidate.candidate_id}:{timing.date_id}"),
        origin=CalendarItemOrigin.CANDIDATE_DATE,
        title=f"{candidate.title} · {timing.label}",
        agency=candidate.agency,
        timing=timing,
        candidate_id=candidate.candidate_id,
        evidence_ids=timing.evidence_ids,
        source_urls=_source_urls(timing.evidence_ids, evidence_by_id),
    )


def _vehicle_calendar_item(
    signal: VehicleSignal,
    timing: DateValue,
    evidence_by_id: dict[str, EvidenceRecord],
) -> CalendarItem:
    return CalendarItem(
        calendar_id="calendar-vehicle-" + _short_digest(
            f"{signal.signal_id}:{timing.date_id}"),
        origin=CalendarItemOrigin.VEHICLE_SIGNAL,
        title=f"{signal.title} · {timing.label}",
        agency=signal.agency,
        timing=timing,
        vehicle_signal_id=signal.signal_id,
        evidence_ids=timing.evidence_ids,
        source_urls=_source_urls(timing.evidence_ids, evidence_by_id),
    )


def _vehicle_watch_calendar_item(
    record: VehicleWatchRecord,
    timing: DateValue,
    evidence_by_id: dict[str, EvidenceRecord],
) -> CalendarItem:
    identity = (
        record.canonical_name
        or record.vehicle_program_id
        or record.parent_idv_piid
        or "Sourced vehicle"
    )
    return CalendarItem(
        calendar_id="calendar-vehicle-watch-" + _short_digest(
            f"{record.watch_record_id}:{timing.date_id}"
        ),
        origin=CalendarItemOrigin.VEHICLE_WATCH_RECORD,
        title=f"{identity} · {timing.label}",
        agency=record.managing_agency,
        timing=timing,
        vehicle_watch_record_id=record.watch_record_id,
        evidence_ids=timing.evidence_ids,
        source_urls=_source_urls(timing.evidence_ids, evidence_by_id),
    )


def _event_calendar_item(
    event: EventRecord,
    evidence_by_id: dict[str, EvidenceRecord],
) -> CalendarItem:
    if event.timing is None:
        raise ValueError("date-less event cannot enter chronological projection")
    return CalendarItem(
        calendar_id="calendar-event-" + _short_digest(event.event_id),
        origin=CalendarItemOrigin.VERIFIED_EVENT,
        title=event.title,
        agency=event.agency,
        timing=event.timing,
        event_id=event.event_id,
        evidence_ids=event.all_evidence_ids,
        source_urls=_source_urls(event.all_evidence_ids, evidence_by_id),
    )


def _source_urls(
    evidence_ids: Iterable[str],
    evidence_by_id: dict[str, EvidenceRecord],
) -> tuple[HttpUrl, ...]:
    urls: list[HttpUrl] = []
    seen: set[str] = set()
    for evidence_id in evidence_ids:
        record = evidence_by_id.get(evidence_id)
        if record is None:
            raise ValueError(f"calendar evidence is unresolved: {evidence_id}")
        key = str(record.source_url)
        if key not in seen:
            seen.add(key)
            urls.append(record.source_url)
    return tuple(urls)


def _validate_timing_evidence(
    timing: DateValue,
    evidence_by_id: dict[str, EvidenceRecord],
) -> None:
    refs = []
    for evidence_id in timing.evidence_ids:
        record = evidence_by_id.get(evidence_id)
        if record is None:
            raise ValueError(f"calendar timing evidence is unresolved: {evidence_id}")
        refs.append(record)
    allowed = _TIMING_SOURCE_KINDS.get(timing.kind)
    if allowed is None or not any(
        record.source_kind in allowed
        and record.official_source
        and record.primary_source
        for record in refs
    ):
        raise ValueError(
            "calendar timing is not backed by compatible official evidence"
        )
    if not evidence_supports_date_value(timing, tuple(refs)):
        raise ValueError(
            "calendar timing value is not visible in official evidence"
        )
    if timing.kind == DateKind.CONFIRMED_RECOMPETE and not any(
        record.has_assertion(EvidenceAssertion.EXPLICIT_RECOMPETE)
        for record in refs
    ):
        raise ValueError("confirmed recompete lacks exact official evidence")
    if timing.kind == DateKind.AWARD_END_RESEARCH_CLOCK and not any(
        record.has_assertion(EvidenceAssertion.AWARD_PERIOD_END)
        for record in refs
    ):
        raise ValueError("award-end research clock lacks exact award evidence")


def _group_event_seeds(
    seeds: tuple[EventSeed, ...],
) -> dict[str, list[EventSeed]]:
    parents = list(range(len(seeds)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = root(left)
        right_root = root(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    key_owner: dict[str, int] = {}
    for index, seed in enumerate(seeds):
        keys: list[str] = []
        if seed.timing is not None:
            keys.append(
                f"derived:{_normalize(seed.organizer or seed.agency or '')}:"
                f"{_normalize(seed.title)}:{_timing_edition_key(seed.timing)}:"
                f"{_normalize(seed.location or '')}:{seed.virtual}"
            )
        if seed.external_event_id:
            keys.append(
                f"external:{_normalize(seed.organizer or seed.agency or '')}:"
                f"{_normalize(seed.external_event_id)}"
            )
        if not keys:
            raise ValueError(
                "date-TBD event requires an explicit external event identity"
            )
        for key in keys:
            owner = key_owner.setdefault(key, index)
            union(index, owner)

    components: dict[int, list[EventSeed]] = defaultdict(list)
    for index, seed in enumerate(seeds):
        components[root(index)].append(seed)
    return {
        f"component:{index:08d}": rows
        for index, rows in sorted(components.items())
    }


def _merge_event_seed_group(
    rows: list[EventSeed],
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    evidence_by_id: dict[str, EvidenceRecord],
    query_specs: dict[str, EventQuerySpec],
    trusted_organizer_domains: tuple[str, ...],
) -> tuple[EventRecord, tuple[CalendarEvidenceAttribution, ...]]:
    _require_single_value((row.kind.value for row in rows), "event kind")
    _require_single_value((str(row.virtual) for row in rows), "event venue mode")
    _require_single_value(
        (row.lifecycle_status.value for row in rows),
        "event lifecycle status",
    )
    representative = sorted(
        rows,
        key=lambda item: json.dumps(
            item.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ),
    )[0]
    event_id = "event-" + _short_digest(sorted(_event_seed_edition_key(row)
                                                for row in rows)[0])
    timing = _merge_event_timing(rows, event_id)
    evidence_ids = _merged_ids(rows, "evidence_ids")
    title = _merge_text_field(rows, "title", required=True)
    relevance = _merge_text_field(rows, "relevance", required=True)
    validate_next = _merge_text_field(rows, "validate_next", required=True)
    last_checked_at = max(row.last_checked_at for row in rows)
    if last_checked_at > as_of:
        raise ValueError("event verification postdates calendar as-of")

    event = EventRecord(
        event_id=event_id,
        client_id=binding.client_id,
        run_id=binding.run_id,
        scope_sha256=binding.scope_sha256,
        title=title,
        kind=representative.kind,
        timing=timing,
        organizer=_merge_text_field(rows, "organizer"),
        agency=_merge_text_field(rows, "agency"),
        location=_merge_text_field(rows, "location"),
        virtual=representative.virtual,
        audience=_merge_text_field(rows, "audience"),
        relevance=relevance,
        validate_next=validate_next,
        registration_url=_merge_url_field(rows, "registration_url"),
        agenda_url=_merge_url_field(rows, "agenda_url"),
        venue_url=_merge_url_field(rows, "venue_url"),
        evidence_ids=evidence_ids,
        registration_evidence_ids=_merged_ids(
            rows, "registration_evidence_ids"),
        agenda_evidence_ids=_merged_ids(rows, "agenda_evidence_ids"),
        venue_evidence_ids=_merged_ids(rows, "venue_evidence_ids"),
        attending_agencies=tuple(sorted({
            value for row in rows for value in row.attending_agencies
        }, key=str.casefold)),
        attendance_evidence_ids=_merged_ids(
            rows, "attendance_evidence_ids"),
        flagship=any(row.flagship for row in rows),
        flagship_evidence_ids=_merged_ids(rows, "flagship_evidence_ids"),
        lifecycle_status=representative.lifecycle_status,
        last_checked_at=last_checked_at,
        active=(representative.lifecycle_status
                == EventLifecycleStatus.SCHEDULED),
    )
    _validate_event_semantics(
        event,
        evidence_by_id=evidence_by_id,
        trusted_organizer_domains=trusted_organizer_domains,
    )
    validate_event_watch_freshness(event, as_of=as_of)
    attributions = tuple(sorted(
        (
            CalendarEvidenceAttribution(
                event_id=event.event_id,
                query_id=row.query_ids[0],
                lead_id=row.discovery_lead_ids[0],
                evidence_id=_seed_acceptance_evidence(
                    row,
                    query_specs[row.query_ids[0]],
                    evidence_by_id,
                    trusted_organizer_domains,
                ),
            )
            for row in rows
            if row.query_ids
        ),
        key=lambda item: (
            item.query_id,
            item.lead_id,
            item.evidence_id,
        ),
    ))
    return event, attributions


def _seed_acceptance_evidence(
    seed: EventSeed,
    query: EventQuerySpec,
    evidence_by_id: dict[str, EvidenceRecord],
    trusted_organizer_domains: tuple[str, ...],
) -> str:
    if query.source_class == EventSourceClass.OFFICIAL_AGENCY:
        candidate_ids = seed.evidence_ids
        allowed_kinds = {
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
            EvidenceKind.OFFICIAL_EVENT,
        }
        host_policy = "agency"
    elif query.source_class == EventSourceClass.OFFICIAL_ORGANIZER:
        candidate_ids = seed.evidence_ids
        allowed_kinds = {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        host_policy = "organizer"
    elif query.source_class == EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE:
        candidate_ids = tuple(dict.fromkeys((
            *seed.agenda_evidence_ids,
            *seed.attendance_evidence_ids,
        )))
        allowed_kinds = {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        host_policy = "organizer"
    elif query.source_class == \
            EventSourceClass.OFFICIAL_REGISTRATION_VENUE:
        candidate_ids = tuple(dict.fromkeys((
            *seed.registration_evidence_ids,
            *seed.venue_evidence_ids,
        )))
        allowed_kinds = {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        host_policy = "organizer"
    else:
        candidate_ids = seed.evidence_ids
        allowed_kinds = {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        host_policy = "agency_or_organizer"

    accepted: list[str] = []
    for evidence_id in candidate_ids:
        record = evidence_by_id.get(evidence_id)
        if record is None \
                or not record.official_source \
                or not record.primary_source \
                or record.source_kind not in allowed_kinds:
            continue
        host = (urlparse(str(record.source_url)).hostname or "").casefold()
        if host_policy == "agency":
            if not (host.endswith(".gov") or host.endswith(".mil")):
                continue
        elif host_policy == "organizer" \
                and not _authoritative_host(
                    host, trusted_organizer_domains):
            continue
        elif host_policy == "agency_or_organizer" and not (
            host.endswith(".gov")
            or host.endswith(".mil")
            or _authoritative_host(host, trusted_organizer_domains)
        ):
            continue
        if query.source_class in (
            EventSourceClass.OFFICIAL_AGENCY,
            EventSourceClass.OFFICIAL_ORGANIZER,
            EventSourceClass.DISCOVERY_INDEX,
        ) and not _record_supports_event_identity(
            record,
            title=seed.title,
            timing=seed.timing,
        ):
            continue
        if query.source_class == \
                EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE \
                and not _record_supports_agenda_or_attendance(
                    record,
                    record_id=evidence_id,
                    title=seed.title,
                    agenda_url=seed.agenda_url,
                    agenda_evidence_ids=seed.agenda_evidence_ids,
                    attending_agencies=seed.attending_agencies,
                    attendance_evidence_ids=seed.attendance_evidence_ids,
                ):
            continue
        if query.source_class == \
                EventSourceClass.OFFICIAL_REGISTRATION_VENUE \
                and not _record_supports_registration_or_venue(
                    record,
                    record_id=evidence_id,
                    title=seed.title,
                    registration_url=seed.registration_url,
                    registration_evidence_ids=(
                        seed.registration_evidence_ids
                    ),
                    venue_url=seed.venue_url,
                    venue_evidence_ids=seed.venue_evidence_ids,
                ):
            continue
        accepted.append(evidence_id)
    if not accepted:
        raise ValueError(
            "verified event evidence does not match its discovery source class"
        )
    return sorted(accepted)[0]


def _attribution_matches_source_class(
    attribution: CalendarEvidenceAttribution,
    *,
    event: EventRecord,
    query: EventQuerySpec,
    evidence: EvidenceRecord,
    trusted_organizer_domains: tuple[str, ...],
) -> bool:
    if not evidence.official_source or not evidence.primary_source:
        return False
    host = (urlparse(str(evidence.source_url)).hostname or "").casefold()
    if query.source_class == EventSourceClass.OFFICIAL_AGENCY:
        return (
            attribution.evidence_id in event.evidence_ids
            and evidence.source_kind in {
                EvidenceKind.NOTICE,
                EvidenceKind.AGENCY_ANNOUNCEMENT,
                EvidenceKind.OFFICIAL_EVENT,
            }
            and (host.endswith(".gov") or host.endswith(".mil"))
            and _record_supports_event_identity(
                evidence,
                title=event.title,
                timing=event.timing,
            )
        )
    if query.source_class == EventSourceClass.OFFICIAL_ORGANIZER:
        return (
            attribution.evidence_id in event.evidence_ids
            and evidence.source_kind in {
                EvidenceKind.OFFICIAL_EVENT,
                EvidenceKind.ORGANIZER_EVENT,
                EvidenceKind.AGENCY_ANNOUNCEMENT,
            }
            and _authoritative_host(host, trusted_organizer_domains)
            and _record_supports_event_identity(
                evidence,
                title=event.title,
                timing=event.timing,
            )
        )
    if query.source_class == EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE:
        return (
            attribution.evidence_id in {
                *event.agenda_evidence_ids,
                *event.attendance_evidence_ids,
            }
            and evidence.source_kind in {
                EvidenceKind.OFFICIAL_EVENT,
                EvidenceKind.ORGANIZER_EVENT,
                EvidenceKind.AGENCY_ANNOUNCEMENT,
            }
            and _authoritative_host(host, trusted_organizer_domains)
            and _record_supports_agenda_or_attendance(
                evidence,
                record_id=attribution.evidence_id,
                title=event.title,
                agenda_url=event.agenda_url,
                agenda_evidence_ids=event.agenda_evidence_ids,
                attending_agencies=event.attending_agencies,
                attendance_evidence_ids=event.attendance_evidence_ids,
            )
        )
    if query.source_class == EventSourceClass.DISCOVERY_INDEX:
        return (
            attribution.evidence_id in event.evidence_ids
            and evidence.source_kind in {
                EvidenceKind.OFFICIAL_EVENT,
                EvidenceKind.ORGANIZER_EVENT,
                EvidenceKind.AGENCY_ANNOUNCEMENT,
            }
            and (
                host.endswith(".gov")
                or host.endswith(".mil")
                or _authoritative_host(host, trusted_organizer_domains)
            )
            and _record_supports_event_identity(
                evidence,
                title=event.title,
                timing=event.timing,
            )
        )
    return (
        attribution.evidence_id in {
            *event.registration_evidence_ids,
            *event.venue_evidence_ids,
        }
        and evidence.source_kind in {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        and _authoritative_host(host, trusted_organizer_domains)
        and _record_supports_registration_or_venue(
            evidence,
            record_id=attribution.evidence_id,
            title=event.title,
            registration_url=event.registration_url,
            registration_evidence_ids=event.registration_evidence_ids,
            venue_url=event.venue_url,
            venue_evidence_ids=event.venue_evidence_ids,
        )
    )


def _record_supports_event_identity(
    record: EvidenceRecord,
    *,
    title: str,
    timing: Optional[DateValue],
) -> bool:
    """Require the attributed page itself to prove this event edition."""

    if not record.has_assertion(EvidenceAssertion.OFFICIAL_EVENT):
        return False
    text = _normalize(" ".join((record.title, record.excerpt)))
    if _normalize(title) not in text:
        return False
    return timing is None or evidence_supports_date_value(timing, (record,))


def _record_supports_agenda_or_attendance(
    record: EvidenceRecord,
    *,
    record_id: str,
    title: str,
    agenda_url: Optional[HttpUrl],
    agenda_evidence_ids: tuple[str, ...],
    attending_agencies: tuple[str, ...],
    attendance_evidence_ids: tuple[str, ...],
) -> bool:
    source_url = str(record.source_url).rstrip("/")
    agenda_match = (
        record_id in agenda_evidence_ids
        and agenda_url is not None
        and source_url == str(agenda_url).rstrip("/")
        and _normalize(title) in _normalize(
            f"{record.title} {record.excerpt}"
        )
    )
    attendance_match = (
        record_id in attendance_evidence_ids
        and any(
            span.assertion == EvidenceAssertion.AGENCY_ATTENDANCE
            and any(
                _normalize(agency) in _normalize(span.quote)
                for agency in attending_agencies
            )
            for span in record.assertion_spans
        )
    )
    return agenda_match or attendance_match


def _record_supports_registration_or_venue(
    record: EvidenceRecord,
    *,
    record_id: str,
    title: str,
    registration_url: Optional[HttpUrl],
    registration_evidence_ids: tuple[str, ...],
    venue_url: Optional[HttpUrl],
    venue_evidence_ids: tuple[str, ...],
) -> bool:
    source_url = str(record.source_url).rstrip("/")
    title_visible = _normalize(title) in _normalize(
        f"{record.title} {record.excerpt}"
    )
    registration_match = (
        record_id in registration_evidence_ids
        and registration_url is not None
        and source_url == str(registration_url).rstrip("/")
    )
    venue_match = (
        record_id in venue_evidence_ids
        and venue_url is not None
        and source_url == str(venue_url).rstrip("/")
    )
    return title_visible and (registration_match or venue_match)


def _event_seed_edition_key(seed: EventSeed) -> str:
    if seed.timing is not None:
        return (
            f"{_normalize(seed.organizer or seed.agency or '')}:"
            f"{_normalize(seed.title)}:{_timing_edition_key(seed.timing)}:"
            f"{_normalize(seed.location or '')}:{seed.virtual}"
        )
    if seed.external_event_id:
        return (
            f"{_normalize(seed.organizer or seed.agency or '')}:"
            f"{_normalize(seed.external_event_id)}"
        )
    raise ValueError("event edition identity cannot be derived without timing")


def _timing_edition_key(timing: DateValue) -> str:
    end = timing.end or timing.start or timing.sort_date
    return ":".join((
        timing.sort_date.isoformat(),
        end.isoformat(),
        timing.sort_datetime.isoformat()
        if timing.sort_datetime is not None else "",
    ))


def _require_single_value(values: Iterable[str], label: str) -> None:
    if len(set(values)) != 1:
        raise ValueError(
            f"conflicting observations share an event edition {label}"
        )


def _merge_text_field(
    rows: list[EventSeed],
    field: str,
    *,
    required: bool = False,
) -> Optional[str]:
    values: dict[str, str] = {}
    for row in rows:
        value = getattr(row, field)
        if value:
            values.setdefault(_normalize(value), value)
    if len(values) > 1:
        raise ValueError(
            f"conflicting observations share an event edition {field}"
        )
    if not values:
        if required:
            raise ValueError(f"event edition requires {field}")
        return None
    return sorted(values.values(), key=lambda value: (value.casefold(), value))[0]


def _merge_url_field(
    rows: list[EventSeed],
    field: str,
) -> Optional[HttpUrl]:
    values = {
        str(value).rstrip("/"): value
        for row in rows
        if (value := getattr(row, field)) is not None
    }
    if len(values) > 1:
        raise ValueError(
            f"conflicting observations share an event edition {field}"
        )
    return next(iter(values.values()), None)


def _merge_event_timing(
    rows: list[EventSeed],
    event_id: str,
) -> Optional[DateValue]:
    present = [row.timing for row in rows if row.timing is not None]
    if not present:
        return None
    if len(present) != len(rows):
        raise ValueError("event observations conflict on whether a date exists")
    payloads = []
    for timing in present:
        payload = timing.model_dump(mode="json")
        for field in ("date_id", "evidence_ids", "label", "source_text"):
            payload.pop(field, None)
        payloads.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if len(set(payloads)) != 1:
        raise ValueError(
            "conflicting observations share an event edition date"
        )
    representative = sorted(
        present,
        key=lambda timing: json.dumps(
            timing.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )[0]
    evidence_ids = tuple(sorted({
        evidence_id for timing in present for evidence_id in timing.evidence_ids
    }))
    return DateValue.model_validate({
        **representative.model_dump(mode="python"),
        "date_id": "date-event-" + _short_digest(event_id),
        "evidence_ids": evidence_ids,
    })


def _merged_ids(rows: list[EventSeed], field: str) -> tuple[str, ...]:
    return tuple(sorted({
        evidence_id for row in rows for evidence_id in getattr(row, field)
    }))


def _validate_event_semantics(
    event: EventRecord,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    trusted_organizer_domains: tuple[str, ...],
) -> None:
    refs = _records(event.all_evidence_ids, evidence_by_id)
    if event.last_checked_at < max(ref.retrieved_at for ref in refs):
        raise ValueError("event last-checked time predates accepted evidence")
    if any(not ref.official_source or not ref.primary_source for ref in refs):
        raise ValueError(
            "discovery or aggregator evidence cannot be authoritative event "
            "evidence"
        )
    for ref in refs:
        host = (urlparse(str(ref.source_url)).hostname or "").casefold()
        if not _authoritative_host(host, trusted_organizer_domains):
            raise ValueError(
                "event evidence is not tied to an approved authority domain"
            )

    anchors = _records(event.evidence_ids, evidence_by_id)

    if event.kind in (EventKind.CONFERENCE, EventKind.INDUSTRY_EVENT):
        official_event_refs = [
            ref for ref in anchors
            if ref.source_kind in (
                EvidenceKind.OFFICIAL_EVENT,
                EvidenceKind.ORGANIZER_EVENT,
                EvidenceKind.AGENCY_ANNOUNCEMENT,
                EvidenceKind.NOTICE,
            )
            and ref.official_source
            and ref.primary_source
            and ref.has_assertion(EvidenceAssertion.OFFICIAL_EVENT)
        ]
        if not official_event_refs:
            raise ValueError("event lacks an exact official event assertion")
        if any(
            not _record_supports_event_identity(
                ref,
                title=event.title,
                timing=event.timing,
            )
            for ref in anchors
        ):
            raise ValueError(
                "event anchor evidence does not prove this event edition"
            )
        _require_visible_claim(event.title, official_event_refs, "event title")
        _require_visible_claim(
            event.organizer or "", official_event_refs, "event organizer")
        _require_visible_claim(
            event.location or "", official_event_refs, "event location")
        if event.timing is not None:
            timing_refs = _records(event.timing.evidence_ids, evidence_by_id)
            if not evidence_supports_date_value(
                event.timing,
                tuple(timing_refs),
            ):
                raise ValueError(
                    "event date is not visible in authoritative evidence"
                )

    _validate_role_link(
        event.registration_url,
        event.registration_evidence_ids,
        evidence_by_id,
        "registration",
    )
    _validate_role_link(
        event.agenda_url,
        event.agenda_evidence_ids,
        evidence_by_id,
        "agenda",
    )
    _validate_role_link(
        event.venue_url,
        event.venue_evidence_ids,
        evidence_by_id,
        "venue",
    )

    visible_copy = " ".join(filter(None, (
        event.title, event.agency, event.audience, event.relevance,
    )))
    if _ATTENDANCE_CLAIM.search(visible_copy) \
            and not event.attending_agencies:
        raise ValueError("event copy implies unsupported agency attendance")
    if event.attending_agencies:
        attendance_refs = _records(
            event.attendance_evidence_ids, evidence_by_id)
        for agency in event.attending_agencies:
            if not any(
                ref.official_source
                and ref.primary_source
                and any(
                    span.assertion == EvidenceAssertion.AGENCY_ATTENDANCE
                    and _normalize(agency) in _normalize(span.quote)
                    for span in ref.assertion_spans
                )
                for ref in attendance_refs
            ):
                raise ValueError(
                    "agency attendance lacks a matching official assertion"
                )
    if event.flagship:
        if not any(
            ref.official_source
            and ref.primary_source
            and ref.has_assertion(EvidenceAssertion.EVENT_FLAGSHIP)
            for ref in _records(event.flagship_evidence_ids, evidence_by_id)
        ):
            raise ValueError("flagship horizon lacks exact official evidence")
    status_assertion = {
        EventLifecycleStatus.CANCELLED: EvidenceAssertion.EVENT_CANCELLED,
        EventLifecycleStatus.POSTPONED: EvidenceAssertion.EVENT_POSTPONED,
        EventLifecycleStatus.DATE_TBD: EvidenceAssertion.EVENT_DATE_TBD,
    }.get(event.lifecycle_status)
    if status_assertion is not None and not any(
        ref.official_source
        and ref.primary_source
        and ref.has_assertion(status_assertion)
        for ref in anchors
    ):
        raise ValueError("inactive event status lacks exact official evidence")


def _validate_role_link(
    url: Optional[HttpUrl],
    evidence_ids: tuple[str, ...],
    evidence_by_id: dict[str, EvidenceRecord],
    label: str,
) -> None:
    if url is None:
        return
    normalized = str(url).rstrip("/")
    refs = _records(evidence_ids, evidence_by_id)
    if not any(
        ref.official_source
        and ref.primary_source
        and str(ref.source_url).rstrip("/") == normalized
        for ref in refs
    ):
        raise ValueError(
            f"event {label} link lacks matching authoritative evidence"
        )


def _records(
    evidence_ids: Iterable[str],
    evidence_by_id: dict[str, EvidenceRecord],
) -> list[EvidenceRecord]:
    refs = []
    for evidence_id in evidence_ids:
        record = evidence_by_id.get(evidence_id)
        if record is None:
            raise ValueError(f"event evidence is unresolved: {evidence_id}")
        refs.append(record)
    return refs


def _require_visible_claim(
    value: str,
    records: Iterable[EvidenceRecord],
    label: str,
) -> None:
    claimed = _normalize(value)
    if not claimed:
        raise ValueError(f"{label} cannot be blank")
    haystack = _normalize(" ".join(
        f"{record.source_name} {record.title} {record.excerpt}"
        for record in records
    ))
    if claimed not in haystack:
        raise ValueError(f"{label} is not visible in authoritative evidence")


def _authoritative_host(
    host: str,
    trusted_organizer_domains: tuple[str, ...],
) -> bool:
    if host.endswith(".gov") or host.endswith(".mil"):
        return True
    return host in trusted_organizer_domains


def _normalize_domain(value: str) -> str:
    return normalize_trusted_organizer_domain(value)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _normalize_past_event(event: EventRecord, as_of: date) -> EventRecord:
    if event.timing is None or not event.active:
        return event
    if _timing_end(event.timing) >= as_of:
        return event
    return EventRecord.model_validate({
        **event.model_dump(mode="python"),
        "lifecycle_status": EventLifecycleStatus.COMPLETED,
        "active": False,
    })


def _procurement_disposition(
    timing: DateValue,
    as_of: date,
) -> tuple[CalendarDisposition, str]:
    if _timing_end(timing) < as_of:
        return (
            CalendarDisposition.HISTORICAL,
            "The procurement milestone is before the report as-of date.",
        )
    if timing.sort_date > add_calendar_months(as_of, 24):
        return (
            CalendarDisposition.OUT_OF_HORIZON,
            "The procurement milestone is beyond the 24-month research window.",
        )
    return CalendarDisposition.ACTIVE, "Inside the procurement calendar window."


def _event_disposition(
    event: EventRecord,
    as_of: date,
) -> tuple[CalendarDisposition, str]:
    if event.timing is None or not event.active:
        return CalendarDisposition.HISTORICAL, "The event is not active and dated."
    if _timing_end(event.timing) < as_of:
        return CalendarDisposition.HISTORICAL, "The event is before the as-of date."
    months = 18 if event.flagship else (
        24 if event.kind in (
            EventKind.PROCUREMENT_MILESTONE,
            EventKind.BUDGET_MILESTONE,
        ) else 12
    )
    if event.timing.sort_date > add_calendar_months(as_of, months):
        return (
            CalendarDisposition.OUT_OF_HORIZON,
            f"The event begins beyond the {months}-month verified window.",
        )
    return CalendarDisposition.ACTIVE, "Inside the verified event window."


def _timing_end(timing: DateValue) -> date:
    if timing.end is not None:
        return timing.end
    if timing.start is not None:
        return timing.start
    if timing.precision == DatePrecision.MONTH:
        return add_calendar_months(timing.sort_date, 1) - timedelta(days=1)
    if timing.precision == DatePrecision.QUARTER:
        return add_calendar_months(timing.sort_date, 3) - timedelta(days=1)
    if timing.precision == DatePrecision.FISCAL_YEAR:
        return add_calendar_months(timing.sort_date, 12) - timedelta(days=1)
    return timing.sort_date


def _calendar_sort_key(item: CalendarItem) -> tuple[str, str, str]:
    instant = item.timing.sort_datetime.isoformat() \
        if item.timing.sort_datetime is not None else ""
    return item.timing.sort_date.isoformat(), instant, item.calendar_id


def _bounded_calendar_projection(
    ordered_items: Iterable[CalendarItem],
    *,
    maximum_items: int,
) -> tuple[CalendarItem, ...]:
    """Keep procurement capacity while bounding researched event rows."""

    selected: list[CalendarItem] = []
    researched_event_count = 0
    for item in ordered_items:
        if len(selected) >= maximum_items:
            break
        if item.origin == CalendarItemOrigin.VERIFIED_EVENT:
            if researched_event_count >= MAX_VISIBLE_RESEARCHED_EVENTS:
                continue
            researched_event_count += 1
        selected.append(item)
    return tuple(selected)


def _event_sort_key(event: EventRecord) -> tuple[str, str, str]:
    if event.timing is None:
        return date.max.isoformat(), "", event.event_id
    instant = event.timing.sort_datetime.isoformat() \
        if event.timing.sort_datetime is not None else ""
    return event.timing.sort_date.isoformat(), instant, event.event_id


def _build_coverage_receipt(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    manifest: EventQueryManifest,
    attempts: tuple[EventQueryAttempt, ...],
    accepted_by_query: dict[str, set[str]],
) -> CalendarCoverageReceipt:
    attempts_by_id = {item.query_id: item for item in attempts}
    if len(attempts_by_id) != len(attempts):
        raise ValueError("event query attempts must be unique")
    expected_ids = {item.query_id for item in manifest.queries}
    if set(attempts_by_id) - expected_ids:
        raise ValueError("event query attempt is absent from the manifest")
    rows: list[CoverageRecord] = []
    for query in manifest.queries:
        attempt = attempts_by_id.get(query.query_id)
        accepted_ids = tuple(sorted(accepted_by_query.get(query.query_id, set())))
        if not query.required:
            expected_state = CoverageState.SCOPE_EXCLUDED
            if attempt is not None and attempt.state != expected_state:
                raise ValueError("inactive event query was unexpectedly attempted")
            state = expected_state
            attempted_at = attempt.attempted_at if attempt is not None else None
            returned = attempt.records_returned if attempt is not None else 0
            if attempt is not None:
                detail = attempt.public_detail
            elif query.nonrequired_reason == "scope_excluded":
                detail = (
                    "This standing watch target is outside the approved "
                    "client frame."
                )
            else:
                detail = (
                    "No approved terms were present for this query family."
                )
        elif attempt is None:
            state = CoverageState.NOT_RUN
            attempted_at = None
            returned = 0
            detail = "Required event-research lane was not run."
        else:
            state = attempt.state
            attempted_at = attempt.attempted_at
            returned = attempt.records_returned
            detail = attempt.public_detail
            if attempted_at is not None and attempted_at > as_of:
                raise ValueError("event query attempt postdates calendar as-of")
        if state == CoverageState.FAILED and accepted_ids:
            raise ValueError("failed event query cannot produce accepted evidence")
        if len(accepted_ids) > returned:
            raise ValueError(
                "accepted event evidence exceeds the query result census"
            )
        rows.append(CoverageRecord(
            client_id=binding.client_id,
            run_id=binding.run_id,
            scope_sha256=binding.scope_sha256,
            source=query.source_class.value,
            query_family=query.query_family.value,
            query_required=query.required,
            state=state,
            window_start=manifest.window_start,
            window_end=manifest.flagship_event_through,
            attempted_at=attempted_at,
            records_returned=returned,
            records_accepted=len(accepted_ids),
            accepted_evidence_ids=accepted_ids,
            query_manifest_id=manifest.manifest_id,
            query_id=query.query_id,
            watch_target_id=query.watch_target_id,
            public_detail=detail,
        ))
    incomplete = {
        CoverageState.PARTIAL,
        CoverageState.PARTIAL_FALLBACK,
        CoverageState.STALE_SNAPSHOT,
        CoverageState.FAILED,
        CoverageState.NOT_RUN,
        CoverageState.SCOPE_EXCLUDED,
        CoverageState.NOT_USED,
    }
    comprehensive = not any(
        row.state in incomplete
        for query, row in zip(manifest.queries, rows)
        if query.required
    )
    required_count = sum(1 for query in manifest.queries if query.required)
    completed_count = sum(
        1 for query, row in zip(manifest.queries, rows)
        if query.required and row.state not in incomplete
    )
    public_summary = (
        "Event research query/source census complete: "
        f"{completed_count} of {required_count} required lanes attempted."
        if comprehensive else
        "Event research query/source census incomplete: "
        f"{completed_count} of {required_count} required lanes completed; "
        "failed, partial, stale, or not-run lanes remain visible internally."
    )
    return CalendarCoverageReceipt(
        binding=binding,
        as_of=as_of,
        query_manifest_id=manifest.manifest_id,
        window_start=manifest.window_start,
        confirmed_event_through=manifest.confirmed_event_through,
        flagship_event_through=manifest.flagship_event_through,
        procurement_through=add_calendar_months(manifest.window_start, 24),
        rows=tuple(rows),
        required_row_keys=tuple(
            row.identity_key for row in rows if row.query_required
        ),
        accepted_evidence_ids=tuple(sorted({
            evidence_id for row in rows
            for evidence_id in row.accepted_evidence_ids
        })),
        comprehensive=comprehensive,
        public_summary=public_summary,
    )


def _short_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


__all__ = (
    "MAX_VISIBLE_RESEARCHED_EVENTS",
    "CalendarBuildResult",
    "CalendarCandidateDateOwner",
    "CalendarVehicleDateOwner",
    "CalendarCoverageReceipt",
    "CalendarDisposition",
    "CalendarEvidenceAttribution",
    "CalendarItem",
    "CalendarItemOrigin",
    "DeferredCalendarItem",
    "EventSeed",
    "add_calendar_months",
    "build_calendar",
    "project_document_calendar",
)
