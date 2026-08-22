"""Budget-execution (spend pressure) + watchdog (GAO/IG) adapters + bank."""

import pytest

import tools.api.usaspending_budget as ub
import tools.api.watchdog_rss as wd
from agents.reports.horizon import build_fact_bank
from tools.api import REGISTRY, SourceQuery
from tools.api.usaspending_budget import BudgetPressureSource
from tools.api.watchdog_rss import WatchdogRssSource


def test_both_registered():
    for name in ("budget_pressure", "watchdogs"):
        assert REGISTRY.get(name) is not None, name


def test_budget_pressure_resolves_names_and_computes_unobligated(monkeypatch):
    def fake_get(url, **kw):
        if "toptier_agencies" in url:
            return {"results": [
                {"agency_name": "Department of Homeland Security",
                 "abbreviation": "DHS", "toptier_code": "070"},
                {"agency_name": "Department of Defense",
                 "abbreviation": "DOD", "toptier_code": "097"},
            ]}
        assert url.endswith("/agency/070/budgetary_resources/")
        return {"agency_data_by_year": [
            {"fiscal_year": 2025, "agency_budgetary_resources": 500e9,
             "agency_total_obligated": 490e9},
            {"fiscal_year": 2026, "agency_budgetary_resources": 575.9e9,
             "agency_total_obligated": 310.7e9},
        ]}

    monkeypatch.setattr(ub, "get_json", fake_get)
    out = BudgetPressureSource().enrich(
        SourceQuery(agencies=["DHS", "Nonexistent Agency of Nowhere"]))
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["agency"] == "Department of Homeland Security"
    assert row["fiscal_year"] == 2026                  # latest FY wins
    assert row["unobligated"] == round(575.9e9 - 310.7e9, 2)
    assert row["pct_obligated"] == 54.0  # 310.7/575.9 rounds up
    assert row["url"].endswith("/agency/070/budgetary_resources/")
    assert out["unresolved_agencies"] == ["Nonexistent Agency of Nowhere"]
    assert "Nonexistent Agency of Nowhere" in out["errors"]

    from run_searches import _payload_partial
    assert _payload_partial(out) is True


def test_budget_pressure_names_resolved_agency_with_no_execution_row(monkeypatch):
    def fake_get(url, **kw):
        if "toptier_agencies" in url:
            return {"results": [{
                "agency_name": "Department of Defense",
                "abbreviation": "DOD",
                "toptier_code": "097",
            }]}
        assert url.endswith("/agency/097/budgetary_resources/")
        return {"agency_data_by_year": []}

    monkeypatch.setattr(ub, "get_json", fake_get)
    out = BudgetPressureSource().enrich(SourceQuery(agencies=["DOD"]))

    assert out["rows"] == []
    assert out["resolved_agencies"] == ["Department of Defense"]
    assert out["errors"] == {
        "Department of Defense": (
            "no valid latest-fiscal-year budget execution row returned"
        )
    }


_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>FAA Oversight of Aviation Risk Data Gaps</title>
  <link>https://www.gao.gov/products/gao-26-1001</link>
  <pubDate>Wed, 08 Jul 2026 12:00:00 GMT</pubDate>
  <description>GAO found the agency lacks aviation risk management data.</description></item>
<item><title>Review of Cafeteria Contracts</title>
  <link>https://www.gao.gov/products/gao-26-1002</link>
  <description>Food services.</description></item>
