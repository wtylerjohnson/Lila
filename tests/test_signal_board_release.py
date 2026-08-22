"""Hash-bound Signal Board release certification."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from agents.reports import signal_board_release as cert
from agents.reports.links import build_sam_notice_link


CLIENT = "Testco"
NOW = datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)
GUID = "0123456789abcdef0123456789abcdef"
LINTS = {
    name: {"ok": True, "violations": []}
    for name in (
        "signal_board", "sam_workspace_links",
        "federal_link_construction", "whitelabel",
        "client_bleed", "emdash",
    )
}


def _html(client: str = CLIENT) -> str:
    link = build_sam_notice_link(GUID)
    return (
        f"<!doctype html><title>{client} · Federal Opportunity Pre-Assessment</title>"
        f'<a href="{link.url}" data-link-builder="{link.builder}" '
        f'data-link-record="{link.record_id}" '
        'data-link-reconciled="1">SAM</a>'
    )


def _sidecar(client: str = CLIENT, verdict: str = cert._CLEAN) -> str:
    from agents.reports.signal_board_presentation import presentation_digest

    presentation_sha256 = presentation_digest(
        client, root=Path("/__lila_absent_presentation__"))
    return (
        f"# INTERNAL · {client} · Signal Board press trail\n\n"
        "## Presentation snapshot\n"
        f"- SHA256 {presentation_sha256}\n\n"
        "## Gate verdict\n"
        f"{verdict}\n"
    )


@pytest.fixture
def mocked_lints(monkeypatch):
    monkeypatch.setattr(cert, "_exact_client_name", lambda client: client)
    monkeypatch.setattr(cert, "_static_lint_results",
                        lambda html, client: dict(LINTS))
    monkeypatch.setattr(cert, "_strict_assess_run_id",
                        lambda client: "run-current")


def test_clean_certificate_binds_exact_bytes_run_and_federal_occurrences(
        tmp_path, mocked_lints):
    html = _html()
    (tmp_path / "testco.federal_opportunity_signals.html").write_text(
        html, encoding="utf-8")
    (tmp_path / "testco.federal_opportunity_signals.internal.md").write_text(
        _sidecar(), encoding="utf-8")

    path = cert.certify_signal_board(
        CLIENT, report_dir=tmp_path, press_timestamp=NOW)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["client_name"] == CLIENT
    assert payload["presentation_name"] == CLIENT
    assert payload["schema_version"] == 2
    assert payload["lint_contract_version"] == 1
    assert payload["slug"] == "testco"
    assert payload["state"] == "release"
    assert payload["release_eligible"] is True
    assert payload["gate_verdict"] == cert._CLEAN
    assert payload["assess_run_id"] == "run-current"
    assert payload["html_sha256"] == hashlib.sha256(
        html.encode("utf-8")).hexdigest()
    assert payload["presentation_sha256"] == cert._presentation_snapshot(
        _sidecar())
    link = build_sam_notice_link(GUID)
    assert payload["federal_link_manifest"] == [{
        "url": link.url,
        "builder": link.builder,
        "record_id": link.record_id,
        "reconciled": True,
    }]


def test_do_not_send_gets_hash_bound_negative_certificate(
        tmp_path, mocked_lints, monkeypatch):
    failed = dict(LINTS)
    failed["signal_board"] = {"ok": False, "violations": ["blocked"]}
    monkeypatch.setattr(cert, "_static_lint_results",
                        lambda html, client: failed)
    html = _html().replace('data-link-reconciled="1"',
                           'data-link-reconciled="0"')
    (tmp_path / "testco.federal_opportunity_signals.DO-NOT-SEND.html").write_text(
        html, encoding="utf-8")
    (tmp_path / "testco.federal_opportunity_signals.internal.md").write_text(
        _sidecar(verdict=cert._BLOCKED), encoding="utf-8")

    payload = json.loads(cert.certify_signal_board(
        CLIENT, report_dir=tmp_path, press_timestamp=NOW).read_text())
    assert payload["state"] == "do_not_send"
    assert payload["release_eligible"] is False
    assert payload["static_lints"]["signal_board"]["ok"] is False
    assert payload["federal_link_manifest"][0]["reconciled"] is False


def test_clean_independent_lint_failure_writes_no_certificate(
        tmp_path, mocked_lints, monkeypatch):
    failed = dict(LINTS)
    failed["emdash"] = {"ok": False, "violations": ["emdash"]}
    monkeypatch.setattr(cert, "_static_lint_results",
                        lambda html, client: failed)
    (tmp_path / "testco.federal_opportunity_signals.html").write_text(
        _html(), encoding="utf-8")
    (tmp_path / "testco.federal_opportunity_signals.internal.md").write_text(
        _sidecar(), encoding="utf-8")

    with pytest.raises(ValueError, match="independent release lint"):
        cert.certify_signal_board(
            CLIENT, report_dir=tmp_path, press_timestamp=NOW)
    assert not (tmp_path / "testco.federal_opportunity_signals.qa.json").exists()


def test_presentation_change_between_render_and_certification_fails_closed(
        tmp_path, mocked_lints):
    from agents.reports.signal_board_presentation import store_logo_override
    from tests.test_signal_board_presentation import _png

    (tmp_path / "testco.federal_opportunity_signals.html").write_text(
        _html(), encoding="utf-8")
    (tmp_path / "testco.federal_opportunity_signals.internal.md").write_text(
        _sidecar(), encoding="utf-8")
    store_logo_override(
        CLIENT, kind="client", label=CLIENT, raw=_png(), root=tmp_path,
        updated_at=NOW)

    with pytest.raises(ValueError, match="presentation marks changed"):
        cert.certify_signal_board(
            CLIENT, report_dir=tmp_path, press_timestamp=NOW)
    assert not (tmp_path / "testco.federal_opportunity_signals.qa.json").exists()


@pytest.mark.parametrize("sidecar,html", [
    (_sidecar("Wrong"), _html()),
    (_sidecar() + "\n## Gate verdict\n- CLEAN (client-final)\n", _html()),
    (f"# INTERNAL · {CLIENT} · Signal Board press trail\n",
     _html()),
    (_sidecar(), _html("Wrong")),
])
def test_malformed_verdict_or_wrong_title_fails_closed(
        tmp_path, mocked_lints, sidecar, html):
    (tmp_path / "testco.federal_opportunity_signals.html").write_text(
        html, encoding="utf-8")
    (tmp_path / "testco.federal_opportunity_signals.internal.md").write_text(
        sidecar, encoding="utf-8")
    with pytest.raises(ValueError):
        cert.certify_signal_board(
            CLIENT, report_dir=tmp_path, press_timestamp=NOW)
    assert not (tmp_path / "testco.federal_opportunity_signals.qa.json").exists()


def test_cli_slug_header_can_resolve_to_exact_display_client(
        tmp_path, mocked_lints, monkeypatch):
    monkeypatch.setattr(cert, "_exact_client_name",
                        lambda client: "Riverbed")
    (tmp_path / "riverbed.federal_opportunity_signals.html").write_text(
        _html("Riverbed"), encoding="utf-8")
    (tmp_path / "riverbed.federal_opportunity_signals.internal.md").write_text(
        _sidecar("riverbed"), encoding="utf-8")

    payload = json.loads(cert.certify_signal_board(
        "riverbed", report_dir=tmp_path, press_timestamp=NOW).read_text())
    assert payload["client_name"] == "Riverbed"
    assert payload["presentation_name"] == "Riverbed"
    assert payload["slug"] == "riverbed"


def test_certificate_keeps_exact_identity_while_title_uses_presentation_name(
        tmp_path, mocked_lints, monkeypatch):
    monkeypatch.setattr(cert, "_exact_client_name", lambda _client: "mark43")
    monkeypatch.setattr(
        cert, "_presentation_client_name", lambda _client: "Mark43")
    html = _html("Mark43")
    (tmp_path / "mark43.federal_opportunity_signals.html").write_text(
        html, encoding="utf-8")
    (tmp_path / "mark43.federal_opportunity_signals.internal.md").write_text(
        _sidecar("mark43"), encoding="utf-8")

    payload = json.loads(cert.certify_signal_board(
        "mark43", report_dir=tmp_path, press_timestamp=NOW).read_text())

    assert payload["client_name"] == "mark43"
    assert payload["presentation_name"] == "Mark43"
    assert payload["slug"] == "mark43"
    assert payload["html_sha256"] == hashlib.sha256(
        html.encode("utf-8")).hexdigest()

    (tmp_path / "mark43.federal_opportunity_signals.html").write_text(
        _html("mark43"), encoding="utf-8")
    with pytest.raises(ValueError, match="title does not match"):
        cert.certify_signal_board(
            "mark43", report_dir=tmp_path, press_timestamp=NOW)
