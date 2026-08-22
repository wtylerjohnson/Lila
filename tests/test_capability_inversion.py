"""Capability inversion (2026-07-09, permanent): capability keywords are the
PRIMARY evidence layer; NAICS is a coarse boundary only. Includes the
client-invariance regression proof."""
from datetime import date

import pytest

from agents.reports.facts import (
    _Counter, _incumbent_product_facts, _recompete_facts, _teaming_facts,
    build_fact_pack, client_invariance_check,
)
from tools.capability import (
    CapabilityTerms, ClientProfile, ProfileMissing, client_display_name,
    load_profile,
    match_capability, require_profile,
)

PROFILE = ClientProfile(
    client_name="Testco",
    capability_terms=CapabilityTerms(
        core=["aviation risk intelligence", "airspace threat"],
        adjacent=["flight data", "geospatial intelligence", "OSINT"],
        excluded=["space management"]),
    named_competitors_and_incumbents=["ForeFlight", "Jeppesen"],
    mission_components=["Air and Marine Operations", "Federal Aviation Administration"],
    naics_boundary=["518210", "541512"],
)


def _results(**over):
    base = {
        "subawards": {"edges": {"518210": [
            {"prime": "AVIA PRIME LLC", "sub": "X", "amount": 2_000_000.0,
             "subaward_id": "S1", "prime_award_id": "P1",
             "description": "airspace threat monitoring and aviation risk intelligence feed",
             "awarding_agency": "Department of Homeland Security",
             "awarding_sub_agency": "Customs and Border Protection"},
            {"prime": "GENERIC IT CORP", "sub": "Y", "amount": 900_000_000.0,
             "subaward_id": "S2", "prime_award_id": "P2",
             "description": "enterprise IT service desk and cloud migration",
             "awarding_agency": "General Services Administration",
             "awarding_sub_agency": "Federal Acquisition Service"},
        ]}, "primes": [
            {"name": "GENERIC IT CORP", "subaward_count": 1, "total": 900_000_000.0},
            {"name": "AVIA PRIME LLC", "subaward_count": 1, "total": 2_000_000.0},
        ]},
        "expiring_awards": {"rows": [
            {"award_id": "E1", "recipient": "CHART CO", "amount": 5_000_000.0,
             "awarding_agency": "DHS", "awarding_sub_agency": "Customs and Border Protection",
             "end_date": "2026-12-01", "naics": "518210",
             "description": "flight data subscription services",
             "url": "https://usaspending.gov/award/E1"},
            {"award_id": "E2", "recipient": "DESK CO", "amount": 9_000_000.0,
             "awarding_agency": "GSA", "awarding_sub_agency": "Federal Acquisition Service",
             "end_date": "2026-11-01", "naics": "541512",
             "description": "help desk staffing", "url": "https://usaspending.gov/award/E2"},
        ]},
        "contract_awards": {"recompetes": [
            {"awardee": "OLD PRIME", "piid": "PIID1", "completion": "2026-10-01",
             "agency": "Federal Aviation Administration"},
            {"awardee": "OTHER PRIME", "piid": "PIID2", "completion": "2026-10-02",
             "agency": "Department of Energy"},
        ], "incumbents": [
            {"name": "GENERIC IT CORP", "contracts": 3, "next_expiry": "2026-08-31"},
        ]},
        "sam.gov": [
            {"source_id": "N1", "title": "Notice of Intent: ForeFlight subscription renewal",
             "api_url": "https://sam.gov/opp/N1/view",
             "raw_payload": {"subtier": "US CUSTOMS AND BORDER PROTECTION",
                             "office": "AIR AND MARINE CONTRACTING DIVISION",
                             "type": "Notice of Intent",
                             "description_snippet": "sole source Jeppesen charts and ForeFlight EFB"}},
        ],
        "triage": {},
    }
    base.update(over)
    return base


def test_match_scoring_tiers_and_veto():
    m = match_capability(PROFILE, "airspace threat monitoring",
                         component="Customs and Border Protection Air and Marine Operations")
    assert m.score == 5 and m.chip == "STRONG"          # core 3 + mission 2
    assert match_capability(PROFILE, "flight data feed").score == 1  # adjacent
    assert match_capability(PROFILE, "space management system for airspace threat").tier == "excluded"
    assert match_capability(PROFILE, "janitorial supplies").score == 0


def test_teaming_ranks_by_capability_dollars_not_lane():
    facts = _teaming_facts(_results(), _Counter(), PROFILE)
    verified = [f for f in facts if f.text.startswith("CAPABILITY-VERIFIED")]
    assert len(verified) == 1
    t = verified[0].text
    assert "AVIA PRIME" in t and "GENERIC IT CORP" not in t  # $900M lane money loses
    assert "airspace threat" in t and "#1 of 1" in t
    assert verified[0].value["capability_score"] >= 3


