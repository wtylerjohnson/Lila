"""Press Lead Gen orchestration (thin real path).

Doctrine 2026-09-07: press executes Build Plan steps 1-9; Assess is
parent; HOLD remains fail-closed when evidence is thin; notice-only
input is refused; Market Map / lila_release stay untouched.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
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
    render_html,
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


@pytest.fixture(autouse=True)
def isolated_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


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
    assert receipt.stub is False
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
    assert receipt.html_path
    html_path = Path(receipt.html_path)
    assert html_path.is_file()
    html = html_path.read_text(encoding="utf-8")
    assert 'class="report"' in html
    assert 'class="topbar"' in html
    assert 'class="hero"' in html
    assert ':root{--red:#ee0000;' in html
    assert "Testco" in html
    assert "\u2014" not in html
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["html_path"] == str(html_path)
    assert payload["assess_run_id"] == "R1"
    assert payload["active_lead_t1"] == []
    assert payload["active_lead_t2"] == []
    assert payload["market_map_untouched"] is True
    summary = md_path.read_text(encoding="utf-8")
    assert "Press Lead Gen receipt" in summary
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


@pytest.mark.parametrize("markdown", [False, True])
def test_cli_writes_review_receipt(tmp_path, capsys, markdown):
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
    ] + (["--markdown"] if markdown else [])) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["leads"]
    assert payload["active_lead_t1"] == []
    assert payload["active_lead_t2"] == []
    assert (review_dir / "testco.leadgen.json").is_file()
    assert (review_dir / "testco.leadgen.md").is_file() == markdown
    assert bool(payload["markdown_path"]) == markdown
    assert Path(payload["html_path"]).is_file()
    assert captured.err.splitlines()[0] == f'[out] {payload["html_path"]}'


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


def test_press_does_not_write_assess_runs(tmp_path, monkeypatch):
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


def test_html_default_path_and_desktop_copy(tmp_path, monkeypatch):
    folder = Path.home() / "Desktop" / "Testco"
    folder.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    receipt = run_press(assess=_run(), review_dir="review")
    expected = tmp_path / "review" / (
        "testco_Press_Lead_Gen_CLIENT_DELIVERABLE_"
        f"{datetime.now(timezone.utc).astimezone().date().isoformat()}.html")
    assert receipt.html_path == str(expected)
    assert expected.is_file()
    assert (folder / expected.name).read_bytes() == expected.read_bytes()
    assert receipt.markdown_path is None
    assert not list((tmp_path / "review").glob("*.md"))


def test_in_memory_press_does_not_create_artifacts(tmp_path):
    receipt = run_press(assess=_run(), write_markdown=True)
    assert receipt.html_path is receipt.json_path is receipt.markdown_path is None
    assert not (Path.home() / "Desktop").exists()


def test_desktop_copy_failure_preserves_primary(tmp_path, monkeypatch, capsys):
    folder = Path.home() / "Desktop" / "Testco"
    folder.mkdir(parents=True)
    from agents.leadgen import press
    original_write = press.atomic_write_text

    def fail_desktop(path, content):
        if str(folder) in str(path):
            raise OSError("read-only Desktop")
        original_write(path, content)

    monkeypatch.setattr(press, "atomic_write_text", fail_desktop)
    receipt = run_press(assess=_run(), review_dir=tmp_path / "review")
    assert Path(receipt.html_path).is_file()
    assert json.loads(Path(receipt.json_path).read_text())["html_path"] == receipt.html_path
    assert "Desktop copy failed" in capsys.readouterr().err


def test_html_receipts_links_and_copy_hygiene():
    from agents.reports.lint import BANNED_PHRASES

    receipt = run_press(assess=_run(partners=[_partner()]),
                        target_actions=_t1_target_actions())
    original = receipt.model_dump_json()
    html = render_html(receipt)
    assert html == render_html(receipt)
    assert receipt.model_dump_json() == original
    for parent in receipt.parents:
        assert parent.assessment_id in html
    for lead in receipt.leads:
        assert lead.lead_id in html
        assert str(lead.external_pathway.source_url) in html
    for tier in LeadTier:
        assert f'id="tier-{tier.value}"' in html
    assert "0 active leads. Empty is expected when four-leg receipts are missing." in html
    assert "Coverage placeholder" in html
    assert "Decision-trace placeholder" in html

    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = set()
            self.fragments = []
            self.tags = set()

        def handle_starttag(self, tag, attrs):
            self.tags.add(tag)
            attrs = dict(attrs)
            if "id" in attrs:
                self.ids.add(attrs["id"])
            href = attrs.get("href", "")
            if href.startswith("#"):
                self.fragments.append(href[1:])

    parsed = Markup()
    parsed.feed(html)
    assert set(parsed.fragments) <= parsed.ids
    assert not {"script", "link", "img", "iframe"} & parsed.tags
    for phrase in [*BANNED_PHRASES, "unverified", "not verified", "did not verify",
                   "no record", "not capability-verified", "pending verification",
                   "zero matched", "could not confirm"]:
        dirty = receipt.model_copy(update={"coverage_placeholder": phrase.upper()})
        rendered = render_html(dirty)
        assert phrase.casefold() not in rendered.casefold()
        assert "Review source detail in JSON sidecar." in rendered
    hostile = receipt.model_copy(update={
        "client_name": '<script>alert("x")</script> & Example &#8212; Name',
    })
    html = render_html(hostile)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "\u2014" not in html


def test_draft_mapper_and_press_agree_on_children_when_not_promoted():
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
