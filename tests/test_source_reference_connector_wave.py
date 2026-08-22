"""Fixture-driven contracts for the four verified reference connectors."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tools.api.abilityone_plims import AbilityOnePlimsSource
from tools.api.base import SourceKind, SourceQuery
from tools.api.cbca_cda_decisions import CbcaCdaDecisionsSource, _parse_index
from tools.api.ecfr_title48 import EcfrTitle48Source
from tools.api.omb_public_budget import OmbPublicBudgetSource, _rows_from_grid

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", [
    "abilityone_plims.json",
    "omb_public_budget.json",
    "cbca_cda_decisions.json",
    "ecfr_title48.json",
])
def test_reference_fixtures_are_recorded_and_source_bound(name):
    provenance = _fixture(name)["_fixture_provenance"]
    assert provenance["kind"] == "recorded"
    assert provenance["retrieved_at_utc"].endswith("Z")
    assert any(str(value).startswith("https://")
               for value in provenance.values())


@pytest.mark.parametrize("source, expected_name", [
    (AbilityOnePlimsSource(), "abilityone_plims"),
    (OmbPublicBudgetSource(), "omb_public_budget_database"),
    (CbcaCdaDecisionsSource(), "cbca_cda_decisions"),
    (EcfrTitle48Source(), "ecfr_title48"),
])
def test_reference_adapters_are_enrichment_only(source, expected_name):
    assert source.name == expected_name
    assert source.kind is SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        source.search(SourceQuery(keywords=["x"]))


# ---------------------------------------------------------------------------
# AbilityOne: mandatory-source / vehicle-access dimension
# ---------------------------------------------------------------------------
def _plims_source(fixture: dict, calls: list | None = None):
    def fetch(_url, *, params, **_kwargs):
        if calls is not None:
            calls.append(dict(params))
        term = (
            params.get("plims_productname")
            or params.get("plims_servicetype")
            or params.get("plims_department")
            or params.get("plims_name")
        )
        if params["type"] == "searchproducts" and term == "calendar":
            return {"results": fixture["product_results"]}
        if params["type"] == "searchservices" and term == "custodial":
            return {"results": fixture["service_results"]}
        return {"results": []}
    return AbilityOnePlimsSource(fetch_json=fetch)


def test_plims_bounded_search_normalizes_products_and_services():
    fixture = _fixture("abilityone_plims.json")
    calls: list[dict] = []
    source = _plims_source(fixture, calls)
    query = SourceQuery(keywords=["calendar", "custodial"], limit=12)

    out = source.procurement_list(query)
    assert len(calls) == 4
    assert all(call["page"] == 1 and call["pageSize"] == 12 for call in calls)
    assert out["total_matched"] == 2
    by_kind = {row["kind"]: row for row in out["items"]}
    assert by_kind["product"]["record_id"] == (
        "abilityone:product:d4f1b8ab-137b-f011-b4cc-001dd803dca7")
    assert by_kind["product"]["nsn"] == "7520-01-496-5475"
    assert by_kind["product"]["nonprofit_agency"] == (
        "Chicago Lighthouse Industries")
    assert by_kind["service"]["cna"] == "SourceAmerica"
    assert by_kind["service"]["ordering_activity"] == "DEPT OF THE NAVY"
    assert by_kind["service"]["effective_date"] == "2015-03-23"
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["record_count"] == 2

    # Content identity never depends on arrival time.
    again = source.procurement_list(query)
    assert [row["record_id"] for row in again["items"]] == [
        row["record_id"] for row in out["items"]]


def test_plims_failure_is_per_lane_and_unscoped_queries_do_not_fetch():
    fixture = _fixture("abilityone_plims.json")

    def partial(_url, *, params, **_kwargs):
        if params["type"] == "searchproducts":
            raise RuntimeError("product route down")
        return {"results": fixture["service_results"]}

    out = AbilityOnePlimsSource(fetch_json=partial).procurement_list(
        SourceQuery(keywords=["custodial"]))
    assert len(out["items"]) == 1
    assert out["_provenance"]["status"] == "partial"
    assert {row["status"] for row in out["_provenance"]["attempts"]} == {
        "failed", "success"}

    def boom(*_args, **_kwargs):
        raise AssertionError("unscoped PLIMS query must not fetch")
    refused = AbilityOnePlimsSource(fetch_json=boom).procurement_list(SourceQuery())
    assert refused["items"] == []
    assert refused["_provenance"]["status"] == "partial"


# ---------------------------------------------------------------------------
# OMB Public Budget Database: budget-context dimension
# ---------------------------------------------------------------------------
def _omb_source(fixture: dict):
    return OmbPublicBudgetSource(
        fetch_text=lambda *_args, **_kwargs: fixture["landing_html"],
        fetch_bytes=lambda *_args, **_kwargs: b"recorded workbook stand-in",
        parse_workbook=lambda _blob: fixture["grid"],
    )


def test_omb_budget_account_normalization_and_amount_semantics():
    fixture = _fixture("omb_public_budget.json")
    rows, dropped = _rows_from_grid(fixture["grid"], budget_year=2027)
    assert dropped == 0 and len(rows) == 3
    assert len({row["record_id"] for row in rows}) == 3

    out = _omb_source(fixture).budget_authority(
        SourceQuery(agencies=["Small Business Administration"], limit=10))
    assert out["budget_year"] == 2027
    assert out["total_matched"] == 3
    first = next(row for row in out["accounts"]
                 if row["account_code"] == "0100"
                 and row["bea_category"] == "Discretionary")
    assert first["treasury_agency_code"] == "73"
    assert first["cgac_agency_code"] == "073"
    assert first["year_amounts"]["2027"] == 260000
    assert first["amount_unit"] == "thousands_usd"
    assert first["url"].endswith("budauth_fy2027.xlsx")
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["retrieval_url"].endswith(
        "budauth_fy2027.xlsx")
    assert len(out["_provenance"]["raw_content_sha256"]) == 64
    assert "not enacted authority" in " ".join(
        out["_provenance"]["limitations"])


def test_omb_budget_refuses_unscoped_and_isolates_fetch_failure():
    fixture = _fixture("omb_public_budget.json")
    refused = _omb_source(fixture).budget_authority(SourceQuery())
    assert refused["accounts"] == []
    assert refused["_provenance"]["status"] == "partial"

    failed = OmbPublicBudgetSource(
        fetch_text=lambda *_args, **_kwargs: fixture["landing_html"],
        fetch_bytes=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("workbook unavailable")),
    ).budget_authority(SourceQuery(agencies=["SBA"]))
    assert failed["accounts"] == []
    assert failed["_provenance"]["status"] == "failed"
    assert "workbook unavailable" in failed["error"]


# ---------------------------------------------------------------------------
# CBCA: post-award dispute / incumbency-risk dimension
# ---------------------------------------------------------------------------
def test_cbca_index_and_bounded_pdf_enrichment():
    fixture = _fixture("cbca_cda_decisions.json")
    parsed = _parse_index(fixture["index_html"])
    assert [row["case_number"] for row in parsed] == ["CBCA 8760", "CBCA 8940"]
    assert parsed[0]["url"].startswith("https://www.cbca.gov/files/decisions/")

    def fetch_bytes(url, **_kwargs):
        return b"precision" if "8760" in url else b"group"

    def extract(blob):
        return (fixture["decision_text"] if blob == b"precision"
                else "CBCA 8940\nGROUP HEALTH INCORPORATED\nv.\nOFFICE OF PERSONNEL MANAGEMENT,\nRespondent.")

    out = CbcaCdaDecisionsSource(
        fetch_text=lambda *_args, **_kwargs: fixture["index_html"],
        fetch_bytes=fetch_bytes,
        extract_pdf_text=extract,
    ).decisions(SourceQuery(agencies=["Department of Agriculture"]))

    assert out["total_matched"] == 1
    row = out["decisions"][0]
    assert row["case_number"] == "CBCA 8760"
    assert row["respondent_agency"] == "DEPARTMENT OF AGRICULTURE"
    assert row["contract_numbers"] == ["1234-AG-TEST-001"]
    assert row["record_id"].startswith("cbca:")
    assert out["pdfs_inspected"] == 2
    assert out["_provenance"]["status"] == "complete"


def test_cbca_one_pdf_failure_does_not_erase_index_hit():
    fixture = _fixture("cbca_cda_decisions.json")
    out = CbcaCdaDecisionsSource(
        fetch_text=lambda *_args, **_kwargs: fixture["index_html"],
        fetch_bytes=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("pdf host unavailable")),
        extract_pdf_text=lambda _blob: "",
    ).decisions(SourceQuery(keywords=["Precision Care"]))
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["appellant"] == "Precision Care Excavation LLC"
    assert out["_provenance"]["status"] == "partial"
    assert out["_provenance"]["attempts"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# eCFR: exact regulatory-requirements dimension
# ---------------------------------------------------------------------------
def test_ecfr_resolves_exact_citation_at_available_version_date():
    fixture = _fixture("ecfr_title48.json")
    calls: list[str] = []

    def fetch_text(url, **_kwargs):
        calls.append(url)
        return fixture["section_xml"]

    out = EcfrTitle48Source(
        fetch_json=lambda *_args, **_kwargs: fixture["titles"],
        fetch_text=fetch_text,
    ).regulations(SourceQuery(keywords=["FAR 9.104-1"]))

    assert calls == [
        "https://www.ecfr.gov/api/versioner/v1/full/2026-08-06/"
        "title-48.xml?section=9.104-1"]
    assert out["version_date"] == "2026-08-06"
    row = out["sections"][0]
    assert row["record_id"] == "ecfr:48:9.104-1"
    assert row["citation"] == "48 CFR 9.104-1"
    assert row["alternate_reference"] == "FAR 9.104-1"
    assert "adequate financial resources" in row["text"]
    assert row["url"].startswith("https://www.ecfr.gov/on/2026-08-06/")
    assert out["_provenance"]["status"] == "complete"


def test_ecfr_honors_point_in_time_and_isolates_citation_failure():
    fixture = _fixture("ecfr_title48.json")

    def fetch_text(url, **_kwargs):
        if "section=52.204-21" in url:
            raise RuntimeError("section unavailable")
        return fixture["section_xml"]

    out = EcfrTitle48Source(
        fetch_json=lambda *_args, **_kwargs: fixture["titles"],
        fetch_text=fetch_text,
    ).regulations(SourceQuery(
        keywords=["48 CFR 9.104-1", "FAR 52.204-21"],
        posted_to=date(2026, 8, 1),
    ))
    assert out["version_date"] == "2026-08-01"
    assert len(out["sections"]) == 1
    assert out["_provenance"]["status"] == "partial"
    assert [attempt["status"] for attempt in out["_provenance"]["attempts"]] == [
        "success", "failed"]

    refused = EcfrTitle48Source(
        fetch_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("free-text query must not fetch")),
    ).regulations(SourceQuery(keywords=["cybersecurity requirements"]))
    assert refused["sections"] == []
    assert refused["_provenance"]["status"] == "partial"
