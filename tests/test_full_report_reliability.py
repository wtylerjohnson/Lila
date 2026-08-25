"""Full-federal assessment availability and release safety.

Hermetic: synthetic sweep, in-process entry point, no subprocess, browser,
network, or LLM calls.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

from agents.reports.facts import FactPack
from agents.reports.link_integrity import (
    ClientLinkGateOutcome,
    LinkCheckResult,
    LinkClass,
    dead_link_violations,
)
from agents.reports.lint import LintResult
from tests.test_assessment_document import _searches
from tests.test_capture_brief import _content


def _prepare(tmp_path, monkeypatch):
    import agents.reports.capture_brief as capture
    import agents.reports.horizon as horizon
    import agents.reports.lint as lint
    import agents.review as review
    import run_capture_brief as runner
    import tools.capability as capability

    root = tmp_path
    cleaned = root / "data" / "cleaned"
    reports = root / "data" / "reports"
    review_dir = root / "data" / "review"
    cleaned.mkdir(parents=True)
    reports.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    sweep = cleaned / "searches_testco.json"
    sweep.write_text(json.dumps(_searches(3)), encoding="utf-8")

    monkeypatch.setattr(runner, "ROOT", str(root))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    monkeypatch.setattr(review, "sweep_artifact_path", lambda _client: str(sweep))
    monkeypatch.setattr(review, "artifact_stem",
                        lambda _client, kind: f"testco.{kind}")
    monkeypatch.setattr(capability, "load_profile", lambda _client: None)
    monkeypatch.setattr(capture, "compose_full_picture",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(horizon, "horizon_for_report",
                        lambda _client: (None, "missing", []))
    monkeypatch.setattr(
        runner, "build_fact_pack",
        lambda _client: FactPack(client_name="Testco",
                                 as_of=date(2026, 7, 10)),
    )
    # The shared _content fixture intentionally carries a non-production
    # ``sam.gov/x`` placeholder. Reliability tests isolate unrelated runner
    # behavior; dedicated cases below supply explicit link-gate outcomes.
    monkeypatch.setattr(
        runner, "run_client_link_gate",
        lambda *_args, **_kwargs: ClientLinkGateOutcome((), (), (), ()),
    )
    monkeypatch.setattr(
        lint, "lint_federal_link_construction",
        lambda *_args, **_kwargs: LintResult(ok=True),
    )
    monkeypatch.setenv("HOME", str(root))
    return runner, reports, review_dir


def _run(runner, monkeypatch, *, pdf=False):
    argv = ["run_capture_brief.py", "--client", "Testco",
            "--without-horizon", "--release", "--offline"]
    if pdf:
        argv.append("--pdf")
    monkeypatch.setattr(sys, "argv", argv)
    return runner.main()


def _configure_composed_draft(runner, monkeypatch):
    """Keep integration cases deterministic and off every optional model."""
    import agents.decisions.assessment_arbiters as arbiters

    monkeypatch.setattr(
        runner, "compose_capture_brief",
        lambda *_args, **_kwargs: _content(client_name="Testco"),
    )
    monkeypatch.setattr(
        arbiters, "arbitrate_assessment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("arbiter unavailable")),
    )


@pytest.mark.parametrize("error_type", [TimeoutError, RuntimeError])
def test_primary_compose_failure_writes_unreleaseable_deterministic_draft(
        tmp_path, monkeypatch, capsys, error_type):
    """2026-07-10: AI timeout/error may shed prose, never the assessment."""
    runner, reports, review_dir = _prepare(tmp_path, monkeypatch)

    def fail_compose(*_args, **_kwargs):
        raise error_type("analysis layer unavailable")

    monkeypatch.setattr(runner, "compose_capture_brief", fail_compose)
    assert _run(runner, monkeypatch, pdf=True) == 0

    out = reports / "testco.federal_opportunity_assessment.html"
    qa_path = reports / "testco.federal_opportunity_assessment.qa.json"
    assert out.exists()
    page = out.read_text(encoding="utf-8")
    assert 'data-assessment-fallback="1"' in page
    assert "DRAFT · DETERMINISTIC FALLBACK · DO NOT RELEASE" in page
    assert "Video Wall Modernization" in page
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assert qa["state"] == "draft"
    assert qa["fallback"] is True
    assert qa["release_eligible"] is False
    assert any(a["rule"] == "AI_FALLBACK" and a["action"] == "flag"
               for a in qa["actions"])
    assert not list(reports.glob("*.pdf"))
    assert not (tmp_path / "Desktop" / "Testco").exists()
    assert "release disabled" in capsys.readouterr().err

    # The Command Center treats the same stable artifact as a visible DRAFT.
    pytest.importorskip("flask")
    import ui.server as server
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    monkeypatch.setattr(server, "REPORT_DIR", str(reports))
    monkeypatch.setattr(server, "REVIEW_DIR", str(review_dir))
    final = server._final_brief("testco")
    assert final is not None
    assert final["qa_pass"] is False
    assert server._report_rows("testco")[0]["qa_pass"] is False
    shelf = next(d for d in server.client_documents("testco")
                 if d["label"] == "Opportunity Assessment")
    assert shelf["qa_pass"] is False

    client = server.app.test_client()
    download = client.get("/client/testco/download/foa.html")
    assert download.status_code == 409
    assert download.get_json()["do_not_send"] is True
    export = client.post("/api/export/pdf", json={
        "slug": "testco", "kind": "foa"})
    assert export.status_code == 409
    assert export.get_json()["do_not_send"] is True


def test_factpack_failure_uses_same_deterministic_draft(tmp_path, monkeypatch):
    """2026-07-10: once scope resolves, FactPack cannot erase Assess."""
    runner, reports, _review = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "build_fact_pack",
        lambda _client: (_ for _ in ()).throw(TimeoutError("facts timeout")),
    )
    monkeypatch.setattr(
        runner, "compose_capture_brief",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("compose must not run after FactPack failure")),
    )
    assert _run(runner, monkeypatch) == 0
    out = reports / "testco.federal_opportunity_assessment.html"
    assert "DETERMINISTIC FALLBACK" in out.read_text(encoding="utf-8")


def test_review_and_arbiter_outages_still_write_normal_draft(
        tmp_path, monkeypatch, capsys):
    """2026-07-10: optional review/audit outages retain composed content."""
    import agents.decisions.assessment_arbiters as arbiters
    import agents.reports.capture_brief as capture

    runner, reports, _review = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        capture, "compose_full_picture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("review timeout")),
    )
    monkeypatch.setattr(
        runner, "compose_capture_brief",
        lambda *_args, **_kwargs: _content(client_name="Testco"),
    )
    monkeypatch.setattr(
        arbiters, "arbitrate_assessment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("arbiter unavailable")),
    )

    assert _run(runner, monkeypatch) == 0
    out = reports / "testco.federal_opportunity_assessment.html"
    qa = json.loads((reports / "testco.federal_opportunity_assessment.qa.json")
                    .read_text(encoding="utf-8"))
    assert out.exists()
    assert qa["state"] == "draft"
    err = capsys.readouterr().err
    assert "composing without a directive" in err
    assert "panel errored" in err


def test_legacy_release_flag_is_ignored_and_never_delivers(
        tmp_path, monkeypatch):
    import agents.assess.approval as approval
    import agents.decisions.assessment_arbiters as arbiters
    import tools.export_pdf as export_pdf

    runner, reports, _review = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "compose_capture_brief",
        lambda *_args, **_kwargs: _content(client_name="Testco"))
    monkeypatch.setattr(
        arbiters, "arbitrate_assessment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("arbiter unavailable")))
    monkeypatch.setattr(
        approval, "assess_approval_for_release",
        lambda *_args, **_kwargs: (
            None, "invalid", ["Assess capability profile changed"]))
    monkeypatch.setattr(
        export_pdf, "export_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("approval-blocked draft must not export a PDF")))

    assert _run(runner, monkeypatch, pdf=True) == 0
    qa = json.loads((reports / "testco.federal_opportunity_assessment.qa.json")
                    .read_text(encoding="utf-8"))
    assert qa["state"] == "draft"
    assert not any(a["rule"] == "ASSESS_APPROVAL"
                   for a in qa["actions"])
    assert not list(reports.glob("*.pdf"))
    assert not (tmp_path / "Desktop" / "Testco").exists()


def test_fallback_atomic_replace_failure_preserves_prior_assessment(
        tmp_path, monkeypatch):
    """2026-07-10: failed replacement cannot truncate the last good HTML."""
    runner, reports, _review = _prepare(tmp_path, monkeypatch)
    out = reports / "testco.federal_opportunity_assessment.html"
    out.write_text("LAST GOOD ASSESSMENT", encoding="utf-8")
    monkeypatch.setattr(
        runner, "compose_capture_brief",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("compose timeout")),
    )

    real_replace = runner.os.replace

    def fail_main_replace(source, destination):
        if os.fspath(destination) == os.fspath(out):
            raise OSError("simulated final replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(runner.os, "replace", fail_main_replace)
    assert _run(runner, monkeypatch) == 2
    assert out.read_text(encoding="utf-8") == "LAST GOOD ASSESSMENT"
    assert not any(p.name.endswith(".tmp") for p in reports.iterdir())
    qa = json.loads((reports / "testco.federal_opportunity_assessment.qa.json")
                    .read_text(encoding="utf-8"))
    assert qa["state"] == "draft"


@pytest.mark.parametrize(
    ("classification", "url", "expected_rule"),
    [
        (LinkClass.DEAD, "https://agency.gov/dead", "external_link_dead"),
        (LinkClass.INVALID, "https://agency.gov/invalid",
         "external_link_invalid"),
        (LinkClass.BUILDER_BLOCKED, "https://sam.gov/opp/"
         "0123456789abcdef0123456789abcdef/view",
         "federal_link_record_blocked"),
    ],
)
def test_hard_link_classes_force_draft_exit_two_and_cannot_be_adjudicated(
        tmp_path, monkeypatch, classification, url, expected_rule):
    runner, reports, review_dir = _prepare(tmp_path, monkeypatch)
    _configure_composed_draft(runner, monkeypatch)
    result = LinkCheckResult(
        url=url,
        classification=classification,
        sections=("Evidence",),
        proof="synthetic hard-link proof",
    )
    outcome = ClientLinkGateOutcome(
        results=(result,),
        violations=tuple(dead_link_violations([result])),
        manual_checks=(),
        claim_warnings=(),
    )
    monkeypatch.setattr(
        runner, "run_client_link_gate", lambda *_args, **_kwargs: outcome)
    (review_dir / "testco.flag_decisions.json").write_text(
        json.dumps({"dismiss": [{"match": url, "note": "accept link"}]}),
        encoding="utf-8",
    )

    import tools.export_pdf as export_pdf
    monkeypatch.setattr(
        export_pdf, "export_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("hard link issue must skip PDF generation")),
    )
    monkeypatch.setattr(
        export_pdf, "export_deliverable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("hard link issue must skip release export")),
    )

    assert _run(runner, monkeypatch, pdf=True) == 2
    qa = json.loads((
        reports / "testco.federal_opportunity_assessment.qa.json"
    ).read_text(encoding="utf-8"))
    action = next(a for a in qa["actions"] if a["rule"] == expected_rule)
    assert action["action"] == "flag"
    assert "operator-adjudicated" not in action["reason"]
    assert qa["state"] == "draft"
    assert not list(reports.glob("*.pdf"))


def test_offline_link_warnings_are_nonblocking_and_reach_internal_sidecar(
        tmp_path, monkeypatch):
    runner, reports, _review_dir = _prepare(tmp_path, monkeypatch)
    _configure_composed_draft(runner, monkeypatch)
    from agents.reports.facts import Fact, SourceSystem
    agency_url = "https://agency.gov/claim"
    pack = FactPack(
        client_name="Testco",
        as_of=date(2026, 7, 10),
        facts=[Fact(
            id="F9",
            kind="market",
            text="$20B agency ceiling",
            value={"amount": 20_000_000_000},
            source=agency_url,
            source_system=SourceSystem.AGENCY_DOC,
            source_record_id="agency-ceiling",
        )],
    )
    monkeypatch.setattr(runner, "build_fact_pack", lambda _client: pack)
    observed = {}
    outcome = ClientLinkGateOutcome(
        results=(),
        violations=(),
        manual_checks=(
            "https://agency.gov/manual · Evidence · checks disabled/offline",
        ),
        claim_warnings=(
            "F9: value $20B is not visible in fetched text at "
            "https://agency.gov/claim",
        ),
    )

    def gate(*_args, **kwargs):
        observed["enabled"] = kwargs["enabled"]
        observed["extra_links"] = kwargs["extra_links"]
        observed["figure"] = kwargs["figures"][0]
        return outcome

    monkeypatch.setattr(runner, "run_client_link_gate", gate)
    assert _run(runner, monkeypatch) == 0
    assert observed["enabled"] is False
    assert observed["extra_links"] == [(agency_url, "Figure provenance")]
    assert observed["figure"].source_url == agency_url
    assert observed["figure"].raw == 20_000_000_000
    internal = (
        reports / "testco.federal_opportunity_assessment.internal.md"
    ).read_text(encoding="utf-8")
    assert "## MANUAL LINK CHECK" in internal
    assert "https://agency.gov/manual" in internal
    assert "## AGENCY_DOC claim-visibility warnings" in internal
    assert "F9: value $20B" in internal


def test_command_center_capture_brief_command_is_internal_only():
    """Only the canonical LILA transaction receives release authority."""
    pytest.importorskip("flask")
    import ui.server as server

    cmd = server._step_cmd("report", "Testco", {"kind": "capture_brief"})
    assert cmd[1:] == ["run_capture_brief.py", "--client", "Testco"]
