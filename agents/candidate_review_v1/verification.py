"""Verification lane for Candidate Review v1 (Chunk 6, Passes A-C).

Turns discovery leads (EventLead / VehicleLead) into official, primary
``EvidenceRecord`` values and the derived event seeds / vehicle signals that
downstream engines require.  It is PURE and deterministic: it reads no clock
and performs no network of its own.  Authoritative fetches are supplied by two
injected callables; ``None`` means that lane was not run, and every lead of
that type becomes a typed ``VerificationDrop`` (never a fabricated record, never
a silent zero-result).

Implemented lanes: the default (no-fetcher) path, event fetch-and-mint
(Pass A), vehicle evidence fetch-and-mint (Pass B), vehicle SIGNAL minting
with exact identity/lineage assertion spans (Pass C1), and typed vehicle
WATCH-RECORD minting from labeled official program pages (Pass C2, GSA
lane).  Every fact enters a watch record only with its typed assertion span
quoted from the record; stale snapshots claiming current status and every
other non-mintable signal or watch record are typed drops, never silent
zeros.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Mapping, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from agents.candidate_review_v1.calendar_engine import EventSeed
from agents.candidate_review_v1.contracts import (
    CURRENT_WATCH_MAX_AGE,
    NEAR_TERM_WATCH_DAYS,
    NEAR_TERM_WATCH_MAX_AGE,
    ArtifactBinding,
    DateKind,
    DatePrecision,
    DateStatus,
    DateValue,
    EventKind,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
    SourceTier,
    VehicleAccessPosture,
    VehicleClass,
    VehicleIdentity,
    VehicleOnRampStatus,
    VehicleOrderingStatus,
    VehicleRelationship,
    VehicleRelationshipKind,
    VehicleSignal,
    VehicleSignalKind,
    VehicleSignalStatus,
    VehicleWatchRecord,
)
from agents.candidate_review_v1.event_research import EventDiscoveryResult, EventLead
from agents.candidate_review_v1.vehicle_watch import (
    VehicleCollectionResult,
    VehicleLead,
    VehicleWatchLane,
)

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_INDEX = {name: index + 1 for index, name in enumerate(_MONTHS)}
_DATE_RE = re.compile(
    r"(" + "|".join(m.capitalize() for m in _MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})")
_PERIOD_END_RE = re.compile(
    r"(?:the )?(?:period of performance|ordering period|award period)[^.]*?"
    r"(?:ends?|through)[^.]*?"
    r"(?:" + "|".join(m.capitalize() for m in _MONTHS) + r")\s+\d{1,2},\s+\d{4}",
    re.I)

_AWARD_LANES = {
    VehicleWatchLane.USASPENDING_IDV,
    VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
}
_CANDIDATE_NOTICE_ROLES = {
    NoticeRole.END_USER_REQUIREMENT,
    NoticeRole.TASK_ORDER_REQUIREMENT,
}
_VEHICLE_NOTICE_SIGNAL_KINDS = {
    NoticeRole.VEHICLE_ON_RAMP: VehicleSignalKind.ON_RAMP,
    NoticeRole.VEHICLE_ESTABLISHMENT: VehicleSignalKind.VEHICLE_ESTABLISHMENT,
}
_AGENCY_LINE_RE = re.compile(r"Agency:\s*([^.|]+)", re.I)
_VEHICLE_LINE_RE = re.compile(r"Vehicle:\s*([^.|]+)", re.I)
_MANAGING_AGENCY_RE = re.compile(r"Managing agency:\s*([^.|]+)", re.I)
_VEHICLE_CLASS_RE = re.compile(r"Vehicle class:\s*([^.|]+)", re.I)
_SCOPE_LINE_RE = re.compile(r"Scope:\s*([^.|]+)", re.I)
_ORDERING_STATUS_RE = re.compile(r"Ordering status:\s*([^.|]+)", re.I)
_ON_RAMP_LINE_RE = re.compile(r"On-ramp:\s*([^.|]+(?:\.[^.|]*\d{4})?)", re.I)
_ORDERING_PERIOD_RE = re.compile(
    r"Ordering period ends[^.]*\d{4}", re.I)
_SOURCE_DATA_RE = re.compile(r"Source data as of[^.]*\d{4}", re.I)
_VEHICLE_CLASS_MAP = (
    ("gwac", VehicleClass.GWAC),
    ("multiple-award idiq", VehicleClass.MULTIPLE_AWARD_IDIQ),
    ("multiple award idiq", VehicleClass.MULTIPLE_AWARD_IDIQ),
    ("single-award idiq", VehicleClass.SINGLE_AWARD_IDIQ),
    ("single award idiq", VehicleClass.SINGLE_AWARD_IDIQ),
    ("bpa", VehicleClass.BPA),
    ("schedule", VehicleClass.FEDERAL_SUPPLY_SCHEDULE),
    ("enterprise", VehicleClass.ENTERPRISE_VEHICLE),
)

VERIFICATION_SCHEMA_VERSION = "candidate_review_v1.verification.v1"


class _FetchedContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class FetchedPage(_FetchedContract):
    """One authoritative page body fetched for an event lead."""

    lead_id: str = Field(min_length=1)
    final_url: HttpUrl
    fetched_at: datetime
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    etag: Optional[str] = None
    published_date: Optional[date] = None
    effective_date: Optional[date] = None
    record_sha256: str = Field(min_length=64, max_length=64)


class FetchedRecord(FetchedPage):
    """An authoritative vehicle/notice record; adds the notice block."""

    verified_at: Optional[datetime] = None
    notice_status: Optional[NoticeStatus] = None
    notice_role: Optional[NoticeRole] = None
    issuing_office: Optional[str] = None
    solicitation_number: Optional[str] = None


class EventPageFetcher(Protocol):
    def __call__(self, lead: EventLead) -> Optional[FetchedPage]:
        ...


class VehicleRecordFetcher(Protocol):
    def __call__(self, lead: VehicleLead) -> Optional[FetchedRecord]:
        ...


@dataclass(frozen=True)
class VerificationDrop:
    lane: str  # 'event' | 'vehicle'
    lead_id: str
    reason: str


@dataclass(frozen=True)
class AnchorOffer:
    """Precomputed anchor admissibility so no model decides the lifecycle."""

    evidence_id: str
    can_anchor: bool
    confirms_open_notice: bool
    notice_window_open: bool


@dataclass(frozen=True)
class VerificationResult:
    evidence: tuple[EvidenceRecord, ...] = ()
    event_seeds: tuple[EventSeed, ...] = ()
    vehicle_signals: tuple[VehicleSignal, ...] = ()
    vehicle_watch_records: tuple[VehicleWatchRecord, ...] = ()
    accepted_vehicle_evidence_by_query: Mapping[str, tuple[EvidenceRecord, ...]] = \
        field(default_factory=dict)
    anchor_offers: tuple[AnchorOffer, ...] = ()
    drops: tuple[VerificationDrop, ...] = ()


class VerificationLaneNotImplemented(NotImplementedError):
    """A fetcher was supplied but the fetch-and-mint pass is not built yet."""


def verify_research(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    event_result: EventDiscoveryResult,
    vehicle_result: VehicleCollectionResult,
    fetch_event: Optional[EventPageFetcher] = None,
    fetch_vehicle: Optional[VehicleRecordFetcher] = None,
    trusted_organizer_domains: tuple[str, ...] = (),
) -> VerificationResult:
    """Verify discovery leads into official evidence + derived seeds/signals.

    Default path (both fetchers ``None``): every lead is recorded as a typed
    drop and no evidence is minted.  Supplying a fetcher activates the
    fetch-and-mint path (next pass).
    """

    drops: list[VerificationDrop] = []
    memo: dict[str, EvidenceRecord] = {}
    evidence: list[EvidenceRecord] = []
    event_seeds: list[EventSeed] = []
    anchor_offers: list[AnchorOffer] = []
    vehicle_signals: list[VehicleSignal] = []
    signal_ids: set[str] = set()
    vehicle_watch_records: list[VehicleWatchRecord] = []
    watch_ids: set[str] = set()
    accepted_by_query: dict[str, list[EvidenceRecord]] = {}

    trusted = {value.casefold() for value in trusted_organizer_domains}
    for lead in event_result.leads:
        if fetch_event is None:
            drops.append(VerificationDrop("event", lead.lead_id, "event lane not run"))
            continue
        try:
            page = fetch_event(lead)
        except Exception as exc:  # noqa: BLE001 - a fetch failure is a drop
            drops.append(VerificationDrop("event", lead.lead_id, f"fetch failed: {type(exc).__name__}"))
            continue
        if page is None:
            drops.append(VerificationDrop("event", lead.lead_id, "no authoritative page"))
            continue
        try:
            record, seed = _mint_event(binding, as_of, lead, page, trusted, memo)
        except (ValueError, _MintSkip) as exc:
            drops.append(VerificationDrop("event", lead.lead_id, f"not verifiable: {str(exc)[:120]}"))
            continue
        if record.evidence_id not in {item.evidence_id for item in evidence}:
            evidence.append(record)
        event_seeds.append(seed)
    for lead in vehicle_result.leads:
        if fetch_vehicle is None:
            drops.append(VerificationDrop("vehicle", lead.lead_id, "vehicle lane not run"))
            continue
        try:
            fetched = fetch_vehicle(lead)
        except Exception as exc:  # noqa: BLE001 - a fetch failure is a drop, never a crash
            drops.append(VerificationDrop("vehicle", lead.lead_id, f"fetch failed: {type(exc).__name__}"))
            continue
        if fetched is None:
            drops.append(VerificationDrop("vehicle", lead.lead_id, "no authoritative record"))
            continue
        try:
            record = _mint_vehicle_evidence(binding, as_of, lead, fetched, memo)
        except (ValueError, _MintSkip) as exc:
            drops.append(VerificationDrop("vehicle", lead.lead_id, f"not verifiable: {str(exc)[:120]}"))
            continue
        if record.evidence_id not in {item.evidence_id for item in evidence}:
            evidence.append(record)
        anchor_offers.append(AnchorOffer(
            evidence_id=record.evidence_id,
            can_anchor=record.can_anchor_candidate,
            confirms_open_notice=record.confirms_open_notice,
            notice_window_open=_notice_window_open(record, as_of)))
        accepted_rows = accepted_by_query.setdefault(lead.query_id, [])
        if record.evidence_id not in {row.evidence_id for row in accepted_rows}:
            accepted_rows.append(record)
        signal, skip_reason = _mint_vehicle_signal(binding, as_of, lead, record)
        if signal is not None:
            if signal.signal_id not in signal_ids:
                signal_ids.add(signal.signal_id)
                vehicle_signals.append(signal)
        elif skip_reason is not None:
            drops.append(
                VerificationDrop("vehicle-signal", lead.lead_id, skip_reason))
        watch_record, watch_skip = _mint_vehicle_watch_record(
            binding, as_of, lead, record)
        if watch_record is not None:
            if watch_record.watch_record_id not in watch_ids:
                watch_ids.add(watch_record.watch_record_id)
                vehicle_watch_records.append(watch_record)
        elif watch_skip is not None:
            drops.append(
                VerificationDrop("vehicle-watch", lead.lead_id, watch_skip))

    return VerificationResult(
        evidence=tuple(evidence),
        event_seeds=tuple(event_seeds),
        vehicle_signals=tuple(vehicle_signals),
        vehicle_watch_records=tuple(vehicle_watch_records),
        accepted_vehicle_evidence_by_query={
            query_id: tuple(rows)
            for query_id, rows in accepted_by_query.items()},
        anchor_offers=tuple(anchor_offers),
        drops=tuple(drops))


_ORGANIZER_RE = re.compile(r"Organizer:\s*([^.|]+)", re.I)
_LOCATION_RE = re.compile(r"Location:\s*([^.|]+)", re.I)


def _first_date(text: str) -> Optional[tuple[str, date]]:
    for match in _DATE_RE.finditer(text):
        month = _MONTH_INDEX[match.group(1).casefold()]
        try:
            return match.group(0), date(int(match.group(3)), month, int(match.group(2)))
        except ValueError:
            continue
    return None


def _sentence_with(text: str, needle: str) -> str:
    idx = text.find(needle)
    if idx < 0:
        return needle
    start = max(text.rfind(".", 0, idx) + 1, 0)
    end = text.find(".", idx + len(needle))
    end = len(text) if end < 0 else end + 1
    return text[start:end].strip() or needle


def _canonical_path(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(str(url))
    return (parsed.path or "/").rstrip("/") or "/"


def _mint_event(
    binding: ArtifactBinding, as_of: datetime, lead: EventLead, page: "FetchedPage",
    trusted: set[str], memo: dict[str, EvidenceRecord],
) -> tuple[EvidenceRecord, EventSeed]:
    """Mint one official event evidence record plus its calendar seed.

    ``lead.claimed_*`` values are discovery hints only and never enter the
    evidence or the seed; every published fact is read from the fetched page.
    """

    if page.fetched_at > as_of:
        raise ValueError("fetched_at postdates as_of")
    host = _host(str(page.final_url))
    if host.endswith(".gov") or host.endswith(".mil"):
        kind = EvidenceKind.OFFICIAL_EVENT
    elif host in trusted:
        kind = EvidenceKind.ORGANIZER_EVENT
    else:
        raise _MintSkip(f"event host {host} is neither official nor a trusted organizer")

    text = " ".join(page.text.split())
    excerpt = text[:4000]
    found = _first_date(excerpt)
    if found is None:
        raise ValueError("no event date published on the page")
    date_text, day = found
    if day < as_of.date():
        raise _MintSkip("event edition already passed")
    quote = _sentence_with(excerpt, date_text)
    if quote not in excerpt or date_text not in quote:
        raise ValueError("event date is not inside a quotable published claim")
    organizer_match = _ORGANIZER_RE.search(excerpt)
    location_match = _LOCATION_RE.search(excerpt)
    if organizer_match is None or location_match is None:
        raise ValueError("page does not publish an organizer and location")
    organizer = organizer_match.group(1).strip()
    location = location_match.group(1).strip()

    identity = SourceIdentity(
        source_system=host, record_id=_canonical_path(str(page.final_url)))
    key = identity.canonical_key
    record = memo.get(key)
    if record is None:
        record = EvidenceRecord(
            evidence_id=f"ev:{_short_key(key)}", client_id=binding.client_id,
            run_id=binding.run_id, scope_sha256=binding.scope_sha256,
            source_identity=identity, source_tier=SourceTier.PROGRAM, source_kind=kind,
            source_name=host, source_url=page.final_url, retrieved_at=page.fetched_at,
            title=page.title, excerpt=excerpt, published_date=page.published_date,
            effective_date=page.effective_date, record_sha256=page.record_sha256,
            official_source=True, primary_source=True,
            supports=(EvidenceUse.EVENT, EvidenceUse.TIMING),
            assertion_spans=(EvidenceAssertionSpan(
                assertion=EvidenceAssertion.OFFICIAL_EVENT, quote=quote),))
        memo[key] = record

    timing = DateValue(
        date_id=f"{record.evidence_id}:event_start", label="Event start",
        kind=DateKind.EVENT_START, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text=date_text, sort_date=day, start=day,
        evidence_ids=(record.evidence_id,))
    seed = EventSeed(
        external_event_id=identity.record_id, title=page.title,
        kind=EventKind.CONFERENCE, timing=timing, organizer=organizer,
        location=location, audience="Federal program and acquisition leaders",
        relevance="The published edition is in range for the reviewed capability set.",
        validate_next="Confirm agenda, registration, and which buying offices attend.",
        evidence_ids=(record.evidence_id,), last_checked_at=page.fetched_at,
        query_ids=(lead.query_id,), discovery_lead_ids=(lead.lead_id,))
    return record, seed


class _MintSkip(Exception):
    """This lane does not mint anchor evidence (e.g. vehicle catalog rows)."""


def _notice_window_open(record: EvidenceRecord, as_of: datetime) -> bool:
    return (record.verified_at is not None
            and record.verified_at <= as_of
            and as_of - record.verified_at <= timedelta(hours=24))


def _period_end_quote(text: str) -> Optional[str]:
    match = _PERIOD_END_RE.search(text)
    return match.group(0) if match is not None else None


def _mint_vehicle_evidence(
    binding: ArtifactBinding, as_of: datetime, lead: VehicleLead,
    fetched: "FetchedRecord", memo: dict[str, EvidenceRecord],
) -> EvidenceRecord:
    """Mint one official EvidenceRecord for a vehicle/notice/award lead."""

    if fetched.fetched_at > as_of:
        raise ValueError("fetched_at postdates as_of")
    key = lead.source_identity.canonical_key
    if key in memo:
        return memo[key]

    lane = lead.lane
    excerpt = " ".join(fetched.text.split())[:3000] or fetched.title
    spans: list[EvidenceAssertionSpan] = []
    notice_kwargs: dict[str, object] = {}

    if lane == VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES:
        kind = EvidenceKind.NOTICE
        tier = SourceTier.NOTICE
        if fetched.notice_status is None or fetched.verified_at is None \
                or fetched.notice_role is None:
            raise ValueError("SAM notice record is missing its notice block")
        notice_kwargs = {
            "notice_status": fetched.notice_status,
            "notice_role": fetched.notice_role,
            "verified_at": fetched.verified_at,
            "issuing_office": fetched.issuing_office,
            "solicitation_number": fetched.solicitation_number,
        }
        supports = (EvidenceUse.REQUIREMENT, EvidenceUse.TIMING)
        if fetched.notice_role in _VEHICLE_NOTICE_SIGNAL_KINDS:
            name_match = _VEHICLE_LINE_RE.search(excerpt)
            if name_match is not None:
                spans.append(EvidenceAssertionSpan(
                    assertion=EvidenceAssertion.VEHICLE_IDENTITY,
                    quote=_sentence_with(excerpt, name_match.group(0))))
    elif lane in _AWARD_LANES:
        kind = EvidenceKind.AWARD
        tier = SourceTier.PROGRAM
        quote = _period_end_quote(excerpt)
        if quote is not None:
            spans.append(EvidenceAssertionSpan(
                assertion=EvidenceAssertion.AWARD_PERIOD_END, quote=quote))
        parent = lead.parent_idv_identity
        piid_verbatim = (
            _find_verbatim(excerpt, parent.record_id)
            if parent is not None else None)
        if piid_verbatim is not None:
            identity_quote = _sentence_with(excerpt, piid_verbatim)
            spans.append(EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_IDENTITY,
                quote=identity_quote))
            order = lead.order_identity
            if order is not None and _find_verbatim(
                    identity_quote, order.record_id) is not None:
                spans.append(EvidenceAssertionSpan(
                    assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                    quote=identity_quote))
        supports = (EvidenceUse.BUYER, EvidenceUse.FUNDING)
    elif lane == VehicleWatchLane.OFFICIAL_AGENCY_PROGRAM_FORECAST:
        kind = EvidenceKind.AGENCY_FORECAST
        tier = SourceTier.PROGRAM
        supports = (EvidenceUse.BUYER, EvidenceUse.TIMING)
    elif lane == VehicleWatchLane.GSA_PROGRAM_RECORDS:
        # Official vehicle program context: never an anchor, but the typed
        # Section 3 watch record and its milestone dates mint from it.
        kind = EvidenceKind.VEHICLE
        tier = SourceTier.PROGRAM
        supports = (EvidenceUse.ACCESS, EvidenceUse.TIMING)
        spans.extend(_vehicle_page_spans(excerpt))
    else:
        # Restricted export rows remain vehicle context, never minted here.
        raise _MintSkip(lane.value)

    record = EvidenceRecord(
        evidence_id=f"ev:{_short_key(key)}",
        client_id=binding.client_id, run_id=binding.run_id,
        scope_sha256=binding.scope_sha256,
        source_identity=lead.source_identity, source_tier=tier, source_kind=kind,
        source_name=_host(str(fetched.final_url)), source_url=fetched.final_url,
        retrieved_at=fetched.fetched_at, title=fetched.title, excerpt=excerpt,
        published_date=fetched.published_date, effective_date=fetched.effective_date,
        record_sha256=fetched.record_sha256, official_source=True, primary_source=True,
        supports=supports, assertion_spans=tuple(spans), **notice_kwargs)
    memo[key] = record
    return record


def _vehicle_page_spans(excerpt: str) -> list[EvidenceAssertionSpan]:
    """Typed assertion spans for every labeled vehicle-program fact published.

    The Phase 3 fetchers emit these labeled lines from structured official
    payloads; each published line becomes one exact quotable span, and a fact
    with no labeled line simply is not asserted (and cannot enter the watch
    record).
    """

    spans: list[EvidenceAssertionSpan] = []

    def add(assertion: EvidenceAssertion, needle: str) -> None:
        spans.append(EvidenceAssertionSpan(
            assertion=assertion, quote=_sentence_with(excerpt, needle)))

    name_match = _VEHICLE_LINE_RE.search(excerpt)
    if name_match is not None:
        add(EvidenceAssertion.VEHICLE_IDENTITY, name_match.group(0))
    class_match = _VEHICLE_CLASS_RE.search(excerpt)
    if class_match is not None:
        add(EvidenceAssertion.VEHICLE_CLASS, class_match.group(0))
    agency_match = (_MANAGING_AGENCY_RE.search(excerpt)
                    or _AGENCY_LINE_RE.search(excerpt))
    if agency_match is not None:
        add(EvidenceAssertion.VEHICLE_MANAGEMENT, agency_match.group(0))
    scope_match = _SCOPE_LINE_RE.search(excerpt)
    if scope_match is not None:
        add(EvidenceAssertion.VEHICLE_SCOPE, scope_match.group(0))
    status_match = _ORDERING_STATUS_RE.search(excerpt)
    if status_match is not None:
        add(EvidenceAssertion.VEHICLE_ORDERING_STATUS, status_match.group(0))
    on_ramp_match = _ON_RAMP_LINE_RE.search(excerpt)
    if on_ramp_match is not None:
        add(EvidenceAssertion.VEHICLE_ON_RAMP, on_ramp_match.group(0))
        on_ramp_sentence = _sentence_with(excerpt, on_ramp_match.group(0))
        if _first_date(on_ramp_sentence) is not None:
            spans.append(EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_ON_RAMP_CLOSE,
                quote=on_ramp_sentence))
    period_match = _ORDERING_PERIOD_RE.search(excerpt)
    if period_match is not None:
        add(EvidenceAssertion.VEHICLE_ORDERING_PERIOD, period_match.group(0))
    source_data_match = _SOURCE_DATA_RE.search(excerpt)
    if source_data_match is not None:
        add(EvidenceAssertion.VEHICLE_SOURCE_DATA_AS_OF,
            source_data_match.group(0))
    return spans


class _WatchSkip(Exception):
    """A vehicle watch record was expected but cannot be minted exactly."""


def _mint_vehicle_watch_record(
    binding: ArtifactBinding, as_of: datetime, lead: VehicleLead,
    record: EvidenceRecord,
) -> tuple[Optional[VehicleWatchRecord], Optional[str]]:
    """Derive the typed Section 3 watch record from a GSA program record.

    Returns ``(record, None)``, ``(None, skip_reason)`` when the vehicle page
    does not publish the required facts or the snapshot is too stale for its
    own claimed status, or ``(None, None)`` for lanes that do not mint watch
    records.  Never raises.
    """

    if lead.lane != VehicleWatchLane.GSA_PROGRAM_RECORDS:
        return None, None
    try:
        return _build_vehicle_watch_record(binding, as_of, lead, record), None
    except _WatchSkip as skip:
        return None, str(skip)
    except ValueError as exc:
        return None, f"watch record not valid: {str(exc)[:120]}"


def _build_vehicle_watch_record(
    binding: ArtifactBinding, as_of: datetime, lead: VehicleLead,
    record: EvidenceRecord,
) -> VehicleWatchRecord:
    excerpt = record.excerpt
    name_match = _VEHICLE_LINE_RE.search(excerpt)
    if name_match is None:
        raise _WatchSkip("vehicle page does not publish a labeled vehicle name")
    name = name_match.group(1).strip()
    agency_match = (_MANAGING_AGENCY_RE.search(excerpt)
                    or _AGENCY_LINE_RE.search(excerpt))
    if agency_match is None:
        raise _WatchSkip("vehicle page does not publish its managing agency")
    agency = agency_match.group(1).strip()
    status_match = _ORDERING_STATUS_RE.search(excerpt)
    if status_match is None:
        raise _WatchSkip("vehicle page does not publish its ordering status")
    status_text = status_match.group(1).casefold()
    if "active" in status_text:
        ordering_status = VehicleOrderingStatus.ACTIVE
    elif "upcoming" in status_text:
        ordering_status = VehicleOrderingStatus.UPCOMING
    elif "closed" in status_text:
        ordering_status = VehicleOrderingStatus.CLOSED
    else:
        ordering_status = VehicleOrderingStatus.NOT_ESTABLISHED

    vehicle_class: Optional[VehicleClass] = None
    class_match = _VEHICLE_CLASS_RE.search(excerpt)
    if class_match is not None:
        class_text = class_match.group(1).casefold()
        for needle, mapped in _VEHICLE_CLASS_MAP:
            if needle in class_text:
                vehicle_class = mapped
                break

    on_ramp_status = VehicleOnRampStatus.NONE_ANNOUNCED
    dates: list[DateValue] = []
    on_ramp_match = _ON_RAMP_LINE_RE.search(excerpt)
    if on_ramp_match is not None:
        on_ramp_text = on_ramp_match.group(1).casefold()
        if "open" in on_ramp_text:
            on_ramp_status = VehicleOnRampStatus.OPEN
        elif "upcoming" in on_ramp_text:
            on_ramp_status = VehicleOnRampStatus.UPCOMING
        elif "closed" in on_ramp_text:
            on_ramp_status = VehicleOnRampStatus.CLOSED
        if on_ramp_status in (
                VehicleOnRampStatus.OPEN, VehicleOnRampStatus.UPCOMING):
            on_ramp_sentence = _sentence_with(excerpt, on_ramp_match.group(0))
            found = _first_date(on_ramp_sentence)
            if found is not None:
                date_text, day = found
                dates.append(DateValue(
                    date_id=f"{record.evidence_id}:on_ramp_close",
                    label="On-ramp responses due", kind=DateKind.ON_RAMP_CLOSE,
                    status=DateStatus.CONFIRMED, precision=DatePrecision.DAY,
                    source_text=date_text, sort_date=day, start=day,
                    evidence_ids=(record.evidence_id,)))
    period_match = _ORDERING_PERIOD_RE.search(excerpt)
    if period_match is not None:
        found = _first_date(period_match.group(0))
        if found is not None:
            date_text, day = found
            dates.append(DateValue(
                date_id=f"{record.evidence_id}:ordering_period_end",
                label="Ordering period monitor",
                kind=DateKind.ORDERING_PERIOD_END,
                status=DateStatus.MONITOR,
                precision=DatePrecision.DAY,
                source_text=date_text, sort_date=day, start=day,
                evidence_ids=(record.evidence_id,)))

    source_data_as_of: Optional[date] = None
    source_data_match = _SOURCE_DATA_RE.search(excerpt)
    if source_data_match is not None:
        found = _first_date(source_data_match.group(0))
        if found is not None:
            source_data_as_of = found[1]

    age = as_of - record.retrieved_at
    current = (
        ordering_status in (
            VehicleOrderingStatus.ACTIVE, VehicleOrderingStatus.UPCOMING)
        or on_ramp_status in (
            VehicleOnRampStatus.OPEN, VehicleOnRampStatus.UPCOMING))
    if current and age > CURRENT_WATCH_MAX_AGE:
        raise _WatchSkip(
            "stale snapshot: a current vehicle status needs a fresh pull")
    near_term_through = as_of.date() + timedelta(days=NEAR_TERM_WATCH_DAYS)
    if any(
        as_of.date() <= item.sort_date <= near_term_through
        for item in dates
        if item.kind in (DateKind.ON_RAMP_OPEN, DateKind.ON_RAMP_CLOSE)
    ) and age > NEAR_TERM_WATCH_MAX_AGE:
        raise _WatchSkip(
            "near-term on-ramp date needs a recheck during the run")

    scope_match = _SCOPE_LINE_RE.search(excerpt)
    return VehicleWatchRecord(
        watch_record_id=(
            f"watch:{_short_key(record.source_identity.canonical_key)}"),
        client_id=binding.client_id, run_id=binding.run_id,
        scope_sha256=binding.scope_sha256,
        canonical_name=name,
        vehicle_source_identity=record.source_identity,
        vehicle_class=vehicle_class,
        identity_evidence_ids=(record.evidence_id,),
        managing_agency=agency,
        management_evidence_ids=(record.evidence_id,),
        scope_summary=(
            scope_match.group(1).strip() if scope_match is not None else None),
        scope_evidence_ids=(
            (record.evidence_id,) if scope_match is not None else ()),
        ordering_status=ordering_status,
        on_ramp_status=on_ramp_status,
        status_evidence_ids=(record.evidence_id,),
        dates=tuple(dates),
        access_posture=VehicleAccessPosture.UNKNOWN,
        records_show=(
            f"Official program records identify {name} and its ordering "
            "status."),
        may_suggest=(
            f"{name} may be a relevant acquisition route for the reviewed "
            "capability set."),
        validate_next=(
            "Validate holder access, ordering eligibility, and the current "
            "on-ramp state."),
        official_evidence_ids=(record.evidence_id,),
        last_checked_at=record.retrieved_at,
        source_data_as_of=source_data_as_of,
        coverage_query_ids=(lead.query_id,))


def _find_verbatim(text: str, identifier: str) -> Optional[str]:
    """Case-insensitively locate ``identifier`` and return it as printed."""

    if not identifier:
        return None
    index = text.casefold().find(identifier.casefold())
    if index < 0:
        return None
    return text[index:index + len(identifier)]


class _SignalSkip(Exception):
    """A vehicle signal was expected but the record does not publish it."""


def _mint_vehicle_signal(
    binding: ArtifactBinding, as_of: datetime, lead: VehicleLead,
    record: EvidenceRecord,
) -> tuple[Optional[VehicleSignal], Optional[str]]:
    """Derive one Section 3 vehicle signal from a minted official record.

    Returns ``(signal, None)``, ``(None, skip_reason)`` when the lane should
    have produced a signal but the record does not publish the required facts,
    or ``(None, None)`` when the lane does not mint signals by design (for
    example an end-user requirement notice or an agency forecast).  Never
    raises: an invalid construction is a typed skip and the evidence stands.
    """

    try:
        return _build_vehicle_signal(binding, as_of, lead, record), None
    except _SignalSkip as skip:
        return None, str(skip)
    except ValueError as exc:
        return None, f"signal not valid: {str(exc)[:120]}"


def _signal_language(kind: VehicleSignalKind, name: str) -> dict[str, str]:
    if kind == VehicleSignalKind.ON_RAMP:
        return {
            "title": f"{name} on-ramp notice",
            "records_show": f"An official notice shows an on-ramp for {name}.",
            "may_suggest": "The on-ramp may open a direct acquisition route.",
            "validate_next": (
                "Confirm eligibility, timing, and submission requirements."),
        }
    if kind == VehicleSignalKind.VEHICLE_ESTABLISHMENT:
        return {
            "title": f"{name} vehicle establishment",
            "records_show": f"An official notice establishes {name}.",
            "may_suggest": "A new vehicle may reshape the acquisition route.",
            "validate_next": "Confirm scope, holders, and ordering rules.",
        }
    if kind == VehicleSignalKind.TASK_ORDER_ACTIVITY:
        return {
            "title": "Verified task order lineage",
            "records_show": (
                "The official order record names the exact parent IDV."),
            "may_suggest": "The parent IDV may be a relevant acquisition route.",
            "validate_next": "Confirm holder access and current ordering status.",
        }
    return {
        "title": "Sourced parent IDV access research",
        "records_show": "The official record identifies the parent IDV.",
        "may_suggest": "The IDV may provide an acquisition route.",
        "validate_next": "Confirm the official program name and holder access.",
    }


def _build_vehicle_signal(
    binding: ArtifactBinding, as_of: datetime, lead: VehicleLead,
    record: EvidenceRecord,
) -> Optional[VehicleSignal]:
    fresh = (as_of - record.retrieved_at) <= CURRENT_WATCH_MAX_AGE
    signal_key = f"{record.source_identity.canonical_key}"

    if lead.lane == VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES:
        kind = _VEHICLE_NOTICE_SIGNAL_KINDS.get(record.notice_role)
        if kind is None:
            return None
        name_span = next(
            (span for span in record.assertion_spans
             if span.assertion == EvidenceAssertion.VEHICLE_IDENTITY), None)
        if name_span is None:
            raise _SignalSkip(
                "vehicle notice does not publish a labeled vehicle name")
        name_match = _VEHICLE_LINE_RE.search(name_span.quote)
        if name_match is None:
            raise _SignalSkip(
                "vehicle notice does not publish a labeled vehicle name")
        name = name_match.group(1).strip()
        agency_match = _AGENCY_LINE_RE.search(record.excerpt)
        agency = record.issuing_office or (
            agency_match.group(1).strip() if agency_match else None)
        if not agency:
            raise _SignalSkip(
                "vehicle notice does not publish its issuing agency")
        status = (
            VehicleSignalStatus.CURRENT
            if kind == VehicleSignalKind.ON_RAMP
            and record.notice_status == NoticeStatus.ACTIVE and fresh
            else VehicleSignalStatus.RESEARCH)
        identity = VehicleIdentity(
            vehicle_id=f"vehicle:{_short_key('name:' + name.casefold())}",
            agency=agency, name=name,
            evidence_ids=(record.evidence_id,))
        return VehicleSignal(
            signal_id=f"signal:{_short_key(signal_key + ':' + kind.value)}",
            client_id=binding.client_id, run_id=binding.run_id,
            scope_sha256=binding.scope_sha256, agency=agency, kind=kind,
            status=status, vehicle=identity,
            evidence_ids=(record.evidence_id,),
            last_checked_at=record.retrieved_at,
            **_signal_language(kind, name))

    if lead.lane in _AWARD_LANES:
        parent = lead.parent_idv_identity
        if parent is None:
            return None
        piid = parent.record_id
        piid_sourced = (
            record.source_identity.record_id.casefold() == piid.casefold()
            or any(
                span.assertion in {
                    EvidenceAssertion.VEHICLE_IDENTITY,
                    EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                }
                and piid.casefold() in span.quote.casefold()
                for span in record.assertion_spans))
        if not piid_sourced:
            raise _SignalSkip(
                "parent IDV is not published on the official record")
        agency_match = _AGENCY_LINE_RE.search(record.excerpt)
        if agency_match is None:
            raise _SignalSkip(
                "official record does not publish a labeled agency")
        agency = agency_match.group(1).strip()
        order = lead.order_identity
        order_bound = (
            order is not None
            and order.canonical_key == record.source_identity.canonical_key
            and any(
                span.assertion == EvidenceAssertion.PARENT_IDV_RELATIONSHIP
                for span in record.assertion_spans))
        kind = (
            VehicleSignalKind.TASK_ORDER_ACTIVITY
            if order_bound else VehicleSignalKind.ACCESS_PATH)
        identity = VehicleIdentity(
            vehicle_id=f"vehicle:{_short_key('piid:' + piid.casefold())}",
            agency=agency, name=None, idv_piid=piid,
            evidence_ids=(record.evidence_id,))
        relationship = VehicleRelationship(
            relationship_id=(
                f"relationship:{_short_key(signal_key + ':' + kind.value)}"),
            vehicle_id=identity.vehicle_id,
            kind=(
                VehicleRelationshipKind.TASK_ORDER
                if order_bound else VehicleRelationshipKind.VEHICLE_ONLY),
            access_posture=VehicleAccessPosture.UNKNOWN,
            order_identity=order if order_bound else None,
            evidence_ids=(record.evidence_id,))
        return VehicleSignal(
            signal_id=f"signal:{_short_key(signal_key + ':' + kind.value)}",
            client_id=binding.client_id, run_id=binding.run_id,
            scope_sha256=binding.scope_sha256, agency=agency, kind=kind,
            status=VehicleSignalStatus.RESEARCH, vehicle=identity,
            relationship=relationship,
            evidence_ids=(record.evidence_id,),
            last_checked_at=record.retrieved_at,
            **_signal_language(kind, piid))

    return None


def _host(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or "official source"


def _short_key(canonical_key: str) -> str:
    import hashlib
    return hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:24]


__all__ = (
    "VERIFICATION_SCHEMA_VERSION",
    "AnchorOffer",
    "EventPageFetcher",
    "FetchedPage",
    "FetchedRecord",
    "VehicleRecordFetcher",
    "VerificationDrop",
    "VerificationLaneNotImplemented",
    "VerificationResult",
    "verify_research",
)