</channel></rss>"""


def test_watchdogs_filter_and_record_why(monkeypatch):
    monkeypatch.setattr(wd, "get_text", lambda url, **kw: _RSS)
    out = WatchdogRssSource().enrich(
        SourceQuery(keywords=["aviation risk management"]))
    assert out["total_reports"] == 4                    # 2 items x 2 feeds
    assert len(out["items"]) == 2                       # cafeteria filtered, both feeds
    item = out["items"][0]
    assert item["matched"] == ["aviation risk management"]
    assert item["url"].startswith("https://www.gao.gov/")
    assert {i["source"] for i in out["items"]} == {"GAO", "Oversight.gov (IG)"}


def test_watchdogs_dead_feeds_never_kill(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(wd, "get_text", boom)
    out = WatchdogRssSource().enrich(SourceQuery(keywords=["x"]))
    assert out["items"] == [] and len(out["errors"]) == 2


def test_watchdogs_malformed_feed_is_failure_but_valid_empty_feed_is_clean(
        monkeypatch):
    monkeypatch.setattr(wd, "get_text", lambda url, **kw: "not XML")
    out = WatchdogRssSource().enrich(SourceQuery(keywords=["x"]))
    assert out["items"] == []
    assert len(out["errors"]) == len(wd.FEEDS)
    assert wd._parse_items("<rss><channel /></rss>", "Empty Feed") == []
    with pytest.raises(ValueError, match="not an RSS feed"):
        wd._parse_items(
            "<html><body>maintenance</body></html>", "Maintenance"
        )


def test_bank_gains_budget_and_oversight_kinds():
    results = {
        "budget_pressure": {"rows": [
            {"agency": "Department of Homeland Security", "fiscal_year": 2026,
             "budgetary_resources": 575.9e9, "obligated": 310.7e9,
             "unobligated": 265.2e9, "pct_obligated": 53.9,
             "url": "https://api.usaspending.gov/api/v2/agency/070/budgetary_resources/"},
        ]},
        "watchdogs": {"items": [
            {"source": "GAO", "title": "Aviation data gaps", "body": "b",
             "published": "Jul 8", "matched": ["aviation risk management"],
             "url": "https://www.gao.gov/products/gao-26-1001"},
        ]},
    }
    bank = build_fact_bank(results)
    kinds = {f["kind"] for f in bank}
    assert {"budget_line", "oversight"} <= kinds
    b = next(f for f in bank if f["kind"] == "budget_line")
    assert "$575.9B resources" in b["text"] and "$265.2B unobligated" in b["text"]
    assert "—" not in b["text"]                        # house style holds
    o = next(f for f in bank if f["kind"] == "oversight")
    assert "GAO report" in o["text"] and o["source"].startswith("https://www.gao.gov")


# ── congress.gov bill wire (client-flow G) ──

def test_congress_wire_filters_flags_vehicles_and_cites(monkeypatch):
    import tools.api.congress_gov as cg
    from tools.api.congress_gov import CongressGovSource, bill_url

    def fake_get(url, **kw):
        assert "api_key=test-key" in url
        return {"bills": [
            {"congress": 119, "number": "1234", "type": "HR",
             "title": "Aviation Risk Management Modernization Act",
             "latestAction": {"actionDate": "2026-07-01",
                              "text": "Referred to committee."}},
            {"congress": 119, "number": "5678", "type": "S",
             "title": "Department of Homeland Security Appropriations Act "
                      "including aviation risk management data programs",
             "latestAction": {"actionDate": "2026-07-08",
                              "text": "Passed Senate."}},
            {"congress": 119, "number": "9", "type": "HR",
             "title": "Post Office Renaming Act",
             "latestAction": {"actionDate": "2026-07-02", "text": "x"}},
        ]}

    monkeypatch.setattr(cg, "get_json", fake_get)
    monkeypatch.setattr(cg, "PAGES", 1)
    src = CongressGovSource(api_key="test-key")
    out = src.enrich(__import__("tools.api.base", fromlist=["SourceQuery"])
                     .SourceQuery(keywords=["aviation risk management"]))
    assert out["total_scanned"] == 3
    assert len(out["items"]) == 2                       # post office filtered
    approp = next(i for i in out["items"] if i["bill"] == "S5678")
    assert approp["money_vehicle"] == "appropriation"   # vehicle flagged
    assert approp["url"] == bill_url(119, "S", "5678")
    assert approp["url"].endswith("/senate-bill/5678")
    plain = next(i for i in out["items"] if i["bill"] == "HR1234")
    assert plain["money_vehicle"] is None
    assert plain["matched"] == ["aviation risk management"]


def test_congress_wire_keyless_is_honest_empty(monkeypatch):
    from tools.api.base import SourceQuery
    from tools.api.congress_gov import CongressGovSource
    # the real key may be loaded from .env in this process — clear it so the
    # keyless path is actually exercised (no network in tests, ever)
    monkeypatch.delenv("CONGRESS_GOV_API_KEY", raising=False)
    out = CongressGovSource(api_key=None).enrich(SourceQuery(keywords=["x"]))
    assert out["items"] == [] and "not set" in out["note"]


def test_congress_schema_error_is_not_a_screened_zero(monkeypatch):
    import tools.api.congress_gov as cg
    from tools.api.base import SourceQuery
    from tools.api.congress_gov import CongressGovSource

    monkeypatch.setattr(cg, "PAGES", 1)
    monkeypatch.setattr(cg, "get_json", lambda *args, **kwargs: {
        "message": "maintenance"
    })
    with pytest.raises(ValueError, match="required bills list"):
        CongressGovSource(api_key="k").enrich(SourceQuery(keywords=["cloud"]))

    monkeypatch.setattr(cg, "get_json", lambda *args, **kwargs: {
        "bills": [{"message": "maintenance"}]
    })
    with pytest.raises(
        ValueError,
        match="omitted congress, type, number, or title",
    ):
        CongressGovSource(api_key="k").enrich(
            SourceQuery(keywords=["cloud"])
        )

    monkeypatch.setattr(cg, "get_json", lambda *args, **kwargs: {"bills": []})
    out = CongressGovSource(api_key="k").enrich(
        SourceQuery(keywords=["cloud"])
    )
    assert out["items"] == []
    assert out["total_scanned"] == 0
