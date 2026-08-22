"""Machine-enforced evidence and product boundaries for Candidate Review v1."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.calendar_engine import CalendarItemOrigin
from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    SECTION_ORDER,
    AnalystDecision,
    ArtifactBinding,
    AssetEditState,
    AssetFit,
    AssetRole,
    AssetSlot,
    CandidateKind,
    CandidateMember,
    CandidateReviewDocument,
    CandidateUnit,
    ClusterLevel,
    CoverageRecord,
    CoverageState,
    ContextualEditorControl,
    DateKind,
    DatePrecision,
    DateStatus,
    DateValue,
    DecisionBoundary,
    DocumentControl,
    EditableTextSlot,
    EditorState,
    EventKind,
    EventRecord,
    EvidenceBoundClaim,
    EvidenceKind,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceRecord,
    EvidenceUse,
    KpiKind,
    KpiTile,
    LifecycleKind,
    MoneyBasis,
    MoneyValue,
    NoticeRole,
    NoticeStatus,
    ProcurementFamilyIdentity,
    ProductControlContract,
    SearchConcepts,
    SourceIdentity,
    SourceTier,
    StrategicCorridorIdentity,
    TextEditState,
    TickerItem,
    VehicleAccessPosture,
    VehicleActivityIdentity,
    VehicleClass,
    VehicleIdentity,
    VehicleOnRampStatus,
    VehicleOrderingStatus,
    VehicleParticipant,
    VehicleParticipantRole,
    VehicleRelationship,
    VehicleRelationshipKind,
    VehicleSignal,
    VehicleSignalKind,
    VehicleSignalStatus,
    VehicleWatchRecord,
    evidence_supports_date_value,
)
from agents.candidate_review_v1.watch_diff import (
    WatchChangeKind,
    append_verified_watch_ticker,
    diff_verified_watch,
)
from agents.candidate_review_v1.projection import project_candidate_review


NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
HASH = "a" * 64
OTHER_HASH = "b" * 64


def _binding(client_id: str = "testco", run_id: str = "run-1",
             scope_hash: str = HASH) -> ArtifactBinding:
    return ArtifactBinding(
        client_id=client_id,
        client_name="TestCo",
        run_id=run_id,
        scope_designator="all-federal",
        scope_sha256=scope_hash,
        profile_sha256="c" * 64,
        evidence_snapshot_sha256="d" * 64,
    )


def _evidence(
    evidence_id: str = "E-N1",
    *,
    client_id: str = "testco",
    run_id: str = "run-1",
    scope_hash: str = HASH,
    record_id: str = "N1",
    kind: EvidenceKind = EvidenceKind.NOTICE,
    tier: SourceTier = SourceTier.NOTICE,
    url: str | None = None,
    official: bool = True,
    primary: bool = True,
    excerpt: str = "The record describes the requirement and its timing.",
    assertions: tuple[EvidenceAssertionSpan, ...] = (),
    notice_role: NoticeRole = NoticeRole.END_USER_REQUIREMENT,
) -> EvidenceRecord:
    if url is None:
        url = (
            f"https://sam.gov/opp/{record_id}/view"
            if kind == EvidenceKind.NOTICE
            else f"https://example.gov/records/{record_id}"
        )
    return EvidenceRecord(
        evidence_id=evidence_id,
        client_id=client_id,
        run_id=run_id,
        scope_sha256=scope_hash,
        source_identity=SourceIdentity(
            source_system="sam.gov" if kind == EvidenceKind.NOTICE else "official",
            record_id=record_id,
        ),
        source_tier=tier,
        source_kind=kind,
        source_name="Official source",
        source_url=url,
        retrieved_at=NOW - timedelta(hours=1),
        title=f"Record {record_id}",
        excerpt=excerpt,
        official_source=official,
        primary_source=primary,
        supports=(EvidenceUse.REQUIREMENT, EvidenceUse.TIMING),
        notice_status=(NoticeStatus.ACTIVE if kind == EvidenceKind.NOTICE else None),
        notice_role=(
            notice_role
            if kind == EvidenceKind.NOTICE else None
        ),
        verified_at=(
            NOW - timedelta(minutes=30)
            if kind == EvidenceKind.NOTICE else None
        ),
        assertion_spans=assertions,
    )


def _boundary(
    lifecycle: LifecycleKind = LifecycleKind.LIVE_SOLICITATION,
    *,
    buyer: str = "agency:office-a",
    deadline: date | None = date(2026, 8, 15),
    eligibility: str = "full-and-open",
    program: str = "program-a",
    access: str = "open-market",
    action: str = "review-requirement",
) -> DecisionBoundary:
    return DecisionBoundary(
        buyer_key=buyer,
        deadline=deadline,
        eligibility_key=eligibility,
        lifecycle=lifecycle,
        program_key=program,
        access_route_key=access,
        analyst_action_key=action,
    )


def _current_candidate(
    candidate_id: str = "C1",
    evidence_id: str = "E-N1",
    record_id: str = "N1",
    *,
    client_id: str = "testco",
) -> CandidateUnit:
    boundary = _boundary()
    return CandidateUnit(
        candidate_id=candidate_id,
        client_id=client_id,
        kind=CandidateKind.CURRENT_NOTICE,
        title=f"Current notice {record_id}",
        agency="Test Agency",
        lifecycle=LifecycleKind.LIVE_SOLICITATION,
        cluster_level=ClusterLevel.EXACT_RECORD,
        members=(CandidateMember(
            source_identity=SourceIdentity(
                source_system="sam.gov", record_id=record_id),
            member_evidence_ids=(evidence_id,),
            decision_boundary=boundary,
        ),),
        records_show="An official notice states a current requirement.",
        may_suggest="The requirement may align with the reviewed capability set.",
        validate_next="Read the complete notice package and verify eligibility.",
        cluster_reason="This card represents one exact official record.",
        strategic_action="Validate requirements, timing, and acquisition access.",
        distinctness_explanation="The buyer and deadline form one distinct review action.",
        inference_chain="Official notice to requirement review to analyst validation.",
        falsifier="The complete package shows no relevant requirement.",
        watch_trigger="A notice amendment changes scope or timing.",
    )


def _corridor_candidate(
    candidate_id: str,
    evidence_rows: list[tuple[str, str]],
    *,
    client_id: str = "testco",
    boundary: DecisionBoundary | None = None,
) -> CandidateUnit:
    boundary = boundary or _boundary(
        LifecycleKind.RECOMPETE_RESEARCH,
        deadline=None,
        access="research",
        action="validate-next-buy",
    )
    members = tuple(
        CandidateMember(
            source_identity=SourceIdentity(
                source_system="official", record_id=record_id),
            member_evidence_ids=(evidence_id,),
            decision_boundary=boundary,
        )
        for evidence_id, record_id in evidence_rows
    )
    return CandidateUnit(
        candidate_id=candidate_id,
        client_id=client_id,
        kind=CandidateKind.RESEARCH_CORRIDOR,
        title=f"Research corridor {candidate_id}",
        agency="Test Agency",
        lifecycle=boundary.lifecycle,
        cluster_level=ClusterLevel.STRATEGIC_CORRIDOR,
        strategic_corridor=StrategicCorridorIdentity(
            corridor_id=f"corridor:{candidate_id.casefold()}",
            decision_boundary=boundary,
        ),
        members=members,
        records_show="Official records show funded activity in one decision lane.",
        may_suggest="The pattern may signal a future acquisition research path.",
        validate_next="Confirm buyer intent, timing, and the next acquisition route.",
        cluster_reason="The records lead to the same analyst validation action.",
        strategic_action="Research the next buying event and access path.",
        distinctness_explanation="Buyer, lifecycle, program, access, and action match.",
        inference_chain="Funded record to research clock to buyer validation.",
        falsifier="The buyer confirms the funded position will not continue.",
        watch_trigger="A forecast, notice, budget record, or industry day appears.",
    )


def _editor(client_id: str, document_id: str,
            candidate_ids: tuple[str, ...]) -> EditorState:
    return EditorState(
        client_id=client_id,
        document_id=document_id,
        baseline_document_sha256="e" * 64,
        candidate_order=candidate_ids,
    )


def _document(
    *,
    evidence: tuple[EvidenceRecord, ...] = (),
    candidates: tuple[CandidateUnit, ...] = (),
    client_id: str = "testco",
    run_id: str = "run-1",
    scope_hash: str = HASH,
    document_id: str = "DOC-1",
    **changes,
) -> CandidateReviewDocument:
    return CandidateReviewDocument(
        document_id=document_id,
        baseline_document_sha256="e" * 64,
        client_name="TestCo",
        binding=_binding(client_id, run_id, scope_hash),
        as_of=NOW,
        generated_at=NOW - timedelta(minutes=1),
        evidence=evidence,
        candidates=candidates,
        editor_state=_editor(
            client_id, document_id,
            tuple(candidate.candidate_id for candidate in candidates),
        ),
        **changes,
    )


def _vehicle_watch_fixture():
    identity_quote = (
        "OASIS Plus, also known as OASIS+, uses program ID OASISPLUS."
    )
    class_quote = "OASIS Plus is a multiple-award IDIQ."
    management_quote = (
        "GSA Federal Acquisition Service and the OASIS Program Office "
        "manage OASIS Plus."
    )
    scope_quote = (
        "Scope includes professional services in Pool 1, Data domain, "
        "SIN 54151, NAICS 541512, and PSC D302."
    )
    eligibility_quote = "All federal agencies are eligible ordering organizations."
    ordering_quote = "Ordering is active through September 1, 2029."
    on_ramp_quote = "The OASIS Plus on-ramp is open."
    close_quote = "The on-ramp closes August 15, 2026."
    holder_quote = "TestCo is an awarded holder of OASIS Plus."
    access_quote = "TestCo is a direct holder of OASIS Plus."
    vehicle_excerpt = " ".join((
        identity_quote,
        class_quote,
        management_quote,
        scope_quote,
        eligibility_quote,
        ordering_quote,
        on_ramp_quote,
        close_quote,
        holder_quote,
        access_quote,
    ))
    vehicle = _evidence(
        "E-VEHICLE-WATCH",
        record_id="OASISPLUS",
        kind=EvidenceKind.VEHICLE,
        tier=SourceTier.PROGRAM,
        excerpt=vehicle_excerpt,
        assertions=(
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_IDENTITY,
                quote=identity_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_CLASS,
                quote=class_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_MANAGEMENT,
                quote=management_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_SCOPE,
                quote=scope_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_ELIGIBILITY,
                quote=eligibility_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_ORDERING_STATUS,
                quote=ordering_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_ON_RAMP,
                quote=on_ramp_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_ON_RAMP_CLOSE,
                quote=close_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_HOLDER,
                quote=holder_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_DIRECT_HOLDER,
                quote=access_quote,
            ),
        ),
    )
    notice = _evidence(
        "E-VEHICLE-NOTICE",
        record_id="ONRAMP-1",
        notice_role=NoticeRole.VEHICLE_ON_RAMP,
    )
    order = _evidence(
        "E-VEHICLE-ORDER",
        record_id="TO-1",
        kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
        excerpt="Task order TO-1 is issued under vehicle program OASISPLUS.",
        assertions=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
            quote=(
                "Task order TO-1 is issued under vehicle program OASISPLUS."
            ),
        ),),
    )
    award = _evidence(
        "E-VEHICLE-AWARD",
        record_id="IDV-AWARD-1",
        kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
        excerpt=(
            "Award IDV-AWARD-1 is issued under vehicle program OASISPLUS. "
            "The IDV award ceiling is $10,000,000 as of July 20, 2026."
        ),
        assertions=(
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                quote=(
                    "Award IDV-AWARD-1 is issued under vehicle program "
                    "OASISPLUS."
                ),
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_SOURCE_DATA_AS_OF,
                quote=(
                    "The IDV award ceiling is $10,000,000 as of July 20, 2026."
                ),
            ),
        ),
    )
    evidence = (vehicle, notice, order, award)
    query_id = "vehicle-query:oasis-plus"
    coverage = CoverageRecord(
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        source="official-vehicle-program",
        query_family="vehicle_watch:program",
        state=CoverageState.RETURNED,
        window_start=date(2026, 7, 1),
        window_end=NOW.date(),
        attempted_at=NOW - timedelta(minutes=20),
        records_returned=len(evidence),
        records_accepted=len(evidence),
        accepted_evidence_ids=tuple(row.evidence_id for row in evidence),
        query_manifest_id="vehicle-manifest-1",
        query_id=query_id,
        public_detail="Official vehicle sources returned four accepted records.",
    )
    timing = DateValue(
        date_id="DATE-OASIS-ONRAMP-CLOSE",
        label="On-ramp close",
        kind=DateKind.ON_RAMP_CLOSE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="August 15, 2026",
        sort_date=date(2026, 8, 15),
        start=date(2026, 8, 15),
        evidence_ids=(vehicle.evidence_id,),
    )
    record = VehicleWatchRecord(
        watch_record_id="vehicle-watch:oasis-plus",
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        canonical_name="OASIS Plus",
        aliases=("OASIS+",),
        vehicle_program_id="OASISPLUS",
        vehicle_source_identity=vehicle.source_identity,
        vehicle_class=VehicleClass.MULTIPLE_AWARD_IDIQ,
        identity_evidence_ids=(vehicle.evidence_id,),
        managing_agency="GSA",
        managing_component="Federal Acquisition Service",
        program_office="OASIS Program Office",
        management_evidence_ids=(vehicle.evidence_id,),
        scope_summary="Professional services across the published domains.",
        pools=("Pool 1",),
        domains=("Data",),
        sins=("54151",),
        naics_codes=("541512",),
        psc_codes=("D302",),
        eligible_ordering_organizations=("All federal agencies",),
        scope_evidence_ids=(vehicle.evidence_id,),
        ordering_status=VehicleOrderingStatus.ACTIVE,
        on_ramp_status=VehicleOnRampStatus.OPEN,
        status_evidence_ids=(vehicle.evidence_id,),
        dates=(timing,),
        participants=(VehicleParticipant(
            participant_id="participant:testco",
            legal_name="TestCo",
            role=VehicleParticipantRole.CLIENT_HOLDER,
            evidence_ids=(vehicle.evidence_id,),
        ),),
        access_posture=VehicleAccessPosture.DIRECT_HOLDER,
        access_evidence_ids=(vehicle.evidence_id,),
        notice_activity=(VehicleActivityIdentity(
            activity_id="activity:notice:onramp-1",
            source_identity=notice.source_identity,
            evidence_ids=(notice.evidence_id,),
        ),),
        order_activity=(VehicleActivityIdentity(
            activity_id="activity:order:to-1",
            source_identity=order.source_identity,
            related_vehicle_identity=vehicle.source_identity,
            evidence_ids=(order.evidence_id,),
        ),),
        award_activity=(VehicleActivityIdentity(
            activity_id="activity:award:idv-award-1",
            source_identity=award.source_identity,
            related_vehicle_identity=vehicle.source_identity,
            evidence_ids=(award.evidence_id,),
        ),),
        ceiling_or_value=MoneyValue(
            money_id="money:oasis-plus-ceiling",
            basis=MoneyBasis.AWARD_CEILING,
            amount=Decimal("10000000"),
            as_of=date(2026, 7, 20),
            source_field="award ceiling",
            evidence_ids=(award.evidence_id,),
        ),
        records_show="Official records identify an active vehicle and open on-ramp.",
        may_suggest="The documented holder status may support a direct access path.",
        validate_next="Confirm pool eligibility and the current on-ramp package.",
        official_evidence_ids=tuple(row.evidence_id for row in evidence),
        last_checked_at=NOW - timedelta(minutes=10),
        source_data_as_of=date(2026, 7, 20),
        coverage_query_ids=(query_id,),
    )
    return evidence, coverage, record


def test_contracts_are_deeply_frozen_strict_and_round_trip_canonically():
    evidence = _evidence()
    candidate = _current_candidate()
    document = _document(evidence=(evidence,), candidates=(candidate,))
    with pytest.raises(ValidationError, match="frozen"):
        document.client_name = "Changed"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CandidateReviewDocument.model_validate({
            **document.model_dump(mode="python"),
            "automatic_recommendation": "bid",
        })
    encoded = document.model_dump_json()
    assert CandidateReviewDocument.model_validate_json(encoded) == document
    with pytest.raises(ValidationError, match="visible client name"):
        CandidateReviewDocument.model_validate({
            **document.model_dump(mode="python"),
            "client_name": "Riverbed",
        })


def test_vehicle_watch_record_is_a_strict_section_three_contract():
    evidence, coverage, record = _vehicle_watch_fixture()

    document = _document(
        evidence=evidence,
        coverage=(coverage,),
        vehicle_watch_records=(record,),
    )
    published = document.vehicle_watch_records[0]

    assert published.vehicle_class is VehicleClass.MULTIPLE_AWARD_IDIQ
    assert published.ordering_status is VehicleOrderingStatus.ACTIVE
    assert published.on_ramp_status is VehicleOnRampStatus.OPEN
    assert published.access_posture is VehicleAccessPosture.DIRECT_HOLDER
    assert published.creates_candidate is False
    assert {
        published.notice_activity[0].source_identity.canonical_key,
        published.order_activity[0].source_identity.canonical_key,
        published.award_activity[0].source_identity.canonical_key,
    } == {
        "sam.gov:onramp-1",
        "official:to-1",
        "official:idv-award-1",
    }
    assert {
        published.order_activity[0].related_vehicle_identity.canonical_key,
        published.award_activity[0].related_vehicle_identity.canonical_key,
    } == {"official:oasisplus"}
    assert published.ceiling_or_value.basis is MoneyBasis.AWARD_CEILING
    projection = project_candidate_review(document)
    assert projection.vehicle_watch_records == (published,)
    assert projection.candidate_count == 0
    assert projection.candidates == ()
    assert len(projection.calendar_items) == 1
    assert projection.calendar_items[0].origin \
        is CalendarItemOrigin.VEHICLE_WATCH_RECORD
    assert projection.calendar_items[0].vehicle_watch_record_id \
        == record.watch_record_id

    unnamed_parent = record.model_copy(update={
        "canonical_name": None,
        "aliases": (),
        "vehicle_program_id": None,
        "parent_idv_piid": "OASISPLUS",
        "vehicle_source_identity": None,
        "parent_idv_source_identity": evidence[0].source_identity,
    })
    no_guess_document = _document(
        evidence=evidence,
        coverage=(coverage,),
        vehicle_watch_records=(unnamed_parent,),
    )
    assert no_guess_document.vehicle_watch_records[0].canonical_name is None
    assert no_guess_document.vehicle_watch_records[0].parent_idv_piid \
        == "OASISPLUS"
    assert no_guess_document.vehicle_watch_records[
        0
    ].vehicle_source_identity is None
    assert no_guess_document.vehicle_watch_records[
        0
    ].parent_idv_source_identity == evidence[0].source_identity

    program_evidence = (evidence[0],)
    program_coverage = coverage.model_copy(update={
        "records_returned": 1,
        "records_accepted": 1,
        "accepted_evidence_ids": (evidence[0].evidence_id,),
    })
    program_only = record.model_copy(update={
        "participants": (),
        "access_posture": VehicleAccessPosture.VALIDATION_REQUIRED,
        "access_evidence_ids": (),
        "notice_activity": (),
        "order_activity": (),
        "award_activity": (),
        "ceiling_or_value": None,
        "official_evidence_ids": (evidence[0].evidence_id,),
        "source_data_as_of": None,
    })
    program_document = _document(
        evidence=program_evidence,
        coverage=(program_coverage,),
        vehicle_watch_records=(program_only,),
    )
    assert program_document.vehicle_watch_records[0].source_data_as_of is None
    assert program_document.vehicle_watch_records[0].access_evidence_ids == ()

    unsupported_direct_access = record.model_copy(update={
        "access_evidence_ids": (),
    })
    with pytest.raises(ValidationError, match="positive vehicle access"):
        VehicleWatchRecord.model_validate(
            unsupported_direct_access.model_dump(mode="python")
        )


def test_vehicle_watch_record_rejects_unproved_identity_and_lineage():
    evidence, coverage, record = _vehicle_watch_fixture()
    guessed_alias = record.model_copy(update={"aliases": ("Unproved Alias",)})
    with pytest.raises(ValidationError, match="identity value lacks"):
        _document(
            evidence=evidence,
            coverage=(coverage,),
            vehicle_watch_records=(guessed_alias,),
        )

    wrong_order_identity = record.model_copy(update={
        "order_activity": (
            record.order_activity[0].model_copy(update={
                "source_identity": SourceIdentity(
                    source_system="official",
                    record_id="ANOTHER-ORDER",
                ),
            }),
        ),
    })
    with pytest.raises(ValidationError, match="exact lineage"):
        _document(
            evidence=evidence,
            coverage=(coverage,),
            vehicle_watch_records=(wrong_order_identity,),
        )

    missing_parent = record.model_copy(update={
        "order_activity": (
            record.order_activity[0].model_copy(update={
                "related_vehicle_identity": None,
            }),
        ),
    })
    with pytest.raises(
        ValidationError,
        match="requires an exact related vehicle identity",
    ):
        VehicleWatchRecord.model_validate(
            missing_parent.model_dump(mode="python")
        )

    unrelated_parent = record.model_copy(update={
        "order_activity": (
            record.order_activity[0].model_copy(update={
                "related_vehicle_identity": SourceIdentity(
                    source_system="official",
                    record_id="ANOTHER-VEHICLE",
                ),
            }),
        ),
    })
    with pytest.raises(ValidationError, match="exact parent IDV or vehicle"):
        _document(
            evidence=evidence,
            coverage=(coverage,),
            vehicle_watch_records=(unrelated_parent,),
        )

    same_identity = record.order_activity[0].model_copy(update={
        "related_vehicle_identity": record.order_activity[0].source_identity,
    })
    with pytest.raises(ValidationError, match="must remain separate"):
        VehicleActivityIdentity.model_validate(
            same_identity.model_dump(mode="python")
        )

    missing_vehicle_source = record.model_copy(update={
        "vehicle_source_identity": None,
    })
    with pytest.raises(ValidationError, match="vehicle or parent IDV source"):
        VehicleWatchRecord.model_validate(
            missing_vehicle_source.model_dump(mode="python")
        )

    parent_without_source = record.model_copy(update={
        "parent_idv_piid": "PARENT-1",
    })
    with pytest.raises(ValidationError, match="parent IDV source identity"):
        VehicleWatchRecord.model_validate(
            parent_without_source.model_dump(mode="python")
        )

    distinct_parent = record.model_copy(update={
        "parent_idv_piid": "PARENT-1",
        "parent_idv_source_identity": SourceIdentity(
            source_system="official",
            record_id="PARENT-1",
        ),
    })
    with pytest.raises(ValidationError, match="exact parent IDV or vehicle"):
        VehicleWatchRecord.model_validate(
            distinct_parent.model_dump(mode="python")
        )


def test_vehicle_watch_activity_requires_one_exact_two_identity_assertion():
    evidence, coverage, record = _vehicle_watch_fixture()
    weak_order = EvidenceRecord.model_validate({
        **evidence[2].model_dump(mode="python"),
        "excerpt": (
            "Task order TO-10 was issued. Vehicle program OASISPLUS is active."
        ),
        "assertion_spans": (EvidenceAssertionSpan(
            assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
            quote=(
                "Task order TO-10 was issued. Vehicle program OASISPLUS is "
                "active."
            ),
        ),),
    })
    weak_evidence = (*evidence[:2], weak_order, evidence[3])
    with pytest.raises(ValidationError, match="lineage assertion"):
        _document(
            evidence=weak_evidence,
            coverage=(coverage,),
            vehicle_watch_records=(record,),
        )


def test_vehicle_watch_record_enforces_evidence_time_data_as_of_and_coverage():
    evidence, coverage, record = _vehicle_watch_fixture()
    evidence_newer_than_check = tuple(
        EvidenceRecord.model_validate({
            **row.model_dump(mode="python"),
            "retrieved_at": NOW - timedelta(minutes=5),
            **(
                {"verified_at": NOW - timedelta(minutes=4)}
                if row.source_kind is EvidenceKind.NOTICE else {}
            ),
        })
        for row in evidence
    )
    with pytest.raises(ValidationError, match="predates accepted evidence"):
        _document(
            evidence=evidence_newer_than_check,
            coverage=(coverage,),
            vehicle_watch_records=(record,),
        )

    future_data = record.model_copy(update={
        "source_data_as_of": NOW.date() + timedelta(days=1),
    })
    with pytest.raises(ValidationError, match="source-data as-of"):
        _document(
            evidence=evidence,
            coverage=(coverage,),
            vehicle_watch_records=(future_data,),
        )

    unsupported_data_date = record.model_copy(update={
        "source_data_as_of": date(2026, 7, 19),
    })
    with pytest.raises(ValidationError, match="exact official assertion"):
        _document(
            evidence=evidence,
            coverage=(coverage,),
            vehicle_watch_records=(unsupported_data_date,),
        )

    incomplete_coverage = coverage.model_copy(update={
        "records_accepted": len(evidence) - 1,
        "accepted_evidence_ids": tuple(
            row.evidence_id for row in evidence[:-1]
        ),
    })
    with pytest.raises(ValidationError, match="source-coverage receipt"):
        _document(
            evidence=evidence,
            coverage=(incomplete_coverage,),
            vehicle_watch_records=(record,),
        )


def test_current_vehicle_watch_record_must_be_checked_within_seven_days():
    evidence, coverage, record = _vehicle_watch_fixture()
    old_retrieval = NOW - timedelta(days=9)
    old_evidence = tuple(
        EvidenceRecord.model_validate({
            **row.model_dump(mode="python"),
            "retrieved_at": old_retrieval,
            **(
                {"verified_at": old_retrieval + timedelta(minutes=1)}
                if row.source_kind is EvidenceKind.NOTICE else {}
            ),
            **(
                {
                    "excerpt": (
                        "The IDV award ceiling is $10,000,000 as of "
                        "July 13, 2026."
                    ),
                    "assertion_spans": (EvidenceAssertionSpan(
                        assertion=(
                            EvidenceAssertion.VEHICLE_SOURCE_DATA_AS_OF
                        ),
                        quote=(
                            "The IDV award ceiling is $10,000,000 as of "
                            "July 13, 2026."
                        ),
                    ),),
                }
                if row.evidence_id == "E-VEHICLE-AWARD" else {}
            ),
        })
        for row in evidence
    )
    stale = record.model_copy(update={
        "last_checked_at": NOW - timedelta(days=8),
        "source_data_as_of": (NOW - timedelta(days=9)).date(),
    })
    with pytest.raises(ValidationError, match="within seven days"):
        _document(
            evidence=old_evidence,
            coverage=(coverage,),
            vehicle_watch_records=(stale,),
        )


@pytest.mark.parametrize("field,value,match", [
    ("client_id", "other-client", "cross-client"),
    ("run_id", "other-run", "cross-client"),
    ("scope_hash", OTHER_HASH, "cross-client"),
])
def test_cross_client_run_or_scope_evidence_is_rejected(field, value, match):
    kwargs = {field: value}
    evidence = _evidence(**kwargs)
    with pytest.raises(ValidationError, match=match):
        _document(evidence=(evidence,))


def test_duplicate_source_identity_and_unresolved_references_are_rejected():
    one = _evidence("E1", record_id="N1")
    two = _evidence("E2", record_id="N1")
    with pytest.raises(ValidationError, match="source identities"):
        _document(evidence=(one, two))

    candidate = _current_candidate(evidence_id="MISSING")
    with pytest.raises(ValidationError, match="unresolved evidence"):
        _document(evidence=(one,), candidates=(candidate,))


def test_current_notice_requires_official_primary_sam_notice_evidence():
    forecast = _evidence(
        "E-F1",
        record_id="F1",
        kind=EvidenceKind.AGENCY_FORECAST,
        tier=SourceTier.PROGRAM,
    )
    candidate = _current_candidate(evidence_id="E-F1", record_id="F1")
    candidate = CandidateUnit.model_validate({
        **candidate.model_dump(mode="python"),
        "members": [{
            **candidate.members[0].model_dump(mode="python"),
            "source_identity": forecast.source_identity,
        }],
    })
    with pytest.raises(ValidationError, match="official primary SAM.gov"):
        _document(evidence=(forecast,), candidates=(candidate,))

    with pytest.raises(ValidationError, match="official primary-source"):
        _evidence(primary=False)

    closed = EvidenceRecord.model_validate({
        **_evidence().model_dump(mode="python"),
        "notice_status": NoticeStatus.CLOSED,
    })
    with pytest.raises(ValidationError, match="official primary SAM.gov"):
        _document(evidence=(closed,), candidates=(_current_candidate(),))

    stale = EvidenceRecord.model_validate({
        **_evidence().model_dump(mode="python"),
        "retrieved_at": NOW - timedelta(days=2, minutes=1),
        "verified_at": NOW - timedelta(days=2),
    })
    with pytest.raises(ValidationError, match="verified within"):
        _document(evidence=(stale,), candidates=(_current_candidate(),))
    with pytest.raises(ValidationError, match="source identity must be SAM.gov"):
        EvidenceRecord.model_validate({
            **_evidence().model_dump(mode="python"),
            "source_identity": SourceIdentity(
                source_system="not-sam", record_id="N1"),
        })
    with pytest.raises(ValidationError, match="notice source tier"):
        EvidenceRecord.model_validate({
            **_evidence().model_dump(mode="python"),
            "source_tier": SourceTier.PROGRAM,
        })


def test_news_only_candidate_is_rejected_but_news_can_support_official_evidence():
    news = _evidence(
        "E-NEWS",
        record_id="NEWS-1",
        kind=EvidenceKind.NEWS,
        tier=SourceTier.DISCOVERY,
        url="https://news.example.com/story",
        official=False,
        primary=False,
    )
    corridor = _corridor_candidate("C-NEWS", [("E-NEWS", "NEWS-1")])
    with pytest.raises(ValidationError, match="news-only"):
        _document(evidence=(news,), candidates=(corridor,))

    award = _evidence(
        "E-A1", record_id="A1", kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
    )
    supported = _corridor_candidate("C-AWARD", [("E-A1", "A1")])
    supported = supported.model_copy(update={"supporting_evidence_ids": ("E-NEWS",)})
    assert _document(evidence=(award, news), candidates=(supported,)).candidates


def test_shared_anchor_policy_rejects_vehicle_only_serialized_candidate():
    vehicle = _evidence(
        "E-VEHICLE",
        record_id="IDV-1",
        kind=EvidenceKind.VEHICLE,
        tier=SourceTier.PROGRAM,
    )
    corridor = _corridor_candidate(
        "C-VEHICLE",
        [(vehicle.evidence_id, vehicle.source_identity.record_id)],
    )

    assert vehicle.can_anchor_candidate is False
    with pytest.raises(ValidationError, match="context-only"):
        _document(evidence=(vehicle,), candidates=(corridor,))


def test_notice_requires_typed_role_and_vehicle_notice_is_not_current_demand():
    notice = _evidence()
    with pytest.raises(ValidationError, match="acquisition role"):
        EvidenceRecord.model_validate({
            **notice.model_dump(mode="python"),
            "notice_role": None,
        })

    on_ramp = EvidenceRecord.model_validate({
        **notice.model_dump(mode="python"),
        "notice_role": NoticeRole.VEHICLE_ON_RAMP,
    })
    assert on_ramp.confirms_open_notice is False
    assert on_ramp.can_anchor_candidate is False


def test_vehicle_signal_preserves_exact_parent_and_order_without_guessing_name():
    excerpt = (
        "OASIS+ is identified by parent IDV IDV-1 for task order TO-1."
    )
    evidence = _evidence(
        "E-ORDER",
        record_id="TO-1",
        kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
        excerpt=excerpt,
        assertions=(
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_IDENTITY,
                quote="OASIS+ is identified by parent IDV IDV-1",
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                quote="parent IDV IDV-1 for task order TO-1",
            ),
        ),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-1",
        agency="GSA",
        name="OASIS+",
        idv_piid="IDV-1",
        evidence_ids=(evidence.evidence_id,),
    )
    relationship = VehicleRelationship(
        relationship_id="relationship:to-1",
        vehicle_id=identity.vehicle_id,
        kind=VehicleRelationshipKind.TASK_ORDER,
        access_posture=VehicleAccessPosture.UNKNOWN,
        order_identity=evidence.source_identity,
        evidence_ids=(evidence.evidence_id,),
    )
    signal = VehicleSignal(
        signal_id="signal:to-1",
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        title="Task order activity under a sourced parent IDV",
        agency="GSA",
        kind=VehicleSignalKind.TASK_ORDER_ACTIVITY,
        status=VehicleSignalStatus.RESEARCH,
        vehicle=identity,
        relationship=relationship,
        records_show="The award record names the task order and parent IDV.",
        may_suggest="The route may require access through an eligible holder.",
        validate_next="Verify the current holder and ordering eligibility.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=NOW - timedelta(minutes=30),
    )

    document = _document(evidence=(evidence,), vehicle_signals=(signal,))
    assert document.vehicle_signals[0].creates_candidate is False
    assert document.vehicle_signals[0].relationship.order_identity.record_id == "TO-1"

    # An exact order source identity alone is not lineage.  This used to pass
    # because the relationship validator accepted the source-record match as
    # an alternative to an explicit order-to-parent assertion.
    identity_only = evidence.model_copy(update={
        "assertion_spans": (evidence.assertion_spans[0],),
    })
    with pytest.raises(ValidationError, match="parent-IDV assertion"):
        _document(evidence=(identity_only,), vehicle_signals=(signal,))

    # Publication projections re-enter the document boundary, including when
    # an unsafe model_copy bypassed Pydantic update validation.
    unsafe_copy = document.model_copy(update={"evidence": (identity_only,)})
    with pytest.raises(ValidationError, match="parent-IDV assertion"):
        project_candidate_review(unsafe_copy)

    for lookalike_quote in (
        "Task order TO-1 is issued under a federal vehicle.",
        "Parent IDV IDV-1 has task-order activity.",
        "Parent IDV IDV-10 supports task order TO-10.",
    ):
        lookalike = evidence.model_copy(update={
            "excerpt": (
                f"{evidence.assertion_spans[0].quote}. {lookalike_quote}"
            ),
            "assertion_spans": (
                evidence.assertion_spans[0],
                EvidenceAssertionSpan(
                    assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                    quote=lookalike_quote,
                ),
            ),
        })
        with pytest.raises(ValidationError, match="parent-IDV assertion"):
            _document(evidence=(lookalike,), vehicle_signals=(signal,))

    guessed_name = signal.model_copy(update={
        "vehicle": identity.model_copy(update={"name": "Alliant 3"}),
    })
    with pytest.raises(ValidationError, match="vehicle name"):
        _document(evidence=(evidence,), vehicle_signals=(guessed_name,))


def test_vehicle_order_lineage_assertion_must_be_on_exact_order_record():
    order = _evidence(
        "E-ORDER",
        record_id="TO-1",
        kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
        excerpt="OASIS+ uses parent IDV IDV-1.",
        assertions=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.VEHICLE_IDENTITY,
            quote="OASIS+ uses parent IDV IDV-1.",
        ),),
    )
    parent = _evidence(
        "E-PARENT",
        record_id="IDV-1",
        kind=EvidenceKind.VEHICLE,
        tier=SourceTier.PROGRAM,
        excerpt="Parent IDV IDV-1 supports task order TO-1.",
        assertions=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
            quote="Parent IDV IDV-1 supports task order TO-1.",
        ),),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-1",
        agency="GSA",
        name="OASIS+",
        idv_piid="IDV-1",
        evidence_ids=(order.evidence_id,),
    )
    relationship = VehicleRelationship(
        relationship_id="relationship:to-1",
        vehicle_id=identity.vehicle_id,
        kind=VehicleRelationshipKind.DELIVERY_ORDER,
        access_posture=VehicleAccessPosture.UNKNOWN,
        order_identity=order.source_identity,
        evidence_ids=(order.evidence_id, parent.evidence_id),
    )
    signal = VehicleSignal(
        signal_id="signal:to-1",
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        title="Delivery order activity under a sourced parent IDV",
        agency="GSA",
        kind=VehicleSignalKind.TASK_ORDER_ACTIVITY,
        status=VehicleSignalStatus.RESEARCH,
        vehicle=identity,
        relationship=relationship,
        records_show="The records separately name an order and a parent IDV.",
        may_suggest="The route may depend on the parent vehicle.",
        validate_next="Verify exact order-to-parent lineage in the order record.",
        evidence_ids=(order.evidence_id,),
        last_checked_at=NOW - timedelta(minutes=30),
    )

    with pytest.raises(ValidationError, match="parent-IDV assertion"):
        _document(
            evidence=(order, parent),
            vehicle_signals=(signal,),
        )


def test_verified_watch_diff_feeds_linked_event_and_order_ticker_changes():
    def event_snapshot(run_id: str, evidence_id: str, event_date: date):
        date_text = event_date.strftime("%B %-d, %Y")
        quote = (
            "The official organizer lists the Federal data conference for "
            f"{date_text}."
        )
        evidence = _evidence(
            evidence_id,
            run_id=run_id,
            record_id="FED-DATA-2026",
            kind=EvidenceKind.OFFICIAL_EVENT,
            tier=SourceTier.PROGRAM,
            url="https://events.example.gov/FED-DATA-2026",
            excerpt=quote,
            assertions=(EvidenceAssertionSpan(
                assertion=EvidenceAssertion.OFFICIAL_EVENT,
                quote=quote,
            ),),
        )
        timing = DateValue(
            date_id="DATE-FED-DATA-2026",
            label="Conference date",
            kind=DateKind.EVENT_START,
            status=DateStatus.CONFIRMED,
            precision=DatePrecision.DAY,
            source_text=date_text,
            sort_date=event_date,
            start=event_date,
            evidence_ids=(evidence_id,),
        )
        event = EventRecord(
            event_id="event:fed-data-2026",
            client_id="testco",
            run_id=run_id,
            scope_sha256=HASH,
            title="Federal data conference",
            kind=EventKind.CONFERENCE,
            timing=timing,
            organizer="Official organizer",
            location="Denver, CO",
            audience="Federal program and acquisition leaders",
            relevance="The agenda covers the approved capability frame.",
            validate_next="Confirm the current agenda and registration status.",
            evidence_ids=(evidence_id,),
            last_checked_at=NOW - timedelta(minutes=30),
        )
        return evidence, event

    old_evidence, old_event = event_snapshot(
        "run-1",
        "E-EVENT-OLD",
        date(2026, 9, 1),
    )
    previous = _document(
        run_id="run-1",
        document_id="DOC-OLD",
        evidence=(old_evidence,),
        calendar_events=(old_event,),
    )
    refreshed_evidence, refreshed_event = event_snapshot(
        "run-2",
        "E-EVENT-REFRESH",
        date(2026, 9, 1),
    )
    refreshed = _document(
        run_id="run-2",
        document_id="DOC-REFRESH",
        evidence=(refreshed_evidence,),
        calendar_events=(refreshed_event,),
    )
    assert diff_verified_watch(previous, refreshed).changes == ()

    new_evidence, new_event = event_snapshot(
        "run-2",
        "E-EVENT-NEW",
        date(2026, 9, 2),
    )

    order_excerpt = (
        "OASIS+ is identified by parent IDV IDV-1 for task order TO-2."
    )
    order_evidence = _evidence(
        "E-ORDER-NEW",
        run_id="run-2",
        record_id="TO-2",
        kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
        excerpt=order_excerpt,
        assertions=(
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_IDENTITY,
                quote="OASIS+ is identified by parent IDV IDV-1",
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                quote="parent IDV IDV-1 for task order TO-2",
            ),
        ),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-1",
        agency="GSA",
        name="OASIS+",
        idv_piid="IDV-1",
        evidence_ids=(order_evidence.evidence_id,),
    )
    relationship = VehicleRelationship(
        relationship_id="relationship:to-2",
        vehicle_id=identity.vehicle_id,
        kind=VehicleRelationshipKind.TASK_ORDER,
        access_posture=VehicleAccessPosture.UNKNOWN,
        order_identity=order_evidence.source_identity,
        evidence_ids=(order_evidence.evidence_id,),
    )
    order_signal = VehicleSignal(
        signal_id="signal:to-2",
        client_id="testco",
        run_id="run-2",
        scope_sha256=HASH,
        title="Task order activity under a sourced parent IDV",
        agency="GSA",
        kind=VehicleSignalKind.TASK_ORDER_ACTIVITY,
        status=VehicleSignalStatus.RESEARCH,
        vehicle=identity,
        relationship=relationship,
        records_show="The award record names the task order and parent IDV.",
        may_suggest="The route may require access through an eligible holder.",
        validate_next="Verify the current holder and ordering eligibility.",
        evidence_ids=(order_evidence.evidence_id,),
        last_checked_at=NOW - timedelta(minutes=30),
    )
    current = _document(
        run_id="run-2",
        document_id="DOC-NEW",
        evidence=(new_evidence, order_evidence),
        calendar_events=(new_event,),
        vehicle_signals=(order_signal,),
    )

    stripped_order_evidence = order_evidence.model_copy(update={
        "assertion_spans": (order_evidence.assertion_spans[0],),
    })
    unsafe_current = current.model_copy(update={
        "evidence": (new_evidence, stripped_order_evidence),
    })
    with pytest.raises(ValidationError, match="parent-IDV assertion"):
        diff_verified_watch(previous, unsafe_current)

    baseline = diff_verified_watch(None, previous)
    assert baseline.baseline_established is True
    assert baseline.changes == baseline.ticker_items == ()

    changes = diff_verified_watch(previous, current)
    assert {row.kind for row in changes.changes} == {
        WatchChangeKind.EVENT_DATE_CHANGED,
        WatchChangeKind.NEW_ORDER_ACTIVITY,
    }
    assert {
        row.source_evidence_id for row in changes.ticker_items
    } == {"E-EVENT-NEW", "E-ORDER-NEW"}
    hydrated = append_verified_watch_ticker(current, changes)
    assert hydrated.ticker_items == changes.ticker_items
    assert all(
        item.source_evidence_id in {
            evidence.evidence_id for evidence in hydrated.evidence
        }
        for item in hydrated.ticker_items
    )

    historical = tuple(
        TickerItem(
            ticker_id=f"historical-{index:02d}",
            label=f"Historical verified signal {index:02d}",
            source_evidence_id="E-EVENT-NEW",
            evidence_ids=("E-EVENT-NEW",),
            historical_context=True,
        )
        for index in range(CONTENT_BUDGETS["ticker_items"])
    )
    full_ticker = CandidateReviewDocument.model_validate({
        **current.model_dump(mode="python"),
        "ticker_items": historical,
    })
    refreshed_ticker = append_verified_watch_ticker(full_ticker, changes)
    assert refreshed_ticker.ticker_items[:len(changes.ticker_items)] \
        == changes.ticker_items
    assert len(refreshed_ticker.ticker_items) \
        == CONTENT_BUDGETS["ticker_items"]
    assert any(item.historical_context for item in refreshed_ticker.ticker_items)


def test_verified_watch_diff_never_treats_outage_absence_as_removal():
    quote = (
        "The official organizer lists the Federal data conference for "
        "September 1, 2026."
    )
    evidence = _evidence(
        "E-EVENT-PRIOR",
        run_id="run-1",
        record_id="FED-DATA-2026",
        kind=EvidenceKind.OFFICIAL_EVENT,
        tier=SourceTier.PROGRAM,
        url="https://events.example.gov/FED-DATA-2026",
        excerpt=quote,
        assertions=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.OFFICIAL_EVENT,
            quote=quote,
        ),),
    )
    timing = DateValue(
        date_id="DATE-FED-DATA-PRIOR",
        label="Conference date",
        kind=DateKind.EVENT_START,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="September 1, 2026",
        sort_date=date(2026, 9, 1),
        start=date(2026, 9, 1),
        evidence_ids=(evidence.evidence_id,),
    )
    event = EventRecord(
        event_id="event:fed-data-2026",
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        title="Federal data conference",
        kind=EventKind.CONFERENCE,
        timing=timing,
        organizer="Official organizer",
        location="Denver, CO",
        audience="Federal program and acquisition leaders",
        relevance="The agenda covers the approved capability frame.",
        validate_next="Confirm the current agenda and registration status.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=NOW - timedelta(minutes=30),
    )
    previous = _document(
        run_id="run-1",
        document_id="DOC-PRIOR",
        evidence=(evidence,),
        calendar_events=(event,),
    )
    failed_coverage = CoverageRecord(
        client_id="testco",
        run_id="run-2",
        scope_sha256=HASH,
        source="official-event-provider",
        query_family="events:standing-watch",
        state=CoverageState.FAILED,
        window_start=NOW.date(),
        window_end=NOW.date(),
        attempted_at=NOW - timedelta(minutes=5),
        query_manifest_id="event-manifest-2",
        query_id="event-query:fed-data",
        public_detail="The provider did not return a usable response.",
    )
    current = _document(
        run_id="run-2",
        document_id="DOC-CURRENT",
        coverage=(failed_coverage,),
    )

    result = diff_verified_watch(previous, current)

    assert result.changes == ()
    assert result.ticker_items == ()
    assert result.removals_evaluated is False
    assert all("remov" not in kind.value for kind in WatchChangeKind)


@pytest.mark.parametrize(("posture", "assertion", "phrase"), (
    (
        VehicleAccessPosture.DIRECT_HOLDER,
        EvidenceAssertion.VEHICLE_DIRECT_HOLDER,
        "a direct contract holder",
    ),
    (
        VehicleAccessPosture.TEAMING_REQUIRED,
        EvidenceAssertion.VEHICLE_TEAMING_REQUIRED,
        "required to team through a contract holder",
    ),
    (
        VehicleAccessPosture.CHANNEL_OR_RESELLER,
        EvidenceAssertion.VEHICLE_CHANNEL_OR_RESELLER,
        "authorized only through its named reseller channel",
    ),
))
def test_non_unknown_vehicle_access_posture_requires_exact_evidence(
    posture: VehicleAccessPosture,
    assertion: EvidenceAssertion,
    phrase: str,
):
    excerpt = f"OASIS+ IDV-1 identifies TestCo as {phrase}."
    identity_span = EvidenceAssertionSpan(
        assertion=EvidenceAssertion.VEHICLE_IDENTITY,
        quote="OASIS+ IDV-1",
    )
    generic = _evidence(
        "E-ACCESS",
        record_id="IDV-1",
        kind=EvidenceKind.VEHICLE,
        tier=SourceTier.PROGRAM,
        excerpt=excerpt,
        assertions=(identity_span,),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-1",
        agency="GSA",
        name="OASIS+",
        idv_piid="IDV-1",
        evidence_ids=(generic.evidence_id,),
    )
    relationship = VehicleRelationship(
        relationship_id="relationship:testco-access",
        vehicle_id=identity.vehicle_id,
        kind=VehicleRelationshipKind.VEHICLE_ONLY,
        access_posture=posture,
        evidence_ids=(generic.evidence_id,),
    )
    signal = VehicleSignal(
        signal_id="signal:testco-access",
        client_id="testco",
        run_id="run-1",
        scope_sha256=HASH,
        title="Sourced OASIS+ access posture",
        agency="GSA",
        kind=VehicleSignalKind.ACCESS_PATH,
        status=VehicleSignalStatus.CURRENT,
        vehicle=identity,
        relationship=relationship,
        records_show="The official source states the client's access posture.",
        may_suggest="The vehicle may provide an acquisition route.",
        validate_next="Confirm the posture remains current before pursuit.",
        evidence_ids=(generic.evidence_id,),
        last_checked_at=NOW - timedelta(minutes=30),
    )

    with pytest.raises(ValidationError, match="access posture"):
        _document(evidence=(generic,), vehicle_signals=(signal,))

    exact = generic.model_copy(update={
        "assertion_spans": (
            identity_span,
            EvidenceAssertionSpan(assertion=assertion, quote=excerpt),
        ),
    })
    document = _document(evidence=(exact,), vehicle_signals=(signal,))
    assert document.vehicle_signals[0].relationship.access_posture is posture


def test_target_level_coverage_requires_query_and_budget_is_deliberate():
    base = {
        "client_id": "testco",
        "run_id": "run-1",
        "scope_sha256": HASH,
        "source": "official_agency",
        "query_family": "vehicle_watch",
        "state": CoverageState.NOT_RUN,
        "window_start": NOW.date(),
        "window_end": NOW.date() + timedelta(days=30),
        "query_manifest_id": "manifest-1",
    }
    with pytest.raises(ValidationError, match="originating query_id"):
        CoverageRecord(**base, watch_target_id="vehicle:idv-1")
    row = CoverageRecord(
        **base,
        query_id="query-1",
        watch_target_id="vehicle:idv-1",
    )
    assert row.identity_key.endswith("query-1\x00vehicle:idv-1")
    assert CONTENT_BUDGETS["coverage_records"] == 8192


def test_corridor_can_have_one_member_but_multi_member_boundaries_must_match():
    award = _evidence(
        "E-A1", record_id="A1", kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM,
    )
    one = _corridor_candidate("C1", [("E-A1", "A1")])
    assert _document(evidence=(award,), candidates=(one,)).candidates[0] == one

    other_boundary = _boundary(
        LifecycleKind.RECOMPETE_RESEARCH,
        deadline=None,
        buyer="agency:other-office",
        access="research",
        action="validate-next-buy",
    )
    first_member = one.members[0]
    second_member = CandidateMember(
        source_identity=SourceIdentity(source_system="official", record_id="A2"),
        member_evidence_ids=("E-A2",),
        decision_boundary=other_boundary,
    )
    with pytest.raises(ValidationError, match="must match before records can cluster"):
        one.model_copy(update={"members": (first_member, second_member)}).__class__ \
            .model_validate({
                **one.model_dump(mode="python"),
                "members": [first_member, second_member],
            })
    with pytest.raises(ValidationError, match="official notice lifecycle"):
        _corridor_candidate(
            "C-LIVE", [("E-A1", "A1")],
            boundary=_boundary(LifecycleKind.LIVE_SOLICITATION))


def test_procurement_family_is_issuing_office_namespaced_or_exact_fallback():
    first = ProcurementFamilyIdentity(
        issuing_office="Office A", solicitation_number="SAME-100")
    second = ProcurementFamilyIdentity(
        issuing_office="Office B", solicitation_number="SAME-100")
    assert first.canonical_key != second.canonical_key
    fallback = ProcurementFamilyIdentity(
        fallback_notice_identity=SourceIdentity(
            source_system="sam.gov", record_id="NOTICE-1"))
    assert fallback.canonical_key == "sam.gov:notice:notice-1"
    with pytest.raises(ValidationError, match=r"office\+solicitation"):
        ProcurementFamilyIdentity(solicitation_number="SAME-100")

    unrelated = ProcurementFamilyIdentity(
        fallback_notice_identity=SourceIdentity(
            source_system="sam.gov", record_id="UNRELATED"))
    candidate = _current_candidate()
    with pytest.raises(ValidationError, match="fallback does not match"):
        CandidateUnit.model_validate({
            **candidate.model_dump(mode="python"),
            "cluster_level": ClusterLevel.PROCUREMENT_FAMILY,
            "procurement_family": unrelated,
            "members": [{
                **candidate.members[0].model_dump(mode="python"),
                "procurement_family": unrelated,
            }],
        })

    named = ProcurementFamilyIdentity(
        issuing_office="Office A", solicitation_number="SOL-100")
    exact = _current_candidate()
    family_candidate = CandidateUnit.model_validate({
        **exact.model_dump(mode="python"),
        "cluster_level": ClusterLevel.PROCUREMENT_FAMILY,
        "procurement_family": named,
        "members": [{
            **exact.members[0].model_dump(mode="python"),
            "procurement_family": named,
        }],
    })
    with pytest.raises(ValidationError, match="not bound to member notice"):
        _document(
            evidence=(_evidence(),), candidates=(family_candidate,))
    bound_notice = EvidenceRecord.model_validate({
        **_evidence().model_dump(mode="python"),
        "issuing_office": "Office A",
        "solicitation_number": "SOL-100",
    })
    assert _document(
        evidence=(bound_notice,), candidates=(family_candidate,)).candidates


def test_analyst_treatment_pipeline_value_and_probability_default_null():
    candidate = _current_candidate()
    assert candidate.analyst_decision is None
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CandidateUnit.model_validate({
            **candidate.model_dump(mode="python"),
            "recommendation": "pursue",
            "disqualified": False,
        })

    pipeline = MoneyValue(
        money_id="M-PIPE",
        basis=MoneyBasis.ANALYST_PIPELINE_VALUE,
        amount=Decimal("1000000"),
        as_of=NOW.date(),
        analyst_decision_id="D1",
    )
    decision = AnalystDecision(
        decision_id="D1",
        decided_by="Analyst One",
        decided_at=NOW,
        treatment="review further",
        pipeline_value=pipeline,
        win_probability=Decimal("0.25"),
    )
    assert decision.provenance == "analyst"
    with pytest.raises(ValidationError, match="attributed analyst decision"):
        MoneyValue(
            money_id="BAD", basis=MoneyBasis.ANALYST_PIPELINE_VALUE,
            amount=1, as_of=NOW.date())
    with pytest.raises(ValidationError, match="belongs only inside"):
        CandidateUnit.model_validate({
            **candidate.model_dump(mode="python"),
            "money": [pipeline],
        })


def test_money_bases_cannot_substitute_for_one_another():
    exact = MoneyValue(
        money_id="M1", basis=MoneyBasis.OBLIGATED_TO_DATE,
        amount=Decimal("125.50"), as_of=NOW.date(),
        source_field="generated_subawards.obligatedAmount",
        evidence_ids=("E-A1",),
    )
    assert exact.amount == Decimal("125.50")
    assert exact.display_value == "$125.5"
    published_range = MoneyValue(
        money_id="M2", basis=MoneyBasis.PUBLISHED_RANGE,
        minimum=100, maximum=200, as_of=NOW.date(),
        source_field="forecast.estimated_value_range",
        evidence_ids=("E-F1",),
    )
    assert published_range.maximum == Decimal("200")
    assert published_range.display_value == "$100 to $200"
    with pytest.raises(ValidationError, match="published range"):
        MoneyValue(
            money_id="BAD", basis=MoneyBasis.PUBLISHED_RANGE,
            amount=150, minimum=100, maximum=200, as_of=NOW.date(),
            source_field="range", evidence_ids=("E1",))
    with pytest.raises(ValidationError, match="exact money basis"):
        MoneyValue(
            money_id="BAD", basis=MoneyBasis.AWARD_CEILING,
            minimum=100, maximum=200, as_of=NOW.date(),
            source_field="ceiling", evidence_ids=("E1",))

    award = _evidence(
        "E-A1", record_id="A1", kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM)
    corridor = _corridor_candidate("C-MONEY", [("E-A1", "A1")])
    corridor = CandidateUnit.model_validate({
        **corridor.model_dump(mode="python"), "money": [exact]})
    forged = KpiTile(
        kpi_id="K-MONEY", kind=KpiKind.OBLIGATION,
        eyebrow="Obligated", value="$999,999,999",
        title="Funded history", note="Typed source amount",
        evidence_ids=("E-A1",), money_id="M1")
    with pytest.raises(ValidationError, match="must be derived"):
        _document(
            evidence=(award,), candidates=(corridor,), kpi_tiles=(forged,))
    valid = KpiTile.model_validate({
        **forged.model_dump(mode="python"), "value": exact.display_value})
    assert _document(
        evidence=(award,), candidates=(corridor,), kpi_tiles=(valid,)).kpi_tiles


def test_date_kind_status_and_precision_are_not_interchangeable():
    award_end = DateValue(
        date_id="D1", label="Award period end",
        kind=DateKind.AWARD_END_RESEARCH_CLOCK,
        status=DateStatus.RESEARCH_CLOCK,
        precision=DatePrecision.DAY,
        source_text="January 1, 2027", sort_date=date(2027, 1, 1),
        start=date(2027, 1, 1), evidence_ids=("E-A1",))
    assert award_end.status == DateStatus.RESEARCH_CLOCK
    with pytest.raises(ValidationError, match="research clock"):
        award_end.model_copy(update={"status": DateStatus.CONFIRMED}).__class__ \
            .model_validate({
                **award_end.model_dump(mode="python"),
                "status": DateStatus.CONFIRMED,
            })
    with pytest.raises(ValidationError, match="forecast dates"):
        DateValue(
            date_id="D2", label="Forecast solicitation",
            kind=DateKind.FORECAST_SOLICITATION,
            status=DateStatus.CONFIRMED,
            precision=DatePrecision.MONTH,
            source_text="February 2027", sort_date=date(2027, 2, 1),
            evidence_ids=("E-F1",))
    with pytest.raises(ValidationError, match="range precision"):
        DateValue(
            date_id="D3", label="Window", kind=DateKind.MONITOR_DATE,
            status=DateStatus.MONITOR, precision=DatePrecision.RANGE,
            source_text="January 2027", sort_date=date(2027, 1, 1),
            start=date(2027, 1, 1), evidence_ids=("E1",))
    month = DateValue(
        date_id="D4", label="Forecast month", kind=DateKind.MONITOR_DATE,
        status=DateStatus.ANTICIPATED, precision=DatePrecision.MONTH,
        source_text="April 2027", sort_date=date(2027, 4, 1),
        evidence_ids=("E1",))
    assert month.start is None
    with pytest.raises(ValidationError, match="month precision"):
        DateValue(
            date_id="D4-FORGED-MONTH",
            label="Forecast month",
            kind=DateKind.MONITOR_DATE,
            status=DateStatus.ANTICIPATED,
            precision=DatePrecision.MONTH,
            source_text="April 2027",
            sort_date=date(2026, 8, 1),
            evidence_ids=("E1",),
        )
    with pytest.raises(ValidationError, match="quarter precision"):
        DateValue(
            date_id="D4-FORGED-QUARTER",
            label="Forecast quarter",
            kind=DateKind.MONITOR_DATE,
            status=DateStatus.ANTICIPATED,
            precision=DatePrecision.QUARTER,
            source_text="FY2027 Q2",
            sort_date=date(2029, 7, 1),
            evidence_ids=("E1",),
        )
    fiscal_quarter = DateValue(
        date_id="D4-FISCAL-QUARTER",
        label="Forecast quarter",
        kind=DateKind.MONITOR_DATE,
        status=DateStatus.ANTICIPATED,
        precision=DatePrecision.QUARTER,
        source_text="FY2027 Q2",
        sort_date=date(2027, 1, 1),
        evidence_ids=("E1",),
    )
    assert fiscal_quarter.sort_date == date(2027, 1, 1)
    fiscal_year = DateValue(
        date_id="D4-FISCAL-YEAR",
        label="Fiscal year",
        kind=DateKind.MONITOR_DATE,
        status=DateStatus.MONITOR,
        precision=DatePrecision.FISCAL_YEAR,
        source_text="FY2027",
        sort_date=date(2026, 10, 1),
        evidence_ids=("E1",),
    )
    assert fiscal_year.sort_date == date(2026, 10, 1)
    with pytest.raises(ValidationError, match="synthetic asserted day"):
        DateValue.model_validate({
            **month.model_dump(mode="python"),
            "start": date(2027, 4, 1),
        })
    research_candidate = _corridor_candidate("C-DATE", [("E1", "R1")])
    confirmed_date = DateValue(
        date_id="D5", label="Purported recompete",
        kind=DateKind.CONFIRMED_RECOMPETE, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="May 1, 2027",
        sort_date=date(2027, 5, 1), start=date(2027, 5, 1),
        evidence_ids=("E1",))
    with pytest.raises(ValidationError, match="confirmed-recompete lifecycle"):
        CandidateUnit.model_validate({
            **research_candidate.model_dump(mode="python"),
            "dates": [confirmed_date],
        })


@pytest.mark.parametrize(
    ("precision", "source_text", "sort_date", "start", "end"),
    (
        (
            DatePrecision.DAY,
            "2026-08-150",
            date(2026, 8, 15),
            date(2026, 8, 15),
            None,
        ),
        (
            DatePrecision.RANGE,
            "2026-08-150 through 2026-08-200",
            date(2026, 8, 15),
            date(2026, 8, 15),
            date(2026, 8, 20),
        ),
        (
            DatePrecision.MONTH,
            "August 20260",
            date(2026, 8, 1),
            None,
            None,
        ),
    ),
)
def test_date_source_lexemes_require_numeric_boundaries(
    precision: DatePrecision,
    source_text: str,
    sort_date: date,
    start: date | None,
    end: date | None,
):
    with pytest.raises(ValidationError, match="source_text"):
        DateValue(
            date_id="D-MALFORMED",
            label="Malformed date",
            kind=DateKind.MONITOR_DATE,
            status=DateStatus.MONITOR,
            precision=precision,
            source_text=source_text,
            sort_date=sort_date,
            start=start,
            end=end,
            evidence_ids=("E1",),
        )


@pytest.mark.parametrize(
    "excerpt",
    (
        "Published 2026-08-16. Responses are due 2026-08-15.",
        "Published 2026-08-16, responses are due 2026-08-15.",
        "Published 2026-08-16 and responses are due 2026-08-15.",
        "Published 2026-08-16 (responses are due 2026-08-15).",
        "Published 2026-08-16: responses are due 2026-08-15.",
        "Published 2026-08-16 — responses are due 2026-08-15.",
        "Published 2026-08-16 / responses are due 2026-08-15.",
        "Published 2026-08-16 - responses are due 2026-08-15.",
        "Published 2026-08-16;responses are due 2026-08-15.",
    ),
)
def test_typed_date_evidence_must_prove_the_date_role_not_any_page_date(
    excerpt: str,
):
    notice = EvidenceRecord.model_validate({
        **_evidence().model_dump(mode="python"),
        "excerpt": excerpt,
    })
    publication_date = DateValue(
        date_id="D-WRONG-ROLE",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-08-16",
        sort_date=date(2026, 8, 16),
        start=date(2026, 8, 16),
        evidence_ids=(notice.evidence_id,),
    )
    true_deadline = DateValue.model_validate({
        **publication_date.model_dump(mode="python"),
        "date_id": "D-RIGHT-ROLE",
        "source_text": "2026-08-15",
        "sort_date": date(2026, 8, 15),
        "start": date(2026, 8, 15),
    })

    assert evidence_supports_date_value(publication_date, (notice,)) is False
    assert evidence_supports_date_value(true_deadline, (notice,)) is True


def test_confirmed_recompete_requires_explicit_official_evidence():
    official = _evidence(
        "E-R1", record_id="R1", kind=EvidenceKind.AGENCY_ANNOUNCEMENT,
        tier=SourceTier.PROGRAM,
    )
    recompete_quote = "The agency confirms this requirement will recompete on March 1, 2027."
    official = EvidenceRecord.model_validate({
        **official.model_dump(mode="python"),
        "excerpt": recompete_quote,
        "assertion_spans": [{
            "assertion": EvidenceAssertion.EXPLICIT_RECOMPETE,
            "quote": recompete_quote,
        }],
    })
    boundary = _boundary(
        LifecycleKind.CONFIRMED_RECOMPETE,
        deadline=None,
        access="forecasted-route",
        action="validate-confirmed-recompete",
    )
    candidate = _corridor_candidate("C-R1", [("E-R1", "R1")],
                                    boundary=boundary)
    confirmed = DateValue(
        date_id="D-R1", label="Official recompete date",
        kind=DateKind.CONFIRMED_RECOMPETE, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="March 1, 2027",
        sort_date=date(2027, 3, 1), start=date(2027, 3, 1),
        evidence_ids=("E-R1",))
    candidate = CandidateUnit.model_validate({
        **candidate.model_dump(mode="python"), "dates": [confirmed]})
    assert _document(evidence=(official,), candidates=(candidate,)).candidates

    generic_announcement = EvidenceRecord.model_validate({
        **official.model_dump(mode="python"),
        "evidence_id": "E-GENERIC",
        "source_identity": SourceIdentity(
            source_system="official", record_id="GENERIC"),
        "source_url": "https://example.gov/records/GENERIC",
        "excerpt": "The agency published a general program update.",
        "assertion_spans": [],
    })
    generic_candidate = _corridor_candidate(
        "C-GENERIC", [("E-GENERIC", "GENERIC")], boundary=boundary)
    generic_candidate = CandidateUnit.model_validate({
        **generic_candidate.model_dump(mode="python"),
        "dates": [{
            **confirmed.model_dump(mode="python"),
            "date_id": "D-GENERIC",
            "evidence_ids": ["E-GENERIC"],
        }],
    })
    with pytest.raises(ValidationError, match="explicit official evidence"):
        _document(
            evidence=(generic_announcement,), candidates=(generic_candidate,))

    unconfirmed = CandidateUnit.model_validate({
        **candidate.model_dump(mode="python"), "dates": []})
    with pytest.raises(ValidationError, match="explicit confirmed date"):
        _document(evidence=(official,), candidates=(unconfirmed,))

    award = _evidence(
        "E-AWARD-END", record_id="AWARD-END",
        kind=EvidenceKind.AWARD, tier=SourceTier.PROGRAM)
    award_candidate = _corridor_candidate(
        "C-AWARD-END", [("E-AWARD-END", "AWARD-END")],
        boundary=boundary)
    award_candidate = CandidateUnit.model_validate({
        **award_candidate.model_dump(mode="python"),
        "dates": [{
            **confirmed.model_dump(mode="python"),
            "date_id": "D-AWARD-END",
            "evidence_ids": ["E-AWARD-END"],
        }],
    })
    with pytest.raises(ValidationError, match="explicit official evidence"):
        _document(evidence=(award,), candidates=(award_candidate,))


def test_events_require_official_sources_and_past_events_cannot_stay_active():
    official = _evidence(
        "E-EVENT", record_id="EV1", kind=EvidenceKind.OFFICIAL_EVENT,
        tier=SourceTier.PROGRAM,
        url="https://events.example.gov/EV1",
    )
    event_quote = "The official organizer lists the Federal data conference for September 1, 2026."
    official = EvidenceRecord.model_validate({
        **official.model_dump(mode="python"),
        "excerpt": event_quote,
        "assertion_spans": [{
            "assertion": EvidenceAssertion.OFFICIAL_EVENT,
            "quote": event_quote,
        }],
    })
    timing = DateValue(
        date_id="DATE-EVENT", label="Conference date",
        kind=DateKind.EVENT_START, status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY, source_text="September 1, 2026",
        sort_date=date(2026, 9, 1), start=date(2026, 9, 1),
        evidence_ids=("E-EVENT",))
    event = EventRecord(
        event_id="EV1", client_id="testco", run_id="run-1",
        scope_sha256=HASH, title="Federal data conference",
        kind=EventKind.CONFERENCE, timing=timing,
        organizer="Official organizer", location="Denver, CO",
        audience="Federal program and acquisition leaders",
        relevance="The agenda covers the client capability area.",
        validate_next="Confirm the current agenda and registration status.",
        evidence_ids=("E-EVENT",),
        registration_url="https://events.example.gov/EV1",
        registration_evidence_ids=("E-EVENT",),
        last_checked_at=NOW - timedelta(minutes=30),
    )
    assert _document(evidence=(official,), calendar_events=(event,)).calendar_events
    with pytest.raises(ValidationError, match="separate from relevance"):
        EventRecord.model_validate({
            **event.model_dump(mode="python"),
            "validate_next": event.relevance,
        })
    evidence_newer_than_check = EvidenceRecord.model_validate({
        **official.model_dump(mode="python"),
        "retrieved_at": NOW - timedelta(minutes=10),
    })
    with pytest.raises(ValidationError, match="predates accepted evidence"):
        _document(
            evidence=(evidence_newer_than_check,),
            calendar_events=(event,),
        )

    no_event_assertion = EvidenceRecord.model_validate({
        **official.model_dump(mode="python"),
        "evidence_id": "E-GENERIC-EVENT",
        "source_identity": SourceIdentity(
            source_system="official", record_id="GENERIC-EVENT"),
        "source_kind": EvidenceKind.AGENCY_ANNOUNCEMENT,
        "source_url": "https://example.gov/records/GENERIC-EVENT",
        "excerpt": "The agency issued a general program announcement.",
        "assertion_spans": [],
    })
    generic_event = EventRecord.model_validate({
        **event.model_dump(mode="python"),
        "event_id": "EV-GENERIC",
        "timing": {
            **timing.model_dump(mode="python"),
            "date_id": "DATE-GENERIC",
            "evidence_ids": ["E-GENERIC-EVENT"],
        },
        "evidence_ids": ["E-GENERIC-EVENT"],
        "registration_url": None,
        "registration_evidence_ids": [],
    })
    with pytest.raises(ValidationError, match="official event assertion"):
        _document(
            evidence=(no_event_assertion,), calendar_events=(generic_event,))

    attendance_claim = EventRecord.model_validate({
        **event.model_dump(mode="python"),
        "event_id": "EV-ATTENDANCE",
        "attending_agencies": ["DHS"],
        "attendance_evidence_ids": ["E-EVENT"],
    })
    with pytest.raises(ValidationError, match="official agenda assertion"):
        _document(evidence=(official,), calendar_events=(attendance_claim,))

    aggregator = _evidence(
        "E-AGG", record_id="AGG1", kind=EvidenceKind.WEB_DISCOVERY,
        tier=SourceTier.DISCOVERY, url="https://events.example.com/listing",
        official=False, primary=False,
    )
    aggregator_event = EventRecord.model_validate({
        **event.model_dump(mode="python"),
        "event_id": "EV-AGG",
        "timing": {
            **timing.model_dump(mode="python"),
            "date_id": "DATE-AGG", "evidence_ids": ["E-AGG"],
        },
        "evidence_ids": ["E-AGG"],
        "registration_url": None,
        "registration_evidence_ids": [],
    })
    with pytest.raises(ValidationError, match="official organizer"):
        _document(evidence=(aggregator,), calendar_events=(aggregator_event,))

    forecast = _evidence(
        "E-FORECAST", record_id="FORECAST-1",
        kind=EvidenceKind.AGENCY_FORECAST, tier=SourceTier.PROGRAM)
    forecast_event = EventRecord.model_validate({
        **event.model_dump(mode="python"),
        "event_id": "EV-FORECAST",
        "timing": {
            **timing.model_dump(mode="python"),
            "date_id": "DATE-FORECAST",
            "evidence_ids": ["E-FORECAST"],
        },
        "evidence_ids": ["E-FORECAST"],
        "registration_url": None,
        "registration_evidence_ids": [],
    })
    with pytest.raises(ValidationError, match="official organizer"):
        _document(evidence=(forecast,), calendar_events=(forecast_event,))

    past = EventRecord.model_validate({
        **event.model_dump(mode="python"),
        "timing": {
            **timing.model_dump(mode="python"),
            "source_text": "January 1, 2026",
            "sort_date": date(2026, 1, 1),
            "start": date(2026, 1, 1),
        },
    })
    past_quote = (
        "The official organizer lists the Federal data conference for "
        "January 1, 2026."
    )
    past_official = EvidenceRecord.model_validate({
        **official.model_dump(mode="python"),
        "excerpt": past_quote,
        "assertion_spans": ({
            "assertion": EvidenceAssertion.OFFICIAL_EVENT,
            "quote": past_quote,
        },),
    })
    with pytest.raises(ValidationError, match="past events"):
        _document(evidence=(past_official,), calendar_events=(past,))


def test_coverage_states_preserve_partial_failed_stale_and_not_run():
    base = {
        "client_id": "testco",
        "run_id": "run-1",
        "scope_sha256": HASH,
        "window_start": NOW.date(),
        "window_end": NOW.date() + timedelta(days=365),
        "query_manifest_id": "manifest-1",
    }
    rows = (
        CoverageRecord(
            **base,
            source="sam.gov", query_family="notices",
            state=CoverageState.PARTIAL, attempted_at=NOW,
            records_returned=10),
        CoverageRecord(
            **base,
            source="usaspending.gov", query_family="awards",
            state=CoverageState.FAILED, attempted_at=NOW,
            public_detail="Official source did not return a usable response."),
        CoverageRecord(
            **base,
            source="agency-forecasts", query_family="forecasts",
            state=CoverageState.STALE_SNAPSHOT, attempted_at=NOW,
            records_returned=3),
        CoverageRecord(
            **base,
            source="events", query_family="conferences",
            state=CoverageState.NOT_RUN),
    )
    assert {row.state for row in _document(coverage=rows).coverage} == {
        CoverageState.PARTIAL,
        CoverageState.FAILED,
        CoverageState.STALE_SNAPSHOT,
        CoverageState.NOT_RUN,
    }
    assert CoverageRecord(
        **base,
        source="agency-cache", query_family="events",
        state=CoverageState.CURRENT_FALLBACK_SNAPSHOT,
        attempted_at=NOW, records_returned=2,
    ).state == CoverageState.CURRENT_FALLBACK_SNAPSHOT
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CoverageRecord(
            **base,
            source="source", query_family="lane", state=CoverageState.FAILED,
            attempted_at=NOW, raw_error="secret vendor stack trace")
    with pytest.raises(ValidationError, match="result counts"):
        CoverageRecord(
            **base,
            source="events", query_family="conferences",
            state=CoverageState.NOT_RUN, records_returned=1)


def test_sections_controls_faces_and_old_labels_are_fail_closed():
    sparse = _document()
    assert sparse.sections == SECTION_ORDER
    assert sparse.candidates == ()
    assert sparse.calendar_events == ()
    with pytest.raises(ValidationError, match="exact eight sections"):
        CandidateReviewDocument.model_validate({
            **sparse.model_dump(mode="python"),
            "sections": list(reversed(SECTION_ORDER)),
        })
    with pytest.raises(ValidationError, match="forbidden language"):
        EvidenceBoundClaim(
            block_id="OLD", client_id="testco",
            heading="Priority federal routes",
            records_show="Records show a route.",
            may_suggest="It may suggest activity.",
            validate_next="Validate the activity.",
            evidence_ids=("E1",))
    with pytest.raises(ValidationError, match="forbidden language"):
        EvidenceBoundClaim(
            block_id="FACES", client_id="testco", heading="Faces",
            records_show="Records show public leadership context.",
            may_suggest="It may suggest a research path.",
            validate_next="Validate only if relevant.",
            evidence_ids=("E1",))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CandidateReviewDocument.model_validate({
            **sparse.model_dump(mode="python"),
            "faces": [],
            "account_ownership": [],
        })
    with pytest.raises(ValidationError, match="document controls"):
        ProductControlContract(
            document_controls=(DocumentControl.EDIT, DocumentControl.SAVE))
    assert ProductControlContract().contextual_editor_controls == (
        ContextualEditorControl.UNDO_SOFT_DELETE,
    )
    with pytest.raises(ValidationError, match="contextual soft-delete Undo"):
        ProductControlContract(contextual_editor_controls=())


def test_content_budgets_are_maximums_not_quotas():
    assert _document().candidates == ()
    evidence = []
    candidates = []
    for index in range(CONTENT_BUDGETS["candidates"] + 1):
        eid, rid, cid = f"E-A{index}", f"A{index}", f"C{index}"
        evidence.append(_evidence(
            eid, record_id=rid, kind=EvidenceKind.AWARD,
            tier=SourceTier.PROGRAM))
        candidates.append(_corridor_candidate(cid, [(eid, rid)]))
    with pytest.raises(ValidationError, match="at most 12"):
        _document(evidence=tuple(evidence), candidates=tuple(candidates))


def test_editor_state_represents_text_size_asset_geometry_and_client_binding():
    evidence = _evidence()
    candidate = _current_candidate()
    text_slot = EditableTextSlot(
        edit_id="candidate:C1:title", baseline_text="Current notice N1",
        font_size_px=22, line_height=1.1, single_line=True)
    asset_slot = AssetSlot(
        asset_slot_id="agency:test-agency:seal",
        role=AssetRole.AGENCY_SEAL,
        entity_id="test-agency",
        alt_text="Test Agency seal",
        baseline_src="data:image/png;base64,AA==")
    editor = EditorState(
        client_id="testco", document_id="DOC-1",
        baseline_document_sha256="e" * 64,
        candidate_order=("C1",),
        text_edits=(TextEditState(
            edit_id=text_slot.edit_id, html_or_text="Edited title",
            font_size_px=26, line_height=1.2),),
        asset_edits=(AssetEditState(
            asset_slot_id=asset_slot.asset_slot_id,
            src="data:image/png;base64,AQ==", width_px=120, height_px=96,
            fit=AssetFit.COVER, position_x=35, position_y=60),),
    )
    document = CandidateReviewDocument(
        document_id="DOC-1", baseline_document_sha256="e" * 64,
        client_name="TestCo", binding=_binding(),
        as_of=NOW, generated_at=NOW - timedelta(minutes=1),
        evidence=(evidence,), candidates=(candidate,),
        text_slots=(text_slot,), asset_slots=(asset_slot,),
        editor_state=editor,
    )
    assert document.editor_state.asset_edits[0].position_y == 60
    with pytest.raises(ValidationError, match="another client or document"):
        CandidateReviewDocument.model_validate({
            **document.model_dump(mode="python"),
            "editor_state": {
                **editor.model_dump(mode="python"),
                "client_id": "other-client",
            },
        })
    with pytest.raises(ValidationError, match="another document baseline"):
        CandidateReviewDocument.model_validate({
            **document.model_dump(mode="python"),
            "editor_state": {
                **editor.model_dump(mode="python"),
                "baseline_document_sha256": "f" * 64,
            },
        })
    with pytest.raises(ValidationError, match="embedded data images"):
        AssetSlot(
            asset_slot_id="agency:test-agency:seal",
            role=AssetRole.AGENCY_SEAL,
            entity_id="test-agency",
            alt_text="Test Agency seal",
            baseline_src="https://example.gov/seal.png")
    with pytest.raises(ValidationError, match="single-line baseline"):
        EditableTextSlot(
            edit_id="bad-title", baseline_text="Line one\nLine two",
            font_size_px=22, line_height=1.1, single_line=True)
    newline_editor = EditorState.model_validate({
        **editor.model_dump(mode="python"),
        "text_edits": [{
            **editor.text_edits[0].model_dump(mode="python"),
            "html_or_text": "Line one\nLine two",
        }],
    })
    with pytest.raises(ValidationError, match="single-line text edit"):
        CandidateReviewDocument.model_validate({
            **document.model_dump(mode="python"),
            "editor_state": newline_editor,
        })


def test_evidence_cannot_postdate_the_frozen_document_as_of():
    future = EvidenceRecord.model_validate({
        **_evidence().model_dump(mode="python"),
        "retrieved_at": NOW + timedelta(minutes=1),
        "verified_at": NOW + timedelta(minutes=2),
    })
    with pytest.raises(ValidationError, match="postdates document as-of"):
        _document(evidence=(future,))


def test_overlapping_candidate_member_ownership_is_rejected():
    award = _evidence(
        "E-A1", record_id="A1", kind=EvidenceKind.AWARD,
        tier=SourceTier.PROGRAM)
    first = _corridor_candidate("C1", [("E-A1", "A1")])
    second = _corridor_candidate("C2", [("E-A1", "A1")])
    with pytest.raises(ValidationError, match="overlapping candidate units"):
        _document(evidence=(award,), candidates=(first, second))


def test_search_concepts_are_preliminary_and_do_not_imply_disposition():
    concepts = SearchConcepts(
        client_id="testco",
        keywords=("network performance", "packet visibility"),
        naics_codes=("541512",),
        psc_codes=("DA01",),
    )
    assert _document(search_concepts=concepts).search_concepts == concepts
    with pytest.raises(ValidationError, match="NAICS"):
        SearchConcepts(client_id="testco", naics_codes=("54151",))