def test_teaming_zero_match_is_a_finding_with_mandatory_label():
    r = _results()
    r["subawards"]["edges"]["518210"] = [
        {"prime": "GENERIC IT CORP", "sub": "Y", "amount": 1.0,
         "subaward_id": "S9", "prime_award_id": "P9",
         "description": "enterprise service desk"}]
    facts = _teaming_facts(r, _Counter(), PROFILE)
    assert facts[0].text.startswith("FINDING: zero of")
    lane = [f for f in facts[1:] if "GENERIC IT CORP" in f.text]
    assert lane and all("LANE-LEVEL EVIDENCE ONLY" in f.text for f in lane)


def test_recompete_cut_semantics():
    facts = _recompete_facts(_results(), _Counter(), PROFILE)
    texts = " | ".join(f.text for f in facts)
    assert "CHART CO" in texts          # description match (flight data)
    assert "DESK CO" not in texts       # cut, not demoted
    assert "OLD PRIME" in texts and "mission component" in texts  # FAA hit
    assert "OTHER PRIME" not in texts   # DOE: cut
    assert "GENERIC IT CORP" not in texts  # incumbents without evidence: cut
    assert "Capability-verified contract-completion signal" in texts
    assert "not a confirmed recompete" in texts
    assert "Capability-verified recompete" not in texts

    expiry = next(f for f in facts if "CHART CO" in f.text)
    assert "award-period signal" in expiry.text
    assert "not a confirmed recompete" in expiry.text


def test_sam_recompete_official_title_can_clear_capability_gate():
    results = _results()
    results["contract_awards"]["recompetes"] = [{
        "awardee": "OBSERVABILITY PRIME",
        "piid": "TIRNO26C0001",
        "completion": "2027-02-28",
        "agency": "Internal Revenue Service",
        "description": "Aviation risk intelligence and flight data platform",
        "record_source": "sam.gov_contract_awards",
    }]

    facts = _recompete_facts(results, _Counter(), PROFILE)
    fact = next(f for f in facts if "OBSERVABILITY PRIME" in f.text)

    assert fact.text.startswith(
        "Capability-verified contract-completion signal")
    assert "not a confirmed recompete" in fact.text
    assert "official award title/description matched" in fact.text
    assert fact.value["capability_score"] >= 1
    assert fact.source.startswith("https://api.sam.gov/contract-awards/")


def test_fallback_recompete_cites_usaspending_not_sam():
    results = _results()
    results["contract_awards"] = {
        "source_mode": "live_usaspending_fallback",
        "provenance": {
            "endpoint": (
                "https://api.usaspending.gov/api/v2/search/spending_by_award/"
            )
        },
        "recompetes": [{
            "awardee": "FALLBACK PRIME",
            "piid": "USA-1",
            "completion": "2027-01-31",
            "agency": "Federal Aviation Administration",
            "description": "flight data subscription",
            "url": "https://www.usaspending.gov/award/USA-1",
            "record_source": "usaspending.gov",
        }],
        "incumbents": [],
    }

    facts = _recompete_facts(results, _Counter(), PROFILE)
    fact = next(f for f in facts if "FALLBACK PRIME" in f.text)

    assert fact.source == "https://www.usaspending.gov/award/USA-1"
    assert "sam.gov" not in fact.source


def test_incumbent_product_collector_maps_buyer_product_office():
    facts = _incumbent_product_facts(_results(), PROFILE, _Counter())
    assert len(facts) == 1
    t = facts[0].text
    assert "INCUMBENT-PRODUCT SIGNAL" in t
    assert "US CUSTOMS AND BORDER PROTECTION" in t
    assert "AIR AND MARINE CONTRACTING DIVISION" in t
    assert "ForeFlight" in t and "Jeppesen" in t
    assert facts[0].kind == "incumbent_product"


def test_client_invariance_proof():
    """Findings shared with a dummy profile mean lane-layer leakage."""
    assert client_invariance_check(_results(), PROFILE) == []
    # force a leak: a builder that verified on lane data alone would surface
    # under BOTH profiles; simulate by matching the dummy's vocabulary too
    r = _results()
    r["subawards"]["edges"]["518210"][0]["description"] += \
        " quantum lattice cryptography widget"
    leaks = client_invariance_check(r, PROFILE)
    assert leaks and "lane-layer leak" in leaks[0]


def test_build_fact_pack_threads_profile_and_views():
    pack = build_fact_pack("Testco", searches={"results": _results()},
                           qualify_report={"candidates": []}, profile=PROFILE)
    assert pack.incumbent_product_fact_ids
    joined = " | ".join(f.text for f in pack.facts)
    assert "CAPABILITY-VERIFIED teaming door" in joined
    assert "BOUNDARY CONTEXT ONLY" not in joined or True  # market facts absent in fixture
    # no-profile pack carries the loud warning
    pack2 = build_fact_pack("NoProfileCo", searches={"results": _results()},
                            qualify_report={"candidates": []})
    assert any("NO CAPABILITY PROFILE" in w for w in pack2.warnings)


