"""Partner candidate identification; evidence first, names second.

Candidates come ONLY from citable rows: lane awards at the target agency,
incumbents and their challengers, primes with subaward history, demand-side
primes (sourced other-than-small), and; for PRIME_WITH_SUBS; small
businesses sourced via the USAspending business-category filter. A company
with no evidence row cannot exist here (model-enforced), and no company is
ever labeled small without a size_basis behind it.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.partnering.blockers import (
    PRIME_WITH_SUBS, SUB_TO_PRIME, Blocker,
)
from agents.partnering.partner_fit import PartnerFitGrade, grade_partner

EvidenceKind = Literal["award", "subaward_edge", "demand_award", "small_award"]


class EvidenceRow(BaseModel):
    kind: EvidenceKind
    fact_id: Optional[str] = Field(default=None, description="award/subaward id; the citable handle")
    dollars: Optional[float] = None
    agency: Optional[str] = None
    date: Optional[str] = None
    detail: str = ""
    size_basis: Optional[str] = Field(default=None, description="citable business-category filter, when the row carries one")
    source: str


class PartnerCandidate(BaseModel):
    name: str
    directions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRow] = Field(min_length=1)  # no evidence, no candidate
    sources: list[str] = Field(default_factory=list, description="evidence kinds present")
    size_basis: Optional[str] = None
    dual_role: Optional[str] = None
    fit: Optional[PartnerFitGrade] = None


def _norm(name: Optional[str]) -> str:
    return " ".join((name or "").upper().replace(".", " ").replace(",", " ").split())


def _canon_agency(agency: Optional[str]) -> str:
    """Canonical toptier name for matching. The pursuit agency is a SAM path
    ('DEPT OF DEFENSE / DIA', 'HOMELAND SECURITY, DEPARTMENT OF / CBP'); the
    award agency is already the USAspending toptier ('Department of Defense').
    normalize_agency_name reconciles the two spellings; without it, 'DEPT OF
    DEFENSE' never matched 'Department of Defense' and the at-target-agency
    signal was silently dead on real data."""
    from tools.api.usaspending import normalize_agency_name
    head = (agency or "").split("/")[0]        # department segment before the office
    return (normalize_agency_name(head) or "").strip().upper()


def _agency_matches(row_agency: Optional[str], pursuit_agency: Optional[str]) -> bool:
    ra, pa = _canon_agency(row_agency), _canon_agency(pursuit_agency)
    return bool(ra) and ra == pa


def identify_candidates(pursuit, blockers: list[Blocker], ctx: dict,
                        as_of=None) -> tuple[list[PartnerCandidate], list[str]]:
    """(ranked candidates, gaps). ctx carries the lane's artifacts:
    awards, incumbents, edges, demand_rows, small_rows (None = never pulled,
    [] = pulled and empty), naics_median_award, client_name, competitors."""
    directions = sorted({d for b in blockers if b.fired for d in b.directions})
    if not directions:
        return [], []

    gaps: list[str] = []
    client_key = _norm(ctx.get("client_name"))
    pool: dict[str, dict] = {}

    def _add(name, direction, row: EvidenceRow):
        key = _norm(name)
        if not key or key == client_key:
            return  # never suggest the client to itself
        c = pool.setdefault(key, {"name": name, "directions": set(),
                                  "evidence": [], "kinds": set(),
                                  "size_basis": None})
        c["directions"].add(direction)
        c["evidence"].append(row)
        c["kinds"].add(row.kind)
        if row.size_basis:
            # small_award sourcing outranks demand sourcing on conflict
            if c["size_basis"] is None or row.kind == "small_award":
                c["size_basis"] = row.size_basis

    incumbents = {_norm(n) for n in ctx.get("incumbents") or []}

    # the SCALE blocker always pairs JV with SUB_TO_PRIME, so the sub-to-prime
    # pool covers every prime-side direction
    if SUB_TO_PRIME in directions:
        for r in ctx.get("awards") or []:
            if not r.get("recipient"):
                continue
            _add(r["recipient"], SUB_TO_PRIME, EvidenceRow(
                kind="award", fact_id=r.get("award_id"), dollars=r.get("amount"),
                agency=r.get("awarding_agency"), date=r.get("start_date"),
                detail="lane award", source=r.get("url") or r.get("source") or ""))
        for e in ctx.get("edges") or []:
            if e.get("error") or not e.get("prime"):
                continue
            _add(e["prime"], SUB_TO_PRIME, EvidenceRow(
                kind="subaward_edge", fact_id=e.get("subaward_id"),
                dollars=e.get("amount"),
                agency=e.get("awarding_agency") or e.get("awarding_sub_agency"),
                date=e.get("date"),
                detail=f"issued subaward to {e.get('sub') or '?'} "
                       f"(prime award {e.get('prime_award_id') or '?'})"
                       + (f": {str(e.get('description'))[:500]}"
                          if e.get("description") else ""),
                source=e.get("source") or ""))
        for r in ctx.get("demand_rows") or []:
            if r.get("error") or not r.get("recipient"):
                continue
            _add(r["recipient"], SUB_TO_PRIME, EvidenceRow(
                kind="demand_award", fact_id=r.get("award_id"),
                dollars=r.get("amount"), agency=r.get("awarding_agency"),
                date=r.get("start_date"),
                detail="above-threshold award (subcontracting plan applies)",
                size_basis=r.get("size_basis"), source=r.get("source") or ""))

    if PRIME_WITH_SUBS in directions:
        small_rows = ctx.get("small_rows")
        if small_rows is None:
            gaps.append(f"pursuit #{pursuit.rank}: small-partner pool N/A; "
                        "small-business-sourced award rows not pulled (run "
                        "run_market_refresh --subawards)")
        else:
            for r in small_rows:
                if r.get("error") or not r.get("recipient") or not r.get("size_basis"):
                    continue  # the sourced-size rule: no basis, no small pool
                _add(r["recipient"], PRIME_WITH_SUBS, EvidenceRow(
                    kind="small_award", fact_id=r.get("award_id"),
                    dollars=r.get("amount"), agency=r.get("awarding_agency"),
                    date=r.get("start_date"), detail="small-business lane award",
                    size_basis=r.get("size_basis"), source=r.get("source") or ""))
        for e in ctx.get("edges") or []:
            if e.get("error") or not e.get("sub"):
                continue
            _add(e["sub"], PRIME_WITH_SUBS, EvidenceRow(
                kind="subaward_edge", fact_id=e.get("subaward_id"),
                dollars=e.get("amount"),
                agency=e.get("awarding_agency") or e.get("awarding_sub_agency"),
                date=e.get("date"),
                detail=f"performed as sub to {e.get('prime') or '?'}"
                       + (f": {str(e.get('description'))[:500]}"
                          if e.get("description") else ""),
                source=e.get("source") or ""))

    if any(b.kind == "VEHICLE" and b.fired for b in blockers):
        gaps.append(f"pursuit #{pursuit.rank}: vehicle-holder candidate source N/A "
                    "; no IDV holder list is wired; candidates come from "
                    "award/subaward evidence instead")

    eligibility_fired = any(b.kind == "ELIGIBILITY" and b.fired for b in blockers)
    vehicle_fired = any(b.kind == "VEHICLE" and b.fired for b in blockers)
    competitors = {_norm(n) for n in ctx.get("competitors") or []}

    out: list[PartnerCandidate] = []
    for key, c in pool.items():
        direction = (SUB_TO_PRIME if SUB_TO_PRIME in c["directions"]
                     else next(iter(c["directions"])))
        dollars_rows = [e.dollars for e in c["evidence"]
                        if isinstance(e.dollars, (int, float))]
        fit = grade_partner({
            "direction": direction,
            "award_count": sum(1 for e in c["evidence"]
                               if e.kind in ("award", "demand_award", "small_award")),
            "award_dollars": sum(e.dollars or 0 for e in c["evidence"]
                                 if e.kind in ("award", "demand_award", "small_award")),
            "at_target_agency": any(_agency_matches(e.agency, pursuit.agency)
                                    for e in c["evidence"]),
            "sub_edges_as_prime": sum(1 for e in c["evidence"]
                                      if e.kind == "subaward_edge"
                                      and e.detail.startswith("issued")),
            "sub_edges_as_sub": sum(1 for e in c["evidence"]
                                    if e.kind == "subaward_edge"
                                    and e.detail.startswith("performed")),
            "vehicle_blocker": vehicle_fired,
            "vehicle_holder": None,  # no citable holder source; stays N/A
            "eligibility_blocker": eligibility_fired,
            "candidate_size_basis": c["size_basis"],
            "is_incumbent": key in incumbents,
            "incumbents_exist": bool(incumbents),
            "naics_median_award": ctx.get("naics_median_award"),
            "candidate_max_award": max(dollars_rows) if dollars_rows else None,
        }, as_of=as_of)
        out.append(PartnerCandidate(
            name=c["name"], directions=sorted(c["directions"]),
            evidence=c["evidence"], sources=sorted(c["kinds"]),
            size_basis=c["size_basis"],
            dual_role=("named in the competitive landscape"
                       if key in competitors else None),
            fit=fit))

    # RANKING (heuristic, transparent lexicographic key, strongest first):
    #   1. wins work AT THE TARGET AGENCY; the sharpest teaming signal for
    #      this specific notice, now weighted as the PRIMARY sort (not just
    #      folded into the grade)
    #   2. graded partner fit
    #   3. RECENCY; most-recent evidence YEAR: currency of the relationship,
    #      lifted above evidence volume (a partner active now beats a dormant
    #      one with a deeper but older record)
    #   4. evidence volume as the final tiebreak
    def _rank_key(c):
        agency_match = any(_agency_matches(e.agency, pursuit.agency)
                           for e in c.evidence)
        recency_year = max((e.date or "" for e in c.evidence), default="")[:4]
        return (agency_match, round(c.fit.score if c.fit else 0.0, 2),
                recency_year, len(c.evidence))
    out.sort(key=_rank_key, reverse=True)
    return out, gaps
