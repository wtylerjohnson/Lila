"""Agency report availability: compose fallback, cache, and atomic writes.

Hermetic: synthetic sweep, in-process entry point, no network, subprocess, or
LLM calls.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from agents.decisions.schemas import IntakeStrategy
from tests.test_assessment_document import _searches
from tests.test_capture_brief import _content
from tools.capability import CapabilityTerms, ClientProfile


def _prepare(tmp_path, monkeypatch):
    import agents.assess.approval as approval
    import agents.reports.lint as lint
    import agents.review as review
    import run_agency_report as runner
    import tools.capability as capability

    root = tmp_path
    cleaned = root / "data" / "cleaned"
    reports = root / "data" / "reports"
    cleaned.mkdir(parents=True)
    reports.mkdir(parents=True)
    canonical_path = cleaned / "searches_testco.json"
    canonical_path.write_text(json.dumps(_searches(3)), encoding="utf-8")

    strategy = IntakeStrategy(
        client_name="Testco", pursuit_strategy="test strategy",
        confidence=0.9, review_gate="operator approved",
    )
    profile = ClientProfile(
        client_name="Testco",
        capability_terms=CapabilityTerms(core=["KVM"], adjacent=[], excluded=[]),
        naics_boundary=["334310", "541512"],
    )
    monkeypatch.setattr(runner, "ROOT", str(root))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    monkeypatch.setattr(runner, "refresh_market_slice",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(review, "load_approved", lambda _client: strategy)
    monkeypatch.setattr(capability, "load_profile", lambda _client: profile)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(
        approval, "current_assess_binding",
        lambda *_args, **_kwargs: {
            "version": 1, "scope_designator": "agency_dod",
            "sweep_artifact": "searches_testco.agency_dod.json",
            "sweep_sha256": "sweep", "profile_sha256": "profile",
        })
    monkeypatch.setattr(
        approval, "assess_approval_for_release",
        lambda *_args, **_kwargs: ({"client": "Testco"}, "approved", []))
    monkeypatch.setattr(
        runner, "run_client_link_gate",
        lambda *_args, **_kwargs: SimpleNamespace(
            results=(), violations=(), manual_checks=(), claim_warnings=()),
    )

    clean = SimpleNamespace(violations=[])
    for name in (
        "lint_brief_identity", "lint_contact_rendering",
        "lint_client_terminology", "lint_counts", "lint_client_bleed",
        "lint_emdash", "lint_entity_lineage",
        "lint_federal_link_construction", "lint_notice_tier_claims",
        "lint_screen_census", "lint_whitelabel",
    ):
        monkeypatch.setattr(lint, name,
                            lambda *_args, _clean=clean, **_kwargs: _clean)

    return runner, canonical_path, reports


def test_missing_assess_approval_stamps_agency_report(tmp_path, monkeypatch):
    import agents.assess.approval as approval

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        approval, "assess_approval_for_release",
        lambda *_args, **_kwargs: (
            None, "missing", ["Assess results have not been approved for release"]))

    assert _run(runner, monkeypatch, no_compose=True) == 2
    assert (reports /
            "testco.agency_dod.assessment.client.DO-NOT-SEND.html").exists()
    assert not (reports / "testco.agency_dod.assessment.client.html").exists()
    assert not (tmp_path / "Desktop" / "Testco").exists()


def _run(runner, monkeypatch, *, no_compose=False):
    argv = ["run_agency_report.py", "--client", "Testco",
            "--agency", "DoD", "--offline"]
    if no_compose:
        argv.append("--no-compose")
    monkeypatch.setattr(sys, "argv", argv)
    return runner.main()


def test_external_dead_link_blocks_agency_release(tmp_path, monkeypatch):
    from agents.reports.lint import LintViolation

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    calls = []

    def gate(_html, *, enabled, figures):
        calls.append(enabled)
        assert figures
        return SimpleNamespace(
            results=(),
            violations=(LintViolation(
                rule="external_link_dead", detail="DEAD: https://dead.gov"),),
            manual_checks=(), claim_warnings=(),
        )

    monkeypatch.setattr(runner, "run_client_link_gate", gate)
    assert _run(runner, monkeypatch, no_compose=True) == 2
    assert calls == [False]
    assert (reports /
            "testco.agency_dod.assessment.client.DO-NOT-SEND.html").exists()


def test_unverifiable_agency_link_writes_internal_sidecar(
        tmp_path, monkeypatch):
    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "run_client_link_gate",
        lambda *_args, **_kwargs: SimpleNamespace(
            results=(), violations=(),
            manual_checks=("https://manual.gov · Sources · offline",),
            claim_warnings=(),
        ),
    )

    assert _run(runner, monkeypatch, no_compose=True) == 0
    sidecar = (reports /
               "testco.agency_dod.assessment.client.link-integrity.internal.md")
    text = sidecar.read_text(encoding="utf-8")
    assert "## MANUAL LINK CHECK" in text
    assert "https://manual.gov" in text


def test_compose_timeout_still_writes_deterministic_report(
        tmp_path, monkeypatch, capsys):
    """2026-07-10 availability rule: prose timeout cannot remove Assess."""
    import agents.reports.capture_brief as capture

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)

    def timeout(*_args, **_kwargs):
        raise TimeoutError("composer exceeded deadline")

    monkeypatch.setattr(capture, "compose_capture_brief", timeout)
    assert _run(runner, monkeypatch) == 2

    out = reports / "testco.agency_dod.assessment.client.DO-NOT-SEND.html"
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "Federal Opportunity Assessment" in html
    assert "Agency focus: Department of Defense (DoD)" in html
    assert '<div class="thesis-block">' not in html
    assert "continuing with the deterministic agency assessment" in capsys.readouterr().err
    assert not (tmp_path / "Desktop" / "Testco").exists()


def test_unchanged_inputs_reuse_valid_content_cache(tmp_path, monkeypatch):
    """2026-07-10 retry rule: identical inputs never rebuy composition."""
    import agents.reports.capture_brief as capture

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    calls = []

    def compose(*_args, **_kwargs):
        calls.append(1)
        return _content(client_name="Testco")

    monkeypatch.setattr(capture, "compose_capture_brief", compose)
    assert _run(runner, monkeypatch) == 0
    assert len(calls) == 1
    cache = (tmp_path / "data" / "state" / "agency_report_content" /
             "testco.agency_dod.content.json")
    assert cache.exists()

    assert _run(runner, monkeypatch) == 0
    assert len(calls) == 1
    assert "thesis-block" in (
        reports / "testco.agency_dod.assessment.client.html"
    ).read_text(encoding="utf-8")


def test_changed_scoped_input_recomposes(tmp_path, monkeypatch):
    """2026-07-10 cache rule: any scoped evidence change invalidates prose."""
    import agents.reports.capture_brief as capture

    runner, _canonical_path, _reports = _prepare(tmp_path, monkeypatch)
    calls = []

    def compose(*_args, **_kwargs):
        calls.append(1)
        return _content(client_name="Testco")

    monkeypatch.setattr(capture, "compose_capture_brief", compose)
    assert _run(runner, monkeypatch) == 0
    cache_path = (tmp_path / "data" / "state" / "agency_report_content" /
                  "testco.agency_dod.content.json")
    first = json.loads(cache_path.read_text(encoding="utf-8"))["fingerprint"]

    scoped_path = (tmp_path / "data" / "cleaned" /
                   "searches_testco.agency_dod.json")
    artifact = json.loads(scoped_path.read_text(encoding="utf-8"))
    artifact["results"]["sam.gov"][0]["title"] = "Changed requirement title"
    scoped_path.write_text(json.dumps(artifact), encoding="utf-8")

    assert _run(runner, monkeypatch) == 0
    second = json.loads(cache_path.read_text(encoding="utf-8"))["fingerprint"]
    assert len(calls) == 2
    assert second != first


def test_atomic_report_failure_preserves_prior_html(tmp_path, monkeypatch):
    """2026-07-10 write rule: replacement failure cannot corrupt last good."""
    import agents.reports.capture_brief as capture

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    out = reports / "testco.agency_dod.assessment.client.html"
    out.write_text("LAST GOOD REPORT", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    monkeypatch.setattr(
        capture, "compose_capture_brief",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("--no-compose called the composer")),
    )
    assert _run(runner, monkeypatch, no_compose=True) == 2
    assert out.read_text(encoding="utf-8") == "LAST GOOD REPORT"
    assert not any(p.name.endswith(".tmp") for p in reports.iterdir())


def test_focus_only_artifact_survives_retry_and_reuses_cache(
        tmp_path, monkeypatch):
    """2026-07-10 filter-first rule: retry never requires an all-scope sweep."""
    import agents.reports.capture_brief as capture

    runner, canonical, _reports = _prepare(tmp_path, monkeypatch)
    focused = _searches(3)
    focused["search_scope"] = {
        "agencies": [{"abbr": "DoD", "name": "Department of Defense"}],
        "mode": "focus",
    }
    direct = (tmp_path / "data" / "cleaned" /
              "searches_testco.agency_dod.json")
    direct.write_text(json.dumps(focused), encoding="utf-8")
    canonical.unlink()
    calls = []

    def compose(*_args, **_kwargs):
        calls.append(1)
        return _content(client_name="Testco")

    monkeypatch.setattr(capture, "compose_capture_brief", compose)
    assert _run(runner, monkeypatch) == 0
    assert _run(runner, monkeypatch) == 0
    assert len(calls) == 1
    persisted = json.loads(direct.read_text(encoding="utf-8"))
    assert persisted["search_scope"]["mode"] == "focus"


def test_scoped_artifact_write_failure_still_renders(tmp_path, monkeypatch):
    """2026-07-10 persistence rule: in-memory scope remains renderable."""
    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    real_atomic = runner._atomic_write_text

    def fail_scoped(path, text):
        if path.endswith("searches_testco.agency_dod.json"):
            raise OSError("scoped persistence unavailable")
        return real_atomic(path, text)

    monkeypatch.setattr(runner, "_atomic_write_text", fail_scoped)
    assert _run(runner, monkeypatch, no_compose=True) == 0
    assert (reports / "testco.agency_dod.assessment.client.html").exists()


def test_market_refresh_failure_is_nonfatal(tmp_path, monkeypatch):
    """2026-07-10 enrichment rule: USAspending outage cannot remove Assess."""
    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "refresh_market_slice",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("market endpoint down")),
    )
    assert _run(runner, monkeypatch, no_compose=True) == 0
    assert (reports / "testco.agency_dod.assessment.client.html").exists()


@pytest.mark.parametrize(
    "failure", ["profile", "profile_missing", "fingerprint", "factpack"])
def test_optional_prose_setup_failure_writes_blocked_assessment(
        tmp_path, monkeypatch, failure):
    """2026-07-10 promotion rule: optional-prose degradation never releases."""
    import agents.reports.capture_brief as capture
    import agents.reports.facts as facts
    import tools.capability as capability

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(capture, "compose_capture_brief",
                        lambda *_args, **_kwargs: _content(client_name="Testco"))

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{failure} unavailable")

    if failure == "profile":
        monkeypatch.setattr(capability, "load_profile", fail)
    elif failure == "profile_missing":
        monkeypatch.setattr(capability, "load_profile", lambda _client: None)
    elif failure == "fingerprint":
        monkeypatch.setattr(runner, "_content_fingerprint", fail)
    else:
        monkeypatch.setattr(facts, "build_fact_pack", fail)

    assert _run(runner, monkeypatch) == 2
    out = reports / "testco.agency_dod.assessment.client.DO-NOT-SEND.html"
    assert out.exists()
    assert "Federal Opportunity Assessment" in out.read_text(encoding="utf-8")
    assert not (tmp_path / "Desktop" / "Testco").exists()


def test_prose_render_failure_retries_deterministically_and_invalidates_cache(
        tmp_path, monkeypatch):
    """2026-07-10 render rule: prose failure sheds prose, never the report."""
    import agents.reports.capture_brief as capture
    import agents.reports.views as views

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    real_render = views.render_assessment

    monkeypatch.setattr(capture, "compose_capture_brief",
                        lambda *_args, **_kwargs: _content(client_name="Testco"))

    def fail_prose(doc, *args, **kwargs):
        if doc.content is not None:
            raise RuntimeError("prose renderer failed")
        return real_render(doc, *args, **kwargs)

    monkeypatch.setattr(views, "render_assessment", fail_prose)
    assert _run(runner, monkeypatch) == 2
    out = reports / "testco.agency_dod.assessment.client.DO-NOT-SEND.html"
    assert out.exists()
    assert '<div class="thesis-block">' not in out.read_text(encoding="utf-8")
    cache = (tmp_path / "data" / "state" / "agency_report_content" /
             "testco.agency_dod.content.json")
    assert not cache.exists()


def test_deterministic_render_failure_emits_emergency_html(
        tmp_path, monkeypatch):
    """2026-07-10 final safety rule: renderer failure still leaves HTML."""
    import agents.reports.views as views

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        views, "render_assessment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("template unavailable")),
    )
    assert _run(runner, monkeypatch, no_compose=True) == 2
    out = reports / "testco.agency_dod.assessment.client.DO-NOT-SEND.html"
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "DETERMINISTIC SAFETY RENDER" in html
    assert "2 scoped notice records retained" in html


def test_lint_exception_becomes_blocking_violation(tmp_path, monkeypatch):
    """2026-07-10 QA rule: a broken lint cannot release or suppress HTML."""
    import agents.reports.lint as lint

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        lint, "lint_counts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("lint unavailable")),
    )
    assert _run(runner, monkeypatch, no_compose=True) == 2
    out = reports / "testco.agency_dod.assessment.client.DO-NOT-SEND.html"
    assert out.exists()
    assert "DETERMINISTIC SAFETY RENDER" not in out.read_text(encoding="utf-8")


def test_cached_prose_that_fails_qa_is_removed_and_shed(
        tmp_path, monkeypatch):
    """2026-07-10 cache rule: a QA-bad cache cannot pin every retry."""
    import agents.reports.capture_brief as capture
    import agents.reports.lint as lint

    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    calls = []

    def compose(*_args, **_kwargs):
        calls.append(1)
        return _content(client_name="Testco")

    monkeypatch.setattr(capture, "compose_capture_brief", compose)
    assert _run(runner, monkeypatch) == 0
    cache = (tmp_path / "data" / "state" / "agency_report_content" /
             "testco.agency_dod.content.json")
    assert cache.exists()

    clean = SimpleNamespace(violations=[])
    bad = SimpleNamespace(violations=[SimpleNamespace(
        rule="prose_test", detail="prose failed")])
    monkeypatch.setattr(
        lint, "lint_counts",
        lambda html: bad if '<div class="thesis-block">' in html else clean,
    )
    assert _run(runner, monkeypatch) == 2
    assert not cache.exists()
    out = reports / "testco.agency_dod.assessment.client.DO-NOT-SEND.html"
    assert '<div class="thesis-block">' not in out.read_text(encoding="utf-8")


def test_desktop_delivery_failure_is_nonfatal(tmp_path, monkeypatch):
    """2026-07-10 delivery rule: repo report success is the job result."""
    runner, _canonical, reports = _prepare(tmp_path, monkeypatch)
    real_atomic = runner._atomic_write_text

    def fail_desktop(path, text):
        if f"{os.sep}Desktop{os.sep}" in path:
            raise PermissionError("Desktop unavailable")
        return real_atomic(path, text)

    monkeypatch.setattr(runner, "_atomic_write_text", fail_desktop)
    assert _run(runner, monkeypatch, no_compose=True) == 0
    assert (reports / "testco.agency_dod.assessment.client.html").exists()


def test_explicit_searches_bypass_gate_artifact_resolution(monkeypatch):
    """2026-07-10 explicit-input rule: in-memory evidence is self-contained."""
    import agents.review as review
    from agents.reports.document import build_document

    monkeypatch.setattr(
        review, "sweep_artifact_path",
        lambda _client: (_ for _ in ()).throw(
            FileNotFoundError("unrelated current-gate artifact missing")),
    )
    doc = build_document("Testco", searches=_searches(2), qualify=None,
                         as_of=date(2026, 7, 10))
    assert doc.counts()["pursue_notices"] == 2


def test_explicit_web_monitor_never_becomes_sam_intelligence(
        monkeypatch):
    """2026-07-10 provenance rule: container location cannot mint SAM truth."""
    import tools.capability as capability
    from agents.reports.document import build_document
    from tools.snapshots import save_snapshot

    monkeypatch.setattr(capability, "load_profile", lambda _client: None)
    searches = _searches(0)
    searches["results"]["sam.gov"] = [{
        "source": "web", "source_id": "WEB-MONITOR", "title": "Trade lead",
        "agency": "GSA", "api_url": "https://example.com/lead",
    }]
    searches["results"]["triage"] = {
        "WEB-MONITOR": {"verdict": "monitor", "reason": "developing lead"}}
    searches["results"]["news"] = {"items": []}
    save_snapshot("sweep", "testco", [], as_of=date(2026, 7, 9))

    doc = build_document("Testco", searches=searches, qualify=None,
                         as_of=date(2026, 7, 10))
    assert doc.verdict_totals.get("monitor", 0) == 0
    assert doc.counts()["monitor_notices"] == 0
    assert all(e.id != "WEB-MONITOR" for e in doc.watchlist.entries)
    assert all(p.source_id != "WEB-MONITOR" for p in doc.partnering.plays)
