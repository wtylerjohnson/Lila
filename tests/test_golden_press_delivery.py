"""Desktop delivery of the golden press, and its gate.

The golden press became the one deliverable but never delivered: the newest
file in the operator's client folder was a 2026-07-21 legacy Signal Board
press, so every golden press after that landed only under data/state and the
operator never saw it. Delivery mirrors run_signal_board.py's convention, and
CONTRACT_SURFACES requires the gate: an uncertified artifact never reaches the
client folder.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agents.golden_press.press import _deliver_to_desktop


def _report(tmp_path: Path) -> Path:
    html = tmp_path / "riverbed.golden_report.html"
    html.write_text("<html><body>report</body></html>", encoding="utf-8")
    return html


def test_certified_press_lands_in_the_client_folder(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))
    out = _deliver_to_desktop(_report(tmp_path), client="Riverbed", certified=True)
    assert out is not None
    delivered = Path(out)
    assert delivered.is_file()
    assert delivered.parent == desktop / "Riverbed"
    assert delivered.name == (
        f"Riverbed · Federal Opportunity Pre-Assessment · {date.today().isoformat()}.html")
    assert delivered.read_text(encoding="utf-8") == "<html><body>report</body></html>"


def test_delivery_failure_never_costs_the_caller_the_report(tmp_path, monkeypatch):
    # a file where the client folder must go: mkdir raises, delivery degrades
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "Riverbed").write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))
    assert _deliver_to_desktop(_report(tmp_path), client="Riverbed",
                               certified=True) is None


def test_repeat_press_on_the_same_day_overwrites_rather_than_duplicates(
        tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop))
    html = _report(tmp_path)
    first = _deliver_to_desktop(html, client="Riverbed", certified=True)
    html.write_text("<html><body>second press</body></html>", encoding="utf-8")
    second = _deliver_to_desktop(html, client="Riverbed", certified=True)
    assert first == second
    assert len(list((desktop / "Riverbed").glob("*.html"))) == 1
    assert Path(second).read_text(encoding="utf-8") == "<html><body>second press</body></html>"


def test_uncertified_press_never_reaches_the_desktop(tmp_path, monkeypatch):
    """FAIL-CLOSED PROMOTION (2026-08-03, supersedes the 2026-07-30
    draft-parking ruling): an uncertified press stays in data/state; the
    Desktop only ever receives a fully gated, certified artifact."""
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
