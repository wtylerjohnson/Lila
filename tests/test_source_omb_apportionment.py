"""Deterministic tests for the omb_apportionment adapter. No network below;
the one live smoke at the bottom is opt-in via LILA_LIVE_SMOKE.

Fixture provenance: tests/fixtures/sources/omb_apportionment.json is REAL
bytes recorded live on 2026-08-08 from https://apportionment-public.max.gov/.
"index_html" holds ten anchor elements copied verbatim from the landing-page
index (fetched 17:22:58Z UTC, HTTP 200, 19,678,018 bytes): five SBA JSON
files across FY2026/FY2025, one COE JSON file, the two real Department of
Agriculture stem-variant JSON files (missing TAFS; Account= run together
with the date), one PDF and one XLSX link that the adapter must ignore.
"files" holds three complete per-TAFS JSON documents byte-preserved from
their URLs: FY2026 SBA TAFS 073-X-4280 iteration 1 (17:21Z, 6,430 bytes),
FY2026 SBA TAFS 073-X-0200 iteration 1 (17:27:55Z, 8,827 bytes), and FY2025
SBA TAFS 073-X-0100 iteration 4 (17:25Z, 11,225 bytes). Trimming honors the
80 KB fixture budget; the full index is 19.7 MB.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.api.omb_apportionment as omb  # noqa: E402
from tools.api.base import SourceKind, SourceQuery  # noqa: E402
from tools.api.provenance import ProvenanceEnvelope  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "omb_apportionment.json"

URL_4280 = (
    "https://apportionment-public.max.gov/Fiscal%20Year%202026/"
    "Small%20Business%20Administration/JSON/FY2026_Agency%3DSBA_Bureau%3DSBA_"
    "TAFS%3D073-X-4280_Iteration%3D1_2025-10-01-11.45.json")


def _fixture() -> dict:
    with FIXTURE.open() as fh:
        return json.load(fh)


def _install_net(monkeypatch, index_html=None, files=None, index_exc=None):
    """Swap the module's get_text/get_json for recording fakes (the network
    seam). get_json raises for any URL absent from the recorded files map."""
    text_calls, json_calls = [], []

    def fake_text(url, **kw):
        text_calls.append({"url": url, "kw": kw})
        if index_exc is not None:
            raise index_exc
        return index_html

    def fake_json(url, **kw):
        json_calls.append({"url": url, "kw": kw})
        if not files or url not in files:
            raise RuntimeError(f"no recorded file for {url}")
        return files[url]

    monkeypatch.setattr(omb, "get_text", fake_text)
    monkeypatch.setattr(omb, "get_json", fake_json)
    return text_calls, json_calls


def _install_fixture(monkeypatch):
    fx = _fixture()
    return _install_net(monkeypatch, index_html=fx["index_html"],
                        files=fx["files"])


def _source() -> omb.OmbApportionmentSource:
    return omb.OmbApportionmentSource()


# --------------------------------------------------------------------------- #
# Normalization against the recorded fixture
# --------------------------------------------------------------------------- #
def test_rows_map_fixture_fields(monkeypatch):
    _install_fixture(monkeypatch)
    payload = _source().apportionments(
        SourceQuery(agencies=["SBA"], limit=3))

    assert "error" not in payload
    assert payload["total_matched"] == 5
    assert len(payload["rows"]) == 3

    first = payload["rows"][0]  # newest approval: FY2026 073-X-4280
    assert first["agency"] == "Small Business Administration"
    assert first["agency_abbr"] == "SBA"
    assert first["bureau"] == "Small Business Administration"
    assert first["account"] == (
        "Business Loan and Investment Guaranteed Loan Financing Account")
    assert first["tafs"] == "073-X-4280"
    assert first["fy"] == 2026
    assert first["iteration"] == 1
    assert first["amount"] == 86300000
    assert first["approved_date"] == "2025-10-01"
    assert first["funds_provided_by"].startswith(
        "Funds Provided by Public Law")
    assert first["approver_title"].startswith(
        "Program Associate Director")
    assert first["footnote_count"] == 0
    assert first["file_url"] == URL_4280

    second = payload["rows"][1]
    assert second["tafs"] == "073-X-0200"
    assert second["account"] == "Office of Inspector General"
    assert second["amount"] == 63391460
    assert second["approved_date"] == "2025-09-29"

    third = payload["rows"][2]  # FY2025 073-X-0100 iteration 4
    assert third["fy"] == 2025
    assert third["iteration"] == 4
    assert third["account"] == "Salaries and Expenses"
    assert third["amount"] == 1813850497
    assert third["approved_date"] == "2025-07-24"
    assert third["funds_provided_by"] == (
        "Funds Provided by Public Law 118-158, 119-4, and Actual Carryover")
    assert third["footnote_count"] == 2

    assert payload["index_url"] == omb.INDEX_URL


def test_agency_term_lanes_folder_abbreviation_and_cgac(monkeypatch):
    _install_fixture(monkeypatch)
    src = _source()
    for query in (
        SourceQuery(agencies=["small business"], limit=1),
        SourceQuery(agencies=["073"], limit=1),
        SourceQuery(keywords=["SBA"], limit=1),  # keywords fallback lane
    ):
        payload = src.apportionments(query)
        assert payload["total_matched"] == 5
        assert payload["rows"][0]["agency"] == "Small Business Administration"


def test_cap_is_disclosed_and_order_is_newest_first(monkeypatch):
    _install_fixture(monkeypatch)
    payload = _source().apportionments(SourceQuery(agencies=["SBA"], limit=2))

    assert [r["tafs"] for r in payload["rows"]] == ["073-X-4280", "073-X-0200"]
    assert payload["total_matched"] == 5
    assert payload["truncated"] is True
    assert "first 2 of 5" in payload["truncated_note"]


def test_approval_date_window(monkeypatch):
    _install_fixture(monkeypatch)
    payload = _source().apportionments(
        SourceQuery(agencies=["SBA"], posted_from=date(2025, 9, 1), limit=8))

    assert payload["total_matched"] == 2
    assert {r["tafs"] for r in payload["rows"]} == {
        "073-X-4280", "073-X-0200"}


def test_per_row_fetch_failure_keeps_filename_fields(monkeypatch):
    """Rows 4 and 5 (0100 iterations 2 and 1) have no recorded file, so the
    per-file GET fails; the row survives on filename metadata and the cut is
    named in provenance."""
    _install_fixture(monkeypatch)
    payload = _source().apportionments(SourceQuery(agencies=["SBA"], limit=5))

    assert len(payload["rows"]) == 5
    failed = [r for r in payload["rows"] if "fetch_error" in r]
    assert len(failed) == 2
    assert {r["iteration"] for r in failed} == {1, 2}
    for row in failed:
        assert row["tafs"] == "073-X-0100"
        assert row["amount"] is None
        assert row["approved_date"] is not None  # filename token date
    prov = payload["_provenance"]
    assert prov["status"] == "partial"
    assert any("failed the per-file fetch" in n for n in prov["limitations"])


def test_stem_variants_parse_via_tolerant_ladder(monkeypatch):
    """The two real Agriculture variants: Account= run together with the
    date, and a bureau-level file with no account token at all."""
    _install_fixture(monkeypatch)
    payload = _source().apportionments(SourceQuery(agencies=["AG"], limit=8))

    assert payload["total_matched"] == 2
    first, second = payload["rows"]
    assert first["agency"] == "Department of Agriculture"
    assert first["tafs"] == "012-0600"  # split off the run-together date
    assert first["iteration"] is None
    assert first["approved_date"] == "2025-09-07"
    assert second["tafs"] is None  # bureau-level file
    assert second["bureau"] == "FSA"
    assert all("fetch_error" in r for r in payload["rows"])


def test_unparseable_stem_dropped_and_counted(monkeypatch):
    """Synthetic garbage stem (clearly not recorded data): the entry drops,
    the count rides provenance, nothing raises."""
    fx = _fixture()
    html = fx["index_html"] + (
        '\n<a href="/Fiscal%20Year%202026/Nowhere/JSON/garbage.json" '
        'download>garbage.json</a>')
    _install_net(monkeypatch, index_html=html, files=fx["files"])
    payload = _source().apportionments(SourceQuery(agencies=["SBA"], limit=1))

    assert payload["total_matched"] == 5
    prov = payload["_provenance"]
    assert prov["status"] == "partial"
    assert any("1 index filenames unparseable" in n
               for n in prov["limitations"])


def test_provenance_envelope_round_trips(monkeypatch):
    _install_fixture(monkeypatch)
    payload = _source().apportionments(SourceQuery(agencies=["SBA"], limit=3))

    prov = payload["_provenance"]
    assert prov["source"] == "omb_apportionment"
    assert prov["schema_version"] == 1
    assert prov["status"] == "complete"
    assert prov["retrieval_mode"] == "live"
    assert prov["record_count"] == 3
    assert prov["retrieved_at"] is not None
    assert prov["data_as_of"] == "2025-10-01"
    assert any("landing-page index" in n for n in prov["limitations"])
    assert any("court order" in n for n in prov["limitations"])
    ProvenanceEnvelope.model_validate(prov)


# --------------------------------------------------------------------------- #
# Behavior at the edges
# --------------------------------------------------------------------------- #
def test_empty_match_is_a_complete_zero(monkeypatch):
    _install_fixture(monkeypatch)
    payload = _source().apportionments(
        SourceQuery(agencies=["zzqxv no such agency"]))

    assert payload["rows"] == []
    assert payload["total_matched"] == 0
    assert "error" not in payload
    assert payload["_provenance"]["status"] == "complete"
    assert payload["_provenance"]["record_count"] == 0


def test_unscoped_query_refused_without_touching_network(monkeypatch):
    text_calls, json_calls = _install_fixture(monkeypatch)
    payload = _source().apportionments(SourceQuery())

    assert text_calls == [] and json_calls == []
    assert payload["rows"] == []
    assert "unscoped query refused" in payload["error"]
    assert payload["_provenance"]["status"] == "failed"


def test_index_failure_returns_honest_payload(monkeypatch):
    _install_net(monkeypatch, index_exc=RuntimeError("connection torn down"))
    payload = _source().apportionments(SourceQuery(agencies=["SBA"]))

    assert payload["rows"] == []
    assert "connection torn down" in payload["error"]
    assert payload["_provenance"]["status"] == "failed"
    assert payload["_provenance"]["source"] == "omb_apportionment"


def test_search_is_enrichment_only():
    src = _source()
    assert src.kind == SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        src.search(SourceQuery(agencies=["SBA"]))


def test_healthcheck_failure_shape_with_injected_fetcher(monkeypatch):
    _install_net(monkeypatch, index_exc=RuntimeError("HTTP 503 blocked"))
    ok, detail = _source().healthcheck()
    assert ok is False
    assert "HTTP 503 blocked" in detail


def test_healthcheck_probe_is_ranged_and_tiny(monkeypatch):
    text_calls, _ = _install_net(
        monkeypatch,
        index_html="<title>Approved Apportionments</title>")
    ok, detail = _source().healthcheck()
    assert ok is True
    assert "reachable" in detail
    assert text_calls[0]["kw"]["headers"] == {"Range": "bytes=0-2047"}


# --------------------------------------------------------------------------- #
# Opt-in live smoke (never runs in the default suite)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.environ.get("LILA_LIVE_SMOKE"),
    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1",
)
def test_live_smoke_smallest_possible_probe():
    """One ranged 2 KB GET of the landing page: proves reachability and that
    the index head still names the apportionment site, without pulling the
    19.7 MB body."""
    ok, detail = _source().healthcheck()
    assert ok is True
    assert "reachable" in detail
