"""Deterministic contracts for accepted connector wave 3.

Every normal test uses the recorded official-source fixture.  The final health
test is opt-in and never reaches the network during a normal suite run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.api.base import SourceKind, SourceQuery
from tools.api.cofc_docket_rss import CofcDocketRssSource
from tools.api.doj_forecast import DojForecastSource
from tools.api.epa_forecast import EpaForecastSource
from tools.api.gsa_it_collect import GsaItCollectSource
from tools.api.nasa_sewp_vi import NasaSewpViSource
from tools.api.nitaac_holders import NitaacHoldersSource
from tools.api.treasury_sbecs import TreasurySbecsSource

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sources" / "connector_wave3.json"


@pytest.fixture(scope="module")
def recorded() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_is_recorded_and_source_bound(recorded):
    provenance = recorded["_fixture_provenance"]
    assert provenance["kind"] == "recorded"
    assert provenance["retrieved_at_utc"].endswith("Z")
    assert len([value for value in provenance.values()
                if str(value).startswith("https://")]) == 8


def _assert_immutable_receipt(out):
    provenance = out["_provenance"]
    assert len(provenance["raw_content_sha256"]) == 64
    assert provenance["raw_content_kind"]


@pytest.mark.parametrize("source,method", [
    (NitaacHoldersSource(), "contract_holders"),
    (NasaSewpViSource(), "awardees"),
    (CofcDocketRssSource(), "litigation_events"),
    (EpaForecastSource(), "forecast_records"),
    (TreasurySbecsSource(), "forecast_records"),
    (DojForecastSource(), "forecast_records"),
    (GsaItCollectSource(), "portfolio_context"),
])
def test_all_wave3_sources_are_enrichment_only(source, method):
    assert source.kind is SourceKind.ENRICHMENT
    assert callable(getattr(source, method))
    with pytest.raises(NotImplementedError):
        source.search(SourceQuery(keywords=["test"]))


def test_nitaac_holder_keys_and_access_semantics(recorded):
    source = NitaacHoldersSource(
        fetch_bytes=lambda *_args, **_kwargs: recorded["nitaac_csv"].encode())
    out = source.contract_holders(SourceQuery(keywords=["HCCFTD1R3UL3"], limit=10))
    assert out["total_matched"] == 1
    row = out["items"][0]
    assert row["uei"] == "HCCFTD1R3UL3"
    assert row["piids"] == ["HHSN316201200006W"]
    assert row["task_areas"] == ["Task Area 1", "Task Area 10"]
    assert row["evidence_semantics"] == "vehicle_access_not_incumbency"
    assert out["_provenance"]["status"] == "complete"
    _assert_immutable_receipt(out)


def test_sewp_dedupes_prime_across_categories_without_inventing_keys(recorded):
    grids = recorded["sewp_grids"]
    source = NasaSewpViSource(
        fetch_bytes=lambda *_args, **_kwargs: b"PK-recorded",
        parse_workbook=lambda _blob: grids)
    out = source.awardees(SourceQuery(keywords=["Carahsoft"], limit=10))
    assert out["total_matched"] == 1
    row = out["items"][0]
    assert row["categories"] == ["Category A", "Category B"]
    assert row["uei"] is None and row["piids"] == []
    assert row["_join_keys"] == {"legal_name_inference": "Carahsoft Technology Corp."}
    _assert_immutable_receipt(out)


def test_cofc_filters_vaccine_noise_and_marks_timing_risk(recorded):
    source = CofcDocketRssSource(
        fetch_text=lambda *_args, **_kwargs: recorded["cofc_rss"])
    out = source.litigation_events(SourceQuery(keywords=["Sublime"], limit=10))
    assert out["total_matched"] == 1
    row = out["items"][0]
    assert row["case_number"] == "1:26-cv-00458"
    assert row["entry_type"] == "Reply to Response to Motion"
    assert row["vendor_name_inference_key"] == "SUBLIME SYSTEMS"
    assert row["evidence_semantics"] == "litigation_timing_risk"
    _assert_immutable_receipt(out)


def test_epa_current_and_canceled_are_planning_records_and_partial(recorded):
    source = EpaForecastSource(fetch_pages=lambda: recorded["epa_pages"])
    current = source.forecast_records(SourceQuery(keywords=["cybersecurity"], limit=10))
    assert current["items"][0]["record_id"] == "epa-forecast:FY-2026-15000"
    assert current["items"][0]["forecast_status"] == "current"
    assert current["items"][0]["evidence_semantics"] == "planning_intent_not_live_opportunity"
    assert current["_provenance"]["status"] == "partial"
    _assert_immutable_receipt(current)

    canceled = source.forecast_records(SourceQuery(keywords=["Environmental"], limit=10))
    assert canceled["items"][0]["forecast_status"] == "canceled"


def _treasury_apex(recorded, calls):
    treasury = recorded["treasury"]

    def apex(classname, method, params):
        calls.append((classname, method, params))
        if method == "getListForFilter_Bureau":
            return treasury["bureau_lookup"]
        if method == "getListForFilter_NAICS":
            return treasury["naics_lookup"]
        if method == "getListForFilter_Psc":
            return treasury["psc_lookup"]
        if method == "getData":
            return treasury["groups"]
        raise AssertionError(method)
    return apex


def test_treasury_resolves_official_filter_ids_before_data_call(recorded):
    calls = []
    source = TreasurySbecsSource(apex=_treasury_apex(recorded, calls))
    out = source.forecast_records(SourceQuery(
        agencies=["Internal Revenue Service"], naics_codes=["541519"],
        psc_codes=["D399"], keywords=["identity"], limit=10))
    assert [method for _, method, _ in calls] == [
        "getListForFilter_Bureau", "getListForFilter_NAICS",
        "getListForFilter_Psc", "getData"]
    sent = calls[-1][2]
    assert sent["bureauFilter"] == ["B-IRS"]
    assert sent["NAICSFilter"] == ["N-541519"]
    assert sent["PSCFilter"] == ["P-D399"]
    row = out["items"][0]
    assert row["record_id"] == "treasury-sbecs:a0RTEST1"
    assert row["naics_code"] == "541519"
    assert row["evidence_semantics"] == "planning_intent_not_live_opportunity"
    _assert_immutable_receipt(out)


def test_it_collect_uses_service_scoped_route_and_context_semantics(recorded):
    calls = []

    def fetch(url, *, params, **_kwargs):
        calls.append((url, dict(params)))
        return recorded["it_collect"]

    source = GsaItCollectSource(fetch_json=fetch, api_key="fixture-key")
    out = source.portfolio_context(
        SourceQuery(keywords=["INV2472"], limit=10))
    assert calls[0][0].endswith("/v1/services/INV2472/contracts")
    assert calls[0][1] == {"api_key": "fixture-key"}
    row = out["items"][0]
    assert row["piid"] == "12314422F0249"
    assert row["reference_piid"] == "47QTCB21D0367"
    assert row["evidence_semantics"] == "it_portfolio_context_not_opportunity_or_obligation"
    _assert_immutable_receipt(out)


def test_doj_normalized_fixture_remains_forecast_only(recorded):
    source = DojForecastSource(fetch_rows=lambda: (recorded["doj_rows"], False))
    out = source.forecast_records(SourceQuery(keywords=["FY26-OJP-1550-0042"], limit=10))
    assert out["items"][0]["action_tracking_number"] == "FY26-OJP-1550-0042"
    assert out["items"][0]["_join_keys"]["contracting_office_code"] == "15PBJS"
    assert out["items"][0]["evidence_semantics"] == "planning_intent_not_live_opportunity"
    _assert_immutable_receipt(out)


@pytest.mark.skipif(
    os.environ.get("LILA_LIVE_SOURCE_HEALTH") != "1",
    reason="set LILA_LIVE_SOURCE_HEALTH=1 for opt-in official endpoint probes",
)
@pytest.mark.parametrize("source", [
    NitaacHoldersSource(), NasaSewpViSource(), CofcDocketRssSource(),
    EpaForecastSource(), TreasurySbecsSource(), DojForecastSource(),
])
def test_live_health_is_opt_in(source):
    ok, detail = source.healthcheck()
    assert ok, detail
