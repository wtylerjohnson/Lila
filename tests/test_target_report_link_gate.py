"""The target-report press uses the shared post-render external-link gate."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def test_target_report_offline_writes_manual_link_sidecar(
        tmp_path, monkeypatch):
    import agents.reports.lint as lint
    import run_target_report as runner

    review = tmp_path / "data" / "review"
    reports = tmp_path / "data" / "reports"
    review.mkdir(parents=True)
    reports.mkdir(parents=True)
    (review / "testco.contacts.json").write_text(
        json.dumps({"client_name": "Testco", "contacts": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "REVIEW_DIR", str(review))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    monkeypatch.setattr(runner, "qa_target_report", lambda _plan: [])
    guid = "a" * 32
    monkeypatch.setattr(
        runner, "render_target_report",
        lambda *_args, **_kwargs: (
            "<html><body><a href=\"https://sam.gov/workspace/contract/opp/"
            f"{guid}/view\">notice</a></body></html>"
        ),
    )
    clean = SimpleNamespace(violations=[])
    for name in (
        "lint_client_terminology", "lint_contact_rendering", "lint_gtm_logo",
        "lint_federal_link_construction", "lint_sam_workspace_links",
        "lint_whitelabel",
    ):
        monkeypatch.setattr(
            lint, name, lambda *_args, _clean=clean, **_kwargs: _clean)
    calls = []

    def gate(html, *, enabled):
        calls.append(enabled)
        assert "sam.gov/workspace" not in html
        assert f"https://sam.gov/opp/{guid}/view" in html
        return SimpleNamespace(
            results=(), violations=(),
            manual_checks=("https://manual.gov · Contacts · offline",),
            claim_warnings=(),
        )

    monkeypatch.setattr(runner, "run_client_link_gate", gate)
    monkeypatch.setattr(sys, "argv", [
        "run_target_report.py", "--client", "Testco", "--offline",
    ])
    monkeypatch.setenv("HOME", str(tmp_path))

    assert runner.main() == 0
    assert calls == [False]
    sidecar = reports / "testco.target_report.link-integrity.internal.md"
    text = sidecar.read_text(encoding="utf-8")
    assert "## MANUAL LINK CHECK" in text
    assert "https://manual.gov" in text

    monkeypatch.setattr(
        runner, "run_client_link_gate",
        lambda *_args, **_kwargs: SimpleNamespace(
            results=(), violations=(), manual_checks=(), claim_warnings=()),
    )
    assert runner.main() == 0
    assert not sidecar.exists()
