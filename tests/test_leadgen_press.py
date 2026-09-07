"""Press Lead Gen orchestration stub.

Doctrine 2026-09-06: stub executes Build Plan steps 1-9; Assess is
parent; drafts are WATCH/HOLD only; notice-only input is refused;
Market Map / lila_release stay untouched.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agents.leadgen import (
    BUILD_PLAN_STEPS,
    LeadTier,
    PressLeadGenError,
    draft_lead_rows,
    leadgen_receipt_path,
    run_press,
)
from agents.leadgen.press import (
    COVERAGE_PLACEHOLDER,
    MARKET_MAP_NOTE,
    MISSING_ASSESS,
    NOTICE_ONLY,
    TRACE_PLACEHOLDER,
    render_markdown,
)
from agents.leadgen.press import (
    main as press_main,
)
from tests.test_leadgen_from_assess import (
    _partner,
    _run,
    _t1_target_actions,
)

ROOT = Path(__file__).resolve().parents[1]
PRESS_PY = ROOT / "agents" / "leadgen" / "press.py"


def test_build_plan_names_steps_1_through_9():
    assert [step[0] for step in BUILD_PLAN_STEPS] == list(range(1, 10))
    names = [step[1] for step in BUILD_PLAN_STEPS]
    assert names[0] == "load_ontology"
    assert names[3] == "assess_handoff"
    assert names[5] == "draft_parents"
    assert names[6] == "draft_children"
    assert names[7] == "publish_tiers"
    assert names[8] == "publish_coverage_trace"


def test_stub_on_assess_and_target_actions_is_parent_plus_watch_hold(tmp_path):
    run = _run(partners=[_partner()])
    actions = _t1_target_actions()
    receipt = run_press(
        assess=run,
        target_actions=actions,
        review_dir=tmp_path,
        write_markdown=True,
    )
    assert receipt.stub is True
    assert receipt.market_map_untouched is True
    assert receipt.assess_run_id == "R1"
    assert receipt.client_name == "Testco"
    assert receipt.client_slug == "testco"
    assert receipt.parents
    assert any(parent.subject_id == "L1" for parent in receipt.parents)
    assert receipt.leads
    assert {row.lead_tier for row in receipt.leads} <= {
        LeadTier.WATCH, LeadTier.HOLD}
    assert receipt.active_lead_t1 == ()
    assert receipt.active_lead_t2 == ()
    assert LeadTier.LEAD_T1 not in {row.lead_tier for row in receipt.leads}
    assert LeadTier.LEAD_T2 not in {row.lead_tier for row in receipt.leads}
    assert receipt.watch_receipts or receipt.hold_receipts
    assert receipt.coverage_placeholder == COVERAGE_PLACEHOLDER
    assert receipt.decision_trace_placeholder == TRACE_PLACEHOLDER
    assert MARKET_MAP_NOTE in receipt.steps[-1].notes
    assert [step.step for step in receipt.steps] == list(range(1, 10))
    assert receipt.steps[1].status.value == "handoff"
    assert receipt.steps[3].status.value == "handoff"
    assert receipt.steps[5].name == "draft_parents"
    assert receipt.steps[6].name == "draft_children"

    json_path = tmp_path / "testco.leadgen.json"
    md_path = tmp_path / "testco.leadgen.md"
    assert Path(receipt.json_path) == json_path
    assert Path(receipt.markdown_path) == md_path
    assert json_path.is_file()
    assert md_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["assess_run_id"] == "R1"
    assert payload["active_lead_t1"] == []
    assert payload["active_lead_t2"] == []
    assert payload["market_map_untouched"] is True
    summary = md_path.read_text(encoding="utf-8")
    assert "Press Lead Gen stub receipt" in summary
    assert "lila_release is untouched" in summary
    assert "—" not in summary
    assert leadgen_receipt_path("Testco", review_dir=tmp_path) == json_path


def test_missing_assess_fails_closed():
    with pytest.raises(PressLeadGenError, match="missing assess input"):
        run_press(assess=None)
    with pytest.raises(PressLeadGenError) as missing:
        run_press(assess=None)
    assert str(missing.value) == MISSING_ASSESS


def test_notice_only_input_is_refused():
    notice = {
        "notice_id": "N1",
        "title": "Network visibility",
        "solicitation_number": "70-TEST-26",
    }
    with pytest.raises(PressLeadGenError, match="notice-only"):
        run_press(assess=notice)
    with pytest.raises(PressLeadGenError) as refused:
        run_press(assess=[notice])
    assert str(refused.value) == NOTICE_ONLY
    sweep = {"results": {"sam.gov": [notice]}}
    with pytest.raises(PressLeadGenError, match="notice-only"):
        run_press(assess=sweep)
    with pytest.raises(PressLeadGenError, match="not readable"):
        run_press(assess="/no/such/assess_run.json")


def test_optional_dossier_is_cited_on_parents(tmp_path):
    run = _run(partners=[_partner()])
    dossier = tmp_path / "dossier.json"
    dossier.write_text(json.dumps({
        "schema_version": "intake.dossier.v1",
        "identity": {"status": "bound"},
    }), encoding="utf-8")
    receipt = run_press(
        assess=run,
        target_actions=_t1_target_actions(),
        dossier_path=dossier,
    )
    assert receipt.dossier_path == str(dossier)
    assert receipt.dossier_schema_version == "intake.dossier.v1"
    assert receipt.identity_status == "bound"
    assert receipt.steps[0].status.value == "complete"
    assert all(
        parent.dossier_schema_version == "intake.dossier.v1"
        for parent in receipt.parents
    )
    assert all(parent.identity_status == "bound" for parent in receipt.parents)


def test_unreadable_dossier_path_fails_closed(tmp_path):
    with pytest.raises(PressLeadGenError, match="dossier path is not readable"):
        run_press(
            assess=_run(partners=[_partner()]),
            dossier_path=tmp_path / "missing-dossier.json",
        )


def test_client_mismatch_fails_closed():
    with pytest.raises(PressLeadGenError, match="does not match AssessRun"):
        run_press(assess=_run(), client_name="Otherco")


def test_cli_writes_review_receipt(tmp_path, capsys):
    run = _run(partners=[_partner()])
    assess_path = tmp_path / "assess.json"
    actions_path = tmp_path / "actions.json"
    assess_path.write_text(
        json.dumps(run.model_dump(mode="json")), encoding="utf-8")
    actions_path.write_text(
        json.dumps(_t1_target_actions()), encoding="utf-8")
    review_dir = tmp_path / "review"
    assert press_main([
        "--assess", str(assess_path),
        "--target-actions", str(actions_path),
        "--review-dir", str(review_dir),
        "--markdown",
    ]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["leads"]
    assert payload["active_lead_t1"] == []
    assert payload["active_lead_t2"] == []
    assert (review_dir / "testco.leadgen.json").is_file()
    assert (review_dir / "testco.leadgen.md").is_file()
    assert "[out]" in captured.err


def test_cli_refuses_notice_only(tmp_path, capsys):
    notices = tmp_path / "notices.json"
    notices.write_text(json.dumps({
        "notice_id": "N1",
        "title": "A notice is not a lead",
    }), encoding="utf-8")
    assert press_main([
        "--assess", str(notices),
        "--review-dir", str(tmp_path),
    ]) == 2
    assert "notice-only" in capsys.readouterr().err


def test_stub_does_not_write_assess_runs(tmp_path, monkeypatch):
    assess_runs = tmp_path / "assess_runs"
    assess_runs.mkdir()
    monkeypatch.chdir(tmp_path)
    receipt = run_press(
        assess=_run(partners=[_partner()]),
        target_actions=_t1_target_actions(),
        review_dir=tmp_path / "review",
    )
    assert receipt.json_path
    assert list(assess_runs.rglob("*")) == []
    assert "assess_runs" not in (receipt.json_path or "")


def test_markdown_summary_has_empty_active_lists():
    receipt = run_press(
        assess=_run(partners=[_partner()]),
        target_actions=_t1_target_actions(),
    )
    text = render_markdown(receipt)
    assert "Active lead T1: 0" in text
    assert "Active lead T2: 0" in text
    assert "lila_release is untouched" in text
    assert "—" not in text


def test_draft_mapper_and_stub_agree_on_children():
    run = _run(partners=[_partner()])
    actions = _t1_target_actions()
    batch = draft_lead_rows(run, actions)
    receipt = run_press(assess=run, target_actions=actions)
    assert [row.lead_id for row in receipt.leads] == [
        row.lead_id for row in batch.leads]
    assert {row.lead_tier for row in receipt.leads} == {
        row.lead_tier for row in batch.leads}


def test_press_does_not_import_discovery_or_release_owners():
    source = PRESS_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = (
        "targeting_rules", "apollo_targets", "apollo",
        "run_searches", "run_qualify", "lila_release",
        "product_bundle", "build_target_actions",
        "materialize_current_assess_run",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        joined = " ".join(modules).casefold()
        for token in forbidden:
            assert token not in joined, f"press.py imports {token}"
