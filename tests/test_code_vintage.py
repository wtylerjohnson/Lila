"""Do the screen's NAICS and PSC prefixes still match live federal usage?

WHAT THIS CAN AND CANNOT PROVE. It checks the prefix lists against codes
OBSERVED in a real sam.gov extract (data/reference/observed_codes.json). That
is evidence of what contracting officers actually write. It is NOT the census
taxonomy: census.gov refuses automated fetch (HTTP 403 on 2026-07-28), so no
vintage mapping here is sourced from the standard. A dead prefix means "no
federal buyer used it in a 78,553-notice day", not "the code was retired".

Why it exists: TECH_NAICS_PREFIXES sat on the 2017 vintage and silently
under-screened telecom for every report ever pressed, because it lacked 5178
while 517810 was live. Nothing failed. Nothing warned. This is the warning.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.retrieval import (  # noqa: E402
    TECH_NAICS_PREFIXES, TECH_PSC_PREFIXES,
)

_REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "reference", "observed_codes.json")


@pytest.fixture(scope="module")
def observed():
    with open(_REF, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_telecom_gap_that_started_this_stays_closed(observed):
    """517810 "All Other Telecommunications" is live federal usage and was
    invisible to the screen. If this fails, the regression is back."""
    assert "517810".startswith(TECH_NAICS_PREFIXES)
    assert observed["naics_counts"].get("517810", 0) > 0


@pytest.mark.parametrize("code", ["517810", "513210", "541519", "541512",
                                  "518210", "334111", "517111"])
def test_high_volume_tech_codes_are_covered(code, observed):
    assert observed["naics_counts"].get(code, 0) > 0, "fixture drift"
    assert code.startswith(TECH_NAICS_PREFIXES), (
        f"{code} is live federal tech usage and the screen does not see it")


@pytest.mark.parametrize("code", ["524114", "335314", "512110", "513130"])
def test_non_tech_codes_stay_out(code):
    """Insurance, relays, film production, book publishing. The boundary is
    only meaningful if it excludes."""
    assert not code.startswith(TECH_NAICS_PREFIXES)


def test_every_naics_prefix_still_matches_something_live(observed):
    """A prefix matching nothing across a full federal day is a candidate for
    retirement. This REPORTS rather than fails, because absence from one day
    is not proof a code is dead, and a wrong deletion under-screens silently."""
    dead = [p for p in TECH_NAICS_PREFIXES
            if not any(c.startswith(p) for c in observed["naics_counts"])]
    assert dead == ["4431", "5172"], (
        f"the set of prefixes unused in live federal traffic changed: {dead}. "
        f"Confirm against census.gov before adding or removing anything; this "
        f"repo has no authoritative vintage source.")


def test_every_psc_prefix_still_matches_something_live(observed):
    dead = [p for p in TECH_PSC_PREFIXES
            if not any(c.startswith(p) for c in observed["psc_counts"])]
    assert dead == [], f"PSC prefixes unused in live federal traffic: {dead}"


def test_a_bare_sector_code_is_a_known_blind_spot(observed):
    """11 notices carry a bare "517" with no industry group. No 4-digit prefix
    can match a 3-character value, so they are invisible to the screen. Left
    alone deliberately: widening to "517" would admit every telecom subsector
    including ones the boundary excludes. Recorded so it is not rediscovered."""
    assert observed["naics_counts"].get("517", 0) > 0
    assert not "517".startswith(TECH_NAICS_PREFIXES)
