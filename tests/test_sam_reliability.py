"""SAM.gov reliability layer — quota ledger, cache fallback, fail-fast."""

from datetime import date

import httpx
import pytest

import tools.api.sam_gov as sg
import tools.api.sam_quota as sq
from tools.api.base import SourceQuery
from tools.api.sam_gov import SamGovSource

NOTICE = {"noticeId": "N-1", "title": "CTI Platform", "postedDate": "2026-06-01",
          "responseDeadLine": "2026-09-01T17:00:00-04:00"}
PAYLOAD = {"opportunitiesData": [NOTICE]}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # cache + ledger isolation comes from conftest; just kill the pacing sleep
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)


def _query(**over):
    base = dict(naics_codes=["541512"], posted_from=date(2026, 1, 1),
                posted_to=date(2026, 7, 1), deadline_from=date(2026, 7, 3),
                limit=25)
    base.update(over)
    return SourceQuery(**base)


def _rate_limit_error():
    req = httpx.Request("GET", sg.SEARCH_URL)
    resp = httpx.Response(429, request=req)
    return httpx.HTTPStatusError("429 too many", request=req, response=resp)


# ── quota ledger ──

def test_ledger_counts_and_resets(tmp_path, monkeypatch):
    assert sq.calls_today() == 0
    assert sq.note_call("search") == 1
    assert sq.note_call("entity") == 2
    assert sq.calls_today() == 2
    assert "2 SAM calls today" in sq.summary()
    assert "search 1" in sq.summary()
    # A stale (yesterday's) ledger resets on read.
    import os
    ledger = __import__("pathlib").Path(os.environ["LILA_SAM_LEDGER"])
    ledger.write_text('{"date": "2020-01-01", "count": 9, "by": {}}')
    assert sq.calls_today() == 0


def test_search_meters_every_live_call(monkeypatch):
    monkeypatch.setattr(sg, "get_json", lambda *a, **k: PAYLOAD)
    src = SamGovSource(api_key="k")
    src.search(_query(naics_codes=["541512", "513210"]))
    assert sq.calls_today() == 2  # one per pass


def test_healthcheck_shows_budget(monkeypatch):
    """The pool is PERMANENT. This used to assert "1,000/day", which is the
    role-upgrade figure: the message was advertising a ceiling that is not
    available, so the test was pinning the wrong promise in place."""
    sq.note_call("search")
    ok, detail = SamGovSource(api_key="k").healthcheck()
    assert ok and "1 SAM calls today" in detail
    assert "permanent" in detail
    assert "1,000/day with one" not in detail


# ── cache ──

def test_fresh_cache_spends_zero_quota(monkeypatch):
    calls = []
    monkeypatch.setattr(sg, "get_json", lambda *a, **k: calls.append(1) or PAYLOAD)
    src = SamGovSource(api_key="k")
    q = _query()
    assert len(src.search(q)) == 1
    assert len(calls) == 1 and sq.calls_today() == 1
    # Identical re-run inside the TTL: served from cache, no new call.
    out = src.search(q)
    assert len(out) == 1
    assert len(calls) == 1 and sq.calls_today() == 1
    assert src.last_status["fresh_cache"] == 1 and src.last_status["live"] == 0


def test_stale_cache_rescues_throttled_run(monkeypatch):
    # Seed the cache with a good run...
    monkeypatch.setattr(sg, "get_json", lambda *a, **k: PAYLOAD)
    src = SamGovSource(api_key="k")
    q = _query()
    src.search(q)
    # ...age it past the TTL, then throttle every live attempt.
    monkeypatch.setattr(sg, "FRESH_TTL_S", -1)

    def throttle(*a, **k):
        raise _rate_limit_error()

    monkeypatch.setattr(sg, "get_json", throttle)
    out = src.search(q)
    assert len(out) == 1  # yesterday's notices beat zero notices
    assert src.last_status["stale_cache"] == 1
    assert src.last_status["throttled"] is True


def test_throttled_run_stops_burning_attempts(monkeypatch):
    attempts = []

    def throttle(*a, **k):
        attempts.append(1)
        raise _rate_limit_error()

    monkeypatch.setattr(sg, "get_json", throttle)
    src = SamGovSource(api_key="k")
    with pytest.raises(RuntimeError, match="SAM passes failed"):
        src.search(_query(naics_codes=["541512", "513210", "541511", "541519"]))
    # First 429 flips the throttled flag; remaining passes never hit the API.
    assert len(attempts) == 1


def test_live_calls_use_long_timeout(monkeypatch):
    seen = {}

    def spy(url, *, params, timeout=None, **kw):
        seen["timeout"] = timeout
        return PAYLOAD

    monkeypatch.setattr(sg, "get_json", spy)
    SamGovSource(api_key="k").search(_query())
    assert seen["timeout"] == sg.SAM_TIMEOUT >= 60
