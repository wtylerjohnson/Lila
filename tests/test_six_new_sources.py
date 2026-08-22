"""Treasury / GAO legal / CALC+ / EDGAR / FedRAMP / Federal Hierarchy adapters."""


import xml.etree.ElementTree as ET

import pytest

import tools.api.calc_rates as cr
import tools.api.federal_hierarchy as fh
import tools.api.fedramp as fr
import tools.api.gao_legal as gl
import tools.api.sec_edgar as se
import tools.api.treasury_fiscal as tf
from tools.api import REGISTRY, SourceQuery


def test_all_six_registered():
    for name in ("treasury_fiscal", "gao_legal", "calc_rates", "sec_edgar",
                 "fedramp", "federal_hierarchy"):
        assert REGISTRY.get(name) is not None, name


def test_treasury_filters_latest_month_and_agencies(monkeypatch):
    monkeypatch.setattr(tf, "get_json", lambda url, *, params, **kw: {"data": [
        {"record_date": "2026-05-31", "classification_desc": "Cybersecurity and Infrastructure Security Agency:",
         "current_fytd_gross_outly_amt": "2900000000"},
        {"record_date": "2026-05-31", "classification_desc": "Rural Utilities Service:",
         "current_fytd_gross_outly_amt": "100"},
        {"record_date": "2026-05-31", "classification_desc": "Broken Row:",
         "current_fytd_gross_outly_amt": "null"},
        {"record_date": "2026-04-30", "classification_desc": "Old Month CISA:",
         "current_fytd_gross_outly_amt": "999"},
    ]})
    out = tf.TreasuryFiscalSource().enrich(SourceQuery(agencies=["Cybersecurity"]))
    assert out["as_of"] == "2026-05-31"
    assert len(out["matched_lines"]) == 1
    assert out["matched_lines"][0]["fytd_gross_outlays"] == 2.9e9


