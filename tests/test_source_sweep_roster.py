"""Always-swept market lanes (operator order, 2026-08-18).

The operator named the roster: SAM.gov mirrors (HigherGov, GovTribe,
GovChime, G2Xchange, OrangeSlices, GDIC), agency forecast pages, USASpending
proxies, DoD ESI reseller catalogs, and vendor/press sources. These tests
pin the rosters, the receipt-per-site discipline (a walled site is a
recorded error, never a silent skip), and the SAM client-side corroboration
note. Offline doctrine: fetches are monkeypatched, no network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tools.api import esi_catalogs, sam_mirrors, vendor_press
from tools.api.base import SourceQuery
from tools.api.source_catalog import source_spec
from tools.api.source_mesh import SOURCE_IDS


def _status_error(url: str, code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


def test_operator_roster_is_pinned():
    # The operator named these six mirrors on 2026-08-18; dropping or
    # renaming one is an operator decision, not a refactor.
    assert [m["id"] for m in sam_mirrors.MIRRORS] == [
        "highergov", "govtribe", "govchime", "g2xchange", "orangeslices",
        "gdic",
    ]
    assert [c["id"] for c in esi_catalogs.CATALOGS] == [
        "esi_mil", "carahsoft_esi", "immixgroup_esi", "fcn_esi",
    ]
    # Mirrors carry the usaspending proxy role somewhere in the roster.
    assert any("usaspending_proxy" in m["roles"] for m in sam_mirrors.MIRRORS)


def test_roster_lanes_ride_the_required_mesh():
    for source_id in ("sam_mirrors", "esi_reseller_catalogs", "vendor_press"):
        assert source_id in SOURCE_IDS
        spec = source_spec(source_id)
        assert spec.parent_identity == "source_mesh"
    mesh = source_spec("source_mesh")
    for source_id in ("sam_mirrors", "esi_reseller_catalogs", "vendor_press"):
        assert source_id in mesh.coverage_origins


def test_mirror_sweep_receipts_walls_and_matches(monkeypatch):
    fixture = (
        "<html><body>"
        "<a href='/opp/1'>Insider threat monitoring tool RFI</a>"
        "<p>data loss prevention appears twice: data loss prevention</p>"
        "</body></html>"
    )

    def fake_get_text(url, **kwargs):
        if "govtribe" in url:
            raise _status_error(url, 403)
        return fixture

    monkeypatch.setattr(sam_mirrors, "get_text", fake_get_text)
    query = SourceQuery(keywords=["insider threat", "data loss prevention"])
    payload = sam_mirrors.SamMirrors().category_sweep(query)

    assert payload["note"] == sam_mirrors.SAM_CLIENT_SIDE_NOTE
    assert "govtribe: HTTP 403" in payload["errors"]
    walled = [s for s in payload["sites"] if s["mirror"] == "govtribe"][0]
    assert walled["ok"] is False and walled["http_status"] == 403
    hits = {(r["mirror"], r["term"]): r["occurrences"]
            for r in payload["records"]}
    assert hits[("highergov", "data loss prevention")] == 2
    assert hits[("highergov", "insider threat")] == 1
    ok_site = [s for s in payload["sites"] if s["mirror"] == "highergov"][0]
    assert ok_site["boundary_complete"] is True
    assert ok_site["sample_titles"] == ["Insider threat monitoring tool RFI"]
    # every site row carries the receipt fields
    for site in payload["sites"]:
        assert site["url_queried"].startswith("http")
        assert site["retrieved_at"]


def test_esi_catalog_challenge_stub_is_a_named_wall(monkeypatch):
    def fake_get_text(url, **kwargs):
        if "esi.mil" in url:
            return "<html><script>window['loaderConfig']='/TSPD/'</script></html>"
        if "immixgroup" in url:
            raise _status_error(url, 403)
        return "<html><body>Varonis DoD ESI agreement N6600122A0080</body></html>"

    monkeypatch.setattr(esi_catalogs, "get_text", fake_get_text)
    query = SourceQuery(keywords=["Varonis", "Netwrix"])
    payload = esi_catalogs.EsiResellerCatalogs().agreements(query)

    assert "esi_mil: challenge-walled" in payload["errors"]
    assert "immixgroup_esi: HTTP 403" in payload["errors"]
    assert payload["fallback_note"] == esi_catalogs.USASPENDING_IDV_FALLBACK
    hits = {(r["catalog"], r["term"]) for r in payload["records"]}
    assert ("carahsoft_esi", "Varonis") in hits
    assert ("carahsoft_esi", "Netwrix") not in hits


def test_vendor_press_reads_roster_and_receipts_missing_pages(
        monkeypatch, tmp_path):
    roster = {
        "version": 1,
        "entries": [
            {"vendor": "Varonis", "url": "https://example.com/press"},
            {"vendor": "Everfox", "url": "https://example.com/gone"},
        ],
    }
    path = tmp_path / "vendor_press_sources.json"
    path.write_text(json.dumps(roster), encoding="utf-8")
    monkeypatch.setenv("LILA_VENDOR_PRESS_ROSTER", str(path))

    def fake_get_text(url, **kwargs):
        if url.endswith("/gone"):
            raise _status_error(url, 404)
        return ("<html><h2>Varonis achieves FedRAMP High authorization</h2>"
                "<h2>Quarterly earnings call scheduled</h2></html>")

    monkeypatch.setattr(vendor_press, "get_text", fake_get_text)
    payload = vendor_press.VendorPress().press_items(
        SourceQuery(keywords=["data security"]))

    assert payload["roster_size"] == 2
    assert payload["boundary_complete"] is True
    heads = [r["headline"] for r in payload["records"]]
    assert heads == ["Varonis achieves FedRAMP High authorization"]
    assert "Everfox: HTTP 404" in payload["errors"]


def test_vendor_press_empty_roster_is_an_error_not_a_clean_zero(
        monkeypatch, tmp_path):
    path = tmp_path / "missing.json"
    monkeypatch.setenv("LILA_VENDOR_PRESS_ROSTER", str(path))
    payload = vendor_press.VendorPress().press_items(SourceQuery())
    assert payload["records"] == []
    assert payload["errors"] == ["vendor press roster missing or empty"]


def test_shipped_roster_file_names_the_client_and_rivals():
    entries = vendor_press.load_roster()
    vendors = {e["vendor"] for e in entries}
    assert {"Varonis", "BigID", "Netwrix", "Cyera", "Everfox", "Forcepoint",
            "Proofpoint"} <= vendors


def test_no_em_dashes_in_roster_strings():
    # R9: no em dashes in any output-reachable string.
    blobs = [json.dumps(sam_mirrors.MIRRORS), json.dumps(esi_catalogs.CATALOGS),
             sam_mirrors.SAM_CLIENT_SIDE_NOTE,
             esi_catalogs.USASPENDING_IDV_FALLBACK,
             vendor_press.ROSTER_PATH.read_text(encoding="utf-8")]
    for blob in blobs:
        assert "—" not in blob


def test_walled_mirror_projects_partial_through_the_mesh_receipt():
    # A payload with errors must never read as a clean success in the
    # coverage band (source-mesh disclosure rules, 2026-07-30).
    from tools.api.source_mesh import _receipt
    payload = {"records": [], "sites": [], "errors": ["govtribe: HTTP 403"]}
    row = _receipt("sam_mirrors", payload, SourceQuery())
    assert row["status"] == "partial"
