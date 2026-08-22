"""Deterministic tests for the nih_reporter enrichment adapter.

FIXTURE PROVENANCE (recorded, not invented): the 'response' block in
tests/fixtures/sources/nih_reporter.json is the REAL, untrimmed body
returned by POST https://api.reporter.nih.gov/v2/projects/search at
2026-08-08T17:24:42Z (HTTP 200, 7,593 bytes). The request was exactly the
body rd_contracts() builds for SourceQuery(limit=8) with
org_names=["LEIDOS"]: the N01/N02/N43/N44 contracts-slice criteria,
the slim include_fields projection, sorted project_start_date descending
(8 of 1,254 matching contract projects; Leidos Biomedical Research runs
the Frederick National Laboratory for Cancer Research, so the slice is
live NCI/NIAID task-order data). No network in these tests: the poster is
injected everywhere; the one live smoke test is opt-in via
LILA_LIVE_SMOKE=1.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.api.base import SourceKind, SourceQuery  # noqa: E402
from tools.api.nih_reporter import (  # noqa: E402
    CONTRACT_ACTIVITY_CODES,
    SEARCH_URL,
    NihReporterSource,
    _fiscal_years,
    piid_hint,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures", "sources", "nih_reporter.json")


def _fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _response() -> dict:
    return _fixture()["response"]


def _source(response=None, exc=None):
    """Adapter with an injected poster; records every call it receives."""
    calls = []

    def poster(url, **kw):
        calls.append({"url": url, "body": kw.get("json"), "kw": kw})
        if exc is not None:
            raise exc
        return response

    source = NihReporterSource(poster=poster)
    source._test_calls = calls
    return source


# --------------------------------------------------------------------------- #
# fixture integrity + normalize path
# --------------------------------------------------------------------------- #
def test_fixture_carries_recorded_provenance():
    recorded = _fixture()["_fixture_provenance"]
    assert recorded["retrieval_url"] == SEARCH_URL
    assert recorded["retrieved_at_utc"].startswith("2026-08-08")
    assert recorded["http_status"] == 200
    body = recorded["request_body"]
    assert body["criteria"]["activity_codes"] == list(CONTRACT_ACTIVITY_CODES)
    assert body["limit"] == 8


def test_field_mapping_on_recorded_rows():
    source = _source(response=_response())
    out = source.rd_contracts(SourceQuery(limit=8), org_names=["LEIDOS"])
    assert len(out["projects"]) == 8
    first = out["projects"][0]
    assert first["project_num"] == "75N91019D00024-0-759102500008-1"
    assert first["org"] == "LEIDOS BIOMEDICAL RESEARCH, INC."
    assert first["uei"] == "HV8BH9BPG8Y9"
    assert first["amount"] == 4287020
    assert first["fy"] == 2025
    assert first["ic"] == "NCI"
    assert first["ic_name"] == "National Cancer Institute"
    assert first["title"] == "FY25 TO-C REFURBISH ELEVATORS"
    assert first["pi"] == "BRISCOE, LYNN"  # source carries a trailing space
    assert first["activity_code"] == "N02"
    assert first["funding_mechanism"] == "R and D Contracts"
    assert first["start_date"] == "2025-09-30"
    assert first["end_date"] == "2030-09-27"  # the recompete clock
    assert first["piid_hint"] == "75N91019D00024"  # IDIQ base for the TO
    assert first["url"] == "https://reporter.nih.gov/project-details/11456812"
    second = out["projects"][1]
    assert second["ic"] == "NIAID"
    assert second["activity_code"] == "N01"
    # the whole slice is the contracts funding mechanism, per the source
    assert {p["funding_mechanism"] for p in out["projects"]} == {
        "R and D Contracts"}


def test_truncation_disclosed_against_meta_total():
    out = _source(response=_response()).rd_contracts(SourceQuery(limit=8))
    assert out["total_matched"] == 1254
    assert out["truncated"] is True
    assert "kept first 8 of 1254" in out["truncated_note"]
    assert out["truncated_note"] in out["_provenance"]["limitations"]
    # the API's own 'https:/' quirk is repaired in the public search URL
    assert out["search_url"] == (
        "https://reporter.nih.gov/search/bLKaQ3Hiyk6VR6FM2aCdlQ/projects")


def test_provenance_envelope_present_and_named():
    out = _source(response=_response()).rd_contracts(SourceQuery(limit=8))
    envelope = out["_provenance"]
    assert envelope["source"] == "nih_reporter"
    assert envelope["schema_version"] == 1
    assert envelope["status"] == "complete"
    assert envelope["retrieval_mode"] == "live"
    assert envelope["record_count"] == len(out["projects"]) == 8
    assert envelope["retrieved_at"]
    assert any("N01/N02/N43/N44" in note for note in envelope["limitations"])


# --------------------------------------------------------------------------- #
# query translation
# --------------------------------------------------------------------------- #
def test_request_translation_is_scoped_to_the_contracts_slice():
    source = _source(response=_response())
    source.rd_contracts(
        SourceQuery(keywords=['"genomic sequencing"', "mpox"],
                    posted_from=date(2024, 10, 1),
                    posted_to=date(2025, 9, 30),
                    limit=8),
        org_names=["LEIDOS", "  "])
    call = source._test_calls[0]
    assert call["url"] == SEARCH_URL
    body = call["body"]
    criteria = body["criteria"]
    assert criteria["activity_codes"] == ["N01", "N02", "N43", "N44"]
    assert criteria["org_names"] == ["LEIDOS"]  # blanks dropped
    text = criteria["advanced_text_search"]
    assert text["operator"] == "advanced"
    assert text["search_text"] == '"genomic sequencing" OR "mpox"'
    assert criteria["fiscal_years"] == [2025]  # FY2025 = Oct 2024..Sep 2025
    assert body["limit"] == 8
    assert body["offset"] == 0
    assert body["sort_field"] == "project_start_date"
    assert body["include_fields"]


def test_bare_query_sends_no_empty_criteria():
    source = _source(response=_response())
    source.rd_contracts(SourceQuery())
    criteria = source._test_calls[0]["body"]["criteria"]
    assert set(criteria) == {"activity_codes"}
    assert source._test_calls[0]["body"]["limit"] == 100


def test_limit_capped_at_api_maximum():
    source = _source(response=_response())
    source.rd_contracts(SourceQuery(limit=9999))
    assert source._test_calls[0]["body"]["limit"] == 500


def test_fiscal_year_window_translation():
    # FY spans: Apr 2024 is FY2024, Nov 2025 is FY2026
    assert _fiscal_years(date(2024, 4, 1), date(2025, 11, 2)) == [
        2024, 2025, 2026]
    assert _fiscal_years(None, date(2025, 2, 1)) == [2025]
    assert _fiscal_years(None, None) == []
    open_ended = _fiscal_years(date(2026, 1, 15), None)
    assert open_ended[0] == 2026 and 2026 in open_ended


def test_piid_hint_families_match_the_registry_examples():
    assert piid_hint("75N92025C00010") == "75N92025C00010"
    assert piid_hint("75N91019D00024-0-759102500008-1") == "75N91019D00024"
    # registry-verified legacy join, trailing letter preserved
    assert piid_hint("261201500036I-0-26100009-2") == "HHSN261201500036I"
    # sibling letters are different awards; the letter must survive
    assert piid_hint("261201500036C-1") == "HHSN261201500036C"
    # OD-style numbers match neither documented family: no guess
    assert piid_hint("92021A00792024F00002-P00002-0-1") is None
    assert piid_hint("") is None
    assert piid_hint(None) is None


# --------------------------------------------------------------------------- #
# empty results, malformed rows, failure isolation
# --------------------------------------------------------------------------- #
def test_empty_result_is_calm_not_failed():
    response = _response()
    response["meta"]["total"] = 0
    response["results"] = []
    out = _source(response=response).rd_contracts(
        SourceQuery(keywords=["zzz-no-such-term"]))
    assert out["projects"] == []
    assert out["total_matched"] == 0
    assert "error" not in out
    assert "truncated" not in out
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["record_count"] == 0


def test_malformed_rows_dropped_and_counted_never_crash():
    response = _response()
    response["results"].extend([
        42,                                   # not a mapping
        {"organization": {"org_name": "X"}},  # no project_num
        {"project_num": "   "},               # blank identity
        # damaged sub-blocks degrade to None fields, row survives
        {"project_num": "75N95022C00001", "organization": ["junk"],
         "agency_ic_admin": None, "award_amount": "not-a-number",
         "fiscal_year": "2025", "appl_id": "not-an-int"},
    ])
    out = _source(response=response).rd_contracts(SourceQuery(limit=8))
    assert len(out["projects"]) == 9
    survivor = out["projects"][-1]
    assert survivor["project_num"] == "75N95022C00001"
    assert survivor["org"] is None
    assert survivor["ic"] is None
    assert survivor["amount"] is None
    assert survivor["fy"] is None
    assert survivor["url"] is None
    assert survivor["piid_hint"] == "75N95022C00001"
    assert any("3 malformed result rows dropped" in note
               for note in out["_provenance"]["limitations"])


def test_fetch_failure_returns_honest_payload_not_raise():
    source = _source(exc=ConnectionError("connect timeout to api.reporter"))
    out = source.rd_contracts(SourceQuery(keywords=["mpox"]))
    assert out["projects"] == []
    assert out["total_matched"] == 0
    assert "connect timeout" in out["error"]
    envelope = out["_provenance"]
    assert envelope["source"] == "nih_reporter"
    assert envelope["status"] == "failed"
    assert envelope["retrieval_mode"] == "live"
    assert envelope["limitations"]


def test_non_object_response_is_a_named_failure():
    out = _source(response=["not", "an", "object"]).rd_contracts(SourceQuery())
    assert out["_provenance"]["status"] == "failed"
    assert "non-JSON-object" in out["error"]


def test_healthcheck_reflects_reality():
    ok, detail = _source(response={
        "meta": {"total": 54759}, "results": [{"appl_id": 9795459}],
    }).healthcheck()
    assert ok is True
    assert "54759" in detail

    ok, detail = _source(
        exc=ConnectionError("boom")).healthcheck()
    assert ok is False and "unreachable" in detail

    ok, detail = _source(response={"unexpected": "shape"}).healthcheck()
    assert ok is False and "meta.total" in detail


def test_enrichment_contract_shape():
    source = _source(response=_response())
    assert source.kind is SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        source.search(SourceQuery())
    # enrich() is the engine-facing alias for the keyword-scoped slice
    out = source.enrich(SourceQuery(keywords=["mpox"], limit=8))
    assert out["projects"][0]["uei"] == "HV8BH9BPG8Y9"
    assert out["_provenance"]["source"] == "nih_reporter"


# --------------------------------------------------------------------------- #
# opt-in live smoke
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: LILA_LIVE_SMOKE=1")
def test_live_smoke_smallest_query():
    source = NihReporterSource()
    ok, detail = source.healthcheck()
    assert ok, detail
    out = source.rd_contracts(SourceQuery(limit=1),
                              org_names=["LEIDOS BIOMEDICAL"])
    assert out["_provenance"]["status"] == "complete"
    assert out["projects"], "Leidos Biomedical holds live NIH contracts"
    first = out["projects"][0]
    assert first["project_num"]
    assert first["funding_mechanism"] == "R and D Contracts"
