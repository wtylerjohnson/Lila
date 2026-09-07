"""Skeptic LeadRow / parent eval harness.

Doctrine 2026-09-07: solicitation is not a lead; UNVERIFIED email
cannot PASS C3; auth-only cannot PASS D3; REJECT is receipted, never
silently dropped. Step 1 intake extract is untouched.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.leadgen import (
    CommercialMotionKind,
    Contactability,
    DecisionTrace,
    LeadReadiness,
    LeadRow,
    LeadTier,
    PathwayKind,
    PublishedContact,
    SellerPathKind,
)
from agents.leadgen.eval import (
    CheckVerdict,
    EmailStatus,
    EvalOverlay,
    ScoreLoadError,
    is_solicitation_only,
    load_score_input,
    render_csv,
    render_markdown,
    score_pack,
)
from agents.leadgen.eval.score import TINY_FIXTURE
from tests.test_leadgen_contracts import (
    _evidence,
    _lead,
    _motion,
    _next,
    _parent,
    _pathway,
    _seller,
)

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


def test_cli_help_and_tiny_fixture(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "agents.leadgen.eval.score", "--help"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    help_text = proc.stdout
    assert "--pack" in help_text
    assert "LeadRow" in help_text
    assert "C3" in help_text
    md_path = tmp_path / "scorecard.md"
    csv_path = tmp_path / "scorecard.csv"
    scored = subprocess.run(
        [
            sys.executable, "-m", "agents.leadgen.eval.score",
            "--pack", str(TINY_FIXTURE),
            "--md", str(md_path),
            "--csv", str(csv_path),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert scored.returncode == 0
    payload = json.loads(scored.stdout)
    assert payload["schema_version"] == "leadgen.eval.v0"
    assert payload["lead_ids"]
    assert "[out]" in scored.stderr
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
        lead.lead_id: {
            "email_status": EmailStatus.VERIFIED.value,
            "email": "jane@agency.example",
            "receipt": "Published agency directory checked 2026-09-06",
        },
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


CITE_FIELDS = (
    "product_fit_cites", "demand_cites", "need_cites", "funding_cites",
    "contestability_cites", "blocker_cites",
)


@pytest.mark.parametrize("field", CITE_FIELDS)
@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_blank_cites_are_rejected_at_eval_boundary(field, blank):
    lead = _lead()
    with pytest.raises(ValidationError):
        EvalOverlay(subject_id=lead.lead_id, **{field: [blank]})
    with pytest.raises(ScoreLoadError, match="overlay is not usable"):
        score_pack(_pack([lead], overlays={lead.lead_id: {field: [blank]}}))


@pytest.mark.parametrize("cite", ["missing-evidence", "E1"])
def test_unresolved_or_blank_evidence_cites_fail_checks(cite):
    # Valid JSON/model inputs, with no alternative span/classification credit.
    lead = _lead(
        external_pathway=_pathway(evidence=[_evidence().model_copy(update={
            "excerpt": " ", "source_name": " ",
        })]),
        seller_path=_seller(holder=None),
        lead_tier=LeadTier.LEAD_T1, readiness=LeadReadiness.ACTIONABLE,
    )
    parent = _parent(lead_ids=[lead.lead_id], requirement_span=None,
                     lifecycle=None, live_classification=None)
    cites = {field: [cite] for field in CITE_FIELDS}
    card = score_pack(_pack([lead], parents=[parent], overlays={
        lead.lead_id: cites, parent.assessment_id: cites,
    }))
    for check in ("B1", "B2", "C1", "P.NEED", "P.FIT", "P.FUNDING",
                  "P.CONTESTABILITY", "P.DEMAND", "TIER.SOLICITATION_ONLY"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL
    held = _lead(lead_tier=LeadTier.REJECT, readiness=LeadReadiness.CLOSED)
    held_card = score_pack(_pack([held], overlays={
        held.parent_assessment_id: {"blocker_cites": ["missing-evidence"]},
    }))
    assert _verdict(held_card, "P.BLOCKERS").verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("field", ["excerpt", "source_name", "evidence_id"])
def test_whitespace_evidence_fails_c1(field):
    lead = _lead(external_pathway=_pathway(evidence=[
        _evidence().model_copy(update={field: " \t "}),
    ]))
    assert _verdict(score_pack(_pack([lead])), "C1").verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("field", ["holder", "vehicle", "dossier_cite", "prime_posture_cite"])
@pytest.mark.parametrize("dummy", ["unknown", "TBD", "n/a", "na", "none", "unk", "x", " "])
def test_dummy_route_cannot_escape_notice_only(field, dummy):
    route = {"holder": None, "vehicle": None, "dossier_cite": None,
             "prime_posture_cite": None}
    route[field] = dummy
    lead = _lead(seller_path=_seller(**route), lead_tier=LeadTier.LEAD_T1,
                 readiness=LeadReadiness.ACTIONABLE)
    card = score_pack(_pack([lead], overlays={lead.lead_id: {"solicitation_only": False}}))
    for check in ("D2", "TIER.SOLICITATION_ONLY", "PACK.SOLICITATION_ONLY_T1T2"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("notice_signal", ["tier", "kind", "host"])
def test_pathway_relabel_does_not_hide_notice_evidence(notice_signal):
    evidence = _evidence().model_dump(mode="json")
    evidence.update(tier="program", kind="budget", source_url="https://agency.example/budget")
    if notice_signal == "host":
        evidence["source_url"] = "https://sam.gov/opp/N1/view"
    else:
        evidence[notice_signal] = "notice"
    lead = _lead(
        external_pathway=_pathway(kind=PathwayKind.VEHICLE_ORDERING, evidence=[evidence]),
        seller_path=_seller(holder="x"), lead_tier=LeadTier.LEAD_T1,
        readiness=LeadReadiness.ACTIONABLE,
    )
    card = score_pack(_pack([lead], overlays={lead.lead_id: {"solicitation_only": False}}))
    assert _verdict(card, "TIER.SOLICITATION_ONLY").verdict is CheckVerdict.FAIL


def test_blank_fit_cannot_escape_solicitation_in_direct_check():
    # Defense even for callers using Pydantic's explicitly unvalidated copy API.
    lead = _lead(seller_path=_seller(holder=None))
    overlay = EvalOverlay(subject_id=lead.lead_id).model_copy(update={"product_fit_cites": ("", " ")})
    assert is_solicitation_only(lead, overlay)


@pytest.mark.parametrize("overlay", [
    {"email": "jane@agency.example"},
    {"email_status": "VERIFIED"},
    {"email_status": "VERIFIED", "email": "jane@agency.example"},
    {"email_status": "VERIFIED", "receipt": "checked directory"},
    {"email_status": "VERIFIED", "email": " ", "receipt": "checked directory"},
    {"email_status": "VERIFIED", "email": "jane@agency.example", "receipt": " "},
])
def test_email_claim_requires_status_address_and_receipt(overlay):
    lead = _lead(lead_tier=LeadTier.LEAD_T1, readiness=LeadReadiness.ACTIONABLE)
    card = score_pack(_pack([lead], overlays={lead.lead_id: overlay}))
    assert _verdict(card, "C3").verdict is CheckVerdict.FAIL
    assert _verdict(card, "TIER.T1T2_REQUIRES_AF").verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("tier", [LeadTier.LEAD_T1, LeadTier.LEAD_T2])
@pytest.mark.parametrize("published", ["contactability", "contacts"])
def test_published_contact_without_verification_fails_c3(tier, published):
    changes = {"contactability": Contactability.PUBLISHED_POC} if published == "contactability" else {
        "published_contacts": [PublishedContact(name="Jane Doe")],
    }
    lead = _lead(external_pathway=_pathway(**changes), lead_tier=tier,
                 readiness=LeadReadiness.ACTIONABLE,
                 contactability=changes.get("contactability", Contactability.ORGANIZATION_ONLY))
    assert _verdict(score_pack(_pack([lead])), "C3").verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("vehicle_field", ["seller", "pathway", "vehicle"])
@pytest.mark.parametrize("support", ["requirement", "vehicle_timing", "budget_timing"])
def test_vehicle_access_requires_non_vehicle_intent_evidence(vehicle_field, support):
    evidence = _evidence().model_dump(mode="json")
    if support != "requirement":
        evidence.update(kind="vehicle" if support == "vehicle_timing" else "budget",
                        tier="program", source_url="https://agency.example/budget",
                        supports=["timing"])
    lead = _lead(
        seller_path=_seller(kind=SellerPathKind.VEHICLE_ACCESS if vehicle_field == "seller"
                            else SellerPathKind.CHANNEL_RESELLER,
                            vehicle="GSA MAS" if vehicle_field != "pathway" else None),
        external_pathway=_pathway(kind=PathwayKind.VEHICLE_ORDERING if vehicle_field == "pathway"
                                  else PathwayKind.SAM_NOTICE,
                                  evidence=[evidence] if support == "requirement"
                                  else [_evidence(), {**evidence, "evidence_id": "E2"}]),
        lead_tier=LeadTier.LEAD_T1, readiness=LeadReadiness.ACTIONABLE,
    )
    card = score_pack(_pack([lead], overlays={lead.lead_id: {"auth_only": False}}))
    expected = CheckVerdict.PASS if support == "budget_timing" else CheckVerdict.FAIL
    assert _verdict(card, "D3").verdict is expected


@pytest.mark.parametrize("url", [
    "https://www.ebuy.gsa.gov/ebuy", "https://login.gov/", "https://agency.example/login",
    "https://agency.example/signin", "https://auth.agency.example/",
    "https://piee.eb.mil/", "https://sam.gov/workspace/contract/opp",
])
@pytest.mark.parametrize("location", ["pathway", "evidence"])
def test_login_wall_urls_fail_c1_and_d3(url, location):
    pathway = _pathway(kind=PathwayKind.VEHICLE_ORDERING).model_dump(mode="json")
    if location == "pathway":
        pathway["source_url"] = url
    else:
        pathway["evidence"][0]["source_url"] = url
    lead = _lead(external_pathway=pathway, lead_tier=LeadTier.LEAD_T1,
                 readiness=LeadReadiness.ACTIONABLE)
    card = score_pack(_pack([lead]))
    for check in ("C1", "D3", "TIER.T1T2_REQUIRES_AF"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("excerpt", ["Sign in to access this content.", "Please log in.", "Authentication required"])
def test_login_wall_excerpt_fails_c1_and_d3(excerpt):
    lead = _lead(external_pathway=_pathway(evidence=[
        _evidence().model_copy(update={"excerpt": excerpt}),
    ]))
    card = score_pack(_pack([lead]))
    for check in ("C1", "D3"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("source", [
    "parent", "trace", "reject_receipts", "hold_receipts", "watch_receipts",
    "active_lead_t1", "active_lead_t2", "by_lead_tier",
])
def test_pruned_declared_lead_fails_inventory(source):
    reject = _lead(lead_tier=LeadTier.REJECT, readiness=LeadReadiness.CLOSED)
    payload = _pack([], parents=[_parent(lead_ids=[reject.lead_id] if source == "parent" else [])],
                    traces=[_trace(reject)] if source == "trace" else [])
    if source == "by_lead_tier":
        payload[source] = [{"lead_tier": "REJECT", "lead_ids": [reject.lead_id]}]
    elif source not in {"parent", "trace"}:
        payload[source] = [reject.lead_id]
    card = score_pack(payload)
    row = _verdict(card, "PACK.REJECT_NOT_DROPPED")
    assert row.verdict is CheckVerdict.FAIL
    assert reject.lead_id in row.receipt
    assert card.lead_ids == ()  # Inventory reconciliation must not mint a row.
    assert reject.lead_id in render_markdown(card)
    assert reject.lead_id in render_csv(card)


def test_orphan_lead_count_uses_parent_inventory():
    lead = _lead()
    card = score_pack(_pack([lead], parents=[_parent(lead_ids=[])]))
    assert card.invented_row_count == 1
    assert _verdict(card, "PACK.NO_ORPHAN_LEADS").verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("supplement", [
    {"receipt": "Supplemental review note"},
    {"email_status": "VERIFIED", "auth_only": False, "solicitation_only": False,
     "receipt": "Supplemental review note"},
    {"email_status": "ABSENT", "email": None},
])
def test_supplement_cannot_wipe_adverse_overlay(supplement):
    lead = _lead(lead_tier=LeadTier.LEAD_T1, readiness=LeadReadiness.ACTIONABLE)
    payload = _pack([lead], overlays={lead.lead_id: {
        "email_status": "UNVERIFIED", "email": "jane@agency.example",
        "auth_only": True, "solicitation_only": True, "product_fit_cites": ["E1"],
    }})
    card = score_pack(payload, {lead.lead_id: supplement})
    for check in ("C3", "D3", "TIER.SOLICITATION_ONLY", "PACK.SOLICITATION_ONLY_T1T2"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL
    merged = load_score_input(payload, {lead.lead_id: supplement}).overlays[0]
    assert merged.email == "jane@agency.example"
    assert merged.product_fit_cites == ("E1",)


def test_supplement_accumulates_stripped_cites_and_receipts():
    lead = _lead()
    payload = _pack([lead], overlays={lead.lead_id: {
        "product_fit_cites": [" E1 "], "receipt": "First review",
    }})
    overlay = load_score_input(payload, {lead.lead_id: {
        "product_fit_cites": ["E2", "E1"], "receipt": "Second review",
    }}).overlays[0]
    assert overlay.product_fit_cites == ("E1", "E2")
    assert overlay.receipt == "First review · Second review"


@pytest.mark.parametrize("span", ["", " \t\n"])
def test_blank_requirement_span_cannot_supply_fit_or_need(span):
    lead = _lead(seller_path=_seller(holder=None))
    parent = _parent(lead_ids=[lead.lead_id], requirement_span=span)
    card = score_pack(_pack([lead], parents=[parent]))
    for check in ("B1", "B2", "P.NEED", "P.FIT", "TIER.T1T2_REQUIRES_AF"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("source", ["reject_receipts", "by_lead_tier", "declared_reject_ids"])
@pytest.mark.parametrize("retained_tier", [LeadTier.REJECT, LeadTier.WATCH])
def test_declared_reject_must_be_retained_as_reject(source, retained_tier):
    lead = _lead(lead_tier=retained_tier,
                 readiness=LeadReadiness.CLOSED if retained_tier is LeadTier.REJECT
                 else LeadReadiness.WATCHING)
    inventory = [{"lead_tier": "REJECT", "lead_ids": [lead.lead_id]}] if source == "by_lead_tier" else [lead.lead_id]
    card = score_pack(_pack([lead], extra={source: inventory}))
    expected = CheckVerdict.PASS if retained_tier is LeadTier.REJECT else CheckVerdict.FAIL
    row = _verdict(card, "PACK.REJECT_NOT_DROPPED")
    assert row.verdict is expected
    if expected is CheckVerdict.FAIL:
        assert lead.lead_id in row.receipt
    assert card.invented_row_count == 0


@pytest.mark.parametrize("source", ["reject_receipts", "by_lead_tier", "declared_lead_ids"])
@pytest.mark.parametrize("bad_id", ["", " \t", None])
def test_malformed_declared_ids_are_not_silently_dropped(source, bad_id):
    lead = _lead()
    inventory = [{"lead_tier": "REJECT", "lead_ids": [bad_id]}] if source == "by_lead_tier" else [bad_id]
    with pytest.raises(ScoreLoadError, match="nonblank lead ids"):
        score_pack(_pack([lead], extra={source: inventory}))


@pytest.mark.parametrize("reverse", [False, True])
def test_adverse_overlay_wins_in_either_order_and_survives_receipt_file(tmp_path, reverse):
    lead = _lead()
    adverse = {"subject_id": lead.lead_id, "email_status": "UNVERIFIED",
               "email": "jane@agency.example", "auth_only": True, "solicitation_only": True}
    favorable = {"subject_id": lead.lead_id, "email_status": "VERIFIED",
                 "email": "jane@agency.example", "auth_only": False,
                 "solicitation_only": False, "receipt": "Directory checked"}
    overlays = [adverse, favorable] if not reverse else [favorable, adverse]
    supplement = tmp_path / "review.json"
    supplement.write_text(json.dumps({lead.lead_id: {"receipt": "Later review"}}))
    card = score_pack(_pack([lead], overlays=overlays), supplement)
    for check in ("C3", "D3", "TIER.SOLICITATION_ONLY"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_email_cannot_inherit_another_addresses_verification(reverse):
    lead = _lead()
    overlays = [{"subject_id": lead.lead_id, "email_status": "VERIFIED",
                 "email": email, "receipt": "Directory checked"}
                for email in ("jane@agency.example", "john@agency.example")]
    if reverse:
        overlays.reverse()
    card = score_pack(_pack([lead], overlays=overlays))
    assert _verdict(card, "C3").verdict is CheckVerdict.FAIL


def test_receipt_only_supplement_preserves_verified_address_and_receipt():
    lead = _lead()
    payload = _pack([lead], overlays={lead.lead_id: {
        "email_status": "VERIFIED", "email": "jane@agency.example",
        "receipt": "Directory checked", "auth_only": True, "solicitation_only": True,
    }})
    card = score_pack(payload, {lead.lead_id: {"receipt": "Follow-up review"}})
    row = _verdict(card, "C3")
    assert row.verdict is CheckVerdict.PASS
    assert "Directory checked" in row.receipt
    assert "Follow-up review" in row.receipt
    for check in ("D3", "TIER.SOLICITATION_ONLY"):
        assert _verdict(card, check).verdict is CheckVerdict.FAIL
