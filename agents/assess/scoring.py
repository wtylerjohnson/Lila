"""Phase 3 — Scoring (pure, no I/O).

Turns a FusedOpportunity into an AssessmentResult: one MatchSignal per dimension,
each carrying its own evidence string, aggregated into a single match_score.

"Why This Match" is assembled from the signals' evidence strings — never free-form
prose without cited evidence behind it. This keeps every recommendation traceable.

Fuzzy term matching uses rapidfuzz when available and degrades to deterministic
substring/token matching otherwise, so this module has no hard runtime dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.schemas import (
    AssessmentResult,
    CapabilityProfile,
    FusedOpportunity,
    MatchSignal,
)

# Single tunable knob for the whole engine. Adjust here; one reviewable change.
SCORING_WEIGHTS: dict[str, float] = {
    "naics": 0.25,
    "set_aside": 0.15,
    "tech": 0.20,
    "past_perf": 0.20,
    "value_fit": 0.10,
    "agency_fit": 0.10,
}

# Score below this for a dimension is treated as "not evidence worth citing".
_EVIDENCE_FLOOR = 0.5
_FUZZY_THRESHOLD = 85  # rapidfuzz partial_ratio cutoff

try:  # optional acceleration; deterministic fallback below
    from rapidfuzz import fuzz as _fuzz

    def _matches(term: str, text: str) -> bool:
        t = term.lower().strip()
        if not t:
            return False
        if t in text:
            return True
        return _fuzz.partial_ratio(t, text) >= _FUZZY_THRESHOLD

except ImportError:  # pragma: no cover - exercised when rapidfuzz absent

    def _matches(term: str, text: str) -> bool:
        t = term.lower().strip()
        if not t:
            return False
        if t in text:
            return True
        # token-overlap fallback: all words of the term appear in the text
        words = [w for w in t.replace("-", " ").split() if w]
        return bool(words) and all(w in text for w in words)


def _coverage(terms: list[str], text: str) -> tuple[float, list[str]]:
    """Fraction of `terms` present in `text`, plus the list of hits."""
    if not terms:
        return 0.0, []
    hits = [t for t in terms if _matches(t, text)]
    return len(hits) / len(terms), hits


def _document_text(opp: FusedOpportunity) -> str:
    """All searchable text for this opportunity, lowercased.

    Pulls from the document full text + semantic tags (Contextual Truth) AND the
    fused summary / title (so scoring still works pre-document for testing).
    """
    parts = [opp.fused_summary or "", opp.raw.title or ""]
    if opp.document:
        parts.append(opp.document.full_text or "")
        parts.extend(opp.document.semantic_tags or [])
    return "\n".join(parts).lower()


def _naics_signal(opp: FusedOpportunity, p: CapabilityProfile) -> MatchSignal:
    code = opp.raw.naics_code
    if code and code in p.naics_codes:
        return MatchSignal(dimension="naics", score=1.0, weight=SCORING_WEIGHTS["naics"],
                           evidence=f"Exact NAICS match on {code}.")
    if code and any(c[:4] == code[:4] for c in p.naics_codes):
        return MatchSignal(dimension="naics", score=0.5, weight=SCORING_WEIGHTS["naics"],
                           evidence=f"Adjacent NAICS family {code[:4]}xx (opp {code}).")
    return MatchSignal(dimension="naics", score=0.0, weight=SCORING_WEIGHTS["naics"],
                       evidence=f"No NAICS overlap (opp {code or 'n/a'}).")


def _set_aside_signal(opp: FusedOpportunity, p: CapabilityProfile) -> MatchSignal:
    sa = (opp.raw.set_aside or "").strip()
    w = SCORING_WEIGHTS["set_aside"]
    if not sa:
        return MatchSignal(dimension="set_aside", score=0.5, weight=w,
                           evidence="Full-and-open (no set-aside restriction).")
    from agents.assess.prefilter import set_aside_matches

    if set_aside_matches(sa, p.set_aside_eligibility):
        return MatchSignal(dimension="set_aside", score=1.0, weight=w,
                           evidence=f"Reserved for {sa}; client is eligible.")
    return MatchSignal(dimension="set_aside", score=0.0, weight=w,
                       evidence=f"Reserved for {sa}; client NOT eligible.")


def _tech_signal(opp: FusedOpportunity, p: CapabilityProfile, text: str) -> MatchSignal:
    cov, hits = _coverage(p.tech_stack, text)
    ev = (f"Tech coverage {cov:.0%}: matched {', '.join(hits)}." if hits
          else "No client tech-stack terms found in source text.")
    return MatchSignal(dimension="tech", score=cov, weight=SCORING_WEIGHTS["tech"], evidence=ev)


def _past_perf_signal(opp: FusedOpportunity, p: CapabilityProfile, text: str) -> MatchSignal:
    cov, hits = _coverage(p.past_performance_keywords, text)
    ev = (f"Past-performance coverage {cov:.0%}: matched {', '.join(hits)}." if hits
          else "No past-performance keywords found in source text.")
    return MatchSignal(dimension="past_perf", score=cov,
                       weight=SCORING_WEIGHTS["past_perf"], evidence=ev)


def _value_signal(opp: FusedOpportunity, p: CapabilityProfile) -> MatchSignal:
    v = opp.raw.estimated_value
    w = SCORING_WEIGHTS["value_fit"]
    if v is None:
        return MatchSignal(dimension="value_fit", score=0.5, weight=w,
                           evidence="Estimated value unknown.")
    lo, hi = p.min_contract_value, p.max_contract_value
    if (lo is None or v >= lo) and (hi is None or v <= hi):
        return MatchSignal(dimension="value_fit", score=1.0, weight=w,
                           evidence=f"Est. value ${v:,.0f} within target band.")
    return MatchSignal(dimension="value_fit", score=0.0, weight=w,
                       evidence=f"Est. value ${v:,.0f} outside target band.")


def _agency_signal(opp: FusedOpportunity, p: CapabilityProfile) -> MatchSignal:
    agency = (opp.raw.agency or "").lower()
    w = SCORING_WEIGHTS["agency_fit"]
    if not p.target_agencies:
        return MatchSignal(dimension="agency_fit", score=0.5, weight=w,
                           evidence="No target agencies specified.")
    if agency and any(_matches(t, agency) for t in p.target_agencies):
        return MatchSignal(dimension="agency_fit", score=1.0, weight=w,
                           evidence=f"Issuing agency '{opp.raw.agency}' is a target.")
    return MatchSignal(dimension="agency_fit", score=0.0, weight=w,
                       evidence=f"Issuing agency '{opp.raw.agency or 'n/a'}' not targeted.")


def score_signals(opp: FusedOpportunity, profile: CapabilityProfile) -> list[MatchSignal]:
    text = _document_text(opp)
    return [
        _naics_signal(opp, profile),
        _set_aside_signal(opp, profile),
        _tech_signal(opp, profile, text),
        _past_perf_signal(opp, profile, text),
        _value_signal(opp, profile),
        _agency_signal(opp, profile),
    ]


def aggregate(signals: list[MatchSignal]) -> float:
    """Weighted mean of signal scores (weights need not sum to 1)."""
    total_w = sum(s.weight for s in signals)
    if total_w == 0:
        return 0.0
    return sum(s.score * s.weight for s in signals) / total_w


def build_why(signals: list[MatchSignal]) -> str:
    """Assemble 'Why This Match' from the strongest evidence-bearing signals."""
    ranked = sorted(signals, key=lambda s: s.score * s.weight, reverse=True)
    cited = [s.evidence for s in ranked if s.score >= _EVIDENCE_FLOOR]
    if not cited:
        return "Weak match: no dimension cleared the evidence threshold."
    return " ".join(cited)


def assess(
    opp: FusedOpportunity,
    profile: CapabilityProfile,
    now: datetime | None = None,
) -> AssessmentResult:
    """Score one fused opportunity against the client profile."""
    signals = score_signals(opp, profile)
    return AssessmentResult(
        opportunity_id=opp.raw.source_id,
        client_name=profile.client_name,
        match_score=round(aggregate(signals), 4),
        signals=signals,
        why_this_match=build_why(signals),
        traceability=list(opp.provenance),
        requires_human_review=opp.requires_human_review,
        assessed_at=now or datetime.now(timezone.utc),
    )
