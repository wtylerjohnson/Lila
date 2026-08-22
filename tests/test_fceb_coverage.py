"""The FCEB list is a checkable coverage surface, not a belief.

Measured 2026-07-30: the civilian engagement preset admitted all 102 CISA
FCEB agencies, but 78 of them only through the fail-open rule ("ignorance
never excludes") — the code never RECOGNIZED them, it merely failed to
refuse them. tools/fceb.py makes the list a first-class catalog so
"we looked for every FCEB agency" is a per-agency receipt.

Two guarantees pinned here:
  1. The catalog and its matcher are sound: exact after normalization,
     collision-free, aliases only for shapes the normalizer cannot derive,
     and NOT-on-list entities (DoD, legislative branch, Smithsonian) stay
     unmatched rather than being guessed onto the list.
  2. The civilian engagement scope admits every FCEB agency. This is the
     tripwire for the fail-open dependency: if resolve_department or
     in_scope is ever tightened, this test fails with a NAMED agency
     instead of 78 of them silently vanishing from civilian engagements.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fceb import (  # noqa: E402
    build_index, census, load_catalog, match_agency,
)

CATALOG = load_catalog()
INDEX = build_index(CATALOG)


# --------------------------------------------------------------------------- #
# catalog integrity
# --------------------------------------------------------------------------- #
def test_the_catalog_carries_the_full_cisa_list():
    assert len(CATALOG) == 102


def test_acronyms_are_unique():
    acronyms = [e["acronym"] for e in CATALOG]
    assert len(set(acronyms)) == len(acronyms)


def test_the_index_builds_without_collisions():
    """build_index raises on a collision; this pins that it currently does
    not, so a future alias cannot silently shadow another agency."""
    assert len(build_index(CATALOG)) >= len(CATALOG)


# --------------------------------------------------------------------------- #
# the store's own naming shapes resolve
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("store_name,acronym", [
    ("VETERANS AFFAIRS, DEPARTMENT OF", "VA"),
    ("TREASURY, DEPARTMENT OF THE", "TREAS"),
    ("INTERIOR, DEPARTMENT OF THE", "DOI"),
    ("AGRICULTURE, DEPARTMENT OF", "USDA"),
    ("HOMELAND SECURITY, DEPARTMENT OF", "DHS"),
    ("EXPORT-IMPORT BANK OF THE US", "EXIM"),
    ("INTERNATIONAL TRADE COMMISSION, UNITED STATES (DUNS # 023599554)",
     "USITC"),
    ("INTERNATIONAL BOUNDARY AND WATER COMMISSION: US-MEXICO", "IBWC"),
    ("UNITED STATES AGENCY FOR GLOBAL MEDIA, BBG", "USAGM"),
    ("AGENCY FOR INTERNATIONAL DEVELOPMENT", "USAID"),
    ("OFFICE OF THE COMPTROLLER OF THE CURRENCY", "OCC"),
    ("FEDERAL ENERGY REGULATORY COMMISSION", "FERC"),
    ("NATIONAL AERONAUTICS AND SPACE ADMINISTRATION", "NASA"),
    ("GENERAL SERVICES ADMINISTRATION", "GSA"),
])
def test_store_shapes_resolve(store_name, acronym):
    """Every naming habit the live store exhibited on 2026-07-30: comma
    inversion, THE-handling, US-for-United-States, parentheticals, and the
    two alias-only shapes (IBWC's ': US-MEXICO', USAGM's ', BBG')."""
    assert match_agency(store_name, INDEX) == acronym


@pytest.mark.parametrize("near_name,acronym", [
    ("SOCIAL SECURITY ADVISORY BOARD", "SSAB"),
    ("SOCIAL SECURITY ADMINISTRATION", "SSA"),
    ("NATIONAL ENDOWMENT FOR THE ARTS", "NEA"),
    ("NATIONAL ENDOWMENT FOR THE HUMANITIES", "NEH"),
])
def test_near_twins_resolve_to_themselves(near_name, acronym):
    """The collision class that killed fuzzy matching: SSA vs SSAB and the
    two Endowments must never resolve onto each other."""
    assert match_agency(near_name, INDEX) == acronym


@pytest.mark.parametrize("name", [
    "DEPT OF DEFENSE",
    "LIBRARY OF CONGRESS",
    "ARCHITECT OF THE CAPITOL",
    "UNITED STATES GOVERNMENT PUBLISHING OFFICE",
    "SMITHSONIAN INSTITUTION",
    "GOVERNMENT ACCOUNTABILITY OFFICE",
    "POSTAL SERVICE",
    "OFFICE OF THE DIRECTOR OF NATIONAL INTELLIGENCE",
    "ADMINISTRATIVE OFFICE OF THE US COURTS",
    "",
    None,
])
def test_entities_not_on_the_list_stay_unmatched(name):
    """DoD, the legislative and judicial branches, and the independents CISA
    excludes are REPORTED as unmatched, never guessed onto the list."""
    assert match_agency(name, INDEX) is None


def test_postal_regulatory_commission_is_not_the_postal_service():
    assert match_agency("POSTAL REGULATORY COMMISSION", INDEX) == "PRC"
    assert match_agency("POSTAL SERVICE", INDEX) is None


# --------------------------------------------------------------------------- #
# the census receipt
# --------------------------------------------------------------------------- #
def _rows():
    return [
        {"agency": "TREASURY, DEPARTMENT OF THE",
         "subtier": "OFFICE OF THE COMPTROLLER OF THE CURRENCY"},
        {"agency": "TREASURY, DEPARTMENT OF THE", "subtier": "IRS"},
        {"agency": "ENERGY, DEPARTMENT OF",
         "subtier": "FEDERAL ENERGY REGULATORY COMMISSION"},
        {"agency": "DEPT OF DEFENSE", "subtier": "DEPT OF THE NAVY"},
        {"agency": "", "subtier": ""},
    ]


def test_every_fceb_agency_gets_a_row_even_at_zero():
    """"Searched, nothing found" and "never looked" must be
    distinguishable, so absence is a counted row, not a missing one."""
    report = census(_rows(), catalog=CATALOG)
    assert report["fceb_total"] == 102
    assert len(report["agencies"]) == 102
    assert report["with_notices"] + report["zero_notices"] == 102
    zero = next(a for a in report["agencies"] if a["acronym"] == "PC")
    assert zero["notices"] == 0


def test_subtier_matches_credit_agencies_that_only_live_there():
    """OCC and FERC never appear as top-level agencies in the store; without
    subtier crediting their receipt rows would read zero while their
    notices sat under Treasury and Energy."""
    report = census(_rows(), catalog=CATALOG)
    by = {a["acronym"]: a for a in report["agencies"]}
    assert by["OCC"]["notices"] == 1
    assert by["OCC"]["matched_subtier_strings"] == [
        "OFFICE OF THE COMPTROLLER OF THE CURRENCY"]
    assert by["FERC"]["notices"] == 1
    assert by["TREAS"]["notices"] == 2      # both Treasury rows, once each


def test_a_notice_is_never_double_credited_to_one_agency():
    rows = [{"agency": "SOCIAL SECURITY ADMINISTRATION",
             "subtier": "SOCIAL SECURITY ADMINISTRATION"}]
    report = census(rows, catalog=CATALOG)
    ssa = next(a for a in report["agencies"] if a["acronym"] == "SSA")
    assert ssa["notices"] == 1


def test_unmatched_agencies_are_reported_with_counts():
    report = census(_rows(), catalog=CATALOG)
    unmatched = {u["agency"]: u["notices"]
                 for u in report["unmatched_agencies"]}
    assert unmatched["DEPT OF DEFENSE"] == 1
    assert unmatched["(blank)"] == 1


# --------------------------------------------------------------------------- #
# the scope tripwire
# --------------------------------------------------------------------------- #
def test_the_civilian_scope_admits_every_fceb_agency():
    """THE GUARANTEE THIS FILE EXISTS FOR. 78 of these pass today only via
    the fail-open rule; if scope resolution is ever tightened, this fails
    naming the exact agency instead of the civilian universe silently
    shrinking. Wiring FCEB recognition INTO the scope resolver is not the
    fix for that failure: under the civilian preset's enumerated department
    list, recognition without membership flips an agency from admitted to
    refused. The scope preset is operator-owned either way."""
    from tools.relevance.scope import EngagementScope, in_scope

    civilian = EngagementScope(preset="civilian")
    refused = []
    for entry in CATALOG:
        ok, _basis = in_scope({"agency": entry["name"]}, civilian)
        if not ok:
            refused.append(entry["acronym"])
    assert refused == [], f"civilian scope now refuses FCEB agencies: {refused}"


# --------------------------------------------------------------------------- #
# the approved non-FCEB civilian buyers (operator ruling 2026-07-30)
#
# "if theres opps there than sure - why not": civilian scope means
# NON-MILITARY BUYERS, not strictly FCEB. Measured that day these entities
# carried 62 open notices, 5 open in tech NAICS.
# --------------------------------------------------------------------------- #
def test_every_approved_buyer_classifies_as_approved():
    from tools.fceb import NON_FCEB_CIVILIAN, classify_unmatched

    for name, branch in NON_FCEB_CIVILIAN.items():
        assert classify_unmatched(name) == f"approved_non_fceb:{branch}"


@pytest.mark.parametrize("store_name,klass", [
    ("DEPT OF DEFENSE", "excluded:defense"),
    ("LIBRARY OF CONGRESS", "approved_non_fceb:legislative"),
    ("SENATE, THE", "approved_non_fceb:legislative"),
    ("UNITED STATES HOLOCAUST MEMORIAL MUSEUM",
     "approved_non_fceb:independent establishment"),
    ("", "blank"),
])
def test_store_strings_classify_into_their_buckets(store_name, klass):
    from tools.fceb import classify_unmatched

    assert classify_unmatched(store_name) == klass


def test_a_novel_agency_lands_unaccounted_not_admitted():
    """A string in no bucket is a PROMPT FOR A DECISION. Automatically
    admitting it would turn the operator's ruling into a default."""
    from tools.fceb import classify_unmatched

    assert classify_unmatched("BUREAU OF NOVEL AFFAIRS") == "unaccounted"


def test_the_census_surfaces_unaccounted_strings_loudly():
    report = census(
        [{"agency": "BUREAU OF NOVEL AFFAIRS", "subtier": ""}],
        catalog=CATALOG)
    assert report["unaccounted"] == ["BUREAU OF NOVEL AFFAIRS"]
    row = report["unmatched_agencies"][0]
    assert row["class"] == "unaccounted"


def test_the_civilian_scope_admits_every_approved_buyer():
    """Tripwire #2, same shape as the FCEB one: these ten are admitted
    today via fail-open, and a future tightening must fail here with a
    named buyer rather than silently dropping the Library of Congress
    optical-storage RFI class of opportunity."""
    from tools.fceb import NON_FCEB_CIVILIAN
    from tools.relevance.scope import EngagementScope, in_scope

    civilian = EngagementScope(preset="civilian")
    refused = [name for name in NON_FCEB_CIVILIAN
               if not in_scope({"agency": name.upper()}, civilian)[0]]
    assert refused == [], f"civilian scope now refuses approved buyers: {refused}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
