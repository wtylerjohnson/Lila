"""PartnerFit grading + candidate identification: deterministic, evidence-
required, size sourced never assumed, dual-role marked. Offline."""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.pursuit_grade import grade  # noqa: E402
from agents.partnering.blockers import (  # noqa: E402
    PRIME_WITH_SUBS, SUB_TO_PRIME, Blocker,
)
from agents.partnering.candidates import (  # noqa: E402
    EvidenceRow, PartnerCandidate, identify_candidates,
)
from agents.partnering.partner_fit import grade_partner  # noqa: E402
from agents.reports.document import GradedPursuit  # noqa: E402

AS_OF = date(2026, 7, 7)


def _pursuit():
    g = grade({"triage_verdict": "pursue", "response_deadline": "2026-09-01"},
              as_of=AS_OF)
    return GradedPursuit(rank=1, source_id="P1", title="Test Pursuit",
                         agency="DEPT OF DEFENSE.DISA", naics="334310", grade=g)


def _fired(kind, directions):
    return Blocker(kind=kind, fired=True, directions=directions, basis="test")


# ── PartnerFit ───────────────────────────────────────────────────────────────
def test_partner_fit_is_deterministic_and_redistributes_na():
    inputs = {"direction": SUB_TO_PRIME, "award_count": 4,
              "award_dollars": 9_000_000.0, "at_target_agency": True}
    a, b = grade_partner(inputs, as_of=AS_OF), grade_partner(inputs, as_of=AS_OF)
    assert a.model_dump() == b.model_dump()          # same inputs, same grade
    # only award_evidence is live -> it carries all the weight; the rest N/A
    assert a.scored_dimensions == 1
    award = next(d for d in a.dimensions if d.dimension == "award_evidence")
    assert award.effective_weight == 1.0 and award.score == 4.0
    for d in a.dimensions:
        if d.dimension != "award_evidence":
            assert d.score is None and d.na_reason    # worksheet says why


def test_eligibility_complement_requires_sourced_size():
    base = {"direction": SUB_TO_PRIME, "award_count": 2, "award_dollars": 1e6,
            "eligibility_blocker": True}
    unsized = grade_partner({**base, "candidate_size_basis": None}, as_of=AS_OF)
    dim = next(d for d in unsized.dimensions if d.dimension == "eligibility_complement")
    assert dim.score is None and "never assumed" in dim.na_reason

    small = grade_partner({**base, "candidate_size_basis":
                           "recipient_type_names=small_business (...)"}, as_of=AS_OF)
    dim = next(d for d in small.dimensions if d.dimension == "eligibility_complement")
    assert dim.score == 3.0 and "certification not sourced" in dim.basis

    ots = grade_partner({**base, "candidate_size_basis":
                         "recipient_type_names=other_than_small_business (...)"},
                        as_of=AS_OF)
    dim = next(d for d in ots.dimensions if d.dimension == "eligibility_complement")
    assert dim.score == 1.0                           # cannot prime a set-aside

    sub_small = grade_partner({**base, "direction": PRIME_WITH_SUBS,
                               "candidate_size_basis":
                               "recipient_type_names=small_business (...)"},
                              as_of=AS_OF)
    dim = next(d for d in sub_small.dimensions if d.dimension == "eligibility_complement")
    assert dim.score == 4.0


def test_vehicle_match_never_assumed_without_a_holder_source():
    g = grade_partner({"direction": SUB_TO_PRIME, "award_count": 1,
                       "award_dollars": 1.0, "vehicle_blocker": True,
                       "vehicle_holder": None}, as_of=AS_OF)
    dim = next(d for d in g.dimensions if d.dimension == "vehicle_match")
    assert dim.score is None and "never assumed" in dim.na_reason


def test_teaming_history_counts_the_direction_relevant_role():
    g = grade_partner({"direction": SUB_TO_PRIME, "award_count": 1,
                       "award_dollars": 1.0, "sub_edges_as_prime": 6},
                      as_of=AS_OF)
    dim = next(d for d in g.dimensions if d.dimension == "teaming_history")
    assert dim.score == 4.0 and "issued subawards" in dim.basis
    none = grade_partner({"direction": SUB_TO_PRIME, "award_count": 1,
                          "award_dollars": 1.0}, as_of=AS_OF)
    dim = next(d for d in none.dimensions if d.dimension == "teaming_history")
    assert dim.score is None and "not evidence against" in dim.na_reason


