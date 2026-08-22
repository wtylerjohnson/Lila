"""SBA Active Mentor-Protege Agreements adapter: fixture-driven, no network.

FIXTURE PROVENANCE (recorded, not invented):
  - document page https://www.sba.gov/document/support--active-mentor-protege-agreements
    fetched 2026-08-08T13:19:13Z (302s to
    https://legacy.sba.gov/document/support-active-mentor-protege-agreements);
    the fixture's document_page_html_excerpt is a contiguous real slice of
    that page covering the .xlsx href and the Effective date block.
  - xlsx https://legacy.sba.gov/sites/default/files/2026-08/Active%20Firm%20List%20with%20Details%20including%20Mentors%208-5-26%20%28Exportable%29%20%2823%29%20%28REM01%29.xlsx
    fetched 2026-08-08T13:19:19Z (292,778 bytes; sheet "Active Firms",
    1,957 rows). The fixture grid is the sheet's first 30 rows verbatim plus
    two real deeper rows (sheet rows 43 and 538) that carry genuinely
    invalid UEIs as published.

The recorded grid stands in for the openpyxl binary decode step; that step
itself is exercised by the opt-in live smoke test at the bottom.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

import tools.api.sba_mentor_protege as smp
from tools.api.base import REGISTRY, SourceKind, SourceQuery

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "sba_mentor_protege.json"

PAIR_KEYS = {"mentor", "mentor_uei", "protege", "protege_uei",
             "approved", "naics", "cert_types"}


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _source(monkeypatch, grid, excerpt):
    """Adapter with injected fetchers; recorded grid replaces binary decode."""
    monkeypatch.setattr(smp, "_grid_from_xlsx", lambda data: grid)
    return smp.SbaMentorProtegeSource(
        fetch_text=lambda url, **kw: excerpt,
        fetch_bytes=lambda url, **kw: b"xlsx-bytes-stand-in",
    )


# --------------------------------------------------------------------- #
# fixture integrity
# --------------------------------------------------------------------- #
def test_fixture_is_recorded_and_documented():
    fx = _fixture()
    prov = fx["_fixture_provenance"]
    assert prov["kind"] == "recorded"
    assert prov["xlsx_url"].startswith("https://legacy.sba.gov/sites/")
    assert prov["xlsx_retrieved_at_utc"].endswith("Z")
    assert len(fx["grid"]) == 32
    assert ".xlsx" in fx["document_page_html_excerpt"]


# --------------------------------------------------------------------- #
# grid normalization: preamble skip, column mapping, pivot-column drop
# --------------------------------------------------------------------- #
def test_field_mapping_of_real_first_row():
    pairs, dropped = smp._pairs_from_grid(_fixture()["grid"])
    assert dropped == 0
    assert len(pairs) == 28  # 26 contiguous data rows + 2 real deeper rows
    first = pairs[0]
    assert first == {
        "mentor": "DS Federal, Inc.",
        "mentor_uei": "WHLSDNWNCGJ5",
        "protege": "Kernel Associates, LLC",
        "protege_uei": "HSHVSVEUSKJ1",
        "approved": "2020-08-05",
        "naics": "541519",
        "cert_types": "EDWOSB,WOSB,8(a)",
    }


def test_pivot_column_dropped_and_contract_keys_exact():
    pairs, _ = smp._pairs_from_grid(_fixture()["grid"])
    for pair in pairs:
        assert set(pair.keys()) == PAIR_KEYS  # no Sum of Count leakage


def test_invalid_ueis_null_out_but_pairs_survive():
    pairs, dropped = smp._pairs_from_grid(_fixture()["grid"])
    assert dropped == 0
    by_mentor = {p["mentor"]: p for p in pairs}
    # real row: a company name published in the mentor UEI column
    edge = by_mentor["Edgewater Technical Associates, LLC"]
    assert edge["mentor_uei"] is None
    assert edge["protege"] is not None
    # real row: a 13-character protege UEI as published
    by_protege = {p["protege"]: p for p in pairs}
    pac = by_protege["Pacrim Engineering, Inc."]
    assert pac["protege_uei"] is None
    assert pac["mentor"] is not None


def test_malformed_rows_drop_and_count_never_crash():
    fx = _fixture()
    grid = fx["grid"][:5]  # preamble x2, blank, header, one real data row
    grid.append([None] * 13)  # blank filler: skipped silently
    grid.append(["2020-01-01T00:00:00", 541511, None, None, None,
                 "555-0100", "1 Main St", "VA", None, None, "VA",
                 "8(a)", 1])  # synthetic mangling: content but neither name
    pairs, dropped = smp._pairs_from_grid(grid)
    assert len(pairs) == 1
    assert dropped == 1


def test_headerless_grid_is_a_named_failure():
    with pytest.raises(ValueError, match="header row not found"):
        smp._pairs_from_grid(_fixture()["grid"][:2])


# --------------------------------------------------------------------- #
# page scrape: churning filename, legacy-host resolution, effective date
# --------------------------------------------------------------------- #
def test_relative_href_resolves_on_legacy_host_only():
    fx = _fixture()
    url = smp._xlsx_url_from_page(fx["document_page_html_excerpt"])
    assert url == fx["_fixture_provenance"]["xlsx_url"]
    assert url.startswith("https://legacy.sba.gov/")  # www 404s the file path


def test_effective_date_parsed_from_page():
    fx = _fixture()
    assert smp._effective_date(fx["document_page_html_excerpt"]) == "2026-08-05"
    assert smp._effective_date("<html>no date here</html>") is None


# --------------------------------------------------------------------- #
# enrich(): scoped filtering + provenance envelope
# --------------------------------------------------------------------- #
def test_enrich_keyword_filter_hits_mentor_or_protege(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    out = src.enrich(SourceQuery(keywords=["kernel"]))
    assert out["total_matched"] == 1
    assert out["pairs"][0]["protege"] == "Kernel Associates, LLC"
    # mentor-side keyword lands the same pair
    out2 = src.enrich(SourceQuery(keywords=["ds federal"]))
    assert out2["pairs"][0]["mentor"] == "DS Federal, Inc."


def test_enrich_naics_prefix_and_combined_filters(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    out = src.enrich(SourceQuery(naics_codes=["5415"]))
    assert out["total_matched"] > 0
    assert all(p["naics"].startswith("5415") for p in out["pairs"])
    # keywords AND naics both apply
    none = src.enrich(SourceQuery(keywords=["kernel"], naics_codes=["5416"]))
    assert none["pairs"] == [] and none["total_matched"] == 0


def test_enrich_translates_posted_window_onto_approval_date(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    later = src.enrich(SourceQuery(posted_from=date(2020, 8, 6)))
    assert all(p["approved"] >= "2020-08-06" for p in later["pairs"])
    assert all(p["protege"] != "Kernel Associates, LLC" for p in later["pairs"])
    upto = src.enrich(SourceQuery(posted_to=date(2020, 8, 5)))
    assert any(p["protege"] == "Kernel Associates, LLC" for p in upto["pairs"])
    assert all(p["approved"] <= "2020-08-05" for p in upto["pairs"])


def test_enrich_empty_result_is_honest(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    out = src.enrich(SourceQuery(keywords=["zz no such firm zz"]))
    assert out["pairs"] == []
    assert out["total_matched"] == 0
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["record_count"] == 0


def test_enrich_cap_is_disclosed_never_silent(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    out = src.enrich(SourceQuery(limit=3))
    assert len(out["pairs"]) == 3
    assert out["total_matched"] == 28
    assert out["truncated"] is True
    assert "kept first 3 of 28" in out["truncated_note"]


def test_enrich_provenance_envelope_contract(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    out = src.enrich(SourceQuery(keywords=["kernel"]))
    prov = out["_provenance"]
    assert prov["schema_version"] == 1
    assert prov["source"] == "sba_mentor_protege"
    assert prov["status"] == "complete"
    assert prov["retrieval_mode"] == "live"
    assert prov["retrieved_at"]  # utc stamp present
    assert prov["data_as_of"] == "2026-08-05"  # the page's own Effective date
    assert prov["record_count"] == len(out["pairs"])
    assert any("snapshot-only" in note for note in prov["limitations"])
    assert out["document_page"] == smp.DOC_PAGE_URL
    assert out["xlsx_url"] == fx["_fixture_provenance"]["xlsx_url"]
    assert out["total_agreements"] == 28
    assert out["dropped_rows"] == 0


def test_pairs_for_returns_matched_pairs_only(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"], fx["document_page_html_excerpt"])
    pairs = src.pairs_for(SourceQuery(keywords=["kernel"]))
    assert [p["protege"] for p in pairs] == ["Kernel Associates, LLC"]


# --------------------------------------------------------------------- #
# failure isolation: adapters never raise out
# --------------------------------------------------------------------- #
def test_fetch_failure_returns_failed_envelope_not_raise():
    def boom(url, **kw):
        raise RuntimeError("dns fail: legacy.sba.gov unreachable")

    src = smp.SbaMentorProtegeSource(fetch_text=boom)
    out = src.enrich(SourceQuery(keywords=["kernel"]))
    assert out["pairs"] == []
    assert "dns fail" in out["error"]
    assert out["_provenance"]["status"] == "failed"
    assert out["_provenance"]["source"] == "sba_mentor_protege"
    assert src.pairs_for(SourceQuery()) == []


def test_layout_change_headerless_returns_failed_envelope(monkeypatch):
    fx = _fixture()
    src = _source(monkeypatch, fx["grid"][:2], fx["document_page_html_excerpt"])
    out = src.enrich(SourceQuery())
    assert out["pairs"] == []
    assert "header row not found" in out["error"]
    assert out["_provenance"]["status"] == "failed"


def test_page_without_xlsx_link_returns_failed_envelope():
    src = smp.SbaMentorProtegeSource(
        fetch_text=lambda url, **kw: "<html><body>migrated</body></html>")
    out = src.enrich(SourceQuery())
    assert out["pairs"] == []
    assert ".xlsx" in out["error"]
    assert out["_provenance"]["status"] == "failed"


# --------------------------------------------------------------------- #
# healthcheck: probes reality with an injected fetcher
# --------------------------------------------------------------------- #
def test_healthcheck_truthful_both_ways():
    fx = _fixture()
    ok_src = smp.SbaMentorProtegeSource(
        fetch_text=lambda url, **kw: fx["document_page_html_excerpt"])
    ok, detail = ok_src.healthcheck()
    assert ok and ".xlsx" in detail and "Active Firm List" in detail

    def boom(url, **kw):
        raise RuntimeError("dns fail")

    bad_src = smp.SbaMentorProtegeSource(fetch_text=boom)
    ok, detail = bad_src.healthcheck()
    assert not ok and "dns fail" in detail

    no_link = smp.SbaMentorProtegeSource(
        fetch_text=lambda url, **kw: "<html>empty</html>")
    ok, detail = no_link.healthcheck()
    assert not ok and "no .xlsx link" in detail


# --------------------------------------------------------------------- #
# adapter identity
# --------------------------------------------------------------------- #
def test_adapter_shape_and_registration_state():
    src = smp.SbaMentorProtegeSource()
    assert src.name == "sba_mentor_protege"
    assert src.kind == SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        src.search(SourceQuery())
    from tools.api.source_catalog import SOURCE_BY_ADAPTER
    if "sba_mentor_protege" in SOURCE_BY_ADAPTER:
        # once the lead lands the SourceSpec, import-time registration must hold
        assert "sba_mentor_protege" in {s.name for s in REGISTRY.all()}


# --------------------------------------------------------------------- #
# opt-in live smoke: smallest possible scoped query
# --------------------------------------------------------------------- #
@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1")
def test_live_smoke_smallest_scoped_query():
    src = smp.SbaMentorProtegeSource()
    out = src.enrich(SourceQuery(keywords=["kernel associates"], limit=1))
    prov = out["_provenance"]
    assert prov["source"] == "sba_mentor_protege"
    assert prov["status"] == "complete", out.get("error")
    assert prov["retrieval_mode"] == "live"
    assert isinstance(out["pairs"], list)
    for pair in out["pairs"]:
        hay = " ".join(v for v in (pair["mentor"], pair["protege"]) if v)
        assert "kernel associates" in hay.lower()
