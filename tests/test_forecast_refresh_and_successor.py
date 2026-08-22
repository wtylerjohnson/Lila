"""A8 forecast refresh runner, va_fco receipted wall, A9 successor watch.

Offline doctrine: adapters and HTTP are injected or monkeypatched, stores
write to tmp dirs (LILA_FORECAST_STORE_DIR seam for the change store),
notice store is an in-memory sqlite. The A9 pin: found=false is a STORED
answer with its queries, never an omission.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from agents.schemas import ForecastRecord
from tools import forecast_refresh, successor_watch
from tools.api.forecasts import va_fco


def test_va_fco_pull_is_a_receipted_wall_never_a_clean_zero(monkeypatch):
    monkeypatch.setattr(va_fco._http, "get_text",
                        lambda url, **kw: "<html>form</html>")
    adapter = va_fco.VaFcoForecast()
    records = adapter.forecasts(None)
    assert records == []
    prov = adapter.last_provenance
    assert prov["status"] == "failed"
    assert prov["mode"] == "login_walled"
    assert prov["form_reachable_anonymously"] is True
    assert prov["measured"] == "2026-08-18"
    assert any("Login.aspx" in r or "login" in r for r in prov["receipts"])


def test_refresh_stores_records_and_receipts_blocked_sources(
        monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_STORE_DIR", str(tmp_path))
    ledger = tmp_path / "coverage.json"
    monkeypatch.setattr("tools.api.forecasts.coverage._path", lambda: ledger)

    rec = ForecastRecord(
        source="fixture_src", source_id="F-1",
        title="Insider threat monitoring platform",
        description="data loss prevention and insider risk",
        agency="HHS", component="IHS",
        estimated_value_range=">= $700K and < $1.5M",
        anticipated_solicitation="FY27 Q1",
        naics_code="541519",
        url="https://example.gov/f1",
        retrieved_at=datetime.now(timezone.utc),
    )

    class _Good:
        name = "fixture_src"
        def forecasts(self, query):
            assert "Varonis" in (query.keywords or [])
            return [rec]

    class _Walled:
        name = "fixture_walled"
        last_provenance = None
        def forecasts(self, query):
            self.last_provenance = {"status": "failed",
                                    "mode": "login_walled",
                                    "receipts": ["bounced to login"]}
            return []

    class _Registry:
        def __init__(self):
            self._by = {"fixture_src": _Good(), "fixture_walled": _Walled()}
        def get(self, name):
            return self._by[name]

    monkeypatch.setattr(forecast_refresh, "REGISTRY", _Registry())
    out = forecast_refresh.refresh(
        ["fixture_src", "fixture_walled"], client_slug="varonis")

    assert out["fixture_src"]["status"] == "stored"
    assert out["fixture_src"]["fetched"] == 1
    stored = json.loads((tmp_path / "fixture_src.json").read_text())
    assert stored  # change store materialized
    assert out["fixture_walled"]["status"] == "blocked"
    assert out["fixture_walled"]["provenance"]["mode"] == "login_walled"


def _notice_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE notices (notice_id TEXT, title TEXT, notice_type TEXT,"
        " agency TEXT, subtier TEXT, office TEXT, posted TEXT, deadline TEXT,"
        " sol_number TEXT, set_aside TEXT, naics TEXT,"
        " description_prefix TEXT)")
    conn.executemany("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     rows)
    return conn


def _forecast_store(tmp_path, rows):
    d = tmp_path / "forecast_store"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fixture_gateway.json").write_text(json.dumps(rows),
                                            encoding="utf-8")
    return d


def test_successor_watch_stores_the_negative_with_its_queries(tmp_path):
    path, payload = successor_watch.run_watch(
        "varonis", "36C10B26Q0418", after="2026-07-01",
        post=lambda body: {"results": []},
        conn=_notice_db([]), out_dir=tmp_path,
        forecast_store_dir=tmp_path / "empty")
    assert path.exists()
    disk = json.loads(path.read_text())
    assert disk["found"] is False
    assert disk["state"] == "dark"
    assert disk["award_matches"] == [] and disk["notice_matches"] == []
    assert disk["forecast_matches"] == []
    assert len(disk["queries_run"]) == 4
    assert disk["window"]["after"] == "2026-07-01"


def test_watch_subscribes_to_forecast_rows_and_reports_forming(tmp_path):
    # Item 1 (operator review, 2026-08-18): the requirement visible only as
    # a forecast line binds to the watch; the state is "forming" and the
    # join lives in the pipeline, not in a human sentence.
    store_dir = _forecast_store(tmp_path, {
        "acq-41559": {
            "source_id": "acq-41559",
            "title": "Data Loss Prevention (DLP) Infrastructure Solution "
                     "- New Requirement",
            "description": "",
            "agency": "Department of Veterans Affairs", "component": "",
            "estimated_value_range": "$50M - $99M",
            "anticipated_solicitation": None, "anticipated_award": None,
            "incumbent_stated": None, "predecessor_contract_id": None,
            "first_seen": "2026-08-01", "last_seen": "2026-08-18",
            "url": "https://acquisitiongateway.gov/forecast/resources/41559",
        },
        "acq-gone": {
            "source_id": "acq-gone",
            "title": "Data loss prevention tooling refresh",
            "description": "", "agency": "Department of Veterans Affairs",
            "component": "", "estimated_value_range": "$1M - $1.9M",
            "anticipated_solicitation": "2026-05-01",
            "anticipated_award": None, "incumbent_stated": None,
            "predecessor_contract_id": "36C10B26Q0418",
            "first_seen": "2026-07-01", "last_seen": "2026-08-01",
            "url": "https://acquisitiongateway.gov/forecast/resources/x",
        },
        "acq-mid": {
            "source_id": "acq-mid",
            "title": "Unrelated line proving the middle pull",
            "description": "", "agency": "GSA", "component": "",
            "estimated_value_range": None,
            "anticipated_solicitation": None, "anticipated_award": None,
            "incumbent_stated": None, "predecessor_contract_id": None,
            "first_seen": "2026-08-01", "last_seen": "2026-08-10",
            "url": "https://example.gov/mid",
        },
    })
    path, payload = successor_watch.run_watch(
        "varonis", "36C10B26Q0418", after="2026-07-01",
        post=lambda body: {"results": []},
        conn=_notice_db([]), out_dir=tmp_path,
        forecast_store_dir=store_dir)
    assert payload["state"] == "forming"
    assert payload["found"] is False  # found still means notice or award
    by_id = {f["source_id"]: f for f in payload["forecast_matches"]}
    assert by_id["acq-41559"]["why"] == "forecast row (agency+term)"
    assert by_id["acq-41559"]["window_state"] == "unstated"
    bound = by_id["acq-gone"]
    assert bound["why"] == "forecast row (predecessor_contract_id)"
    assert bound["gone_from_latest_pull"] is True
    # DEBOUNCED fire: absent from TWO successful pulls (stamp dates
    # 2026-08-10 and 2026-08-18 both postdate the row's last_seen)
    assert bound["absent_from_successful_pulls"] == 2
    assert "two consecutive successful pulls" in bound["fire_signal"]
    assert bound["window_state"] == "stated-past"


def test_fire_signal_debounce_needs_two_pulls_and_a_healthy_ledger(
        tmp_path, monkeypatch):
    # Operator follow-up (2026-08-18): disappearance also happens when a
    # source re-keys rows or a pull truncates. One missed pull never
    # fires, and a failed latest ledger pull never fires.
    def rows(gone_last_seen):
        return {
            "gone": {
                "source_id": "gone", "title": "DLP successor line",
                "description": "",
                "agency": "Department of Veterans Affairs", "component": "",
                "estimated_value_range": None,
                "anticipated_solicitation": None, "anticipated_award": None,
                "incumbent_stated": None,
                "predecessor_contract_id": "36C10B26Q0418",
                "first_seen": "2026-07-01", "last_seen": gone_last_seen,
                "url": "https://example.gov/gone"},
            "fresh": {
                "source_id": "fresh", "title": "Unrelated fresh line",
                "description": "", "agency": "GSA", "component": "",
                "estimated_value_range": None,
                "anticipated_solicitation": None, "anticipated_award": None,
                "incumbent_stated": None, "predecessor_contract_id": None,
                "first_seen": "2026-08-01", "last_seen": "2026-08-18",
                "url": "https://example.gov/fresh"},
        }

    # one missed successful pull: gone_from_latest_pull, but NO fire
    one = _forecast_store(tmp_path / "one", rows("2026-08-10"))
    matches = successor_watch.forecast_matches(
        "36C10B26Q0418", title_term="data loss prevention",
        agency_like="VETERANS", store_dir=one)
    bound = {m["source_id"]: m for m in matches}["gone"]
    assert bound["gone_from_latest_pull"] is True
    assert bound["absent_from_successful_pulls"] == 1
    assert "fire_signal" not in bound

    # two missed pulls but the source's latest ledger pull FAILED: no fire
    two = _forecast_store(tmp_path / "two", {
        **rows("2026-08-01"),
        "mid": {"source_id": "mid", "title": "Middle pull witness",
                "description": "", "agency": "GSA", "component": "",
                "estimated_value_range": None,
                "anticipated_solicitation": None, "anticipated_award": None,
                "incumbent_stated": None, "predecessor_contract_id": None,
                "first_seen": "2026-08-01", "last_seen": "2026-08-10",
                "url": "https://example.gov/mid"}})
    monkeypatch.setattr(
        "tools.api.forecasts.coverage._load",
        lambda: {"fixture_gateway": {"last_pull": {"ok": False}}})
    matches = successor_watch.forecast_matches(
        "36C10B26Q0418", title_term="data loss prevention",
        agency_like="VETERANS", store_dir=two)
    bound = {m["source_id"]: m for m in matches}["gone"]
    assert bound["absent_from_successful_pulls"] == 2
    assert "fire_signal" not in bound

    # same store, healthy ledger: the debounced fire signal fires
    monkeypatch.setattr(
        "tools.api.forecasts.coverage._load",
        lambda: {"fixture_gateway": {"last_pull": {"ok": True}}})
    matches = successor_watch.forecast_matches(
        "36C10B26Q0418", title_term="data loss prevention",
        agency_like="VETERANS", store_dir=two)
    bound = {m["source_id"]: m for m in matches}["gone"]
    assert "two consecutive successful pulls" in bound["fire_signal"]


def test_watch_state_open_beats_forming(tmp_path):
    store_dir = _forecast_store(tmp_path, {
        "acq-41559": {
            "source_id": "acq-41559",
            "title": "Data Loss Prevention Infrastructure",
            "description": "", "agency": "Department of Veterans Affairs",
            "component": "", "estimated_value_range": "$50M - $99M",
            "anticipated_solicitation": None, "anticipated_award": None,
            "incumbent_stated": None, "predecessor_contract_id": None,
            "first_seen": "2026-08-01", "last_seen": "2026-08-18",
            "url": "https://example.gov",
        },
    })
    conn = _notice_db([
        ("n-2", "Data Loss Prevention Infrastructure Solution",
         "Solicitation", "VETERANS AFFAIRS, DEPARTMENT OF", "VA", "TAC",
         "2026-08-20", "2026-09-20", "36C10B26R0001", "", "541519",
         "the successor posting")])
    _path, payload = successor_watch.run_watch(
        "varonis", "36C10B26Q0418", after="2026-07-01",
        post=lambda body: {"results": []},
        conn=conn, out_dir=tmp_path, forecast_store_dir=store_dir)
    assert payload["state"] == "open"
    assert payload["found"] is True
    assert payload["forecast_matches"]  # the binding stays visible


def test_successor_watch_finds_follow_on_notice_and_award(tmp_path):
    award_page = {"results": [{
        "Award ID": "36C10B26N0099", "Recipient Name": "NEWCO LLC",
        "Description": "DATA LOSS PREVENTION FOLLOW-ON TO 36C10B26Q0418",
        "Award Amount": 900000.0, "Base Obligation Date": "2026-07-20",
        "Awarding Agency": "Department of Veterans Affairs",
        "Awarding Sub Agency": "VA OIT",
        "generated_internal_id": "CONT_AWD_X"}]}
    calls = iter([award_page, {"results": []}])
    conn = _notice_db([
        ("n-1", "Data Loss Prevention Solution - Rerelease", "Solicitation",
         "VETERANS AFFAIRS, DEPARTMENT OF", "VA", "TAC", "2026-07-15",
         "2026-09-01", "36C10B26Q0418A", "", "541519", "successor to "
         "36C10B26Q0418")])
    path, payload = successor_watch.run_watch(
        "varonis", "36C10B26Q0418", after="2026-07-01",
        post=lambda body: next(calls), conn=conn, out_dir=tmp_path,
        forecast_store_dir=tmp_path / "empty")
    assert payload["state"] == "awarded"
    assert payload["found"] is True
    assert payload["award_matches"][0]["award_id"] == "36C10B26N0099"
    assert payload["notice_matches"][0]["notice_id"] == "n-1"
    assert payload["notice_matches"][0]["sol_number"] == "36C10B26Q0418A"


def test_frame_terms_ride_the_a1_frame():
    terms = forecast_refresh.frame_terms("varonis")
    assert "Varonis" in terms
    assert "Forcepoint" in terms
    assert "data loss prevention" in terms
    assert "zero trust" not in terms  # tier 3 stays context, never a probe
