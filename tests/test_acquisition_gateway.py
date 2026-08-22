"""Deterministic contract tests for the public Acquisition Gateway adapter."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import tools.api.forecasts.acquisition_gateway as gateway
from tools.api.base import SourceQuery
from tools.api.forecasts.acquisition_gateway import AcquisitionGatewaySource, map_record

import pytest


@pytest.fixture(autouse=True)
def _arm_gateway(monkeypatch):
    """conftest disarms the adapter suite-wide (a cache miss in an isolated
    cache dir becomes a live ~5-minute crawl); these unit tests exercise the
    adapter itself against fakes, so they re-arm it locally."""
    monkeypatch.setenv("LILA_ENABLE_ACQ_GATEWAY", "on")


LISTING_ROW = {
    "nid": "47815",
    "pid": "47815",
    "render": {
        "title": "Enterprise Network Performance Monitoring",
        "body": "<p>Monitoring and observability for a civilian agency network.</p>",
        "field_result_id": "Department of the Treasury",
        "field_organization": "Internal Revenue Service",
        "field_source_listing_id": "A032600001",
        "field_award_status": "Forecast",
        "field_contract_type": "Firm Fixed Price",
        "field_estimated_award_fy": "2027",
        "field_estimated_contract_v_max": "$10M to $25M",
        "field_naics_code": "541512 - Computer Systems Design Services",
        "field_acquisition_strategy": "Full and Open",
        "changed": "2026-07-18",
    },
    "values": {},
}


def _snapshot(records, *, retrieved_at="2026-07-20T08:00:00+00:00"):
    return {
        "retrieved_at": retrieved_at,
        "total_available": len(records),
        "complete": True,
        "records": records,
    }


def test_listing_record_maps_full_fidelity():
    observed = datetime(2026, 7, 20, tzinfo=timezone.utc)
    record = map_record(LISTING_ROW, retrieved_at=observed)

    assert record.source == "acquisition_gateway"
    assert record.source_id == "A032600001"
    assert record.agency == "Department of the Treasury"
    assert record.component == "Internal Revenue Service"
    assert record.title == "Enterprise Network Performance Monitoring"
    assert record.description == (
        "Monitoring and observability for a civilian agency network."
    )
    assert record.naics_code == "541512"
    assert record.naics_label == "Computer Systems Design Services"
    assert record.estimated_value_range == "$10M to $25M"
    assert record.fiscal_year == "2027"
    assert record.anticipated_award == "2027"
    assert record.award_type == "Firm Fixed Price"
    assert record.set_aside == "Full and Open"
    assert record.forecast_status == "Forecast"
    assert record.url.endswith("/resources/47815?nid=47815")
    assert record.retrieved_at == observed
    assert record.data_as_of == "2026-07-18"


def test_live_snapshot_pages_complete_census_and_deduplicates(monkeypatch):
    second = {**LISTING_ROW, "nid": "47816", "pid": "47816"}
    third = {**LISTING_ROW, "nid": "47817", "pid": "47817"}
    fourth = {**LISTING_ROW, "nid": "47818", "pid": "47818"}
    fifth = {**LISTING_ROW, "nid": "47819", "pid": "47819"}
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params)
        if "page" not in params:
            return {"listing": {"total": 5, "data": [LISTING_ROW, second]}}
        if params["page"] == 2:
            return {"listing": {"total": 5, "data": [third, fourth]}}
        return {"listing": {"total": 5, "data": [fifth]}}

    monkeypatch.setattr(gateway, "get_json", fake_get)
    monkeypatch.setattr(gateway, "PAGE_SIZE", 2)
    monkeypatch.setattr(gateway, "PAGE_DELAY_S", 0)

    snapshot = AcquisitionGatewaySource()._live_snapshot()

    assert [row["nid"] for row in snapshot["records"]] == [
        "47815",
        "47816",
        "47817",
        "47818",
        "47819",
    ]
    assert snapshot["total_available"] == 5
    assert snapshot["complete"] is True
    assert calls == [
        {"range": 2},
        {"page": 2, "range": 2},
        {"page": 3, "range": 2},
    ]


def test_daily_cache_prevents_repeat_network_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    calls = 0

    def fake_live(self):
        nonlocal calls
        calls += 1
        return _snapshot([LISTING_ROW])

    monkeypatch.setattr(AcquisitionGatewaySource, "_live_snapshot", fake_live)
    first = AcquisitionGatewaySource()
    second = AcquisitionGatewaySource()

    assert len(first.forecasts()) == 1
    cached_records = second.forecasts()
    assert len(cached_records) == 1
    assert calls == 1
    assert first.last_provenance["mode"] == "live_official_api"
    assert second.last_provenance["mode"] == "official_daily_cache"
    assert cached_records[0].retrieved_at == datetime(
        2026, 7, 20, 8, tzinfo=timezone.utc
    )


def test_corrupt_current_cache_is_replaced_from_official_api(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    current = tmp_path / "acquisition_gateway_2026-07-20.json"
    current.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        AcquisitionGatewaySource,
        "_live_snapshot",
        lambda self: _snapshot([LISTING_ROW]),
    )

    source = AcquisitionGatewaySource()
    assert [row.source_id for row in source.forecasts()] == ["A032600001"]
    assert json.loads(current.read_text(encoding="utf-8"))["complete"] is True
    assert source.last_provenance["mode"] == "live_official_api"


def test_incomplete_current_cache_retries_and_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    current = tmp_path / "acquisition_gateway_2026-07-20.json"
    incomplete = {
        **_snapshot([LISTING_ROW]),
        "total_available": 2,
        "complete": False,
    }
    current.write_text(json.dumps(incomplete), encoding="utf-8")
    calls = []

    def refresh(self):
        calls.append(1)
        second = {**LISTING_ROW, "nid": "47816", "pid": "47816"}
        return _snapshot([LISTING_ROW, second])

    monkeypatch.setattr(AcquisitionGatewaySource, "_live_snapshot", refresh)
    source = AcquisitionGatewaySource()

    assert len(source.forecasts()) == 2
    assert calls == [1]
    assert json.loads(current.read_text(encoding="utf-8"))["complete"] is True
    assert source.last_provenance["mode"] == "live_official_api"


def test_incomplete_repair_never_downgrades_a_larger_partial_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    current = tmp_path / "acquisition_gateway_2026-07-20.json"
    second = {**LISTING_ROW, "nid": "47816", "pid": "47816"}
    current.write_text(json.dumps({
        **_snapshot([LISTING_ROW, second]),
        "total_available": 3,
        "complete": False,
    }), encoding="utf-8")
    monkeypatch.setattr(
        AcquisitionGatewaySource,
        "_live_snapshot",
        lambda self: {
            **_snapshot([LISTING_ROW]),
            "total_available": 3,
            "complete": False,
        },
    )

    source = AcquisitionGatewaySource()

    assert len(source.forecasts()) == 2
    assert len(json.loads(current.read_text(encoding="utf-8"))["records"]) == 2
    assert source.last_provenance["mode"] == "partial_current_day_cache"
    assert "did not improve" in source.last_provenance["limitation"]


def test_concurrent_complete_refresh_wins_over_late_partial(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    current = tmp_path / "acquisition_gateway_2026-07-20.json"
    current.write_text(json.dumps({
        **_snapshot([LISTING_ROW]),
        "total_available": 2,
        "complete": False,
    }), encoding="utf-8")
    second = {**LISTING_ROW, "nid": "47816", "pid": "47816"}

    def refresh_while_another_worker_finishes(self):
        gateway._atomic_json(current, _snapshot([LISTING_ROW, second]))
        return {
            **_snapshot([LISTING_ROW]),
            "total_available": 2,
            "complete": False,
        }

    monkeypatch.setattr(
        AcquisitionGatewaySource,
        "_live_snapshot",
        refresh_while_another_worker_finishes,
    )
    source = AcquisitionGatewaySource()

    assert len(source.forecasts()) == 2
    assert json.loads(current.read_text(encoding="utf-8"))["complete"] is True
    assert source.last_provenance["mode"] == (
        "official_daily_cache_after_concurrent_refresh"
    )
    assert source.last_provenance["complete"] is True


def test_failed_refresh_retains_named_partial_current_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    current = tmp_path / "acquisition_gateway_2026-07-20.json"
    current.write_text(json.dumps({
        **_snapshot([LISTING_ROW]),
        "total_available": 3,
        "complete": False,
    }), encoding="utf-8")

    def fail_live(self):
        raise RuntimeError("official endpoint unavailable")

    monkeypatch.setattr(AcquisitionGatewaySource, "_live_snapshot", fail_live)
    source = AcquisitionGatewaySource()

    assert [row.source_id for row in source.forecasts()] == ["A032600001"]
    assert source.last_provenance["mode"] == "partial_current_day_cache"
    assert source.last_provenance["complete"] is False
    assert "partial snapshot was retained" in source.last_provenance["limitation"]
    assert source.last_provenance["private_error"] == "official endpoint unavailable"


def test_live_failure_uses_named_stale_official_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    stale = tmp_path / "acquisition_gateway_2026-07-19.json"
    stale.write_text(json.dumps(_snapshot([LISTING_ROW])), encoding="utf-8")

    def fail_live(self):
        raise RuntimeError("official endpoint unavailable")

    monkeypatch.setattr(AcquisitionGatewaySource, "_live_snapshot", fail_live)
    source = AcquisitionGatewaySource()

    assert [row.source_id for row in source.forecasts()] == ["A032600001"]
    assert source.last_provenance["mode"] == "stale_official_cache"
    assert source.last_provenance["complete"] is True
    assert "live public API failed" in source.last_provenance["limitation"]


def test_corrupt_current_cache_does_not_mask_valid_older_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    current = tmp_path / "acquisition_gateway_2026-07-20.json"
    current.write_text("not-json", encoding="utf-8")
    stale = tmp_path / "acquisition_gateway_2026-07-19.json"
    stale.write_text(json.dumps(_snapshot([LISTING_ROW])), encoding="utf-8")
    monkeypatch.setattr(
        AcquisitionGatewaySource,
        "_live_snapshot",
        lambda self: (_ for _ in ()).throw(RuntimeError("official endpoint down")),
    )

    source = AcquisitionGatewaySource()

    assert [row.source_id for row in source.forecasts()] == ["A032600001"]
    assert source.last_provenance["mode"] == "stale_official_cache"


def test_healthcheck_uses_the_real_first_page_shape(monkeypatch):
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params)
        return {"listing": {"total": 1, "data": [LISTING_ROW]}}

    monkeypatch.setattr(gateway, "get_json", fake_get)

    assert AcquisitionGatewaySource().healthcheck()[0] is True
    assert calls == [{"range": 1}]


def test_matched_detail_enrichment_is_cached_and_evidence_bound(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_today", lambda: date(2026, 7, 20))
    monkeypatch.setattr(gateway, "DETAIL_DELAY_S", 0)
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return {
            "field_estimated_solicitation_dat": [{"value": "2026-09-15"}],
            "field_contractor_name": [{"value": "Example Incumbent LLC"}],
            "field_advisor_info_name": [{"value": "Alex Advisor"}],
            "field_advisor_info_email": [{"value": "alex@gsa.gov"}],
        }

    monkeypatch.setattr(gateway, "get_json", fake_get)
    record = map_record(LISTING_ROW)

    enriched = AcquisitionGatewaySource().enrich_matched([record])[0]
    cached = AcquisitionGatewaySource().enrich_matched([record])[0]

    assert enriched.anticipated_solicitation == "2026-09-15"
    assert enriched.incumbent_stated == "Example Incumbent LLC"
    assert enriched.small_business_poc == "Alex Advisor <alex@gsa.gov>"
    assert cached == enriched
    assert calls == [gateway.DETAIL_API.format(id="47815")]


def test_forecast_source_never_enters_live_flow_and_can_be_disabled(monkeypatch):
    monkeypatch.delenv("LILA_ENABLE_ACQ_GATEWAY", raising=False)
    assert AcquisitionGatewaySource().enabled is True
    assert AcquisitionGatewaySource().search(SourceQuery(keywords=["network"])) == []

    monkeypatch.setenv("LILA_ENABLE_ACQ_GATEWAY", "off")
    source = AcquisitionGatewaySource()
    assert source.enabled is False
    assert source.forecasts() == []
    assert source.last_provenance["mode"] == "not_run"
