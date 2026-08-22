"""Cross-client leakage: one client's vocabulary must never search for
another client's market.

THE MEASURED FAILURE. The store-lane fallback carried a hard-coded
payment-integrity phrase list plus a shared synonyms tier. Four materially
different clients then pressed with IDENTICAL qualified sets: 24 of one
company's shaping records and 11 of its government contacts, in every
other company's map. Certification could not see it, because every
document was internally consistent; only the cross-client comparison shows
it. These tests are that comparison, made permanent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.golden_press.market_map_projection import _exact_terms

_ROOT = Path(__file__).resolve().parents[1]

# The phrases that leaked. If any of them comes back for a profile that
# does not carry them, the fallback has grown a side channel again.
_LEAKED = ("fraud prevention services", "claims review services",
           "vendor vetting", "unclaimed property", "recovery audit",
           "duplicate payment", "financial improvement",
           "audit remediation", "supplier onboarding",
           "bank account validation", "vendor management office")


def test_terms_come_from_the_profile_alone():
    profile = {"discovered_terms_approved": ["network telemetry"],
               "capability_terms": {"core": ["packet capture"]}}
    terms = _exact_terms(profile)
    assert terms == ["network telemetry", "packet capture"]


def test_no_foreign_vocabulary_for_an_unrelated_client():
    terms = _exact_terms({"discovered_terms_approved": ["kvm switching"]})
    for phrase in _LEAKED:
        assert phrase not in terms, (
            f"{phrase!r} reached a client that never approved it")


def test_an_empty_profile_searches_nothing():
    """Thin vocabulary yields a thin, honest lane, never a borrowed one."""
    assert _exact_terms({}) == []


def test_two_clients_share_no_searched_vocabulary():
    a = _exact_terms({"discovered_terms_approved": ["wan optimization"],
                      "capability_terms": {"core": ["network visibility"]}})
    b = _exact_terms({"discovered_terms_approved": ["payment integrity"],
                      "capability_terms": {"core": ["supplier risk"]}})
    assert not set(a) & set(b)


def test_pressed_clients_do_not_share_identifier_sets():
    """The disk-level check: two different clients' qualified sets must not
    be identical. Runs only when both coverage receipts exist."""
    sets = {}
    for slug in ("riverbed", "thinklogical", "varonis", "mark43"):
        path = (_ROOT / "data" / "state" / "candidate_review_v1" / slug
                / f"{slug}.market_map.coverage.json")
        if not path.exists():
            continue
        cov = json.loads(path.read_text(encoding="utf-8"))
        sets[slug] = (cov["query_plan"]["terms_searched"],
                      cov["query_plan"]["candidate_rows_inspected"])
    if len(sets) < 2:
        pytest.skip("fewer than two pressed clients on this checkout")
    # Identical (terms, rows) across DIFFERENT clients is the leakage
    # signature that went unnoticed: it is only plausible when both are
    # zero (no store lane at all).
    values = list(sets.values())
    nonzero = [v for v in values if v != (0, 0)]
    assert len(set(nonzero)) == len(nonzero), (
        f"clients share identical store-lane query plans: {sets}")
