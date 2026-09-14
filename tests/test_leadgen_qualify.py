"""Real Press Lead Gen qualifier (thin slice).

Doctrine 2026-09-07: HOLD remains fail-closed when evidence is thin;
four-leg + receipts may promote to WATCH or LEAD_T2 (at most one
LEAD_T1); a solicitation-only row cannot green T1/T2; HTML stays
primary; skeptic eval must score the pack without TypeError.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from agents.assess.contracts import (
    CoverageStatus,
    EvidenceKind,
    EvidenceUse,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    PartnerDirection,
)
from agents.leadgen import (
    QUALIFIER_VERSION,
    LeadTier,
    draft_lead_rows,
    qualify_drafts,
    run_press,
)
from agents.leadgen.eval import score_pack
from agents.leadgen.eval.checks import CheckVerdict, family_pass
from agents.leadgen.press import render_html
from tests.test_leadgen_from_assess import (
    _evidence,
    _live_bid_now,
    _partner,
    _review_fields,
    _run,
    _sam_coverage,
    _t1_target_actions,
)

NOW_DEADLINE = date(2026, 10, 1)
ROOT = Path(__file__).resolve().parents[1]
PROMOTED_FIXTURE = (
    ROOT / "agents" / "leadgen" / "eval" / "fixtures" / "promoted_pack.json"
)


def _verdict(card, check_id, *, subject_id=None):
    rows = [
        item for item in card.checks
        if item.check_id == check_id
        and (subject_id is None or item.subject_id == subject_id)
    ]
    assert rows, f"missing check {check_id} subject={subject_id}"
    return rows[0]


def _decision_actions(*, notice_id="N1", record_id="L1"):
    actions = _t1_target_actions(notice_id=notice_id, record_id=record_id)
    actions["rows"][0]["why_now"] = (
        "Incumbent Example Reseller holds the current seat. "
        "The buying component documented a renewal decision "
        "approaching 2026-10-01."
    )
    return actions


def _timed_live(*, record_id="L1", notice_id="N1", timing_support=True):
    excerpt = (
        "The contractor shall provide packet capture. "
        "Example Reseller is the incumbent. The seat faces a renewal decision on 2026-10-01."
    )
    return LiveSolicitation(
        record_id=record_id, notice_id=notice_id,
        solicitation_number="70-TEST-26",
        title="Network visibility", agency="DHS", component="CISA",
        classification=LiveClassification.BID_NOW,
        response_deadline=NOW_DEADLINE,
        authoritative_evidence=[_evidence(
            notice_id=notice_id, excerpt=excerpt,
            supports=[EvidenceUse.REQUIREMENT, EvidenceUse.TIMING] if timing_support else [EvidenceUse.REQUIREMENT],
        )],
        requirement_excerpt=excerpt,
        **_review_fields(),
        attachments_checked=True, fit_trace=["E1: packet capture"],
        recommendation=LiveRecommendation.PURSUE,
        verified_at=_live_bid_now().verified_at,
    )


def _holder_partner(*, partner_id="P1", linked=("L1",)):
    """Named holder without a vehicle cite.

    A SEWP / schedule seat is authorization, not buying intent. T2
    promotion tests use this so a vehicle cite cannot buy the tier.
    """

    return _partner(partner_id=partner_id, linked=linked).model_copy(update={
        "vehicle_or_channel": None,
    })


def _prime_partner(*, partner_id="P-prime", linked=("L1",)):
    return _partner(partner_id=partner_id, linked=linked).model_copy(update={
        "direction": PartnerDirection.PRIME_TO_SUB,
        "vehicle_or_channel": None,
        "role_hypothesis": (
            "Prime holds the vehicle; the named vendor component role "
            "is packet capture on the CISA switching fabric."
        ),
    })


def _assert_not_t1t2(receipt):
    tiers = {row.lead_tier for row in receipt.leads}
    assert LeadTier.LEAD_T1 not in tiers
    assert LeadTier.LEAD_T2 not in tiers
    assert receipt.active_lead_t1 == ()
    assert receipt.active_lead_t2 == ()


def test_thin_evidence_stays_hold():
    run = _run(live=[_live_bid_now()], partners=())
    receipt = run_press(assess=run, target_actions={"rows": []})
    assert receipt.stub is False
    assert receipt.qualifier == QUALIFIER_VERSION
    assert receipt.leads
    assert {row.lead_tier for row in receipt.leads} == {LeadTier.HOLD}
    assert receipt.active_lead_t1 == ()
    assert receipt.active_lead_t2 == ()


def test_four_leg_bare_expiry_promotes_to_watch_not_t1t2():
    run = _run(partners=[_holder_partner()])
    receipt = run_press(assess=run, target_actions=_t1_target_actions())
    assert receipt.stub is False
    assert receipt.leads
    assert {row.lead_tier for row in receipt.leads} == {LeadTier.WATCH}
    _assert_not_t1t2(receipt)
    row = receipt.leads[0]
    assert "WATCH" in (row.next_action.blocked_by or "")
    assert "four-leg" in (row.next_action.blocked_by or "")


def test_four_leg_renewal_decision_promotes_to_t2():
    run = _run(live=[_timed_live(timing_support=False)], partners=[_holder_partner()])
    receipt = run_press(assess=run, target_actions=_decision_actions())
    assert receipt.stub is False
    assert {row.lead_tier for row in receipt.leads} == {LeadTier.LEAD_T2}
    assert receipt.active_lead_t1 == ()
    assert len(receipt.active_lead_t2) == 1
    row = receipt.leads[0]
    assert row.seller_path.holder == "Example Reseller"
    assert row.next_action.blocked_by is None
    assert "LEAD_T2" in row.next_action.object


def test_fully_evidenced_renewal_promotes_at_most_one_t1():
    first = _timed_live(record_id="L1", notice_id="N1")
    second = _timed_live(record_id="L2", notice_id="N2")
    run = _run(
        live=[first, second],
        partners=[
            _partner(partner_id="P1", linked=("L1",)),
            _partner(partner_id="P2", linked=("L2",)),
        ],
    )
    actions = {
        "rows": [
            *_decision_actions(notice_id="N1", record_id="L1")["rows"],
            *_decision_actions(notice_id="N2", record_id="L2")["rows"],
        ],
        "groups": [],
        "motions": [],
    }
    receipt = run_press(assess=run, target_actions=actions)
    tiers = [row.lead_tier for row in receipt.leads]
    assert tiers.count(LeadTier.LEAD_T1) == 1
    assert tiers.count(LeadTier.LEAD_T2) == 1
    assert LeadTier.HOLD not in tiers


def test_solicitation_only_cannot_green_t1_or_t2():
    run = _run(live=[_live_bid_now()], partners=())
    actions = _decision_actions()
    actions["rows"][0]["route_role"] = "unknown_route"
    receipt = run_press(assess=run, target_actions=actions)
    assert receipt.leads
    assert LeadTier.LEAD_T1 not in {row.lead_tier for row in receipt.leads}
    assert LeadTier.LEAD_T2 not in {row.lead_tier for row in receipt.leads}
    card = score_pack(receipt.model_dump(mode="json"))
    assert _verdict(card, "PACK.SOLICITATION_ONLY_T1T2").verdict is CheckVerdict.PASS


def test_prime_recompete_with_component_role_promotes_to_t2():
    excerpt = ("The contractor shall provide packet capture. "
               "This recompete has an award decision on 2026-10-01. "
               "Example Reseller is an eligible prime for this recompete. "
               "The prime shall select and supply packet capture software.")
    live = _timed_live().model_copy(update={
        "requirement_excerpt": excerpt,
        "authoritative_evidence": (_evidence(excerpt=excerpt, supports=[EvidenceUse.REQUIREMENT, EvidenceUse.TIMING]),),
    })
    run = _run(live=[live], partners=[_prime_partner()])
    actions = _t1_target_actions()
    actions["rows"][0]["row_class"] = "displacement"
    actions["rows"][0]["rule_id"] = "T2"
    actions["rows"][0]["why_this_account"] = (
        "Prime-led recompete; vendor component role is packet capture."
    )
    receipt = run_press(assess=run, target_actions=actions)
    assert {row.lead_tier for row in receipt.leads} == {LeadTier.LEAD_T2}


def test_html_path_still_produced(tmp_path):
    receipt = run_press(
        assess=_run(partners=[_holder_partner()]),
        target_actions=_decision_actions(),
        review_dir=tmp_path,
    )
    assert receipt.html_path
    html_path = Path(receipt.html_path)
    assert html_path.is_file()
    html = html_path.read_text(encoding="utf-8")
    assert 'class="report"' in html
    assert 'class="topbar"' in html
    assert ":root{--red:#ee0000;" in html
    assert "Real qualifier ran" in html
    assert receipt.leads[0].lead_id in html
    assert "\u2014" not in html
    assert json.loads(Path(receipt.json_path).read_text())["stub"] is False


def test_eval_scores_promoted_pack_without_typeerror_and_af_pass():
    run = _run(live=[_timed_live(timing_support=False)], partners=[_holder_partner()])
    receipt = run_press(assess=run, target_actions=_decision_actions())
    payload = receipt.model_dump(mode="json")
    card = score_pack(payload)
    lead = receipt.leads[0]
    assert lead.lead_tier is LeadTier.LEAD_T2
    lead_checks = [
        item for item in card.checks if item.subject_id == lead.lead_id
    ]
    for family in "ABCDEF":
        assert family_pass(lead_checks, family), family
    assert _verdict(
        card, "TIER.T1T2_REQUIRES_AF", subject_id=lead.lead_id,
    ).verdict is CheckVerdict.PASS
    assert _verdict(
        card, "TIER.SOLICITATION_ONLY", subject_id=lead.lead_id,
    ).verdict is CheckVerdict.PASS
    assert _verdict(card, "PACK.SOLICITATION_ONLY_T1T2").verdict is CheckVerdict.PASS
    assert _verdict(card, "PACK.NO_QUOTA_FILL").verdict is CheckVerdict.PASS
    assert card.invented_row_count == 0


def test_checked_in_promoted_fixture_scores_without_typeerror():
    assert PROMOTED_FIXTURE.is_file()
    card = score_pack(PROMOTED_FIXTURE)
    assert card.lead_ids
    assert any(
        item.lead_tier in {LeadTier.LEAD_T2.value, LeadTier.WATCH.value}
        for item in card.checks if item.scope == "lead"
    )
    assert _verdict(card, "PACK.NO_QUOTA_FILL").verdict is CheckVerdict.PASS
    t2 = [
        item.subject_id for item in card.checks
        if item.lead_tier == LeadTier.LEAD_T2.value
    ]
    if t2:
        sid = t2[0]
        lead_checks = [item for item in card.checks if item.subject_id == sid]
        for family in "ABCDEF":
            assert family_pass(lead_checks, family), family


def test_mapper_still_watch_or_hold_only():
    run = _run(live=[_timed_live(timing_support=False)], partners=[_holder_partner()])
    batch = draft_lead_rows(run, _decision_actions())
    assert {row.lead_tier for row in batch.leads} <= {
        LeadTier.WATCH, LeadTier.HOLD}
    qualified = qualify_drafts(batch, run, _decision_actions())
    assert {row.lead_tier for row in qualified.leads} == {LeadTier.LEAD_T2}


def test_no_quota_keys_on_receipt():
    receipt = run_press(
        assess=_run(live=[_timed_live(timing_support=False)], partners=[_holder_partner()]),
        target_actions=_decision_actions(),
    )
    dumped = receipt.model_dump(mode="json")
    for key in ("quota", "fill_to", "min_t1", "min_t2", "target_count"):
        assert key not in dumped
    html = render_html(receipt)
    assert "lead T2" in html.casefold() or "LEAD_T2" in html


def test_incumbent_in_requirement_span_plus_bare_expiry_is_not_t2():
    excerpt = "The incumbent contractor shall provide packet capture."
    live = _live_bid_now().model_copy(update={
        "requirement_excerpt": excerpt,
        "authoritative_evidence": [_evidence(excerpt=excerpt)],
    })
    receipt = run_press(
        assess=_run(live=[live], partners=[_holder_partner()]),
        target_actions=_t1_target_actions(),
    )
    _assert_not_t1t2(receipt)


def test_displacement_plus_packet_capture_is_not_prime_recompete():
    actions = _t1_target_actions()
    actions["rows"][0]["row_class"] = "displacement"
    actions["rows"][0]["rule_id"] = "T2"
    receipt = run_press(
        assess=_run(partners=[_holder_partner()]),
        target_actions=actions,
    )
    _assert_not_t1t2(receipt)


def test_routing_and_optics_are_not_component_role():
    excerpt = "The contractor shall provide routing and optics."
    live = _live_bid_now().model_copy(update={
        "requirement_excerpt": excerpt,
        "authoritative_evidence": [_evidence(excerpt=excerpt)],
    })
    actions = _t1_target_actions()
    actions["rows"][0]["row_class"] = "displacement"
    actions["rows"][0]["rule_id"] = "T2"
    receipt = run_press(
        assess=_run(live=[live], partners=[_holder_partner()]),
        target_actions=actions,
    )
    _assert_not_t1t2(receipt)


def test_incomplete_required_coverage_blocks_t1_t2():
    run = _run(
        partners=[_holder_partner()],
        coverage=[_sam_coverage(status=CoverageStatus.PARTIAL)],
    )
    receipt = run_press(assess=run, target_actions=_decision_actions())
    _assert_not_t1t2(receipt)
    assert receipt.leads
    blocked = (receipt.leads[0].next_action.blocked_by or "").casefold()
    assert "required assess coverage is incomplete" in blocked


def test_vehicle_cite_is_not_stripped_and_does_not_green_t2():
    receipt = run_press(
        assess=_run(partners=[_partner()]),
        target_actions=_decision_actions(),
    )
    assert receipt.leads
    row = receipt.leads[0]
    assert row.seller_path.vehicle == "SEWP V"
    _assert_not_t1t2(receipt)
    blocked = (row.next_action.blocked_by or "").casefold()
    assert "vehicle" in blocked
    assert "authorization is not buying intent" in blocked


def test_overlays_do_not_self_certify_auth_or_solicitation_only():
    receipt = run_press(
        assess=_run(live=[_timed_live(timing_support=False)], partners=[_holder_partner()]),
        target_actions=_decision_actions(),
    )
    assert {row.lead_tier for row in receipt.leads} == {LeadTier.LEAD_T2}
    assert receipt.eval_overlays
    for body in receipt.eval_overlays.values():
        assert "auth_only" not in body
        assert "solicitation_only" not in body
