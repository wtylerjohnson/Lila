"""Phase 1 — Pre-filter (pure, no I/O).

Discards opportunities that cannot possibly match the client BEFORE the expensive
Contextual Enrichment Gate runs. Operates only on free API metadata.

Design rule: be INCLUSIVE. A false negative here is a silently lost deal; a false
positive merely costs one enrichment pass. When a field is missing/unknown, pass through.
"""

from __future__ import annotations

from agents.schemas import CapabilityProfile, RawOpportunity


def _naics_family(code: str) -> str:
    """First 4 digits identify the NAICS industry group ('family')."""
    return code[:4]


def naics_overlaps(opp: RawOpportunity, profile: CapabilityProfile) -> bool:
    """True if the opp's NAICS/PSC intersects the profile (exact or same family)."""
    if not opp.naics_code and not opp.psc_code:
        return True  # unknown -> don't filter out
    if opp.psc_code and opp.psc_code in profile.psc_codes:
        return True
    if not opp.naics_code:
        return True
    if opp.naics_code in profile.naics_codes:
        return True
    fam = _naics_family(opp.naics_code)
    return any(_naics_family(c) == fam for c in profile.naics_codes)


def value_in_band(opp: RawOpportunity, profile: CapabilityProfile) -> bool:
    """True if estimated value is within the client's band (unknown -> pass)."""
    if opp.estimated_value is None:
        return True
    lo = profile.min_contract_value
    hi = profile.max_contract_value
    if lo is not None and opp.estimated_value < lo:
        return False
    if hi is not None and opp.estimated_value > hi:
        return False
    return True


def set_aside_matches(set_aside: str, eligibility: list[str]) -> bool:
    """Shared matcher: normalized labels, and sole-source variants match the base
    program ('SDVOSB Sole Source' matches eligibility 'SDVOSB')."""
    sa = set_aside.strip().upper()
    for e in eligibility:
        el = e.strip().upper()
        if not el:
            continue
        if sa == el or sa.startswith(el + " ") or el.startswith(sa + " "):
            return True
    return False


def set_aside_eligible(opp: RawOpportunity, profile: CapabilityProfile) -> bool:
    """True unless the opp is restricted to a set-aside the client can't claim.

    Full-and-open (no set-aside) always passes.
    """
    if not opp.set_aside:
        return True
    return set_aside_matches(opp.set_aside, profile.set_aside_eligibility)


def passes_prefilter(opp: RawOpportunity, profile: CapabilityProfile) -> bool:
    """A candidate survives only if it clears every gate."""
    return (
        naics_overlaps(opp, profile)
        and value_in_band(opp, profile)
        and set_aside_eligible(opp, profile)
    )


def prefilter(
    opps: list[RawOpportunity], profile: CapabilityProfile
) -> list[RawOpportunity]:
    """Return the subset of opportunities worth sending to the enrichment gate."""
    return [o for o in opps if passes_prefilter(o, profile)]
