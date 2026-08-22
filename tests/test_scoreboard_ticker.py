"""Ticker famine fallback (2026-07-12): a strict cutover with zero
monitor-grade rows plus a zero-news sweep emptied the internal tape. The
internal view falls back to verifiable motion (news, buyer incumbents,
census state); sales and client tapes are unchanged, and a sales tape with
zero watchlist entries no longer renders a pointless "plus 0 more"."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.document import build_document  # noqa: E402
from agents.reports.views import render_scoreboard  # noqa: E402


def _doc(with_incumbents=True, with_census=True):
    results = {
        "sam.gov": [], "triage": {},
        "sam_census": ({"active_screened": 17, "retrieved": 17,
                        "matched": 17, "complete": True}
                       if with_census else {}),
        # the banner's no-assessment guard needs a market basis
        "usaspending.gov": [{
            "naics_code": "541512",
            "summary": {"award_count": 1, "total_obligated": 900000.0,
                        "median_award": 900000.0, "max_award": 900000.0,
                        "top_incumbents": ["PrimeCo"]},
            "awards": [{"award_id": "A1", "recipient": "PrimeCo",
                        "amount": 900000.0,
                        "awarding_agency": "Department of Homeland Security",
                        "start_date": "2026-01-01",
                        "url": "https://usaspending.gov/award/A1"}],
            "agency_breakdown": [{
                "agency": "Department of Homeland Security",
                "total_obligated": 900000.0}],
        }],
    }
    if with_incumbents:
        results["incumbent_buyer_map"] = {
            "buyers": [
                {"buyer": "DEFENSE INFORMATION SYSTEMS AGENCY",
                 "agency": "DEFENSE INFORMATION SYSTEMS AGENCY",
                 "incumbent": "SolarWinds", "product": "Orion",
                 "evidence_url": "https://usaspending.gov/award/X1"},
            ],
        }
    return build_document("Testco", searches={"client": "Testco",
                                              "results": results},
                          qualify={}, _live_report=False)


def test_internal_tape_falls_back_to_census_when_everything_is_quiet():
    doc = _doc(with_incumbents=False)
    html = render_scoreboard(doc, view="internal")
    assert "vb-ticker" in html
    assert "live records screened this cycle" in html


def test_internal_tape_prefers_real_signals_over_silence():
    doc = _doc()
    html = render_scoreboard(doc, view="internal")
    assert "vb-ticker" in html
    # census note rides along regardless
    assert "CENSUS" in html


def test_sales_tape_never_advertises_zero_watchlist():
    doc = _doc()
    html = render_scoreboard(doc, view="sales")
    assert "plus 0 more" not in html
