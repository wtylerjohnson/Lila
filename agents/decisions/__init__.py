"""Claude-powered decision layers.

The DecisionEngine is the single seam to the Anthropic API. Layers (contextualize,
and future target-strategy / execute-drafting) call it with a system prompt, a
context dict, and a structured-output schema, and receive a validated DecisionReport
that ends at a human Review Gate.
"""

from __future__ import annotations

from agents.decisions.engine import DecisionEngine  # noqa: F401
from agents.decisions.schemas import (  # noqa: F401
    Artifact,
    ArtifactKind,
    DecisionOption,
    DecisionPoint,
    DecisionReport,
    FitRationale,
    FitVerdict,
    MatchedCapability,
)

__all__ = [
    "DecisionEngine",
    "DecisionReport",
    "DecisionPoint",
    "DecisionOption",
    "Artifact",
    "ArtifactKind",
    "FitRationale",
    "FitVerdict",
    "MatchedCapability",
]
