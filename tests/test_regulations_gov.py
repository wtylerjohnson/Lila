"""Regulations.gov adapter: key gating, mapping, open-comment flag, facts."""
import pytest

from agents.reports.horizon_discovery import REGISTRY as HORIZON_REGISTRY
from agents.reports.facts import build_fact_pack
from tools.api.base import REGISTRY, SourceQuery
from tools.api.regulations_gov import RegulationsGovSource

PAYLOAD = {"data": [
    {"id": "DHS-2026-0042-0001", "attributes": {
        "agencyId": "DHS", "title": "Online Presence Vetting Requirements",
        "documentType": "Proposed Rule",
        "postedDate": "2026-06-20", "docketId": "DHS-2026-0042",
        "openForComment": True, "commentEndDate": "2026-08-15"}},
    {"id": "DOD-2026-0007-0002", "attributes": {
        "agencyId": "DOD", "title": "CTI sharing standards",
        "documentType": "Notice",
        "postedDate": "2026-06-10", "docketId": "DOD-2026-0007",
        "openForComment": False}},
]}


def test_registered():
    assert "regulations_gov" in {s.name for s in REGISTRY.all()}


def test_no_key_health_and_enrich_are_actionable():
    src = RegulationsGovSource(api_key=None)
    src._api_key = None
    ok, detail = src.healthcheck()
    assert not ok and "api.data.gov/signup" in detail
    try:
        src.enrich(SourceQuery(keywords=["vetting"]))
        assert False
    except RuntimeError as e:
        assert "DATA_GOV_API_KEY" in str(e)


def test_enrich_maps_docs_and_comment_flag(monkeypatch):
    import tools.api.regulations_gov as rg
    monkeypatch.setattr(rg, "get_json", lambda url, params=None, **kw: PAYLOAD)
    out = RegulationsGovSource(api_key="k").enrich(SourceQuery(keywords=['"vetting"']))
    docs = out['"vetting"']
    assert docs[0]["comment_open"] and docs[0]["comment_ends"] == "2026-08-15"
    assert docs[0]["url"].endswith("DHS-2026-0042-0001")
    assert docs[0]["agency"] == "DHS"
    assert docs[1]["agency"] == "DOD"
    assert not docs[1]["comment_open"]


def test_schema_error_is_not_a_screened_zero(monkeypatch):
    import tools.api.regulations_gov as rg

    monkeypatch.setattr(rg, "get_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    with pytest.raises(ValueError, match="required data list"):
        RegulationsGovSource(api_key="k").enrich(
            SourceQuery(keywords=["vetting"])
        )

    monkeypatch.setattr(rg, "get_json", lambda *args, **kwargs: {
        "data": [{"message": "maintenance"}]
    })
    with pytest.raises(ValueError, match="omitted id, attributes, or title"):
        RegulationsGovSource(api_key="k").enrich(
            SourceQuery(keywords=["vetting"])
        )

    monkeypatch.setattr(rg, "get_json", lambda *args, **kwargs: {"data": []})
    assert RegulationsGovSource(api_key="k").enrich(
        SourceQuery(keywords=["vetting"])
    ) == {}


def test_http_fixture_preserves_agency_through_horizon_scope(monkeypatch):
    """Only HTTP is stubbed: collector and Horizon mapper remain real."""
    import tools.api.regulations_gov as rg

    monkeypatch.setattr(rg, "get_json", lambda url, params=None, **kw: PAYLOAD)
    collected = RegulationsGovSource(api_key="k").enrich(
        SourceQuery(keywords=["vetting"]))
    rows = HORIZON_REGISTRY.get("regulations_gov").pull({
        "regulations_gov": collected,
    })

    dhs = next(row for row in rows if "Online Presence Vetting" in row["text"])
    assert dhs["scope"] == {"kind": "agency", "agencies": ["DHS"]}


def test_facts_flag_open_comment_windows():
    searches = {"results": {"usaspending.gov": [], "regulations_gov": {"vetting": [
        {"id": "X", "title": "OPV Requirements", "type": "Proposed Rule",
         "posted": "2026-06-20", "docket": "DHS-2026-0042",
         "comment_open": True, "comment_ends": "2026-08-15",
         "url": "https://www.regulations.gov/document/X"}]}}}
    pack = build_fact_pack("Testco", searches=searches, qualify_report={"candidates": []})
    texts = " | ".join(f.text for f in pack.facts)
    assert "Comment period OPEN until 2026-08-15" in texts
    assert "engagement window" in texts