# ── candidates ───────────────────────────────────────────────────────────────
def test_candidate_requires_at_least_one_evidence_row():
    with pytest.raises(Exception):
        PartnerCandidate(name="No Proof LLC", evidence=[])
    ok = PartnerCandidate(name="Proof Inc", evidence=[EvidenceRow(
        kind="award", fact_id="W1", dollars=1.0, source="https://x")])
    assert ok.evidence[0].fact_id == "W1"


def _ctx(**over):
    ctx = {
        "client_name": "Testco",
        "competitors": ["AVI-SPL"],
        "incumbents": ["AVI-SPL"],
        "naics_median_award": 900_000.0,
        "awards": [
            {"award_id": "W1", "recipient": "AVI-SPL", "amount": 5_000_000.0,
             "awarding_agency": "Department of Defense", "start_date": "2025-01-01",
             "url": "https://usaspending.gov/award/W1"},
            {"award_id": "W2", "recipient": "Testco", "amount": 100.0,
             "awarding_agency": "Department of Defense", "url": "https://x"},
            {"award_id": "W3", "recipient": "Zeta Integrators", "amount": 400_000.0,
             "awarding_agency": "General Services Administration",
             "start_date": "2024-05-01", "url": "https://usaspending.gov/award/W3"},
        ],
        "edges": [
            {"subaward_id": "SA1", "prime": "AVI-SPL", "sub": "TinyTech",
             "amount": 250_000.0, "prime_award_id": "W1", "date": "2025-03-01",
             "source": "https://api.usaspending.gov"},
        ],
        "demand_rows": [
            {"award_id": "W9", "recipient": "MegaPrime Corp", "amount": 2_000_000.0,
             "awarding_agency": "Department of Defense", "start_date": "2025-02-01",
             "size_basis": "recipient_type_names=other_than_small_business (filter)",
             "source": "https://api.usaspending.gov"},
        ],
        "small_rows": [
            {"award_id": "S1", "recipient": "SmallCo LLC", "amount": 300_000.0,
             "awarding_agency": "Department of Defense", "start_date": "2025-04-01",
             "size_basis": "recipient_type_names=small_business (filter)",
             "source": "https://api.usaspending.gov"},
            {"award_id": "S2", "recipient": "Unsourced Partners", "amount": 1.0,
             "awarding_agency": "Department of Defense",
             "size_basis": None, "source": "https://x"},  # dropped: no basis
        ],
    }
    ctx.update(over)
    return ctx


def test_identify_merges_evidence_ranks_marks_and_excludes_the_client():
    blockers = [_fired("ELIGIBILITY", [SUB_TO_PRIME])]
    cands, gaps = identify_candidates(_pursuit(), blockers, _ctx(), as_of=AS_OF)
    names = [c.name for c in cands]
    assert "Testco" not in names                     # never suggest the client
    avi = next(c for c in cands if c.name == "AVI-SPL")
    # evidence merged across sources: lane award + issued-subaward edge
    assert {e.kind for e in avi.evidence} == {"award", "subaward_edge"}
    assert avi.dual_role and "competitive landscape" in avi.dual_role
    assert names[0] == "AVI-SPL"                     # incumbent+agency outranks
    zeta = next(c for c in cands if c.name == "Zeta Integrators")
    assert zeta.dual_role is None
    assert all(c.evidence for c in cands)            # the invariant, end to end
    # demand-sourced prime present with its size basis carried
    mega = next(c for c in cands if c.name == "MegaPrime Corp")
    assert "other_than_small" in mega.size_basis


def test_small_pool_is_sourced_only_and_gaps_when_never_pulled():
    blockers = [_fired("ELIGIBILITY", [PRIME_WITH_SUBS])]
    cands, gaps = identify_candidates(_pursuit(), blockers, _ctx(), as_of=AS_OF)
    names = [c.name for c in cands]
    assert "SmallCo LLC" in names
    assert "Unsourced Partners" not in names         # no size basis, no pool
    small = next(c for c in cands if c.name == "SmallCo LLC")
    assert "small_business" in small.size_basis
    dim = next(d for d in small.fit.dimensions
               if d.dimension == "eligibility_complement")
    assert dim.score == 4.0
    # sub-history candidates are allowed WITHOUT a size label (never claimed
    # small) and score N/A on eligibility_complement
    tiny = next(c for c in cands if c.name == "TinyTech")
    assert tiny.size_basis is None
    dim = next(d for d in tiny.fit.dimensions
               if d.dimension == "eligibility_complement")
    assert dim.score is None and "never assumed" in dim.na_reason

    # artifacts never pulled (None) -> explicit gap, no small candidates
    cands2, gaps2 = identify_candidates(_pursuit(), blockers,
                                        _ctx(small_rows=None), as_of=AS_OF)
    assert any("small-partner pool N/A" in g for g in gaps2)
    assert "SmallCo LLC" not in [c.name for c in cands2]


