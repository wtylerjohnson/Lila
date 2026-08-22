"""NAWCAD FY26 LRAF: recorded-row contract plus opt-in live health."""

import json
import os
from pathlib import Path

import pytest

import tools.api.navy_nawcad_lraf as source_module
from agents.schemas import ForecastRecord
from tools.api.base import SourceQuery


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sources" /
                      "pdf_forecast_wave.json").read_text())
ROW = FIXTURE["nawcad"]


def _source(monkeypatch, rows=None):
    rows = rows if rows is not None else [ROW]
    header = list(source_module._COLUMNS)
    table = [header, [None] * len(header)] + [
        [row.get(key) for key in header] for row in rows
    ]
    monkeypatch.setattr(source_module, "pdf_tables", lambda _blob: [table])
    return source_module.NavyNawcadLrafSource(
        fetch_bytes=lambda *args, **kwargs: (b"%PDF-recorded-fixture", {}))


def test_pdf_table_maps_forecast_without_inventing_naics(monkeypatch):
    src = _source(monkeypatch)
    records = src.search(SourceQuery(keywords=["cyber warfare"], limit=10))
    assert len(records) == 1
    record = records[0]
    assert record.source_id.startswith(
        "nawcad-lraf-incumbent-N0042122F3000N0042123D0021-")
    assert record.naics_code is None and record.psc_code is None
    assert record.estimated_value == 200_000_000
    assert record.posted_date is None and record.response_deadline is None
    normalized = record.raw_payload["_normalized"]
    assert normalized["anticipated_solicitation"] == "FY27 QTR3"
    assert normalized["anticipated_award"] == "FY28 QTR2"
    assert normalized["incumbent_contract_stated"] == (
        "N00421-22-F-3000 / N00421-23-D-0021")
    assert record.raw_payload["_evidence_tier"] == "PROGRAM"


def test_starred_values_keep_revision_fact_but_clean_typed_fields(monkeypatch):
    src = _source(monkeypatch, [dict(ROW, title=ROW["title"] + "*",
                                      rfp_fy="FY27*", award_quarter="QTR2*")])
    record = src.search(SourceQuery())[0]
    normalized = record.raw_payload["_normalized"]
    assert record.title == ROW["title"]
    assert normalized["revised_from_prior_posting"] is True
    assert normalized["anticipated_solicitation"] == "FY27 QTR3"


def test_failure_is_failed_provenance_not_empty_success():
    def fail(*args, **kwargs):
        raise RuntimeError("fixture timeout")

    src = source_module.NavyNawcadLrafSource(fetch_bytes=fail)
    assert src.search(SourceQuery()) == []
    assert src.last_provenance["status"] == "failed"
    assert src.last_provenance["record_count"] == 0


def test_forecast_method_is_structurally_program_evidence(monkeypatch):
    src = _source(monkeypatch)
    forecasts = src.forecasts()
    assert len(forecasts) == 1
    assert isinstance(forecasts[0], ForecastRecord)
    assert forecasts[0].source == "navy_nawcad_lraf"


def test_healthcheck_validates_parse(monkeypatch):
    ok, detail = _source(monkeypatch).healthcheck()
    assert ok and "1 parsed rows" in detail


@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1")
def test_live_healthcheck():
    ok, detail = source_module.NavyNawcadLrafSource().healthcheck()
    assert ok, detail
