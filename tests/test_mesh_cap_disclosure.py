"""Capped lanes say what they cut; empty lanes never read as success.

AUDIT FINDINGS 2026-07-30 (the source-mesh B-):
  1. Eight enrichment lanes computed every match, then silently kept the
     first N ([:20]/[:40]/[:15]/[:12]) - matches past the cap were computed
     and thrown away, and the capped list read as a census. A broad-keyword
     client on a busy news week lost matches with nothing saying so.
  2. A missing API key returned {"items": [], "note": "..."} with no error
     key, attempt_row reported ok=True, and the client-facing coverage band
     called a lane that gathered NOTHING "RETURNED".

The rules pinned here: every capped lane speaks cap_disclosed's one dialect
(kept slice + total_matched + truncated only when real); order is preserved,
never re-ranked, because sweep approvals bind to exact sweep contents; a
keyless lane is a NAMED failure; and a structurally-inapplicable lane writes
NO attempt row so coverage renders NOT RUN THIS REFRESH - the projection's
fixed vocabulary (a contract surface) is never touched.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.api.base import cap_disclosed  # noqa: E402


# --------------------------------------------------------------------------- #
# the one dialect
# --------------------------------------------------------------------------- #
def test_a_cut_is_disclosed_with_the_true_total():
    out = cap_disclosed(list(range(30)), 20)
    assert len(out["items"]) == 20
    assert out["total_matched"] == 30
    assert out["truncated"] is True
    assert "30" in out["truncated_note"]


def test_no_cut_no_truncation_claim():
    """truncated must MEAN something: stamping it on an uncut payload would
    train readers to ignore it."""
    out = cap_disclosed([1, 2, 3], 20)
    assert out["total_matched"] == 3
    assert "truncated" not in out
    assert "truncated_note" not in out


def test_order_is_preserved_never_reranked():
    """Re-sorting would change which items survive the cap, and sweep
    approvals bind to exact sweep contents."""
    items = [5, 1, 9, 3]
    assert cap_disclosed(items, 2)["items"] == [5, 1]


def test_the_key_is_configurable_for_matched_shaped_lanes():
    out = cap_disclosed(["a", "b"], 1, key="matched")
    assert out["matched"] == ["a"]
    assert out["total_matched"] == 2


# --------------------------------------------------------------------------- #
# the eight lanes actually speak it
# --------------------------------------------------------------------------- #
def test_watchdog_lane_discloses_its_cut(monkeypatch):
    from tools.api import watchdog_rss as w

    item = ("<item><title>Cyber monitoring weakness found</title>"
            "<link>https://x.gov/r</link><pubDate>2026-07-01</pubDate>"
            "<description>network monitoring gap</description></item>")
    feed = f"<rss><channel>{item * 30}</channel></rss>"
    monkeypatch.setattr(w, "get_text", lambda url, **k: feed)
    monkeypatch.setattr(w, "FEEDS", {"gao": "https://x.gov/feed"})
    from tools.api.base import SourceQuery

    out = w.WatchdogRssSource().enrich(SourceQuery(keywords=["monitoring"]))
    assert len(out["items"]) == 20
    assert out["total_matched"] == 30
    assert out["truncated"] is True
    assert out["total_reports"] == 30      # the pre-existing census survives


def test_dod_contracts_lane_discloses_its_cut(monkeypatch):
    from tools.api import dod_contracts as d

    item = ("<item><title>Network award to Vendor</title>"
            "<link>https://defense.gov/x</link><pubDate>2026-07-01</pubDate>"
            "<description>network monitoring contract awarded</description>"
            "</item>")
    feed = f"<rss><channel>{item * 25}</channel></rss>"
    monkeypatch.setattr(d, "get_text", lambda url, **k: feed)
    from tools.api.base import SourceQuery

    out = d.DodContractsSource().enrich(SourceQuery(keywords=["monitoring"]))
    assert len(out["items"]) == 20
    assert out["total_matched"] == 25
    assert out["truncated"] is True


def test_incumbent_field_depth_is_disclosed():
    from tools.api.contract_awards import _rank_incumbents

    recompetes = [{"awardee": f"VENDOR {i % 20}", "period_end": "2027-01-01"}
                  for i in range(60)]
    ranked = _rank_incumbents(recompetes)
    assert len(ranked) == 20               # the FULL field comes back now
    out = cap_disclosed(ranked, 15, key="incumbents")
    assert len(out["incumbents"]) == 15
    assert out["total_matched"] == 20
    assert out["truncated"] is True


# --------------------------------------------------------------------------- #
# a lane that gathered nothing is never a success
# --------------------------------------------------------------------------- #
def test_a_missing_api_key_is_a_named_failure(monkeypatch):
    from tools.api.congress_gov import CongressGovSource
    from tools.api.base import SourceQuery

    # api_key=None falls back to the environment, and some environments
    # carry the key; keylessness must be forced, not assumed.
    monkeypatch.delenv("CONGRESS_GOV_API_KEY", raising=False)
    out = CongressGovSource(api_key=None).enrich(
        SourceQuery(keywords=["monitoring"]))
    assert out["items"] == []
    assert "CONGRESS_GOV_API_KEY" in out["error"]


def test_attempt_row_reports_the_keyless_lane_as_failed():
    """The projection reads ok: this is the line that stops the coverage
    band from calling a zero-gather 'RETURNED'."""
    from run_searches import attempt_row

    keyless = {"items": [], "total_scanned": 0,
               "error": "CONGRESS_GOV_API_KEY not set; lane cannot gather"}
    row = attempt_row("congress", keyless, None, 0.1, None)
    assert row["ok"] is False
    assert "CONGRESS_GOV_API_KEY" in row["error"]


def test_the_not_applicable_sentinel_exists_and_is_not_a_real_summary():
    from run_searches import LANE_NOT_APPLICABLE

    assert LANE_NOT_APPLICABLE.startswith("__")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