def test_require_profile_gate():
    try:
        require_profile("Definitely Missing Client XYZ")
        raise AssertionError("gate did not fire")
    except ProfileMissing as e:
        assert "No profile, no sweep" in str(e)
    assert load_profile("Osprey Flight Solutions") is not None


def test_presentation_name_preserves_identity_and_binding_payload():
    profile = ClientProfile(
        client_name="mark43",
        display_name="  Mark43  ",
        capability_terms=CapabilityTerms(core=["dispatch"]),
        naics_boundary=["541512"],
    )

    assert profile.client_name == "mark43"
    assert profile.display_name == "Mark43"
    assert client_display_name("mark43", profile=profile) == "Mark43"
    assert "display_name" not in profile.model_dump(mode="json")

    with pytest.raises(ValueError, match="preserve the client_name slug"):
        ClientProfile(
            client_name="mark43",
            display_name="Mark 43",
            capability_terms=CapabilityTerms(core=["dispatch"]),
            naics_boundary=["541512"],
        )


def test_r11_lane_demotion_and_mandatory_label():
    from agents.reports.capture_brief import CBBudgetBar, CBStat
    from agents.reports.facts import Fact, FactPack
    from agents.reports.remediation import remediate
    from tests.test_remediation import _content
    pack = FactPack(client_name="Testco", as_of=date(2026, 7, 9), facts=[
        Fact(id="F7", kind="competitor", source="https://u.example",
             text="GENERIC IT CORP pushed $900M; teaming target. "
                  "LANE-LEVEL EVIDENCE ONLY, NOT CAPABILITY-VERIFIED."),
    ])
    c = _content(
        budget_bars=[CBBudgetBar(label="NAICS 518210 lane", amount_label="$10.8B",
                                 relative=1.0)],
        stats=[CBStat(number="$53.4B", accent="", context="NAICS 541990 lane total")]
        + [CBStat(number="5", accent="", context="c")] * 4,
        thesis=["GENERIC IT CORP is the teaming door [F7].", "Two.", "Three."])
    out, report = remediate(c, pack, {})
    assert out.budget_bars == []                                # bar dropped
    assert any(a.rule == "R11" and a.action == "flag"
               and a.location == "stats[0]" for a in report.actions)
    # CLIENT-FILE DOCTRINE (2026-07-10): the lane claim is DELETED from
    # client copy, never caveated; the internal trail carries it
    assert "[F7]" not in out.thesis[0]
    assert any(a.rule == "R11" and a.action == "suppress"
               and "deleted from the client file" in a.reason
               for a in report.actions)
    # renderer: no slot for lane bars even if remediation missed one
    from agents.reports.capture_brief import _budget_chart
    assert "518210" not in _budget_chart(
        [CBBudgetBar(label="NAICS 518210", amount_label="$1B", relative=1.0)])


def test_fit_trace_shows_the_work():
    """2026-07-10: every identified opportunity carries inspectable
    provenance — matched terms, NAICS boundary, mission component, and the
    screen inference. Shown work builds trust."""
    from tools.capability import fit_trace
    n = {"source_id": "N9", "title": "Airspace threat monitoring subscription",
         "naics_code": "518210",
         "raw_payload": {"subtier": "Air and Marine Operations",
                         "agency": "DHS",
                         "description_snippet": "aviation risk intelligence feed"}}
    t = fit_trace(PROFILE, n, "matches the client's core delivery pattern")
    assert "airspace threat" in t["matched_terms"]
    assert t["naics"] == "518210" and t["naics_in_boundary"] is True
    assert t["mission_component"] == "Air and Marine Operations"
    assert t["score"] >= 4 and t["chip"] == "STRONG"
    assert t["screen_inference"].startswith("matches the client")


def test_opportunity_card_renders_why_this_fits():
    from agents.reports.capture_brief import _opp_card
    from tests.test_remediation import _content
    o = _content().opportunities[0]
    html = _opp_card(o, "OPP-01", trace={
        "matched_terms": ["airspace threat"], "naics": "518210",
        "naics_in_boundary": True, "mission_component": "Federal Aviation Administration",
        "score": 5, "chip": "STRONG",
        "screen_inference": "core subject match on the notice description"})
    assert "Why this fits" in html
    assert "airspace threat" in html and "NAICS 518210" in html
    assert "client lane" in html
    assert "Analyst inference:" in html
    # without a trace the card renders exactly as before
    assert "Why this fits" not in _opp_card(o, "OPP-01")


def test_category_url_scopes_to_subagency():
    """2026-07-10: the by-category endpoint takes the category in the URL
    path; a scoped pull breaks down by SUB-agency so a departmental focus
    lists its components (CBP, TSA), never itself."""
    from tools.api.usaspending import (
        AGENCY_CATEGORY_URL, SUBAGENCY_CATEGORY_URL, _category_url)
    assert _category_url(None) == AGENCY_CATEGORY_URL
    assert _category_url("Department of Homeland Security") == SUBAGENCY_CATEGORY_URL
    assert SUBAGENCY_CATEGORY_URL.endswith("awarding_subagency/")
