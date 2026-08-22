"""Partner bench: who can prime what the client cannot. Offline, injected HTTP."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.records import GoldenRecord  # noqa: E402
from agents.golden_press.partner_bench import (  # noqa: E402
    partner_bench, qualified_partners, set_aside_code,
)


def _award(name, amount, agency, gid="G1", piid="P1"):
    return {"Recipient Name": name, "Award Amount": amount,
            "Awarding Agency": agency, "Award ID": piid,
            "generated_internal_id": gid, "NAICS": "541519",
            "Start Date": "2025-01-01"}


def _poster(by_agency: dict):
    """Fake USAspending. Keys are the toptier name filtered on, or None."""
    calls = []

    def post(url, json=None, **kw):
        filters = (json or {}).get("filters", {})
        agencies = filters.get("agencies") or []
        key = agencies[0]["name"] if agencies else None
        calls.append({"agency": key, "filters": filters})
        return {"results": list(by_agency.get(key, []))}
    return post, calls


# ---- the crosswalk is measured, never guessed ------------------------------ #
@pytest.mark.parametrize("text,code", [
    ("Total Small Business Set-Aside (FAR 19.5)", "SBA"),
    ("Women-Owned Small Business", "WOSB"),
    ("8(a) Sole Source", "8AN"),
    ("8a Competed", "8A"),
    ("Service-Disabled Veteran-Owned Small Business Set Aside", "SDVOSBC"),
    ("HUBZone Set Aside", "HZC"),
    ("  total small business set-aside (far 19.5)  ", "SBA"),
])
def test_known_set_asides_map_to_probed_fpds_codes(text, code):
    """Every code here returned a real awardee when probed against
    USAspending on 2026-07-29. None is from memory."""
    assert set_aside_code(text) == code


def test_an_unprobed_set_aside_returns_none_rather_than_a_guess():
    """LAS returned zero awards on probe, so no code is known to represent
    Local Area Set-Aside. Guessing one would answer the notice with firms
    qualified for something else, which is worse than saying "unmapped"."""
    assert set_aside_code("Local Area Set-Aside (FAR 26.2)") is None
    assert set_aside_code("some new program invented next year") is None
    assert set_aside_code("") is None


# ---- the buying agency is asked for, not hoped for ------------------------- #
def test_the_buying_agency_is_queried_separately_from_the_nation():
    """THE BUG THIS PINS. Ranking a national list by dollars and hoping a firm
    from the buying agency lands in the window does not work. Measured live:
    set-aside SBA in NAICS 541519 returns Treasury, DHS, Interior and DHS
    nationally, so a VA notice got four firms with no VA record. The agency
    must be a FILTER, and it takes its own query."""
    post, calls = _poster({
        "Department of Veterans Affairs": [_award("ThunderCat", 14e6, "Department of Veterans Affairs", "GVA", "VA1")],
        None: [_award("FCN", 233e6, "Department of the Treasury", "GT", "T1")],
    })
    out = qualified_partners("SBA", naics="541519",
                             agency="VETERANS AFFAIRS, DEPARTMENT OF",
                             limit=5, post=post)
    assert [c["agency"] for c in calls] == ["Department of Veterans Affairs", None]
    # ThunderCat holds 1/16th the dollars and still ranks first: it is inside
    # the office that is buying.
    assert out[0]["partner"] == "ThunderCat" and out[0]["same_agency"] is True
    assert out[1]["partner"] == "FCN" and out[1]["same_agency"] is False


def test_the_sam_agency_string_is_normalized_before_it_is_sent():
    """SAM writes "DEPT OF DEFENSE", USAspending writes "Department of
    Defense". A raw compare matched 2.9% of live notices; normalizing matched
    99.3%. The whole same-agency ranking rode on this."""
    post, calls = _poster({})
    qualified_partners("SBA", agency="DEPT OF DEFENSE", post=post)
    assert calls[0]["agency"] == "Department of Defense"


def test_no_agency_means_one_national_query_only():
    post, calls = _poster({None: [_award("FCN", 1.0, "Treasury")]})
    out = qualified_partners("SBA", agency=None, post=post)
    assert [c["agency"] for c in calls] == [None]
    assert out[0]["same_agency"] is False


def test_the_set_aside_code_is_always_sent_as_a_filter():
    """Without it the bench is just "big firms in this NAICS", which is the
    opposite of the question: those are mostly firms that CANNOT bid it."""
    post, calls = _poster({})
    qualified_partners("WOSB", naics="541519", post=post)
    assert calls[0]["filters"]["set_aside_type_codes"] == ["WOSB"]
    assert calls[0]["filters"]["naics_codes"] == ["541519"]


# ---- one firm, several awards --------------------------------------------- #
def test_a_firm_appears_once_carrying_its_strongest_proof():
    post, _ = _poster({
        "Department of Veterans Affairs": [
            _award("Acme", 5.0, "Department of Veterans Affairs", "G-VA-SMALL", "VA-S"),
            _award("Acme", 90.0, "Department of Veterans Affairs", "G-VA-BIG", "VA-B"),
        ],
        None: [_award("Acme", 900.0, "Department of the Treasury", "G-T", "T1")],
    })
    out = qualified_partners("SBA", agency="VETERANS AFFAIRS, DEPARTMENT OF",
                             post=post)
    assert len(out) == 1
    p = out[0]
    assert p["awards_seen"] == 3
    # The 900.0 Treasury award is larger and is NOT the proof: presence at the
    # buying agency outranks size somewhere else.
    assert p["proof_award_id"] == "VA-B" and p["same_agency"] is True


def test_a_usaspending_outage_returns_an_empty_bench_and_never_raises():
    """Teaming is additive evidence and must never sink a press."""
    def boom(url, json=None, **kw):
        raise RuntimeError("usaspending down")
    assert qualified_partners("SBA", naics="541519", post=boom) == []


# ---- the bench over a pack ------------------------------------------------- #
def _notice(rid, set_aside, naics="541519", agency="DEPT OF DEFENSE",
            deadline="2026-08-01"):
    return GoldenRecord(record_id=rid, lane="L1_notice", title=f"t{rid}",
                        set_aside=set_aside, naics=naics, agency=agency,
                        response_deadline=deadline)


def test_only_notices_the_client_cannot_prime_get_a_bench():
    """An open notice needs no partner, and a sole source is a decided buy."""
    recs = [_notice("open", "No Set aside used"),
            _notice("blank", None),
            _notice("ss", "8(a) Sole Source"),
            _notice("sba", "Total Small Business Set-Aside (FAR 19.5)")]
    seen = []

    def fetch(code, **kw):
        seen.append(code)
        return [{"partner": "Acme"}]
    out = partner_bench(recs, fetch=fetch)
    assert seen == ["SBA"]
    assert [n["record_id"] for n in out["notices"]] == ["sba"]
    assert out["receipt"]["notices_seen"] == 4
    assert out["receipt"]["restricted"] == 1


def test_notices_sharing_a_lane_share_one_lookup():
    """5,263 live restricted notices collapse to a few dozen distinct
    (set-aside, NAICS) pairs. Querying per notice would be thousands of calls
    for the same answer."""
    recs = [_notice(f"n{i}", "Total Small Business Set-Aside (FAR 19.5)")
            for i in range(25)]
    calls = []
    out = partner_bench(recs, fetch=lambda c, **k: calls.append(c) or [])
    assert len(calls) == 1
    assert out["receipt"]["groups"] == 1
    assert len(out["notices"]) == 25


def test_a_different_naics_is_a_different_lane():
    recs = [_notice("a", "Total Small Business Set-Aside (FAR 19.5)", naics="541519"),
            _notice("b", "Total Small Business Set-Aside (FAR 19.5)", naics="334519")]
    calls = []
    partner_bench(recs, fetch=lambda c, naics=None, **k: calls.append(naics) or [])
    assert sorted(calls) == ["334519", "541519"]


def test_an_unmapped_set_aside_is_reported_not_silently_dropped():
    recs = [_notice("las", "Local Area Set-Aside (FAR 26.2)")]
    out = partner_bench(recs, fetch=lambda c, **k: [])
    assert out["receipt"]["unmapped_set_aside"] == 1
    assert out["receipt"]["unmapped_strings"] == ["Local Area Set-Aside (FAR 26.2)"]
    assert out["notices"] == []


def test_the_lookup_cap_is_loud():
    """A silent cap reads as "this is the whole market" when it is not."""
    recs = [_notice(f"n{i}", "Total Small Business Set-Aside (FAR 19.5)",
                    naics=f"5415{i:02d}") for i in range(6)]
    out = partner_bench(recs, max_lookups=2, fetch=lambda c, **k: [])
    assert out["receipt"]["lookups"] == 2
    assert out["receipt"]["lookups_skipped_by_cap"] == 4


def test_soonest_deadline_first():
    """The bench is only useful before the door shuts."""
    recs = [_notice("late", "Women-Owned Small Business", deadline="2026-12-01"),
            _notice("soon", "Women-Owned Small Business", deadline="2026-08-02")]
    out = partner_bench(recs, fetch=lambda c, **k: [])
    assert [n["record_id"] for n in out["notices"]] == ["soon", "late"]


def test_the_bench_spends_no_sam_quota():
    """SAM is 20/day and permanent. USAspending is keyless and unmetered, and
    every figure in this lane comes from USAspending plus the stored extract."""
    out = partner_bench([_notice("a", "Women-Owned Small Business")],
                        fetch=lambda c, **k: [])
    assert out["receipt"]["metered_quota_spent"] == 0


def test_a_small_client_primes_the_set_aside_and_needs_no_bench():
    recs = [_notice("sba", "Total Small Business Set-Aside (FAR 19.5)")]
    out = partner_bench(recs, client_is_small=True, fetch=lambda c, **k: [])
    assert out["notices"] == [] and out["receipt"]["restricted"] == 0
