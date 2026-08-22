"""Subaward edges + subcontracting-demand signal. No network — the HTTP
boundary is monkeypatched with recorded-shape fixtures; query construction
and persistence shapes are asserted exactly."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import tools.api.usaspending_subawards as subs_mod  # noqa: E402
from agents.partnering.demand import subcontracting_demand  # noqa: E402
from tools.api.base import SourceQuery  # noqa: E402
from tools.api.usaspending_subawards import SubawardsSource  # noqa: E402

_EDGE_ROW = {"Sub-Award ID": "SA-1", "Sub-Awardee Name": "AKAMAI TECHNOLOGIES INC",
             "Sub-Award Amount": 179_865_050.09,
             "Prime Recipient Name": "PERSPECTA ENTERPRISE SOLUTIONS LLC",
             "Prime Award ID": "HC108421F0089", "Action Date": "2024-06-01"}
_DEMAND_ROW = {"Award ID": "VA11817F1888", "Recipient Name": "DELL FEDERAL SYSTEMS L.P",
               "Award Amount": 1_730_532_626.58, "Awarding Agency":
               "Department of Veterans Affairs",
               "Period of Performance Start Date": "2023-01-15"}


def test_edges_query_scopes_and_normalizes(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append(json)
        return {"results": [_EDGE_ROW]}

    monkeypatch.setattr(subs_mod, "post_json", fake_post)
    rows = SubawardsSource().edges("541519", agency="DEPT OF DEFENSE.DISA")
    body = calls[0]
    assert body["subawards"] is True
    assert body["filters"]["naics_codes"] == ["541519"]
    # SAM-style agency string normalized to the toptier name the API needs
    assert body["filters"]["agencies"] == [
        {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}]
    assert rows[0]["sub"] == "AKAMAI TECHNOLOGIES INC"
    assert rows[0]["prime_award_id"] == "HC108421F0089"    # citable evidence
    assert rows[0]["agency_filter_used"] == "Department of Defense"
    assert rows[0]["source"].startswith("https://api.usaspending.gov")


def test_demand_query_sources_size_from_the_business_category_filter(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append(json)
        return {"results": [_DEMAND_ROW]}

    monkeypatch.setattr(subs_mod, "post_json", fake_post)
    rows = SubawardsSource().demand_awards("541519", 900_000)
    f = calls[0]["filters"]
    # the size claim is SOURCED: the API's recipient business-category filter
    assert f["recipient_type_names"] == ["other_than_small_business"]
    assert f["award_amounts"] == [{"lower_bound": 900_000}]
    assert rows[0]["recipient"] == "DELL FEDERAL SYSTEMS L.P"
    assert "other_than_small_business" in rows[0]["size_basis"]


def test_demand_awards_rejects_placeholder_award_row(monkeypatch):
    monkeypatch.setattr(
        subs_mod,
        "post_json",
        lambda *args, **kwargs: {"results": [{"message": "maintenance"}]},
    )

    with pytest.raises(ValueError, match="result omitted award identity"):
        SubawardsSource().demand_awards("541519", 900_000)


def test_enrich_persists_full_edge_lists_per_lane(monkeypatch):
    def fake_post(url, json=None, **kw):
        return {"results": [_EDGE_ROW] * 3}

    monkeypatch.setattr(subs_mod, "post_json", fake_post)
    out = SubawardsSource().enrich(SourceQuery(naics_codes=["541519", "334310"]))
    assert out["primes"]                      # legacy rollup intact
    assert set(out["edges"]) == {"541519", "334310"}
    assert len(out["edges"]["541519"]) == 3   # full rows, never samples
    assert out["edges"]["541519"][0]["subaward_id"] == "SA-1"


def test_demand_summarizer_ranks_recipients_by_sourced_dollars():
    rows = {"541519": [
        {"recipient": "DELL FEDERAL SYSTEMS L.P", "amount": 1_000_000.0},
        {"recipient": "DELL FEDERAL SYSTEMS L.P", "amount": 2_000_000.0},
        {"recipient": "LEIDOS, INC.", "amount": 2_500_000.0},
        {"error": "one lane hiccup"},          # tolerated, dropped
    ]}
    d = subcontracting_demand(rows, threshold_usd=900_000)
    assert d["na"] is False and d["threshold_usd"] == 900_000
    assert "FAR 19.702" in d["provenance"]
    recips = d["by_naics"]["541519"]["recipients"]
    assert [r["name"] for r in recips] == ["DELL FEDERAL SYSTEMS L.P", "LEIDOS, INC."]
    assert recips[0]["total"] == 3_000_000.0 and recips[0]["award_count"] == 2


def test_unverified_threshold_degrades_to_na_never_guesses():
    d = subcontracting_demand({"541519": [{"recipient": "X", "amount": 1.0}]},
                              threshold_usd=None)
    assert d["na"] is True
    assert "unverified" in d["flag"]
    assert d["by_naics"] == {}                # no partial output on a guess


def test_small_awards_query_sources_size(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append(json)
        return {"results": [{"Award ID": "S1", "Recipient Name": "SmallCo LLC",
                             "Award Amount": 300_000.0,
                             "Awarding Agency": "Department of Defense",
                             "Period of Performance Start Date": "2025-04-01"}]}

    monkeypatch.setattr(subs_mod, "post_json", fake_post)
    rows = SubawardsSource().small_awards("334310")
    assert calls[0]["filters"]["recipient_type_names"] == ["small_business"]
    assert rows[0]["recipient"] == "SmallCo LLC"
    assert "small_business" in rows[0]["size_basis"]
