"""Assess product contracts: lane separation, evidence, scope, and gates."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from agents.assess.contracts import (
    AssessRun,
    AssessScope,
    CoverageStatus,
    EvidenceKind,
    EvidenceRef,
    EvidenceStrength,
    EvidenceTier,
    EvidenceUse,
    GateStatus,
    IntelligenceStatus,
    LifecycleStage,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    LiveSolicitationLedger,
    NoticeAttachment,
    OpportunityThesis,
    OpportunityThesisLedger,
    PartnerDirection,
    PartnerOpportunity,
    PartnerOpportunityLedger,
    ProjectedWindow,
    ScopeAgency,
    ScopeMode,
    SourceCoverage,
    SourceLane,
)


NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _evidence(*, eid="E1", tier=EvidenceTier.NOTICE,
              kind=EvidenceKind.NOTICE, primary=True,
              supports=None, excerpt="The contractor shall provide packet capture.",
              notice_id="N1"):
    return EvidenceRef(
        evidence_id=eid, tier=tier, kind=kind, source_name="official source",
        source_url=f"https://sam.gov/opp/{notice_id}/view", retrieved_at=NOW,
        observed_date=date(2026, 7, 10), excerpt=excerpt,
        primary_source=primary,
        supports=([EvidenceUse.REQUIREMENT] if supports is None else supports),
    )


def _scope():
    return AssessScope(mode=ScopeMode.AGENCY,
                       agencies=[ScopeAgency(name="Department of Homeland Security",
                                             abbr="DHS")])


def _live_ledger(run_id="R1", scope=None):
    return LiveSolicitationLedger(
        run_id=run_id, client_name="Testco", profile_version="P1",
        scope=scope or _scope(), as_of=NOW,
    )


def _horizon_ledger(run_id="R1", scope=None):
    return OpportunityThesisLedger(
        run_id=run_id, client_name="Testco", profile_version="P1",
        scope=scope or _scope(), as_of=NOW,
    )


def _partner_ledger(run_id="R1", scope=None):
    return PartnerOpportunityLedger(
        run_id=run_id, client_name="Testco", profile_version="P1",
        scope=scope or _scope(), as_of=NOW,
    )


def _sam_coverage(status=CoverageStatus.COMPLETE):
    return SourceCoverage(
        source="sam.gov", lane=SourceLane.LIVE, status=status,
        required=True, records_screened=200, total_available=200)


def _review_fields(evidence_id="E1"):
    return {
        "requirement_review_evidence_id": evidence_id,
        "requirement_reviewed_by": "operator",
        "requirement_reviewed_at": NOW,
        "attachment_inventory_count": 0,
        "attachment_inventory_hash": "empty-inventory-sha256",
        "attachment_inventory_valid": True,
    }


def test_scope_is_operator_boundary_without_mixed_semantics():
    assert AssessScope().mode == ScopeMode.ALL
    with pytest.raises(ValidationError, match="cannot also name agencies"):
        AssessScope(mode=ScopeMode.ALL,
                    agencies=[ScopeAgency(name="DHS")])
    with pytest.raises(ValidationError, match="requires at least one agency"):
        AssessScope(mode=ScopeMode.AGENCY)


def test_live_bid_now_requires_notice_evidence_requirement_and_fit():
    rec = LiveSolicitation(
        record_id="L1", notice_id="N1", title="Network visibility",
        agency="DHS", classification=LiveClassification.BID_NOW,
        response_deadline=date(2026, 8, 1),
        authoritative_evidence=[_evidence()],
        requirement_excerpt="The contractor shall provide packet capture.",
        **_review_fields(),
        attachments_checked=True, fit_trace=["E1: packet capture"],
        recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
    )
    assert rec.classification == LiveClassification.BID_NOW

    forecast = _evidence(tier=EvidenceTier.PROGRAM,
                         kind=EvidenceKind.AGENCY_FORECAST)
    with pytest.raises(ValidationError, match="NOTICE-tier"):
        LiveSolicitation(
            record_id="L2", notice_id="F1", title="Future requirement",
            agency="DHS", classification=LiveClassification.BID_NOW,
            authoritative_evidence=[forecast],
            requirement_excerpt="Forecast text", fit_trace=["network"],
            **_review_fields(),
            verified_at=NOW,
        )
    with pytest.raises(ValidationError, match="completed attachment review"):
        LiveSolicitation(
            record_id="L3", notice_id="N3", title="Network visibility",
            agency="DHS", classification=LiveClassification.BID_NOW,
            response_deadline=date(2026, 8, 1),
            authoritative_evidence=[_evidence(notice_id="N3")],
            requirement_excerpt="The contractor shall provide packet capture.",
            **_review_fields(),
            fit_trace=["E1: packet capture"], attachment_gap="PWS unread",
            recommendation=LiveRecommendation.PURSUE,
            verified_at=NOW,
        )


def test_bid_now_requires_human_requirement_and_attachment_inventory_reviews():
    base = {
        "record_id": "L1",
        "notice_id": "N1",
        "title": "Network visibility",
        "agency": "DHS",
        "classification": LiveClassification.BID_NOW,
        "response_deadline": date(2026, 8, 1),
        "authoritative_evidence": [_evidence()],
        "requirement_excerpt": (
            "The contractor shall provide packet capture."),
        "attachments_checked": True,
        "fit_trace": ["E1: packet capture"],
        "recommendation": LiveRecommendation.PURSUE,
        "verified_at": NOW,
    }
    with pytest.raises(ValidationError,
                       match="explicit human requirement review"):
        LiveSolicitation(
            **base, attachment_inventory_count=0,
            attachment_inventory_hash="empty-inventory-sha256",
            attachment_inventory_valid=True)

    attachment = NoticeAttachment(
        name="PWS.pdf", media_type="application/pdf", resource_id="R1")
    attachment_review_fields = {
        **_review_fields(),
        "attachment_inventory_count": 1,
        "attachment_inventory_hash": "one-attachment-sha256",
    }
    with pytest.raises(ValidationError,
                       match="attachments require human inventory review"):
        LiveSolicitation(
            **base, **attachment_review_fields, attachments=[attachment])

    reviewed = LiveSolicitation(
        **base, **attachment_review_fields, attachments=[attachment],
        attachment_reviewed_by="operator", attachment_reviewed_at=NOW)
    assert reviewed.classification == LiveClassification.BID_NOW
    assert reviewed.attachments_checked is True
    assert reviewed.attachment_reviewed_by == "operator"


def test_live_classification_and_recommendation_cannot_contradict():
    with pytest.raises(ValidationError, match="unscreened records cannot"):
        LiveSolicitation(
            record_id="L1", notice_id="N1", title="Unknown package",
            agency="DHS", classification=LiveClassification.UNSCREENED,
            authoritative_evidence=[_evidence()],
            attachment_gap="full package not reviewed",
            recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
        )
    with pytest.raises(ValidationError, match="future actionable deadline"):
        LiveSolicitation(
            record_id="L2", notice_id="N2", title="Expired requirement",
            agency="DHS", classification=LiveClassification.BID_NOW,
            response_deadline=NOW.date(),
            authoritative_evidence=[_evidence(
                notice_id="N2", excerpt="Packet capture is required.")],
            requirement_excerpt="Packet capture is required.",
            **_review_fields(),
            attachments_checked=True, fit_trace=["E1: packet capture"],
            recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
        )
    non_sam = _evidence().model_copy(update={
        "source_url": "https://example.gov/not-a-sam-record"})
    with pytest.raises(ValidationError, match="must resolve to SAM.gov"):
        LiveSolicitation(
            record_id="L3", notice_id="N3", title="Untrusted link",
            agency="DHS", classification=LiveClassification.UNSCREENED,
            authoritative_evidence=[non_sam], attachment_gap="source mismatch",
            verified_at=NOW,
        )


def test_bid_now_contract_rejects_wrong_notice_or_unanchored_claims():
    wrong_notice = _evidence().model_copy(update={
        "source_url": "https://sam.gov/opp/OTHER/view"})
    with pytest.raises(ValidationError, match="notice id must match"):
        LiveSolicitation(
            record_id="L1", notice_id="N1", title="Packet capture",
            agency="DHS", classification=LiveClassification.BID_NOW,
            response_deadline=date(2026, 8, 1),
            authoritative_evidence=[wrong_notice],
            requirement_excerpt="The contractor shall provide packet capture.",
            **_review_fields(),
            attachments_checked=True, fit_trace=["E1: packet capture"],
            recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
        )

    no_requirement = _evidence().model_copy(update={"supports": ()})
    with pytest.raises(ValidationError, match="requirement-support evidence"):
        LiveSolicitation(
            record_id="L2", notice_id="N1", title="Packet capture",
            agency="DHS", classification=LiveClassification.BID_NOW,
            response_deadline=date(2026, 8, 1),
            authoritative_evidence=[no_requirement],
            requirement_excerpt="Invented requirement text.",
            **_review_fields(),
            attachments_checked=True, fit_trace=["E1: packet capture"],
            recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
        )

    with pytest.raises(ValidationError, match="exact evidence span"):
        LiveSolicitation(
            record_id="L3", notice_id="N1", title="Packet capture",
            agency="DHS", classification=LiveClassification.BID_NOW,
            response_deadline=date(2026, 8, 1),
            authoritative_evidence=[_evidence()],
            requirement_excerpt="Invented requirement text.",
            **_review_fields(),
            attachments_checked=True, fit_trace=["E1: packet capture"],
            recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
        )


def test_horizon_allows_zero_items_and_news_cannot_claim_strength():
    assert _horizon_ledger().items == ()
    news = _evidence(tier=EvidenceTier.DISCOVERY, kind=EvidenceKind.NEWS,
                     primary=False, supports=[EvidenceUse.REQUIREMENT])
    with pytest.raises(ValidationError, match="authoritative primary evidence"):
        OpportunityThesis(
            thesis_id="H1", title="Possible modernization",
            predicted_event="Agency may compete a modernization requirement",
            agency="DHS", lifecycle_stage=LifecycleStage.EARLY_SIGNAL,
            projected_window=ProjectedWindow(label="6 to 18 months"),
            evidence=[news], inference_chain="A trade article reports interest.",
            falsifier="No agency confirmation appears within 90 days.",
            watch_trigger="Agency forecast or market-research notice posts.",
            monitoring_cadence="weekly",
            evidence_strength=EvidenceStrength.MODERATE,
        )
    early = OpportunityThesis(
        thesis_id="H2", title="Unconfirmed modernization lead",
        predicted_event="Agency may compete a modernization requirement",
        agency="DHS", lifecycle_stage=LifecycleStage.EARLY_SIGNAL,
        projected_window=ProjectedWindow(label="unknown"), evidence=[news],
        inference_chain="Discovery lead only; primary confirmation is absent.",
        falsifier="Agency denies or omits the requirement.",
        watch_trigger="Primary-source confirmation appears.",
        monitoring_cadence="weekly", evidence_strength=EvidenceStrength.EARLY,
        status=IntelligenceStatus.RESEARCH_NEEDED,
    )
    assert early.status == IntelligenceStatus.RESEARCH_NEEDED


def test_partner_requires_capability_or_access_specific_evidence():
    generic = _evidence(tier=EvidenceTier.MARKET, kind=EvidenceKind.AWARD,
                        supports=[EvidenceUse.BUYER])
    with pytest.raises(ValidationError, match="capability-specific or access"):
        PartnerOpportunity(
            partner_id="P1", partner_name="Prime LLC",
            linked_assess_ids=["H1"], direction=PartnerDirection.PRIME_TO_SUB,
            role_hypothesis="Client supplies the product layer.",
            client_needs_partner="Prime holds the vehicle.",
            partner_needs_client="Client has the required product.",
            evidence=[generic], next_validation_step="Confirm workshare.",
        )
    access = _evidence(eid="E2", tier=EvidenceTier.MARKET,
                       kind=EvidenceKind.VEHICLE,
                       supports=[EvidenceUse.ACCESS])
    play = PartnerOpportunity(
        partner_id="P2", partner_name="Prime LLC",
        linked_assess_ids=["H1"], direction=PartnerDirection.VEHICLE_ACCESS,
        role_hypothesis="Prime provides the task-order path.",
        client_needs_partner="Prime is a verified vehicle holder.",
        partner_needs_client="Client supplies the capability-specific product.",
        evidence=[access], next_validation_step="Verify the active contract seat.",
    )
    assert play.evidence[0].kind == EvidenceKind.VEHICLE


def test_assess_run_never_auto_releases_and_required_failures_are_visible():
    scope = _scope()
    coverage = [_sam_coverage(),
        SourceCoverage(source="agency_forecasts", lane=SourceLane.HORIZON,
                       status=CoverageStatus.FAILED, required=True,
                       note="collector unavailable")]
    pending = AssessRun(
        run_id="R1", client_name="Testco", profile_version="P1", scope=scope,
        as_of=NOW, coverage=coverage, live=_live_ledger(scope=scope),
        horizon=_horizon_ledger(scope=scope),
        partners=_partner_ledger(scope=scope),
    )
    assert pending.approval_status == GateStatus.PENDING
    assert not pending.can_release()
    assert [c.source for c in pending.blocking_sources()] == ["agency_forecasts"]

    # model_copy(update=...) skips validation by Pydantic design; can_release
    # still fails closed when approval metadata is incomplete.
    forged = pending.model_copy(update={
        "approval_status": GateStatus.APPROVED,
    })
    assert not forged.can_release()

    approved = pending.model_copy(update={
        "approval_status": GateStatus.APPROVED,
        "approved_by": "operator",
        "approved_at": NOW,
        "partial_release_approved": True,
    })
    # model_copy is deliberately not validation; round-trip validates the
    # persisted artifact exactly as production loading will.
    approved = AssessRun.model_validate(approved.model_dump())
    assert approved.can_release()


def test_contract_graph_is_deeply_immutable_and_json_ergonomic():
    """Validation is a durable boundary, not a one-time construction check."""
    scope = _scope()
    live = _live_ledger(scope=scope)
    run = AssessRun(
        run_id="R1", client_name="Testco", profile_version="P1", scope=scope,
        as_of=NOW, coverage=[_sam_coverage()],
        live=live, horizon=_horizon_ledger(scope=scope),
        partners=_partner_ledger(scope=scope),
    )

    with pytest.raises(ValidationError, match="frozen"):
        run.approval_status = GateStatus.APPROVED
    with pytest.raises(AttributeError):
        scope.agencies.append(ScopeAgency(name="Department of Defense"))
    with pytest.raises(ValidationError, match="frozen"):
        scope.agencies[0].name = "Changed after approval"

    rec = LiveSolicitation(
        record_id="L1", notice_id="N1", title="Network visibility",
        agency="DHS", classification=LiveClassification.BID_NOW,
        response_deadline=date(2026, 8, 1),
        authoritative_evidence=[_evidence()],
        requirement_excerpt="The contractor shall provide packet capture.",
        **_review_fields(),
        attachments_checked=True, fit_trace=["E1: packet capture"],
        recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
    )
    with pytest.raises(AttributeError):
        rec.authoritative_evidence.append(_evidence(
            tier=EvidenceTier.PROGRAM, kind=EvidenceKind.AGENCY_FORECAST))

    # Callers may still construct with lists, and persistence remains ordinary
    # JSON arrays rather than tuple-specific encodings.
    dumped = run.model_dump(mode="json")
    assert isinstance(dumped["scope"]["agencies"], list)
    assert isinstance(dumped["live"]["records"], list)


def test_ledgers_cannot_cross_profile_or_scope_boundaries():
    scope = _scope()
    other = AssessScope(mode=ScopeMode.AGENCY,
                        agencies=[ScopeAgency(name="Department of Defense",
                                              abbr="DOD")])
    with pytest.raises(ValidationError, match="must share run"):
        AssessRun(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, coverage=[_sam_coverage()],
            live=_live_ledger(scope=scope),
            horizon=_horizon_ledger(scope=other),
            partners=_partner_ledger(scope=scope),
        )

    wrong_time = _horizon_ledger(scope=scope).model_copy(update={
        "as_of": NOW.replace(hour=13)})
    with pytest.raises(ValidationError, match="scope, and as-of"):
        AssessRun(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, coverage=[_sam_coverage()],
            live=_live_ledger(scope=scope), horizon=wrong_time,
            partners=_partner_ledger(scope=scope),
        )

    with pytest.raises(ValidationError, match="mandatory SAM.gov"):
        AssessRun(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, live=_live_ledger(scope=scope),
            horizon=_horizon_ledger(scope=scope),
            partners=_partner_ledger(scope=scope),
        )


def test_run_rejects_duplicate_ids_or_orphan_partner_links():
    scope = _scope()
    rec = LiveSolicitation(
        record_id="L1", notice_id="N1", title="Market research",
        agency="DHS", classification=LiveClassification.MARKET_RESEARCH,
        authoritative_evidence=[_evidence()],
        recommendation=LiveRecommendation.MONITOR, verified_at=NOW,
    )
    duplicate_live = LiveSolicitationLedger(
        run_id="R1", client_name="Testco", profile_version="P1",
        scope=scope, as_of=NOW, records=[rec, rec],
    )
    with pytest.raises(ValidationError, match="live ids must be unique"):
        AssessRun(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, coverage=[_sam_coverage()],
            live=duplicate_live,
            horizon=_horizon_ledger(scope=scope),
            partners=_partner_ledger(scope=scope),
        )

    partner = PartnerOpportunity(
        partner_id="P1", partner_name="Prime LLC",
        linked_assess_ids=["MISSING"],
        direction=PartnerDirection.SUB_TO_PRIME,
        role_hypothesis="Prime leads and client supplies the product layer.",
        client_needs_partner="Prime has evidenced buyer access.",
        partner_needs_client="Client supplies a capability-specific product.",
        evidence=[_evidence(
            tier=EvidenceTier.MARKET, kind=EvidenceKind.SUBAWARD,
            supports=[EvidenceUse.CAPABILITY])],
        next_validation_step="Validate workshare with the prime.",
    )
    orphan_partners = PartnerOpportunityLedger(
        run_id="R1", client_name="Testco", profile_version="P1",
        scope=scope, as_of=NOW, items=[partner],
    )
    with pytest.raises(ValidationError, match="must link to this Assess run"):
        AssessRun(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, coverage=[_sam_coverage()],
            live=_live_ledger(scope=scope),
            horizon=_horizon_ledger(scope=scope), partners=orphan_partners,
        )
