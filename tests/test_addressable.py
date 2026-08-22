"""Keyword-scoped USAspending pulls — the addressable slice. No network:
the HTTP boundary is monkeypatched; the query construction is real.
"""

from __future__ import annotations

import tools.api.usaspending as usa
import pytest
from tools.api.usaspending import UsaSpendingSource, addressable_terms


def test_addressable_terms_are_product_terms_only():
    kws = [
        {"term": "KVM matrix switch", "category": "capability"},
        {"term": "KVM extender / signal extension", "category": "capability"},
        {"term": "command and control / C2", "category": "capability"},
        {"term": "DoDIN APL / UC APL", "category": "technology"},
        {"term": "Department of Defense", "category": "agency"},          # dropped
        {"term": "334118", "category": "naics"},                          # dropped
        {"term": "total small business set-aside", "category": "set_aside"},  # dropped
        {"term": "Velocity KVM / TLX matrix switch", "category": "search_term"},
        {"term": "kvm matrix switch", "category": "technology"},          # dupe
        {"term": "U.S. Army (TechNet Augusta)", "category": "capability"},
    ]
    terms = addressable_terms(kws)
    assert "KVM matrix switch" in terms
    assert "KVM extender" in terms and "signal extension" in terms  # slash split
    assert "command and control" in terms
    assert "C2" not in terms                       # under the API's 3-char floor
    assert "Department of Defense" not in terms    # agency terms over-match
    assert "334118" not in terms                   # lanes already NAICS-filtered
    assert not any("set-aside" in t for t in terms)
    assert terms.count("KVM matrix switch") == 1   # case-insensitive dedupe
    assert "U.S. Army" in terms                    # parenthetical stripped
    assert len(terms) <= 20