def test_vehicle_blocker_gaps_the_holder_source_and_no_directions_no_candidates():
    blockers = [_fired("VEHICLE", [SUB_TO_PRIME])]
    _cands, gaps = identify_candidates(_pursuit(), blockers, _ctx(), as_of=AS_OF)
    assert any("vehicle-holder" in g for g in gaps)
    assert identify_candidates(_pursuit(), [], _ctx(), as_of=AS_OF) == ([], [])


def test_agency_match_reconciles_sam_and_usaspending_spellings():
    """The silently-dead signal this restores: SAM 'DEPT OF DEFENSE / office'
    never matched the USAspending toptier 'Department of Defense'."""
    from agents.partnering.candidates import _agency_matches
    assert _agency_matches("Department of Defense",
                           "DEPT OF DEFENSE / DEFENSE INTELLIGENCE AGENCY (DIA)")
    assert _agency_matches("Department of Homeland Security",
                           "HOMELAND SECURITY, DEPARTMENT OF / US CUSTOMS AND BORDER PROTECTION")
    assert not _agency_matches("General Services Administration",
                               "DEPT OF DEFENSE / DIA")


def test_ranking_puts_target_agency_primes_first():
    """Agency-match is the PRIMARY ranking signal: a prime that wins at the
    notice's agency outranks a bigger, more recent off-agency prime."""
    p = _pursuit()  # DEPT OF DEFENSE.DISA
    blockers = [_fired("ELIGIBILITY", [SUB_TO_PRIME])]
    ctx = _ctx(competitors=[], incumbents=[], edges=[], demand_rows=[], small_rows=None,
               awards=[
                   {"award_id": "G1", "recipient": "OffAgency Corp", "amount": 9e6,
                    "awarding_agency": "General Services Administration",
                    "start_date": "2026-06-01", "url": "https://x"},
                   {"award_id": "G2", "recipient": "OffAgency Corp", "amount": 8e6,
                    "awarding_agency": "General Services Administration",
                    "start_date": "2026-05-01", "url": "https://x"},
                   {"award_id": "D1", "recipient": "OnAgency LLC", "amount": 1e6,
                    "awarding_agency": "Department of Defense",
                    "start_date": "2023-01-01", "url": "https://x"}])
    names = [c.name for c in identify_candidates(p, blockers, ctx, as_of=AS_OF)[0]]
    assert names.index("OnAgency LLC") < names.index("OffAgency Corp")


def test_ranking_recency_breaks_ties_above_volume():
    """Among equal-fit agency-matched primes, the more recently active one
    outranks the deeper-but-older record."""
    p = _pursuit()
    blockers = [_fired("ELIGIBILITY", [SUB_TO_PRIME])]
    ctx = _ctx(competitors=[], incumbents=[], edges=[], demand_rows=[], small_rows=None,
               awards=[
                   {"award_id": "R1", "recipient": "Recent Co", "amount": 1e6,
                    "awarding_agency": "Department of Defense", "start_date": "2026-01-01", "url": "https://x"},
                   {"award_id": "R2", "recipient": "Recent Co", "amount": 1e6,
                    "awarding_agency": "Department of Defense", "start_date": "2026-02-01", "url": "https://x"},
                   {"award_id": "O1", "recipient": "Older Co", "amount": 1e6,
                    "awarding_agency": "Department of Defense", "start_date": "2021-01-01", "url": "https://x"},
                   {"award_id": "O2", "recipient": "Older Co", "amount": 1e6,
                    "awarding_agency": "Department of Defense", "start_date": "2021-02-01", "url": "https://x"}])
    names = [c.name for c in identify_candidates(p, blockers, ctx, as_of=AS_OF)[0]]
    assert names.index("Recent Co") < names.index("Older Co")
