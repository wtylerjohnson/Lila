"""Regression proof for the sole external-delivery transaction."""

from __future__ import annotations

from pathlib import Path

import pytest


pytest.importorskip("flask")

import ui.server as server
from agents.golden_press.press import _deliver_to_desktop


LEGACY_RUNNERS = (
    "run_capture_brief.py",
    "run_views.py",
    "run_agency_report.py",
    "run_target_report.py",
    "run_signal_board.py",
)


def test_only_lila_release_command_receives_release_authority():
    capture = server._step_cmd(
        "report", "Acme", {"kind": "capture_brief"})
    assert capture[1:] == ["run_capture_brief.py", "--client", "Acme"]
    assert "--release" not in capture
    assert "--pdf" not in capture

    digest = "a" * 64
    canonical = server._step_cmd(
        "lila_release", "Acme", {"_lila_target_set_sha256": digest})
    assert canonical[1:] == [
        "run_lila_release.py", "--client", "Acme", "--release",
        "--target-set-sha256", digest,
    ]


def test_legacy_runners_have_no_desktop_copy_seam():
    root = Path(server.ROOT)
    for relative in LEGACY_RUNNERS:
        source = (root / relative).read_text(encoding="utf-8")
        assert "shutil.copy" not in source, relative
        assert "~/Desktop/" not in source, relative


def test_legacy_golden_press_delivery_seam_is_internal_only(
        tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    report = tmp_path / "legacy.html"
    report.write_text("<html>legacy</html>", encoding="utf-8")
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))

    assert _deliver_to_desktop(
        report, client="Acme", certified=True) is None
    assert not desktop.exists()


@pytest.mark.parametrize("route", (
    "/client/acme/download/foa.html",
    "/client/acme/download/signal-board.html",
))
def test_compatibility_html_download_cannot_bypass_canonical_state(
        tmp_path, monkeypatch, route):
    legacy = tmp_path / "acme.federal_opportunity_assessment.html"
    legacy.write_text("<html>legacy release</html>", encoding="utf-8")
    monkeypatch.setattr(
        server, "release_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("compatibility route consulted legacy state")),
    )
    monkeypatch.setattr(
        server, "_current_lila_product_state",
        lambda _slug: {
            "releasable": False,
            "reason": "no canonical LILA release",
        },
    )

    response = server.app.test_client().get(route)
    assert response.status_code == 409
    assert response.get_json()["do_not_send"] is True
    assert "no canonical LILA release" in response.get_json()["error"]


@pytest.mark.parametrize("route", (
    "/client/acme/download/lila.html",
    "/client/acme/download/foa.html",
    "/client/acme/download/signal-board.html",
))
def test_all_public_client_html_routes_serve_the_same_canonical_artifact(
        tmp_path, monkeypatch, route):
    canonical = tmp_path / "Acme.LILA.CLIENT.html"
    canonical.write_bytes(b"<html>canonical LILA</html>")
    monkeypatch.setattr(
        server, "_current_lila_product_state",
        lambda _slug: {
            "releasable": True,
            "path": str(canonical),
            "bundle_path": str(tmp_path / "unused.zip"),
        },
    )
    monkeypatch.setattr(server, "_targeting_download_gate", lambda _slug: None)

    response = server.app.test_client().get(route)
    assert response.status_code == 200
    assert response.data == canonical.read_bytes()
    assert "Acme.LILA.CLIENT.html" in response.headers["Content-Disposition"]


def test_command_center_does_not_badge_internal_views_as_release_ready():
    source = (Path(server.ROOT) / "ui" / "static" /
              "command-center-home.js").read_text(encoding="utf-8")
    assert "detail.final_product && detail.final_product.qa_pass" in source
    assert "var report = detail.final_product || null" in source
    assert "doc.external_product && doc.qa_pass" in source
    assert '"Internal view"' in source
