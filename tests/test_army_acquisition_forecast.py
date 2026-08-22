"""Deterministic tests for the public Army OSBP forecast workbook adapter."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

import tools.api.forecasts.army_acquisition_forecast as army
from agents.schemas import ForecastRecord
from tools.api.base import SourceQuery
from tools.api.forecasts.army_acquisition_forecast import (
    ArmyAcquisitionForecastSource,
    map_row,
    parse_workbook,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "army_acquisition_forecast_rows.json"


@pytest.fixture(autouse=True)
def _arm_source(monkeypatch):
    monkeypatch.setenv("LILA_ENABLE_ARMY_ACQUISITION_FORECAST", "on")


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _workbook_bytes(fixture: dict | None = None) -> bytes:
    payload = fixture or _fixture()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = payload["sheet"]
    sheet.append([payload["title"]])
    sheet.append(payload["headers"])
    date_columns = set(payload["date_columns"])
    for raw_row in payload["rows"]:
        row = []
        for index, value in enumerate(raw_row):
            if index in date_columns and value:
                row.append(datetime.fromisoformat(value))
            else:
                row.append(value)
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _row(snapshot: dict, source_id: str) -> dict:
    return next(row for row in snapshot["rows"] if row["source_id"] == source_id)


def test_official_fixture_schema_and_full_fidelity_mapping():
    snapshot = parse_workbook(_workbook_bytes())

    assert snapshot["source_title"] == (
        "FY 26 ARMY MATERIEL COMMAND (AMC) ACQUSITION FORECAST JULY 2026"
    )
    assert snapshot["sheet"] == "Preaward_OSBP"
    assert snapshot["header_row"] == 2
    assert snapshot["source_rows"] == 4
    assert snapshot["data_as_of"] == "2026-07"
    assert snapshot["fiscal_year"] == "2026"
    observed = datetime(2026, 8, 14, tzinfo=timezone.utc)
    record = map_row(
        _row(snapshot, "PANMCC-26-P-0000 039034"),
        fiscal_year=snapshot["fiscal_year"],
        data_as_of=snapshot["data_as_of"],
        retrieved_at=observed,
    )

    assert isinstance(record, ForecastRecord)
    assert record.source == "army_acquisition_forecast"
    assert record.source_id == "PANMCC-26-P-0000 039034"
    assert record.agency == "Department of the Army"
    assert record.component == "TRADOC"
    assert record.title == "MCCoE HQ Consolidate Wireless contracts"
    assert record.estimated_value_range == "$25K to $250K"
    assert record.psc == "DG11"
    assert record.anticipated_solicitation == "2026-06-05"
    assert record.anticipated_solicitation_close == "2026-07-03"
    assert record.anticipated_award == "2026-09-01"
    assert record.predecessor_contract_id == "N0024424D0005"
    assert record.award_type == "Firm Fixed Price"
    assert record.small_business_poc == (
        "MICC SBO <usarmy.jbsa.acc-micc.list.hq-osbp-headquarters@army.mil>"
    )
    assert record.data_as_of == "2026-07"
    assert record.fiscal_year == "2026"
    assert record.forecast_status == "Agency acquisition forecast"
    assert record.url == army.WORKBOOK_URL
    assert record.source_fields["Forecasted contract value"] == "$25K to $250K"
    assert record.source_fields["Forecasted Award Date"] == "2026-09-01"


def test_duplicate_ids_dedupe_and_missing_ids_get_stable_fallbacks():
    fixture = deepcopy(_fixture())
    duplicate = deepcopy(fixture["rows"][0])
    duplicate[7] = "541512"
    fixture["rows"].append(duplicate)
    missing_id = deepcopy(fixture["rows"][1])
    missing_id[0] = None
    fixture["rows"].append(missing_id)

    snapshot = parse_workbook(_workbook_bytes(fixture))

    assert snapshot["source_rows"] == 6
    assert snapshot["missing_identifier_rows"] == 1
    assert snapshot["generated_identifier_rows"] == 1
    assert snapshot["duplicate_source_id_rows"] == 1
    assert snapshot["duplicate_source_ids"] == ["PANDTA-26-P-0000 030554"]
    assert len(snapshot["rows"]) == 5
    selected = _row(snapshot, "PANDTA-26-P-0000 030554")
    assert selected["naics_code"] == "541512"
    assert selected["source_id"] == "PANDTA-26-P-0000 030554"
    assert len(selected["_merged_source_rows"]) == 2
    assert selected["_published_values"] == {}
    fallback = next(
        row for row in snapshot["rows"]
        if row["source_id"].startswith("army-osbp-fallback-")
    )
    assert fallback["_generated_source_id"] is True

    reordered = deepcopy(fixture)
    reordered["rows"] = list(reversed(reordered["rows"]))
    second = parse_workbook(_workbook_bytes(reordered))
    assert fallback["source_id"] in {
        row["source_id"] for row in second["rows"]
    }

    source = ArmyAcquisitionForecastSource()
    source._provenance(
        snapshot,
        mode="live_official_workbook",
        retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert source.last_provenance["complete"] is True
    assert source.last_provenance["generated_identifier_rows"] == 1
    assert "deterministic fallback IDs" in source.last_provenance["limitation"]
    assert "omitted" not in source.last_provenance["limitation"]


def test_duplicate_rows_merge_conflicting_official_values_without_data_loss():
    fixture = deepcopy(_fixture())
    fixture["rows"][0][7] = "541330"
    duplicate = deepcopy(fixture["rows"][0])
    duplicate[7] = "541715"
    duplicate[1] = "W91TEST25C0001"
    fixture["rows"].append(duplicate)

    snapshot = parse_workbook(_workbook_bytes(fixture))
    selected = _row(snapshot, "PANDTA-26-P-0000 030554")
    assert selected["predecessor_contract_id"] == "W91TEST25C0001"
    assert selected["_published_values"]["naics_code"] == ["541330", "541715"]
    assert len(selected["_merged_source_rows"]) == 2

    record = map_row(
        selected,
        fiscal_year=snapshot["fiscal_year"],
        data_as_of=snapshot["data_as_of"],
        retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert record.source_fields["_published_values"]["naics_code"] == [
        "541330",
        "541715",
    ]
    assert len(record.source_fields["_merged_source_rows"]) == 2

    reordered = deepcopy(fixture)
    reordered["rows"] = list(reversed(reordered["rows"]))
    second = _row(
        parse_workbook(_workbook_bytes(reordered)),
        "PANDTA-26-P-0000 030554",
    )
    assert second["naics_code"] == selected["naics_code"]
    assert second["predecessor_contract_id"] == selected["predecessor_contract_id"]
    assert second["_published_values"] == selected["_published_values"]
    assert second["_merged_source_rows"] == selected["_merged_source_rows"]


def test_search_is_structurally_forecast_only():
    source = ArmyAcquisitionForecastSource()
    assert source.search(SourceQuery(keywords=["wireless"])) == []


def test_live_workbook_is_cached_with_explicit_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(army, "_today", lambda: date(2026, 8, 14))
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _workbook_bytes()

    monkeypatch.setattr(army, "get_bytes", fake_get)
    live = ArmyAcquisitionForecastSource()
    records = live.forecasts()
    cached = ArmyAcquisitionForecastSource()
    cached_records = cached.forecasts()

    assert len(records) == len(cached_records) == 4
    assert calls and len(calls) == 1
    assert live.last_provenance["mode"] == "live_official_workbook"
    assert cached.last_provenance["mode"] == "official_daily_cache"
    assert live.last_provenance["complete"] is True
    assert live.last_provenance["classification"] == (
        "agency forecast; never an active notice"
    )
    assert live.last_provenance["source_url"] == army.WORKBOOK_URL
    assert live.last_provenance["sha256"]
    assert (
        tmp_path / "army_acquisition_forecast_2026-08-14.xlsx"
    ).exists()


def test_failed_live_refresh_uses_named_stale_official_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    stale = tmp_path / "army_acquisition_forecast_2026-08-13.xlsx"
    stale.write_bytes(_workbook_bytes())
    monkeypatch.setattr(army, "_today", lambda: date(2026, 8, 14))

    def fail(*_args, **_kwargs):
        raise RuntimeError("official workbook unavailable")

    monkeypatch.setattr(army, "get_bytes", fail)
    source = ArmyAcquisitionForecastSource()

    assert len(source.forecasts()) == 4
    assert source.last_provenance["mode"] == "stale_official_cache"
    assert source.last_provenance["status"] == "partial"
    assert source.last_provenance["complete"] is False
    assert source.last_provenance["stale"] is True
    assert "using newest parseable official cache" in (
        source.last_provenance["limitation"]
    )
    assert "official workbook unavailable" in source.last_provenance["private_error"]


def test_unrecognized_workbook_is_rejected_by_named_schema_gate():
    workbook = Workbook()
    workbook.active.append(["Unrelated", "Spreadsheet"])
    output = BytesIO()
    workbook.save(output)

    with pytest.raises(ValueError, match="no recognized identifier/title/value"):
        parse_workbook(output.getvalue())
