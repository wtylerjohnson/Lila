"""Focused tests for the NASA agency-wide acquisition forecast lane."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook

from agents.schemas import ForecastRecord
from tools.api.base import SourceKind, SourceQuery, SourceRegistry, discover
from tools.api.forecasts.nasa_naf import (
    NasaNafSource,
    _stable_id,
)


ACTIVE_ROW: dict[str, Any] = {
    "BuyingOffice": "KSC",
    "BuyingOfficeCode": "OPMS0",
    "AcquisitionStatus": "Revised",
    "AwardedOrWithdrawn": "N/A",
    "AcquisitionPhase": "Acquisition Planning",
    "SourceID": 1139,
    "TitleOfRequirement": (
        "Consolidated Operations, Management, Engineering and Test Follow-On"
    ),
    "TechnicalPOC": "nicholas.s.reinert@nasa.gov",
    "Tech POC Name": "Reinert, Nick",
    "NAICS": "541715",
    "NAICS Description": "Research and Development Services",
    "PSC Code": "AR15",
    "SmallBusinessSpecialistPOC": "Sims, Tamara",
    "SmallBusinessSpecialistEmail": "ksc-smallbusiness@mail.nasa.gov",
    "AnticipatedFYAward": "2033",
    "Anticipated Qtr of Award": "Q3",
    "NewOrRecompete": "Re-compete",
    "EstimatedContractValue": "$250M - $500M",
    "SetAsideType": "To Be Determined",
    "ContractType": "Cost Plus Award Fee",
    "Summary": "Engineering and ground-system support.",
    "QtrSolOrNOFORelease": "Q1",
    "FYofSolOrNOFORelease": "2032",
}

WITHDRAWN_ROW: dict[str, Any] = {
    **ACTIVE_ROW,
    "SourceID": 1186,
    "TitleOfRequirement": "Enterprise Contract Administration System",
    "AcquisitionStatus": "Withdrawn",
    "ReasonForWithdraw": "Requirement cancelled",
}

SECOND_ACTIVE_ROW: dict[str, Any] = {
    **ACTIVE_ROW,
    "BuyingOffice": "ARC",
    "SourceID": 372,
    "TitleOfRequirement": "NASA Launch Services Contract",
    "NAICS": "336414",
    "NAICS Description": "Guided Missile and Space Vehicle Manufacturing",
    "QtrSolOrNOFORelease": "Q4",
    "FYofSolOrNOFORelease": "2028",
}

LAST_MODIFIED = "Tue, 04 Aug 2026 18:03:05 GMT"


def _workbook(rows: list[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Forecast"
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _source(rows: list[dict[str, Any]]) -> NasaNafSource:
    payload = _workbook(rows)

    def fetch(url: str) -> tuple[bytes, dict[str, Any]]:
        return payload, {
            "url": url,
            "last_modified": LAST_MODIFIED,
            "etag": "fixture-etag",
        }

    return NasaNafSource(fetch=fetch)


def test_maps_official_row_to_typed_forecast(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    source = _source([ACTIVE_ROW])

    records = source.forecasts()

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ForecastRecord)
    assert record.source == "nasa_naf"
    assert record.source_id == "1139"
    assert _stable_id(ACTIVE_ROW) == "1139"
    assert record.agency == "NASA"
    assert record.component == "KSC"
    assert record.naics_code == "541715"
    assert record.naics_label == "Research and Development Services"
    assert record.psc == "AR15"
    assert record.estimated_value_range == "$250M - $500M"
    assert record.anticipated_solicitation == "Q1 FY2032"
    assert record.anticipated_award == "Q3 FY2033"
    assert record.small_business_poc == (
        "Sims, Tamara <ksc-smallbusiness@mail.nasa.gov>"
    )
    assert record.incumbent_stated is None
    assert record.source_fields["SourceID"] == 1139
    assert record.data_as_of == "2026-08-04T18:03:05+00:00"


def test_withdrawn_rows_are_excluded_and_disclosed(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    source = _source([ACTIVE_ROW, WITHDRAWN_ROW, SECOND_ACTIVE_ROW])

    records = source.forecasts()

    assert [record.source_fields["SourceID"] for record in records] == [
        1139,
        372,
    ]
    provenance = source.last_provenance
    assert provenance["schema_version"] == 1
    assert provenance["status"] == "complete"
    assert provenance["complete"] is True
    assert provenance["record_count"] == provenance["records"] == 2
    assert provenance["total_available"] == 3
    assert provenance["inactive_rows_excluded"] == 1
    assert "Awarded or Withdrawn" in " ".join(provenance["limitations"])
    assert provenance["classification"] == (
        "agency forecast; never an active notice"
    )


def test_stable_ids_ignore_row_order_and_fallback_spacing():
    reordered = dict(reversed(list(ACTIVE_ROW.items())))
    changed_non_id_field = {**ACTIVE_ROW, "Summary": "Different summary"}
    assert _stable_id(reordered) == _stable_id(changed_non_id_field) == "1139"

    fallback = {**ACTIVE_ROW, "SourceID": None}
    spaced = {
        **fallback,
        "TitleOfRequirement": "  Consolidated   Operations, Management,  "
        "Engineering and Test Follow-On ",
        "BuyingOffice": " ksc ",
    }
    assert _stable_id(fallback) == _stable_id(spaced)
    assert _stable_id(fallback).startswith("nasa-naf-fallback-")


def test_forecast_lane_cannot_enter_discovery(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    source = _source([ACTIVE_ROW])
    assert source.kind is SourceKind.ENRICHMENT
    assert source.search(SourceQuery(keywords=["engineering"])) == []

    registry = SourceRegistry()
    registry.add(source)
    assert discover(SourceQuery(keywords=["engineering"]), registry) == []


def test_successful_all_withdrawn_pull_is_complete_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    source = _source([WITHDRAWN_ROW])

    assert source.forecasts() == []
    assert source.last_provenance["status"] == "complete"
    assert source.last_provenance["complete"] is True
    assert source.last_provenance["record_count"] == 0
    assert source.last_provenance["inactive_rows_excluded"] == 1


def test_untitled_row_makes_snapshot_partial(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))
    untitled = {**ACTIVE_ROW, "SourceID": 999, "TitleOfRequirement": None}
    source = _source([ACTIVE_ROW, untitled])

    records = source.forecasts()

    assert len(records) == 1
    assert source.last_provenance["status"] == "partial"
    assert source.last_provenance["complete"] is False
    assert source.last_provenance["malformed_rows"] == 1


def test_fetch_failure_isolated_with_failed_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path))

    def fail(_url: str) -> tuple[bytes, dict[str, Any]]:
        raise ConnectionError("fixture outage")

    source = NasaNafSource(fetch=fail)

    assert source.forecasts() == []
    assert source.last_provenance["status"] == "failed"
    assert source.last_provenance["complete"] is False
    assert source.last_provenance["record_count"] == 0
    assert "fixture outage" in source.last_provenance["private_error"]
