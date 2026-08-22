"""NAWCWD FY26 LRAF: recorded-row contract plus opt-in live health."""

import json
import os
from pathlib import Path

import pytest

import tools.api.navy_nawcwd_lraf as source_module
from agents.schemas import ForecastRecord
from tools.api.base import SourceQuery


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sources" /
                      "pdf_forecast_wave.json").read_text())
ROW = FIXTURE["nawcwd"]


def _source(monkeypatch, rows=None, headers=None):
    rows = rows if rows is not None else [ROW]
    table = [list(source_module._COLUMNS)] + [
        [row.get(key) for key in source_module._COLUMNS] for row in rows
    ]
    monkeypatch.setattr(source_module, "pdf_tables", lambda _blob: [table])
    return source_module.NavyNawcwdLrafSource(
        fetch_bytes=lambda *args, **kwargs: (
            b"%PDF-recorded-fixture", headers or {}))


def test_pdf_table_and_normalization_preserve_join_keys(monkeypatch):
    src = _source(monkeypatch)
    records = src.search(SourceQuery(
        keywords=["cyber survivability"], naics_codes=["5415"],
        psc_codes=["DJ10"], limit=10))
    assert len(records) == 1
    record = records[0]
    assert record.source_id.startswith(
        "nawcwd-lraf-incumbent-N6893624D0003-")
    assert record.naics_code == "541512"
    assert record.psc_code == "DJ10"
    assert record.estimated_value == 2_000_000
    assert record.posted_date is None and record.response_deadline is None
    normalized = record.raw_payload["_normalized"]
    assert normalized["_join_keys"]["incumbent_contract_number"] == (
        "N6893624D0003")
    assert normalized["solicitation_number_stated"] is None
    assert record.raw_payload["_evidence_tier"] == "PROGRAM"
    assert {contact.email for contact in record.contacts} == {
        "daryl.t.magdangal@us.navy.mil",
        "matthew.p.minnick.civ@us.navy.mil",
    }


def test_stable_identifier_never_depends_on_row_number():
    changed = dict(ROW, row="999")
    assert source_module._source_id(ROW) == source_module._source_id(changed)
    other = dict(ROW, title="Different follow-on")
    assert source_module._source_id(ROW) != source_module._source_id(other)


def test_complete_and_failed_provenance_are_explicit(monkeypatch):
    src = _source(monkeypatch)
    records = src.search(SourceQuery(limit=1))
    env = records[0].raw_payload["_provenance"]
    assert env["source"] == "navy_nawcwd_lraf"
    assert env["status"] == "complete"
    assert env["data_as_of"] == "2026-01-13"
    assert env["record_count"] == 1

    def fail(*args, **kwargs):
        raise RuntimeError("recorded outage")

    dead = source_module.NavyNawcwdLrafSource(fetch_bytes=fail)
    assert dead.search(SourceQuery()) == []
    assert dead.last_provenance["status"] == "failed"
    assert "recorded outage" in dead.last_provenance["limitations"][0]


def test_forecast_method_is_structurally_program_evidence(monkeypatch):
    src = _source(monkeypatch)
    forecasts = src.forecasts()
    assert len(forecasts) == 1
    assert isinstance(forecasts[0], ForecastRecord)
    assert forecasts[0].source == "navy_nawcwd_lraf"
    assert forecasts[0].source_id == src.search(SourceQuery())[0].source_id


def test_healthcheck_validates_parse_not_only_http(monkeypatch):
    src = _source(monkeypatch)
    ok, detail = src.healthcheck()
    assert ok and "1 parsed rows" in detail


@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1")
def test_live_healthcheck():
    ok, detail = source_module.NavyNawcwdLrafSource().healthcheck()
    assert ok, detail
