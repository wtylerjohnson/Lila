"""Deterministic contracts for the second accepted-source connector wave."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from openpyxl import Workbook

from tools.api.base import SourceQuery
from tools.api.doe_eere_exchange import DoeEereExchangeSource
from tools.api.hhs_oig_leie import HhsOigLeieSource
from tools.api.omb_sf133 import OmbSf133Source
from tools.api.oversight_gov_reports import OversightGovReportsSource
from tools.api.oversight_gov_reports import _query_params as oversight_params
from tools.api.sam_wage_determinations import SamWageDeterminationsSource
from tools.api.sba_procurement_scorecards import SbaProcurementScorecardsSource


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sources" /
                      "accepted_wave2.json").read_text())
HEADERS = {"Last-Modified": "Sat, 08 Aug 2026 12:00:00 GMT"}


def _fetch(blob: bytes):
    return lambda *args, **kwargs: (blob, HEADERS)


def _dead(*args, **kwargs):
    raise RuntimeError("recorded transport failure")


def _xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Raw Data"
    sheet.append(FIXTURE["sf133"]["headers"])
    for row in FIXTURE["sf133"]["rows"]:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _assert_receipt_bound(result: dict) -> None:
    source_receipt = result["_receipt"]
    envelope = result["_provenance"]
    assert len(source_receipt["raw_content_sha256"]) == 64
    assert len(source_receipt["normalized_content_sha256"]) == 64
    assert source_receipt["retrieval_url"].startswith("https://")
    assert envelope["retrieval_url"] == source_receipt["retrieval_url"]
    assert envelope["retrieval_query"] == source_receipt["retrieval_query"]
    assert envelope["raw_content_sha256"] == source_receipt["raw_content_sha256"]
    assert envelope["raw_content_kind"] == source_receipt["raw_content_kind"]
    assert envelope["normalization_version"] == "1"


def test_sf133_is_account_context_and_drops_preparer_identifiers():
    source = OmbSf133Source(fetcher=_fetch(_xlsx()))
    result = source.budget_execution(SourceQuery(
        agencies=["DHS"], keywords=["cybersecurity"], limit=10))
    assert result["_provenance"]["status"] == "complete"
    assert result["total_matched"] == 1
    row = result["items"][0]
    assert row["record_id"].startswith("sf133:")
    assert row["_join_keys"]["agency_identifier"] == "070"
    assert row["_join_keys"]["main_account_code"] == "0100"
    assert row["amounts_by_report_column"]["AMT_AUG"] == 1_750_000
    assert "not an obligation, award, or opportunity" in row["semantics"]
    assert "F2_USER" not in json.dumps(row)
    assert "preparer@example.gov" not in json.dumps(result)
    _assert_receipt_bound(result)


def test_sf133_refuses_unscoped_or_unmapped_and_fails_closed():
    source = OmbSf133Source(fetcher=_fetch(_xlsx()))
    assert source.budget_execution(SourceQuery())["_provenance"]["status"] == "partial"
    unmapped = source.budget_execution(SourceQuery(agencies=["Department of Mars"]))
    assert unmapped["items"] == [] and "no verified" in " ".join(
        unmapped["_provenance"]["limitations"])
    failed = OmbSf133Source(fetcher=_dead).budget_execution(
        SourceQuery(agencies=["DHS"]))
    assert failed["items"] == []
    assert failed["_provenance"]["status"] == "failed"


def test_sf133_skips_unsupported_lead_and_uses_later_supported_agency():
    source = OmbSf133Source(fetcher=_fetch(_xlsx()))
    result = source.budget_execution(SourceQuery(
        agencies=["Cybersecurity and Infrastructure Security Agency", "DHS"],
        keywords=["cybersecurity"], limit=10))
    assert result["_provenance"]["status"] == "complete"
    assert result["items"]
    limits = " ".join(result["_provenance"]["limitations"])
    assert "skipped unsupported" in limits and "DHS" in limits


def test_sf133_accepts_production_parenthetical_agency_labels():
    source = OmbSf133Source(fetcher=_fetch(_xlsx()))
    result = source.budget_execution(SourceQuery(
        agencies=[
            "Cybersecurity and Infrastructure Security Agency (CISA)",
            "Department of Homeland Security (DHS)",
        ],
        keywords=["cybersecurity"], limit=10,
    ))
    assert result["_provenance"]["status"] == "complete"
    assert result["items"]
    assert "Department_of_Homeland_Security.xlsx" in result["_receipt"][
        "retrieval_url"]


def test_sba_scorecard_preserves_stated_goals_and_direct_agency_join():
    blob = json.dumps(FIXTURE["sba"]).encode()
    result = SbaProcurementScorecardsSource(fetcher=_fetch(blob)).scorecards(
        SourceQuery(agencies=["7000"], limit=5))
    assert result["total_matched"] == 1
    row = result["items"][0]
    assert row["record_id"] == "sba-scorecard:2025:7000"
    assert row["categories"]["sb"]["prime_goal_stated"] == "29.00%"
    assert row["categories"]["sb"]["subcontract_achievement_stated"] == "35.00%"
    assert row["_join_keys"]["toptier_agency_code"] == "7000"
    assert "not a live opportunity" in row["semantics"]
    assert "<br>" not in row["prime_footnotes"]
    _assert_receipt_bound(result)


def test_sba_scorecard_unscoped_and_transport_failure_are_explicit():
    blob = json.dumps(FIXTURE["sba"]).encode()
    source = SbaProcurementScorecardsSource(fetcher=_fetch(blob))
    assert source.scorecards(SourceQuery())["_provenance"]["status"] == "partial"
    failed = SbaProcurementScorecardsSource(fetcher=_dead).scorecards(
        SourceQuery(agencies=["DHS"]))
    assert failed["_provenance"]["status"] == "failed"


def test_leie_is_review_only_and_does_not_store_dob_or_street_address():
    result = HhsOigLeieSource(
        fetcher=_fetch(FIXTURE["leie_csv"].encode()),
    ).screen(SourceQuery(keywords=["Acme Health"], limit=10))
    assert result["total_matched"] == 1
    row = result["items"][0]
    assert row["business_name"] == "ACME HEALTH SERVICES, INC"
    assert row["npi"] == "1234567890"
    assert row["exclusion_date"] == "2023-05-18"
    assert "manual identity verification" in row["screening_disposition"]
    assert "not automatic" in row["semantics"]
    serialized = json.dumps(result)
    assert "19800101" not in serialized
    assert "PRIVATE STREET" not in serialized
    _assert_receipt_bound(result)


def test_leie_refuses_unscoped_and_records_failure_provenance():
    source = HhsOigLeieSource(fetcher=_fetch(FIXTURE["leie_csv"].encode()))
    assert source.screen(SourceQuery())["_provenance"]["status"] == "partial"
    failed = HhsOigLeieSource(fetcher=_dead).screen(
        SourceQuery(keywords=["Acme"]))
    assert failed["items"] == []
    assert failed["_provenance"]["status"] == "failed"


def test_oversight_scoped_html_normalizes_report_metadata_not_opportunity():
    result = OversightGovReportsSource(
        fetcher=_fetch(FIXTURE["oversight_html"].encode()),
    ).reports(SourceQuery(keywords=["cybersecurity"], limit=10))
    assert result["total_matched"] == 1
    row = result["items"][0]
    assert row["record_id"] == "oversight:DOE-OIG-26-01"
    assert row["agency_reviewed"] == "Department of Energy"
    assert row["submitting_oig"] == "Department of Energy OIG"
    assert row["recommendation_count"] == 5
    assert row["citation_url"].startswith("https://www.oversight.gov/reports/")
    assert "not a procurement opportunity" in row["semantics"]
    assert "search_api_fulltext=cybersecurity" in result["_receipt"]["retrieval_url"]
    _assert_receipt_bound(result)


def test_oversight_refuses_crawl_and_is_fail_soft():
    source = OversightGovReportsSource(
        fetcher=_fetch(FIXTURE["oversight_html"].encode()))
    assert source.reports(SourceQuery())["_provenance"]["status"] == "partial"
    failed = OversightGovReportsSource(fetcher=_dead).reports(
        SourceQuery(keywords=["cybersecurity"]))
    assert failed["_provenance"]["status"] == "failed"


def test_oversight_bounds_large_assessment_query():
    params, capped = oversight_params(SourceQuery(
        keywords=[f"capability term {index}" for index in range(20)],
        agencies=[f"Department {index}" for index in range(10)]))
    assert capped is True
    assert len(params["search_api_fulltext"]) <= 180
    assert len(params["search_api_fulltext"].split(" capability term ")) <= 5


def test_doe_exchange_keeps_noi_and_teaming_semantics_distinct():
    result = DoeEereExchangeSource(
        fetcher=_fetch(FIXTURE["eere_html"].encode()),
    ).announcements(SourceQuery(keywords=["hydrogen"], limit=10))
    assert result["total_matched"] == 2
    by_id = {row["announcement_number"]: row for row in result["items"]}
    noi = by_id["DE-FOA-0003662"]
    assert noi["signal_stage"] == "advance-intent-notice"
    assert noi["contract_opportunity"] is False
    assert noi["_join_keys"]["foa_number"] == "DE-FOA-0003662"
    teaming = by_id["TPL-0000073"]
    assert teaming["signal_stage"] == "teaming-list"
    assert "TeamingPartners.aspx?foaid=" in teaming["teaming_list_url"]
    assert "not funding or contract opportunities" in noi["semantics"]
    _assert_receipt_bound(result)


def test_doe_exchange_refuses_unscoped_and_reports_transport_failure():
    source = DoeEereExchangeSource(
        fetcher=_fetch(FIXTURE["eere_html"].encode()))
    assert source.announcements(SourceQuery())["_provenance"]["status"] == "partial"
    failed = DoeEereExchangeSource(fetcher=_dead).announcements(
        SourceQuery(keywords=["hydrogen"]))
    assert failed["_provenance"]["status"] == "failed"


def test_sam_wage_search_is_reference_enrichment_without_wage_rates():
    blob = json.dumps(FIXTURE["sam_wd"]).encode()
    result = SamWageDeterminationsSource(fetcher=_fetch(blob)).wage_determinations(
        SourceQuery(keywords=["2015-4281"], limit=10))
    assert result["_provenance"]["status"] == "complete"
    row = result["items"][0]
    assert row["record_id"] == "sam-wd:2015-4281"
    assert row["wage_determination_type"] == "SCA"
    assert row["revision_number"] == 38
    assert row["locations"][0]["counties_included"][0]["name"] == "Denver"
    assert row["wage_rates_present"] is False
    assert "not an opportunity" in row["semantics"]
    assert result["_receipt"]["raw_content_kind"] == "application/hal+json"
    _assert_receipt_bound(result)


def test_sam_wage_refuses_unscoped_and_fails_closed():
    blob = json.dumps(FIXTURE["sam_wd"]).encode()
    source = SamWageDeterminationsSource(fetcher=_fetch(blob))
    assert source.wage_determinations(SourceQuery())[
        "_provenance"]["status"] == "partial"
    failed = SamWageDeterminationsSource(fetcher=_dead).wage_determinations(
        SourceQuery(keywords=["2015-4281"]))
    assert failed["items"] == []
    assert failed["_provenance"]["status"] == "failed"


def test_sam_wage_accepts_valid_zero_result_hal_page():
    zero = {"_links": {}, "page": {"size": 0, "totalElements": 0,
                                     "totalPages": 0, "number": 0}}
    result = SamWageDeterminationsSource(
        fetcher=_fetch(json.dumps(zero).encode())) \
        .wage_determinations(SourceQuery(keywords=["no-match"], limit=10))
    assert result["items"] == []
    assert result["total_matched"] == 0
    assert result["_provenance"]["status"] == "complete"
    _assert_receipt_bound(result)


def test_fixture_healthchecks_validate_shapes():
    sources = [
        OmbSf133Source(fetcher=_fetch(_xlsx())),
        SbaProcurementScorecardsSource(
            fetcher=_fetch(json.dumps(FIXTURE["sba"]).encode())),
        HhsOigLeieSource(fetcher=_fetch(FIXTURE["leie_csv"].encode())),
        OversightGovReportsSource(
            fetcher=_fetch(FIXTURE["oversight_html"].encode())),
        DoeEereExchangeSource(fetcher=_fetch(FIXTURE["eere_html"].encode())),
        SamWageDeterminationsSource(
            fetcher=_fetch(json.dumps(FIXTURE["sam_wd"]).encode())),
    ]
    for source in sources:
        ok, detail = source.healthcheck()
        assert ok, f"{source.name}: {detail}"


@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1")
@pytest.mark.parametrize("source", [
    OmbSf133Source(), SbaProcurementScorecardsSource(), HhsOigLeieSource(),
    OversightGovReportsSource(), DoeEereExchangeSource(),
    SamWageDeterminationsSource(),
], ids=lambda source: source.name)
def test_live_healthcheck(source):
    ok, detail = source.healthcheck()
    assert ok, detail
