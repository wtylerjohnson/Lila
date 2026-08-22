"""ASSESS engine — orchestration entry point.

Wires the four phases: pre-filter (raw) -> Hybrid Fusion -> score -> rank + gate.
Phases 1 and 3 are implemented (prefilter.py, scoring.py). Phase 2 (fusion) is
injected as a callable so this module stays pure and testable; in production pass
`tools.browser.enrichment_gate.fuse`. See docs/assess_agent_plan.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from agents.assess import scoring
from agents.assess.prefilter import prefilter
from agents.schemas import (
    AssessmentResult,
    CapabilityProfile,
    FusedOpportunity,
    RawOpportunity,
)

# A fusion function: RawOpportunity -> FusedOpportunity (or None if unretrievable).
FuseFn = Callable[[RawOpportunity], Optional[FusedOpportunity]]


def assess_opportunity(
    opp: FusedOpportunity,
    profile: CapabilityProfile,
    now: datetime | None = None,
) -> AssessmentResult:
    """Score a single fused opportunity (Phase 3)."""
    return scoring.assess(opp, profile, now=now)


def rank(assessments: list[AssessmentResult]) -> list[AssessmentResult]:
    """Sort by match score, highest first (Phase 4)."""
    return sorted(assessments, key=lambda a: a.match_score, reverse=True)


def assess_fused_batch(
    opps: list[FusedOpportunity],
    profile: CapabilityProfile,
    threshold: float = 0.6,
    now: datetime | None = None,
) -> list[AssessmentResult]:
    """Score + rank already-fused opportunities; keep high-match targets.

    Discrepancy-flagged opps are retained even if above threshold IS handled by
    the caller's Review Gate; here we simply never drop a flagged record silently.
    """
    assessed = [assess_opportunity(o, profile, now=now) for o in opps]
    keep = [a for a in assessed if a.match_score >= threshold or a.requires_human_review]
    return rank(keep)


def run(
    raw_opps: list[RawOpportunity],
    profile: CapabilityProfile,
    fuse: FuseFn,
    threshold: float = 0.6,
    now: datetime | None = None,
) -> list[AssessmentResult]:
    """Full Assess pipeline: prefilter -> fuse -> score -> rank + gate.

    `fuse` is injected (Phase 2 lives in tools/browser). Candidates the gate cannot
    retrieve are dropped from the automated path.
    """
    candidates = prefilter(raw_opps, profile)
    fused = [f for f in (fuse(c) for c in candidates) if f is not None]
    return assess_fused_batch(fused, profile, threshold=threshold, now=now)
