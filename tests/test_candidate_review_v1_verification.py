"""Verification minting tests (Chunk 6, Pass B): leads -> official evidence.

Exercises the fetch-and-mint core directly with constructed leads + fetched
records (the injected fetcher is the only live seam), and confirms the minted
evidence produces real candidate opportunities through the deterministic author.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from agents.candidate_review_v1.authoring import author_candidate_review
from agents.candidate_review_v1.candidate_engine import build_candidate_inventory
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
)
from agents.candidate_review_v1.event_research import EventLead, EventSourceClass
from agents.candidate_review_v1.vehicle_watch import VehicleLead, VehicleWatchLane
from agents.candidate_review_v1.verification import (
    AnchorOffer,
    FetchedPage,
    FetchedRecord,
    _MintSkip,
    _mint_event,
    _mint_vehicle_evidence,
    _notice_window_open,
)

_NOW = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)
_H = "a" * 64
_BINDING = ArtifactBinding(
    client_id="testco", client_name="TestCo", run_id="run-1",
    scope_designator="all_federal", scope_sha256=_H,
    profile_sha256="b" * 64, evidence_snapshot_sha256="c" * 64)


def _order_lead(index: int, record_id: str) -> VehicleLead:
    return VehicleLead(
        lead_id=f"lead-{index}", client_id="testco", run_id="run-1", scope_sha256=_H,
        query_id="q1", lane=VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
        discovered_at=_NOW - timedelta(hours=3), title="award",
        source_url=f"https://www.usaspending.gov/award/{record_id}",
        source_identity=SourceIdentity(source_system="usaspending.gov", record_id=record_id),
        parent_idv_identity=SourceIdentity(source_system="usaspending.gov", record_id=f"IDV-{index}"),
        order_identity=SourceIdentity(source_system="usaspending.gov", record_id=f"TO-{index}"))


def _notice_lead(record_id: str) -> VehicleLead:
    return VehicleLead(
        lead_id="lead-n", client_id="testco", run_id="run-1", scope_sha256=_H, query_id="q2",
        lane=VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        discovered_at=_NOW - timedelta(hours=3), title="notice",
        source_url=f"https://sam.gov/opp/{record_id}/view",
        source_identity=SourceIdentity(source_system="sam.gov", record_id=record_id))


def _award_page(lead: VehicleLead, end_text: str = "September 30, 2027") -> FetchedRecord:
    return FetchedRecord(
        lead_id=lead.lead_id, final_url=str(lead.source_url), fetched_at=_NOW - timedelta(hours=1),
        title="Department of the Navy - Network Observability Services",
        text=("Department of the Navy awarded Network Observability Services to an incumbent. "
              f"The period of performance ends {end_text}. Obligations to date total $42.6M."),
        record_sha256="d" * 64, effective_date=date(2025, 10, 1))


def _notice_page(lead: VehicleLead, *, verified_at: datetime) -> FetchedRecord:
    # Retrieval always precedes verification (the watch run fetched then verified).
    return FetchedRecord(
        lead_id=lead.lead_id, final_url=str(lead.source_url),
        fetched_at=verified_at - timedelta(minutes=10),
        title="US Special Operations Command - SOF Enterprise Professional Services",
        text="USSOCOM seeks SOF enterprise professional services. Responses are due August 3, 2026.",
        record_sha256="e" * 64, verified_at=verified_at, notice_status=NoticeStatus.ACTIVE,
        notice_role=NoticeRole.END_USER_REQUIREMENT, issuing_office="USSOCOM",
        solicitation_number="RFISEPS2027")


def _offer(record) -> AnchorOffer:
    return AnchorOffer(
        evidence_id=record.evidence_id, can_anchor=record.can_anchor_candidate,
        confirms_open_notice=record.confirms_open_notice,
        notice_window_open=_notice_window_open(record, _NOW))


# --------------------------------------------------------------------------- #

def test_award_lead_mints_award_evidence_and_becomes_a_research_corridor():
    lead = _order_lead(1, "AWARD-1")
    rec = _mint_vehicle_evidence(_BINDING, _NOW, lead, _award_page(lead), {})
    assert rec.source_kind.value == "award" and rec.official_source and rec.primary_source
    assert rec.can_anchor_candidate is True
    assert any(s.assertion.value == "award_period_end" for s in rec.assertion_spans)
    build = _build((rec,))
    assert len(build.candidates) == 1
    c = build.candidates[0]
    assert c.kind.value == "research_corridor"
    assert [d.kind.value for d in c.dates] == ["award_end_research_clock"]
    assert len(c.money) == 1


def test_sam_lead_mints_a_current_notice_when_verified_inside_24h():
    lead = _notice_lead("NOTICE1")
    rec = _mint_vehicle_evidence(_BINDING, _NOW, lead, _notice_page(lead, verified_at=_NOW - timedelta(minutes=20)), {})
    assert rec.source_kind.value == "notice" and rec.confirms_open_notice is True
    offer = _offer(rec)
    assert offer.notice_window_open is True
    build = _build((rec,))
    assert [c.kind.value for c in build.candidates] == ["current_notice"]


def test_stale_notice_is_dropped_not_forced_or_downgraded():
    # The 24h trap: an active notice verified >24h before as_of cannot be a
    # current-notice candidate and the engine forbids demoting it to a corridor,
    # so the author drops it (it needs re-verification during the watch run).
    lead = _notice_lead("NOTICE2")
    rec = _mint_vehicle_evidence(_BINDING, _NOW, lead, _notice_page(lead, verified_at=_NOW - timedelta(hours=48)), {})
    assert rec.confirms_open_notice is True
    assert _offer(rec).notice_window_open is False
    authored = author_candidate_review(
        binding=_BINDING, as_of=_NOW, evidence=(rec,), anchor_offers=(_offer(rec),))
    assert authored.seeds == ()
    assert any("could not derive" in d.reason or d.stage == "compose" for d in authored.drops)
    build = build_candidate_inventory(binding=_BINDING, as_of=_NOW, evidence=(rec,), seeds=authored.seeds)
    assert build.candidates == ()


def test_fetch_after_as_of_refuses_to_mint():
    lead = _order_lead(3, "AWARD-3")
    page = _award_page(lead)
    future = page.model_copy(update={"fetched_at": _NOW + timedelta(hours=1)})
    with pytest.raises(ValueError):
        _mint_vehicle_evidence(_BINDING, _NOW, lead, future, {})


def test_one_record_per_canonical_key_is_memoized():
    lead = _order_lead(4, "AWARD-4")
    memo = {}
    first = _mint_vehicle_evidence(_BINDING, _NOW, lead, _award_page(lead), memo)
    second = _mint_vehicle_evidence(_BINDING, _NOW, lead, _award_page(lead, end_text="January 1, 2030"), memo)
    assert first is second  # the second fetch cannot fork a second record for the same identity


def test_gsa_catalog_lane_is_vehicle_context_never_an_anchor():
    # 2026-07-24 (Pass C2 update): the GSA program lane now mints VEHICLE-kind
    # context evidence for the watch record, but the original doctrine holds
    # mechanically: it can never anchor a candidate.
    lead = VehicleLead(
        lead_id="lead-g", client_id="testco", run_id="run-1", scope_sha256=_H, query_id="q3",
        lane=VehicleWatchLane.GSA_PROGRAM_RECORDS, discovered_at=_NOW - timedelta(hours=3),
        title="vehicle", source_url="https://www.gsaelibrary.gsa.gov/vehicle/OASIS",
        source_identity=SourceIdentity(source_system="gsa.gov", record_id="OASIS"))
    page = FetchedRecord(
        lead_id="lead-g", final_url=str(lead.source_url), fetched_at=_NOW - timedelta(hours=1),
        title="OASIS+ Vehicle", text="OASIS Plus is a multiple-award IDIQ.", record_sha256="f" * 64)
    record = _mint_vehicle_evidence(_BINDING, _NOW, lead, page, {})
    assert record.source_kind.value == "vehicle"
    assert record.can_anchor_candidate is False
    build = _build((record,))
    assert build.candidates == ()


def _build(evidence):
    authored = author_candidate_review(
        binding=_BINDING, as_of=_NOW, evidence=evidence,
        anchor_offers=tuple(_offer(e) for e in evidence))
    return build_candidate_inventory(
        binding=_BINDING, as_of=_NOW, evidence=evidence, seeds=authored.seeds)


# --------------------------------------------------------------------------- #
# Pass A - event minting
# --------------------------------------------------------------------------- #

def _event_lead(url: str = "https://www.afcea.org/events/technet-cyber") -> EventLead:
    return EventLead(
        lead_id="el-1", query_id="eq-1", source_class=EventSourceClass.OFFICIAL_ORGANIZER,
        discovered_at=_NOW - timedelta(hours=4), title="AFCEA TechNet Cyber", url=url,
        claimed_date="sometime in December", claimed_organizer="Unverified Organizer",
        claimed_location="Unverified City")


def _event_page(url: str, *, when: str = "December 1, 2026",
                organizer: str = "AFCEA International",
                location: str = "Baltimore, Maryland") -> FetchedPage:
    return FetchedPage(
        lead_id="el-1", final_url=url, fetched_at=_NOW - timedelta(hours=1),
        title="AFCEA TechNet Cyber 2026",
        text=(f"AFCEA TechNet Cyber 2026 convenes the defense cyber community. "
              f"The conference opens {when} at the convention center. "
              f"Organizer: {organizer}. Location: {location}."),
        record_sha256="f" * 64)


def test_trusted_organizer_event_mints_evidence_and_a_calendar_seed():
    lead = _event_lead()
    rec, seed = _mint_event(_BINDING, _NOW, lead, _event_page(str(lead.url)), {"afcea.org"}, {})
    assert rec.source_kind.value == "organizer_event"
    assert rec.official_source and rec.primary_source
    span = next(s for s in rec.assertion_spans if s.assertion.value == "official_event")
    assert seed.timing.kind.value == "event_start"
    # Path A: the date must live inside the assertion span quote, not just the excerpt
    assert seed.timing.source_text in span.quote
    assert seed.organizer == "AFCEA International" and seed.location == "Baltimore, Maryland"
    assert seed.query_ids == ("eq-1",) and seed.discovery_lead_ids == ("el-1",)


def test_official_gov_host_mints_an_official_event():
    lead = _event_lead("https://www.dhs.gov/events/industry-day")
    rec, _seed = _mint_event(_BINDING, _NOW, lead, _event_page(str(lead.url)), set(), {})
    assert rec.source_kind.value == "official_event"


def test_claimed_lead_fields_never_enter_the_evidence():
    lead = _event_lead()
    rec, seed = _mint_event(_BINDING, _NOW, lead, _event_page(str(lead.url)), {"afcea.org"}, {})
    for hint in ("sometime in December", "Unverified Organizer", "Unverified City"):
        assert hint not in rec.excerpt
        assert hint != seed.organizer and hint != seed.location
        assert hint != seed.timing.source_text


def test_aggregator_host_is_discovery_only_and_never_publishes():
    lead = _event_lead("https://aggregator.example.com/e/1")
    with pytest.raises(_MintSkip):
        _mint_event(_BINDING, _NOW, lead, _event_page(str(lead.url)), {"afcea.org"}, {})


def test_past_event_edition_is_skipped():
    lead = _event_lead()
    page = _event_page(str(lead.url), when="January 5, 2020")
    with pytest.raises(_MintSkip):
        _mint_event(_BINDING, _NOW, lead, page, {"afcea.org"}, {})


def test_page_without_a_published_organizer_or_location_refuses():
    lead = _event_lead()
    page = FetchedPage(
        lead_id="el-1", final_url=str(lead.url), fetched_at=_NOW - timedelta(hours=1),
        title="AFCEA TechNet Cyber 2026",
        text="AFCEA TechNet Cyber 2026 opens December 1, 2026.", record_sha256="f" * 64)
    with pytest.raises(ValueError):
        _mint_event(_BINDING, _NOW, lead, page, {"afcea.org"}, {})


# --- Pass C1: vehicle signal minting (2026-07-23) --------------------------- #
# Policy: docs/reference/federal-event-and-idiq-watch-policy.md; vehicle
# signals render selectively in Section 3 and never create candidates.

from agents.candidate_review_v1.contracts import (  # noqa: E402
    EvidenceAssertion,
    VehicleSignalKind,
    VehicleSignalStatus,
    validate_vehicle_signal_evidence,
)
from agents.candidate_review_v1.verification import verify_research  # noqa: E402
from agents.candidate_review_v1.event_research import (  # noqa: E402
    EventDiscoveryResult,
)
from agents.candidate_review_v1.vehicle_watch import (  # noqa: E402
    VehicleCollectionResult,
)


def _onramp_lead(record_id: str = "ONRAMP1") -> VehicleLead:
    return VehicleLead(
        lead_id=f"lead-{record_id}", client_id="testco", run_id="run-1",
        scope_sha256=_H, query_id="q4",
        lane=VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        discovered_at=_NOW - timedelta(hours=3), title="on-ramp",
        source_url=f"https://sam.gov/opp/{record_id}/view",
        source_identity=SourceIdentity(
            source_system="sam.gov", record_id=record_id))


def _onramp_page(lead: VehicleLead, *, verified_at: datetime,
                 text: str | None = None) -> FetchedRecord:
    return FetchedRecord(
        lead_id=lead.lead_id, final_url=str(lead.source_url),
        fetched_at=verified_at - timedelta(minutes=10),
        title="GSA - Polaris On-Ramp",
        text=text or (
            "Agency: General Services Administration. Vehicle: Polaris. "
            "GSA announces an on-ramp for additional awards. "
            "Responses are due September 15, 2026."),
        record_sha256="a1" * 32, verified_at=verified_at,
        notice_status=NoticeStatus.ACTIVE,
        notice_role=NoticeRole.VEHICLE_ON_RAMP,
        issuing_office="General Services Administration",
        solicitation_number="POLARIS-ONRAMP-2026")


def _lineage_lead(record_id: str = "TO-77") -> VehicleLead:
    # The fetched record IS the order row: source identity equals the order
    # identity, with the parent IDV held separately (never conflated).
    return VehicleLead(
        lead_id=f"lead-{record_id}", client_id="testco", run_id="run-1",
        scope_sha256=_H, query_id="q5",
        lane=VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
        discovered_at=_NOW - timedelta(hours=3), title="order",
        source_url=f"https://www.usaspending.gov/award/{record_id}",
        source_identity=SourceIdentity(
            source_system="usaspending.gov", record_id=record_id),
        parent_idv_identity=SourceIdentity(
            source_system="usaspending.gov", record_id="IDV-9000"),
        order_identity=SourceIdentity(
            source_system="usaspending.gov", record_id=record_id))


def _lineage_page(lead: VehicleLead, *, text: str | None = None) -> FetchedRecord:
    return FetchedRecord(
        lead_id=lead.lead_id, final_url=str(lead.source_url),
        fetched_at=_NOW - timedelta(hours=1),
        title="Task order record",
        text=text or (
            "Agency: Department of the Navy. "
            "Task order TO-77 uses parent IDV IDV-9000 for network services. "
            "The ordering period ends March 31, 2027."),
        record_sha256="b2" * 32)


def test_onramp_notice_mints_a_current_on_ramp_signal():
    lead = _onramp_lead()
    record = _mint_vehicle_evidence(
        _BINDING, _NOW, lead,
        _onramp_page(lead, verified_at=_NOW - timedelta(minutes=20)), {})
    signal, skip = __import__(
        "agents.candidate_review_v1.verification",
        fromlist=["_mint_vehicle_signal"])._mint_vehicle_signal(
        _BINDING, _NOW, lead, record)
    assert skip is None and signal is not None
    assert signal.kind == VehicleSignalKind.ON_RAMP
    assert signal.status == VehicleSignalStatus.CURRENT
    assert signal.vehicle.name == "Polaris"
    assert signal.creates_candidate is False
    validate_vehicle_signal_evidence(
        signal, binding=_BINDING, as_of=_NOW,
        evidence_by_id={record.evidence_id: record})


def test_onramp_without_labeled_vehicle_name_is_a_typed_skip():
    lead = _onramp_lead("ONRAMP2")
    page = _onramp_page(
        lead, verified_at=_NOW - timedelta(minutes=20),
        text=("Agency: General Services Administration. "
              "An on-ramp is announced. Responses are due September 15, 2026."))
    record = _mint_vehicle_evidence(_BINDING, _NOW, lead, page, {})
    from agents.candidate_review_v1.verification import _mint_vehicle_signal
    signal, skip = _mint_vehicle_signal(_BINDING, _NOW, lead, record)
    assert signal is None
    assert skip is not None and "vehicle name" in skip


def test_lineage_record_mints_task_order_activity_with_exact_spans():
    lead = _lineage_lead()
    record = _mint_vehicle_evidence(_BINDING, _NOW, lead, _lineage_page(lead), {})
    assert any(
        span.assertion == EvidenceAssertion.PARENT_IDV_RELATIONSHIP
        for span in record.assertion_spans)
    from agents.candidate_review_v1.verification import _mint_vehicle_signal
    signal, skip = _mint_vehicle_signal(_BINDING, _NOW, lead, record)
    assert skip is None and signal is not None
    assert signal.kind == VehicleSignalKind.TASK_ORDER_ACTIVITY
    assert signal.vehicle.idv_piid == "IDV-9000"
    assert signal.vehicle.name is None
    assert signal.relationship is not None
    assert signal.relationship.order_identity == lead.order_identity
    validate_vehicle_signal_evidence(
        signal, binding=_BINDING, as_of=_NOW,
        evidence_by_id={record.evidence_id: record})


def test_lineage_without_published_parent_is_a_typed_skip():
    lead = _lineage_lead("TO-88")
    page = _lineage_page(lead, text=(
        "Agency: Department of the Navy. "
        "Task order TO-88 provides network services."))
    record = _mint_vehicle_evidence(_BINDING, _NOW, lead, page, {})
    from agents.candidate_review_v1.verification import _mint_vehicle_signal
    signal, skip = _mint_vehicle_signal(_BINDING, _NOW, lead, record)
    assert signal is None and skip is not None
    assert "parent IDV" in skip


def test_stale_retrieval_demotes_on_ramp_to_research_status():
    # 2026-07-23: CURRENT requires the seven-day watch freshness window; an
    # older fetched page mints an honest RESEARCH signal instead of failing
    # the document validator later.
    lead = _onramp_lead("ONRAMP3")
    verified = _NOW - timedelta(days=9)
    record = _mint_vehicle_evidence(
        _BINDING, _NOW, lead, _onramp_page(lead, verified_at=verified), {})
    from agents.candidate_review_v1.verification import _mint_vehicle_signal
    signal, skip = _mint_vehicle_signal(_BINDING, _NOW, lead, record)
    assert skip is None and signal is not None
    assert signal.status == VehicleSignalStatus.RESEARCH


def test_verify_research_returns_signals_accepted_map_and_typed_skips():
    onramp = _onramp_lead("ONRAMP4")
    lineage = _lineage_lead("TO-99")
    bare = _lineage_lead("TO-100")
    pages = {
        onramp.lead_id: _onramp_page(
            onramp, verified_at=_NOW - timedelta(minutes=20)),
        lineage.lead_id: _lineage_page(lineage, text=(
            "Agency: Department of the Navy. "
            "Task order TO-99 uses parent IDV IDV-9000 for network services.")),
        bare.lead_id: _lineage_page(bare, text=(
            "Task order TO-100 provides base support services.")),
    }
    result = verify_research(
        binding=_BINDING, as_of=_NOW,
        event_result=EventDiscoveryResult.model_construct(
            leads=(), attempts=(), manifest=None),
        vehicle_result=VehicleCollectionResult.model_construct(
            leads=(onramp, lineage, bare), attempts=(), manifest=None),
        fetch_event=None,
        fetch_vehicle=lambda lead: pages[lead.lead_id])
    kinds = {signal.kind for signal in result.vehicle_signals}
    assert kinds == {
        VehicleSignalKind.ON_RAMP, VehicleSignalKind.TASK_ORDER_ACTIVITY}
    assert set(result.accepted_vehicle_evidence_by_query) == {"q4", "q5"}
    assert len(result.accepted_vehicle_evidence_by_query["q5"]) == 2
    skip_lanes = {drop.lane for drop in result.drops}
    assert "vehicle-signal" in skip_lanes


# --- Pass C2: vehicle watch record minting (2026-07-24) --------------------- #

from datetime import date as _date  # noqa: E402

from agents.candidate_review_v1.contracts import (  # noqa: E402
    CoverageRecord,
    CoverageState,
    EvidenceKind,
    VehicleOnRampStatus,
    VehicleOrderingStatus,
    validate_vehicle_watch_record_evidence,
)
from agents.candidate_review_v1.verification import (  # noqa: E402
    _mint_vehicle_watch_record,
)


def _gsa_lead(record_id: str = "POLARIS") -> VehicleLead:
    return VehicleLead(
        lead_id=f"lead-{record_id}", client_id="testco", run_id="run-1",
        scope_sha256=_H, query_id="q-gsa",
        lane=VehicleWatchLane.GSA_PROGRAM_RECORDS,
        discovered_at=_NOW - timedelta(hours=3), title=record_id,
        source_url=f"https://www.gsaelibrary.gsa.gov/vehicle/{record_id}",
        source_identity=SourceIdentity(
            source_system="gsa.gov", record_id=record_id))


_GSA_TEXT = (
    "Vehicle: Polaris. Vehicle class: GWAC. "
    "Managing agency: General Services Administration. "
    "Scope: Information technology services and emerging technology. "
    "Ordering status: Ordering is active. "
    "On-ramp: An on-ramp is open. Responses are due September 15, 2026. "
    "Ordering period ends May 30, 2029. "
    "Source data as of July 20, 2026.")


def _gsa_page(lead: VehicleLead, *, text: str = _GSA_TEXT,
              fetched_at: datetime | None = None) -> FetchedRecord:
    return FetchedRecord(
        lead_id=lead.lead_id, final_url=str(lead.source_url),
        fetched_at=fetched_at or (_NOW - timedelta(hours=1)),
        title="Polaris GWAC", text=text, record_sha256="c3" * 32)


def _coverage_row(lead: VehicleLead, record) -> CoverageRecord:
    return CoverageRecord(
        client_id="testco", run_id="run-1", scope_sha256=_H,
        source="gsa_elibrary", query_family="vehicle_program",
        state=CoverageState.RETURNED,
        window_start=_NOW.date() - timedelta(days=1),
        window_end=_NOW.date(),
        attempted_at=_NOW - timedelta(hours=2),
        records_returned=1, records_accepted=1,
        accepted_evidence_ids=(record.evidence_id,),
        query_manifest_id="m1", query_id=lead.query_id)


def test_gsa_lane_now_mints_vehicle_evidence_and_a_watch_record():
    # 2026-07-24 Pass C2: the GSA program lane mints VEHICLE-kind context
    # evidence (never an anchor) plus the typed Section 3 watch record.
    lead = _gsa_lead()
    record = _mint_vehicle_evidence(_BINDING, _NOW, lead, _gsa_page(lead), {})
    assert record.source_kind == EvidenceKind.VEHICLE
    assert record.can_anchor_candidate is False
    watch, skip = _mint_vehicle_watch_record(_BINDING, _NOW, lead, record)
    assert skip is None and watch is not None
    assert watch.canonical_name == "Polaris"
    assert watch.managing_agency == "General Services Administration"
    assert watch.ordering_status == VehicleOrderingStatus.ACTIVE
    assert watch.on_ramp_status == VehicleOnRampStatus.OPEN
    assert watch.source_data_as_of == _date(2026, 7, 20)
    assert {item.kind.value for item in watch.dates} == {
        "on_ramp_close", "ordering_period_end"}
    assert watch.creates_candidate is False
    validate_vehicle_watch_record_evidence(
        watch, binding=_BINDING, as_of=_NOW,
        evidence_by_id={record.evidence_id: record},
        coverage_by_query_id={lead.query_id: _coverage_row(lead, record)})


def test_watch_record_without_ordering_status_is_a_typed_skip():
    lead = _gsa_lead("OASISPLUS")
    page = _gsa_page(lead, text=(
        "Vehicle: OASIS Plus. Managing agency: General Services "
        "Administration."))
    record = _mint_vehicle_evidence(_BINDING, _NOW, lead, page, {})
    watch, skip = _mint_vehicle_watch_record(_BINDING, _NOW, lead, record)
    assert watch is None and skip is not None
    assert "ordering status" in skip


def test_stale_current_snapshot_is_a_typed_skip_not_a_stale_claim():
    # 2026-07-24 freshness policy: a snapshot older than seven days cannot
    # carry a current ordering/on-ramp status; it skips with a named reason
    # instead of minting a record the document validator would reject.
    lead = _gsa_lead("ALLIANT3")
    record = _mint_vehicle_evidence(
        _BINDING, _NOW, lead,
        _gsa_page(lead, fetched_at=_NOW - timedelta(days=9)), {})
    watch, skip = _mint_vehicle_watch_record(_BINDING, _NOW, lead, record)
    assert watch is None and skip is not None
    assert "stale snapshot" in skip


def test_near_term_on_ramp_requires_a_fresh_recheck():
    # An on-ramp closing inside 30 days demands a within-24h retrieval.
    lead = _gsa_lead("CIOSP4")
    near = (_NOW + timedelta(days=10)).date()
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    text = _GSA_TEXT.replace(
        "September 15, 2026",
        f"{months[near.month - 1]} {near.day}, {near.year}")
    record = _mint_vehicle_evidence(
        _BINDING, _NOW, lead,
        _gsa_page(lead, text=text, fetched_at=_NOW - timedelta(hours=30)), {})
    watch, skip = _mint_vehicle_watch_record(_BINDING, _NOW, lead, record)
    assert watch is None and skip is not None
    assert "recheck" in skip


def test_verify_research_returns_watch_records_for_gsa_leads():
    lead = _gsa_lead("POLARIS2")
    pages = {lead.lead_id: _gsa_page(lead)}
    result = verify_research(
        binding=_BINDING, as_of=_NOW,
        event_result=EventDiscoveryResult.model_construct(
            leads=(), attempts=(), manifest=None),
        vehicle_result=VehicleCollectionResult.model_construct(
            leads=(lead,), attempts=(), manifest=None),
        fetch_event=None,
        fetch_vehicle=lambda item: pages[item.lead_id])
    assert len(result.vehicle_watch_records) == 1
    assert result.vehicle_watch_records[0].coverage_query_ids == ("q-gsa",)
    assert result.accepted_vehicle_evidence_by_query["q-gsa"]
