"""Fit-logic decision layer — the LLM 'why this is a fit for the client' rationale.

Inputs are VERIFIED: a field-checked SAM.gov opportunity + USAspending market
evidence + the client Capability Profile. Claude maps client capabilities to the
specific requirement, names gaps and risks, and cites the source URLs. It explains
fit against the given evidence — it does not invent requirements or scores.

Output is a structured FitRationale that ends at a human Review Gate.
"""

from __future__ import annotations

from typing import Optional

from agents.decisions.engine import DecisionEngine
from agents.decisions.schemas import FitRationale
from agents.schemas import CapabilityProfile, RawOpportunity

LAYER = "fit"

SYSTEM_PROMPT = """\
You assess whether a federal contracting opportunity fits a specific client, for a
LILA. You are given VERIFIED data only: a SAM.gov
opportunity (already field-checked), USAspending market evidence (real historical
awards for this NAICS/agency), and the client's Capability Profile.

Your job: generate the logic for WHY this is (or isn't) a fit for THIS client.

Rules you MUST follow:
- GROUND EVERY CLAIM in the provided data. Map each client capability to the exact
  requirement text or field in the opportunity. Do not invent requirements the
  opportunity does not state, and do not assume capabilities the profile lacks.
- USE THE MARKET EVIDENCE: reference the USAspending incumbents and typical award
  size to judge competitiveness and realism (market_evidence_note).
- CITE SOURCES: populate citations with the opportunity's source URL and the
  USAspending award URLs you relied on (traceability).
- BE HONEST ABOUT GAPS AND RISKS: list capability gaps vs. the requirement and any
  risks (incumbent strength, dollar mismatch, closing deadline, set-aside mismatch).
- HUMAN-IN-THE-LOOP: this is a recommendation. Set requires_human_review=true.
- Pick a verdict (strong_fit / partial_fit / weak_fit / no_fit) consistent with the
  evidence and your confidence.
"""


def _opportunity_context(opp: RawOpportunity) -> dict:
    return {
        "opportunity_id": opp.source_id,
        "title": opp.title,
        "agency": opp.agency,
        "naics_code": opp.naics_code,
        "psc_code": opp.psc_code,
        "set_aside": opp.set_aside,
        "posted_date": opp.posted_date,
        "response_deadline": opp.response_deadline,
        "estimated_value": opp.estimated_value,
        "source_url": str(opp.api_url) if opp.api_url else None,
        # Full payload gives Claude the description/requirement text to map against.
        "raw_payload": opp.raw_payload,
    }


def _profile_context(p: CapabilityProfile) -> dict:
    return {
        "client_name": p.client_name,
        "naics_codes": p.naics_codes,
        "psc_codes": p.psc_codes,
        "set_aside_eligibility": p.set_aside_eligibility,
        "tech_stack": p.tech_stack,
        "past_performance_keywords": p.past_performance_keywords,
        "target_agencies": p.target_agencies,
        "min_contract_value": p.min_contract_value,
        "max_contract_value": p.max_contract_value,
    }


def assess_fit(
    opp: RawOpportunity,
    profile: CapabilityProfile,
    market_evidence: dict,
    engine: Optional[DecisionEngine] = None,
) -> FitRationale:
    """Generate the LLM fit rationale for one verified opportunity."""
    engine = engine or DecisionEngine()
    context = {
        "opportunity": _opportunity_context(opp),
        "client_profile": _profile_context(profile),
        "market_evidence_usaspending": market_evidence,
    }
    return engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context=context,
        schema=FitRationale,
    )
