"""Gated PDF export: gates run BEFORE Chrome, a failed gate produces no PDF,
siblings share the base name. Chrome's subprocess boundary is mocked for the
orchestration tests; one real-Chrome smoke runs when a binary is present."""

from __future__ import annotations

import os
import sys
from datetime import date
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.export_pdf as ep  # noqa: E402
from agents.reports.document import build_document  # noqa: E402
from agents.reports.link_integrity import (  # noqa: E402
    ClientLinkGateOutcome, LinkCheckResult, LinkClass,
)
from agents.reports.lint import LintViolation  # noqa: E402
from agents.reports.links import build_sam_notice_link  # noqa: E402
from agents.reports.views import render_assessment  # noqa: E402
from tests.test_assessment_document import _searches  # noqa: E402

AS_OF = date(2026, 7, 6)


def _clean_client_html() -> str:
    doc = build_document("Testco", searches=_searches(3), qualify=None, as_of=AS_OF)
    html = render_assessment(doc, "client")
    # This exporter-orchestration fixture uses synthetic record IDs (P1, M1)
    # that cannot be reconciled by the canonical SAM/USAspending builders.
    # Keep those unrelated fake records out of the clean path; construction
    # lint itself is asserted at the exporter boundary below.
    return (html
            .replace("https://sam.gov/", "https://records.example.gov/")
            .replace("https://www.usaspending.gov/award/",
                     "https://records.example.gov/award/"))


def _fake_chrome(record: list):
    """Stands in for subprocess.run: writes a fake PDF where Chrome would."""
    def _run(cmd, capture_output=True, timeout=0):
        record.append(cmd)
        target = next(a.split("=", 1)[1] for a in cmd
                      if a.startswith("--print-to-pdf="))
        with open(target, "wb") as f:
            f.write(b"%PDF-1.4 fake\n%%EOF")
        return SimpleNamespace(returncode=0, stderr=b"")
    return _run


# ---- binary resolution -----------------------------------------------------
def test_find_chrome_env_override_and_actionable_failure(tmp_path, monkeypatch):
    fake = tmp_path / "chrome-bin"
    fake.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setenv("LILA_CHROME", str(fake))
    assert ep.find_chrome() == str(fake)

    monkeypatch.delenv("LILA_CHROME")
    monkeypatch.setattr(ep.os.path, "exists", lambda p: False)
    monkeypatch.setattr(ep.shutil, "which", lambda name: None)
    with pytest.raises(ep.ChromeNotFound) as e:
        ep.find_chrome()
    # the message tells the user exactly what to do
    assert "LILA_CHROME" in str(e.value) and "Chromium" in str(e.value)


# ---- the hard rule: gates before Chrome, no PDF on failure -------------------
def test_gate_failure_produces_no_pdf(tmp_path, monkeypatch):
    bad = tmp_path / "broken.assessment.client.html"
    bad.write_text("<html><body><p>Nine pursuits identified.</p></body></html>",
                   encoding="utf-8")

    def _never(*a, **k):
        raise AssertionError("Chrome ran before the gates — the hard rule is broken")
    monkeypatch.setattr(ep.subprocess, "run", _never)

    pdf, violations = ep.export_deliverable(
        str(bad), link_checks_enabled=False)
    assert pdf is None and violations
    assert not list(tmp_path.glob("*.pdf")), "a gate failure must leave no PDF"


def test_free_string_federal_link_is_rejected_at_export_boundary(
        tmp_path, monkeypatch):
    bad = tmp_path / "raw-link.assessment.client.html"
    html = _clean_client_html().replace(
        "</body>",
        "<!-- copied https://sam.gov/opp/"
        "0123456789abcdef0123456789abcdef/view --></body>",
    )
    bad.write_text(html, encoding="utf-8")

    def _never(*_args, **_kwargs):
        raise AssertionError("Chrome ran despite a free-string federal link")

    monkeypatch.setattr(ep.subprocess, "run", _never)
    pdf, violations = ep.export_deliverable(
        str(bad), link_checks_enabled=False)
    assert pdf is None
    assert "federal_link_free_string" in {v.rule for v in violations}
    assert not list(tmp_path.glob("*.pdf"))


def test_do_not_send_stamp_refused_by_name(tmp_path, monkeypatch):
    # even CONTENT that passes every gate is refused when the file is stamped
    stamped = tmp_path / "x.assessment.client.DO-NOT-SEND.html"
    stamped.write_text(_clean_client_html(), encoding="utf-8")
    monkeypatch.setattr(ep.subprocess, "run", _fake_chrome([]))
    pdf, violations = ep.export_deliverable(
        str(stamped), link_checks_enabled=False)
    assert pdf is None
    assert violations[0].rule == "do_not_send_stamp"
    assert not list(tmp_path.glob("*.pdf"))


def test_internal_view_refused_on_self_label(tmp_path, monkeypatch):
    """The internal view says DO NOT SEND in its own eyebrow — the export
    boundary refuses it no matter who calls (ratified: internal never gets a
    send-around-shaped sibling)."""
    doc = build_document("Testco", searches=_searches(3), qualify=None, as_of=AS_OF)
    internal = tmp_path / "testco.assessment.internal.html"
    internal.write_text(render_assessment(doc, "internal"), encoding="utf-8")
    monkeypatch.setattr(ep.subprocess, "run", _fake_chrome([]))
    pdf, violations = ep.export_deliverable(
        str(internal), link_checks_enabled=False)
    assert pdf is None
    assert violations[0].rule == "do_not_send_content"
    assert not list(tmp_path.glob("*.pdf"))


