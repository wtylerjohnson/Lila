"""Three keyless sources: Federal Register, trade RSS, USAspending subawards."""

import pytest

from agents.reports.facts import build_fact_pack
from tools.api.base import REGISTRY, SourceQuery
from tools.api.federal_register import FederalRegisterSource
from tools.api.trade_rss import TradeRssSource, _matches, _parse_items
from tools.api.usaspending_subawards import SubawardsSource

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>CISA posts threat intelligence RFI</title>
<link>https://x.example/a</link><pubDate>Thu, 02 Jul 2026</pubDate>
<description>threat intelligence services</description></item>
<item><title>Unrelated satellite story</title>
<link>https://x.example/b</link><pubDate>Thu, 02 Jul 2026</pubDate>
<description>weather</description></item>
</channel></rss>"""


def test_all_three_registered():
    names = {s.name for s in REGISTRY.all()}
    assert {"federal_register", "trade_rss", "usaspending_subawards"} <= names


def test_rss_parses_and_keyword_filters():
    items = _parse_items(RSS, "Test Feed")
    assert len(items) == 2 and items[0]["url"] == "https://x.example/a"
    kw = ['"threat intelligence"']
    assert _matches(items[0], kw) and not _matches(items[1], kw)


def test_rss_dead_feed_isolated(monkeypatch):
    import tools.api.trade_rss as tr
    calls = {"n": 0}
    def fake_get(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("feed down")
        return RSS
    monkeypatch.setattr(tr, "get_text", fake_get)
    out = TradeRssSource().enrich(SourceQuery(keywords=[]))
    assert len(out["errors"]) == 1 and len(out["items"]) > 0


def test_rss_malformed_feed_is_failure_but_valid_empty_feed_is_clean(monkeypatch):
    import tools.api.trade_rss as tr

    monkeypatch.setattr(tr, "get_text", lambda url, **kw: "not XML")
    out = TradeRssSource().enrich(SourceQuery(keywords=[]))
    assert out["items"] == []
    assert len(out["errors"]) == len(tr.FEEDS)
    assert _parse_items("<rss><channel /></rss>", "Empty Feed") == []
    with pytest.raises(ValueError, match="not an RSS feed"):
        _parse_items("<html><body>maintenance</body></html>", "Maintenance")


def test_federal_register_maps_fields(monkeypatch):
    import tools.api.federal_register as fr
    monkeypatch.setattr(fr, "get_json", lambda url, params=None, **kw: {
        "results": [{"title": "Vetting rule", "type": "Proposed Rule",
                     "abstract": "screening", "publication_date": "2026-06-30",
                     "html_url": "https://fr.example/doc",
                     "agencies": [{"name": "DHS"}]}]})
    out = FederalRegisterSource().enrich(SourceQuery(keywords=["vetting"]))
    assert out["vetting"][0]["url"] == "https://fr.example/doc"
    assert out["vetting"][0]["agencies"] == ["DHS"]


def test_federal_register_schema_error_is_not_a_screened_zero(monkeypatch):
    import tools.api.federal_register as fr

    monkeypatch.setattr(fr, "get_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    with pytest.raises(ValueError, match="required results list"):
        FederalRegisterSource().enrich(SourceQuery(keywords=["vetting"]))

    monkeypatch.setattr(fr, "get_json", lambda *args, **kwargs: {"results": []})
    assert FederalRegisterSource().enrich(
        SourceQuery(keywords=["vetting"])
    ) == {}

    monkeypatch.setattr(fr, "get_json", lambda *args, **kwargs: {
        "results": [{"message": "maintenance"}]
    })
    with pytest.raises(ValueError, match="omitted its title"):
        FederalRegisterSource().enrich(SourceQuery(keywords=["vetting"]))


def test_subawards_ranks_primes(monkeypatch):
    import tools.api.usaspending_subawards as sa
    rows = [
        {"Prime Recipient Name": "BigPrime", "Sub-Awardee Name": "S1",
         "Sub-Award ID": "SUB-1", "Sub-Award Amount": 900000,
         "Action Date": "2026-01-01"},
        {"Prime Recipient Name": "BigPrime", "Sub-Awardee Name": "S2",
         "Sub-Award ID": "SUB-2", "Sub-Award Amount": 100000,
         "Action Date": "2026-02-01"},
        {"Prime Recipient Name": "SmallPrime", "Sub-Awardee Name": "S3",
         "Sub-Award ID": "SUB-3", "Sub-Award Amount": 50000,
         "Action Date": "2026-03-01"},
    ]
    monkeypatch.setattr(sa, "post_json", lambda url, json: {"results": rows})
    out = SubawardsSource().enrich(SourceQuery(naics_codes=["541512"]))
    assert out["primes"][0]["name"] == "BigPrime"
    assert out["primes"][0]["subaward_count"] == 2
    assert out["primes"][0]["total"] == 1000000.0


def test_subawards_empty_without_naics():
    assert SubawardsSource().enrich(SourceQuery()) == {"primes": [], "sample": [], "edges": {}}


def test_subawards_rejects_unexpected_success_schema(monkeypatch):
    import tools.api.usaspending_subawards as sa

    monkeypatch.setattr(
        sa, "post_json", lambda *args, **kwargs: {"message": "maintenance"}
    )
    with pytest.raises(ValueError, match="required results list"):
        SubawardsSource().enrich(SourceQuery(naics_codes=["541512"]))


def test_subawards_accepts_official_empty_results(monkeypatch):
    import tools.api.usaspending_subawards as sa

    monkeypatch.setattr(sa, "post_json", lambda *args, **kwargs: {"results": []})
    out = SubawardsSource().enrich(SourceQuery(naics_codes=["541512"]))
    assert out == {"primes": [], "sample": [], "edges": {"541512": []}}


def test_subawards_rejects_placeholder_result_row(monkeypatch):
    import tools.api.usaspending_subawards as sa

    monkeypatch.setattr(
        sa,
        "post_json",
        lambda *args, **kwargs: {"results": [{"message": "maintenance"}]},
    )
    with pytest.raises(ValueError, match="result omitted subaward identity"):
        SubawardsSource().enrich(SourceQuery(naics_codes=["541512"]))


def test_fact_pack_ingests_new_sources():
    searches = {"results": {
        "usaspending.gov": [],
        "federal_register": {"vetting": [
            {"title": "Vetting rule", "type": "Proposed Rule",
             "publication_date": "2026-06-30", "url": "https://fr.example/doc",
             "agencies": ["DHS"]}]},
        "news": {"items": [
            {"title": "CISA RFI", "url": "https://n.example/1",
             "published": "Thu, 02 Jul 2026", "source": "IC News"}]},
        "subawards": {"primes": [
            {"name": "BigPrime", "subaward_count": 2, "total": 1000000.0}]},
    }}
    pack = build_fact_pack("Testco", searches=searches, qualify_report={"candidates": []})
    texts = " | ".join(f.text for f in pack.facts)
    assert "Vetting rule" in texts
    assert "CISA RFI" in texts
    assert "BigPrime" in texts and "teaming target" in texts
    assert all(f.source for f in pack.facts)


def test_monitor_notices_become_citable_facts():
    """2026-07-09 Osprey: the review pushed monitor-grade notices (Project
    Pioneer, OASIS+) that had no fact ids, forcing the composer to invent.
    Monitor notices must be citable facts — labeled, never pursuits."""
    searches = {"results": {
        "triage": {
            "abc123": {"verdict": "monitor", "reason": "adjacent data lane"},
            "def456": {"verdict": "discard", "reason": "unrelated"},
        },
        "sam.gov": [
            {"source_id": "abc123", "title": "Project Pioneer",
             "api_url": "https://sam.gov/opp/abc123/view",
             "raw_payload": {"agency": "DEPT OF DEFENSE", "naics_code": "518210"}},
            {"source_id": "def456", "title": "Unrelated",
             "api_url": "https://sam.gov/opp/def456/view", "raw_payload": {}},
        ],
    }}
    pack = build_fact_pack("Testco", searches=searches, qualify_report={"candidates": []})
    mon = [pack.get(i) for i in pack.monitor_fact_ids]
    assert len(mon) == 1
    f = mon[0]
    assert "Project Pioneer" in f.text and "abc123" in f.text
    assert "MONITOR" in f.text and "NOT a live pursuit" in f.text
    assert "adjacent data lane" in f.text
    assert f.source == "https://sam.gov/opp/abc123/view"
    # a monitor notice is never a pursuit-grade opportunity
    assert f.id not in pack.opportunity_fact_ids
