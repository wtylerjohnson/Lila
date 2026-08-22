"""GSA eLibrary MAS vehicle-holders adapter: fixture-driven, no network.

Fixture provenance (recorded, not invented):
    tests/fixtures/sources/gsa_elibrary.csv was fetched 2026-08-08T13:18:46Z
    from https://gsaelibrary.gsa.gov/elib_contracts/schedule_MAS.csv via an
    HTTP Range request (bytes=0-199999; server answered 206 Partial Content,
    Content-Range bytes 0-199999/29947083, Last-Modified Sat, 08 Aug 2026
    09:22:17 GMT), then trimmed to the last quote-balanced record boundary
    under 80KB. Real bytes throughout: the genuine multi-line header row
    (embedded newlines, two trailing blank columns) plus 130 complete
    contractor rows across SINs 238160 / 238320 / 238910 / 314110, legacy
    (GS-07F-...) and current (47QS...) contract-number formats, and two
    vendors holding multiple SINs on one contract.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tools.api.gsa_elibrary as ge
from tools.api.base import SourceKind, SourceQuery
from tools.api.gsa_elibrary import GsaElibrarySource

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "gsa_elibrary.csv"
FIXED_NOW = datetime(2026, 8, 8, 13, 18, 46, tzinfo=timezone.utc)

#: minimal header matching the live column layout (positions 0-18)
_HEADER = (
    '"Large Category","Sub Category","Source","Category","Vendor",'
    '"Contract #","Closed for New Award","Address 1","Address 2","City",'
    '"State","Zip","Country","Phone","Email","URL",'
    '"Current Option Period End Date","Ultimate Contract End Date","SAM UEI"'
)


def _wire(monkeypatch, text):
    monkeypatch.setattr(ge, "get_text", lambda url, **kw: text)
    monkeypatch.setattr(ge, "_utc_now", lambda: FIXED_NOW)


def _fixture_text():
    return FIXTURE.read_text(encoding="utf-8")


def test_adapter_identity_and_enrichment_only():
    src = GsaElibrarySource()
    assert src.name == "gsa_elibrary"
    assert src.kind is SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        src.search(SourceQuery(keywords=["x"]))


def test_vendor_keyword_maps_every_contract_field(monkeypatch):
    """'roofing' hits 9 vendors by name; the first holder (file order) maps
    every assigned field, dates normalize to ISO, and both the payload and
    each holder carry their citation URLs."""
    _wire(monkeypatch, _fixture_text())
    out = GsaElibrarySource().vehicle_holders(SourceQuery(keywords=["roofing"]))

    assert out["total_matched"] == 9
    assert len(out["holders"]) == 9
    assert "truncated" not in out
    assert out["rows_scanned"] == 130 and out["rows_dropped"] == 0
    assert out["source_url"] == ge.CSV_URL
    assert out["browse_url"] == ge.BROWSE_URL

    # BY NAME, NOT BY POSITION. This test checks FIELD MAPPING; ordering is
    # a separate contract (a named-vendor prefix match now outranks an
    # incidental one, so "ROOFING RESOURCES" leads a "roofing" query ahead
    # of "A TEAM PACIFIC ROOFING"). Asserting on holders[0] made a mapping
    # test fail for a ranking change it does not govern.
    first = next(h for h in out["holders"]
                 if h["vendor"] == "A TEAM PACIFIC ROOFING INC")
    assert first == {
        "vendor": "A TEAM PACIFIC ROOFING INC",
        "uei": "H9HTJ3JKMZ74",
        "contract_number": "47QSMS24D00AP",
        "sins": ["238160"],
        "option_end": "2029-07-31",
        "ultimate_end": "2044-07-31",
        "closed_for_new_award": False,
        "email": "jclark@usacom.org",
        "phone": "808-682-8096",
        "matched": ["roofing"],
        "url": ("https://www.gsaelibrary.gsa.gov/ElibMain/contractorInfo.do"
                "?contractNumber=47QSMS24D00AP"
                "&contractorName=A%20TEAM%20PACIFIC%20ROOFING%20INC"
                "&executeQuery=YES"),
    }

    env = out["_provenance"]
    assert env["schema_version"] == 1
    assert env["source"] == "gsa_elibrary"
    assert env["status"] == "complete"
    assert env["retrieval_mode"] == "live"
    assert env["record_count"] == 9
    assert env["retrieved_at"].startswith("2026-08-08T13:18:46")
    assert any("snapshot" in note for note in env["limitations"])


def test_sin_code_and_naics_translation_hit_the_sin_column(monkeypatch):
    """A SIN-shaped keyword filters the SIN column, never vendor names; a
    NAICS code translates to the same lane (rule: adapters translate the
    normalized query, callers never learn per-vendor dialects)."""
    _wire(monkeypatch, _fixture_text())
    src = GsaElibrarySource()

    by_kw = src.vehicle_holders(SourceQuery(keywords=["238320"]))
    assert by_kw["total_matched"] == 9
    assert all("238320" in h["sins"] for h in by_kw["holders"])
    assert all(h["matched"] == ["238320"] for h in by_kw["holders"])

    by_naics = src.vehicle_holders(SourceQuery(naics_codes=["238160"]))
    assert by_naics["total_matched"] == 32
    assert any(h["contract_number"] == "47QSMS24D00AP"
               for h in by_naics["holders"])


def test_sin_prefix_match_and_full_holdings(monkeypatch):
    """'2389' covers the whole SIN family, and a SIN-matched holder still
    lists its FULL witnessed holdings, not only the matched rows: KYA holds
    238160 and 238910 on one contract and both must survive."""
    _wire(monkeypatch, _fixture_text())
    out = GsaElibrarySource().vehicle_holders(SourceQuery(keywords=["2389"]))
    assert out["total_matched"] == 78
    kya = [h for h in out["holders"]
           if h["contract_number"] == "47QSMA20D08P7"]
    assert len(kya) == 1
    assert kya[0]["vendor"] == "KYA SERVICES LLC"
    assert kya[0]["uei"] == "NNWJHV8VLP55"
    assert kya[0]["sins"] == ["238160", "238910"]
    assert kya[0]["matched"] == ["2389"]


def test_multi_sin_rows_aggregate_to_one_holder(monkeypatch):
    """Two fixture rows (same contract, two SINs) return ONE holder."""
    _wire(monkeypatch, _fixture_text())
    out = GsaElibrarySource().vehicle_holders(
        SourceQuery(keywords=["kya services"]))
    assert out["total_matched"] == 1
    holder = out["holders"][0]
    assert holder["contract_number"] == "47QSMA20D08P7"
    assert holder["sins"] == ["238160", "238910"]


def test_no_hits_is_calm_empty(monkeypatch):
    _wire(monkeypatch, _fixture_text())
    out = GsaElibrarySource().vehicle_holders(
        SourceQuery(keywords=["zz never a vendor zz"]))
    assert out["holders"] == []
    assert out["total_matched"] == 0
    assert "error" not in out
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["record_count"] == 0


def test_unscoped_query_refused_before_any_fetch(monkeypatch):
    """No keywords and no NAICS: the adapter must NOT pull the 30MB file."""
    def boom(url, **kw):
        raise AssertionError("unscoped query must never fetch")
    monkeypatch.setattr(ge, "get_text", boom)
    monkeypatch.setattr(ge, "_utc_now", lambda: FIXED_NOW)
    out = GsaElibrarySource().vehicle_holders(SourceQuery())
    assert out["holders"] == [] and out["total_matched"] == 0
    env = out["_provenance"]
    assert env["status"] == "partial"
    assert any("unscoped" in note for note in env["limitations"])


def test_malformed_rows_dropped_never_fatal(monkeypatch):
    """Short rows and blank lines drop with a count; a marked closed-for-
    new-award flag surfaces as True. (Inline CSV: a unit input for the
    tolerance path, distinct from the recorded fixture.)"""
    text = "\n".join([
        _HEADER,
        'Facilities,Structures,MAS,238160,GOOD ROOFING LLC,47QTEST0001,,'
        'addr,,HONOLULU,HI,96813,USA,808-555-0100,ops@example.com,,'
        '"Jul 31, 2029","Jul 31, 2044",TESTUEI12345',
        "Facilities,Structures,MAS",          # short row -> dropped
        "",                                    # blank line -> ignored
        'Facilities,Structures,MAS,238160,SHUT ROOFING LLC,47QTEST0002,Y,'
        'addr,,RENO,NV,89501,USA,775-555-0100,shut@example.com,,'
        '"Feb 01, 2027","Feb 01, 2032",TESTUEI67890',
    ]) + "\n"
    _wire(monkeypatch, text)
    out = GsaElibrarySource().vehicle_holders(SourceQuery(keywords=["roofing"]))
    assert out["rows_dropped"] == 1
    assert out["rows_scanned"] == 3
    assert out["total_matched"] == 2
    closed = {h["vendor"]: h["closed_for_new_award"] for h in out["holders"]}
    assert closed == {"GOOD ROOFING LLC": False, "SHUT ROOFING LLC": True}
    env = out["_provenance"]
    assert env["status"] == "partial"
    assert any("dropped" in note for note in env["limitations"])


def test_header_drift_is_a_named_failure(monkeypatch):
    """If GSA renames a load-bearing column, the payload says WHICH ONE
    instead of silently matching nothing."""
    drifted = _HEADER.replace('"Contract #"', '"Contract Number"')
    _wire(monkeypatch, drifted + "\n")
    out = GsaElibrarySource().vehicle_holders(SourceQuery(keywords=["roof"]))
    assert out["holders"] == []
    assert "Contract #" in out["error"]
    assert out["_provenance"]["status"] == "failed"


def test_fetch_failure_isolated_to_honest_payload(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("connect timeout to gsaelibrary.gsa.gov")
    monkeypatch.setattr(ge, "get_text", boom)
    monkeypatch.setattr(ge, "_utc_now", lambda: FIXED_NOW)
    out = GsaElibrarySource().vehicle_holders(SourceQuery(keywords=["roof"]))
    assert out["holders"] == [] and out["total_matched"] == 0
    assert "connect timeout" in out["error"]
    env = out["_provenance"]
    assert env["status"] == "failed"
    assert env["source"] == "gsa_elibrary"
    assert env["record_count"] == 0


def test_cap_disclosed_at_200_preserves_arrival_order(monkeypatch):
    buf = io.StringIO()
    buf.write(_HEADER + "\n")
    writer = csv.writer(buf)
    for n in range(210):
        writer.writerow([
            "Facilities", "Structures", "MAS", "238160",
            "CAP VENDOR {:03d}".format(n), "47QCAP{:04d}".format(n), "",
            "addr", "", "AUSTIN", "TX", "78701", "USA", "512-555-0100",
            "cap{}@example.com".format(n), "", "Jul 31, 2029",
            "Jul 31, 2044", "CAPUEI{:06d}".format(n),
        ])
    _wire(monkeypatch, buf.getvalue())
    out = GsaElibrarySource().vehicle_holders(
        SourceQuery(keywords=["cap vendor"]))
    assert out["total_matched"] == 210
    assert len(out["holders"]) == 200
    assert out["truncated"] is True
    assert "first 200 of 210" in out["truncated_note"]
    assert out["holders"][0]["vendor"] == "CAP VENDOR 000"
    assert out["holders"][-1]["vendor"] == "CAP VENDOR 199"
    assert out["_provenance"]["record_count"] == 200


def test_healthcheck_probes_range_and_reports_reality(monkeypatch):
    seen = {}

    def fake_get_text(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        return _fixture_text()[:2048]

    monkeypatch.setattr(ge, "get_text", fake_get_text)
    ok, detail = GsaElibrarySource().healthcheck()
    assert ok is True and "reachable" in detail
    assert seen["url"] == ge.CSV_URL
    assert seen["headers"] == {"Range": "bytes=0-2047"}

    monkeypatch.setattr(ge, "get_text",
                        lambda url, **kw: "<html>maintenance</html>")
    ok, detail = GsaElibrarySource().healthcheck()
    assert ok is False and "header" in detail

    def boom(url, **kw):
        raise ConnectionError("dns failure")
    monkeypatch.setattr(ge, "get_text", boom)
    ok, detail = GsaElibrarySource().healthcheck()
    assert ok is False and "dns failure" in detail


@pytest.mark.skipif(not os.environ.get("LILA_LIVE_SMOKE"),
                    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1")
def test_live_smoke_smallest_probe():
    """Smallest possible live touch: the 2KB Range healthcheck probe."""
    ok, detail = GsaElibrarySource().healthcheck()
    assert ok, detail


def test_a_named_vendor_outranks_incidental_matches(monkeypatch):
    """THE CAP TRUNCATES ALPHABETICALLY, so ordering decides what survives.

    Measured 2026-08-08: a caller asked for ten named rivals plus capability
    terms and got "1901 GROUP", "3AM INNOVATIONS", "4CLICKS" while ALTANA,
    RESILINC and SPHERA fell past row 200. The lane then reported zero
    rivals holding a vehicle, which was false. A vendor whose name STARTS
    with a queried term is the caller's named firm; one that merely contains
    it is incidental.
    """
    _wire(monkeypatch, _fixture_text())
    out = GsaElibrarySource().vehicle_holders(
        SourceQuery(keywords=["roofing"], limit=50))
    vendors = [h["vendor"] for h in out["holders"]]
    lead = vendors.index("ROOFING RESOURCES INC")
    incidental = vendors.index("A TEAM PACIFIC ROOFING INC")
    assert lead < incidental, (
        "a prefix match is the named firm; it must not lose to alphabetical "
        "order")