# ---- clean export -----------------------------------------------------------
def test_clean_export_writes_nonzero_sibling(tmp_path, monkeypatch):
    html = tmp_path / "testco.assessment.client.html"
    html.write_text(_clean_client_html(), encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(ep.subprocess, "run", _fake_chrome(calls))

    pdf, violations = ep.export_deliverable(
        str(html), link_checks_enabled=False)
    assert violations == []
    assert pdf == str(tmp_path / "testco.assessment.client.pdf")  # same base name
    assert os.path.getsize(pdf) > 0
    # Chrome invoked exactly once, headless, without header/footer
    assert len(calls) == 1
    assert "--headless" in calls[0] and "--no-pdf-header-footer" in calls[0]


def test_dead_external_link_refuses_before_chrome(tmp_path, monkeypatch):
    html = tmp_path / "dead.assessment.client.html"
    url = "https://agency.example.gov/dead"
    html.write_text(
        f'<html><body><h2>Evidence</h2><a href="{url}">Source</a></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(ep, "run_gates", lambda _html: [])
    monkeypatch.setattr(
        "agents.reports.link_integrity.run_client_link_gate",
        lambda _html, **_kwargs: ClientLinkGateOutcome(
            results=(LinkCheckResult(
                url=url, classification=LinkClass.DEAD,
                sections=("Evidence",), proof="GET 404 terminal"),),
            violations=(LintViolation(
                rule="external_link_dead",
                detail=f"DEAD external link in section Evidence: {url}",
                excerpt=url,
            ),),
            manual_checks=(),
            claim_warnings=(),
        ),
    )

    def never_chrome(*_args, **_kwargs):
        raise AssertionError("Chrome ran despite a DEAD external link")

    monkeypatch.setattr(ep.subprocess, "run", never_chrome)
    pdf, violations = ep.export_deliverable(str(html))

    assert pdf is None
    assert [violation.rule for violation in violations] == [
        "external_link_dead"]
    assert not list(tmp_path.glob("*.pdf"))


def test_rendered_federal_attributes_cannot_attest_without_manifest(
        tmp_path, monkeypatch):
    html = tmp_path / "forged.assessment.client.html"
    link = build_sam_notice_link(
        "0123456789abcdef0123456789abcdef", reconciled=True)
    html.write_text(
        f'<a href="{link.url}" data-link-builder="{link.builder}" '
        f'data-link-record="{link.record_id}" '
        'data-link-reconciled="1">notice</a>',
        encoding="utf-8",
    )
    monkeypatch.setattr(ep, "run_gates", lambda _html: [])
    monkeypatch.setattr(
        ep.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("Chrome ran without a manifest"),
    )

    pdf, violations = ep.export_deliverable(
        str(html), link_checks_enabled=False)

    assert pdf is None
    assert [violation.rule for violation in violations] == [
        "federal_link_record_blocked"]


def test_unverifiable_link_allows_pdf_and_writes_internal_sidecar(
        tmp_path, monkeypatch):
    html = tmp_path / "manual.assessment.client.html"
    url = "https://agency.example.gov/manual"
    html.write_text(
        f'<html><body><h2>Evidence</h2><a href="{url}">Source</a></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(ep, "run_gates", lambda _html: [])
    monkeypatch.setattr(
        "agents.reports.link_integrity.run_client_link_gate",
        lambda _html, **_kwargs: ClientLinkGateOutcome(
            results=(LinkCheckResult(
                url=url, classification=LinkClass.UNVERIFIABLE,
                sections=("Evidence",), proof="GET 403 cannot verify"),),
            violations=(),
            manual_checks=(
                f"{url} · Evidence · GET 403 cannot verify",
            ),
            claim_warnings=(),
        ),
    )
    calls: list = []
    monkeypatch.setattr(ep.subprocess, "run", _fake_chrome(calls))

    pdf, violations = ep.export_deliverable(str(html))

    assert pdf is not None and violations == []
    assert len(calls) == 1
    sidecar = tmp_path / "manual.assessment.client.link-integrity.internal.md"
    rendered = sidecar.read_text(encoding="utf-8")
    assert "## MANUAL LINK CHECK" in rendered
    assert url in rendered


def test_export_pdf_raises_on_chrome_failure(tmp_path, monkeypatch):
    html = tmp_path / "x.html"
    html.write_text("<html></html>", encoding="utf-8")

    def _broken(cmd, capture_output=True, timeout=0):
        return SimpleNamespace(returncode=1, stderr=b"boom")
    monkeypatch.setattr(ep.subprocess, "run", _broken)
    with pytest.raises(RuntimeError, match="boom"):
        ep.export_pdf(str(html), chrome="/fake/chrome")
    assert not list(tmp_path.glob("*.pdf"))


# ---- real Chrome smoke (skipped only when no binary exists) -------------------
def test_real_chrome_renders_nonzero_pdf(tmp_path):
    try:
        chrome = ep.find_chrome()
    except ep.ChromeNotFound:
        pytest.skip("no Chrome/Chromium on this machine")
    html = tmp_path / "smoke.assessment.client.html"
    html.write_text(_clean_client_html(), encoding="utf-8")
    pdf, violations = ep.export_deliverable(
        str(html), chrome=chrome, link_checks_enabled=False)
    assert violations == [] and pdf is not None
    assert os.path.getsize(pdf) > 1000
    with open(pdf, "rb") as f:
        assert f.read(5) == b"%PDF-"
