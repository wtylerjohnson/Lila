"""GovInfo adapter — legislative full-text on the shared data.gov key."""

import pytest

import tools.api.govinfo as gv
from tools.api import REGISTRY, SourceQuery
from tools.api.govinfo import GovInfoSource

PAYLOAD = {
    "count": 2,
    "packages": [
        {"packageId": "BILLS-119hr1234ih", "title": "Cyber Threat Intel Act",
         "collectionCode": "BILLS", "dateIssued": "2026-05-01"},
        {"packageId": "CRPT-119hrpt55", "title": "DHS Appropriations Report",
         "docClass": "CRPT", "lastModified": "2026-04-02"},
    ],
}


def test_registered():
    assert REGISTRY.get("govinfo") is not None


def test_healthcheck_no_key():
    ok, detail = GovInfoSource(api_key=None).healthcheck()
    if "DATA_GOV_API_KEY" in __import__("os").environ:
        pytest.skip("env key present")
    assert not ok and "signup" in detail


def test_enrich_requires_key(monkeypatch):
    monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DATA_GOV_API_KEY"):
        GovInfoSource(api_key=None).enrich(SourceQuery(keywords=["cyber"]))


def test_enrich_parses_and_links(monkeypatch):
    calls = []

    def fake_post(url, *, json, headers, **kw):
        calls.append((url, json, headers))
        return PAYLOAD

    monkeypatch.setattr(gv, "post_json", fake_post)
    out = GovInfoSource(api_key="k").enrich(SourceQuery(keywords=['"threat intelligence"']))
    docs = out['"threat intelligence"']
    assert docs[0]["url"] == "https://www.govinfo.gov/app/details/BILLS-119hr1234ih"
    assert docs[0]["collection"] == "BILLS"
    assert docs[1]["collection"] == "CRPT"  # docClass fallback
    assert docs[1]["date"] == "2026-04-02"  # lastModified fallback
    url, body, headers = calls[0]
    assert url.endswith("/search")
    assert 'collection:(BILLS OR CRPT OR PLAW)' in body["query"]
    assert '"threat intelligence"' in body["query"]
    assert headers["X-Api-Key"] == "k"


def test_enrich_drops_empty_lanes(monkeypatch):
    monkeypatch.setattr(gv, "post_json", lambda *a, **k: {"count": 0, "packages": []})
    out = GovInfoSource(api_key="k").enrich(SourceQuery(keywords=["nichetermxyz"]))
    assert out == {}


def test_schema_error_is_not_a_screened_zero_for_both_govinfo_lanes(monkeypatch):
    monkeypatch.setattr(gv, "post_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    source = GovInfoSource(api_key="k")
    with pytest.raises(ValueError, match="required packages list"):
        source.enrich(SourceQuery(keywords=["cyber"]))
    with pytest.raises(ValueError, match="required packages list"):
        source.funded_demand(["cyber"])

    monkeypatch.setattr(gv, "post_json", lambda *args, **kwargs: {
        "packages": [{"message": "maintenance"}]
    })
    with pytest.raises(ValueError, match="package omitted packageId"):
        source.enrich(SourceQuery(keywords=["cyber"]))
    with pytest.raises(ValueError, match="package omitted packageId"):
        source.funded_demand(["cyber"])

    monkeypatch.setattr(gv, "post_json", lambda *args, **kwargs: {
        "count": 0, "packages": []
    })
    assert source.enrich(SourceQuery(keywords=["cyber"])) == {}
    assert source.funded_demand(["cyber"])["hits"] == {}


def test_search_is_enrichment_only():
    with pytest.raises(NotImplementedError):
        GovInfoSource(api_key="k").search(SourceQuery())


def test_distiller_picks_up_legislative_docs():
    from agents.decisions.research_picture import distill
    d = distill({"govinfo": {"cyber": [{"title": "Bill X", "collection": "BILLS",
                                        "date": "2026-05-01"}]}})
    assert d["legislative_docs"][0]["title"] == "Bill X"
    assert d["legislative_docs"][0]["keyword"] == "cyber"
