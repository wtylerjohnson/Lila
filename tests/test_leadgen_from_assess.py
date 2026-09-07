"""Assess / target_actions -> draft LeadRow shim.

Doctrine 2026-09-06: OpportunityAssessment is parent; LeadRow is child;
targeting motion rule_id T1 is not LeadTier; drafts are WATCH/HOLD only.
"""

from __future__ import annotations

import ast
import json
from datetime import date, datetime, timezone
from pathlib import Path

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
    IntelligenceStatus,
    LifecycleStage,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    LiveSolicitationLedger,
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
from agents.leadgen import (
    AssessmentSubjectKind,
    CommercialMotionKind,
    LeadRow,
    LeadTier,
    TargetingRuleId,
    draft_lead_rows,
)
from agents.leadgen.from_assess import (
    CLOCK_HOLD,
    CLOCK_UNESTABLISHED,
    GATES_NOT_READY,
    PATHWAY_HOLD,
    ROUTE_HOLD,
    coerce_assess_run,
    coerce_target_actions,
)
from agents.leadgen.from_assess import (
    main as mapper_main,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _evidence(*, eid="E1", notice_id="N1",
              kind=EvidenceKind.NOTICE, tier=EvidenceTier.NOTICE,
              primary=True, supports=None, url=None, excerpt=None):
    return EvidenceRef(
        evidence_id=eid, tier=tier, kind=kind,
        source_name="official source",
        source_url=url or f"https://sam.gov/opp/{notice_id}/view",
        retrieved_at=NOW,
        excerpt=excerpt or "The contractor shall provide packet capture.",
        primary_source=primary,
        supports=supports or [EvidenceUse.REQUIREMENT],
    )


def _scope():
    return AssessScope(
        mode=ScopeMode.AGENCY,
        agencies=[ScopeAgency(name="Department of Homeland Security",
                              abbr="DHS")],
    )


def _sam_coverage(status=CoverageStatus.COMPLETE):
    return SourceCoverage(
        source="sam.gov", lane=SourceLane.LIVE, status=status,
        required=True, records_screened=1, total_available=1)


def _review_fields(evidence_id="E1"):
    return {
        "requirement_review_evidence_id": evidence_id,
        "requirement_reviewed_by": "operator",
        "requirement_reviewed_at": NOW,
        "attachment_inventory_count": 0,
        "attachment_inventory_hash": "empty-inventory-sha256",
        "attachment_inventory_valid": True,
    }


def _live_bid_now(*, record_id="L1", notice_id="N1"):
    return LiveSolicitation(
        record_id=record_id, notice_id=notice_id,
        solicitation_number="70-TEST-26",
        title="Network visibility", agency="DHS", component="CISA",
        classification=LiveClassification.BID_NOW,
        response_deadline=date(2026, 10, 1),
        authoritative_evidence=[_evidence(notice_id=notice_id)],
        requirement_excerpt="The contractor shall provide packet capture.",
        **_review_fields(),
        attachments_checked=True, fit_trace=["E1: packet capture"],
        recommendation=LiveRecommendation.PURSUE, verified_at=NOW,
    )


def _live_research(*, record_id="L-res", notice_id="N-res"):
    return LiveSolicitation(
        record_id=record_id, notice_id=notice_id,
        title="Market research notice", agency="DHS",
        classification=LiveClassification.MARKET_RESEARCH,
        authoritative_evidence=[_evidence(eid="E-res", notice_id=notice_id)],
        recommendation=LiveRecommendation.MONITOR, verified_at=NOW,
    )


def _partner(*, partner_id="P1", linked=("L1",)):
    return PartnerOpportunity(
        partner_id=partner_id, partner_name="Example Reseller",
        linked_assess_ids=list(linked),
        direction=PartnerDirection.CHANNEL_RESELLER,
        role_hypothesis="Reseller holds the vehicle seat.",
        client_needs_partner="Client reaches the buyer through the channel.",
        partner_needs_client="Client supplies the capability product.",
        evidence=[_evidence(
            eid="E-access", notice_id="N1",
            kind=EvidenceKind.VEHICLE, tier=EvidenceTier.MARKET,
            primary=True, supports=[EvidenceUse.ACCESS],
            url="https://www.usaspending.gov/award/SEWP-1",
            excerpt="Channel holds a SEWP seat used for this capability.",
        )],
        vehicle_or_channel="SEWP V",
        next_validation_step="Confirm the active seat.",
    )


def _ledgers(scope, *, live=(), horizon=(), partners=()):
    return {
        "live": LiveSolicitationLedger(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, records=list(live)),
        "horizon": OpportunityThesisLedger(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, items=list(horizon)),
        "partners": PartnerOpportunityLedger(
            run_id="R1", client_name="Testco", profile_version="P1",
            scope=scope, as_of=NOW, items=list(partners)),
    }


def _run(*, live=None, horizon=(), partners=(), coverage=None):
    scope = _scope()
    live = [_live_bid_now()] if live is None else list(live)
    return AssessRun(
        run_id="R1", client_name="Testco", profile_version="P1",
        scope=scope, as_of=NOW,
        coverage=coverage or [_sam_coverage()],
        **_ledgers(scope, live=live, horizon=horizon, partners=partners),
    )


def _t1_target_actions(*, notice_id="N1", record_id="L1"):
    """Minimal target_actions projection synthesized from the row template."""

    return {
        "version": "target_actions.v1.2026-08-06",
        "client": "Testco",
        "rows": [{
            "motion": "defend renewal",
            "motion_id": "renewal-buying-component-civilian",
            "decision_rule": "R1",
            "rule_id": "T1",
            "spec_id": "t1-01-dhs",
            "row_class": "renewal",
            "buying_agency": "DHS",
            "route_role": "unknown_route",
            "record_ids": [record_id, notice_id],
            "record_urls": [f"https://sam.gov/opp/{notice_id}/view"],
            "notice_id": notice_id,
            "solicitation_number": "70-TEST-26",
            "why_now": "The cited record's current period ends 2026-10-01.",
            "recommended_action": (
                "Ask who owns the renewal decision on this record."),
            "name": "Apollo Person",
            "contact_source_class": "commercial enrichment",
            "source_url": f"https://sam.gov/opp/{notice_id}/view",
        }],
        "groups": [],
        "motions": [{
            "motion_id": "renewal-buying-component-civilian",
            "row_class": "renewal",
            "specs": [{"spec_id": "t1-01-dhs", "rule_id": "T1"}],
        }],
    }


def test_parent_link_and_four_factor_shape():
    run = _run(partners=[_partner()])
    batch = draft_lead_rows(run, _t1_target_actions())
    live_parents = [
        parent for parent in batch.parents
        if parent.subject_kind is AssessmentSubjectKind.LIVE_SOLICITATION
    ]
    assert len(live_parents) == 1
    parent = live_parents[0]
    assert parent.assess_run_id == "R1"
    assert parent.subject_id == "L1"
    assert parent.notice_id == "N1"
    assert parent.requirement_span == (
        "The contractor shall provide packet capture.")
    assert len(batch.leads) == 1
    row = batch.leads[0]
    assert row.parent_assessment_id == parent.assessment_id
    assert row.assess_run_id == run.run_id
    assert row.lead_id in parent.lead_ids
    assert row.buying_motion.parent_subject_id == "L1"
    assert row.external_pathway.notice_id == "N1"
    assert row.seller_path.kind.value == PartnerDirection.CHANNEL_RESELLER.value
    assert row.next_action.blocked_by
    dumped = row.model_dump(mode="json")
    for factor in ("buying_motion", "external_pathway",
                   "seller_path", "next_action"):
        assert factor in dumped


def test_targeting_t1_string_does_not_become_lead_tier():
    run = _run(partners=[_partner()])
    batch = draft_lead_rows(run, _t1_target_actions())
    row = batch.leads[0]
    assert row.buying_motion.targeting_rule_id is TargetingRuleId.T1
    assert row.buying_motion.targeting_rule_id.value == "T1"
    assert row.buying_motion.kind is CommercialMotionKind.RENEWAL
    assert row.lead_tier is not LeadTier.LEAD_T1
    assert row.lead_tier.value != "T1"
    dumped = row.model_dump(mode="json")
    assert dumped["lead_tier"] in {"WATCH", "HOLD"}
    assert dumped["buying_motion"]["targeting_rule_id"] == "T1"
    with pytest.raises(ValidationError):
        LeadRow.model_validate({**dumped, "lead_tier": "T1"})


def test_drafts_are_watch_or_hold_only_even_for_bid_now():
    run = _run(partners=[_partner()])
    batch = draft_lead_rows(run, _t1_target_actions())
    assert batch.leads
    assert {row.lead_tier for row in batch.leads} <= {
        LeadTier.WATCH, LeadTier.HOLD}
    assert LeadTier.LEAD_T1 not in {row.lead_tier for row in batch.leads}
    assert LeadTier.LEAD_T2 not in {row.lead_tier for row in batch.leads}
    complete = batch.leads[0]
    assert complete.lead_tier is LeadTier.WATCH
    assert GATES_NOT_READY in (complete.next_action.blocked_by or "")


def test_missing_route_or_clock_holds_with_promotion_condition():
    run = _run(live=[_live_research()])
    actions = _t1_target_actions(notice_id="N-res", record_id="L-res")
    actions["rows"][0]["route_role"] = "unknown_route"
    actions["rows"][0]["why_now"] = (
        "No dated clock rides the cited record; timing is unestablished.")
    actions["rows"][0].pop("period_end", None)
    batch = draft_lead_rows(run, actions)
    assert batch.leads
    row = batch.leads[0]
    assert row.lead_tier is LeadTier.HOLD
    blocked = row.next_action.blocked_by or ""
    assert ROUTE_HOLD in blocked
    assert CLOCK_HOLD in blocked
    assert row.buying_motion.window is not None
    assert row.buying_motion.window.label == CLOCK_UNESTABLISHED


def test_missing_pathway_is_hold_not_silent_drop():
    news = _evidence(
        eid="E-news", notice_id="news",
        kind=EvidenceKind.NEWS, tier=EvidenceTier.DISCOVERY,
        primary=False, supports=[EvidenceUse.TIMING],
        url="https://www.example.com/news",
        excerpt="A trade article reports interest.",
    )
    thesis = OpportunityThesis(
        thesis_id="H-news", title="Unconfirmed modernization",
        predicted_event="Agency may compete a modernization requirement",
        agency="DHS", lifecycle_stage=LifecycleStage.EARLY_SIGNAL,
        projected_window=ProjectedWindow(label="unknown"),
        evidence=[news],
        inference_chain="Discovery lead only.",
        falsifier="Agency denies the requirement.",
        watch_trigger="Primary-source confirmation appears.",
        monitoring_cadence="weekly",
        evidence_strength=EvidenceStrength.EARLY,
        status=IntelligenceStatus.RESEARCH_NEEDED,
    )
    run = _run(live=[], horizon=[thesis])
    batch = draft_lead_rows(run, {"rows": []})
    parents = [
        parent for parent in batch.parents
        if parent.subject_id == "H-news"
    ]
    assert parents
    assert parents[0].lead_ids == ()
    hold_notes = [trace.notes for trace in batch.traces
                  if PATHWAY_HOLD in trace.notes]
    assert hold_notes, "missing pathway must be receipted, not dropped"


def test_award_only_targeting_row_does_not_mint_an_orphan_lead():
    run = _run()
    orphan = {
        "rows": [{
            "motion_id": "renewal-award-only",
            "rule_id": "T1",
            "row_class": "renewal",
            "record_ids": ["GS35F001"],
            "record_urls": ["https://www.usaspending.gov/award/GS35F001"],
            "buying_agency": "IRS",
        }],
    }
    batch = draft_lead_rows(run, orphan)
    live_leads = [
        row for row in batch.leads if row.buying_motion.kind
        is CommercialMotionKind.RENEWAL
    ]
    assert live_leads == []
    assert all(row.parent_assessment_id for row in batch.leads)
    assert all(row.assess_run_id == "R1" for row in batch.leads)


def test_assess_evidence_pointers_round_trip():
    run = _run(partners=[_partner()])
    batch = draft_lead_rows(run, _t1_target_actions())
    row = batch.leads[0]
    live = run.live.records[0]
    assert [item.evidence_id for item in row.external_pathway.evidence] == [
        item.evidence_id for item in live.authoritative_evidence
    ]
    assert row.external_pathway.evidence[0] == live.authoritative_evidence[0]
    parent = next(
        item for item in batch.parents if item.subject_id == "L1")
    assert parent.requirement_span == live.requirement_excerpt
    assert parent.notice_id == live.notice_id
    assert parent.solicitation_number == live.solicitation_number
    assert parent.live_classification == live.classification
    assert parent.live_recommendation == live.recommendation
    traces = [trace for trace in batch.traces if trace.lead_id == row.lead_id]
    assert traces
    assert "E1" in traces[0].evidence_ids
    assert "E1: packet capture" in traces[0].notes


def test_apollo_contact_is_not_copied_onto_the_pathway():
    run = _run(partners=[_partner()])
    batch = draft_lead_rows(run, _t1_target_actions())
    assert batch.leads[0].external_pathway.published_contacts == ()


def test_no_bid_live_record_is_parent_only():
    closed = LiveSolicitation(
        record_id="L-closed", notice_id="N-closed",
        title="Awarded package", agency="DHS",
        classification=LiveClassification.AWARDED_OR_CLOSED,
        authoritative_evidence=[_evidence(eid="E-c", notice_id="N-closed")],
        recommendation=LiveRecommendation.NO_BID, verified_at=NOW,
    )
    batch = draft_lead_rows(_run(live=[closed]), {"rows": []})
    parent = next(item for item in batch.parents if item.subject_id == "L-closed")
    assert parent.lead_ids == ()
    assert all(row.parent_assessment_id != parent.assessment_id
               for row in batch.leads)


def test_incomplete_coverage_holds_an_otherwise_watchable_row():
    run = _run(
        partners=[_partner()],
        coverage=[_sam_coverage(status=CoverageStatus.PARTIAL)],
    )
    batch = draft_lead_rows(run, _t1_target_actions())
    row = batch.leads[0]
    assert row.lead_tier is LeadTier.HOLD
    assert "sam.gov (partial)" in (row.next_action.blocked_by or "")


def test_assess_run_shaped_dict_and_empty_actions_are_accepted():
    run = _run(partners=[_partner()])
    payload = {"assess_run": run.model_dump(mode="json")}
    batch = draft_lead_rows(payload, None)
    assert batch.assess_run_id == "R1"
    assert batch.leads
    assert coerce_assess_run(run) == run
    assert coerce_target_actions(None)["rows"] == []
    with pytest.raises(TypeError):
        coerce_assess_run("R1")
    with pytest.raises(ValueError):
        coerce_target_actions({"client": "Testco"})


def test_cli_prints_a_draft_batch(tmp_path, capsys):
    run = _run(partners=[_partner()])
    assess_path = tmp_path / "assess.json"
    actions_path = tmp_path / "actions.json"
    assess_path.write_text(json.dumps(run.model_dump(mode="json")),
                           encoding="utf-8")
    actions_path.write_text(json.dumps(_t1_target_actions()),
                            encoding="utf-8")
    assert mapper_main(["--assess", str(assess_path),
                        "--target-actions", str(actions_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["assess_run_id"] == "R1"
    assert payload["leads"]
    assert payload["leads"][0]["lead_tier"] in {"WATCH", "HOLD"}


def test_mapper_does_not_import_targeting_or_apollo():
    source = (ROOT / "agents" / "leadgen" / "from_assess.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    forbidden = ("targeting_rules", "apollo_targets", "apollo")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        joined = " ".join(modules).casefold()
        for token in forbidden:
            assert token not in joined
