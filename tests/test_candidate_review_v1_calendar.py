"""Adversarial acceptance tests for the Candidate Review v1 calendar lane."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.calendar_engine import (
    MAX_VISIBLE_RESEARCHED_EVENTS,
    CalendarDisposition,
    CalendarItemOrigin,
    EventSeed,
    add_calendar_months,
    build_calendar,
)
from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    ArtifactBinding,
    CandidateKind,
    CandidateMember,
    CandidateReviewDocument,
    CandidateUnit,
    ClusterLevel,
    CoverageState,
    DateKind,
    DatePrecision,
    DateStatus,
    DateValue,
    DecisionBoundary,
    EditorState,
    EventKind,
    EventLifecycleStatus,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    LifecycleKind,
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
    SourceTier,
    VehicleAccessPosture,
    VehicleIdentity,
    VehicleRelationship,
    VehicleRelationshipKind,
    VehicleSignal,
    VehicleSignalKind,
    VehicleSignalStatus,
)
from agents.candidate_review_v1.event_research import (
    EventQueryAttempt,
    EventResearchFrame,
    EventSourceClass,
    build_event_query_manifest,
    run_event_discovery,
)
from agents.candidate_review_v1.event_watch import (
    WatchFrameTags,
    load_event_watch_universe,
    select_watch_targets,
    watch_universe_sha256,
)
from agents.candidate_review_v1.projection import project_candidate_review


NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
HASH = "a" * 64


def _binding(
    client_id: str = "testco",
    *,
    run_id: str = "run-1",
    scope_hash: str = HASH,
) -> ArtifactBinding:
    names = {"testco": "TestCo", "otherco": "OtherCo"}
    return ArtifactBinding(
        client_id=client_id,
        client_name=names[client_id],
        run_id=run_id,
        scope_designator="all-federal",
        scope_sha256=scope_hash,
        profile_sha256="b" * 64,
        evidence_snapshot_sha256="c" * 64,
    )


def _manifest(
    binding: ArtifactBinding | None = None,
    *,
    trusted_domains: tuple[str, ...] = ("events.example.org",),
):
    frame = EventResearchFrame(
        binding=binding or _binding(),
        revision_sha256="d" * 64,
        capabilities_missions=("federal data operations",),
        trusted_organizer_domains=trusted_domains,
    )
    return build_event_query_manifest(frame, NOW)


def _query(manifest):
    return next(
        item for item in manifest.queries
        if item.required
        and item.source_class == EventSourceClass.OFFICIAL_ORGANIZER
    )


def _attempts(
    manifest,
    *,
    leads_by_query: dict[str, tuple[str, ...]] | None = None,
    failed_query_ids: frozenset[str] = frozenset(),
):
    leads_by_query = leads_by_query or {}
    rows = []
    for query in manifest.queries:
        if not query.required:
            rows.append(EventQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.SCOPE_EXCLUDED,
            ))
        elif query.query_id in failed_query_ids:
            rows.append(EventQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.FAILED,
                attempted_at=NOW,
                public_detail="The official-source search failed.",
            ))
        else:
            lead_ids = leads_by_query.get(query.query_id, ())
            rows.append(EventQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.RETURNED,
                attempted_at=NOW,
                records_returned=len(lead_ids),
                lead_ids=lead_ids,
                public_detail=(
                    "Discovery completed with no leads."
                    if not lead_ids
                    else f"Discovery returned {len(lead_ids)} lead(s)."
                ),
            ))
    return tuple(rows)


def _evidence(
    evidence_id: str,
    record_id: str,
    *,
    kind: EvidenceKind,
    excerpt: str,
    url: str,
    assertions: tuple[EvidenceAssertion, ...] = (),
    assertion_quotes: tuple[tuple[EvidenceAssertion, str], ...] = (),
    client_id: str = "testco",
    run_id: str = "run-1",
    scope_hash: str = HASH,
    official: bool = True,
    primary: bool = True,
    source_name: str = "Official organizer",
    notice_role: NoticeRole = NoticeRole.END_USER_REQUIREMENT,
    retrieved_at: datetime | None = None,
) -> EvidenceRecord:
    notice = kind == EvidenceKind.NOTICE
    discovery = kind in (EvidenceKind.NEWS, EvidenceKind.WEB_DISCOVERY)
    spans = (
        tuple(EvidenceAssertionSpan(assertion=assertion, quote=quote)
              for assertion, quote in assertion_quotes)
        if assertion_quotes else
        tuple(EvidenceAssertionSpan(assertion=assertion, quote=excerpt)
              for assertion in assertions)
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        client_id=client_id,
        run_id=run_id,
        scope_sha256=scope_hash,
        source_identity=SourceIdentity(
            source_system=("sam.gov" if notice else "event-organizer"),
            record_id=record_id,
        ),
        source_tier=(
            SourceTier.NOTICE if notice
            else SourceTier.DISCOVERY if discovery
            else SourceTier.PROGRAM
        ),
        source_kind=kind,
        source_name=source_name,
        source_url=url,
        retrieved_at=retrieved_at or NOW - timedelta(hours=2),
        title=f"Official record {record_id}",
        excerpt=excerpt,
        official_source=official,
        primary_source=primary,
        supports=(EvidenceUse.TIMING, EvidenceUse.EVENT),
        notice_status=NoticeStatus.ACTIVE if notice else None,
        notice_role=notice_role if notice else None,
        verified_at=NOW - timedelta(hours=1) if notice else None,
        assertion_spans=spans,
    )


def _event_evidence(
    *,
    evidence_id: str = "E-EVENT",
    record_id: str = "DATA-SUMMIT-2026",
    start: date = date(2026, 9, 1),
    title: str = "Federal Data Summit",
    organizer: str = "Example Organizer",
    location: str = "Denver, CO",
    url: str = "https://events.example.org/data-summit",
    extra: str = "",
    assertions: tuple[EvidenceAssertion, ...] = (
        EvidenceAssertion.OFFICIAL_EVENT,
    ),
    kind: EvidenceKind = EvidenceKind.ORGANIZER_EVENT,
    official: bool = True,
    primary: bool = True,
    retrieved_at: datetime | None = None,
):
    source_text = start.isoformat()
    excerpt = (
        f"{organizer} presents {title} on {source_text} at {location}. "
        f"{extra}"
    ).strip()
    return _evidence(
        evidence_id,
        record_id,
        kind=kind,
        excerpt=excerpt,
        url=url,
        assertions=assertions,
        official=official,
        primary=primary,
        source_name=organizer,
        retrieved_at=retrieved_at,
    )


def _event_seed(
    evidence: EvidenceRecord,
    manifest,
    *,
    start: date = date(2026, 9, 1),
    title: str = "Federal Data Summit",
    organizer: str = "Example Organizer",
    location: str = "Denver, CO",
    external_event_id: str = "DATA-SUMMIT-2026",
    lead_id: str = "LEAD-1",
    query_id: str | None = None,
    flagship: bool = False,
    lifecycle_status: EventLifecycleStatus = EventLifecycleStatus.SCHEDULED,
    timing: DateValue | None | object = ...,  # ``...`` means build one.
    relevance: str = "The program covers the approved capability frame.",
    validate_next: str = "Confirm the current agenda and registration status.",
    attending_agencies: tuple[str, ...] = (),
    attendance_evidence_ids: tuple[str, ...] = (),
    registration_url: str | None = None,
    registration_evidence_ids: tuple[str, ...] = (),
    last_checked_at: datetime | None = None,
):
    query_id = query_id or _query(manifest).query_id
    if timing is ...:
        timing = DateValue(
            date_id=f"DATE-{external_event_id}",
            label="Event date",
            kind=DateKind.EVENT_START,
            status=DateStatus.CONFIRMED,
            precision=DatePrecision.DAY,
            source_text=start.isoformat(),
            sort_date=start,
            start=start,
            evidence_ids=(evidence.evidence_id,),
        )
    return EventSeed(
        external_event_id=external_event_id,
        title=title,
        kind=EventKind.CONFERENCE,
        timing=timing,
        organizer=organizer,
        location=location,
        audience="Federal program and acquisition leaders",
        relevance=relevance,
        validate_next=validate_next,
        registration_url=registration_url,
        registration_evidence_ids=registration_evidence_ids,
        evidence_ids=(evidence.evidence_id,),
        attending_agencies=attending_agencies,
        attendance_evidence_ids=attendance_evidence_ids,
        flagship=flagship,
        flagship_evidence_ids=(evidence.evidence_id,) if flagship else (),
        lifecycle_status=lifecycle_status,
        last_checked_at=last_checked_at or NOW - timedelta(minutes=30),
        query_ids=(query_id,),
        discovery_lead_ids=(lead_id,),
    )


def _build(
    *,
    evidence=(),
    seeds=(),
    candidates=(),
    manifest=None,
    attempts=None,
    vehicle_signals=(),
    maximum_items=32,
):
    manifest = manifest or _manifest()
    if attempts is None:
        attempts = _attempts(manifest)
    return build_calendar(
        binding=manifest.binding,
        as_of=NOW,
        candidates=candidates,
        evidence=evidence,
        event_seeds=seeds,
        manifest=manifest,
        attempts=attempts,
        trusted_organizer_domains=manifest.trusted_organizer_domains,
        vehicle_signals=vehicle_signals,
        maximum_items=maximum_items,
    )


def _procurement_seed(
    evidence: EvidenceRecord,
    timing: DateValue,
    *,
    title: str = "Award-end research clock",
):
    return EventSeed(
        title=title,
        kind=EventKind.PROCUREMENT_MILESTONE,
        timing=timing,
        agency="Test Agency",
        relevance="The official record establishes a research timing marker.",
        validate_next="Confirm the milestone against the current official record.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=NOW - timedelta(minutes=30),
    )


def _candidate(
    evidence: EvidenceRecord,
    dates: tuple[DateValue, ...],
    *,
    candidate_id: str = "C-1",
    supporting_evidence_ids: tuple[str, ...] = (),
):
    boundary = DecisionBoundary(
        buyer_key="agency:office-a",
        deadline=date(2026, 8, 15),
        eligibility_key="full-and-open",
        lifecycle=LifecycleKind.LIVE_SOLICITATION,
        program_key="program-a",
        access_route_key="open-market",
        analyst_action_key="review-requirement",
    )
    return CandidateUnit(
        candidate_id=candidate_id,
        client_id="testco",
        kind=CandidateKind.CURRENT_NOTICE,
        title="Current federal notice",
        agency="Test Agency",
        lifecycle=LifecycleKind.LIVE_SOLICITATION,
        cluster_level=ClusterLevel.EXACT_RECORD,
        members=(CandidateMember(
            source_identity=evidence.source_identity,
            member_evidence_ids=(evidence.evidence_id,),
            decision_boundary=boundary,
        ),),
        supporting_evidence_ids=supporting_evidence_ids,
        dates=dates,
        records_show="An official notice states a current requirement.",
        may_suggest="The requirement may align with the capability frame.",
        validate_next="Validate the complete requirement and timing.",
        cluster_reason="This is one exact official record.",
        strategic_action="Validate the acquisition route.",
        distinctness_explanation="The buyer and deadline are distinct.",
        inference_chain="Official notice to analyst validation.",
        falsifier="The notice no longer supports the requirement.",
        watch_trigger="An official amendment changes the timing.",
    )


def _document(binding: ArtifactBinding):
    return CandidateReviewDocument(
        document_id="DOC-CALENDAR",
        baseline_document_sha256="e" * 64,
        client_name=binding.client_name,
        binding=binding,
        as_of=NOW,
        generated_at=NOW - timedelta(minutes=1),
        editor_state=EditorState(
            client_id=binding.client_id,
            document_id="DOC-CALENDAR",
            baseline_document_sha256="e" * 64,
        ),
    )


def _vehicle_signal(
    evidence: EvidenceRecord,
    dates: tuple[DateValue, ...],
    *,
    candidate_ids: tuple[str, ...] = (),
    last_checked_at: datetime | None = None,
) -> VehicleSignal:
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-1",
        agency="GSA",
        name="OASIS+",
        idv_piid="IDV-1",
        evidence_ids=(evidence.evidence_id,),
    )
    return VehicleSignal(
        signal_id="signal:oasis-on-ramp",
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        title="OASIS+ access-window monitoring",
        agency="GSA",
        kind=VehicleSignalKind.ON_RAMP,
        status=VehicleSignalStatus.UPCOMING,
        vehicle=identity,
        relationship=VehicleRelationship(
            relationship_id="relationship:oasis-access",
            vehicle_id=identity.vehicle_id,
            kind=VehicleRelationshipKind.VEHICLE_ONLY,
            access_posture=VehicleAccessPosture.UNKNOWN,
            evidence_ids=(evidence.evidence_id,),
        ),
        dates=dates,
        candidate_ids=candidate_ids,
        records_show="The official vehicle source states an on-ramp window.",
        may_suggest="The window may create a route for later task-order access.",
        validate_next="Confirm the client's holder or teaming posture.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=last_checked_at or NOW - timedelta(minutes=30),
    )


def test_official_events_build_in_chronological_order_with_complete_census():
    manifest = _manifest()
    query = _query(manifest)
    later = _event_evidence(
        evidence_id="E-LATER",
        record_id="LATER",
        start=date(2026, 10, 1),
        title="Later Summit",
    )
    earlier = _event_evidence(
        evidence_id="E-EARLIER",
        record_id="EARLIER",
        start=date(2026, 8, 1),
        title="Earlier Summit",
    )
    seeds = (
        _event_seed(
            later, manifest, start=date(2026, 10, 1), title="Later Summit",
            external_event_id="LATER", lead_id="LEAD-LATER"),
        _event_seed(
            earlier, manifest, start=date(2026, 8, 1),
            title="Earlier Summit", external_event_id="EARLIER",
            lead_id="LEAD-EARLIER"),
    )
    attempts = _attempts(manifest, leads_by_query={
        query.query_id: ("LEAD-LATER", "LEAD-EARLIER"),
    })

    result = _build(
        evidence=(later, earlier), seeds=seeds,
        manifest=manifest, attempts=attempts)

    assert [item.title for item in result.active_items] == [
        "Earlier Summit", "Later Summit",
    ]
    assert all(item.origin == CalendarItemOrigin.VERIFIED_EVENT
               for item in result.active_items)
    assert len(result.events_for_document) == len(result.audit_events) == 2
    assert result.coverage_receipt.comprehensive is True
    assert len(result.coverage_receipt.rows) == 28


def test_current_event_editions_enforce_cache_and_near_term_rechecks():
    manifest = _manifest()
    query = _query(manifest)

    stale = _event_evidence(
        evidence_id="E-STALE-EVENT",
        record_id="STALE-EVENT",
        start=date(2026, 10, 1),
        title="Stale Federal Event",
        retrieved_at=NOW - timedelta(days=9),
    )
    stale_seed = _event_seed(
        stale,
        manifest,
        start=date(2026, 10, 1),
        title="Stale Federal Event",
        external_event_id="STALE-EVENT",
        last_checked_at=NOW - timedelta(days=8),
    )
    stale_attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: ("LEAD-1",)},
    )
    with pytest.raises(ValueError, match="within seven days"):
        _build(
            evidence=(stale,),
            seeds=(stale_seed,),
            manifest=manifest,
            attempts=stale_attempts,
        )

    near_term = _event_evidence(
        evidence_id="E-NEAR-EVENT",
        record_id="NEAR-EVENT",
        start=date(2026, 8, 10),
        title="Near-Term Federal Event",
        retrieved_at=NOW - timedelta(days=3),
    )
    near_seed = _event_seed(
        near_term,
        manifest,
        start=date(2026, 8, 10),
        title="Near-Term Federal Event",
        external_event_id="NEAR-EVENT",
        last_checked_at=NOW - timedelta(days=2),
    )
    near_attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: ("LEAD-1",)},
    )
    with pytest.raises(ValueError, match="rechecked during the run"):
        _build(
            evidence=(near_term,),
            seeds=(near_seed,),
            manifest=manifest,
            attempts=near_attempts,
        )


def test_researched_event_cap_preserves_calendar_room_for_procurement_dates():
    manifest = _manifest()
    query = _query(manifest)
    event_count = MAX_VISIBLE_RESEARCHED_EVENTS + 1
    evidence_rows = []
    seeds = []
    lead_ids = []
    for index in range(event_count):
        event_start = date(2026, 8, 1) + timedelta(days=index)
        event_id = f"EVENT-{index:02d}"
        lead_id = f"LEAD-{index:02d}"
        evidence = _event_evidence(
            evidence_id=f"E-{event_id}",
            record_id=event_id,
            start=event_start,
            title=f"Federal Event {index:02d}",
        )
        evidence_rows.append(evidence)
        seeds.append(_event_seed(
            evidence,
            manifest,
            start=event_start,
            title=f"Federal Event {index:02d}",
            external_event_id=event_id,
            lead_id=lead_id,
        ))
        lead_ids.append(lead_id)

    notice = _evidence(
        "E-CALENDAR-NOTICE",
        "CALENDAR-NOTICE",
        kind=EvidenceKind.NOTICE,
        excerpt="Responses are due 2026-09-15.",
        url="https://sam.gov/opp/CALENDAR-NOTICE/view",
        source_name="SAM.gov",
    )
    deadline = DateValue(
        date_id="DATE-CALENDAR-NOTICE",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-09-15",
        sort_date=date(2026, 9, 15),
        start=date(2026, 9, 15),
        evidence_ids=(notice.evidence_id,),
    )
    candidate = _candidate(notice, (deadline,))
    attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: tuple(lead_ids)},
    )

    result = _build(
        evidence=(*evidence_rows, notice),
        seeds=tuple(seeds),
        candidates=(candidate,),
        manifest=manifest,
        attempts=attempts,
    )

    assert sum(
        item.origin == CalendarItemOrigin.VERIFIED_EVENT
        for item in result.active_items
    ) == MAX_VISIBLE_RESEARCHED_EVENTS
    assert any(
        item.origin == CalendarItemOrigin.CANDIDATE_DATE
        for item in result.active_items
    )
    omitted_events = tuple(
        row for row in result.deferred_items
        if row.item.origin == CalendarItemOrigin.VERIFIED_EVENT
        and row.disposition == CalendarDisposition.CONTENT_LIMIT
    )
    assert len(omitted_events) == 1


@pytest.mark.parametrize("mode", ["aggregator", "untrusted"])
def test_discovery_or_untrusted_official_flags_cannot_publish_events(mode):
    manifest = _manifest()
    query = _query(manifest)
    if mode == "aggregator":
        evidence = _event_evidence(
            kind=EvidenceKind.WEB_DISCOVERY,
            official=False,
            primary=False,
            assertions=(),
        )
    else:
        evidence = _event_evidence(url="https://events.bad.example/summit")
    seed = _event_seed(evidence, manifest)
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})

    with pytest.raises(ValueError, match="(?i)(authoritative|authority domain)"):
        _build(
            evidence=(evidence,), seeds=(seed,), manifest=manifest,
            attempts=attempts)


def test_exact_event_flagship_and_procurement_horizon_boundaries():
    manifest = _manifest()
    query = _query(manifest)
    exact_12 = add_calendar_months(NOW.date(), 12)
    beyond_12 = exact_12 + timedelta(days=1)
    standard_a = _event_evidence(start=exact_12)
    standard_b = _event_evidence(
        evidence_id="E-BEYOND", record_id="BEYOND", start=beyond_12,
        title="Beyond Event")
    standard_seeds = (
        _event_seed(
            standard_a, manifest, start=exact_12, lead_id="LEAD-EXACT"),
        _event_seed(
            standard_b, manifest, start=beyond_12, title="Beyond Event",
            external_event_id="BEYOND", lead_id="LEAD-BEYOND"),
    )
    attempts = _attempts(manifest, leads_by_query={
        query.query_id: ("LEAD-EXACT", "LEAD-BEYOND"),
    })
    standard = _build(
        evidence=(standard_a, standard_b), seeds=standard_seeds,
        manifest=manifest, attempts=attempts)
    assert [item.timing.sort_date for item in standard.active_items] == [exact_12]
    assert standard.deferred_items[0].disposition \
        == CalendarDisposition.OUT_OF_HORIZON

    exact_18 = add_calendar_months(NOW.date(), 18)
    flagship_evidence = _event_evidence(
        evidence_id="E-FLAGSHIP", record_id="FLAGSHIP", start=exact_18,
        title="Flagship Summit", extra="This is the official flagship event.",
        assertions=(EvidenceAssertion.OFFICIAL_EVENT,
                    EvidenceAssertion.EVENT_FLAGSHIP))
    flagship = _event_seed(
        flagship_evidence, manifest, start=exact_18,
        title="Flagship Summit", external_event_id="FLAGSHIP",
        lead_id="LEAD-FLAGSHIP", flagship=True)
    flagship_attempts = _attempts(manifest, leads_by_query={
        query.query_id: ("LEAD-FLAGSHIP",),
    })
    assert _build(
        evidence=(flagship_evidence,), seeds=(flagship,),
        manifest=manifest, attempts=flagship_attempts).active_items

    exact_24 = add_calendar_months(NOW.date(), 24)
    award_excerpt = f"The current award period ends {exact_24.isoformat()}."
    award = _evidence(
        "E-AWARD", "AWARD-1", kind=EvidenceKind.AWARD,
        excerpt=award_excerpt,
        url="https://www.usaspending.gov/award/AWARD-1",
        assertions=(EvidenceAssertion.AWARD_PERIOD_END,),
        source_name="USAspending.gov")
    clock = DateValue(
        date_id="DATE-AWARD-END", label="Award-end research clock",
        kind=DateKind.AWARD_END_RESEARCH_CLOCK,
        status=DateStatus.RESEARCH_CLOCK,
        precision=DatePrecision.DAY,
        source_text=exact_24.isoformat(), sort_date=exact_24,
        start=exact_24, evidence_ids=(award.evidence_id,))
    result = _build(
        evidence=(award,), seeds=(_procurement_seed(award, clock),),
        manifest=manifest)
    assert result.active_items[0].timing.status == DateStatus.RESEARCH_CLOCK


def test_candidate_procurement_dates_preserve_precision_and_sort_order():
    notice = _evidence(
        "E-NOTICE", "NOTICE-1", kind=EvidenceKind.NOTICE,
        excerpt="Responses are due 2026-08-15.",
        url="https://sam.gov/opp/NOTICE-1/view",
        source_name="SAM.gov")
    forecast = _evidence(
        "E-FORECAST", "FORECAST-1", kind=EvidenceKind.AGENCY_FORECAST,
        excerpt="The anticipated solicitation is FY2027 Q2.",
        url="https://agency.gov/forecast/FORECAST-1",
        source_name="Agency forecast")
    response = DateValue(
        date_id="DATE-RESPONSE", label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="2026-08-15",
        sort_date=date(2026, 8, 15), start=date(2026, 8, 15),
        evidence_ids=(notice.evidence_id,))
    forecast_date = DateValue(
        date_id="DATE-FORECAST", label="Forecast solicitation",
        kind=DateKind.FORECAST_SOLICITATION,
        status=DateStatus.ANTICIPATED,
        precision=DatePrecision.QUARTER,
        source_text="FY2027 Q2", sort_date=date(2027, 1, 1),
        evidence_ids=(forecast.evidence_id,))
    candidate = _candidate(
        notice, (forecast_date, response),
        supporting_evidence_ids=(forecast.evidence_id,))

    result = _build(
        evidence=(notice, forecast), candidates=(candidate,))

    assert [item.timing.date_id for item in result.active_items] == [
        "DATE-RESPONSE", "DATE-FORECAST",
    ]
    assert result.active_items[1].timing.precision == DatePrecision.QUARTER
    assert result.active_items[1].timing.start is None


def test_vehicle_on_ramp_projects_independently_without_becoming_candidate():
    excerpt = (
        "OASIS+ parent IDV IDV-1 has an official on-ramp. "
        "The on-ramp opens August 1, 2026. "
        "The on-ramp closes September 1, 2026."
    )
    evidence = _evidence(
        "E-VEHICLE",
        "IDV-1",
        kind=EvidenceKind.VEHICLE,
        excerpt=excerpt,
        url="https://www.gsa.gov/oasis/idv-1",
        source_name="GSA OASIS+ program office",
        assertion_quotes=(
            (EvidenceAssertion.VEHICLE_IDENTITY,
             "OASIS+ parent IDV IDV-1"),
            (EvidenceAssertion.VEHICLE_ON_RAMP,
             "OASIS+ parent IDV IDV-1 has an official on-ramp"),
            (EvidenceAssertion.VEHICLE_ON_RAMP_OPEN,
             "The on-ramp opens August 1, 2026"),
            (EvidenceAssertion.VEHICLE_ON_RAMP_CLOSE,
             "The on-ramp closes September 1, 2026"),
        ),
    )
    opened = DateValue(
        date_id="DATE-ON-RAMP-OPEN", label="On-ramp opens",
        kind=DateKind.ON_RAMP_OPEN, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="August 1, 2026",
        sort_date=date(2026, 8, 1), start=date(2026, 8, 1),
        evidence_ids=(evidence.evidence_id,))
    closed = DateValue(
        date_id="DATE-ON-RAMP-CLOSE", label="On-ramp closes",
        kind=DateKind.ON_RAMP_CLOSE, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="September 1, 2026",
        sort_date=date(2026, 9, 1), start=date(2026, 9, 1),
        evidence_ids=(evidence.evidence_id,))
    signal = _vehicle_signal(evidence, (opened, closed))

    result = _build(evidence=(evidence,), vehicle_signals=(signal,))

    assert result.candidate_ids == ()
    assert [item.origin for item in result.active_items] == [
        CalendarItemOrigin.VEHICLE_SIGNAL,
        CalendarItemOrigin.VEHICLE_SIGNAL,
    ]
    assert {item.vehicle_signal_id for item in result.active_items} \
        == {signal.signal_id}
    hydrated = result.hydrate_document(_document(result.binding))
    projection = project_candidate_review(hydrated)
    assert projection.candidate_count == 0
    assert projection.vehicle_signals == (signal,)
    assert all(item.origin == CalendarItemOrigin.VEHICLE_SIGNAL
               for item in projection.calendar_items)


@pytest.mark.parametrize(
    ("checked_age", "retrieved_age", "error"),
    (
        (timedelta(days=8), timedelta(days=9), "within seven days"),
        (timedelta(days=2), timedelta(days=3), "rechecked during the run"),
    ),
)
def test_current_vehicle_on_ramp_enforces_watch_freshness(
    checked_age,
    retrieved_age,
    error,
):
    excerpt = (
        "OASIS+ parent IDV IDV-1 has an official on-ramp. "
        "The on-ramp opens August 1, 2026."
    )
    evidence = _evidence(
        "E-VEHICLE-FRESHNESS",
        "IDV-1",
        kind=EvidenceKind.VEHICLE,
        excerpt=excerpt,
        url="https://www.gsa.gov/oasis/idv-1",
        source_name="GSA OASIS+ program office",
        assertion_quotes=(
            (EvidenceAssertion.VEHICLE_IDENTITY,
             "OASIS+ parent IDV IDV-1"),
            (EvidenceAssertion.VEHICLE_ON_RAMP,
             "OASIS+ parent IDV IDV-1 has an official on-ramp"),
            (EvidenceAssertion.VEHICLE_ON_RAMP_OPEN,
             "The on-ramp opens August 1, 2026"),
        ),
        retrieved_at=NOW - retrieved_age,
    )
    opens = DateValue(
        date_id="DATE-VEHICLE-FRESHNESS",
        label="On-ramp opens",
        kind=DateKind.ON_RAMP_OPEN,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="August 1, 2026",
        sort_date=date(2026, 8, 1),
        start=date(2026, 8, 1),
        evidence_ids=(evidence.evidence_id,),
    )
    signal = _vehicle_signal(
        evidence,
        (opens,),
        last_checked_at=NOW - checked_age,
    )

    with pytest.raises(ValueError, match=error):
        _build(evidence=(evidence,), vehicle_signals=(signal,))


def test_ordering_and_option_ends_are_monitor_clocks_not_recompetes():
    excerpt = (
        "OASIS+ parent IDV IDV-1 has a last date to order of October 1, 2026. "
        "The vehicle option ends December 1, 2026."
    )
    evidence = _evidence(
        "E-VEHICLE-CLOCK", "IDV-1", kind=EvidenceKind.VEHICLE,
        excerpt=excerpt, url="https://www.gsa.gov/oasis/idv-1",
        source_name="GSA OASIS+ program office",
        assertion_quotes=(
            (EvidenceAssertion.VEHICLE_IDENTITY,
             "OASIS+ parent IDV IDV-1"),
            (EvidenceAssertion.VEHICLE_ORDERING_PERIOD,
             "last date to order of October 1, 2026"),
            (EvidenceAssertion.VEHICLE_OPTION_END,
             "vehicle option ends December 1, 2026"),
        ))
    ordering_end = DateValue(
        date_id="DATE-ORDERING-END", label="Last date to order",
        kind=DateKind.ORDERING_PERIOD_END, status=DateStatus.MONITOR,
        precision=DatePrecision.DAY, source_text="October 1, 2026",
        sort_date=date(2026, 10, 1), start=date(2026, 10, 1),
        evidence_ids=(evidence.evidence_id,))
    option_end = DateValue(
        date_id="DATE-OPTION-END", label="Vehicle option-end research clock",
        kind=DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK,
        status=DateStatus.RESEARCH_CLOCK,
        precision=DatePrecision.DAY, source_text="December 1, 2026",
        sort_date=date(2026, 12, 1), start=date(2026, 12, 1),
        evidence_ids=(evidence.evidence_id,))
    base_signal = _vehicle_signal(evidence, (ordering_end, option_end))
    signal = base_signal.model_copy(update={
        "kind": VehicleSignalKind.ORDERING_PERIOD,
    })

    result = _build(evidence=(evidence,), vehicle_signals=(signal,))
    assert [item.timing.status for item in result.active_items] == [
        DateStatus.MONITOR,
        DateStatus.RESEARCH_CLOCK,
    ]
    assert all(item.timing.kind != DateKind.CONFIRMED_RECOMPETE
               for item in result.active_items)

    unsupported_recompete = DateValue(
        date_id="DATE-FORGED-RECOMPETE", label="Confirmed recompete",
        kind=DateKind.CONFIRMED_RECOMPETE, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="October 1, 2026",
        sort_date=date(2026, 10, 1), start=date(2026, 10, 1),
        evidence_ids=(evidence.evidence_id,))
    forged = base_signal.model_copy(update={
        "kind": VehicleSignalKind.CONFIRMED_RECOMPETE,
        "dates": (unsupported_recompete,),
    })
    with pytest.raises(ValueError, match="(?i)(recompete|role assertion)"):
        _build(evidence=(evidence,), vehicle_signals=(forged,))


def test_semantic_duplicates_merge_sources_but_conflicting_dates_fail_closed():
    manifest = _manifest()
    query = _query(manifest)
    first = _event_evidence(evidence_id="E-ONE", record_id="ONE")
    second = _event_evidence(
        evidence_id="E-TWO", record_id="TWO",
        url="https://events.example.org/data-summit/agenda")
    seeds = (
        _event_seed(first, manifest, lead_id="LEAD-ONE"),
        _event_seed(second, manifest, lead_id="LEAD-TWO"),
    )
    attempts = _attempts(manifest, leads_by_query={
        query.query_id: ("LEAD-ONE", "LEAD-TWO"),
    })
    merged = _build(
        evidence=(first, second), seeds=seeds,
        manifest=manifest, attempts=attempts)
    assert len(merged.audit_events) == 1
    assert set(merged.audit_events[0].evidence_ids) == {"E-ONE", "E-TWO"}

    changed = _event_evidence(
        evidence_id="E-CHANGED", record_id="CHANGED",
        start=date(2026, 9, 2))
    changed_seed = _event_seed(
        changed, manifest, start=date(2026, 9, 2), lead_id="LEAD-CHANGED")
    conflict_attempts = _attempts(manifest, leads_by_query={
        query.query_id: ("LEAD-ONE", "LEAD-CHANGED"),
    })
    with pytest.raises(ValueError, match="conflicting observations"):
        _build(
            evidence=(first, changed), seeds=(seeds[0], changed_seed),
            manifest=manifest, attempts=conflict_attempts)


@pytest.mark.parametrize(
    ("status", "assertion", "timing_mode"),
    [
        (EventLifecycleStatus.CANCELLED,
         EvidenceAssertion.EVENT_CANCELLED, "future"),
        (EventLifecycleStatus.DATE_TBD,
         EvidenceAssertion.EVENT_DATE_TBD, "none"),
        (EventLifecycleStatus.SCHEDULED,
         EvidenceAssertion.OFFICIAL_EVENT, "past"),
    ],
)
def test_cancelled_tbd_and_past_events_stay_in_audit_only(
    status,
    assertion,
    timing_mode,
):
    manifest = _manifest()
    query = _query(manifest)
    start = date(2026, 9, 1) if timing_mode != "past" else date(2026, 1, 1)
    assertions = (EvidenceAssertion.OFFICIAL_EVENT, assertion) \
        if assertion != EvidenceAssertion.OFFICIAL_EVENT \
        else (EvidenceAssertion.OFFICIAL_EVENT,)
    extra = {
        EventLifecycleStatus.CANCELLED: "The event is cancelled.",
        EventLifecycleStatus.DATE_TBD: "The event date is TBD.",
    }.get(status, "")
    evidence = _event_evidence(
        start=start, extra=extra, assertions=assertions)
    seed = _event_seed(
        evidence, manifest, start=start, lifecycle_status=status,
        timing=None if timing_mode == "none" else ...)
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})

    result = _build(
        evidence=(evidence,), seeds=(seed,),
        manifest=manifest, attempts=attempts)

    assert not result.active_items
    assert not result.events_for_document
    assert len(result.audit_events) == 1
    if timing_mode == "past":
        assert result.audit_events[0].lifecycle_status \
            == EventLifecycleStatus.COMPLETED


def test_coverage_distinguishes_missing_failed_and_successful_zero_results():
    manifest = _manifest()
    required = tuple(item for item in manifest.queries if item.required)

    missing = _build(manifest=manifest, attempts=())
    assert missing.coverage_receipt.comprehensive is False
    assert sum(row.state == CoverageState.NOT_RUN
               for row in missing.coverage_receipt.rows) == len(required)

    zero = _build(manifest=manifest, attempts=_attempts(manifest))
    assert zero.coverage_receipt.comprehensive is True
    assert all(
        row.records_returned == 0
        for row in zero.coverage_receipt.rows
        if row.state == CoverageState.RETURNED
    )

    failed = _build(
        manifest=manifest,
        attempts=_attempts(
            manifest, failed_query_ids=frozenset({required[0].query_id})))
    assert failed.coverage_receipt.comprehensive is False
    assert any(row.state == CoverageState.FAILED
               for row in failed.coverage_receipt.rows)


def test_recent_watch_check_still_runs_while_scope_exclusion_stays_explicit():
    frame = EventResearchFrame(
        binding=_binding(),
        revision_sha256="d" * 64,
        capabilities_missions=("federal geospatial operations",),
        trusted_organizer_domains=("events.example.org",),
    )
    selection = select_watch_targets(
        load_event_watch_universe(),
        WatchFrameTags(capability_tags=("geospatial",)),
        as_of=NOW,
        last_checked_at={"esri": NOW - timedelta(days=1)},
    )
    manifest = build_event_query_manifest(
        frame,
        NOW,
        watch_selection=selection,
        watch_universe_digest=watch_universe_sha256(),
    )
    discovery = run_event_discovery(manifest, lambda _query: (), NOW)

    receipt = _build(
        manifest=manifest,
        attempts=discovery.attempts,
    ).coverage_receipt
    esri_rows = tuple(
        row for row in receipt.rows if row.watch_target_id == "esri"
    )
    excluded_rows = tuple(
        row for row in receipt.rows
        if row.watch_target_id in manifest.scope_excluded_watch_target_ids
    )

    assert esri_rows
    assert all(row.state == CoverageState.RETURNED for row in esri_rows)
    assert all(row.query_required for row in esri_rows)
    assert all(row.records_returned == 0 for row in esri_rows)
    assert excluded_rows
    assert all(
        row.state == CoverageState.SCOPE_EXCLUDED for row in excluded_rows
    )
    assert all(not row.query_required for row in excluded_rows)
    assert all(
        "outside the approved client frame" in row.public_detail
        for row in excluded_rows
    )


def test_discovery_index_lead_requires_separate_official_event_evidence():
    frame = EventResearchFrame(
        binding=_binding(),
        revision_sha256="d" * 64,
        capabilities_missions=("federal artificial intelligence",),
        trusted_organizer_domains=("events.example.org",),
    )
    selection = select_watch_targets(
        load_event_watch_universe(),
        WatchFrameTags(capability_tags=("artificial-intelligence",)),
        as_of=NOW,
        last_checked_at={},
    )
    manifest = build_event_query_manifest(
        frame,
        NOW,
        watch_selection=selection,
        watch_universe_digest=watch_universe_sha256(),
    )
    query = next(
        row for row in manifest.queries
        if row.watch_target_id == "dgi-ai-for-defense-summit"
        and row.source_class == EventSourceClass.DISCOVERY_INDEX
    )
    evidence = _event_evidence(
        evidence_id="E-DGI-OFFICIAL",
        record_id="DGI-AI-2026",
        title="AI for Defense Summit",
        organizer="Official Event Host",
        url="https://events.example.org/ai-for-defense-2026",
    )
    seed = _event_seed(
        evidence,
        manifest,
        title="AI for Defense Summit",
        organizer="Official Event Host",
        external_event_id="DGI-AI-2026",
        query_id=query.query_id,
        lead_id="LEAD-DGI-INDEX",
    )
    attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: ("LEAD-DGI-INDEX",)},
    )

    result = _build(
        evidence=(evidence,),
        seeds=(seed,),
        manifest=manifest,
        attempts=attempts,
    )

    assert [event.title for event in result.events_for_document] == [
        "AI for Defense Summit",
    ]
    attribution = next(
        row for row in result.event_attributions
        if row.query_id == query.query_id
    )
    assert attribution.evidence_id == evidence.evidence_id

    discovery_page = _event_evidence(
        evidence_id="E-DGI-DISCOVERY-PAGE",
        record_id="DGI-AI-2026-DISCOVERY",
        title="AI for Defense Summit",
        organizer="Event Index",
        url="https://www.govevents.com/details/ai-for-defense-2026",
    )
    discovery_seed = _event_seed(
        discovery_page,
        manifest,
        title="AI for Defense Summit",
        organizer="Event Index",
        external_event_id="DGI-AI-2026-DISCOVERY",
        query_id=query.query_id,
        lead_id="LEAD-DGI-DISCOVERY-PAGE",
    )
    discovery_attempts = _attempts(
        manifest,
        leads_by_query={
            query.query_id: ("LEAD-DGI-DISCOVERY-PAGE",),
        },
    )
    with pytest.raises(ValueError, match="(?i)authority"):
        _build(
            evidence=(discovery_page,),
            seeds=(discovery_seed,),
            manifest=manifest,
            attempts=discovery_attempts,
        )


def test_forged_discovery_lineage_and_unsupported_attendance_fail_closed():
    manifest = _manifest()
    query = _query(manifest)
    evidence = _event_evidence(
        extra="DHS will attend the event.")
    seed = _event_seed(
        evidence, manifest,
        relevance="DHS will attend and the program matches the capability.")
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("DIFFERENT-LEAD",)})
    with pytest.raises(ValueError, match="discovery lead"):
        _build(
            evidence=(evidence,), seeds=(seed,),
            manifest=manifest, attempts=attempts)

    valid_attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})
    with pytest.raises(ValueError, match="unsupported agency attendance"):
        _build(
            evidence=(evidence,), seeds=(seed,),
            manifest=manifest, attempts=valid_attempts)

    common_wording = _event_evidence(
        extra="DHS is attending the event.")
    common_seed = _event_seed(
        common_wording,
        manifest,
        relevance="DHS is attending and the program matches the capability.",
    )
    with pytest.raises(ValueError, match="unsupported agency attendance"):
        _build(
            evidence=(common_wording,),
            seeds=(common_seed,),
            manifest=manifest,
            attempts=valid_attempts,
        )


@pytest.mark.parametrize(
    "phrase",
    (
        "DHS attends the event.",
        "DHS attended the event.",
        "DHS confirmed attendance.",
        "DHS will be at the event.",
        "DHS is expected at the summit.",
        "DHS joins the summit.",
        "DHS representatives are on the agenda.",
        "DHS will be attending.",
        "DHS is an exhibitor.",
        "DHS plans on attending.",
        "DHS will join the summit.",
        "DHS has registered.",
        "DHS is on the agenda.",
        "DHS will have a booth.",
        "The DHS delegation will be present.",
        "DHS representatives are expected.",
        "Agency participation is expected.",
        "DHS will appear at the conference.",
        "DHS will send representatives.",
        "DHS has a booth.",
        "DHS is presenting.",
        "DHS will be represented.",
        "DHS officials are slated for the summit.",
        "DHS is among the participating agencies.",
        "DHS appears on the speaker roster.",
        "DHS is listed as an exhibitor.",
        "DHS officials will take part.",
        "DHS is scheduled for a panel.",
        "DHS will host a panel.",
        "DHS will deliver remarks.",
        "DHS will present.",
        "DHS executives are appearing at the event.",
        "DHS is sending a delegation.",
        "DHS has confirmed its presence.",
        "Participating agencies include DHS.",
        "The participant roster includes DHS.",
        "DHS is a listed exhibitor.",
        "DHS appears at the event.",
        "DHS is appearing at the event.",
        "DHS sends representatives.",
        "DHS is sending representatives.",
        "DHS is represented at the event.",
        "DHS takes part.",
        "DHS is on a panel.",
        "Remarks by DHS are scheduled.",
        "DHS delivers remarks.",
        "DHS is present at the event.",
    ),
)
def test_common_attendance_phrasing_requires_structured_official_proof(
    phrase: str,
):
    manifest = _manifest()
    query = _query(manifest)
    evidence = _event_evidence(extra=phrase)
    seed = _event_seed(
        evidence,
        manifest,
        relevance=f"{phrase} The program matches the capability frame.",
    )
    attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: ("LEAD-1",)},
    )

    with pytest.raises(ValueError, match="unsupported agency attendance"):
        _build(
            evidence=(evidence,),
            seeds=(seed,),
            manifest=manifest,
            attempts=attempts,
        )


def test_role_links_and_named_attendance_require_exact_official_evidence():
    manifest = _manifest()
    query = _query(manifest)
    event = _event_evidence(
        extra="DHS will attend the event.",
        assertions=(EvidenceAssertion.OFFICIAL_EVENT,
                    EvidenceAssertion.AGENCY_ATTENDANCE))
    registration = _evidence(
        "E-REGISTER", "REGISTER", kind=EvidenceKind.ORGANIZER_EVENT,
        excerpt="Official registration for Federal Data Summit.",
        url="https://events.example.org/data-summit/register",
        assertions=(), source_name="Example Organizer")
    seed = _event_seed(
        event, manifest,
        relevance="DHS will attend and the program matches the capability.",
        attending_agencies=("DHS",),
        attendance_evidence_ids=(event.evidence_id,),
        registration_url=str(registration.source_url),
        registration_evidence_ids=(registration.evidence_id,))
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})

    assert _build(
        evidence=(event, registration), seeds=(seed,),
        manifest=manifest, attempts=attempts).active_items

    forged = EventSeed.model_validate({
        **seed.model_dump(mode="python"),
        "registration_url": "https://events.example.org/other/register",
    })
    with pytest.raises(ValueError, match="registration link"):
        _build(
            evidence=(event, registration), seeds=(forged,),
            manifest=manifest, attempts=attempts)


@pytest.mark.parametrize(
    ("source_class", "role_url_field", "role_evidence_field", "role_path"),
    (
        (
            EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE,
            "agenda_url",
            "agenda_evidence_ids",
            "agenda",
        ),
        (
            EventSourceClass.OFFICIAL_REGISTRATION_VENUE,
            "registration_url",
            "registration_evidence_ids",
            "register",
        ),
    ),
)
def test_role_query_credits_the_exact_event_role_page(
    source_class: EventSourceClass,
    role_url_field: str,
    role_evidence_field: str,
    role_path: str,
):
    manifest = _manifest()
    query = next(
        item for item in manifest.queries
        if item.required and item.source_class == source_class
    )
    anchor = _event_evidence(evidence_id="E-ANCHOR")
    wrong = _evidence(
        "E-A-WRONG-ROLE",
        "WRONG-ROLE",
        kind=EvidenceKind.ORGANIZER_EVENT,
        excerpt="Official role page for Unrelated Workshop.",
        url=f"https://events.example.org/unrelated/{role_path}",
        source_name="Example Organizer",
    )
    right = _evidence(
        "E-Z-RIGHT-ROLE",
        "RIGHT-ROLE",
        kind=EvidenceKind.ORGANIZER_EVENT,
        excerpt=f"Official {role_path} page for Federal Data Summit.",
        url=f"https://events.example.org/data-summit/{role_path}",
        source_name="Example Organizer",
    )
    seed_payload = _event_seed(
        anchor,
        manifest,
        query_id=query.query_id,
        lead_id="LEAD-ROLE",
    ).model_dump(mode="python")
    seed_payload.update({
        role_url_field: str(right.source_url),
        role_evidence_field: (wrong.evidence_id, right.evidence_id),
    })
    seed = EventSeed.model_validate(seed_payload)
    result = _build(
        evidence=(anchor, wrong, right),
        seeds=(seed,),
        manifest=manifest,
        attempts=_attempts(
            manifest,
            leads_by_query={query.query_id: ("LEAD-ROLE",)},
        ),
    )

    assert result.event_attributions[0].evidence_id == right.evidence_id


def test_event_acceptance_rejects_discovery_support_and_untrusted_role_hosts():
    manifest = _manifest()
    query = _query(manifest)
    event = _event_evidence()
    discovery = _evidence(
        "E-DISCOVERY",
        "DISCOVERY",
        kind=EvidenceKind.WEB_DISCOVERY,
        excerpt="Search result mentioning Federal Data Summit.",
        url="https://search.example.com/result",
        official=False,
        primary=False,
    )
    mixed_seed = EventSeed.model_validate({
        **_event_seed(event, manifest).model_dump(mode="python"),
        "evidence_ids": (event.evidence_id, discovery.evidence_id),
    })
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})
    with pytest.raises(ValueError, match="discovery or aggregator"):
        _build(
            evidence=(event, discovery),
            seeds=(mixed_seed,),
            manifest=manifest,
            attempts=attempts,
        )

    untrusted_registration = _evidence(
        "E-UNTRUSTED-REGISTER",
        "UNTRUSTED-REGISTER",
        kind=EvidenceKind.ORGANIZER_EVENT,
        excerpt="Registration for Federal Data Summit.",
        url="https://tickets.untrusted.com/register",
    )
    role_seed = _event_seed(
        event,
        manifest,
        registration_url=str(untrusted_registration.source_url),
        registration_evidence_ids=(untrusted_registration.evidence_id,),
    )
    with pytest.raises(ValueError, match="approved authority domain"):
        _build(
            evidence=(event, untrusted_registration),
            seeds=(role_seed,),
            manifest=manifest,
            attempts=attempts,
        )


def test_hydration_and_projection_retain_calendar_coverage_and_sources():
    manifest = _manifest()
    query = _query(manifest)
    evidence = _event_evidence()
    seed = _event_seed(evidence, manifest)
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})
    result = _build(
        evidence=(evidence,), seeds=(seed,),
        manifest=manifest, attempts=attempts)

    hydrated = result.hydrate_document(_document(manifest.binding))
    projection = project_candidate_review(hydrated, clean_export=True)

    assert projection.calendar_items == result.active_items
    assert projection.calendar_events == result.events_for_document
    assert projection.coverage == result.coverage_receipt.rows
    assert {item.evidence_id for item in projection.source_evidence} \
        == {evidence.evidence_id}


def test_sparse_calendar_has_no_filler_and_global_date_ids_fail_closed():
    sparse = _build()
    assert sparse.active_items == ()
    assert sparse.deferred_items == ()
    assert sparse.audit_events == ()

    notice = _evidence(
        "E-NOTICE", "NOTICE-1", kind=EvidenceKind.NOTICE,
        excerpt="Responses are due 2026-08-15.",
        url="https://sam.gov/opp/NOTICE-1/view",
        source_name="SAM.gov")
    timing = DateValue(
        date_id="DATE-DUPLICATE", label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="2026-08-15",
        sort_date=date(2026, 8, 15), start=date(2026, 8, 15),
        evidence_ids=(notice.evidence_id,))
    candidates = (
        _candidate(notice, (timing,), candidate_id="C-1"),
        _candidate(notice, (timing,), candidate_id="C-2"),
    )
    with pytest.raises(ValueError, match="globally unique"):
        _build(evidence=(notice,), candidates=candidates)

    with pytest.raises(ValidationError, match="absent from source_text"):
        DateValue(
            date_id="DATE-FORGED",
            label="Event date",
            kind=DateKind.EVENT_START,
            status=DateStatus.CONFIRMED,
            precision=DatePrecision.DAY,
            source_text="2026-09-01",
            sort_date=date(2026, 9, 2),
            start=date(2026, 9, 2),
            evidence_ids=(notice.evidence_id,),
        )

    misleading_notice = EvidenceRecord.model_validate({
        **notice.model_dump(mode="python"),
        "published_date": date(2026, 8, 16),
    })
    unsupported_timing = DateValue(
        date_id="DATE-UNSUPPORTED",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-08-16",
        sort_date=date(2026, 8, 16),
        start=date(2026, 8, 16),
        evidence_ids=(misleading_notice.evidence_id,),
    )
    unsupported_candidate = _candidate(
        misleading_notice,
        (unsupported_timing,),
        candidate_id="C-UNSUPPORTED-DATE",
    )
    with pytest.raises(ValueError, match="not visible"):
        _build(
            evidence=(misleading_notice,),
            candidates=(unsupported_candidate,),
        )


def test_event_contract_rejects_flagship_without_exact_official_assertion():
    manifest = _manifest()
    evidence = _event_evidence()
    with pytest.raises(ValidationError, match="flagship"):
        EventSeed.model_validate({
            **_event_seed(evidence, manifest).model_dump(mode="python"),
            "flagship": True,
        })


def _audit_rejects(callable_) -> bool:
    """Return whether a deliberately invalid audit object fails closed."""

    try:
        callable_()
    except (TypeError, ValueError, ValidationError):
        return True
    return False


def test_required_unattempted_states_are_incomplete_and_not_called_attempted():
    manifest = _manifest()
    required = tuple(query for query in manifest.queries if query.required)
    observed = []

    for state in (CoverageState.SCOPE_EXCLUDED, CoverageState.NOT_USED):
        attempts = list(_attempts(manifest))
        index = manifest.queries.index(required[0])
        attempts[index] = EventQueryAttempt(
            query_id=required[0].query_id,
            state=state,
        )
        receipt = _build(
            manifest=manifest,
            attempts=tuple(attempts),
        ).coverage_receipt
        observed.append((
            receipt.comprehensive,
            "attempted" in receipt.public_summary.casefold(),
        ))

    assert observed == [(False, False), (False, False)]


def test_each_seed_query_requires_its_own_discovery_lead_lineage():
    manifest = _manifest()
    first, unrelated = tuple(
        query for query in manifest.queries if query.required
    )[:2]
    evidence = _event_evidence()
    seed = EventSeed.model_validate({
        **_event_seed(evidence, manifest).model_dump(mode="python"),
        "query_ids": (first.query_id, unrelated.query_id),
        "discovery_lead_ids": ("LEAD-FIRST",),
    })
    attempts = _attempts(manifest, leads_by_query={
        first.query_id: ("LEAD-FIRST",),
        unrelated.query_id: ("LEAD-UNRELATED",),
    })

    assert _audit_rejects(lambda: _build(
        evidence=(evidence,),
        seeds=(seed,),
        manifest=manifest,
        attempts=attempts,
    ))


def test_merged_event_credits_each_query_only_with_its_seed_evidence():
    manifest = _manifest()
    first, second = tuple(
        query for query in manifest.queries if query.required
    )[:2]
    first_evidence = _event_evidence(
        evidence_id="E-QUERY-A",
        record_id="QUERY-A",
        kind=EvidenceKind.AGENCY_ANNOUNCEMENT,
        url="https://agency.gov/events/data-summit",
    )
    second_evidence = _event_evidence(
        evidence_id="E-QUERY-B",
        record_id="QUERY-B",
        url="https://events.example.org/data-summit/agenda",
    )
    seeds = (
        _event_seed(
            first_evidence,
            manifest,
            query_id=first.query_id,
            lead_id="LEAD-QUERY-A",
        ),
        _event_seed(
            second_evidence,
            manifest,
            query_id=second.query_id,
            lead_id="LEAD-QUERY-B",
        ),
    )
    attempts = _attempts(manifest, leads_by_query={
        first.query_id: ("LEAD-QUERY-A",),
        second.query_id: ("LEAD-QUERY-B",),
    })
    result = _build(
        evidence=(first_evidence, second_evidence),
        seeds=seeds,
        manifest=manifest,
        attempts=attempts,
    )
    rows = {
        (row.source, row.query_family): row
        for row in result.coverage_receipt.rows
    }

    assert rows[(
        first.source_class.value,
        first.query_family.value,
    )].accepted_evidence_ids == (first_evidence.evidence_id,)
    assert rows[(
        second.source_class.value,
        second.query_family.value,
    )].accepted_evidence_ids == (second_evidence.evidence_id,)
    assert set(result.coverage_receipt.accepted_evidence_ids) == {
        first_evidence.evidence_id,
        second_evidence.evidence_id,
    }


def test_recurring_same_year_editions_stay_separate_and_exact_duplicates_merge():
    manifest = _manifest()
    query = _query(manifest)
    september = date(2026, 9, 1)
    november = date(2026, 11, 1)

    exact_one = _event_evidence(
        evidence_id="E-EXACT-ONE",
        record_id="EXACT-ONE",
        start=september,
    )
    exact_two = _event_evidence(
        evidence_id="E-EXACT-TWO",
        record_id="EXACT-TWO",
        start=september,
        url="https://events.example.org/data-summit/agenda",
    )
    exact_seeds = (
        _event_seed(
            exact_one, manifest, start=september,
            external_event_id=None, lead_id="LEAD-EXACT-ONE"),
        _event_seed(
            exact_two, manifest, start=september,
            external_event_id=None, lead_id="LEAD-EXACT-TWO"),
    )
    exact = _build(
        evidence=(exact_one, exact_two),
        seeds=exact_seeds,
        manifest=manifest,
        attempts=_attempts(manifest, leads_by_query={
            query.query_id: ("LEAD-EXACT-ONE", "LEAD-EXACT-TWO"),
        }),
    )

    later = _event_evidence(
        evidence_id="E-RECURRING-LATER",
        record_id="RECURRING-LATER",
        start=november,
        url="https://events.example.org/data-summit/november",
    )
    recurring_seeds = (
        _event_seed(
            exact_one, manifest, start=september,
            external_event_id=None, lead_id="LEAD-SEPTEMBER"),
        _event_seed(
            later, manifest, start=november,
            external_event_id=None, lead_id="LEAD-NOVEMBER"),
    )
    try:
        recurring_count: int | str = len(_build(
            evidence=(exact_one, later),
            seeds=recurring_seeds,
            manifest=manifest,
            attempts=_attempts(manifest, leads_by_query={
                query.query_id: ("LEAD-SEPTEMBER", "LEAD-NOVEMBER"),
            }),
        ).audit_events)
    except ValueError:
        recurring_count = "rejected-as-conflict"

    assert (
        len(exact.audit_events),
        set(exact.audit_events[0].evidence_ids),
        recurring_count,
    ) == (
        1,
        {"E-EXACT-ONE", "E-EXACT-TWO"},
        2,
    )

    cross_id_seeds = (
        _event_seed(
            exact_one,
            manifest,
            start=september,
            external_event_id="SOURCE-A",
            lead_id="LEAD-CROSS-A",
        ),
        _event_seed(
            exact_two,
            manifest,
            start=september,
            external_event_id="SOURCE-B",
            lead_id="LEAD-CROSS-B",
        ),
    )
    cross_id_attempts = _attempts(manifest, leads_by_query={
        query.query_id: ("LEAD-CROSS-A", "LEAD-CROSS-B"),
    })
    cross_id_result = _build(
        evidence=(exact_one, exact_two),
        seeds=cross_id_seeds,
        manifest=manifest,
        attempts=cross_id_attempts,
    )
    assert len(cross_id_result.audit_events) == 1

    conflicting_location = _event_evidence(
        evidence_id="E-CONFLICTING-LOCATION",
        record_id="CONFLICTING-LOCATION",
        start=september,
        location="Washington, DC",
        url="https://events.example.org/data-summit/washington",
    )
    location_seed = _event_seed(
        conflicting_location,
        manifest,
        start=september,
        location="Washington, DC",
        external_event_id="SOURCE-A",
        lead_id="LEAD-CONFLICTING-LOCATION",
    )
    location_attempts = _attempts(manifest, leads_by_query={
        query.query_id: (
            "LEAD-CROSS-A",
            "LEAD-CONFLICTING-LOCATION",
        ),
    })
    with pytest.raises(ValueError, match="conflicting observations"):
        _build(
            evidence=(exact_one, conflicting_location),
            seeds=(cross_id_seeds[0], location_seed),
            manifest=manifest,
            attempts=location_attempts,
        )


def test_projection_hides_deleted_calendar_rows_and_deferred_refs_close():
    from agents.candidate_review_v1.calendar_engine import CalendarBuildResult
    from agents.candidate_review_v1.projection import soft_delete_candidate

    manifest = _manifest()
    notice = _evidence(
        "E-DELETED", "NOTICE-DELETED", kind=EvidenceKind.NOTICE,
        excerpt="Responses are due 2026-08-15.",
        url="https://sam.gov/opp/NOTICE-DELETED/view",
        source_name="SAM.gov",
    )
    timing = DateValue(
        date_id="DATE-DELETED",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-08-15",
        sort_date=date(2026, 8, 15),
        start=date(2026, 8, 15),
        evidence_ids=(notice.evidence_id,),
    )
    candidate = _candidate(
        notice, (timing,), candidate_id="C-DELETED")
    result = _build(
        evidence=(notice,), candidates=(candidate,), manifest=manifest)
    document = CandidateReviewDocument(
        document_id="DOC-DELETED",
        baseline_document_sha256="f" * 64,
        client_name=manifest.binding.client_name,
        binding=manifest.binding,
        as_of=NOW,
        generated_at=NOW - timedelta(minutes=1),
        candidates=(candidate,),
        evidence=(notice,),
        editor_state=EditorState(
            client_id=manifest.binding.client_id,
            document_id="DOC-DELETED",
            baseline_document_sha256="f" * 64,
            candidate_order=(candidate.candidate_id,),
        ),
    )
    hydrated = result.hydrate_document(document)
    deleted = soft_delete_candidate(
        hydrated,
        candidate.candidate_id,
        deleted_at=NOW,
        undo_token="undo-deleted-calendar",
    )
    projection = project_candidate_review(deleted, clean_export=True)
    projected_refs = {
        evidence_id
        for item in projection.calendar_items
        for evidence_id in item.evidence_ids
    }
    unresolved_projected = projected_refs - set(projection.source_evidence_ids)

    beyond = add_calendar_months(NOW.date(), 12) + timedelta(days=1)
    event_evidence = _event_evidence(
        evidence_id="E-DEFERRED",
        record_id="DEFERRED",
        start=beyond,
        title="Deferred Summit",
    )
    event_seed = _event_seed(
        event_evidence,
        manifest,
        start=beyond,
        title="Deferred Summit",
        external_event_id="DEFERRED",
        lead_id="LEAD-DEFERRED",
    )
    deferred_result = _build(
        evidence=(event_evidence,),
        seeds=(event_seed,),
        manifest=manifest,
        attempts=_attempts(manifest, leads_by_query={
            _query(manifest).query_id: ("LEAD-DEFERRED",),
        }),
    )
    deferred_payload = deferred_result.model_dump(mode="python")
    deferred_row = deferred_result.deferred_items[0].model_dump(mode="python")
    deferred_row["item"] = {
        **deferred_row["item"],
        "evidence_ids": ("E-MISSING-DEFERRED",),
    }
    deferred_payload["deferred_items"] = (deferred_row,)
    deferred_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(deferred_payload))

    assert (
        tuple(item.candidate_id for item in projection.calendar_items),
        unresolved_projected,
        deferred_rejected,
    ) == ((), set(), True)


def test_result_binding_and_calendar_hydration_close_candidate_universe():
    from agents.candidate_review_v1.calendar_engine import CalendarBuildResult

    manifest = _manifest()
    query = _query(manifest)
    evidence = _event_evidence()
    seed = _event_seed(evidence, manifest)
    event_result = _build(
        evidence=(evidence,),
        seeds=(seed,),
        manifest=manifest,
        attempts=_attempts(manifest, leads_by_query={
            query.query_id: ("LEAD-1",),
        }),
    )

    evidence_payload = event_result.model_dump(mode="python")
    evidence_payload["evidence"] = ({
        **evidence.model_dump(mode="python"),
        "client_id": "otherco",
    },)
    foreign_evidence_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(evidence_payload))

    foreign_event = {
        **event_result.audit_events[0].model_dump(mode="python"),
        "client_id": "otherco",
    }
    event_payload = event_result.model_dump(mode="python")
    event_payload["audit_events"] = (foreign_event,)
    event_payload["events_for_document"] = (foreign_event,)
    foreign_event_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(event_payload))

    missing_document_event = event_result.model_dump(mode="python")
    missing_document_event["events_for_document"] = ()
    missing_document_event_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(missing_document_event))

    duplicate_document_event = event_result.model_dump(mode="python")
    duplicate_document_event["events_for_document"] = (
        *duplicate_document_event["events_for_document"],
        *duplicate_document_event["events_for_document"],
    )
    duplicate_document_event_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(duplicate_document_event))

    forged_item = event_result.model_dump(mode="python")
    forged_item["active_items"] = ({
        **forged_item["active_items"][0],
        "title": "Forged calendar title",
    },)
    forged_item_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(forged_item))

    forged_summary = event_result.model_dump(mode="python")
    forged_summary["coverage_receipt"] = {
        **forged_summary["coverage_receipt"],
        "public_summary": (
            "Comprehensive event research is complete across every lane."
        ),
    }
    forged_summary_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(forged_summary))

    forged_horizon = event_result.model_dump(mode="python")
    forged_horizon["coverage_receipt"] = {
        **forged_horizon["coverage_receipt"],
        "flagship_event_through": add_calendar_months(NOW.date(), 19),
        "procurement_through": add_calendar_months(NOW.date(), 25),
    }
    forged_horizon_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(forged_horizon))

    forged_requirements = event_result.model_dump(mode="python")
    forged_requirements["coverage_receipt"] = {
        **forged_requirements["coverage_receipt"],
        "required_row_keys": (),
        "comprehensive": True,
    }
    forged_requirements_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(forged_requirements))

    other_manifest = build_event_query_manifest(EventResearchFrame(
        binding=manifest.binding,
        revision_sha256="8" * 64,
        capabilities_missions=("different approved frame",),
        trusted_organizer_domains=("events.example.org",),
    ), NOW)
    swapped_manifest = event_result.model_dump(mode="python")
    swapped_manifest["manifest"] = other_manifest
    swapped_manifest_rejected = _audit_rejects(
        lambda: CalendarBuildResult.model_validate(swapped_manifest))

    notice = _evidence(
        "E-UNIVERSE", "NOTICE-UNIVERSE", kind=EvidenceKind.NOTICE,
        excerpt="Responses are due 2026-08-15.",
        url="https://sam.gov/opp/NOTICE-UNIVERSE/view",
        source_name="SAM.gov",
    )
    timing = DateValue(
        date_id="DATE-UNIVERSE",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-08-15",
        sort_date=date(2026, 8, 15),
        start=date(2026, 8, 15),
        evidence_ids=(notice.evidence_id,),
    )
    built_candidate = _candidate(
        notice, (timing,), candidate_id="C-BUILT")
    other_candidate = _candidate(
        notice, (timing,), candidate_id="C-OTHER")
    candidate_result = _build(
        evidence=(notice,),
        candidates=(built_candidate,),
        manifest=manifest,
    )
    other_document = CandidateReviewDocument(
        document_id="DOC-OTHER-UNIVERSE",
        baseline_document_sha256="9" * 64,
        client_name=manifest.binding.client_name,
        binding=manifest.binding,
        as_of=NOW,
        generated_at=NOW - timedelta(minutes=1),
        candidates=(other_candidate,),
        evidence=(notice,),
        editor_state=EditorState(
            client_id=manifest.binding.client_id,
            document_id="DOC-OTHER-UNIVERSE",
            baseline_document_sha256="9" * 64,
            candidate_order=(other_candidate.candidate_id,),
        ),
    )
    universe_mismatch_rejected = _audit_rejects(
        lambda: candidate_result.hydrate_document(other_document))

    assert (
        foreign_evidence_rejected,
        foreign_event_rejected,
        missing_document_event_rejected,
        duplicate_document_event_rejected,
        forged_item_rejected,
        forged_summary_rejected,
        forged_horizon_rejected,
        forged_requirements_rejected,
        swapped_manifest_rejected,
        universe_mismatch_rejected,
    ) == (True,) * 10


def test_trusted_organizers_reject_public_suffixes_but_accept_real_domains():
    valid_counts = []
    for domain in ("example.org", "events.example.org"):
        manifest = _manifest(trusted_domains=(domain,))
        query = _query(manifest)
        attempts = _attempts(
            manifest, leads_by_query={query.query_id: ("LEAD-1",)})
        trusted = _event_evidence(
            url=f"https://{domain}/data-summit")
        trusted_seed = _event_seed(trusted, manifest)
        valid_counts.append(len(build_calendar(
            binding=manifest.binding,
            as_of=NOW,
            candidates=(),
            evidence=(trusted,),
            event_seeds=(trusted_seed,),
            manifest=manifest,
            attempts=attempts,
            trusted_organizer_domains=(domain,),
        ).active_items))

    manifest = _manifest()
    query = _query(manifest)
    attempts = _attempts(
        manifest, leads_by_query={query.query_id: ("LEAD-1",)})
    broad = _event_evidence(
        url="https://unrelated.com/data-summit")
    broad_seed = _event_seed(broad, manifest)
    broad_rejected = _audit_rejects(lambda: build_calendar(
        binding=manifest.binding,
        as_of=NOW,
        candidates=(),
        evidence=(broad,),
        event_seeds=(broad_seed,),
        manifest=manifest,
        attempts=attempts,
        trusted_organizer_domains=("com",),
    ))

    attacker = _event_evidence(
        url="https://attacker.example.com/data-summit")
    attacker_seed = _event_seed(attacker, manifest)
    unbound_override_rejected = _audit_rejects(lambda: build_calendar(
        binding=manifest.binding,
        as_of=NOW,
        candidates=(),
        evidence=(attacker,),
        event_seeds=(attacker_seed,),
        manifest=manifest,
        attempts=attempts,
        trusted_organizer_domains=("attacker.example.com",),
    ))

    shared_suffix_rejected = _audit_rejects(
        lambda: _manifest(trusted_domains=("blogspot.com",))
    )

    assert (
        valid_counts,
        broad_rejected,
        unbound_override_rejected,
        shared_suffix_rejected,
    ) == ([1, 1], True, True, True)


def test_event_seed_attendance_and_flagship_evidence_have_boolean_parity():
    manifest = _manifest()
    evidence = _event_evidence()
    baseline = _event_seed(evidence, manifest).model_dump(mode="python")
    invalid_updates = (
        {
            "attending_agencies": ("DHS",),
            "attendance_evidence_ids": (),
        },
        {
            "attending_agencies": (),
            "attendance_evidence_ids": (evidence.evidence_id,),
        },
        {
            "flagship": False,
            "flagship_evidence_ids": (evidence.evidence_id,),
        },
    )

    assert all(
        _audit_rejects(lambda update=update: EventSeed.model_validate({
            **baseline,
            **update,
        }))
        for update in invalid_updates
    )


def test_calendar_result_rejects_foreign_candidate_snapshots():
    from agents.candidate_review_v1.calendar_engine import CalendarBuildResult

    notice = _evidence(
        "E-BOUND-CANDIDATE",
        "NOTICE-BOUND-CANDIDATE",
        kind=EvidenceKind.NOTICE,
        excerpt="Responses are due 2026-08-15.",
        url="https://sam.gov/opp/NOTICE-BOUND-CANDIDATE/view",
        source_name="SAM.gov",
    )
    timing = DateValue(
        date_id="DATE-BOUND-CANDIDATE",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-08-15",
        sort_date=date(2026, 8, 15),
        start=date(2026, 8, 15),
        evidence_ids=(notice.evidence_id,),
    )
    result = _build(
        evidence=(notice,),
        candidates=(_candidate(notice, (timing,)),),
    )
    payload = result.model_dump(mode="python")
    payload["candidates"] = ({
        **result.candidates[0].model_dump(mode="python"),
        "client_id": "otherco",
    },)

    with pytest.raises(ValueError, match="another client"):
        CalendarBuildResult.model_validate(payload)


def test_every_researched_event_requires_persistent_discovery_attribution():
    from agents.candidate_review_v1.calendar_engine import CalendarBuildResult

    manifest = _manifest()
    query = _query(manifest)
    evidence = _event_evidence()
    attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: ("LEAD-1",)},
    )
    result = _build(
        evidence=(evidence,),
        seeds=(_event_seed(evidence, manifest),),
        manifest=manifest,
        attempts=attempts,
    )
    empty_attribution_result = _build(
        manifest=manifest,
        attempts=attempts,
    )
    payload = result.model_dump(mode="python")
    payload["event_attributions"] = ()
    payload["coverage_receipt"] = (
        empty_attribution_result.coverage_receipt
    )

    with pytest.raises(ValueError, match="discovery attributions"):
        CalendarBuildResult.model_validate(payload)


def test_unrelated_official_page_cannot_anchor_or_credit_an_event():
    manifest = _manifest()
    query = _query(manifest)
    wrong = _event_evidence(
        evidence_id="E-A-WRONG",
        record_id="UNRELATED-WORKSHOP",
        title="Unrelated Workshop",
    )
    right = _event_evidence(
        evidence_id="E-Z-RIGHT",
        record_id="DATA-SUMMIT-2026",
    )
    seed = EventSeed.model_validate({
        **_event_seed(right, manifest).model_dump(mode="python"),
        "evidence_ids": (wrong.evidence_id, right.evidence_id),
    })
    attempts = _attempts(
        manifest,
        leads_by_query={query.query_id: ("LEAD-1",)},
    )

    with pytest.raises(ValueError, match="event edition"):
        _build(
            evidence=(wrong, right),
            seeds=(seed,),
            manifest=manifest,
            attempts=attempts,
        )


def test_coarse_procurement_periods_remain_active_until_period_end():
    month_evidence = _evidence(
        "E-MONTH",
        "MONTH",
        kind=EvidenceKind.NOTICE,
        excerpt="Review window July 2026.",
        url="https://sam.gov/opp/MONTH/view",
    )
    quarter_evidence = _evidence(
        "E-QUARTER",
        "QUARTER",
        kind=EvidenceKind.NOTICE,
        excerpt="Review window FY2026 Q4.",
        url="https://sam.gov/opp/QUARTER/view",
    )
    fiscal_evidence = _evidence(
        "E-FY",
        "FY",
        kind=EvidenceKind.NOTICE,
        excerpt="Review window FY2026.",
        url="https://sam.gov/opp/FY/view",
    )
    timings = (
        DateValue(
            date_id="DATE-MONTH",
            label="Monitor month",
            kind=DateKind.MONITOR_DATE,
            status=DateStatus.MONITOR,
            precision=DatePrecision.MONTH,
            source_text="July 2026",
            sort_date=date(2026, 7, 1),
            evidence_ids=(month_evidence.evidence_id,),
        ),
        DateValue(
            date_id="DATE-QUARTER",
            label="Monitor quarter",
            kind=DateKind.MONITOR_DATE,
            status=DateStatus.MONITOR,
            precision=DatePrecision.QUARTER,
            source_text="FY2026 Q4",
            sort_date=date(2026, 7, 1),
            evidence_ids=(quarter_evidence.evidence_id,),
        ),
        DateValue(
            date_id="DATE-FY",
            label="Monitor fiscal year",
            kind=DateKind.MONITOR_DATE,
            status=DateStatus.MONITOR,
            precision=DatePrecision.FISCAL_YEAR,
            source_text="FY2026",
            sort_date=date(2025, 10, 1),
            evidence_ids=(fiscal_evidence.evidence_id,),
        ),
    )
    candidate = _candidate(
        month_evidence,
        timings,
        supporting_evidence_ids=(
            quarter_evidence.evidence_id,
            fiscal_evidence.evidence_id,
        ),
    )
    result = _build(
        evidence=(month_evidence, quarter_evidence, fiscal_evidence),
        candidates=(candidate,),
    )

    assert {item.timing.date_id for item in result.active_items} == {
        "DATE-MONTH",
        "DATE-QUARTER",
        "DATE-FY",
    }
    assert result.deferred_items == ()


def test_calendar_projection_maximum_cannot_exceed_document_budget():
    with pytest.raises(ValueError, match="between 0 and 32"):
        _build(maximum_items=CONTENT_BUDGETS["calendar_events"] + 1)