def test_keyword_slice_builds_scoped_queries_and_sums_breakdown(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append((url, json))
        if "spending_by_category" in url:
            return {"results": [
                {"name": "Department of Defense", "amount": 6_000_000.0, "code": "097"},
                {"name": "General Services Administration", "amount": 3_000_000.0, "code": "047"},
            ]}
        return {"results": [
            {"Award ID": "W1", "Recipient Name": "AVI-SPL", "Award Amount": 2_000_000.0,
             "Awarding Agency": "Department of Defense",
             "generated_internal_id": "CONT_AWD_W1"},
        ]}

    monkeypatch.setattr(usa, "post_json", fake_post)
    s = UsaSpendingSource().keyword_slice("334310", ["KVM matrix switch", "VDS"])

    assert s["total_obligated"] == 9_000_000.0     # summed from the aggregate
    assert s["window_years"] == 3
    assert [r["agency"] for r in s["agency_breakdown"]] == [
        "Department of Defense", "General Services Administration"]
    assert s["awards"][0]["recipient"] == "AVI-SPL"
    assert s["keywords_used"] == ["KVM matrix switch", "VDS"]
    # BOTH queries carried the keyword scope AND the lane filter
    assert len(calls) == 2
    for _url, body in calls:
        assert body["filters"]["keywords"] == ["KVM matrix switch", "VDS"]
        assert body["filters"]["naics_codes"] == ["334310"]


def test_keyword_slice_rejects_placeholder_category_row(monkeypatch):
    monkeypatch.setattr(
        usa,
        "post_json",
        lambda *args, **kwargs: {"results": [{"message": "maintenance"}]},
    )

    with pytest.raises(
        ValueError,
        match="category result omitted name, code, or numeric amount",
    ):
        UsaSpendingSource().keyword_slice("334310", ["KVM matrix switch"])


def test_market_evidence_rejects_unexpected_success_schema(monkeypatch):
    monkeypatch.setattr(
        usa, "post_json", lambda *args, **kwargs: {"message": "maintenance"}
    )

    with pytest.raises(ValueError, match="required results list"):
        UsaSpendingSource().market_evidence(
            "334310", agency="Department of Defense", keywords=["cloud"]
        )


def test_market_evidence_accepts_official_empty_results(monkeypatch):
    monkeypatch.setattr(
        usa, "post_json", lambda *args, **kwargs: {"results": []}
    )

    result = UsaSpendingSource().market_evidence(
        "334310", agency="Department of Defense", keywords=["cloud"]
    )
    assert result["awards"] == []
    assert result["agency_breakdown"] == []
    assert result["addressable"]["awards"] == []


def test_expiring_awards_filters_to_window_and_cites(monkeypatch):
    """The recompete calendar: only awards ENDING inside the forward window
    survive; every surviving row carries its citable award URL; earliest end
    date sorts first (it's a calendar, not a leaderboard)."""
    from datetime import date, timedelta
    today = date.today()
    inside = (today + timedelta(days=90)).isoformat()
    inside2 = (today + timedelta(days=30)).isoformat()
    past = (today - timedelta(days=10)).isoformat()
    far = (today + timedelta(days=900)).isoformat()

    def fake_post(url, json=None, **kw):
        assert json["filters"]["naics_codes"] == ["541990"]
        return {"results": [
            {"Award ID": "A1", "Recipient Name": "GDIT", "Award Amount": 5e6,
             "Awarding Agency": "Department of Defense",
             "Awarding Sub Agency": "Department of the Air Force",
             "Start Date": "2023-01-01", "End Date": inside,
             "generated_internal_id": "CONT_AWD_A1"},
            {"Award ID": "A2", "Recipient Name": "SOONER LLC", "Award Amount": 2e6,
             "Awarding Agency": "GSA", "Awarding Sub Agency": "FAS",
             "Start Date": "2023-01-01", "End Date": inside2,
             "generated_internal_id": "CONT_AWD_A2"},
            {"Award ID": "A3", "Recipient Name": "DONE INC", "Award Amount": 9e6,
             "Awarding Agency": "GSA", "Awarding Sub Agency": "FAS",
             "Start Date": "2020-01-01", "End Date": past,
             "generated_internal_id": "CONT_AWD_A3"},           # already ended
            {"Award ID": "A4", "Recipient Name": "FAR OUT", "Award Amount": 8e6,
             "Awarding Agency": "GSA", "Awarding Sub Agency": "FAS",
             "Start Date": "2024-01-01", "End Date": far,
             "generated_internal_id": "CONT_AWD_A4"},           # beyond window
            {"Award ID": "A5", "Recipient Name": "NO END", "Award Amount": 1e6,
             "Awarding Agency": "GSA", "Awarding Sub Agency": "FAS",
             "Start Date": "2024-01-01", "End Date": None,
             "generated_internal_id": "CONT_AWD_A5"},           # no end date
        ]}

    monkeypatch.setattr(usa, "post_json", fake_post)
    rows = UsaSpendingSource().expiring_awards("541990")
    assert [r["award_id"] for r in rows] == ["A2", "A1"]  # calendar order
    assert rows[0]["generated_internal_id"] == "CONT_AWD_A2"
    assert rows[0]["amount_basis"] == "USAspending Award Amount"
    assert rows[0]["url"] == usa.AWARD_LINK + "CONT_AWD_A2"
    assert rows[1]["url"] == usa.AWARD_LINK + "CONT_AWD_A1"
    assert rows[1]["awarding_sub_agency"] == "Department of the Air Force"


def test_expiring_awards_pages_and_deduplicates_the_official_scan(monkeypatch):
    from datetime import date, timedelta

    end = (date.today() + timedelta(days=120)).isoformat()
    calls = []

    def row(award_id):
        return {
            "Award ID": award_id,
            "Recipient Name": f"Vendor {award_id}",
            "Award Amount": 1_000_000,
            "Awarding Agency": "Department of the Treasury",
            "Awarding Sub Agency": "Internal Revenue Service",
            "Start Date": "2024-01-01",
            "End Date": end,
            "generated_internal_id": f"CONT_AWD_{award_id}",
        }

    def fake_post(_url, json=None, **_kwargs):
        calls.append(json["page"])
        if json["page"] == 1:
            return {"results": [row("A1"), row("A2")],
                    "page_metadata": {"hasNext": True}}
        return {"results": [row("A2"), row("A3")],
                "page_metadata": {"hasNext": False}}

    monkeypatch.setattr(usa, "post_json", fake_post)
    rows = UsaSpendingSource().expiring_awards(
        "541512", limit=2, max_pages=3
    )

    assert calls == [1, 2]
    assert [item["award_id"] for item in rows] == ["A1", "A2", "A3"]
    assert [item["retrieved_page"] for item in rows] == [1, 1, 2]


def test_market_evidence_attaches_addressable_and_never_fails_on_it(monkeypatch):
    def fake_post(url, json=None, **kw):
        if "spending_by_category" in url and json["filters"].get("keywords"):
            raise RuntimeError("boom")            # addressable query dies
        if "spending_by_category" in url:
            return {"results": []}
        return {"results": []}

    monkeypatch.setattr(usa, "post_json", fake_post)
    b = UsaSpendingSource().market_evidence("334310", keywords=["KVM matrix switch"])
    assert b["addressable"]["error"]               # recorded, not raised
    assert b["addressable"]["keywords_used"] == ["KVM matrix switch"]

    b2 = UsaSpendingSource().market_evidence("334310")   # no keywords -> no slice
    assert b2["addressable"] is None
