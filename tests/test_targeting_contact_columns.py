"""Band 09's contact columns under the phone policy, and the tier
measurement (Tasks 3, 4, 5).

The band now renders what the confirmed email path actually produces: a work
email, a direct line when one is positively classified, and the switchboard
in its OWN column. The policy is fail-closed, so a number the response never
typed does not reach the client artifact at all.

TASK 4 is a MEASUREMENT, not a change. The band tiers targets by substring
matching on titles; Apollo returns its own seniority and department
taxonomy in the same response. Both attributions are printed side by side
and the disagreement rate is reported. The logic is deliberately NOT
swapped in this edition.
"""

from __future__ import annotations

import os
import re
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import phone_policy  # noqa: E402
from agents.golden_press import render as render_mod  # noqa: E402
from agents.golden_press.decision_rules import build_decisions  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402
from agents.golden_press.targeting_rules import (  # noqa: E402
    build_targeting, tier_for_title)
from agents.golden_press.validate import validate_press  # noqa: E402


def _award(**kw):
    base = dict(record_id="GS35F001", lane="L2_entity_award",
                title="Network performance monitoring support",
                description="Network performance monitoring support",
                agency="Department of the Treasury",
                sub_agency="Internal Revenue Service",
                recipient="Carahsoft Technology",
                obligated_dollars=1_920_000.0,
                url="https://www.usaspending.gov/award/CONT_AWD_GS35F001",
                period_end="2026-08-31", entity_hits=["AcmeFlow"])
    base.update(kw)
    return GoldenRecord(**base)


def _rival(**kw):
    base = dict(record_id="RIV1", recipient="Rival Prime Inc",
                entity_hits=["OtherMonitor"], obligated_dollars=900_000.0,
                period_end="2026-11-30",
                url="https://www.usaspending.gov/award/CONT_AWD_RIV1")
    base.update(kw)
    return _award(**base)


def _pack():
    pack = EvidencePack(
        client_name="Acme Networks", generated_at="2026-07-27T12:00:00Z",
        records=[_award(), _rival()],
        research={"entities": {"competitor": ["OtherMonitor"],
                               "product": ["AcmeFlow"]}})
    pack.decisions = build_decisions(pack)
    return pack


def _with(pack, phones, **extra):
    base = build_targeting(pack)
    spec_id = base["specs"][0]["spec_id"]
    contact = {
        "contact_id": "a1", "spec_id": spec_id, "tier": 2,
        "name": "Dana Reyes", "title": "Contracting Officer",
        "organization": "Internal Revenue Service",
        "email": "dana.reyes@irs.gov", "email_status": "verified",
        "phones": phones,
        "provenance": {"class": "apollo", "retrieved_at": "2026-07-27",
                       "source": "apollo.people_bulk_match"},
        # SCREENING IS A PUBLICATION REQUIREMENT (2026-08-06): a contact
        # without an accepted screen is withheld, so every fixture that
        # expects to render must state its screen explicitly.
        "screen": {"state": "accepted", "verdict": "pass", "source_class": "federal_component", "findings": [], "cautions": []},
    }
    contact.update(extra)
    pack.targeting = build_targeting(pack, contacts={
        "receipt": {"attempted": True, "source": "apollo.people_bulk_match",
                    "retrieved_at": "2026-07-27", "result_count": 1,
                    "calls_made": 1},
        "contacts": [contact]})
    return pack


def _band(content):
    m = re.search(r'<section class="band" id="targeting".*?</section>',
                  content, re.S)
    assert m
    return m.group(0)


_MAIN = {"number": "+1 202-324-3000", "type": phone_policy.ORG_MAIN,
         "basis": "source field organization.primary_phone",
         "basis_kind": "apollo_field"}
_DIRECT = {"number": "+1 202-555-0142", "type": phone_policy.WORK_DIRECT,
           "basis": "apollo type flag 'direct'",
           "basis_kind": "apollo_type_flag"}
_MOBILE = {"number": "+1 917-555-0100", "type": phone_policy.MOBILE,
           "basis": "apollo type flag 'mobile'",
           "basis_kind": "apollo_type_flag"}


