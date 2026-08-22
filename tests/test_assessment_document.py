"""AssessmentDocument composer: deterministic joins, ground-truth counts,
count-token resolution, compound phrases, TAM honesty, watchlist delta.
Offline — synthetic sweep fixtures; snapshot dir isolated by conftest."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.document import (  # noqa: E402
    agency_group_phrase, build_document, int_to_word, resolve_count_tokens,
)
from tools.snapshots import save_snapshot, sweep_slim_records  # noqa: E402

AS_OF = date(2026, 7, 6)


def _searches(n_pursue=3):
    notices, triage = [], {}
    specs = [
        ("P1", "Video Wall Modernization", "DEPT OF DEFENSE.INARNG", "334310", "2026-07-30"),
        ("P2", "Courtroom AV Refresh", "U.S. COURTS", "334310", "2026-08-15"),
        ("P3", "KVM Signal Distribution", "DEPT OF DEFENSE.INARNG", "541512", "2026-09-01"),
        ("P4", "Extra Pursuit", "GSA", "541512", "2026-09-20"),
    ]
    for i, (sid, title, agency, naics, deadline) in enumerate(specs[:n_pursue]):
        notices.append({"source": "sam.gov", "source_id": sid, "title": title,
                        "agency": agency, "naics_code": naics,
                        "response_deadline": deadline,
                        "api_url": f"https://sam.gov/opp/{sid}/view",
                        "raw_payload": {"solicitation": f"70RSAT26R000000{i + 1}",
                                        "type": "Solicitation"}})
        triage[sid] = {"verdict": "pursue", "reason": "fit"}
    notices.append({"source": "sam.gov", "source_id": "M1", "title": "Watch this",
                    "agency": "GSA", "naics_code": "334310",
                    "response_deadline": "2026-10-01",
                    "api_url": "https://sam.gov/opp/M1/view"})
    triage["M1"] = {"verdict": "monitor", "reason": "forming"}
    triage["D1"] = {"verdict": "discard", "reason": "no fit"}

    return {"client": "Testco", "generated_at": "2026-07-06T09:00:00", "results": {
        "sam.gov": notices,
        "triage": triage,
        "usaspending.gov": [{
            "naics_code": "334310",
            "summary": {"award_count": 50, "total_obligated": 120_000_000.0,
                        "median_award": 900_000.0,
                        "top_incumbents": ["AVI-SPL", "Diversified"]},
            "sample_awards": [],
            "awards": [{"recipient": "AVI-SPL", "amount": 5_000_000.0,
                        "awarding_agency": "Department of Defense",
                        "award_id": "W91ABC24C0001",
                        "start_date": "2024-03-01",
                        "url": "https://www.usaspending.gov/award/CONT_AWD_W91ABC24C0001"},
                       # second pool agency; recipient is NOT a tracked
                       # competitor so the competitive joins are untouched
                       {"recipient": "Zeta Integrators", "amount": 2_400_000.0,
                        "awarding_agency": "General Services Administration",
                        "award_id": "47QTCA22D0042"}],
            "addressable": {
                "keywords_used": ["KVM matrix switch", "secure video distribution"],
                "total_obligated": 9_000_000.0,
                "window_years": 3,
                "agency_breakdown": [
                    {"agency": "Department of Defense", "total_obligated": 6_000_000.0,
                     "agency_code": "097", "source": "https://api.usaspending.gov/x"},
                    {"agency": "General Services Administration",
                     "total_obligated": 3_000_000.0,
                     "agency_code": "047", "source": "https://api.usaspending.gov/x"},
                ],
                "awards": [{"recipient": "AVI-SPL", "amount": 2_000_000.0,
                            "awarding_agency": "Department of Defense"}],
            },
            "agency_breakdown": [
                {"agency": "Department of Defense", "total_obligated": 80_000_000.0,
                 "agency_code": "097", "source": "https://api.usaspending.gov/x"},
                {"agency": "Department of Justice", "total_obligated": 12_000_000.0,
                 "agency_code": "015", "source": "https://api.usaspending.gov/x"},
            ],
        }],
        "subawards": {"primes": [{"name": "GDIT", "subaward_count": 3, "total": 60_000_000.0}]},
        "news": {"items": [{"title": "DoD AV budget grows", "url": "https://n.gov/1",
                            "published": "2026-07-01", "source": "trade"}]},
        "dossiers": {"records": [{"id": "P1", "title": "Video Wall Modernization",
                                  "fit_verdict": "strong_fit", "vehicle": None}]},
        "contract_awards": {"error": "HTTP 429"},
    }}


def test_counts_are_ground_truth_and_joined():
    doc = build_document("Testco", searches=_searches(3), qualify=None, as_of=AS_OF)
    c = doc.counts()
    assert c["pursuits"] == 3
    assert c["pursue_notices"] == 3
    assert c["monitor_notices"] == 1
    assert c["dossiers"] == 1
    assert c["agencies"] == 2               # DoD + DOJ from the breakdown
    assert c["competitors"] == 3            # AVI-SPL, Diversified, GDIT
    # ranked by grade score; every pursuit graded with a worksheet
    assert [p.rank for p in doc.board.pursuits] == [1, 2, 3]
    assert all(len(p.grade.dimensions) == 5 for p in doc.board.pursuits)
    # P1 has dossier depth -> its fit uses dossier precedence
    p1 = next(p for p in doc.board.pursuits if p.source_id == "P1")
    assert "dossier" in next(d for d in p1.grade.dimensions if d.dimension == "fit").basis
    # verifier source fields joined from the raw notice payload
    assert p1.notice_type == "Solicitation"
    assert p1.solicitation == "70RSAT26R0000001"
    assert p1.locked is None                # the FULL document is never gated
    # competitor drill-down: the award rows behind the dollars ride the model,
    # largest first, every row citable (id + USAspending link)
    avi = next(x for x in doc.competitive.competitors if x.name == "AVI-SPL")
    assert avi.awards[0]["award_id"] == "W91ABC24C0001"
    assert avi.awards[0]["url"].startswith("https://www.usaspending.gov/award/")
    assert avi.awards[0]["naics"] == "334310"
    gdit = next(x for x in doc.competitive.competitors if x.name == "GDIT")
    assert gdit.awards == []                # subaward-flow prime: basis says why


def test_non_sam_row_in_sam_bucket_never_enters_live_board():
    """2026-07-10 live-lane rule: bucket placement is not provenance.

    A reachable web forecast accidentally written into the SAM bucket may
    remain available in the sweep, but cannot create a pursuit or live count.
    The zero-live document still assembles normally.
    """
    searches = _searches(0)
    searches["results"]["sam.gov"].append({
        "source": "web", "source_id": "WEB-FC-1",
        "title": "Agency modernization forecast",
        "agency": "GSA", "response_deadline": "2026-11-01",
        "api_url": "https://example.gov/forecast/WEB-FC-1",
    })
    searches["results"]["triage"]["WEB-FC-1"] = {
        "verdict": "pursue", "reason": "legacy misclassification"}

    doc = build_document("Testco", searches=searches, qualify=None, as_of=AS_OF)

    assert doc.board.pursuits == []
    assert doc.counts()["pursuits"] == 0
    assert doc.counts()["pursue_notices"] == 0
    assert any(n.get("source_id") == "WEB-FC-1"
               for n in searches["results"]["sam.gov"])


def test_count_tokens_and_words():
    doc = build_document("Testco", searches=_searches(4), qualify=None, as_of=AS_OF)
    text = ("{{COUNT_WORD:pursue_notices}} pursue-grade notices, "
            "{{COUNT:competitors}} competitors mapped, {{COUNT:unknown_thing}} left")
    resolved = resolve_count_tokens(text, doc.counts())
    assert resolved.startswith("four pursue-grade notices")
    assert "3 competitors mapped" in resolved
    assert "{{COUNT:unknown_thing}}" in resolved   # unknown stays for lint to flag
    assert int_to_word(21) == "21" and int_to_word(0) == "zero"


def test_compound_agency_phrase_assembled_not_freewritten():
    doc = build_document("Testco", searches=_searches(3), qualify=None, as_of=AS_OF)
    phrase = agency_group_phrase(doc.board.pursuits)
    assert phrase == ("Two DEPT OF DEFENSE.INARNG postings and "
                      "one U.S. COURTS solicitation")


def test_tam_math_and_agency_map_honesty():
    doc = build_document("Testco", searches=_searches(3), qualify=None, as_of=AS_OF)
    m = doc.market
    assert m.tam_dollars == 120_000_000.0           # market components only
    market_kinds = {c.kind for c in m.components}
    assert "vehicle" in market_kinds                 # listed as access paths
    assert all(c.dollars for c in m.components if c.kind == "market")
    assert [a.agency for a in m.agency_map] == ["Department of Defense", "Department of Justice"]
    assert m.agency_map[0].dollars_label == "$80.0M"
    assert all(a.source.startswith("https://") for a in m.agency_map)
    # grounded annual framing (2026-07-06): the honest headline unit is
    # avg-per-year over the 3-year query window, never the raw 3-yr pile
    assert m.tam_annual_dollars == 40_000_000.0
    assert m.tam_annual_label == "$40.0M"
    # award-pool agency slices share the SAME basis as the headline, so any
    # surface showing both stays coherent (slices sum toward the total)
    assert [a.agency for a in m.award_agency_annual] == [
        "Department of Defense", "General Services Administration"]
    assert int(m.award_agency_annual[0].dollars) == 1_666_666   # 5M / 3yr
    assert m.award_agency_annual[0].dollars_label == "$1.7M"
    # the keyword-scoped (addressable) slice: annualized, per-agency, and a
    # math-table row LISTED but never summed into the lane TAM (tam_dollars
    # stays 120M above)
    assert m.addressable_annual_dollars == 3_000_000.0          # 9M / 3yr
    assert m.addressable_annual_label == "$3.0M"
    assert [(a.agency, a.dollars_label) for a in m.addressable_agency_annual] == [
        ("Department of Defense", "$2.0M"),
        ("General Services Administration", "$1.0M")]
    assert "addressable" in {c.kind for c in m.components}
    assert m.addressable_keywords[0] == "KVM matrix switch"
    assert not any("addressable" in g for g in doc.gaps)
    # incumbent-expiry gap is explicit, not silent
    assert any("contract_awards" in g or "incumbent-expiry" in g for g in doc.gaps)


def test_addressable_gap_when_sweep_predates_the_pull():
    s = _searches(2)
    s["results"]["usaspending.gov"][0].pop("addressable")
    doc = build_document("Testco", searches=s, qualify=None, as_of=AS_OF)
    m = doc.market
    assert m.addressable_annual_dollars is None
    assert m.addressable_agency_annual == []
    assert any("addressable" in g and "run_market_refresh" in g for g in doc.gaps)


def test_agency_map_gap_when_breakdown_missing():
    s = _searches(2)
    s["results"]["usaspending.gov"][0].pop("agency_breakdown")
    doc = build_document("Testco", searches=s, qualify=None, as_of=AS_OF)
    assert doc.market.agency_map == []
    assert any("agency_breakdown" in g or "agency map" in g for g in doc.gaps)


def test_watchlist_delta_against_snapshot():
    s = _searches(3)
    # last week's snapshot: P1 existed with an earlier deadline; P2 absent
    old = sweep_slim_records(s["results"])
    old = [r for r in old if r["source_id"] != "P2"]
    for r in old:
        if r["source_id"] == "P1":
            r["response_deadline"] = "2026-07-20"
    save_snapshot("sweep", "testco", old, as_of=date(2026, 6, 29))

    doc = build_document("Testco", searches=s, qualify=None, as_of=AS_OF)
    w = doc.watchlist
    assert w.last_refreshed == "2026-07-06T09:00:00"
    kinds = {(e.kind, e.id) for e in w.entries}
    assert ("new", "P2") in kinds
    assert any(k == "changed" and i == "P1" for k, i in kinds)
    changed = next(e for e in w.entries if e.kind == "changed" and e.id == "P1")
    assert "2026-07-20 → 2026-07-30" in changed.detail
    assert any(e.kind == "standing" and e.id == "M1" for e in w.entries)
    assert "new" in w.delta_note


def test_first_pull_watchlist_has_no_fabricated_motion():
    doc = build_document("FreshCo", searches={**_searches(2), "client": "FreshCo"},
                         qualify=None, as_of=AS_OF)
    assert "first pull" in doc.watchlist.delta_note
    assert all(e.kind == "standing" for e in doc.watchlist.entries)


def _amended_searches():
    """P3 posted twice under one solicitation number: an amendment pair."""
    s = _searches(3)
    notices = s["results"]["sam.gov"]
    p3 = next(n for n in notices if n["source_id"] == "P3")
    p3["posted_date"] = "2026-06-20"
    notices.append({**p3, "source_id": "P3A",
                    "title": "KVM Signal Distribution (Amended)",
                    "posted_date": "2026-07-02", "response_deadline": "2026-09-15",
                    "api_url": "https://sam.gov/opp/P3A/view"})
    s["results"]["triage"]["P3A"] = {"verdict": "pursue", "reason": "fit"}
    return s


def test_amendment_pair_dedupes_to_one_pursuit():
    doc = build_document("Testco", searches=_amended_searches(), qualify=None, as_of=AS_OF)
    c = doc.counts()
    # one solicitation = one card = one count, everywhere a client reads
    assert c["pursuits"] == 3 and c["pursue_notices"] == 3
    assert doc.verdict_totals["pursue"] == 4          # raw notice tally: provenance
    merged = next(p for p in doc.board.pursuits if p.solicitation == "70RSAT26R0000003")
    assert merged.source_id == "P3A"                  # latest posting = live state
    assert merged.response_deadline == "2026-09-15"   # grading keys to latest dates
    assert merged.title == "KVM Signal Distribution (Amended)"
    assert [a["source_id"] for a in merged.amendments] == ["P3", "P3A"]
    # the change line states only what the postings themselves state
    assert merged.amendments[1]["change"] == "deadline 2026-09-01 → 2026-09-15"
    assert "change" not in merged.amendments[0]
    # unmerged pursuits carry no history
    assert all(not p.amendments for p in doc.board.pursuits if p is not merged)


def test_distinct_solicitations_never_merge():
    # P1 and P3 sit at the SAME office with different solicitation numbers
    doc = build_document("Testco", searches=_searches(3), qualify=None, as_of=AS_OF)
    assert doc.counts()["pursuits"] == 3
    assert len({p.solicitation for p in doc.board.pursuits}) == 3
    # notices with NO solicitation number never merge either
    s = _searches(3)
    for n in s["results"]["sam.gov"]:
        n.pop("raw_payload", None)
    doc2 = build_document("Testco", searches=s, qualify=None, as_of=AS_OF)
    assert doc2.counts()["pursuits"] == 3
    assert all(not p.amendments for p in doc2.board.pursuits)


def test_real_thinklogical_artifact_composes():
    """Pinned-fixture smoke: a real Thinklogical sweep composes without
    error, counts reconcile with its triage totals, gaps are explicit.

    2026-08-05: reads the pinned snapshot, not data/cleaned - every
    legitimate live re-sweep rewrote the live artifact and rotted the
    triage expectations (same class and same fix as the composer footprint
    pins of 2026-08-04)."""
    import gzip
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "pinned_searches_thinklogical.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        searches = json.load(handle)
    doc = build_document("Thinklogical", searches=searches, qualify=None, as_of=AS_OF)
    triage = searches["results"]["triage"]
    n_pursue = sum(1 for v in triage.values() if v.get("verdict") == "pursue")
    notices = {n.get("source_id"): n for n in searches["results"]["sam.gov"]}
    n_sols = len({(notices.get(sid) or {}).get("raw_payload", {}).get("solicitation")
                  or f"sid:{sid}"
                  for sid, v in triage.items() if v.get("verdict") == "pursue"})
    # counts are SOLICITATION-level: amendment pairs merge (Jul-6 sweep carries
    # one — W912L926Q9003 posted twice), the raw tally stays in verdict_totals
    assert doc.counts()["pursue_notices"] == len(doc.board.pursuits) == n_sols
    assert doc.verdict_totals.get("pursue", 0) == n_pursue >= n_sols
    assert doc.counts()["monitor_notices"] == sum(
        1 for v in triage.values() if v.get("verdict") == "monitor")
    assert doc.gaps  # qualify + dossiers missing -> said out loud
