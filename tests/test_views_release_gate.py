"""Direct three-view generation cannot bypass Assess approval."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def test_client_view_without_current_approval_is_do_not_send(
        tmp_path, monkeypatch):
    import agents.assess.approval as approval
    import agents.review as review
    import run_views as runner
    import tools.export_pdf as export_pdf
    from agents.reports.lint import LintViolation

    reports = tmp_path / "data" / "reports"
    cleaned = tmp_path / "data" / "cleaned"
    reports.mkdir(parents=True)
    cleaned.mkdir(parents=True)
    sweep = cleaned / "searches_testco.json"
    sweep.write_text(json.dumps({"results": {}}), encoding="utf-8")
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    monkeypatch.setattr(runner, "_compose_content", lambda _client: object())
    monkeypatch.setattr(runner, "build_document",
                        lambda *_args, **_kwargs: SimpleNamespace(gaps=[]))
    monkeypatch.setattr(runner, "render_assessment",
                        lambda *_args, **_kwargs: "<html><body>assessment</body></html>")
    clean = SimpleNamespace(violations=[])
    for name in (
        "lint_brief_identity", "lint_contact_rendering",
        "lint_client_terminology", "lint_counts", "lint_client_bleed",
        "lint_emdash", "lint_entity_lineage", "lint_notice_tier_claims",
        "lint_screen_census", "lint_whitelabel",
    ):
        monkeypatch.setattr(runner, name,
                            lambda *_args, _clean=clean, **_kwargs: _clean)
    monkeypatch.setattr(review, "sweep_artifact_path", lambda _client: str(sweep))
    monkeypatch.setattr(review, "artifact_stem",
                        lambda _client, kind: f"testco.{kind}")
    monkeypatch.setattr(
        approval, "assess_approval_for_release",
        lambda *_args, **_kwargs: (None, "missing", ["approval required"]))

    exported = []

    def refuse(path):
        exported.append(path)
        return None, [LintViolation(rule="do_not_send", detail="stamped")]

    monkeypatch.setattr(export_pdf, "export_deliverable", refuse)
    monkeypatch.setattr(sys, "argv", [
        "run_views.py", "--client", "Testco", "--view", "client", "--pdf",
        "--offline"])
    monkeypatch.setenv("HOME", str(tmp_path))

    assert runner.main() == 2
    stamped = reports / "testco.assessment.client.DO-NOT-SEND.html"
    assert stamped.exists() and exported == [str(stamped)]
    assert not (reports / "testco.assessment.client.html").exists()
    assert not list(reports.glob("*.pdf"))
    assert not (tmp_path / "Desktop" / "Testco").exists()
