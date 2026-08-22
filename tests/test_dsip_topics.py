"""Recorded contracts for the official DSIP PROGRAM-tier adapter."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import tools.api.dsip_topics as dsip
import tools.api.program_cache as program_cache
from tools.api.base import SourceKind, SourceQuery
from tools.api.dsip_topics import DsipTopicsSource

FIXTURES = Path(__file__).parent / "fixtures"
FIXED_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fixed_clock(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(program_cache, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(dsip, "_today", lambda: date(2026, 7, 20))
    monkeypatch.setattr(dsip, "DETAIL_DELAY_S", 0)
    monkeypatch.setenv("LILA_DSIP_CACHE_DIR", str(tmp_path))


def _recorded_api(monkeypatch, *, paginate: bool = False) -> list[dict]:
    listing = _fixture("dsip_topics_search.json")
    details = _fixture("dsip_topic_details.json")
    calls: list[dict] = []

    def fake_get(url, *, params=None, **kwargs):
        calls.append({"url": url, "params": params, "headers": kwargs.get("headers")})
        if url == dsip.STATUS_URL:
            return listing["statuses"]
        if url == dsip.SEARCH_URL:
            if paginate:
                page = int(params["page"])
                rows = listing["data"][page : page + 1]
            else:
                rows = listing["data"] if int(params["page"]) == 0 else []
            return {"total": listing["total"], "data": rows}
        topic_id = url.removesuffix("/details").rsplit("/", 1)[-1]
        return details[topic_id]

    monkeypatch.setattr(dsip, "get_json", fake_get)
    return calls


def test_recorded_active_topic_maps_program_evidence(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    calls = _recorded_api(monkeypatch)

    out = DsipTopicsSource().enrich(
        SourceQuery(keywords=["particle beam"], agencies=["USAF"], limit=10)
    )

    assert out["_provenance"]["status"] == "success"
    assert out["_provenance"]["promotion_eligible"] is False
    assert out["_provenance"]["records_received"] == 2
    assert out["_provenance"]["records_matched"] == 1
    assert out["_provenance"]["consumer"] == "prospective_horizon"
    row = out["records"][0]
    assert row["record_id"] == "dsip:6106c9a94bbc4141ad47165380086146_86519"
    assert row["source"] == "dsip_topics"
    assert row["tier"] == "program"
    assert row["status"] == "Open"
    assert row["topic_code"] == "DAF26BX03-DV505"
    assert row["open_date"] == "2026-06-24"
    assert row["close_date"] == "2026-07-22"
    assert row["matched_terms"] == ["particle beam"]
    assert row["live_solicitation"] is False
    assert row["promotion_eligible"] is False
    assert row["retrieved_at"] == "2026-07-20T12:00:00+00:00"
    assert row["data_as_of"] == "2026-07-20"
    assert "<p>" not in row["objective"]
    assert row["canonical_url"].endswith(
        "/6106c9a94bbc4141ad47165380086146_86519/details"
    )
    assert calls[0]["url"] == dsip.STATUS_URL
    assert all(call["headers"] == dsip.HEADERS for call in calls)


def test_pre_release_topic_keeps_only_officially_displayed_tpoc(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    _recorded_api(monkeypatch)

    out = DsipTopicsSource().enrich(SourceQuery(keywords=["quantum sensors"]))

    assert len(out["records"]) == 1
    row = out["records"][0]
    assert row["status"] == "Pre-Release"
    assert row["direct_contact_permitted"] is True
    assert row["topic_managers"] == [
        {
            "name": "xTech Team",
            "organization": "ARMY",
            "role": "TPOC",
            "email": "usarmy.pentagon.hqda-asa-alt.mbx.xtechsearch@army.mil",
        }
    ]
    assert row["technology_areas"] == ["Materials"]
    assert "Quantum Science" in row["focus_areas"]


def test_official_active_status_ids_and_all_pages_are_pinned(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    monkeypatch.setattr(dsip, "PAGE_SIZE", 1)
    calls = _recorded_api(monkeypatch, paginate=True)

    out = DsipTopicsSource().enrich(SourceQuery())

    search_calls = [call for call in calls if call["url"] == dsip.SEARCH_URL]
    assert [call["params"]["page"] for call in search_calls] == [0, 1]
    for call in search_calls:
        request = json.loads(call["params"]["searchParam"])
        assert request["solicitationCycleNames"] == ["openTopics"]
        assert request["topicReleaseStatus"] == [591, 592]
        assert call["params"]["size"] == 1
    assert {row["status"] for row in out["records"]} == {"Open", "Pre-Release"}
    attempts = out["_provenance"]["source_attempts"]
    assert attempts[1] == {
        "source": "dsip_active_topic_search",
        "status": "success",
        "records_received": 2,
        "records_expected": 2,
    }


def test_daily_snapshot_is_reused_and_atomic(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    calls = _recorded_api(monkeypatch)

    first = DsipTopicsSource().enrich(SourceQuery())
    first_call_count = len(calls)
    second = DsipTopicsSource().enrich(SourceQuery())

    assert len(calls) == first_call_count
    assert first["_provenance"]["mode"] == "live_official_source"
    assert second["_provenance"]["mode"] == "official_daily_cache"
    snapshot = tmp_path / "snapshot_2026-07-20.json"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert len(payload["records"]) == 2
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]


def test_corrupt_current_uses_named_stale_official_snapshot(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    (tmp_path / "snapshot_2026-07-20.json").write_text("{bad-json", encoding="utf-8")
    old_record = {
        "record_id": "dsip:cached-topic",
        "canonical_url": "https://www.dodsbirsttr.mil/topics/api/public/topics/cached-topic/details",
        "source": "dsip_topics",
        "tier": "program",
        "title": "Cached official DSIP topic",
        "status": "Open",
        "retrieved_at": "2026-07-19T12:00:00+00:00",
        "data_as_of": "2026-07-19",
    }
    (tmp_path / "snapshot_2026-07-19.json").write_text(
        json.dumps(
            {
                "source": "dsip_topics",
                "canonical_url": dsip.TOPICS_APP_URL,
                "retrieved_at": "2026-07-19T12:00:00+00:00",
                "data_as_of": "2026-07-19",
                "partial": False,
                "records": [old_record],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dsip,
        "get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("DSIP down")),
    )

    out = DsipTopicsSource().enrich(SourceQuery())

    assert out["records"][0]["record_id"] == "dsip:cached-topic"
    provenance = out["_provenance"]
    assert provenance["mode"] == "stale_official_cache"
    assert provenance["status"] == "partial"
    assert provenance["stale"] is True
    assert provenance["age_days"] == 1
    assert "DSIP down" in provenance["live_error"]


def test_corrupt_current_is_replaced_when_live_source_recovers(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    (tmp_path / "snapshot_2026-07-20.json").write_text("{bad-json", encoding="utf-8")
    _recorded_api(monkeypatch)

    out = DsipTopicsSource().enrich(SourceQuery())

    assert out["_provenance"]["mode"] == "live_official_source"
    assert out["_provenance"]["source_attempts"][0]["source"] == "daily_cache"
    assert out["_provenance"]["source_attempts"][0]["status"] == "failed"
    recovered = json.loads(
        (tmp_path / "snapshot_2026-07-20.json").read_text(encoding="utf-8")
    )
    assert len(recovered["records"]) == 2


def test_detail_outage_retains_header_and_marks_snapshot_partial(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, tmp_path)
    listing = _fixture("dsip_topics_search.json")
    details = _fixture("dsip_topic_details.json")
    failed_id = "6106c9a94bbc4141ad47165380086146_86519"

    def fake_get(url, *, params=None, **kwargs):
        if url == dsip.STATUS_URL:
            return listing["statuses"]
        if url == dsip.SEARCH_URL:
            return {"total": listing["total"], "data": listing["data"]}
        topic_id = url.removesuffix("/details").rsplit("/", 1)[-1]
        if topic_id == failed_id:
            raise RuntimeError("detail endpoint unavailable")
        return details[topic_id]

    monkeypatch.setattr(dsip, "get_json", fake_get)
    out = DsipTopicsSource().enrich(SourceQuery())

    assert out["_provenance"]["status"] == "partial"
    assert out["_provenance"]["partial"] is True
    failed = next(row for row in out["records"] if row["record_id"].endswith(failed_id))
    assert failed["title"].startswith("Development of Directed Energy")
    assert failed["keywords"] is None
    assert "1 of 2 topic-detail calls failed" in out["_provenance"]["limitations"]
    assert out["_provenance"]["source_attempts"][-1]["status"] == "partial"


def test_phrase_matching_is_deterministic_and_not_substring_based():
    row = {
        "agency": "Department of Defense",
        "component": "USAF",
        "title": "Training platform for a zero-trust network",
    }

    assert dsip._match_record(row, SourceQuery(keywords=["ai"])) == (False, [])
    assert dsip._match_record(row, SourceQuery(keywords=["zero trust"])) == (
        True,
        ["zero trust"],
    )
    assert dsip._match_record(
        row, SourceQuery(keywords=["zero trust"], agencies=["Treasury"])
    ) == (False, [])


def test_adapter_cannot_enter_live_opportunity_flow():
    source = DsipTopicsSource()
    assert source.kind == SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError, match="PROGRAM-tier"):
        source.search(SourceQuery())
