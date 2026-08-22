"""FOCUS projection at the report joins (2026-07-12, operator-caught leak).

The filter-first convergence let a DHS deliverable render a DISA
displacement window as its own forming play: FOCUS sweeps deliberately carry
whole-market context layers (L18), the July-10 scoper fix lived only in the
agency runner, and the capture path trusted the artifact. Every join now
projects context layers through tools/agency_scope.scope_focus_results.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.document import build_document  # noqa: E402
from agents.reports.facts import build_fact_pack  # noqa: E402
from tools.agency_scope import scope_focus_results, scope_results  # noqa: E402


def _sweep(mode="focus", agencies=None):
    scope = ({"mode": mode, "agencies": agencies
              if agencies is not None else [{"abbr": "DHS",
                                             "name": "Department of Homeland Security"}]}
             if mode else None)
    sweep = {
        "client": "Testco",
        "results": {
            "sam.gov": [], "triage": {},
            "sam_census": {"active_screened": 3, "retrieved": 3,
                           "matched": 3, "complete": True},
            "incumbent_buyer_map": {
                "population_label": "buyer accounts",
                "buyers": [
                    {"buyer": "CUSTOMS AND BORDER PROTECTION",
                     "agency": "HOMELAND SECURITY, DEPARTMENT OF",
                     "incumbent": "SolarWinds", "product": "Orion",
                     "displacement_window": "2026-09",
                     "evidence_url": "https://usaspending.gov/award/H1"},
                    {"buyer": "DEFENSE INFORMATION SYSTEMS AGENCY",
                     "agency": "DEFENSE, DEPARTMENT OF",
                     "incumbent": "SolarWinds", "product": "Orion",
                     "displacement_window": "2026-08",
                     "evidence_url": "https://usaspending.gov/award/D1"},
                ],
            },
            "news": {"items": [
                {"title": "DHS modernizes border network monitoring",
                 "summary": "Department of Homeland Security program",
                 "url": "https://example.gov/dhs"},
                {"title": "DISA awards new cyber contract",
                 "summary": "Defense Information Systems Agency award",
                 "url": "https://example.gov/disa"},
            ]},
        },
    }
    if scope:
        sweep["search_scope"] = scope
    return sweep


def test_focus_join_drops_foreign_buyer_rows_and_news():
    results = scope_focus_results(_sweep())
    buyers = results["incumbent_buyer_map"]["buyers"]
    assert [b["buyer"] for b in buyers] == ["CUSTOMS AND BORDER PROTECTION"]
    titles = [n["title"] for n in results["news"]["items"]]
    assert titles == ["DHS modernizes border network monitoring"]


def test_all_scope_sweep_passes_through_untouched():
    sweep = _sweep(mode=None)
    results = scope_focus_results(sweep)
    assert results is sweep["results"]  # zero-cost identity, not a copy
    assert len(results["incumbent_buyer_map"]["buyers"]) == 2


def test_document_under_focus_carries_no_foreign_incumbents():
    doc = build_document("Testco", searches=_sweep(), qualify={},
                         _live_report=False)
    for row in doc.buyer_incumbents:
        joined = " ".join(list(row.agencies) or []) + " " + row.company
        assert "DEFENSE INFORMATION" not in joined.upper()


def test_facts_under_focus_mint_no_foreign_agency_rows():
    pack = build_fact_pack("Testco", searches=_sweep(), qualify_report={})
    for fact in pack.facts:
        assert "DEFENSE INFORMATION SYSTEMS" not in (fact.text or "").upper()


def test_multi_agency_focus_is_a_union_not_an_intersection():
    two = _sweep(agencies=[
        {"abbr": "DHS", "name": "Department of Homeland Security"},
        {"abbr": "DOD", "name": "Department of Defense"},
    ])
    results = scope_focus_results(two)
    buyers = {b["buyer"] for b in results["incumbent_buyer_map"]["buyers"]}
    assert buyers == {"CUSTOMS AND BORDER PROTECTION",
                      "DEFENSE INFORMATION SYSTEMS AGENCY"}


def test_single_agency_dict_signature_still_works():
    from tools.agencies import find
    scoped, stats = scope_results(_sweep()["results"], find("DHS"))
    assert [b["buyer"] for b in scoped["incumbent_buyer_map"]["buyers"]] == [
        "CUSTOMS AND BORDER PROTECTION"]


# ── Adversarial fixtures (2026-07-12): DISA/DoD/IRS/etc. inside a DHS
# artifact. Required by the scope-containment acceptance criteria. Covers the
# seven agencies the operator caught leaking: DISA, Army, GSA, IRS, Navy, VA,
# WHS — plus the trap that a naive gate would fail: unlisted DHS components
# (Secret Service, FLETC) must NOT be rejected. ──

def _adversarial_sweep():
    """A DHS-scoped sweep contaminated with every leaked foreign agency AND
    legitimate unlisted DHS components, across buyer map, recompete-shaped
    expiring awards, and news."""
    return {
        "client": "Testco",
        "search_scope": {"mode": "focus",
                         "agencies": [{"abbr": "DHS",
                                       "name": "Department of Homeland Security"}]},
        "results": {
            "sam.gov": [], "triage": {},
            "sam_census": {"active_screened": 4, "retrieved": 4,
                           "matched": 4, "complete": True},
            "incumbent_buyer_map": {
                "population_label": "buyer accounts",
                "buyers": [
                    {"buyer": "US SECRET SERVICE",
                     "agency": "HOMELAND SECURITY, DEPARTMENT OF",
                     "incumbent": "SolarWinds", "product": "Orion",
                     "evidence_url": "https://usaspending.gov/award/H1"},
                    {"buyer": "DEFENSE INFORMATION SYSTEMS AGENCY",
                     "agency": "DEFENSE, DEPARTMENT OF",
                     "incumbent": "SolarWinds", "product": "Orion",
                     "evidence_url": "https://usaspending.gov/award/D1"},
                    {"buyer": "INTERNAL REVENUE SERVICE",
                     "agency": "TREASURY, DEPARTMENT OF",
                     "incumbent": "Akamai", "product": "CDN",
                     "evidence_url": "https://usaspending.gov/award/T1"},
                    {"buyer": "WASHINGTON HEADQUARTERS SERVICES",
                     "agency": "DEFENSE, DEPARTMENT OF",
                     "incumbent": "Cloudflare", "product": "WAF",
                     "evidence_url": "https://usaspending.gov/award/W1"},
                ],
            },
            "expiring_awards": {"rows": [
                {"recipient": "PrimeA", "award_id": "E1",
                 "awarding_agency": "DEPARTMENT OF THE NAVY",
                 "description": "network monitoring services",
                 "end_date": "2026-10", "amount": 5000000.0},
                {"recipient": "PrimeB", "award_id": "E2",
                 "awarding_agency": "GENERAL SERVICES ADMINISTRATION",
                 "description": "packet capture platform", "end_date": "2026-11",
                 "amount": 3000000.0},
                {"recipient": "PrimeC", "award_id": "E3",
                 "awarding_agency": "DEPARTMENT OF VETERANS AFFAIRS",
                 "description": "network observability", "end_date": "2026-12",
                 "amount": 2000000.0},
                {"recipient": "PrimeD", "award_id": "E4",
                 "awarding_sub_agency": "TRANSPORTATION SECURITY ADMINISTRATION",
                 "awarding_agency": "HOMELAND SECURITY, DEPARTMENT OF",
                 "description": "network monitoring", "end_date": "2026-09",
                 "amount": 4000000.0},
            ]},
        },
    }


def test_adversarial_facts_carry_no_foreign_agency():
    from agents.reports.facts import build_fact_pack
    pack = build_fact_pack("Testco", searches=_adversarial_sweep(),
                           qualify_report={})
    FOREIGN = ["DEFENSE INFORMATION", "DEPARTMENT OF THE NAVY",
               "GENERAL SERVICES ADMIN", "VETERANS AFFAIRS",
               "INTERNAL REVENUE", "WASHINGTON HEADQUARTERS"]
    for fact in pack.facts:
        up = (fact.text or "").upper()
        assert not any(x in up for x in FOREIGN), fact.text


def test_adversarial_document_has_no_scope_violation():
    from agents.reports.document import build_document
    from tools.agency_scope import document_focus_violations
    sweep = _adversarial_sweep()
    doc = build_document("Testco", searches=sweep, qualify={},
                         _live_report=False)
    assert document_focus_violations(doc, sweep) == []


def test_adversarial_scoped_results_keep_dhs_drop_foreign():
    from tools.agency_scope import scope_focus_results
    results = scope_focus_results(_adversarial_sweep())
    buyers = {b["buyer"] for b in results["incumbent_buyer_map"]["buyers"]}
    # the unlisted DHS component is kept; every foreign buyer is dropped
    assert "US SECRET SERVICE" in buyers
    assert not ({"DEFENSE INFORMATION SYSTEMS AGENCY",
                 "INTERNAL REVENUE SERVICE",
                 "WASHINGTON HEADQUARTERS SERVICES"} & buyers)
    kept_awards = {r["award_id"]
                   for r in results["expiring_awards"]["rows"]}
    assert kept_awards == {"E4"}  # only the TSA (DHS) recompete survives


def test_document_guard_flags_a_foreign_pursuit_row():
    """The render-boundary guard itself: a hand-injected foreign pursuit
    agency is caught even if it somehow bypassed the fact gate."""
    from types import SimpleNamespace
    from tools.agency_scope import document_focus_violations
    sweep = _adversarial_sweep()
    doc = SimpleNamespace(
        board=SimpleNamespace(pursuits=[
            SimpleNamespace(agency="DEFENSE INFORMATION SYSTEMS AGENCY"),
            SimpleNamespace(agency="U.S. Secret Service"),
        ]),
        partnering=SimpleNamespace(plays=[]),
        buyer_incumbents=[])
    bad = document_focus_violations(doc, sweep)
    assert len(bad) == 1 and "DEFENSE INFORMATION" in bad[0]


def test_unlisted_dhs_component_is_never_foreign():
    from tools.agency_scope import agency_is_foreign
    from tools.agencies import find
    dhs = [find("DHS")]
    for legit in ["US SECRET SERVICE", "FEDERAL LAW ENFORCEMENT TRAINING CENTER",
                  "TRANSPORTATION SECURITY ADMINISTRATION",
                  "US CITIZENSHIP AND IMMIGRATION SERVICES"]:
        assert not agency_is_foreign(legit, dhs), legit


def test_unknown_agency_is_not_treated_as_foreign():
    from tools.agency_scope import agency_is_foreign
    from tools.agencies import find
    dhs = [find("DHS")]
    # an unclassifiable string is not proof of foreignness; the source-level
    # department filter is the authority for those
    assert agency_is_foreign("SOME BRAND NEW OFFICE", dhs) is False
