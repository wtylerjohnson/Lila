"""Skeptic LeadRow / parent eval harness.

Doctrine 2026-09-07: solicitation is not a lead; UNVERIFIED email
cannot PASS C3; auth-only cannot PASS D3; REJECT is receipted, never
silently dropped. Step 1 intake extract is untouched.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from agents.leadgen import (
    CommercialMotionKind,
    DecisionTrace,
    LeadReadiness,
    LeadRow,
    LeadTier,
    SellerPathKind,
)
from agents.leadgen.eval import (
    CheckVerdict,
    EmailStatus,
    load_score_input,
    render_csv,
    render_markdown,
    score_pack,
)
from agents.leadgen.eval.score import TINY_FIXTURE, main as score_main
from tests.test_leadgen_contracts import _lead, _motion, _next, _parent, _pathway, _seller

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agents" / "leadgen" / "eval"
RUBRIC_LEAD = ROOT / "docs" / "leadgen" / "LEADROW_RELEVANCE_RUBRIC_v0.md"
RUBRIC_PARENT = ROOT / "docs" / "leadgen" / "OPP_PARENT_QUALITY_RUBRIC_v0.md"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _trace(lead: LeadRow, *, notes: str = "assess · motion · pathway"):
    return DecisionTrace(
        trace_id=lead.decision_trace_id,
        assess_run_id=lead.assess_run_id,
        as_of=NOW,
        parent_assessment_id=lead.parent_assessment_id,
        lead_id=lead.lead_id,
        steps=("assess", "motion", "pathway", "path", "action", "tier"),
        evidence_ids=("E1",),
        notes=notes,
    )


def _pack(leads, *, parents=None, traces=None, overlays=None, extra=None):
    if parents is None:
        parents = [_parent(lead_ids=[lead.lead_id for lead in leads])]
    if traces is None:
        traces = [_trace(lead) for lead in leads]
    payload = {
        "schema_version": "leadgen.eval.v0",
        "parents": [parent.model_dump(mode="json") for parent in parents],
        "leads": [lead.model_dump(mode="json") for lead in leads],
        "traces": [trace.model_dump(mode="json") for trace in traces],
        "overlays": overlays or {},
    }
    if extra:
        payload.update(extra)
    return payload


def _verdict(card, check_id, *, subject_id=None):
    rows = [
        item for item in card.checks
        if item.check_id == check_id
        and (subject_id is None or item.subject_id == subject_id)
    ]
    assert rows, f"missing check {check_id} subject={subject_id}"
    return rows[0]


def test_solicitation_only_t1_fails():
    """A wrapped SAM notice cannot be lead T1."""

    lead = _lead(
        seller_path=_seller(
            holder=None, vehicle=None, dossier_cite=None,
            prime_posture_cite=None),
        lead_tier=LeadTier.LEAD_T1,
        readiness=LeadReadiness.ACTIONABLE,
    )
    card = score_pack(_pack([lead]))
    row = _verdict(card, "TIER.SOLICITATION_ONLY", subject_id=lead.lead_id)
    assert row.verdict is CheckVerdict.FAIL
    assert "solicitation-only" in row.receipt
    pack_row = _verdict(card, "PACK.SOLICITATION_ONLY_T1T2")
    assert pack_row.verdict is CheckVerdict.FAIL
    af = _verdict(card, "TIER.T1T2_REQUIRES_AF", subject_id=lead.lead_id)
    assert af.verdict is CheckVerdict.FAIL


def test_unverified_email_cannot_pass_c3():
    lead = _lead(
        seller_path=_seller(holder="Example Reseller"),
        lead_tier=LeadTier.WATCH,
        readiness=LeadReadiness.WATCHING,
        next_action=_next(blocked_by="WATCH: confirm published POC"),
    )
    card = score_pack(_pack([lead], overlays={
        lead.lead_id: {
            "email_status": "UNVERIFIED",
            "email": "jane@agency.example",
        },
    }))
    row = _verdict(card, "C3", subject_id=lead.lead_id)
    assert row.verdict is CheckVerdict.FAIL
    assert row.verdict is not CheckVerdict.PASS
    assert "UNVERIFIED" in row.receipt


def test_auth_only_cannot_pass_d3():
    lead = _lead(
        buying_motion=_motion(kind=CommercialMotionKind.ADJACENCY),
        seller_path=_seller(
            kind=SellerPathKind.VEHICLE_ACCESS,
            holder=None, vehicle="SEWP V"),
        lead_tier=LeadTier.WATCH,
        readiness=LeadReadiness.WATCHING,
        next_action=_next(blocked_by="WATCH: vehicle seat is not intent"),
    )
    card = score_pack(_pack([lead], overlays={
        lead.lead_id: {"auth_only": True},
    }))
    row = _verdict(card, "D3", subject_id=lead.lead_id)
    assert row.verdict is CheckVerdict.FAIL
    assert "auth-only" in row.receipt


def test_reject_receipt_is_not_silently_dropped():
    """REJECT stays on the scorecard even when the receipt check fails."""

    silent = _lead(
        lead_id="lr:oa:R1:live_solicitation:L1:M-rej:P1:S1",
        buying_motion=_motion(motion_id="M-rej"),
        lead_tier=LeadTier.REJECT,
        readiness=LeadReadiness.CLOSED,
        next_action=_next(blocked_by=None),
        decision_trace_id="dt:R1:silent",
    )
    receipted = _lead(
        lead_id="lr:oa:R1:live_solicitation:L1:M-rej2:P1:S1",
        buying_motion=_motion(motion_id="M-rej2"),
        lead_tier=LeadTier.REJECT,
        readiness=LeadReadiness.CLOSED,
        next_action=_next(blocked_by="NO_BID: capability mismatch"),
        decision_trace_id="dt:R1:receipted",
    )
    parent = _parent(lead_ids=[silent.lead_id, receipted.lead_id])
    traces = [
        _trace(silent, notes=""),
        _trace(receipted, notes="reject: capability mismatch"),
    ]
    card = score_pack(_pack(
        [silent, receipted], parents=[parent], traces=traces,
        overlays={
            receipted.lead_id: {"receipt": "NO_BID: capability mismatch"},
        },
    ))
    assert silent.lead_id in card.reject_lead_ids
    assert receipted.lead_id in card.reject_lead_ids
    assert silent.lead_id in card.lead_ids
    md = render_markdown(card)
    csv_text = render_csv(card)
    assert silent.lead_id in md
    assert receipted.lead_id in md
    assert silent.lead_id in csv_text
    assert receipted.lead_id in csv_text
    assert "REJECT rows retained: 2" in md
    assert _verdict(
        card, "TIER.RECEIPT_JUSTIFIED", subject_id=silent.lead_id,
    ).verdict is CheckVerdict.FAIL
    assert _verdict(
        card, "TIER.RECEIPT_JUSTIFIED", subject_id=receipted.lead_id,
    ).verdict is CheckVerdict.PASS
    assert _verdict(card, "PACK.REJECT_NOT_DROPPED").verdict is CheckVerdict.PASS


def test_cli_help_and_tiny_fixture(tmp_path, capsys):
    assert score_main(["--help"]) == 0
    help_text = capsys.readouterr().out
    assert "--pack" in help_text
    assert "LeadRow" in help_text
    assert "C3" in help_text
    md_path = tmp_path / "scorecard.md"
    csv_path = tmp_path / "scorecard.csv"
    assert score_main([
        "--pack", str(TINY_FIXTURE),
        "--md", str(md_path),
        "--csv", str(csv_path),
    ]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "leadgen.eval.v0"
    assert payload["lead_ids"]
    assert "[out]" in captured.err
    text = md_path.read_text(encoding="utf-8")
    assert "LeadRow relevance scorecard" in text
    assert "—" not in text
    assert "WATCH" in text
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "C3" in csv_text
    assert "P.DEMAND" in csv_text


def test_tiny_fixture_is_valid_eval_pack():
    pack = load_score_input(TINY_FIXTURE)
    assert pack.leads
    assert pack.parents
    card = score_pack(TINY_FIXTURE)
    assert card.invented_row_count == 0
    assert _verdict(card, "PACK.NO_QUOTA_FILL").verdict is CheckVerdict.PASS
    assert _verdict(card, "PACK.SOLICITATION_ONLY_T1T2").verdict is CheckVerdict.PASS


def test_quota_fill_fails_pack_check():
    lead = _lead(
        lead_tier=LeadTier.WATCH,
        readiness=LeadReadiness.WATCHING,
        next_action=_next(blocked_by="WATCH receipt"),
        seller_path=_seller(holder="Example Reseller"),
    )
    card = score_pack(_pack([lead], extra={"min_t1": 5, "quota": 10}))
    row = _verdict(card, "PACK.NO_QUOTA_FILL")
    assert row.verdict is CheckVerdict.FAIL
    assert card.invented_row_count == 0


def test_rubrics_are_checked_in_and_name_seed_ids():
    lead_text = RUBRIC_LEAD.read_text(encoding="utf-8")
    parent_text = RUBRIC_PARENT.read_text(encoding="utf-8")
    assert "—" not in lead_text
    assert "—" not in parent_text
    for check_id in ("A1", "B1", "C3", "D3", "E1", "F1",
                     "TIER.SOLICITATION_ONLY", "PACK.REJECT_NOT_DROPPED"):
        assert check_id in lead_text
    for check_id in ("P.DEMAND", "P.BUYER", "P.NEED", "P.FUNDING",
                     "P.TIMING", "P.FIT", "P.MECHANISM",
                     "P.CONTESTABILITY", "P.BLOCKERS", "P.EVIDENCE"):
        assert check_id in parent_text
    assert "UNVERIFIED" in lead_text
    assert "auth-only" in lead_text
    assert "Step 1" in lead_text


def test_eval_does_not_import_intake_or_release():
    for path in EVAL_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = (
            "agents.intake", "lila_release", "product_bundle",
            "apollo_targets", "targeting_rules",
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            joined = " ".join(modules)
            for token in forbidden:
                assert token not in joined, f"{path.name} imports {token}"


def test_verified_email_can_pass_c3():
    lead = _lead(
        seller_path=_seller(holder="Example Reseller"),
        lead_tier=LeadTier.WATCH,
        readiness=LeadReadiness.WATCHING,
        next_action=_next(blocked_by="WATCH receipt"),
    )
    card = score_pack(_pack([lead], overlays={
        lead.lead_id: {"email_status": EmailStatus.VERIFIED.value},
    }))
    assert _verdict(card, "C3", subject_id=lead.lead_id).verdict is CheckVerdict.PASS


def test_dated_intent_is_not_auth_only():
    lead = _lead(
        buying_motion=_motion(kind=CommercialMotionKind.LIVE_BID),
        seller_path=_seller(
            kind=SellerPathKind.CHANNEL_RESELLER, holder="Example Reseller"),
        lead_tier=LeadTier.WATCH,
        readiness=LeadReadiness.WATCHING,
        next_action=_next(blocked_by="WATCH receipt"),
    )
    card = score_pack(_pack([lead]))
    assert _verdict(card, "D3", subject_id=lead.lead_id).verdict is CheckVerdict.PASS
