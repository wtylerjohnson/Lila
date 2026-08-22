"""Deterministic tests for the sba_sbs adapter. No network anywhere below;
the one live smoke at the bottom is opt-in via LILA_LIVE_SMOKE.

Fixture provenance: tests/fixtures/sources/sba_sbs.json is REAL bytes
recorded from POST https://search.certifications.sba.gov/_api/v2/search on
2026-08-08T13:21Z UTC, sent with the app's full default filters object plus
searchProfiles.searchTerm "zero trust" and naics codes
[{"label": "541512", "value": "541512"}] with isPrimary true. The live
response carried 225 results; the fixture preserves the envelope (status,
meili_filter) and the first 14 result records byte-identically, truncated
only to honor the 80 KB fixture budget.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.api.sba_sbs as sba_sbs  # noqa: E402
from tools.api.base import SourceKind, SourceQuery  # noqa: E402
from tools.api.provenance import ProvenanceEnvelope  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "sba_sbs.json"


def _fixture() -> dict:
    with FIXTURE.open() as fh:
        return json.load(fh)


def _install_poster(monkeypatch, response=None, exc=None):
    """Swap the module's post_json for a recording fake (the network seam)."""
    calls = []

    def fake(url, *, json=None, **kw):
        calls.append({"url": url, "json": json, "kw": kw})
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(sba_sbs, "post_json", fake)
    return calls


def _source() -> sba_sbs.SbaSbsSource:
    return sba_sbs.SbaSbsSource()


QUERY = SourceQuery(keywords=["zero trust"], naics_codes=["541512"])


# --------------------------------------------------------------------------- #
# Normalization against the recorded fixture
# --------------------------------------------------------------------------- #
def test_firms_maps_fixture_fields(monkeypatch):
    _install_poster(monkeypatch, response=_fixture())
    payload = _source().firms(QUERY)

    assert "error" not in payload
    assert payload["total_matched"] == 14
    assert len(payload["firms"]) == 14
    first = payload["firms"][0]
    assert first["name"] == "22 NEXUS LLC"
    assert first["uei"] == "GTWSK9WKU318"
    assert first["cage"] == "9H3S5"
    assert first["naics"] == "541512"
    assert first["state"] == "Georgia"
    assert first["certs"][0] == {
        "name": "VOSB",
        "status": "Active",
        "active": True,
        "entrance_date": "2023-04-05",
        "exit_date": "2027-10-05",
    }
    assert {c["name"] for c in first["certs"]} == {"VOSB", "SDVOSB"}
    assert first["capabilities_narrative_head"].startswith(
        "22 Nexus is a SBA Certified SDVOSB")
    assert len(first["capabilities_narrative_head"]) <= 240
    assert first["capabilities_link"] == (
        "https://certifications.sba.gov/capabilities/GTWSK9WKU318")
    # fixture record 2 carries no capability statement: key rides, value None
    assert payload["firms"][1]["capabilities_link"] is None
    assert first["keywords"][0] == "Salesforce"
    assert first["contact"] == "ANDREW DAY"
    assert first["email"] == "andrew@22nexus.com"
    assert first["last_updated"] == "2026-01-22"
    assert first["naics_small"] == ["541512", "541611", "541690"]
    assert first["profile_url"] == (
        "https://search.certifications.sba.gov/profile/GTWSK9WKU318/9H3S5")
    # the server-injected coverage filter is surfaced, not hidden (the
    # recorded capture composed it with the NAICS scope, so containment)
    assert "public_display = true" in payload["server_filter"]
    assert payload["search_url"] == sba_sbs.SEARCH_URL


def test_posted_body_is_the_full_default_contract(monkeypatch):
    """Anything less than the app's full filters object gets HTTP 500, so
    the outgoing body shape IS the contract; pin it."""
    calls = _install_poster(monkeypatch, response=_fixture())
    _source().firms(QUERY)

    assert len(calls) == 1
    assert calls[0]["url"] == sba_sbs.SEARCH_URL
    body = calls[0]["json"]
    assert set(body) == {
        "searchProfiles", "location", "sbaCertifications", "naics",
        "selfCertifications", "keywords", "lastUpdated", "samStatus",
        "qualityAssuranceStandards", "bondingLevels", "businessSize",
        "annualRevenue", "entityDetailId",
    }
    assert body["searchProfiles"]["searchTerm"] == "zero trust"
    assert body["naics"]["codes"] == [{"label": "541512", "value": "541512"}]
    assert body["naics"]["operatorType"] == "Or"
    assert body["lastUpdated"]["date"] == {
        "label": "Anytime", "value": "anytime"}
    assert body["businessSize"]["relationOperator"] == "at-least"


