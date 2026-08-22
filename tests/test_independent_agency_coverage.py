"""Every independent federal agency has a named home in our accounting.

Operator verification request 2026-07-30: the Wikipedia roster of
independent agencies of the US federal government, checked not against one
client's scope but against ALL-FEDERAL sweeps. Three layers, each pinned:

  1. SCOPE. The all_federal preset is unbounded with no exclusions, so it
     admits every entity on the roster (and any future one) by construction.
     Pinned per-name AND as the structural property, so a future edit to the
     preset that adds an exclusion fails here with a named agency.
  2. ACCOUNTING. 37 of the 40 current entities are CISA-FCEB and live in
     the FCEB catalog; USPS is an operator-approved non-FCEB civilian buyer;
     CIA and FEC are on neither list ON PURPOSE (CISA's IC carve-out and
     regulatory exclusions). Those two must stay UNCLAIMED by any catalog so
     a first appearance in the store lands in the census's "unaccounted"
     bucket, which is the loud prompt for an operator decision - never a
     silent admission, never a silent drop.
  3. HISTORY. Defunct agencies (ICC, AEC, the wartime boards) must not
     resolve onto their modern successors: "covering" a ghost by crediting
     its heir would double-count the heir and report coverage of an agency
     that cannot post.

Store presence itself is measured by tools/fceb.census (21 of the 37 FCEB
independents held notices on 2026-07-30's two-day store; a measurement, not
a test, because it moves with every ingest).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fceb import (  # noqa: E402
    NON_FCEB_CIVILIAN, build_index, classify_unmatched, load_catalog,
    match_agency,
)
from tools.relevance.scope import EngagementScope, in_scope  # noqa: E402

CATALOG = load_catalog()
INDEX = build_index(CATALOG)
BY_ACRONYM = {e["acronym"]: e for e in CATALOG}

# The Wikipedia roster's 37 currently-operating entities that CISA also
# lists as FCEB, by our catalog acronym. FRB stands in for the Federal
# Reserve System: the Board of Governors is the federal procurement surface;
# the regional banks are quasi-private and do not post to sam.gov.
WIKI_FCEB = [
    "CSB", "CFTC", "CFPB", "CPSC", "EAC", "EPA", "EXIM", "FCC", "FDIC",
    "FERC", "FHFA", "FMC", "FMCS", "FRB", "FRTIB", "FTC", "GSA", "USITC",
    "NASA", "NARA", "NCUA", "NLRB", "NSF", "NTSB", "NRC", "OSC", "PC",
    "PRC", "SEC", "SSS", "SBA", "SSA", "STB", "TVA", "USAID", "OPM",
    "USTDA",
]

# On the roster, deliberately on NO catalog of ours: CISA excludes the CIA
# (Intelligence Community carve-out) and the FEC. Zero store presence
# measured 2026-07-30; the CIA buys through classified channels and In-Q-Tel
# rather than public SAM notices. Their coverage story is the unaccounted
# tripwire, pinned below.
DELIBERATELY_UNCLAIMED = [
    "Central Intelligence Agency",
    "Federal Election Commission",
]

# The roster's "Former agencies": they cannot post, so they must not be
# claimed - especially not by their modern successors.
DEFUNCT = [
    "Committee on Public Information",
    "Interstate Commerce Commission",
    "United States Maritime Commission",
    "Reconstruction Finance Corporation",
    "Atomic Energy Commission",
    "Office of the United States Nuclear Waste Negotiator",
    "United States Information Agency",
]


# --------------------------------------------------------------------------- #
# 1. scope: all-federal admits the entire roster
# --------------------------------------------------------------------------- #
def test_all_federal_is_unbounded_with_no_exclusions():
    """The structural fact everything below rests on."""
    scope = EngagementScope(preset="all_federal")
    assert scope.unbounded
    assert not scope.excluded_departments


def test_all_federal_admits_every_current_independent_agency():
    scope = EngagementScope(preset="all_federal")
    names = ([BY_ACRONYM[a]["name"] for a in WIKI_FCEB]
             + DELIBERATELY_UNCLAIMED + ["United States Postal Service"])
    refused = [n for n in names if not in_scope({"agency": n}, scope)[0]]
    assert refused == [], f"all_federal refuses independents: {refused}"


# --------------------------------------------------------------------------- #
# 2. accounting: every current entity has exactly one named home
# --------------------------------------------------------------------------- #
def test_the_37_fceb_independents_are_all_in_the_catalog():
    missing = [a for a in WIKI_FCEB if a not in BY_ACRONYM]
    assert missing == [], f"FCEB catalog lost independents: {missing}"


def test_usps_is_an_approved_non_fceb_buyer():
    assert classify_unmatched("POSTAL SERVICE") == \
        "approved_non_fceb:independent establishment"


@pytest.mark.parametrize("name", DELIBERATELY_UNCLAIMED)
def test_cia_and_fec_stay_unclaimed_so_their_first_notice_is_loud(name):
    """The coverage story for the two non-CISA entities: NO catalog claims
    them, so the day either one posts a sam.gov notice the census files it
    as "unaccounted" - a prompt for an operator decision. Quietly adding
    them to a catalog would replace that alarm with silence."""
    assert match_agency(name, INDEX) is None
    assert match_agency(name.upper(), INDEX) is None
    assert classify_unmatched(name.upper()) == "unaccounted"
    assert name not in NON_FCEB_CIVILIAN


# --------------------------------------------------------------------------- #
# 3. history: ghosts are not covered by their heirs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", DEFUNCT)
def test_a_defunct_agency_never_resolves_onto_a_successor(name):
    """ICC must not credit the Surface Transportation Board, the AEC must
    not credit the NRC, and the 1936 Maritime Commission must not credit
    the Federal Maritime Commission."""
    assert match_agency(name, INDEX) is None
    assert match_agency(name.upper(), INDEX) is None


def test_the_successors_themselves_still_resolve():
    """The other half of the ghost rule: refusing the ICC must not have
    cost us the STB."""
    assert match_agency("SURFACE TRANSPORTATION BOARD", INDEX) == "STB"
    assert match_agency("NUCLEAR REGULATORY COMMISSION", INDEX) == "NRC"
    assert match_agency("FEDERAL MARITIME COMMISSION", INDEX) == "FMC"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
