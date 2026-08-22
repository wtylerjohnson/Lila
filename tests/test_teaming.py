"""Sub and teaming routes. Offline: no network, subaward fetch is injected."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402
from agents.golden_press.teaming import (  # noqa: E402
    fetch_subawards, restricted_lanes, set_aside_posture, teaming_rollup,
)


# ---- set-aside posture ----------------------------------------------------- #
@pytest.mark.parametrize("value", ["None", "No Set aside used", "N/A"])
def test_open_competition_is_a_direct_bid(value):
    """"No Set aside used" reads like a restriction and is the opposite of
    one. Counting it as restricted overstated apexanalytix's closed market by
    55% (1,409 of 2,574 measured rows).

    Every value here is one the government AFFIRMATIVELY WROTE. A blank field
    is not in this list and must never be added to it: see the test below."""
    p = set_aside_posture(value)
    assert p["route"] == "direct" and p["can_prime"] is True


@pytest.mark.parametrize("value", ["", None, "   "])
def test_an_unstated_set_aside_is_unknown_never_open(value):
    """THE BUG THIS PINS. The first cut counted a blank set_aside as open
    competition. Measured on the store that was 43,096 of 78,577 rows (54.8%),
    and among notices still live it is 7,929 of 15,605 (50.8%): more than half
    the market told "any responsible source may bid" on the strength of a
    field the government left empty.

    For a client that can only sub, that is the most expensive possible error.
    It reads as a door standing open, so the seller walks past the partner
    search and straight at a prime bid that may not be available to them.
    can_prime is None, not False: the answer is unknown, and the honest move
    is to open the notice."""
    p = set_aside_posture(value)
    assert p["route"] == "unknown"
    assert p["can_prime"] is None
    assert "check the notice" in p["why"]


def test_unknown_stays_unknown_for_a_small_business_too():
    """Size cannot resolve a fact the notice never stated."""
    p = set_aside_posture("", client_is_small=True)
    assert p["route"] == "unknown" and p["can_prime"] is None


def test_a_large_business_cannot_prime_a_set_aside():
    p = set_aside_posture("Total Small Business Set-Aside (FAR 19.5)",
                          client_is_small=False)
    assert p["route"] == "teaming"
    assert p["can_prime"] is False
    assert "large business cannot prime" in p["why"]


def test_the_same_notice_is_a_direct_bid_for_a_small_business():
    """Client size is an OPERATOR FACT. No federal dataset states whether a
    client meets the size standard, and guessing sends a seller at a door
    they cannot open."""
    p = set_aside_posture("Total Small Business Set-Aside (FAR 19.5)",
                          client_is_small=True)
    assert p["route"] == "direct" and p["can_prime"] is True


@pytest.mark.parametrize("value", ["8(a) Sole Source", "SDVOSB Sole Source"])
def test_sole_source_is_closed_not_a_teaming_route(value):
    """The agency has already named its intended awardee. Reporting that as a
    partnering opportunity sends a seller after a decided buy."""
    p = set_aside_posture(value)
    assert p["route"] == "closed" and p["can_prime"] is False


# ---- subawards ------------------------------------------------------------- #
def test_a_failed_subaward_fetch_returns_empty_and_never_raises():
    """Teaming is additive evidence and must never sink a press."""
    def boom(url, json=None, **kw):
        raise RuntimeError("usaspending down")
    assert fetch_subawards("CONT_AWD_X", post=boom) == []


def test_the_subaward_endpoint_is_only_ever_asked_by_award_id():
    """THE TRAP THIS PINS, verified live 2026-07-29. /api/v2/subawards/ does
    not reject a filter it does not understand, it IGNORES it and answers
    anyway: posting recipient_search_text=["CACI"] returned three subawards
    belonging to MAYA BRIDGE LLC. Any name-based lookup through this endpoint
    produces unrelated rows that look exactly like an answer.

    award_id is the only key this endpoint honors, so it is the only key sent.
    """
    sent = {}

    def post(url, json=None, **kw):
        sent.update(json or {})
        return {"results": []}
    fetch_subawards("CONT_AWD_X", post=post)
    assert set(sent) == {"award_id", "limit", "page"}
    assert sent["award_id"] == "CONT_AWD_X"


# ---- which primes actually take subs --------------------------------------- #
def test_a_prime_with_no_subaward_record_is_unproven_not_refusing():
    """Measured live: Dell Federal, World Wide Technology and CDW-G carry no
    subaward records while Leidos, Northrop and Serco carry many. That is
    resellers against integrators. But absence of a record is not a record of
    absence, and the wording must not claim the prime refuses to team."""
    from agents.golden_press.teaming import subcontracting_practice
    out = subcontracting_practice(
        [{"Recipient Name": "Dell Federal", "generated_internal_id": "G1"}],
        fetch=lambda gid, **k: [])
    row = out["primes"][0]
    assert row["takes_subs"] is False
    assert "unproven" in row["why"] and "may resell" in row["why"]
    assert out["takes_subs"] == 0 and out["examined"] == 1


def test_primes_are_ranked_by_what_actually_flows_through_them():
    from agents.golden_press.teaming import subcontracting_practice
    data = {"G-big": [{"subawardee": "A", "amount": 900.0}],
            "G-small": [{"subawardee": "B", "amount": 5.0}],
            "G-none": []}
    out = subcontracting_practice(
        [{"Recipient Name": "None Co", "generated_internal_id": "G-none"},
         {"Recipient Name": "Small Co", "generated_internal_id": "G-small"},
         {"Recipient Name": "Big Co", "generated_internal_id": "G-big"}],
        fetch=lambda gid, **k: data[gid])
    assert [p["prime"] for p in out["primes"]] == ["Big Co", "Small Co", "None Co"]
    assert out["takes_subs"] == 2


def test_a_full_page_of_subawards_is_declared_truncated():
    """USAspending pages. A prime that fills the page has AT LEAST this many
    subawards, and reporting the page size as the total would understate the
    flow while looking precise."""
    from agents.golden_press.teaming import subcontracting_practice
    out = subcontracting_practice(
        [{"Recipient Name": "Leidos", "generated_internal_id": "G1"}],
        page=3, fetch=lambda gid, limit=None, **k: [
            {"subawardee": f"S{i}", "amount": 1.0} for i in range(3)])
    assert out["primes"][0]["truncated"] is True
    assert out["any_truncated"] is True


def test_an_award_with_no_internal_id_is_skipped_not_guessed():
    from agents.golden_press.teaming import subcontracting_practice
    out = subcontracting_practice([{"Recipient Name": "No ID Co"}],
                                  fetch=lambda gid, **k: [])
    assert out["primes"] == [] and out["examined"] == 0


def test_no_award_id_costs_no_call():
    calls = []
    assert fetch_subawards("", post=lambda *a, **k: calls.append(1)) == []
    assert calls == []


def test_subawards_are_shaped_and_named():
    def fake(url, json=None, **kw):
        return {"results": [
            {"recipient_name": "TRUE ZERO TECHNOLOGIES, LLC", "amount": 3796165.0,
             "description": "PROCUREMENT IN SUPPORT OF THE SOW",
             "action_date": "2024-09-05", "subaward_number": "ZZ1"},
            {"recipient_name": "", "amount": 1.0},          # nameless: dropped
        ]}
    rows = fetch_subawards("CONT_AWD_X", post=fake)
    assert len(rows) == 1
    assert rows[0]["subawardee"] == "TRUE ZERO TECHNOLOGIES, LLC"
    assert rows[0]["prime_award_id"] == "CONT_AWD_X"


# ---- the rollup ------------------------------------------------------------ #
def _pack(records):
    return EvidencePack(client_name="Acme", generated_at="2026-07-29T00:00:00Z",
                        records=records)


def _award(rid, gid, recipient):
    return GoldenRecord(record_id=rid, lane="L2_entity_award",
                        generated_internal_id=gid, recipient=recipient,
                        title=f"{recipient} award", url=f"https://x/{rid}")


def test_a_partner_under_several_primes_outranks_a_bigger_one_under_one():
    """A firm subbing to one prime is that prime's supplier. A firm subbing to
    several has a federal practice, and that is the one worth a call."""
    subs = {
        "G1": [{"recipient_name": "MULTI LLC", "amount": 10.0}],
        "G2": [{"recipient_name": "MULTI LLC", "amount": 10.0},
               {"recipient_name": "WHALE INC", "amount": 9_000_000.0}],
    }
    def fake(gid, **kw):
        return [{"subawardee": s["recipient_name"], "amount": s["amount"],
                 "description": "", "action_date": "", "subaward_number": "",
                 "prime_award_id": gid} for s in subs.get(gid, [])]
    out = teaming_rollup(_pack([_award("a", "G1", "Prime One"),
                                _award("b", "G2", "Prime Two")]), fetch=fake)
    assert [p["subawardee"] for p in out["partners"]] == ["MULTI LLC", "WHALE INC"]
    assert out["partners"][0]["prime_count"] == 2


def test_the_client_subbing_to_itself_is_not_teaming():
    def fake(gid, **kw):
        return [{"subawardee": "Acme", "amount": 5.0, "description": "",
                 "action_date": "", "subaward_number": "", "prime_award_id": gid}]
    out = teaming_rollup(_pack([_award("a", "G1", "Prime One")]),
                         client_aliases=["Acme"], fetch=fake)
    assert out["partners"] == []


def test_only_l2_awards_are_examined():
    notice = GoldenRecord(record_id="n1", lane="L1_notice",
                          generated_internal_id="GX")
    seen = []
    out = teaming_rollup(_pack([notice]), fetch=lambda g, **k: seen.append(g) or [])
    assert seen == [] and out["prime_awards_examined"] == 0


# ---- routes ---------------------------------------------------------------- #
def test_routes_split_a_pack_by_what_the_client_can_actually_bid():
    recs = [
        GoldenRecord(record_id="open", lane="L1_notice", set_aside="None"),
        GoldenRecord(record_id="sba", lane="L1_notice",
                     set_aside="Total Small Business Set-Aside (FAR 19.5)"),
        GoldenRecord(record_id="ss", lane="L1_notice", set_aside="8(a) Sole Source"),
        GoldenRecord(record_id="blank", lane="L1_notice", set_aside=None),
    ]
    out = restricted_lanes(recs, client_is_small=False)
    assert out["counts"] == {"direct": 1, "teaming": 1, "closed": 1, "unknown": 1}
    assert out["teaming"][0]["record_id"] == "sba"
    # The blank one lands in its own bucket. It used to inflate "direct",
    # which is the count a seller reads as "doors I can walk through alone".
    assert out["unknown"][0]["record_id"] == "blank"
    assert out["direct"][0]["record_id"] == "open"


# ---- size is LOOKED UP, not asked ------------------------------------------ #
def _profile_stub(business_types):
    """Echoes the searched name back, the way USAspending does on a real hit."""
    def post(url, json=None, **kw):
        asked = ((json or {}).get("filters", {})
                 .get("recipient_search_text") or ["X"])[0]
        return {"results": [{"Recipient Name": f"{asked}, INC.".upper(),
                             "recipient_id": "rid-1"}]}
    def get(url, **kw):
        return {"name": "X CORP", "uei": "ABC123", "business_types": business_types}
    return post, get


def test_a_large_business_is_identified_from_federal_data():
    """Verified live 2026-07-29: Booz Allen and Carahsoft both return
    other_than_small_business. This is the government's own classification,
    keyless and public, and it replaces asking the operator."""
    from agents.golden_press.teaming import business_profile
    post, get = _profile_stub(["category_business", "other_than_small_business"])
    p = business_profile("Booz Allen", post=post, get=get)
    assert p["resolved"] is True and p["is_small"] is False
    assert p["set_asides_eligible"] == []


def test_a_woman_owned_small_business_unlocks_the_right_set_asides():
    """IT PARTNERS, INC. is women_owned_small_business in live data. A WOSB
    set-aside is a closed door to a large client and an open one to them."""
    from agents.golden_press.teaming import business_profile
    post, get = _profile_stub(["small_business", "women_owned_small_business"])
    p = business_profile("IT Partners", post=post, get=get)
    assert p["is_small"] is True
    assert "WOSB" in p["set_asides_eligible"]
    assert "Total Small Business Set-Aside" in p["set_asides_eligible"]


def test_an_unresolvable_firm_is_unknown_not_large():
    """apexanalytix returns no federal prime award. Unknown and large are
    different answers and must not be collapsed."""
    from agents.golden_press.teaming import business_profile
    p = business_profile("Nonexistent Co",
                         post=lambda *a, **k: {"results": []},
                         get=lambda *a, **k: {})
    assert p["resolved"] is False
    assert p["is_small"] is None


def test_a_near_miss_company_may_not_answer_for_the_client():
    """THE BUG THIS PINS. recipient_search_text is a full-text search, so a
    different firm comes back looking exactly like a hit, and the first cut
    took row zero without checking the name. Probed live 2026-07-29: a search
    around "apexanalytix" surfaces APEXA, an unrelated company, whose size
    would then have been reported as the client's and would have decided
    whether the whole partner-bench lane fires."""
    from agents.golden_press.teaming import business_profile

    def post(url, json=None, **kw):
        return {"results": [{"Recipient Name": "APEXA", "recipient_id": "rid-9"}]}
    p = business_profile("apexanalytix", post=post, get=lambda *a, **k: {})
    assert p["resolved"] is False and p["is_small"] is None
    assert "different firm" in p["why"] and "APEXA" in p["why"]


def test_a_longer_official_name_still_resolves():
    """The guard is one-directional. "APEX ANALYTIX LLC" carries the whole
    name asked for and is the same company; APEXA does not and is not."""
    from agents.golden_press.teaming import business_profile

    def post(url, json=None, **kw):
        return {"results": [{"Recipient Name": "APEX ANALYTIX LLC",
                             "recipient_id": "rid-1"}]}
    p = business_profile("apexanalytix", post=post,
                         get=lambda *a, **k: {"name": "APEX ANALYTIX LLC",
                                              "business_types": ["small_business"]})
    assert p["resolved"] is True and p["is_small"] is True


def test_a_firm_recorded_as_both_sizes_is_unknown_not_large():
    """Observed live 2026-07-29: OSPREY FLIGHT SOLUTIONS, INC. carries
    small_business AND other_than_small_business together. That is not a data
    error. Size is judged per award against the NAICS size standard, so a firm
    can be small in one line of work and large in another. Resolving that to a
    single flag sounds certain about a question whose answer depends on the
    notice."""
    from agents.golden_press.teaming import business_profile
    post, get = _profile_stub(["small_business", "other_than_small_business"])
    p = business_profile("Osprey Flight Solutions", post=post, get=get)
    assert p["resolved"] is True
    assert p["is_small"] is None
    assert p["size_conflict"] is True
    assert "BOTH" in p["why"] and "NAICS size standard" in p["why"]


def test_a_failed_lookup_never_raises():
    from agents.golden_press.teaming import business_profile
    def boom(*a, **k):
        raise RuntimeError("usaspending down")
    p = business_profile("Anyone", post=boom, get=boom)
    assert p["resolved"] is False and p["is_small"] is None


@pytest.mark.parametrize("types,set_aside,expected", [
    (["women_owned_small_business"], "Women-Owned Small Business", True),
    (["women_owned_small_business"], "8(a) Sole Source", False),
    (["service_disabled_veteran_owned_business"],
     "Service-Disabled Veteran-Owned Small Business Set Aside", True),
    (["small_business"], "Total Small Business Set-Aside (FAR 19.5)", True),
    ([], "Total Small Business Set-Aside (FAR 19.5)", False),
])
def test_partner_opens_the_door_matches_the_right_program(types, set_aside, expected):
    from agents.golden_press.teaming import partner_opens_the_door
    assert partner_opens_the_door(types, set_aside) is expected