def test_the_confirmed_email_path_renders_with_its_verified_address():
    pack = _with(_pack(), [_MAIN, _DIRECT])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "dana.reyes@irs.gov" in band
    assert "Work email" in band and "Direct line" in band
    assert validate_press(content, content, pack)["ok"]


def test_the_main_line_gets_its_own_column_and_never_the_dial(monkeypatch):
    """The operator ruling, enforced at the renderer: a switchboard has real
    value and may never be presented as somebody's direct line."""
    pack = _with(_pack(), [_MAIN])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "Main line" in band
    assert "+1 202-324-3000" in band
    # the dial column stays empty rather than borrowing the switchboard
    assert "<td>not resolved</td>" in band
    assert "MAIN LINE IS NOT A DIRECT LINE" in band
    assert validate_press(content, content, pack)["ok"]


def test_a_mobile_renders_in_its_own_column_by_default(monkeypatch):
    """OPERATOR RULING 2026-08-06: nothing is withheld. A mobile renders in
    the Mobile column and never in the Direct line column."""
    monkeypatch.delenv(phone_policy.INCLUDE_MOBILE_ENV, raising=False)
    pack = _with(_pack(), [_MAIN, _MOBILE])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "+1 917-555-0100" in band
    assert "<th>Mobile</th>" in band
    assert "NUMBERS WITHHELD" not in band
    # it is still in the store, for the operator
    assert any(p["type"] == phone_policy.MOBILE
               for p in pack.targeting["contacts"][0]["phones"])
    assert validate_press(content, content, pack)["ok"]


def test_the_switch_can_still_suppress_a_mobile(monkeypatch):
    """Suppression is now the explicit act, and it is disclosed."""
    monkeypatch.setenv(phone_policy.INCLUDE_MOBILE_ENV, "off")
    pack = _with(_pack(), [_MAIN, _MOBILE])
    band = _band(render_mod.render_content_region(pack, prose={}))
    assert "+1 917-555-0100" not in band
    assert "NUMBERS WITHHELD" in band


def test_a_number_with_no_established_type_still_never_renders(monkeypatch):
    """The integrity rule survives the policy change: an entry carrying no
    type at all is not a classified number and cannot reach the page."""
    monkeypatch.delenv(phone_policy.INCLUDE_MOBILE_ENV, raising=False)
    pack = _with(_pack(), [{"number": "+1 202-555-9999",
                            "basis": "no apollo type flag"}])
    band = _band(render_mod.render_content_region(pack, prose={}))
    assert "+1 202-555-9999" not in band


def test_a_number_with_no_classification_receipt_never_enters_the_store():
    """The store's own admission law: a number with no basis is not a
    classified number, whatever its type field claims."""
    pack = _with(_pack(), [{"number": "+1 202-555-1111",
                            "type": phone_policy.WORK_DIRECT}])
    from agents.golden_press.targets_store import admissible
    rows, dropped = admissible(
        {"contacts": pack.targeting["contacts"]},
        spec_ids=[s["spec_id"] for s in pack.targeting["specs"]])
    assert rows[0]["phones"] == []
    assert dropped["phones_unclassified"] == 1


def test_zero_live_calls_at_press_time_still_holds(monkeypatch):
    """Task 3's standing requirement, re-proved now that enrichment exists:
    the press reads the store and the network seam is never touched."""
    import tools.apollo_targets as apollo_mod

    def forbidden(*a, **k):
        raise AssertionError("the press made a live Apollo call")

    monkeypatch.setattr(apollo_mod, "_http_post", forbidden)
    pack = _with(_pack(), [_MAIN, _DIRECT])
    content = render_mod.render_content_region(pack, prose={})
    assert validate_press(content, content, pack)["ok"]
    assert "dana.reyes@irs.gov" in _band(content)


