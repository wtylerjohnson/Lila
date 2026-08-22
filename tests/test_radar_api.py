"""Practice radar API + ticker taxonomy + honest states (2026-07-17).

Offline; tmp roots; Flask test client. The ticker renders only what the
pipeline already knows; suppressed counts are reported, never silent.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

flask = pytest.importorskip("flask")

import ui.lifecycle as lc  # noqa: E402
import ui.server as srv  # noqa: E402
import ui.lifecycle as _lc  # noqa: E402
from datetime import date as _date  # noqa: E402
from test_lifecycle_stages import (  # noqa: E402
    TODAY,
    _mk_content,
    _mk_press,
    _mk_profile,
    _mk_sweep,
    book,  # noqa: F401  (fixture reuse)
)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    """The API path takes no `today` parameter, so these tests aged in real
    time: the press fixtures crossed the 14-day freshness gate at midnight
    2026-07-31 and "shipped" silently became "refresh_due" with no code
    change (verified failing on pristine HEAD). Freeze the lifecycle clock
    at the module's own TODAY, exactly as the stage tests already inject."""

    class _FrozenDate(_date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(_lc, "date", _FrozenDate)


def test_ticker_taxonomy_from_fixtures(book):  # noqa: F811
    _mk_profile(book)
    # window_closing: kept notice with a deadline inside 60 days
    (book / "data" / "cleaned" / "searches_testco.json").write_text(
        json.dumps({"client": "Testco", "results": {
            "triage": {"N1": {"verdict": "pursue"},
                       "N2": {"verdict": "discard"}},
            "sam.gov": [
                {"source_id": "N1", "title": "Closing Window Notice",
                 "response_deadline": "2026-08-15"},
                {"source_id": "N2", "title": "Discarded",
                 "response_deadline": "2026-08-01"},
            ]}}), encoding="utf-8")
    # relevance_candidate: calibration FN row
    (book / "data" / "state" / "relevance" / "calibration.csv").write_text(
        "client,record_ref,title,engine_score,disagreement\n"
        "Testco,R1,Missed SBOM Notice,6,false_negative_candidate\n"
        "Testco,R2,Agreement Row,0,\n", encoding="utf-8")
    # recompete_event: calendar row ending inside the window
    rec = book / "data" / "state" / "recompete"
    rec.mkdir(parents=True, exist_ok=True)
    os.environ["LILA_RECOMPETE_DIR"] = str(rec)
    (rec / "testco.json").write_text(json.dumps({
        "generated": "2026-07-17", "attack": [
            {"award_id": "AW1", "pop_end": "2026-08-20",
             "description": "expiring seat"}], "defend": []}),
        encoding="utf-8")
    # disputed_figure + gate_failure: content flag + newest DO-NOT-SEND
    _mk_content(book)
    content_path = book / "clients" / "testco" / "signal_board_content.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["figures"][0]["disputed"] = True
    content_path.write_text(json.dumps(content), encoding="utf-8")
    _mk_press(book, gated=False, order=0)
    _mk_press(book, gated=True, order=10)

    try:
        cards = [lc.derive_stage("testco", today=TODAY)]
        feed = lc.ticker(cards, today=TODAY)
    finally:
        os.environ.pop("LILA_RECOMPETE_DIR", None)
    kinds = {it["kind"] for it in feed["items"]}
    assert kinds == {"relevance_candidate", "window_closing",
                     "recompete_event", "disputed_figure", "gate_failure"}
    assert all(it["client_name"] == "Testco" for it in feed["items"])
    texts = " ".join(it["text"] for it in feed["items"])
    assert "Closing Window Notice" in texts
    assert "Discarded" not in texts               # discards never tick
    assert "Missed SBOM Notice" in texts
    assert "Agreement Row" not in texts           # agreements never tick
    assert "AW1" in texts
    assert "disputed figure" in texts
    assert "DO-NOT-SEND" in texts
    assert feed["suppressed"] == 0


def test_radar_api_payload_and_empty_state(book):  # noqa: F811
    app = srv.app.test_client()
    empty = app.get("/api/radar").get_json()
    assert empty["cards"] == []
    assert empty["scoreboard"]["clients"] == 0
    assert empty["ticker"]["items"] == []

    _mk_profile(book)                              # valid profile only
    payload = app.get("/api/radar").get_json()
    assert payload["scoreboard"]["clients"] == 1
    card = payload["cards"][0]
    assert card["stage"] == "onboarding"
    assert card["sweep_ready"] is True             # affordance, not an error
    assert card["has_review_packet"] is False
    assert card["freshness"]["light"] == "red"     # unknown, honestly


def test_full_stage_card_over_api(book):  # noqa: F811
    _mk_profile(book)
    _mk_sweep(book)
    _mk_content(book)
    _mk_press(book, gated=False)
    app = srv.app.test_client()
    card = app.get("/api/radar").get_json()["cards"][0]
    assert card["stage"] == "shipped"
    assert card["headline_stat"] == "$1.00M"


def test_onboarding_stub_route_and_radar_backend_wiring(book):  # noqa: F811
    """Operator revert 2026-07-18: the home page is intentionally bare of
    radar UI pending the fable/home-rebuild merge (screenshots required
    before any home UI returns; see V4_PLAYBOOK V4.5). The radar backend
    stays fully live, so this test pins API behavior, not HTML presence,
    and guards against the band returning by accident."""
    app = srv.app.test_client()
    # 2026-07-24 declutter: the placeholder page is retired; the door just
    # goes home (the guided flow lives in the Control Room).
    stub = app.get("/onboarding")
    assert stub.status_code == 302
    assert stub.headers["Location"].endswith("/")
    home = app.get("/").data.decode()
    assert "+ New Client" in home          # C4: the one client-creation door
    assert "renderRadar" not in home       # the band is gone, deliberately
    assert "radar-cards" not in home
    r = app.get("/api/radar")
    assert r.status_code == 200
    payload = r.get_json()
    assert set(payload) >= {"cards", "scoreboard", "ticker"}
