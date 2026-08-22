"""Adversarial acceptance tests for the Candidate Review v1 candidate engine.

These tests intentionally exercise the product boundary instead of internal
helpers.  Candidate Review surfaces evidence-bound units for analyst review;
it does not make pursue/no-bid decisions, and ranking may change only display
order/compression--never source truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import random

import pytest

from agents.candidate_review_v1.candidate_engine import (
    CandidateBuildResult,
    CandidateRankSignals,
    CandidateSeed,
    RankedCandidate,
    build_candidate_inventory,
)
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateKind,
    CandidateReviewDocument,
    CandidateUnit,
    ClusterLevel,
    DateKind,
    DatePrecision,
    DateStatus,
    DateValue,
    DecisionBoundary,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    EditorState,
    LifecycleKind,
    MoneyBasis,
    MoneyValue,
    NoticeRole,
    NoticeStatus,
    ProcurementFamilyIdentity,
    SourceIdentity,
    SourceTier,
)


NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
HASH = "a" * 64
OTHER_HASH = "b" * 64
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "candidate_review_v1"


def _binding(
    client_id: str = "testco",
    *,
    run_id: str = "run-1",
    scope_hash: str = HASH,
) -> ArtifactBinding:
    names = {
        "testco": "TestCo",
        "otherco": "OtherCo",
        "mark43": "Mark43",
        "imerit": "iMerit",
        "riverbed": "Riverbed",
    }
    return ArtifactBinding(
        client_id=client_id,
        client_name=names[client_id],
        run_id=run_id,
        scope_designator="all-federal",
        scope_sha256=scope_hash,
        profile_sha256="c" * 64,
        evidence_snapshot_sha256="d" * 64,
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


def _narrative(*, cautions: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "records_show":
            "Official records show one reviewable federal buying signal.",
        "may_suggest":
            "The evidence may suggest a capability-aligned buying path.",
        "validate_next":
            "Validate requirements, timing, eligibility, and access.",
        "cluster_reason":
            "The records resolve to the same analyst review action.",
        "strategic_action":
            "Research the buyer and validate the acquisition path.",
        "distinctness_explanation":
            "The decision boundary defines a distinct action.",
        "inference_chain":
            "Primary record to buying signal to analyst validation.",
        "falsifier":
            "The authoritative record no longer supports the requirement.",
        "watch_trigger":
            "A new notice, amendment, forecast, or budget record appears.",
        "cautions": cautions,
    }


def _ranking(score: float = 0.8) -> CandidateRankSignals:
    """Give every declared rank dimension the same controlled score."""

    return CandidateRankSignals(
        source_authority=score,
        decision_specificity=score,
        timing_strength=score,
        recency=score,
        capability_fit=score,
    )


def _evidence(
    evidence_id: str,
    record_id: str,
    *,
    client_id: str = "testco",
    run_id: str = "run-1",
    scope_hash: str = HASH,
    kind: EvidenceKind = EvidenceKind.NOTICE,
    status: NoticeStatus = NoticeStatus.ACTIVE,
    notice_role: NoticeRole = NoticeRole.END_USER_REQUIREMENT,
    title: str | None = None,
    source_system: str | None = None,
    source_url: str | None = None,
    issuing_office: str | None = None,
    solicitation_number: str | None = None,
    published_date: date | None = date(2026, 7, 20),
    effective_date: date | None = None,
    excerpt: str = "The official record describes the requirement and timing.",
    assertions: tuple[EvidenceAssertionSpan, ...] = (),
    official: bool | None = None,
    primary: bool | None = None,
) -> EvidenceRecord:
    notice = kind == EvidenceKind.NOTICE
    event = kind in (EvidenceKind.OFFICIAL_EVENT, EvidenceKind.ORGANIZER_EVENT)
    discovery = kind in (EvidenceKind.NEWS, EvidenceKind.WEB_DISCOVERY)
    if source_system is None:
        source_system = (
            "sam.gov" if notice else "event-organizer" if event else "official"
        )
    if source_url is None:
        source_url = (
            f"https://sam.gov/opp/{record_id}/view"
            if notice
            else f"https://example.gov/records/{record_id}"
        )
    tier = (
        SourceTier.NOTICE
        if notice
        else SourceTier.DISCOVERY
        if discovery
        else SourceTier.PROGRAM
    )
    if official is None:
        official = not discovery
    if primary is None:
        primary = not discovery
    return EvidenceRecord(
        evidence_id=evidence_id,
        client_id=client_id,
        run_id=run_id,
        scope_sha256=scope_hash,
        source_identity=SourceIdentity(
            source_system=source_system,
            record_id=record_id,
        ),
        source_tier=tier,
        source_kind=kind,
        source_name="Official source" if not discovery else "Discovery source",
        source_url=source_url,
        retrieved_at=NOW - timedelta(hours=2),
        title=title or f"Record {record_id}",
        excerpt=excerpt,
        published_date=published_date,
        effective_date=effective_date,
        official_source=official,
        primary_source=primary,
        supports=(EvidenceUse.REQUIREMENT, EvidenceUse.TIMING),
        notice_status=status if notice else None,
        notice_role=notice_role if notice else None,
        verified_at=NOW - timedelta(hours=1) if notice else None,
        assertion_spans=assertions,
        issuing_office=issuing_office if notice else None,
        solicitation_number=solicitation_number if notice else None,
    )


@dataclass(frozen=True)
class _SeedInput:
    evidence: EvidenceRecord
    seed: CandidateSeed


def _input(
    evidence: EvidenceRecord,
    *,
    boundary: DecisionBoundary | None = None,
    agency: str = "Agency A",
    title: str | None = None,
    family: ProcurementFamilyIdentity | None = None,
    corridor_id: str | None = None,
    score: float = 0.8,
    dates: tuple[DateValue, ...] = (),
    money: tuple[MoneyValue, ...] = (),
    supporting_evidence_ids: tuple[str, ...] = (),
    counterevidence_ids: tuple[str, ...] = (),
    cautions: tuple[str, ...] = (),
) -> _SeedInput:
    if boundary is None:
        boundary = _boundary()
    if family and not family.fallback_notice_identity:
        assert (evidence.issuing_office or "").casefold() \
            == family.issuing_office.casefold()
        assert (evidence.solicitation_number or "").casefold() \
            == family.solicitation_number.casefold()
    return _SeedInput(evidence=evidence, seed=CandidateSeed(
        anchor_evidence_id=evidence.evidence_id,
        boundary=boundary,
        agency=agency,
        title=title or evidence.title,
        corridor_id=corridor_id,
        supporting_evidence_ids=supporting_evidence_ids,
        counterevidence_ids=counterevidence_ids,
        dates=dates,
        money=money,
        rank_signals=_ranking(score),
        diversity_key=agency.casefold(),
        **_narrative(cautions=cautions),
    ))


@dataclass(frozen=True)
class _EngineConfig:
    binding: ArtifactBinding
    as_of: datetime
    max_candidates: int | None
    additional_evidence: tuple[EvidenceRecord, ...] = ()


def _config(
    client_id: str = "testco",
    *,
    run_id: str = "run-1",
    scope_hash: str = HASH,
    max_candidates: int | None = None,
    additional_evidence: tuple[EvidenceRecord, ...] = (),
) -> _EngineConfig:
    return _EngineConfig(
        binding=_binding(client_id, run_id=run_id, scope_hash=scope_hash),
        as_of=NOW,
        max_candidates=max_candidates,
        additional_evidence=additional_evidence,
    )


def build_candidate_projection(
    inputs: tuple[_SeedInput, ...] | list[_SeedInput],
    config: _EngineConfig,
) -> CandidateBuildResult:
    """Exercise the public Chunk 2 API while keeping test setup readable."""

    maximum = config.max_candidates
    kwargs = {} if maximum is None else {"max_candidates": maximum}
    return build_candidate_inventory(
        binding=config.binding,
        as_of=config.as_of,
        evidence=(
            *(row.evidence for row in inputs),
            *config.additional_evidence,
        ),
        seeds=tuple(row.seed for row in inputs),
        **kwargs,
    )


def _record_ids(candidate) -> tuple[str, ...]:
    return tuple(member.source_identity.record_id for member in candidate.members)


def _award_end_seed_input() -> _SeedInput:
    excerpt = "The current award period ends September 30, 2027."
    award = _evidence(
        "E-AWARD",
        "AWARD-1",
        kind=EvidenceKind.AWARD,
        excerpt=excerpt,
        assertions=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.AWARD_PERIOD_END,
            quote="award period ends September 30, 2027",
        ),),
    )
    timing = DateValue(
        date_id="D-AWARD-END",
        label="Award end research clock",
        kind=DateKind.AWARD_END_RESEARCH_CLOCK,
        status=DateStatus.RESEARCH_CLOCK,
        precision=DatePrecision.DAY,
        source_text="September 30, 2027",
        sort_date=date(2027, 9, 30),
        start=date(2027, 9, 30),
        evidence_ids=(award.evidence_id,),
    )
    return _input(
        award,
        boundary=_boundary(
            LifecycleKind.RECOMPETE_RESEARCH,
            deadline=None,
            access="research",
            action="validate-next-buy",
        ),
        corridor_id="corridor:award-end",
        dates=(timing,),
    )


def _obligated_money(evidence_id: str, money_id: str) -> MoneyValue:
    return MoneyValue(
        money_id=money_id,
        basis=MoneyBasis.OBLIGATED_TO_DATE,
        amount=Decimal("1000000"),
        as_of=date(2026, 7, 20),
        source_field="obligated_amount",
        evidence_ids=(evidence_id,),
    )


def _award_money_input(
    evidence_id: str,
    record_id: str,
    corridor_id: str,
    buyer: str,
    money_id: str,
) -> _SeedInput:
    award = _evidence(evidence_id, record_id, kind=EvidenceKind.AWARD)
    boundary = _boundary(
        LifecycleKind.AWARD_HISTORY,
        buyer=buyer,
        deadline=None,
        program=f"program:{record_id.casefold()}",
        access="research",
        action="review-award-history",
    )
    return _input(
        award,
        boundary=boundary,
        corridor_id=corridor_id,
        money=(_obligated_money(evidence_id, money_id),),
    )


def test_exact_record_identity_collapses_duplicates_without_losing_evidence_links():
    first = _evidence("E-1", "NOTICE-1")
    duplicate = _evidence("E-2", "NOTICE-1")

    result = build_candidate_projection(
        (_input(first), _input(duplicate)),
        _config(),
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.cluster_level == ClusterLevel.EXACT_RECORD
    assert len(candidate.members) == 1
    assert candidate.members[0].member_evidence_ids == ("E-1",)
    assert tuple(row.evidence_id for row in result.evidence) == ("E-1",)
    assert result.evidence_alias_map == {"E-2": "E-1"}
    incoming_by_id = {row.evidence_id: row for row in (first, duplicate)}
    canonical_by_id = {row.evidence_id: row for row in result.evidence}
    resolved_urls = {
        str(canonical_by_id[result.evidence_alias_map.get(eid, eid)].source_url)
        for eid in incoming_by_id
    }
    assert resolved_urls == {
        "https://sam.gov/opp/NOTICE-1/view",
    }
    hydrated = CandidateReviewDocument(
        document_id="DOC-ENGINE-1",
        baseline_document_sha256="e" * 64,
        client_name="TestCo",
        binding=_binding(),
        as_of=NOW,
        generated_at=NOW - timedelta(minutes=1),
        evidence=result.evidence,
        candidates=result.candidates,
        editor_state=EditorState(
            client_id="testco",
            document_id="DOC-ENGINE-1",
            baseline_document_sha256="e" * 64,
            candidate_order=tuple(
                item.candidate_id for item in result.candidates),
        ),
    )
    assert hydrated.candidates == result.candidates
    assert len({row.source_identity.canonical_key for row in hydrated.evidence}) \
        == len(hydrated.evidence)


def test_exact_identity_with_conflicting_decision_boundaries_fails_closed():
    first = _input(_evidence("E-1", "NOTICE-1"))
    conflicting = _input(
        _evidence("E-2", "NOTICE-1"),
        boundary=_boundary(buyer="agency:office-b"),
    )

    with pytest.raises(ValueError, match="(?i)(conflict|boundary|identity)"):
        build_candidate_projection((first, conflicting), _config())


def test_exact_identity_with_conflicting_source_observations_fails_closed():
    first = _input(_evidence(
        "E-1", "NOTICE-1", title="Original official title"))
    conflicting = _input(_evidence(
        "E-2", "NOTICE-1", title="Conflicting official title"))

    with pytest.raises(ValueError, match="(?i)(conflict|identity|observation)"):
        build_candidate_projection((first, conflicting), _config())


def test_procurement_amendment_family_groups_only_one_decision_boundary():
    family = ProcurementFamilyIdentity(
        issuing_office="Office A",
        solicitation_number="SOL-100",
    )
    inputs = tuple(
        _input(
            _evidence(
                f"E-{number}",
                f"NOTICE-{number}",
                issuing_office="Office A",
                solicitation_number="SOL-100",
            ),
            family=family,
        )
        for number in (1, 2)
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.cluster_level == ClusterLevel.PROCUREMENT_FAMILY
    assert candidate.procurement_family == family
    assert _record_ids(candidate) == ("NOTICE-1", "NOTICE-2")
    assert all(member.procurement_family == family for member in candidate.members)


@pytest.mark.parametrize(
    ("field", "changed"),
    (
        ("buyer_key", "agency:office-b"),
        ("deadline", date(2026, 8, 31)),
        ("eligibility_key", "small-business"),
        ("lifecycle", LifecycleKind.MARKET_RESEARCH),
        ("program_key", "program-b"),
        ("access_route_key", "vehicle:gwac-x"),
        ("analyst_action_key", "validate-vehicle-access"),
    ),
)
def test_family_records_stay_separate_when_any_decision_dimension_differs(
    field: str,
    changed,
):
    family = ProcurementFamilyIdentity(
        issuing_office="Office A",
        solicitation_number="SOL-100",
    )
    base = _boundary()
    second_boundary = base.model_copy(update={field: changed})
    first = _input(
        _evidence(
            "E-1", "NOTICE-1",
            issuing_office="Office A", solicitation_number="SOL-100",
        ),
        boundary=base,
        family=family,
    )
    second = _input(
        _evidence(
            "E-2", "NOTICE-2",
            issuing_office="Office A", solicitation_number="SOL-100",
        ),
        boundary=second_boundary,
        family=family,
    )

    result = build_candidate_projection((first, second), _config())

    assert len(result.candidates) == 2
    assert sorted(len(candidate.members) for candidate in result.candidates) == [1, 1]
    assert {candidate.members[0].decision_boundary for candidate in result.candidates} \
        == {base, second_boundary}


def test_same_solicitation_number_from_different_offices_never_merges():
    first_family = ProcurementFamilyIdentity(
        issuing_office="Office A", solicitation_number="SOL-100")
    second_family = ProcurementFamilyIdentity(
        issuing_office="Office B", solicitation_number="SOL-100")
    inputs = (
        _input(
            _evidence(
                "E-1", "NOTICE-1",
                issuing_office="Office A", solicitation_number="SOL-100",
            ),
            family=first_family,
        ),
        _input(
            _evidence(
                "E-2", "NOTICE-2",
                issuing_office="Office B", solicitation_number="SOL-100",
            ),
            family=second_family,
        ),
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 2
    assert {candidate.members[0].source_identity.record_id
            for candidate in result.candidates} == {
        "NOTICE-1", "NOTICE-2",
    }
    represented_families = {
        candidate.procurement_family.canonical_key
        for candidate in result.candidates
        if candidate.procurement_family is not None
    }
    assert represented_families.issubset({
        first_family.canonical_key,
        second_family.canonical_key,
    })


def test_ambiguous_current_amendments_with_competing_deadlines_are_not_collapsed():
    """Without a provably effective amendment, the engine cannot pick a deadline."""

    family = ProcurementFamilyIdentity(
        issuing_office="Office A", solicitation_number="SOL-100")
    inputs = (
        _input(
            _evidence(
                "E-BASE", "NOTICE-BASE",
                issuing_office="Office A", solicitation_number="SOL-100",
                effective_date=date(2026, 7, 20),
            ),
            boundary=_boundary(deadline=date(2026, 8, 1)),
            family=family,
        ),
        _input(
            _evidence(
                "E-AMEND", "NOTICE-AMEND",
                issuing_office="Office A", solicitation_number="SOL-100",
                effective_date=date(2026, 7, 20),
            ),
            boundary=_boundary(deadline=date(2026, 8, 15)),
            family=family,
        ),
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 2
    assert {candidate.members[0].decision_boundary.deadline
            for candidate in result.candidates} == {
        date(2026, 8, 1), date(2026, 8, 15),
    }


def test_later_effective_amendment_controls_family_deadline_without_losing_history():
    family = ProcurementFamilyIdentity(
        issuing_office="Office A", solicitation_number="SOL-100")
    original = _evidence(
        "E-BASE", "NOTICE-BASE",
        issuing_office="Office A", solicitation_number="SOL-100",
        effective_date=date(2026, 7, 10),
    )
    amendment = _evidence(
        "E-AMEND", "NOTICE-AMEND",
        issuing_office="Office A", solicitation_number="SOL-100",
        effective_date=date(2026, 7, 20),
    )
    inputs = (
        _input(
            original,
            boundary=_boundary(deadline=date(2026, 8, 1)),
            family=family,
        ),
        _input(
            amendment,
            boundary=_boundary(deadline=date(2026, 8, 15)),
            family=family,
        ),
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.cluster_level == ClusterLevel.PROCUREMENT_FAMILY
    assert set(_record_ids(candidate)) == {"NOTICE-BASE", "NOTICE-AMEND"}
    assert {
        member.decision_boundary.deadline for member in candidate.members
    } == {date(2026, 8, 15)}
    assert {row.evidence_id for row in result.evidence} == {
        original.evidence_id, amendment.evidence_id,
    }


def test_strategic_corridor_clusters_only_materially_same_analyst_action():
    boundary = _boundary(
        LifecycleKind.RECOMPETE_RESEARCH,
        deadline=None,
        access="research",
        action="validate-next-buy",
    )
    inputs = tuple(
        _input(
            _evidence(f"E-{number}", f"AWARD-{number}", kind=EvidenceKind.AWARD),
            boundary=boundary,
            corridor_id="corridor:network-estate",
        )
        for number in (1, 2, 3)
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.kind == CandidateKind.RESEARCH_CORRIDOR
    assert candidate.cluster_level == ClusterLevel.STRATEGIC_CORRIDOR
    assert _record_ids(candidate) == ("AWARD-1", "AWARD-2", "AWARD-3")
    assert candidate.strategic_corridor.decision_boundary == boundary
    assert candidate.cluster_reason
    assert candidate.strategic_action
    assert candidate.distinctness_explanation


@pytest.mark.parametrize(
    ("field", "changed"),
    (
        ("buyer_key", "agency:office-b"),
        ("deadline", date(2027, 1, 1)),
        ("eligibility_key", "vehicle-holders-only"),
        ("lifecycle", LifecycleKind.AWARD_HISTORY),
        ("program_key", "program-b"),
        ("access_route_key", "vehicle:gwac-x"),
        ("analyst_action_key", "validate-budget-owner"),
    ),
)
def test_same_corridor_label_cannot_override_a_distinct_decision_boundary(
    field: str,
    changed,
):
    base = _boundary(
        LifecycleKind.RECOMPETE_RESEARCH,
        deadline=None,
        access="research",
        action="validate-next-buy",
    )
    second_boundary = base.model_copy(update={field: changed})
    inputs = (
        _input(
            _evidence("E-1", "AWARD-1", kind=EvidenceKind.AWARD),
            boundary=base,
            corridor_id="corridor:same-label",
        ),
        _input(
            _evidence("E-2", "AWARD-2", kind=EvidenceKind.AWARD),
            boundary=second_boundary,
            corridor_id="corridor:same-label",
        ),
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 2
    assert sorted(len(candidate.members) for candidate in result.candidates) == [1, 1]


def test_similar_titles_without_shared_corridor_identity_never_cluster():
    boundary = _boundary(
        LifecycleKind.AWARD_HISTORY,
        deadline=None,
        access="research",
        action="review-award-history",
    )
    inputs = (
        _input(
            _evidence(
                "E-1", "AWARD-1", kind=EvidenceKind.AWARD,
                title="Enterprise network monitoring support",
            ),
            boundary=boundary,
            corridor_id="corridor:agency-a-network",
        ),
        _input(
            _evidence(
                "E-2", "AWARD-2", kind=EvidenceKind.AWARD,
                title="Enterprise network monitoring services",
            ),
            boundary=boundary,
            corridor_id="corridor:agency-b-network",
        ),
    )

    result = build_candidate_projection(inputs, _config())

    assert len(result.candidates) == 2
    assert {candidate.strategic_corridor.corridor_id
            for candidate in result.candidates} == {
        "corridor:agency-a-network",
        "corridor:agency-b-network",
    }


def test_every_cluster_member_remains_evidence_and_url_traceable():
    boundary = _boundary(
        LifecycleKind.RECOMPETE_RESEARCH,
        deadline=None,
        access="research",
        action="validate-next-buy",
    )
    evidence = tuple(
        _evidence(
            f"E-{number}", f"AWARD-{number}",
            kind=EvidenceKind.AWARD,
            source_url=f"https://example.gov/awards/AWARD-{number}",
        )
        for number in range(1, 5)
    )
    inputs = tuple(
        _input(
            row,
            boundary=boundary,
            corridor_id="corridor:traceable",
        )
        for row in evidence
    )

    candidate = build_candidate_projection(inputs, _config()).candidates[0]
    evidence_by_id = {row.evidence_id: row for row in evidence}

    assert set(candidate.member_evidence_ids) == set(evidence_by_id)
    assert {
        str(evidence_by_id[evidence_id].source_url)
        for evidence_id in candidate.member_evidence_ids
    } == {
        f"https://example.gov/awards/AWARD-{number}"
        for number in range(1, 5)
    }


@pytest.mark.parametrize("kind", (EvidenceKind.NEWS, EvidenceKind.WEB_DISCOVERY))
def test_news_or_web_discovery_cannot_anchor_a_candidate(kind: EvidenceKind):
    source = _evidence("E-DISCOVERY", "DISCOVERY-1", kind=kind)
    candidate_input = _input(
        source,
        boundary=_boundary(
            LifecycleKind.EARLY_SIGNAL,
            deadline=None,
            access="research",
            action="validate-signal",
        ),
        corridor_id="corridor:discovery-only",
    )

    with pytest.raises(ValueError, match="(?i)(anchor|candidate|discovery|news)"):
        build_candidate_projection((candidate_input,), _config())


@pytest.mark.parametrize(
    "kind",
    (
        EvidenceKind.OFFICIAL_EVENT,
        EvidenceKind.ORGANIZER_EVENT,
        EvidenceKind.VEHICLE,
        EvidenceKind.LEGISLATION,
        EvidenceKind.REGULATION,
    ),
)
def test_context_record_alone_cannot_anchor_an_opportunity_corridor(
    kind: EvidenceKind,
):
    source = _evidence("E-CONTEXT", "CONTEXT-1", kind=kind)
    candidate_input = _input(
        source,
        boundary=_boundary(
            LifecycleKind.EARLY_SIGNAL,
            deadline=None,
            access="event",
            action="attend-event",
        ),
        corridor_id="corridor:context-only",
    )

    with pytest.raises(
        ValueError,
        match="(?i)(anchor|candidate|event|vehicle|legislation|regulation)",
    ):
        build_candidate_projection((candidate_input,), _config())


@pytest.mark.parametrize(
    "notice_role",
    (
        NoticeRole.VEHICLE_ESTABLISHMENT,
        NoticeRole.VEHICLE_ON_RAMP,
        NoticeRole.VEHICLE_ADMINISTRATION,
    ),
)
def test_active_vehicle_notice_roles_cannot_become_candidate_opportunities(
    notice_role: NoticeRole,
):
    notice = _evidence(
        "E-VEHICLE-NOTICE",
        "VEHICLE-NOTICE-1",
        notice_role=notice_role,
    )

    with pytest.raises(ValueError, match="acquisition role"):
        build_candidate_projection((_input(notice),), _config())


def test_active_task_order_notice_remains_current_with_vehicle_support():
    notice = _evidence(
        "E-TASK-NOTICE",
        "TASK-NOTICE-1",
        notice_role=NoticeRole.TASK_ORDER_REQUIREMENT,
    )
    vehicle = _evidence(
        "E-PARENT-IDV",
        "IDV-1",
        kind=EvidenceKind.VEHICLE,
    )
    candidate_input = _input(
        notice,
        supporting_evidence_ids=(vehicle.evidence_id,),
    )

    result = build_candidate_projection(
        (candidate_input,),
        _config(additional_evidence=(vehicle,)),
    )

    assert result.candidates[0].kind == CandidateKind.CURRENT_NOTICE
    assert result.candidates[0].supporting_evidence_ids == (vehicle.evidence_id,)


def test_discovery_record_may_support_but_never_anchor_an_official_candidate():
    award = _evidence("E-AWARD", "AWARD-1", kind=EvidenceKind.AWARD)
    news = _evidence("E-NEWS", "NEWS-1", kind=EvidenceKind.NEWS)
    boundary = _boundary(
        LifecycleKind.AWARD_HISTORY,
        deadline=None,
        access="research",
        action="validate-future-buy",
    )
    seed = _input(
        award,
        boundary=boundary,
        corridor_id="corridor:official-anchor",
        supporting_evidence_ids=(news.evidence_id,),
    )

    result = build_candidate_projection(
        (seed,),
        _config(additional_evidence=(news,)),
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.member_evidence_ids == (award.evidence_id,)
    assert candidate.supporting_evidence_ids == (news.evidence_id,)
    assert {row.evidence_id for row in result.evidence} == {
        award.evidence_id,
        news.evidence_id,
    }


def test_nonofficial_award_record_cannot_anchor_a_research_corridor():
    unverified_award = _evidence(
        "E-AWARD", "AWARD-1", kind=EvidenceKind.AWARD,
        official=False, primary=False,
    )
    candidate_input = _input(
        unverified_award,
        boundary=_boundary(
            LifecycleKind.AWARD_HISTORY,
            deadline=None,
            access="research",
            action="validate-future-buy",
        ),
        corridor_id="corridor:unverified-award",
    )

    with pytest.raises(ValueError, match="(?i)(official|primary|anchor)"):
        build_candidate_projection((candidate_input,), _config())


@pytest.mark.parametrize(
    ("kind", "lifecycle"),
    (
        (EvidenceKind.AGENCY_FORECAST, LifecycleKind.ACQUISITION_PLANNING),
        (EvidenceKind.AWARD, LifecycleKind.AWARD_HISTORY),
    ),
)
def test_forecasts_and_awards_are_research_corridors_not_current_notices(
    kind: EvidenceKind,
    lifecycle: LifecycleKind,
):
    boundary = _boundary(
        lifecycle,
        deadline=None,
        access="research",
        action="validate-future-buy",
    )
    candidate_input = _input(
        _evidence("E-1", "RECORD-1", kind=kind),
        boundary=boundary,
        corridor_id="corridor:future-buy",
    )

    candidate = build_candidate_projection(
        (candidate_input,), _config()).candidates[0]

    assert candidate.kind == CandidateKind.RESEARCH_CORRIDOR
    assert candidate.cluster_level == ClusterLevel.STRATEGIC_CORRIDOR
    assert candidate.lifecycle == lifecycle


@pytest.mark.parametrize(
    ("date_kind", "date_status"),
    (
        (DateKind.RESPONSE_DEADLINE, DateStatus.CONFIRMED),
        (DateKind.FORECAST_SOLICITATION, DateStatus.ANTICIPATED),
    ),
)
def test_typed_procurement_date_requires_a_compatible_source_kind(
    date_kind: DateKind,
    date_status: DateStatus,
):
    award = _evidence("E-AWARD", "AWARD-1", kind=EvidenceKind.AWARD)
    incompatible_date = DateValue(
        date_id=f"D-{date_kind.value}",
        label="Unsupported typed date",
        kind=date_kind,
        status=date_status,
        precision=DatePrecision.DAY,
        source_text="August 15, 2026",
        sort_date=date(2026, 8, 15),
        start=date(2026, 8, 15),
        evidence_ids=(award.evidence_id,),
    )
    candidate_input = _input(
        award,
        boundary=_boundary(
            LifecycleKind.AWARD_HISTORY,
            deadline=None,
            access="research",
            action="validate-future-buy",
        ),
        corridor_id="corridor:wrong-date-source",
        dates=(incompatible_date,),
    )

    with pytest.raises(ValueError, match="(?i)(compatible|source|date)"):
        build_candidate_projection((candidate_input,), _config())


def test_candidate_engine_rejects_a_typed_date_absent_from_source_text():
    notice = _evidence(
        "E-DATE",
        "NOTICE-DATE",
        excerpt="Responses are due 2026-09-01.",
    )
    unsupported = DateValue(
        date_id="D-UNSUPPORTED",
        label="Response deadline",
        kind=DateKind.RESPONSE_DEADLINE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="2026-08-15",
        sort_date=date(2026, 8, 15),
        start=date(2026, 8, 15),
        evidence_ids=(notice.evidence_id,),
    )

    with pytest.raises(ValueError, match="semantic source role"):
        build_candidate_projection(
            (_input(notice, dates=(unsupported,)),),
            _config(),
        )


def test_candidate_build_result_is_an_exact_client_bound_projection():
    result = build_candidate_projection(
        (_input(_evidence("E-BOUND", "NOTICE-BOUND")),),
        _config(),
    )

    forged_visible = result.model_dump(mode="python")
    forged_visible["candidates"] = ({
        **result.candidates[0].model_dump(mode="python"),
        "title": "Fabricated visible title",
    },)
    with pytest.raises(ValueError, match="ranked inventory content"):
        CandidateBuildResult.model_validate(forged_visible)

    foreign_evidence = result.model_dump(mode="python")
    foreign_evidence["evidence"] = ({
        **result.evidence[0].model_dump(mode="python"),
        "client_id": "otherco",
    },)
    with pytest.raises(ValueError, match="crosses client"):
        CandidateBuildResult.model_validate(foreign_evidence)


def test_closed_notice_cannot_be_presented_as_a_current_notice():
    boundary = _boundary(
        LifecycleKind.AWARD_HISTORY,
        deadline=None,
        access="research",
        action="review-closed-notice",
    )
    candidate_input = _input(
        _evidence("E-1", "CLOSED-1", status=NoticeStatus.CLOSED),
        boundary=boundary,
        corridor_id="corridor:closed-notice-history",
    )

    candidate = build_candidate_projection(
        (candidate_input,), _config()).candidates[0]

    assert candidate.kind == CandidateKind.RESEARCH_CORRIDOR
    assert candidate.lifecycle == LifecycleKind.AWARD_HISTORY


def test_stale_notice_verification_cannot_create_a_current_candidate():
    stale = _evidence("E-STALE", "STALE-1").model_copy(update={
        "verified_at": NOW - timedelta(hours=25),
    })

    with pytest.raises(ValueError, match="(?i)(24|stale|verif|current)"):
        build_candidate_projection((_input(stale),), _config())


def test_award_end_is_a_research_clock_and_never_a_confirmed_recompete():
    excerpt = "The current award period ends September 30, 2027."
    award = _evidence(
        "E-AWARD",
        "AWARD-1",
        kind=EvidenceKind.AWARD,
        excerpt=excerpt,
        assertions=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.AWARD_PERIOD_END,
            quote="award period ends September 30, 2027",
        ),),
    )
    timing = DateValue(
        date_id="D-AWARD-END",
        label="Award end research clock",
        kind=DateKind.AWARD_END_RESEARCH_CLOCK,
        status=DateStatus.RESEARCH_CLOCK,
        precision=DatePrecision.DAY,
        source_text="September 30, 2027",
        sort_date=date(2027, 9, 30),
        start=date(2027, 9, 30),
        evidence_ids=(award.evidence_id,),
    )
    boundary = _boundary(
        LifecycleKind.RECOMPETE_RESEARCH,
        deadline=None,
        access="research",
        action="validate-next-buy",
    )
    candidate = build_candidate_projection(
        (_input(
            award,
            boundary=boundary,
            corridor_id="corridor:award-end",
            dates=(timing,),
        ),),
        _config(),
    ).candidates[0]

    assert candidate.lifecycle == LifecycleKind.RECOMPETE_RESEARCH
    assert candidate.dates == (timing,)
    assert all(item.kind != DateKind.CONFIRMED_RECOMPETE for item in candidate.dates)


def test_award_evidence_cannot_be_relabeled_as_a_confirmed_recompete():
    award = _evidence("E-AWARD", "AWARD-1", kind=EvidenceKind.AWARD)
    false_confirmation = DateValue(
        date_id="D-FALSE",
        label="Confirmed recompete",
        kind=DateKind.CONFIRMED_RECOMPETE,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text="September 30, 2027",
        sort_date=date(2027, 9, 30),
        start=date(2027, 9, 30),
        evidence_ids=(award.evidence_id,),
    )
    candidate_input = _input(
        award,
        boundary=_boundary(
            LifecycleKind.CONFIRMED_RECOMPETE,
            deadline=None,
            access="research",
            action="validate-recompete",
        ),
        corridor_id="corridor:false-recompete",
        dates=(false_confirmation,),
    )

    with pytest.raises(ValueError, match="(?i)(award|recompete|evidence)"):
        build_candidate_projection((candidate_input,), _config())


@pytest.mark.parametrize(
    "copy_field",
    (
        "title",
        "records_show",
        "may_suggest",
        "validate_next",
        "cluster_reason",
        "strategic_action",
        "distinctness_explanation",
        "inference_chain",
        "falsifier",
        "watch_trigger",
        "cautions",
    ),
)
def test_award_end_cannot_be_described_as_a_confirmed_future_recompete(
    copy_field: str,
):
    row = _award_end_seed_input()
    unsupported_claim = (
        "The award-period end confirms this procurement will recompete "
        "on September 30, 2027."
    )
    change = (
        (unsupported_claim,)
        if copy_field == "cautions"
        else unsupported_claim
    )
    misleading_seed = CandidateSeed.model_validate({
        **row.seed.model_dump(mode="python"),
        copy_field: change,
    })

    with pytest.raises(ValueError, match="(?i)(award|research|recompete|confirm)"):
        build_candidate_projection(
            (_SeedInput(evidence=row.evidence, seed=misleading_seed),),
            _config(),
        )


@pytest.mark.parametrize(
    "unsupported_claim",
    (
        "The procurement will recompete on September 30, 2027.",
        "The requirement will be recompeted on September 30, 2027.",
        "The recompete is scheduled for September 30, 2027.",
        "The recompete is expected in fiscal year 2028.",
        "The award expiry signals an upcoming recompete.",
        "The recompete date is September 30, 2027.",
    ),
)
def test_award_end_rejects_definitive_recompete_language_without_confirm_word(
    unsupported_claim: str,
):
    row = _award_end_seed_input()
    misleading_seed = CandidateSeed.model_validate({
        **row.seed.model_dump(mode="python"),
        "records_show": unsupported_claim,
    })

    with pytest.raises(ValueError, match="(?i)(official|recompete|evidence)"):
        build_candidate_projection(
            (_SeedInput(evidence=row.evidence, seed=misleading_seed),),
            _config(),
        )


@pytest.mark.parametrize(
    "cautious_statement",
    (
        "The award end is a research clock, not a confirmed recompete.",
        "No official source confirms a recompete.",
        "The record does not confirm that the requirement will be recompeted.",
    ),
)
def test_award_end_allows_cautious_recompete_research_language(
    cautious_statement: str,
):
    row = _award_end_seed_input()
    cautious_seed = CandidateSeed.model_validate({
        **row.seed.model_dump(mode="python"),
        "title": "Possible recompete research corridor",
        "records_show": cautious_statement,
        "may_suggest": (
            "The timing may suggest a possible successor acquisition to research."
        ),
        "validate_next": (
            "Validate whether the buyer intends any recompete or follow-on action."
        ),
    })

    result = build_candidate_projection(
        (_SeedInput(evidence=row.evidence, seed=cautious_seed),),
        _config(),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].lifecycle == LifecycleKind.RECOMPETE_RESEARCH


def test_low_fit_set_aside_naics_and_value_cautions_never_disqualify():
    cautions = (
        "Capability fit is weak and requires analyst validation.",
        "The notice lists a different NAICS code.",
        "The opportunity is set aside.",
        "Published value is below the preferred range.",
    )
    low_fit = _input(
        _evidence("E-LOW", "NOTICE-LOW"),
        score=0.0,
        cautions=cautions,
    )

    result = build_candidate_projection((low_fit,), _config())

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.cautions == cautions
    serialized = candidate.model_dump_json().casefold()
    assert "pursue" not in serialized
    assert "no-bid" not in serialized
    assert "disqualified" not in serialized
    assert candidate.analyst_decision is None


def test_engine_models_expose_ranking_not_automated_business_disposition():
    forbidden = {
        "pursue",
        "no_bid",
        "disqualified",
        "disqualification",
        "business_eligible",
        "win_probability",
        "pipeline_value",
    }

    assert forbidden.isdisjoint(CandidateSeed.model_fields)
    assert forbidden.isdisjoint(RankedCandidate.model_fields)
    assert forbidden.isdisjoint(CandidateBuildResult.model_fields)


@pytest.mark.parametrize(
    ("copy_field", "disposition_copy"),
    (
        ("title", "Pursue this opportunity"),
        ("records_show", "The analyst should mark this as a no-bid."),
        ("cautions", ("This opportunity is disqualified.",)),
    ),
)
def test_candidate_unit_rejects_business_disposition_language_in_visible_copy(
    copy_field: str,
    disposition_copy: str | tuple[str, ...],
):
    candidate = build_candidate_projection(
        (_input(_evidence("E-1", "NOTICE-1")),),
        _config(),
    ).candidates[0]
    payload = candidate.model_dump(mode="python")
    payload[copy_field] = disposition_copy

    with pytest.raises(ValueError, match="(?i)(forbidden|pursue|no.?bid|disqual)"):
        CandidateUnit.model_validate(payload)


def test_ranking_is_stable_under_input_permutation_and_ties():
    rows = tuple(
        _input(
            _evidence(f"E-{record_id}", record_id),
            agency="Agency A",
            score=0.75,
        )
        for record_id in ("N-3", "N-1", "N-2", "N-4")
    )
    expected = build_candidate_projection(rows, _config())
    expected_ids = tuple(candidate.candidate_id for candidate in expected.candidates)
    expected_records = tuple(_record_ids(candidate) for candidate in expected.candidates)

    for seed in range(8):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        actual = build_candidate_projection(tuple(shuffled), _config())
        assert tuple(candidate.candidate_id for candidate in actual.candidates) \
            == expected_ids
        assert tuple(_record_ids(candidate) for candidate in actual.candidates) \
            == expected_records


def test_diversity_aware_compression_keeps_a_second_agency_in_view():
    rows = (
        _input(_evidence("E-A1", "A-1"), agency="Agency A", score=1.0),
        _input(_evidence("E-A2", "A-2"), agency="Agency A", score=0.99),
        _input(_evidence("E-A3", "A-3"), agency="Agency A", score=0.98),
        _input(_evidence("E-B1", "B-1"), agency="Agency B", score=0.90),
    )

    result = build_candidate_projection(rows, _config(max_candidates=2))

    assert len(result.candidates) == 2
    assert [candidate.agency for candidate in result.candidates] \
        == ["Agency A", "Agency B"]
    deferred_ids = {
        candidate.candidate_id for candidate in result.deferred_candidates
    }
    assert deferred_ids.isdisjoint(
        {candidate.candidate_id for candidate in result.candidates})
    assert len(deferred_ids) == 2
    assert len(result.ranked_inventory) == 4
    assert [item.rank for item in result.ranked_inventory] == [1, 2, 3, 4]
    assert sum(item.selected_for_report for item in result.ranked_inventory) == 2


def test_compression_does_not_relabel_omitted_candidates_as_disqualified():
    rows = tuple(
        _input(
            _evidence(f"E-{number}", f"N-{number}"),
            agency=f"Agency {number}",
            score=1.0 - number / 10,
        )
        for number in range(1, 5)
    )

    result = build_candidate_projection(rows, _config(max_candidates=2))

    assert len(result.candidates) == 2
    assert len(result.deferred_candidates) == 2
    assert "disqual" not in result.model_dump_json().casefold()
    assert "no-bid" not in result.model_dump_json().casefold()


def test_build_rejects_one_money_id_shared_by_distinct_candidates():
    inputs = (
        _award_money_input(
            "E-AWARD-1", "AWARD-1", "corridor:money-1",
            "agency:office-a", "M-SHARED",
        ),
        _award_money_input(
            "E-AWARD-2", "AWARD-2", "corridor:money-2",
            "agency:office-b", "M-SHARED",
        ),
    )

    with pytest.raises(ValueError, match="(?i)(money ID|shared|unique)"):
        build_candidate_projection(inputs, _config())


def test_document_hydration_rejects_money_id_collision_across_build_results():
    """The document contract remains a backstop for separately built units."""

    first = _award_money_input(
        "E-AWARD-1", "AWARD-1", "corridor:money-1",
        "agency:office-a", "M-SHARED",
    )
    second = _award_money_input(
        "E-AWARD-2", "AWARD-2", "corridor:money-2",
        "agency:office-b", "M-SHARED",
    )
    first_result = build_candidate_projection((first,), _config())
    second_result = build_candidate_projection((second,), _config())
    candidates = (*first_result.candidates, *second_result.candidates)

    with pytest.raises(ValueError, match="(?i)money IDs must be unique"):
        CandidateReviewDocument(
            document_id="DOC-MONEY-COLLISION",
            baseline_document_sha256="e" * 64,
            client_name="TestCo",
            binding=_binding(),
            as_of=NOW,
            generated_at=NOW - timedelta(minutes=1),
            evidence=(*first_result.evidence, *second_result.evidence),
            candidates=candidates,
            editor_state=EditorState(
                client_id="testco",
                document_id="DOC-MONEY-COLLISION",
                baseline_document_sha256="e" * 64,
                candidate_order=tuple(
                    candidate.candidate_id for candidate in candidates),
            ),
        )


def test_sparse_and_empty_inputs_do_not_create_filler_candidates():
    empty = build_candidate_projection((), _config())
    assert empty.candidates == ()
    assert empty.deferred_candidates == ()
    assert empty.ranked_inventory == ()
    assert empty.evidence == ()

    one = build_candidate_projection(
        (_input(_evidence("E-1", "NOTICE-1")),),
        _config(),
    )
    assert len(one.candidates) == 1
    assert _record_ids(one.candidates[0]) == ("NOTICE-1",)


@pytest.mark.parametrize(
    "changes",
    (
        {"client_id": "otherco"},
        {"run_id": "run-other"},
        {"scope_hash": OTHER_HASH},
    ),
)
def test_cross_client_run_or_scope_evidence_is_rejected(changes: dict[str, str]):
    source = _evidence("E-1", "NOTICE-1", **changes)

    with pytest.raises(ValueError, match="(?i)(client|run|scope|binding)"):
        build_candidate_projection((_input(source),), _config())


@pytest.mark.parametrize("client_id", ("mark43", "imerit", "riverbed"))
def test_golden_candidate_inventory_and_order_can_be_reproduced(client_id: str):
    fixture = json.loads(
        (FIXTURE_ROOT / client_id / "expected_contract.json").read_text()
    )
    contract = fixture["candidate_contract"]
    rows: list[_SeedInput] = []
    total = len(contract["candidates_in_order"])
    for candidate_index, expected in enumerate(contract["candidates_in_order"]):
        score = 1.0 - candidate_index / max(total, 20)
        kind = expected["kind"]
        if kind == CandidateKind.CURRENT_NOTICE.value:
            lifecycle = LifecycleKind.LIVE_SOLICITATION
            boundary = _boundary(
                lifecycle,
                buyer=f"golden:{candidate_index}",
                program=f"golden-program:{candidate_index}",
            )
            corridor_id = None
            evidence_kind = EvidenceKind.NOTICE
        else:
            lifecycle = LifecycleKind.RECOMPETE_RESEARCH
            boundary = _boundary(
                lifecycle,
                buyer=f"golden:{candidate_index}",
                deadline=None,
                program=f"golden-program:{candidate_index}",
                access="research",
                action="validate-next-buy",
            )
            corridor_id = expected["stable_reference_key"]
            evidence_kind = EvidenceKind.AWARD
        for member_index, record_id in enumerate(expected["member_record_ids"]):
            rows.append(_input(
                _evidence(
                    f"E-{candidate_index}-{member_index}",
                    record_id,
                    client_id=client_id,
                    kind=evidence_kind,
                    title=expected["title"],
                ),
                boundary=boundary,
                agency="Golden Agency",
                title=expected["title"],
                corridor_id=corridor_id,
                score=score,
            ))

    result = build_candidate_projection(tuple(rows), _config(client_id))

    assert len(result.candidates) == contract["display_count"]
    assert sum(len(candidate.members) for candidate in result.candidates) \
        == contract["member_evidence_count"]
    assert {
        kind: sum(candidate.kind.value == kind for candidate in result.candidates)
        for kind in contract["candidate_kind_counts"]
    } == contract["candidate_kind_counts"]
    assert [candidate.title for candidate in result.candidates] == [
        row["title"] for row in contract["candidates_in_order"]
    ]
    assert [set(_record_ids(candidate)) for candidate in result.candidates] == [
        set(row["member_record_ids"])
        for row in contract["candidates_in_order"]
    ]


def test_diverse_selection_backfill_returns_ranked_order():
    # 2026-07-23 contract-surface repro (alias-remap handoff sec 7): the
    # diversity backfill APPENDED high-ranked same-key candidates below
    # lower-ranked diverse picks, so the visible projection violated the
    # ranked-inventory order contract and CandidateBuildResult failed closed
    # on valid input. Masked in production only by the author's 12-seed cap.
    inputs = (
        _input(_evidence("E-1", "NOTICE-1"), agency="Agency A", score=0.9,
               boundary=_boundary(program="program-a")),
        _input(_evidence("E-2", "NOTICE-2"), agency="Agency A", score=0.8,
               boundary=_boundary(program="program-b")),
        _input(_evidence("E-3", "NOTICE-3"), agency="Agency B", score=0.7,
               boundary=_boundary(program="program-c")),
        _input(_evidence("E-4", "NOTICE-4"), agency="Agency A", score=0.6,
               boundary=_boundary(program="program-d")),
    )
    result = build_candidate_projection(inputs, _config(max_candidates=3))
    assert [item.agency for item in result.candidates] == \
        ["Agency A", "Agency A", "Agency B"]
    visible_scores = [item.rank_score for item in result.ranked_inventory
                      if item.selected_for_report]
    assert visible_scores == sorted(visible_scores, reverse=True)
    deferred = result.deferred_candidates
    assert len(deferred) == 1 and deferred[0].agency == "Agency A"
