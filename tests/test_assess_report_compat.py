"""Legacy assessment compatibility while the new Assess ledgers are absent.

Offline by construction: the legacy sweep is synthetic, every model-dependent
stage is stubbed, and the report entry point runs in-process.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

from agents.reports.document import build_document
from agents.reports.facts import FactPack
from tests.test_assessment_document import _searches
from tests.test_capture_brief import _content


def test_legacy_assessment_still_builds_without_assess_ledgers(
        tmp_path, monkeypatch):
    """The additive ledger contracts cannot become a prerequisite for Assess.

    This exercises the sanctioned report command's real orchestration and
    deterministic render from the pre-ledger sweep shape. It intentionally
    creates no AssessRun or ledger artifact anywhere in the temporary tree.
    """
    flask = pytest.importorskip("flask")  # noqa: F841

    import agents.decisions.assessment_arbiters as arbiters
    import agents.reports.capture_brief as capture
    import agents.reports.horizon as horizon
    import agents.reports.lint as lint
    import agents.review as review
    import run_capture_brief as runner
    import tools.capability as capability
    import ui.server as server

    client = "Testco"
    slug = "testco"
    root = tmp_path
    cleaned = root / "data" / "cleaned"
    reports = root / "data" / "reports"
    review_dir = root / "data" / "review"
    cleaned.mkdir(parents=True)
    review_dir.mkdir(parents=True)

    # The only intelligence artifact is the canonical legacy sweep fixture.
    sweep = cleaned / f"searches_{slug}.json"
    legacy_searches = _searches(3)
    sweep.write_text(json.dumps(legacy_searches), encoding="utf-8")
    assert not (root / "data" / "assess").exists()
    assert not list(root.rglob("*ledger*.json"))

    # Prove the deterministic compatibility surface still understands the
    # original sweep before exercising the full report entry point.
    monkeypatch.setattr(review, "sweep_artifact_path", lambda _client: str(sweep))
    monkeypatch.setattr(review, "artifact_stem",
                        lambda _client, kind: f"{slug}.{kind}")
    monkeypatch.setattr(capability, "load_profile", lambda _client: None)
    doc = build_document(client, searches=legacy_searches, qualify=None,
                         as_of=date(2026, 7, 10))
    assert doc.counts()["pursue_notices"] == 3
    assert {p.source_id for p in doc.board.pursuits} == {"P1", "P2", "P3"}

    composed = _content(client_name=client)
    composed.opportunities[0].notice_id = "P1"
    composed.opportunities[0].url = "https://sam.gov/opp/P1/view"

    # Keep the entry point real while replacing only model calls. An arbiter
    # outage deliberately yields a DRAFT, which is still a valid generated
    # assessment under the graceful-QA contract.
    pack = FactPack(client_name=client, as_of=date(2026, 7, 10))
    monkeypatch.setattr(runner, "ROOT", str(root))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    monkeypatch.setattr(runner, "build_fact_pack", lambda _client: pack)
    monkeypatch.setattr(runner, "compose_capture_brief",
                        lambda *_args, **_kwargs: composed.model_copy(deep=True))
    # This compatibility fixture predates canonical federal-link builders and
    # intentionally exercises only the additive Assess-ledger boundary. Link
    # integrity has dedicated runner tests and stays offline here.
    from agents.reports.link_integrity import ClientLinkGateOutcome
    from agents.reports.lint import LintResult
    monkeypatch.setattr(
        runner, "run_client_link_gate",
        lambda *_args, **_kwargs: ClientLinkGateOutcome((), (), (), ()),
    )
    monkeypatch.setattr(
        lint, "lint_federal_link_construction",
        lambda *_args, **_kwargs: LintResult(ok=True),
    )
    monkeypatch.setattr(capture, "compose_full_picture",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(horizon, "approved_set", lambda _client: None)
    monkeypatch.setattr(horizon, "load_horizon", lambda _client: None)

    def _offline_arbiter(*_args, **_kwargs):
        raise RuntimeError("offline compatibility test")

    monkeypatch.setattr(arbiters, "arbitrate_assessment", _offline_arbiter)
    monkeypatch.setattr(
        sys, "argv",
        ["run_capture_brief.py", "--client", client, "--without-horizon",
         "--offline"],
    )

    assert runner.main() == 0

    report_path = reports / f"{slug}.federal_opportunity_assessment.html"
    qa_path = reports / f"{slug}.federal_opportunity_assessment.qa.json"
    assert report_path.exists()
    assert qa_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "Federal Opportunity Assessment" in html
    assert client in html
    assert "P1" in html
    assert json.loads(qa_path.read_text(encoding="utf-8"))["state"] == "draft"

    # The Command Center contract still invokes this entry point, and the
    # dashboard can discover the artifact even though no ledger was present.
    cmd = server._step_cmd("report", client, {"kind": "capture_brief"})
    assert cmd[1:] == ["run_capture_brief.py", "--client", client,
                       "--pdf", "--release"]
    monkeypatch.setattr(server, "ROOT", str(root))
    monkeypatch.setattr(server, "REPORT_DIR", str(reports))
    monkeypatch.setattr(server, "REVIEW_DIR", str(review_dir))
    final = server._final_brief(slug)
    assert final is not None
    assert os.path.basename(final["path"]) == report_path.name
    assert final["qa_pass"] is False
    assert server._report_rows(slug)[0]["exists"] is True