def test_treasury_schema_error_is_not_a_screened_zero(monkeypatch):
    monkeypatch.setattr(tf, "get_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    with pytest.raises(ValueError, match="required data list"):
        tf.TreasuryFiscalSource().enrich(SourceQuery(agencies=["Treasury"]))

    monkeypatch.setattr(tf, "get_json", lambda *args, **kwargs: {"data": []})
    out = tf.TreasuryFiscalSource().enrich(SourceQuery(agencies=["Treasury"]))
    assert out["as_of"] is None
    assert out["matched_lines"] == []

    monkeypatch.setattr(tf, "get_json", lambda *args, **kwargs: {
        "data": [{"message": "maintenance"}]
    })
    with pytest.raises(ValueError, match="omitted record_date"):
        tf.TreasuryFiscalSource().enrich(SourceQuery(agencies=["Treasury"]))


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>B-423001, Acme Cyber LLC</title><link>https://gao.gov/d1</link>
<pubDate>Thu, 02 Jul 2026</pubDate><description>protest of DHS threat intelligence award</description></item>
<item><title>Appropriations opinion</title><link>https://gao.gov/d2</link>
<pubDate>Wed, 01 Jul 2026</pubDate><description>impoundment question</description></item>
</channel></rss>"""


def test_gao_matches_keywords_and_flags_protests(monkeypatch):
    monkeypatch.setattr(gl, "get_text", lambda url, **kw: RSS)
    out = gl.GaoLegalSource().enrich(SourceQuery(keywords=["threat intelligence"]))
    assert out["feed_total"] == 2
    assert len(out["items"]) == 1
    assert out["items"][0]["protest"] is True
    assert out["items"][0]["matched"] == ["threat intelligence"]


def test_gao_malformed_feed_fails_but_valid_empty_feed_is_clean(monkeypatch):
    monkeypatch.setattr(gl, "get_text", lambda url, **kw: "not XML")
    with pytest.raises(ET.ParseError):
        gl.GaoLegalSource().enrich(SourceQuery(keywords=["networking"]))
    assert gl._parse_items("<rss><channel /></rss>") == []
    with pytest.raises(ValueError, match="not an RSS feed"):
        gl._parse_items("<html><body>maintenance</body></html>")


def test_calc_computes_rate_stats(monkeypatch):
    monkeypatch.setattr(cr, "get_json", lambda url, *, params, **kw: {"results": [
        {"labor_category": "Cyber Analyst", "ceiling_rate": 100.0},
        {"labor_category": "Cyber Analyst II", "ceiling_rate": 150.0},
        {"labor_category": "Bad Row", "ceiling_rate": "n/a"},
        {"labor_category": "Insane Row", "ceiling_rate": 99999},
    ]})
    out = cr.CalcRatesSource(api_key="k").enrich(SourceQuery(keywords=["cyber analyst"]))
    stats = out["cyber analyst"]
    assert stats == {"count": 2, "min": 100.0, "median": 125.0, "max": 150.0}


def test_calc_accepts_current_opensearch_shape_and_keyword_params(monkeypatch):
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append((url, dict(params)))
        return {
            "timed_out": False,
            "_shards": {"total": 5, "successful": 5, "failed": 0},
            "hits": {
                "total": {"value": 2, "relation": "eq"},
                "hits": [{
                    "_id": "1",
                    "_source": {
                        "labor_category": "Network Engineer I",
                        "current_price": 125.0,
                    },
                }, {
                    "_id": "2",
                    "_source": {
                        "labor_category": "Network Engineer II",
                        "current_price": 175.0,
                    },
                }],
            },
        }

    monkeypatch.setattr(cr, "get_json", fake_get)
    out = cr.CalcRatesSource(api_key="k").enrich(
        SourceQuery(keywords=["network engineer"])
    )

    assert out["network engineer"] == {
        "count": 2, "min": 125.0, "median": 150.0, "max": 175.0,
    }
    assert calls == [(cr.RATES_URL, {
        "keyword": "network engineer",
        "page": 1,
        "page_size": cr.PAGE_SIZE,
        "api_key": "k",
    })]


def test_calc_schema_error_is_not_a_screened_zero(monkeypatch):
    monkeypatch.setattr(cr, "get_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    with pytest.raises(ValueError, match="recognized result list"):
        cr.CalcRatesSource(api_key="k").enrich(
            SourceQuery(keywords=["cyber analyst"])
        )

    monkeypatch.setattr(cr, "get_json", lambda *args, **kwargs: {"results": []})
    assert cr.CalcRatesSource(api_key="k").enrich(
        SourceQuery(keywords=["cyber analyst"])
    ) == {}

    monkeypatch.setattr(cr, "get_json", lambda *args, **kwargs: {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {"hits": [{"_source": {"message": "maintenance"}}]},
    })
    with pytest.raises(ValueError, match="omitted labor_category"):
        cr.CalcRatesSource(api_key="k").enrich(
            SourceQuery(keywords=["cyber analyst"])
        )

    monkeypatch.setattr(cr, "get_json", lambda *args, **kwargs: {
        "timed_out": True,
        "hits": {"hits": []},
    })
    with pytest.raises(ValueError, match="timed out"):
        cr.CalcRatesSource(api_key="k").enrich(
            SourceQuery(keywords=["cyber analyst"])
        )


def test_edgar_dedupes_companies_and_links(monkeypatch):
    monkeypatch.setattr(se, "get_json", lambda url, *, params, headers, **kw: {
        "hits": {"hits": [
            {"_source": {"display_names": ["Recorded Future Rival Inc (RFR)"],
                         "root_forms": "10-K", "file_date": "2026-06-01"}},
            {"_source": {"display_names": ["Recorded Future Rival Inc (RFR)"],
                         "root_forms": "10-Q", "file_date": "2026-05-01"}},
            {"_source": {"display_names": [], "root_forms": "10-K"}},
        ]}})
    out = se.SecEdgarSource().enrich(SourceQuery(keywords=["threat intelligence"]))
    v = out["threat intelligence"]
    assert out["keywords_screened"] == 1
    assert out["keywords_total"] == 1
    assert len(v["companies"]) == 1
    assert v["companies"][0]["form"] == "10-K"
    assert "sec.gov/edgar/search" in v["search_url"]


def test_fedramp_tries_candidates_and_matches(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return {"data": {"Products": [
            {"id": "FR123", "csp": "CrowdStrike", "cso": "Falcon",
             "status": "FedRAMP Certified", "impact_level": "High",
             "authorization": 8, "reuse": 7, "auth_type": "Rev5",
             "service_model": ["SaaS"], "deployment_model": "Public Cloud",
             "agency_authorizations": ["Department of Defense"],
             "agency_reuse": ["Department of Homeland Security"],
             "website": "https://example.test/falcon"},
            {"id": "FR456", "csp": "OtherCo", "cso": "Widget",
             "status": "FedRAMP In Process"},
        ]}}

    monkeypatch.setattr(fr, "get_json", fake_get)
    out = fr.FedRampSource().enrich(SourceQuery(keywords=["crowdstrike"]))
    assert out["marketplace_total"] == 2
    assert out["matched"][0]["provider"] == "CrowdStrike"
    assert out["matched"][0]["product"] == "Falcon"
    assert out["matched"][0]["status"] == "FedRAMP Certified"
    assert out["matched"][0]["authorization_count"] == 8
    assert out["matched"][0]["marketplace_url"] == (
        "https://marketplace.fedramp.gov/products/FR123/"
    )
    assert out["source_mode"] == "current_official"
    assert "FedRAMP/marketplace-fedramp-gov-data" in out["source"]
    assert len(calls) == 1
    assert calls[0].endswith(
        "/FedRAMP/marketplace-fedramp-gov-data/main/data.json"
    )


def test_fedramp_rejects_placeholder_rows_and_accepts_explicit_empty(monkeypatch):
    monkeypatch.setattr(fr, "get_json", lambda *args, **kwargs: {
        "data": {"Products": [{"message": "maintenance"}]}
    })
    with pytest.raises(RuntimeError, match="omitted provider/product identity"):
        fr.FedRampSource().enrich(SourceQuery(keywords=["cloud"]))

    calls = []

    def empty_marketplace(url, **kwargs):
        calls.append(url)
        return {"data": {"Products": []}}

    monkeypatch.setattr(fr, "get_json", empty_marketplace)
    out = fr.FedRampSource().enrich(SourceQuery(keywords=["cloud"]))
    assert out["marketplace_total"] == 0
    assert out["matched"] == []
    assert out["source_mode"] == "current_official"
    assert len(calls) == 1

    monkeypatch.setattr(
        fr, "get_json", lambda *args, **kwargs: {"data": []}
    )
    assert fr.FedRampSource().enrich(
        SourceQuery(keywords=["cloud"])
    )["marketplace_total"] == 0


def test_fedramp_historical_fallback_is_stale_and_partial(monkeypatch):
    monkeypatch.setattr(fr, "fetch_marketplace", lambda: ([{
        "Provider": "CrowdStrike",
        "Package": "Falcon",
        "Designation": "Authorized",
    }], "18F/fedramp-data (historical fallback)"))

    out = fr.FedRampSource().enrich(SourceQuery(keywords=["crowdstrike"]))

    assert out["source_mode"] == "historical_official_fallback"
    assert out["stale"] is True
    assert out["partial"] is True

    from run_searches import attempt_row
    receipt = attempt_row("fedramp", out, "1 match", 0.1, None)
    assert receipt["partial"] is True
    assert receipt["coverage_contract"]["complete_within_boundary"] is False


def test_fedramp_archived_gsa_fallback_is_stale_and_partial(monkeypatch):
    monkeypatch.setattr(fr, "fetch_marketplace", lambda: ([{
        "csp": "CrowdStrike",
        "cso": "Falcon",
        "status": "FedRAMP Authorized",
    }], "GSA/marketplace-fedramp-gov-data (archived fallback)"))

    out = fr.FedRampSource().enrich(SourceQuery(keywords=["crowdstrike"]))

    assert out["source_mode"] == "historical_official_fallback"
    assert out["stale"] is True
    assert out["partial"] is True


def test_hierarchy_meters_quota_and_parses(monkeypatch):
    monkeypatch.setattr(fh, "get_json", lambda url, *, params, **kw: {"orglist": [
        {"fhorgname": "CYBERSECURITY AND INFRASTRUCTURE SECURITY AGENCY",
         "fhorgtype": "SUB-TIER", "fhorgid": 100006688, "status": "ACTIVE"}]})
    import tools.api.sam_quota as sq
    before = sq.calls_today()
    out = fh.FederalHierarchySource(api_key="k").enrich(
        SourceQuery(agencies=["Department of Homeland Security"]))
    assert sq.calls_today() == before + 1
    orgs = out["Department of Homeland Security"]
    assert orgs[0]["type"] == "SUB-TIER" and orgs[0]["id"] == 100006688


def test_hierarchy_schema_error_is_not_a_screened_zero(monkeypatch):
    monkeypatch.setattr(fh, "get_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    monkeypatch.setattr(fh.sam_quota, "note_call", lambda *args, **kwargs: None)
    source = fh.FederalHierarchySource(api_key="k")
    query = SourceQuery(agencies=["Department of Homeland Security"])
    with pytest.raises(ValueError, match="recognized organization list"):
        source.enrich(query)

    monkeypatch.setattr(fh, "get_json", lambda *args, **kwargs: {"orglist": []})
    assert source.enrich(query) == {}

    monkeypatch.setattr(fh, "get_json", lambda *args, **kwargs: {
        "orglist": [{"message": "maintenance"}]
    })
    with pytest.raises(ValueError, match="omitted organization name"):
        source.enrich(query)


def test_distiller_covers_new_lanes():
    from agents.decisions.research_picture import distill, render_provenance
    sweep = distill({
        "treasury_fiscal": {"as_of": "2026-05-31", "matched_lines": [
            {"line": "CISA", "fytd_gross_outlays": 2.9e9}]},
        "gao_legal": {"items": [{"title": "B-1", "protest": True, "published": "x"}]},
        "calc_rates": {"cyber analyst": {"count": 2, "min": 100, "median": 125, "max": 150}},
        "sec_edgar": {"cti": {"companies": [{"company": "RivalCo", "form": "10-K"}]}},
        "fedramp": {"matched": [{"provider": "CrowdStrike", "status": "Authorized"}],
                    "marketplace_total": 500},
        "federal_hierarchy": {"DHS": [{"name": "CISA", "type": "SUB-TIER"}]},
    })
    assert sweep["agency_outlays"][0]["line"] == "CISA"
    assert sweep["legal_decisions"][0]["protest"] is True
    assert sweep["labor_rates"]["cyber analyst"]["median"] == 125
    assert sweep["public_filings"][0]["company"] == "RivalCo"
    assert sweep["fedramp_matches"][0]["provider"] == "CrowdStrike"
    assert sweep["buying_offices"][0]["office"] == "CISA"
    prov = render_provenance(sweep)
    for needle in ("agency outlay lines", "GAO legal decisions", "public-company filings",
                   "FedRAMP marketplace entries", "buying offices", "labor-rate"):
        assert needle in prov, needle
