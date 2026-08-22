"""SEC FY2026 forecast: opaque dollars and incumbent dates stay honest."""

import json
import os
from pathlib import Path

import pytest

import tools.api.sec_procurement_forecast as source_module
from agents.schemas import ForecastRecord
from tools.api.base import SourceQuery


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sources" /
                      "pdf_forecast_wave.json").read_text())
ROW = FIXTURE["sec"]
LAST_MODIFIED = "Thu, 26 Feb 2026 14:30:00 GMT"


def _source(monkeypatch, rows=None):
    rows = rows if rows is not None else [ROW]
    header = list(source_module._COLUMNS)
    table = [header] + [[row.get(key) for key in header] for row in rows]
    monkeypatch.setattr(source_module, "pdf_tables", lambda _blob: [table])
    return source_module.SecProcurementForecastSource(
        fetch_bytes=lambda *args, **kwargs: (
            b"%PDF-recorded-fixture", {"Last-Modified": LAST_MODIFIED}))


def test_pdf_table_preserves_piid_and_opaque_dollar_code(monkeypatch):
    src = _source(monkeypatch)
    records = src.search(SourceQuery(
        keywords=["JFROG"], naics_codes=["5415"], psc_codes=["7A"], limit=10))
    assert len(records) == 1
    record = records[0]
    assert record.source_id.startswith(
        "sec-fy2026-incumbent-50310225F0054-")
    assert record.naics_code == "541519" and record.psc_code == "7A21"
    assert record.estimated_value is None
    assert record.set_aside is None
    assert record.posted_date is None and record.response_deadline is None
    normalized = record.raw_payload["_normalized"]
    assert normalized["obligated_dollars_code"] == "A"
    assert normalized["incumbent_end_date"] == "5/31/2026"
    assert normalized["anticipated_solicitation"] is None
    assert normalized["anticipated_award"] is None
    assert normalized["_join_keys"]["incumbent_contract_id"] == "50310225F0054"


def test_stable_id_disambiguates_same_piid_requirements():
    second = dict(ROW, requirement="A distinct requirement")
    assert source_module._source_id(ROW) != source_module._source_id(second)


def test_http_last_modified_and_failure_provenance(monkeypatch):
    src = _source(monkeypatch)
    record = src.search(SourceQuery())[0]
    env = record.raw_payload["_provenance"]
    assert env["status"] == "complete"
    assert env["data_as_of"].startswith("2026-02-26")
    assert "opaque" in " ".join(env["limitations"])

    def fail(*args, **kwargs):
        raise RuntimeError("SEC access denied")

    dead = source_module.SecProcurementForecastSource(fetch_bytes=fail)
    assert dead.search(SourceQuery()) == []
    assert dead.last_provenance["status"] == "failed"


def test_forecast_method_does_not_turn_incumbent_end_into_solicitation(monkeypatch):
    forecast = _source(monkeypatch).forecasts()[0]
    assert isinstance(forecast, ForecastRecord)
    assert forecast.source == "sec_procurement_forecast"
    assert forecast.anticipated_solicitation is None
    assert forecast.anticipated_award is None
    assert forecast.estimated_value_range is None


def test_healthcheck_validates_parse(monkeypatch):
    ok, detail = _source(monkeypatch).healthcheck()
    assert ok and "1 parsed rows" in detail


@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1")
def test_live_healthcheck():
    ok, detail = source_module.SecProcurementForecastSource().healthcheck()
    assert ok, detail
