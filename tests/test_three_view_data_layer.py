"""Three-view commit 1: sweep snapshots + diff, agency aggregates, numeric
vehicle ceilings, agency-name normalization. Offline — snapshot dirs isolated
via LILA_SWEEP_SNAP_DIR (conftest), HTTP mocked.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.snapshots import (  # noqa: E402
    diff_snapshots, load_latest_snapshot, save_snapshot, sweep_slim_records,
)
from tools.api.usaspending import UsaSpendingSource, normalize_agency_name  # noqa: E402


# ---- generic snapshots -------------------------------------------------------
def test_snapshot_roundtrip_and_parameterized_diff():
    slug = "acme"
    old = [
        {"source": "sam.gov", "source_id": "N1", "title": "Old notice",
         "url": "https://sam.gov/opp/N1", "response_deadline": "2026-07-20",
         "verdict": "monitor"},
        {"source": "sam.gov", "source_id": "GONE", "title": "Vanishing",
         "url": None, "response_deadline": None, "verdict": "discard"},
    ]
    save_snapshot("sweep", slug, old, as_of=date(2026, 6, 29))

    cur = [
        {"source": "sam.gov", "source_id": "N1", "title": "Old notice",
         "url": "https://sam.gov/opp/N1", "response_deadline": "2026-08-01",
         "verdict": "pursue"},
        {"source": "news", "source_id": "https://x.gov/a", "title": "Fresh item",
         "url": "https://x.gov/a"},
    ]
    prev = load_latest_snapshot("sweep", slug, before=date(2026, 7, 6))
    delta = diff_snapshots(prev, cur, compare={"response_deadline": "deadline",
                                               "verdict": "verdict"})
    assert [n["id"] for n in delta["new"]] == ["https://x.gov/a"]
    assert [d["id"] for d in delta["disappeared"]] == ["GONE"]
    # one moved entry PER changed field: deadline slipped AND verdict upgraded
    moved = {(m["field"], m["from"], m["to"]) for m in delta["moved"]}
    assert ("response_deadline", "2026-07-20", "2026-08-01") in moved
    assert ("verdict", "monitor", "pursue") in moved


def test_snapshot_first_run_has_no_baseline():
    assert load_latest_snapshot("sweep", "never_seen") is None
    # same-day snapshot is not its own baseline (strictly-older rule)
    save_snapshot("sweep", "today_only", [{"source": "s", "source_id": "1"}])
    assert load_latest_snapshot("sweep", "today_only") is None


def test_sweep_slim_records_shapes():
    results = {
        "sam.gov": [{"source": "sam.gov", "source_id": "N1", "title": "T",
                     "api_url": "https://sam.gov/opp/N1",
                     "response_deadline": "2026-07-20"}],
        "triage": {"N1": {"verdict": "pursue", "reason": "fit"}},
        "news": {"items": [{"title": "Item", "url": "https://n.gov/1",
                            "published": "Mon, 06 Jul 2026"},
                           {"title": "no url skipped"}]},
        "federal_register": {"kw": [{"title": "FR doc", "url": "https://fr.gov/1",
                                     "publication_date": "2026-07-01"}]},
        "cisa_kev": {"matched": [{"cve": "CVE-2026-1", "vendor": "V", "product": "P"}],
                     "catalog_url": "https://kev.gov"},
        "gdelt": {"error": "HTTP 429"},  # failed sources are skipped, not fatal
    }
    recs = sweep_slim_records(results)
    by_src = {r["source"]: r for r in recs}
    assert by_src["sam.gov"]["verdict"] == "pursue"
    assert by_src["news"]["source_id"] == "https://n.gov/1"
    assert by_src["federal_register"]["source_id"] == "https://fr.gov/1"
    assert by_src["cisa_kev"]["source_id"] == "CVE-2026-1"
    assert len(recs) == 4  # url-less news item dropped, error source ignored


# ---- agency-name normalization (the .title() casing bug) ----------------------
def test_normalize_agency_name_matches_toptier_casing():
    assert normalize_agency_name("Department of Defense") == "Department of Defense"
    assert normalize_agency_name("DEPT OF DEFENSE.US ARMY") == "Department of Defense"
    assert normalize_agency_name(
        "VETERANS AFFAIRS, DEPARTMENT OF.VA TECH ACQUISITION CENTER"
    ) == "Department of Veterans Affairs"
    assert normalize_agency_name("HOMELAND SECURITY, DEPARTMENT OF") == "Department of Homeland Security"
    assert normalize_agency_name(None) is None
    # the regression: never 'Department Of Defense'
    assert "Of " not in (normalize_agency_name("Department of Defense") or "")


# ---- agency breakdown aggregate + full-list persistence ------------------------
def test_market_evidence_persists_full_awards_and_breakdown(monkeypatch):
    import tools.api.usaspending as usa

    def fake_post(url, json=None, **kw):
        if "spending_by_category" in url:
            return {"results": [
                {"name": "Department of Homeland Security", "amount": 20_000_000.0, "code": "070"},
                {"name": "Department of Defense", "amount": 12_500_000.0, "code": "097"},
            ]}
        return {"results": [
            {"Award ID": f"A{i}", "Recipient Name": f"Vendor {i % 3}",
             "Award Amount": 1000.0 * (i + 1), "Awarding Agency": "DHS",
             "Period of Performance Start Date": "2026-01-01",
             "generated_internal_id": f"id{i}"}
            for i in range(12)
        ]}

    monkeypatch.setattr(usa, "post_json", fake_post)
    ev = UsaSpendingSource().market_evidence("541512", agency="Department of Defense")
    assert len(ev["awards"]) == 12            # FULL list persisted
    assert len(ev["sample_awards"]) == 5      # compat samples kept
    assert ev["agency_filter_used"] == "Department of Defense"  # scoping works now
    bd = ev["agency_breakdown"]
    assert bd[0] == {"agency": "Department of Homeland Security",
                     "total_obligated": 20_000_000.0, "agency_code": "070",
                     "source": usa.AGENCY_CATEGORY_URL}


def test_agency_breakdown_failure_is_additive_not_fatal(monkeypatch):
    import tools.api.usaspending as usa

    def fake_post(url, json=None, **kw):
        if "spending_by_category" in url:
            raise RuntimeError("HTTP 500")
        return {"results": []}

    monkeypatch.setattr(usa, "post_json", fake_post)
    ev = UsaSpendingSource().market_evidence("541512")
    assert ev["agency_breakdown"] == [{"error": "HTTP 500"}]
    assert ev["summary"]["award_count"] == 0


# ---- numeric vehicle ceilings ---------------------------------------------------
def test_vehicles_carry_numeric_ceilings():
    d = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "reference", "vehicles.json")))
    v = {x["name"]: x for x in d["vehicles"]}
    assert v["SEWP VI"]["ceiling_usd"] == 60_000_000_000.0
    assert v["SEWP VI"]["ceiling_period"] == "2026-2036"
    assert v["OASIS+"]["ceiling_usd"] is None      # 'No ceiling' stays null
    assert all("ceiling_usd" in x for x in d["vehicles"])
    # TAM inputs exist: at least one verified vehicle with a numeric ceiling
    assert any(x["ceiling_usd"] and x.get("verified") for x in d["vehicles"])
