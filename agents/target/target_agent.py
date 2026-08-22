"""TARGET engine (stub).

Consumes AssessmentResult records, ranks by win probability (capability match +
competitive landscape from USAspending award history), and emits a prioritized
target list with a "Why This Match" justification per recommendation.
"""

from __future__ import annotations

from agents.schemas import AssessmentResult


def rank_targets(assessments: list[AssessmentResult]) -> list[AssessmentResult]:
    """Re-rank assessed opportunities by win probability."""
    raise NotImplementedError("Target engine — built after Assess lands.")
