"""The legacy golden press never crosses the external-delivery boundary."""

from __future__ import annotations

from pathlib import Path

from agents.golden_press.press import _deliver_to_desktop


def _report(tmp_path: Path) -> Path:
    html = tmp_path / "riverbed.golden_report.html"
    html.write_text("<html><body>report</body></html>", encoding="utf-8")
    return html


def test_certified_legacy_press_stays_internal(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))
    out = _deliver_to_desktop(_report(tmp_path), client="Riverbed", certified=True)
    assert out is None
    assert not desktop.exists()


def test_delivery_failure_never_costs_the_caller_the_report(tmp_path, monkeypatch):
    # a file where the client folder must go: mkdir raises, delivery degrades
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "Riverbed").write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))
    assert _deliver_to_desktop(_report(tmp_path), client="Riverbed",
                               certified=True) is None


def test_repeat_legacy_press_still_creates_no_external_artifact(
        tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))
    html = _report(tmp_path)
    first = _deliver_to_desktop(html, client="Riverbed", certified=True)
    html.write_text("<html><body>second press</body></html>", encoding="utf-8")
    second = _deliver_to_desktop(html, client="Riverbed", certified=True)
    assert first is None and second is None
    assert not desktop.exists()


def test_uncertified_press_never_reaches_the_desktop(tmp_path, monkeypatch):
    """Uncertified compatibility output also stays internal."""
    from agents.golden_press.press import _deliver_to_desktop
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(tmp_path))
    monkeypatch.delenv("LILA_PRESS_HOLD_DELIVERY", raising=False)
    html = tmp_path / "r.html"
    html.write_text("<html></html>", encoding="utf-8")
    out = _deliver_to_desktop(html, client="Acme", certified=False)
    assert out is None
    assert list(tmp_path.glob("Acme/*")) == []


def test_held_delivery_promotes_nothing_even_certified(tmp_path, monkeypatch):
    from agents.golden_press.press import _deliver_to_desktop
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(tmp_path))
    monkeypatch.setenv("LILA_PRESS_HOLD_DELIVERY", "1")
    html = tmp_path / "r.html"
    html.write_text("<html></html>", encoding="utf-8")
    assert _deliver_to_desktop(html, client="Acme", certified=True) is None
    assert list(tmp_path.glob("Acme/*")) == []
