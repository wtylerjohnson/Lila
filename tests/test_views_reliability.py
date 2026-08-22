"""Three-view assessment availability when optional composition fails."""

from __future__ import annotations

import json
from datetime import date
import sys
from types import SimpleNamespace

import pytest


def _prepare(tmp_path, monkeypatch):
    import agents.assess.approval as approval
    import agents.decisions.research_picture as research_picture
    import agents.reports.capture_brief as capture_brief
    import agents.reports.facts as facts
    import agents.review as review
    import run_views as runner

    reports = tmp_path / "data" / "reports"
    cleaned = tmp_path / "data" / "cleaned"
    reports.mkdir(parents=True)
    cleaned.mkdir(parents=True)
    sweep = cleaned / "searches_testco.json"
    sweep.write_text(json.dumps({
        "client": "Testco",
        "results": {"sam.gov": [], "triage": {}},
    }), encoding="utf-8")

    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    built_content = []

    def build_document(*_args, content=None, **_kwargs):
        built_content.append(content)
        return SimpleNamespace(gaps=[])

    monkeypatch.setattr(runner, "build_document", build_document)
    monkeypatch.setattr(
        runner, "render_assessment",
        lambda *_args, **_kwargs: "<html><body>assessment</body></html>",
    )
    clean = SimpleNamespace(violations=[])
    for name in (
        "lint_brief_identity", "lint_contact_rendering",
        "lint_client_terminology", "lint_counts", "lint_client_bleed",
        "lint_emdash", "lint_entity_lineage", "lint_notice_tier_claims",
        "lint_screen_census", "lint_whitelabel",
    ):
        monkeypatch.setattr(
            runner, name,
            lambda *_args, _clean=clean, **_kwargs: _clean,
        )
    monkeypatch.setattr(review, "sweep_artifact_path", lambda _client: str(sweep))
    monkeypatch.setattr(
        review, "artifact_stem", lambda _client, kind: f"testco.{kind}")
    approval_calls = []
    monkeypatch.setattr(
        approval,
        "assess_approval_for_release",
        lambda client: (
            approval_calls.append(client) or {}, "approved", []),
    )
    monkeypatch.setattr(
        facts, "build_fact_pack",
        # faithful FactPack shape: as_of is a required field of the real
        # model and the freshness gate (2026-07-16) reads it
        lambda _client: SimpleNamespace(facts=[], warnings=[],
                                        as_of=date(2026, 7, 6)),
    )
    monkeypatch.setattr(research_picture, "distill", lambda _results: {})
    monkeypatch.setenv("HOME", str(tmp_path))
    return {
        "runner": runner,
        "capture_brief": capture_brief,
        "reports": reports,
        "built_content": built_content,
        "approval_calls": approval_calls,
    }


@pytest.mark.parametrize("phase", ["review", "compose"])
@pytest.mark.parametrize("error_type", [TimeoutError, RuntimeError])
def test_compose_failure_writes_every_view_as_do_not_send(
        tmp_path, monkeypatch, capsys, phase, error_type):
    env = _prepare(tmp_path, monkeypatch)
    capture_brief = env["capture_brief"]

    def review(*_args, **_kwargs):
        if phase == "review":
            raise error_type("simulated review failure")
        return {"review": "ok"}

    def compose(*_args, **_kwargs):
        if phase == "compose":
            raise error_type("simulated compose failure")
        return object()

    monkeypatch.setattr(capture_brief, "compose_full_picture", review)
    monkeypatch.setattr(capture_brief, "compose_capture_brief", compose)
    monkeypatch.setattr(sys, "argv", [
        "run_views.py", "--client", "Testco", "--offline",
    ])

    assert env["runner"].main() == 2
    assert env["built_content"] == [None]
    assert env["approval_calls"] == ["Testco"]
    for view in ("client", "sales", "internal"):
        stamped = env["reports"] / (
            f"testco.assessment.{view}.DO-NOT-SEND.html")
        assert stamped.exists()
        assert not (env["reports"] / f"testco.assessment.{view}.html").exists()
    assert not (tmp_path / "Desktop" / "Testco").exists()
    stderr = capsys.readouterr().err
    assert "continuing with the deterministic assessment" in stderr
    assert sum(
        "FAIL: compose_failure" in line
        for line in stderr.splitlines()
    ) == 3


def test_no_compose_is_intentional_and_does_not_gain_failure_stamp(
        tmp_path, monkeypatch):
    env = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        env["runner"], "_compose_content",
        lambda _client: (_ for _ in ()).throw(
            AssertionError("--no-compose called the composer")),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_views.py", "--client", "Testco", "--no-compose", "--offline",
    ])

    assert env["runner"].main() == 0
    assert env["built_content"] == [None]
    assert env["approval_calls"] == ["Testco"]
    for view in ("client", "sales", "internal"):
        assert (env["reports"] / f"testco.assessment.{view}.html").exists()
        assert not (env["reports"] / (
            f"testco.assessment.{view}.DO-NOT-SEND.html")).exists()
    assert not (tmp_path / "Desktop" / "Testco").exists()


def test_link_gate_runs_for_client_and_sales_only(tmp_path, monkeypatch):
    env = _prepare(tmp_path, monkeypatch)
    calls = []

    def gate(html, *, enabled, figures):
        calls.append((html, enabled, figures))
        return SimpleNamespace(
            results=(), violations=(),
            manual_checks=("https://example.gov · Evidence · offline",),
            claim_warnings=(),
        )

    monkeypatch.setattr(env["runner"], "run_client_link_gate", gate)
    monkeypatch.setattr(sys, "argv", [
        "run_views.py", "--client", "Testco", "--no-compose", "--offline",
    ])

    assert env["runner"].main() == 0
    assert calls == [
        ("<html><body>assessment</body></html>", False, []),
        ("<html><body>assessment</body></html>", False, []),
    ]
    for view in ("client", "sales"):
        sidecar = env["reports"] / (
            f"testco.assessment.{view}.link-integrity.internal.md")
        assert "## MANUAL LINK CHECK" in sidecar.read_text(encoding="utf-8")
    assert not list(env["reports"].glob(
        "*internal.link-integrity.internal.md"))