# ---- TASK 4 · tier attribution, measured side by side -------------------- #
# The two people actually enriched on 2026-08-06, with Apollo's own
# attribution as returned. This is the whole measured sample: seniority and
# departments arrive ONLY on the enrichment response, so a larger sample
# costs credits and was not taken.
_ENRICHED = [
    {"name": "Brian McCormick", "title": "Supervisory Contracting Officer",
     "apollo_seniority": "manager", "apollo_departments": ["master_finance"],
     "apollo_subdepartments": ["sourcing_procurement"],
     "apollo_functions": ["finance"]},
    {"name": "Geoff Swanstrom", "title": "Chief Technology Officer",
     "apollo_seniority": "c_suite",
     "apollo_departments": ["c_suite", "master_engineering_technical"],
     "apollo_subdepartments": ["information_technology_executive",
                               "engineering_technical",
                               "technology_operations"],
     "apollo_functions": ["information_technology"]},
]

# A candidate Apollo-side mapping, written for MEASUREMENT ONLY. Note that
# procurement lives under master_finance in Apollo's taxonomy, so a rule
# built on `departments` alone would file a Contracting Officer as finance;
# the usable signal is `subdepartments`.
_APOLLO_SUBDEPT_TIER = {
    "sourcing_procurement": 2,
    "information_technology_executive": 1,
    "engineering_technical": 3,
    "technology_operations": 3,
}

_SPEC = {"seeks_tiers": [1, 2],
         "persona_titles": {
             "1": ["Program Manager", "Program Director",
                   "Chief Information Officer",
                   "Deputy Chief Information Officer",
                   "Director of Network Operations",
                   "Chief Technology Officer"],
             "2": ["Contracting Officer", "Contract Specialist",
                   "Procurement Analyst", "Director of Acquisition",
                   "Head of Contracting Activity",
                   "Small Business Specialist"]}}


def _apollo_tier(person):
    """Lowest (strongest) tier any subdepartment maps to, or None."""
    tiers = [_APOLLO_SUBDEPT_TIER[s]
             for s in person["apollo_subdepartments"]
             if s in _APOLLO_SUBDEPT_TIER]
    return min(tiers) if tiers else None


def test_tier_attribution_measured_substring_against_apollo(capsys):
    """MEASUREMENT ONLY. Prints both attributions and the disagreement rate.
    The substring logic is NOT swapped in this session."""
    rows, disagreements = [], 0
    for person in _ENRICHED:
        substring = tier_for_title(_SPEC, person["title"])
        apollo = _apollo_tier(person)
        agree = substring == apollo
        disagreements += 0 if agree else 1
        rows.append((person["name"], person["title"], substring, apollo,
                     "agree" if agree else "DISAGREE",
                     ",".join(person["apollo_subdepartments"])))
    with capsys.disabled():
        print("\n  TIER ATTRIBUTION, substring vs Apollo subdepartments")
        print(f"  {'name':18s} {'substring':>9s} {'apollo':>6s}  verdict")
        for name, _t, sub, apo, verdict, subs in rows:
            print(f"  {name:18s} {sub!s:>9s} {apo!s:>6s}  {verdict}  ({subs})")
        rate = disagreements / len(rows)
        print(f"  disagreement rate: {disagreements}/{len(rows)} = {rate:.0%}"
              f"   SAMPLE SIZE {len(rows)}, too small to conclude")
    # Pin the measured result so a later change to either rule shows up here.
    assert disagreements == 0
    assert [r[2] for r in rows] == [2, 1]
    assert [r[3] for r in rows] == [2, 1]


def test_apollo_departments_alone_would_misfile_a_contracting_officer():
    """The finding worth keeping: a naive swap to `departments` files
    procurement under finance. Only `subdepartments` carries the signal."""
    mccormick = _ENRICHED[0]
    assert mccormick["apollo_departments"] == ["master_finance"]
    assert "sourcing_procurement" in mccormick["apollo_subdepartments"]
    assert _apollo_tier(mccormick) == 2


def test_apollo_carries_multi_tier_signal_the_substring_rule_collapses():
    """Where Apollo is richer: the CTO reads as tier 1 AND tier 3. The
    substring rule can only return one rung."""
    swanstrom = _ENRICHED[1]
    tiers = {_APOLLO_SUBDEPT_TIER[s] for s in swanstrom["apollo_subdepartments"]
             if s in _APOLLO_SUBDEPT_TIER}
    assert tiers == {1, 3}
    assert tier_for_title(_SPEC, swanstrom["title"]) == 1
