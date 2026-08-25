"""Blocker detection: per-blocker fixtures including the inverse-eligibility
case, degradation to N/A with gaps (never guesses), and the
no-blocker-no-analysis rule. Pure functions, offline."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.pursuit_grade import grade  # noqa: E402
from agents.partnering.blockers import (  # noqa: E402
    PRIME_WITH_SUBS, SUB_TO_PRIME, detect_blockers, has_partnering_play,
    parse_set_aside,
)
from agents.partnering.profile import ATTESTABLE_FIELDS, PartneringProfile  # noqa: E402
from agents.reports.document import GradedPursuit  # noqa: E402

AS_OF = date(2026, 7, 7)


def _pursuit(set_aside=None, set_aside_code=None, dossier=None, recompete=None,
             incumbents=None, naics="334310"):
    g = grade({"triage_verdict": "pursue", "response_deadline": "2026-09-01",
               "recompete": recompete, "naics_incumbents": incumbents or [],
               "vehicle": (dossier or {}).get("vehicle")}, as_of=AS_OF)
    return GradedPursuit(rank=1, source_id="P1", title="Test Pursuit",
                         agency="DEPT OF DEFENSE", naics=naics,
                         set_aside=set_aside, set_aside_code=set_aside_code,
                         dossier=dossier, grade=g)


_FULL = PartneringProfile(
    size_status_by_naics={"334310": "small"},
    certifications=["WOSB"],
    vehicles_held=["SEWP VI (NASA)"],
    award_band={"floor_usd": 50_000, "ceiling_usd": 1_500_000})


def _by_kind(blockers, kind):
    return next((b for b in blockers if b.kind == kind), None)


# ── set-aside recognition ────────────────────────────────────────────────────
def test_parse_set_aside_recognition_table():
    assert parse_set_aside(None, None) is None
    assert parse_set_aside("No Set aside used", "") is None
    assert parse_set_aside("No Set aside used", "NONE") is None
    assert parse_set_aside("", "NONE") is None
    total = parse_set_aside("Total Small Business Set-Aside (FAR 19.5)", "")
    assert total == {"requires_small": True, "required_cert": None, "recognized": True}
    sam_variant = parse_set_aside("Small Business Set Aside - Total", "")
    assert sam_variant == {
        "requires_small": True, "required_cert": None, "recognized": True}
    # EDWOSB matches before WOSB — ordering is load-bearing
    ed = parse_set_aside("SBA Certified Economically Disadvantaged WOSB (EDWOSB) "
                         "Program Set-Aside (FAR 19.15)", "")
    assert ed["required_cert"] == "EDWOSB"
    assert parse_set_aside("Women-Owned Small Business", "")["required_cert"] == "WOSB"
    # code-only recognition (label missing)
    assert parse_set_aside("", "SDVOSBC")["required_cert"] == "SDVOSB"
    # An inconsistent NONE code cannot erase an affirmative restricted label.
    assert parse_set_aside(
        "Total Small Business Set-Aside (FAR 19.5)", "NONE"
    )["recognized"] is True
    # a set-aside we do not recognize is surfaced, never guessed
    isbee = parse_set_aside("Indian Small Business Economic Enterprise (ISBEE) "
                            "Set-Aside", "")
    assert isbee["recognized"] is False


# ── VEHICLE ──────────────────────────────────────────────────────────────────
def test_vehicle_blocker_fires_when_client_lacks_the_paper():
    p = _pursuit(dossier={"vehicle": "SEWP VI"})
    held_other = _FULL.model_copy(update={"vehicles_held": ["8(a) STARS III (GSA)"]})
    b = _by_kind(detect_blockers(p, held_other, []), "VEHICLE")
    assert b.fired and b.directions == [SUB_TO_PRIME]
    assert "SEWP VI" in b.basis

    # holding the vehicle -> no blocker (containment either direction)
    assert _by_kind(detect_blockers(p, _FULL, []), "VEHICLE") is None


def test_vehicle_blocker_degrades_never_guesses():
    # no dossier at all: requirement unknown -> N/A + gap
    b = _by_kind(detect_blockers(_pursuit(), _FULL, []), "VEHICLE")
    assert b.na and not b.fired and "dossier" in b.gap
    # dossier affirms full-and-open -> silence (no requirement, not a gap)
    p = _pursuit(dossier={"vehicle": None})
    assert _by_kind(detect_blockers(p, _FULL, []), "VEHICLE") is None
    # vehicles unattested -> N/A + gap naming the profile field
    p = _pursuit(dossier={"vehicle": "SEWP VI"})
    b = _by_kind(detect_blockers(p, _FULL, ["vehicles_held"]), "VEHICLE")
    assert b.na and "vehicles_held" in b.gap


# ── ELIGIBILITY (and the inverse) ───────────────────────────────────────────
def test_eligibility_excluded_by_size():
    p = _pursuit(set_aside="Total Small Business Set-Aside (FAR 19.5)")
    other = _FULL.model_copy(update={"size_status_by_naics": {"334310": "other_than_small"}})
    b = _by_kind(detect_blockers(p, other, []), "ELIGIBILITY")
    assert b.fired and not b.inverse and b.directions == [SUB_TO_PRIME]
    assert any("52.219-14" in f for f in b.review_flags)


def test_eligibility_inverse_client_qualifies_primes_with_subs():
    p = _pursuit(set_aside="Women-Owned Small Business")
    b = _by_kind(detect_blockers(p, _FULL, []), "ELIGIBILITY")
    assert b.fired and b.inverse and b.directions == [PRIME_WITH_SUBS]
    assert "WOSB" in b.basis
    assert any("52.219-14" in f for f in b.review_flags)


def test_eligibility_cert_attested_absent_excludes_whatever_the_size():
    p = _pursuit(set_aside="Service-Disabled Veteran-Owned Small Business Set Aside")
    b = _by_kind(detect_blockers(p, _FULL, ["size_status_by_naics"]), "ELIGIBILITY")
    assert b.fired and b.directions == [SUB_TO_PRIME]      # SDVOSB not held
    assert "SDVOSB" in b.basis


def test_eligibility_degrades_to_na_with_gaps():
    p = _pursuit(set_aside="Total Small Business Set-Aside (FAR 19.5)")
    b = _by_kind(detect_blockers(p, PartneringProfile(), list(ATTESTABLE_FIELDS)),
                 "ELIGIBILITY")
    assert b.na and "size_status_by_naics" in b.gap
    # cert held but size unattested: N/A plus the unlock hint
    p2 = _pursuit(set_aside="Women-Owned Small Business")
    prof = PartneringProfile(certifications=["WOSB"])
    b2 = _by_kind(detect_blockers(p2, prof, ["size_status_by_naics",
                                             "vehicles_held", "award_band"]),
                  "ELIGIBILITY")
    assert b2.na and any("attest size" in f.lower() for f in b2.review_flags)
    # unrecognized set-aside: N/A + human-review flag, never guessed
    p3 = _pursuit(set_aside="Indian Small Business Economic Enterprise (ISBEE) Set-Aside")
    b3 = _by_kind(detect_blockers(p3, _FULL, []), "ELIGIBILITY")
    assert b3.na and any("unrecognized" in f for f in b3.review_flags)
    # unrestricted: no eligibility entry at all
    p4 = _pursuit(set_aside="No Set aside used")
    assert _by_kind(detect_blockers(p4, _FULL, []), "ELIGIBILITY") is None


# ── SCALE ────────────────────────────────────────────────────────────────────
def test_scale_blocker_uses_named_multiple_and_states_it():
    p = _pursuit()
    fired = _by_kind(detect_blockers(p, _FULL, [], naics_median_award=10_000_000),
                     "SCALE")
    assert fired.fired and SUB_TO_PRIME in fired.directions
    assert "SCALE_MULTIPLE 3" in fired.basis          # reviewer sees the knob
    assert "MARKET PROXY" in fired.basis              # proxy labeling holds
    assert any("human decision" in f for f in fired.review_flags)  # JV flag
    # within reach -> silence (1.5M ceiling x3 = 4.5M threshold)
    assert _by_kind(detect_blockers(p, _FULL, [], naics_median_award=4_000_000),
                    "SCALE") is None


def test_scale_degrades_without_band_or_median():
    p = _pursuit()
    b = _by_kind(detect_blockers(p, PartneringProfile(), ["award_band"],
                                 naics_median_award=10_000_000), "SCALE")
    assert b.na and "award_band" in b.gap
    b2 = _by_kind(detect_blockers(p, _FULL, [], naics_median_award=None), "SCALE")
    assert b2.na and "median" in b2.gap.lower()


# ── INCUMBENCY ───────────────────────────────────────────────────────────────
def test_incumbency_fires_only_at_high_tier():
    named = _pursuit(recompete={"awardee": "AVI-SPL", "completion": "2026-12-01"})
    b = _by_kind(detect_blockers(named, _FULL, []), "INCUMBENCY")
    assert b.fired and b.directions == [SUB_TO_PRIME] and "AVI-SPL" in b.basis
    # market-level signal (score 2-3) is not the high tier
    market = _pursuit(incumbents=["A", "B", "C", "D"])
    assert _by_kind(detect_blockers(market, _FULL, []), "INCUMBENCY") is None
    # dimension N/A -> silent here (grading already gaps it)
    assert _by_kind(detect_blockers(_pursuit(), _FULL, []), "INCUMBENCY") is None


# ── the governing rule ───────────────────────────────────────────────────────
def test_no_blocker_means_no_partnering_analysis():
    """Full attestation, clean pursuit: nothing fires, nothing N/A, and
    has_partnering_play is False — teaming is never suggested where the
    client can simply prime."""
    p = _pursuit(set_aside="No Set aside used", dossier={"vehicle": None})
    blockers = detect_blockers(p, _FULL, [], naics_median_award=1_000_000)
    assert blockers == []
    assert has_partnering_play(blockers) is False
    # N/A-only entries never count as a play either
    p2 = _pursuit(set_aside="Total Small Business Set-Aside (FAR 19.5)")
    na_only = detect_blockers(p2, PartneringProfile(), list(ATTESTABLE_FIELDS))
    assert all(b.na for b in na_only) and na_only
    assert has_partnering_play(na_only) is False


def test_adjacency_blocker_fires_only_for_monitor_verdict():
    """A monitor-grade notice always gets the ADJACENCY teaming direction
    (sub to a prime); pursue-verdict pursuits never do."""
    p = _pursuit()
    p.verdict = "monitor"
    b = _by_kind(detect_blockers(p, _FULL, []), "ADJACENCY")
    assert b is not None and b.fired and b.directions == [SUB_TO_PRIME]
    assert "tracking this, not priming" in b.basis

    p2 = _pursuit()
    p2.verdict = "pursue"
    assert _by_kind(detect_blockers(p2, _FULL, []), "ADJACENCY") is None