def test_limit_cap_is_disclosed(monkeypatch):
    _install_poster(monkeypatch, response=_fixture())
    payload = _source().firms(
        SourceQuery(keywords=["zero trust"], naics_codes=["541512"], limit=5))

    assert len(payload["firms"]) == 5
    assert payload["total_matched"] == 14
    assert payload["truncated"] is True
    assert "first 5 of 14" in payload["truncated_note"]


def test_provenance_envelope_round_trips(monkeypatch):
    _install_poster(monkeypatch, response=_fixture())
    payload = _source().firms(QUERY)

    prov = payload["_provenance"]
    assert prov["source"] == "sba_sbs"
    assert prov["schema_version"] == 1
    assert prov["status"] == "complete"
    assert prov["retrieval_mode"] == "live"
    assert prov["record_count"] == 14
    assert prov["retrieved_at"] is not None
    assert any("public_display" in note for note in prov["limitations"])
    # the emitted dict must validate back into the canonical envelope
    ProvenanceEnvelope.model_validate(prov)


# --------------------------------------------------------------------------- #
# Behavior at the edges
# --------------------------------------------------------------------------- #
def test_empty_results_is_a_complete_zero(monkeypatch):
    empty = {"status": 200, "results": [], "meili_filter": "public_display = true"}
    _install_poster(monkeypatch, response=empty)
    payload = _source().firms(QUERY)

    assert payload["firms"] == []
    assert payload["total_matched"] == 0
    assert "error" not in payload
    assert payload["_provenance"]["status"] == "complete"
    assert payload["_provenance"]["record_count"] == 0


def test_malformed_rows_dropped_and_counted(monkeypatch):
    good = _fixture()["results"][0]
    mangled = {
        "status": 200,
        "results": [good, "not an object", {"public_display": True}, 42],
        "meili_filter": "public_display = true",
    }
    _install_poster(monkeypatch, response=mangled)
    payload = _source().firms(QUERY)

    assert len(payload["firms"]) == 1
    assert payload["firms"][0]["uei"] == "GTWSK9WKU318"
    prov = payload["_provenance"]
    assert prov["status"] == "partial"
    assert any("3 malformed" in note for note in prov["limitations"])


def test_unscoped_query_refused_without_touching_network(monkeypatch):
    calls = _install_poster(monkeypatch, response=_fixture())
    payload = _source().firms(SourceQuery())

    assert calls == []
    assert payload["firms"] == []
    assert "unscoped query refused" in payload["error"]
    assert payload["_provenance"]["status"] == "failed"


def test_network_failure_returns_honest_payload(monkeypatch):
    _install_poster(monkeypatch, exc=RuntimeError("connection torn down"))
    payload = _source().firms(QUERY)

    assert payload["firms"] == []
    assert "connection torn down" in payload["error"]
    assert payload["_provenance"]["status"] == "failed"
    assert payload["_provenance"]["source"] == "sba_sbs"


def test_unexpected_shape_returns_honest_payload(monkeypatch):
    _install_poster(monkeypatch, response={"status": 200, "results": "nope"})
    payload = _source().firms(QUERY)

    assert payload["firms"] == []
    assert "unexpected response shape" in payload["error"]
    assert payload["_provenance"]["status"] == "failed"


def test_search_is_enrichment_only():
    src = _source()
    assert src.kind == SourceKind.ENRICHMENT
    with pytest.raises(NotImplementedError):
        src.search(QUERY)


def test_healthcheck_failure_shape_with_injected_poster(monkeypatch):
    _install_poster(monkeypatch, exc=RuntimeError("HTTP 500 body drift"))
    ok, detail = _source().healthcheck()
    assert ok is False
    assert "HTTP 500 body drift" in detail


def test_healthcheck_ok_and_probe_stays_tiny(monkeypatch):
    calls = _install_poster(
        monkeypatch,
        response={"status": 200, "results": [],
                  "meili_filter": "public_display = true"})
    ok, detail = _source().healthcheck()
    assert ok is True
    assert "reachable" in detail
    probe_term = calls[0]["json"]["searchProfiles"]["searchTerm"]
    assert probe_term == sba_sbs.HEALTHCHECK_TERM


# --------------------------------------------------------------------------- #
# Opt-in live smoke (never runs in the default suite)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.environ.get("LILA_LIVE_SMOKE"),
    reason="live smoke is opt-in: set LILA_LIVE_SMOKE=1",
)
def test_live_smoke_smallest_possible_query():
    """One real POST with the nonsense probe term: a ~66 byte response that
    proves reachability and that the pinned body contract still parses."""
    payload = _source().firms(SourceQuery(keywords=[sba_sbs.HEALTHCHECK_TERM]))
    assert "error" not in payload
    assert payload["firms"] == []
    assert payload["_provenance"]["status"] == "complete"
