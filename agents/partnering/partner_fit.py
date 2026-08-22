"""PartnerFit; reproducible partner grading in the pursuit_grade pattern.

Pure function: same inputs, same grade. Six dimensions; anything the data
cannot support scores N/A, redistributes its weight to the live dimensions,
and says so in the worksheet. Letter bands and the worksheet model are
imported from pursuit_grade so a partner grade reads exactly like a pursuit
grade.

The sourced-size rule (approved 2026-07-07) is enforced here for
eligibility_complement: without a citable size basis on the candidate
(USAspending recipient business-category filter), the dimension is N/A ;
no candidate is ever treated as small or certified on assumption.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from agents.assess.pursuit_grade import _LETTER_BANDS, DimensionScore, PursuitGrade
from agents.partnering.blockers import PRIME_WITH_SUBS, SUB_TO_PRIME

WEIGHTS = {"award_evidence": 0.30, "teaming_history": 0.20,
           "vehicle_match": 0.15, "eligibility_complement": 0.15,
           "recompete_position": 0.10, "scale_complement": 0.10}


class PartnerFitGrade(PursuitGrade):
    """Same shape and letter semantics as a pursuit grade; reviewers read
    one worksheet convention everywhere."""


def _award_evidence(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    n = inputs.get("award_count") or 0
    dollars = inputs.get("award_dollars") or 0.0
    at_agency = bool(inputs.get("at_target_agency"))
    if not n:
        return None, "no award rows in this lane on record", \
            "no citable award evidence; candidate should not reach grading without it"
    if at_agency and n >= 3:
        score = 4.0
    elif at_agency:
        score = 3.0
    elif n >= 3:
        score = 2.0
    else:
        score = 1.0
    # phrase avoids the count-noun 'agency/agencies' next to a number; lint_counts
    # would read 'N ... agency' as a bogus agency-count claim (it is award rows)
    where = "at the target buyer" if at_agency else "in the lane, at other buyers"
    return score, (f"{n} award row(s) ${dollars:,.0f} {where} "
                   f"(USAspending) -> {score:g}/4"), None


def _teaming_history(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    direction = inputs.get("direction")
    as_prime = inputs.get("sub_edges_as_prime") or 0
    as_sub = inputs.get("sub_edges_as_sub") or 0
    relevant = as_prime if direction == SUB_TO_PRIME else as_sub
    role = "issued subawards" if direction == SUB_TO_PRIME else "performed as a sub"
    if not (as_prime or as_sub):
        return None, "no subaward history on record", \
            "absence of subaward rows is not evidence against teaming; N/A"
    if relevant >= 5:
        score = 4.0
    elif relevant >= 1:
        score = 3.0
    else:
        score = 2.0  # history exists, but in the other role
    return score, (f"{relevant} subaward edge(s) where the candidate {role} "
                   f"in this lane (USAspending subawards) -> {score:g}/4"), None


def _vehicle_match(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    if not inputs.get("vehicle_blocker"):
        return None, "no vehicle blocker on this pursuit", \
            "dimension applies only when access runs through a vehicle"
    holder = inputs.get("vehicle_holder")  # True/False/None; sourced only
    if holder is None:
        return None, "vehicle-holder data not wired (no IDV holder source)", \
            "no citable holder list; never assumed"
    if holder:
        return 4.0, "candidate holds the required vehicle (sourced) -> 4/4", None
    return 1.0, "candidate does not hold the required vehicle -> 1/4", None


def _eligibility_complement(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    if not inputs.get("eligibility_blocker"):
        return None, "no eligibility blocker on this pursuit", \
            "dimension applies only on set-aside pursuits"
    basis = inputs.get("candidate_size_basis")  # citable filter string or None
    if not basis:
        return None, "candidate size unsourced", \
            "no citable size basis (USAspending business-category filter); never assumed"
    small = "small_business" in basis and "other_than" not in basis
    direction = inputs.get("direction")
    if direction == SUB_TO_PRIME:
        # candidate would PRIME the set-aside: must be small; certification
        # (where required) is not sourced -> capped below top marks
        if small:
            return 3.0, (f"candidate sourced small ({basis}); certification "
                         "not sourced, capped -> 3/4"), None
        return 1.0, (f"candidate sourced other-than-small ({basis}); cannot "
                     "prime a small-business set-aside -> 1/4"), None
    if direction == PRIME_WITH_SUBS:
        # client primes; the sub's size shapes the workshare conversation
        if small:
            return 4.0, (f"candidate sourced small ({basis}); clean sub under "
                         "a set-aside prime -> 4/4"), None
        return 2.0, (f"candidate sourced other-than-small ({basis}); "
                     "subcontracting limitations apply (flagged, not "
                     "computed) -> 2/4"), None
    return None, "direction unknown", "no teaming direction supplied"


def _recompete_position(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    if inputs.get("is_incumbent"):
        return 4.0, "the incumbent on this work; the incumbent-sub play -> 4/4", None
    if inputs.get("incumbents_exist") and inputs.get("award_count"):
        return 3.0, ("active challenger: lane awards alongside named "
                     "incumbents -> 3/4"), None
    return None, "no incumbency context for this pursuit", \
        "recompete position needs incumbent evidence"


def _scale_complement(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    median = inputs.get("naics_median_award")
    top = inputs.get("candidate_max_award")
    if not isinstance(median, (int, float)) or median <= 0 \
            or not isinstance(top, (int, float)) or top <= 0:
        return None, "scale complement needs the lane median and candidate award sizes", \
            "missing market proxy or candidate dollars"
    direction = inputs.get("direction")
    if direction == SUB_TO_PRIME:
        # the candidate must carry prime-scale work
        if top >= median:
            return 4.0, (f"candidate's largest award ${top:,.0f} >= lane median "
                         f"${median:,.0f} (MARKET PROXY) -> 4/4"), None
        if top >= median / 3:
            return 3.0, (f"candidate's largest award ${top:,.0f} within a third "
                         f"of the lane median ${median:,.0f} -> 3/4"), None
        return 2.0, (f"candidate's largest award ${top:,.0f} well under the "
                     f"lane median ${median:,.0f} -> 2/4"), None
    # PRIME_WITH_SUBS: a sub does not need prime scale; evidence of real
    # delivery is enough
    return 3.0, (f"sub role: candidate delivers at ${top:,.0f} scale; prime "
                 "scale not required -> 3/4"), None


_SCORERS = {
    "award_evidence": _award_evidence,
    "teaming_history": _teaming_history,
    "vehicle_match": _vehicle_match,
    "eligibility_complement": _eligibility_complement,
    "recompete_position": _recompete_position,
    "scale_complement": _scale_complement,
}


def grade_partner(inputs: dict[str, Any],
                  as_of: Optional[date] = None) -> PartnerFitGrade:
    """Same inputs, same grade; the pursuit_grade contract, for partners."""
    as_of = as_of or date.today()
    raw = {name: fn(inputs) for name, fn in _SCORERS.items()}
    available = {k for k, (s, _b, _r) in raw.items() if s is not None}
    live_weight = sum(WEIGHTS[k] for k in available) or 1.0
    dims, total = [], 0.0
    for name in _SCORERS:
        score, basis, na = raw[name]
        eff = (WEIGHTS[name] / live_weight) if name in available else 0.0
        if score is not None:
            total += score * eff
        dims.append(DimensionScore(dimension=name, score=score,
                                   weight=WEIGHTS[name],
                                   effective_weight=round(eff, 4),
                                   basis=basis, na_reason=na))
    letter = next((mark for floor, mark in _LETTER_BANDS if total >= floor), "F")
    return PartnerFitGrade(letter=letter, score=round(total, 3), dimensions=dims,
                           scored_dimensions=len(available), as_of=as_of)
