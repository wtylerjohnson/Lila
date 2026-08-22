"""Deterministic tests for the oasis_plus_holders enrichment adapter.

FIXTURE PROVENANCE (recorded, not invented): every grid row in
tests/fixtures/sources/oasis_plus_holders.json was sampled verbatim from the
real weekly workbook fetched from
https://www.gsa.gov/system/files/OASIS%2B%20Contractor%20list%2008062026.xlsx
(resolved from the OASIS+ landing page, HTTP 200, 5,483,828 bytes) at
2026-08-08T13:19:36Z. Sampling per project family: 2 Active + 1 Dormant
contract-information rows, the family-sheet rows for those contract numbers,
2 real WOSB family-sheet-only rows (the thin-join caveat), and the Mapping
rows for the sampled SINs. No network in these tests: fetchers are injected;
the one live smoke test is opt-in via LILA_LIVE_SMOKE=1.
"""

from __future__ import annotations

import io
import json
import os
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.api.base import SourceKind, SourceQuery  # noqa: E402
from tools.api.oasis_plus_holders import (  # noqa: E402
    LANDING_URL,
    OasisPlusHoldersSource,
    _grids_from_xlsx,
    _holders_from_grids,
    _resolve_list_url,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures", "sources", "oasis_plus_holders.json")
LIST_URL = ("https://www.gsa.gov/system/files/"
            "OASIS%2B%20Contractor%20list%2008062026.xlsx")
LANDING_HTML = ('<html><body><a href="%s">OASIS+ Contractor list '
                "[XLSX]</a></body></html>" % LIST_URL)


def _fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _grids() -> dict:
    return _fixture()["sheets"]


def _xlsx_bytes(grids: dict) -> bytes:
    """Re-materialize the recorded grids as a real XLSX (exercises the
    binary parse leg without any network)."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in grids.items():
        sheet = workbook.create_sheet(title=name.strip()[:31])
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _source(grids=None, landing_html=LANDING_HTML,
            fetch_text=None, fetch_bytes=None) -> OasisPlusHoldersSource:
    payload = _xlsx_bytes(grids if grids is not None else _grids())
    calls = {"bytes": 0}

    def fake_text(url, **kw):
        return landing_html

    def fake_bytes(url, **kw):
        calls["bytes"] += 1
        return payload

    source = OasisPlusHoldersSource(
        fetch_text=fetch_text or fake_text,
        fetch_bytes=fetch_bytes or fake_bytes)
    source._test_calls = calls
    return source


# --------------------------------------------------------------------------- #
# fixture integrity + normalize path
# --------------------------------------------------------------------------- #
def test_fixture_carries_recorded_provenance():
    recorded = _fixture()["_recorded"]
    assert recorded["source_url"].endswith(".xlsx")
    assert recorded["retrieved_at"].startswith("2026-08-08")
    assert "gsa.gov" in recorded["landing_url"]


def test_field_mapping_on_recorded_rows():
    holders, meta = _holders_from_grids(_grids())
    assert meta["data_as_of"] == "2026-08-06"  # from the preamble row
    zemitek = next(h for h in holders
                   if h["contract_number"] == "47QRCA25DA001")
    assert zemitek["vendor"] == "ZEMITEK LLC"
    assert zemitek["uei"] == "MKFJCFSVQ9B8"
    assert zemitek["primary_naics"] == "541715"
    assert zemitek["project_id"] == "OAS+8A"
    assert zemitek["family"] == "OASIS+ 8(a)"
    assert zemitek["status"] == "Active"
    assert zemitek["dormant"] is False
    assert zemitek["info_joined"] is True
    assert "Management and Advisory" in zemitek["domains"]
    roles = {p["role"]: p for p in zemitek["pocs"]}
    assert roles["COCM"]["email"] == "rcaldas@zemitek.com"
    assert roles["COPM"]["name"] == "Charles La Duca"


def test_sdvosb_sentinel_domains_resolved_via_sin():
    """SDVOSB sheet's Domain column holds 'OASIS+VO'; real domains come back
    through the SIN domain-code join against the populated sheets. Verified
    against the live file: Dormant holders can have ZERO family-sheet rows
    (e.g. 47QRCA24DV004), so those legitimately keep an empty domain list."""
    holders, meta = _holders_from_grids(_grids())
    sdvosb = [h for h in holders if h["family"] == "OASIS+ SDVOSB"]
    assert sdvosb, "fixture must carry SDVOSB holders"
    assert meta["unresolved_domain_rows"] == 0
    with_rows = [h for h in sdvosb if h["naics"]]
    assert with_rows, "fixture must carry SDVOSB holders with family rows"
    for holder in with_rows:
        assert holder["domains"], holder["contract_number"]
    assert all("OASIS+VO" not in h["domains"] for h in sdvosb)
    dormant_only = next(h for h in sdvosb
                        if h["contract_number"] == "47QRCA24DV004")
    assert dormant_only["dormant"] is True
    assert dormant_only["domains"] == []  # absent from the family sheet


def test_wosb_family_sheet_only_rows_become_thin_holders():
    """435 of 446 WOSB contracts have no contract-information row; they stay
    in the roster as thin holders with the gap stated, never dropped."""
    holders, _ = _holders_from_grids(_grids())
    thin = {h["contract_number"]: h for h in holders if not h["info_joined"]}
    assert "47QRCA24DW087" in thin and "47QRCA25DW110" in thin
    for holder in thin.values():
        assert holder["uei"] is None
        assert holder["status"] is None
        assert holder["family"] == "OASIS+ WOSB"
        assert holder["domains"]


def test_dormant_status_flagged():
    holders, _ = _holders_from_grids(_grids())
    dormant = [h for h in holders if h["dormant"]]
    assert dormant, "fixture deliberately includes Dormant rows"
    assert all(h["status"] == "Dormant" for h in dormant)


# --------------------------------------------------------------------------- #
# holders(query) end to end, fetchers injected
# --------------------------------------------------------------------------- #
def test_holders_end_to_end_with_provenance_envelope():
    source = _source()
    out = source.holders(SourceQuery(keywords=["zemitek"]))
    assert [h["vendor"] for h in out["holders"]] == ["ZEMITEK LLC"]
    assert out["total_matched"] == 1
    assert out["list_url"] == LIST_URL
    assert out["landing_url"] == LANDING_URL
    assert out["data_last_updated"] == "2026-08-06"
    assert out["summary"]["total_holders"] == 19
    assert out["summary"]["dormant"] == 5
    assert out["summary"]["family_sheet_only"] == 2
    envelope = out["_provenance"]
    assert envelope["source"] == "oasis_plus_holders"
    assert envelope["status"] == "complete"
    assert envelope["retrieval_mode"] == "live"
    assert envelope["record_count"] == len(out["holders"])
    assert envelope["data_as_of"] == "2026-08-06"
    assert envelope["retrieved_at"]
    assert not any("_blob" in h for h in out["holders"])
    # weekly-file memo: a second query re-parses nothing
    source.holders(SourceQuery(keywords=["intelligence"]))
    assert source._test_calls["bytes"] == 1


def test_keyword_matches_domain_language():
    source = _source()
    out = source.holders(SourceQuery(keywords=["intelligence"]))
    assert out["holders"]
    for holder in out["holders"]:
        assert "intelligence" in (
            " ".join(holder["domains"]) + " " + holder["vendor"]).lower()


def test_naics_filter_narrows():
    source = _source()
    out = source.holders(SourceQuery(naics_codes=["541715"]))
    vendors = {h["vendor"] for h in out["holders"]}
    assert "ZEMITEK LLC" in vendors
    for holder in out["holders"]:
        own = [holder["primary_naics"] or ""] + holder["naics"]
        assert any(code.startswith("541715") for code in own)


def test_empty_result_is_calm_not_failed():
    source = _source()
    out = source.holders(SourceQuery(keywords=["zzz-no-such-vendor-xyz"]))
    assert out["holders"] == []
    assert out["total_matched"] == 0
    assert "error" not in out
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["record_count"] == 0
    assert out["summary"]["total_holders"] == 19  # market context stays


def test_malformed_rows_dropped_and_counted_never_crash():
    grids = _grids()
    info = grids["OASIS+Contract Information"]
    info.append(["Active", "OAS+8A", None, "541611", None])  # no cn/vendor
    info.append(["garbage"])
    grids["8a"].append([None, "Logistics", None, None, None, "20801"])
    grids["8a"].append(["OASIS+ 8(a)"])
    holders, meta = _holders_from_grids(grids)
    assert meta["dropped_rows"] == 4
    assert len(holders) == 19  # junk never becomes a holder
    out = _source(grids=grids).holders(SourceQuery())
    assert any("malformed rows dropped" in note
               for note in out["_provenance"]["limitations"])


def test_fetch_failure_returns_honest_payload_not_raise():
    def down(url, **kw):
        raise ConnectionError("connect timeout to www.gsa.gov")

    source = OasisPlusHoldersSource(fetch_text=down, fetch_bytes=down)
    out = source.holders(SourceQuery(keywords=["zemitek"]))
    assert out["holders"] == []
    assert "connect timeout" in out["error"]
    assert out["_provenance"]["status"] == "failed"
    assert out["_provenance"]["source"] == "oasis_plus_holders"
    assert out["_provenance"]["limitations"]


def test_non_xlsx_body_is_a_named_failure():
    """Link rot returning an HTML error page must fail honestly, not crash
    deep inside openpyxl."""
    source = _source(fetch_bytes=lambda url, **kw: b"<html>404</html>")
    out = source.holders(SourceQuery())
    assert out["_provenance"]["status"] == "failed"
    assert "not an XLSX" in out["error"]


def test_missing_list_link_is_a_named_failure():
    source = _source(landing_html="<html>no downloads today</html>")
    out = source.holders(SourceQuery())
    assert out["_provenance"]["status"] == "failed"
    assert "landing" in out["error"]


def test_healthcheck_reflects_reality():
    ok, detail = _source().healthcheck()
    assert ok is True
    assert "08062026.xlsx" in detail

    ok, detail = _source(
        landing_html="<html>no link</html>").healthcheck()
    assert ok is False and "no contractor list" in detail

    def down(url, **kw):
        raise ConnectionError("boom")

    ok, detail = OasisPlusHoldersSource(fetch_text=down).healthcheck()
    assert ok is False and "unreachable" in detail


def test_enrichment_contract_shape():
    source = _source()
    assert source.kind is SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        source.search(SourceQuery())
    assert _resolve_list_url(LANDING_HTML) == LIST_URL
    # enrich() is the generic engine-facing alias for holders()
    out = source.enrich(SourceQuery(keywords=["zemitek"]))
    assert out["holders"][0]["uei"] == "MKFJCFSVQ9B8"


def test_binary_leg_round_trip():
    """Recorded grids -> real XLSX bytes -> _grids_from_xlsx -> same holders."""
    holders_direct, _ = _holders_from_grids(_grids())
    holders_bytes, _ = _holders_from_grids(_grids_from_xlsx(
        _xlsx_bytes(_grids())))
    assert ([h["contract_number"] for h in holders_bytes]
            == [h["contract_number"] for h in holders_direct])


# --------------------------------------------------------------------------- #
# opt-in live smoke
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: LILA_LIVE_SMOKE=1")
def test_live_smoke_smallest_query():
    source = OasisPlusHoldersSource()
    ok, detail = source.healthcheck()
    assert ok, detail
    out = source.holders(SourceQuery(keywords=["zemitek"], limit=3))
    assert out["_provenance"]["status"] == "complete"
    assert out["summary"]["total_holders"] > 3000
    assert any(h["uei"] for h in out["holders"])
