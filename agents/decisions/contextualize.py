"""Contextualization layer — the first Claude decision layer.

Bridges the deterministic Assess engine to human strategy. It takes the ranked
AssessmentResult[] (objective capability scores) and asks Claude to:
  1. think through the strategy across the portfolio of matches,
  2. surface explicit bid / no-bid / teaming DECISION POINTS, and
  3. draft a pursuit-brief ARTIFACT per top target, ready for human review.

The deterministic scores constrain the model — it explains and prioritizes them,
it does not re-score. Output is a DecisionReport that stops at a Review Gate.
"""

from __future__ import annotations

from typing import Optional

from agents.decisions.engine import DecisionEngine
from agents.decisions.schemas import DecisionReport
from agents.schemas import AssessmentResult

LAYER = "contextualize"

SYSTEM_PROMPT = """\
You are the strategy layer of a LILA built on the
Assess -> Target -> Execute framework. A deterministic engine has already scored
each opportunity against the client's Capability Profile. Your job is to turn those
scores into actionable sales strategy.

Operating principles you MUST follow:
- CONTEXTUAL TRUTH over API literalism: reason from the fused, document-backed
  evidence you are given; never assert facts not present in the context.
- TRACEABILITY: every decision point's reasoning must cite the specific source
  URLs/documents from the evidence/traceability fields provided.
- HUMAN-IN-THE-LOOP: you produce recommendations and DRAFT artifacts only. Set
  requires_human_review=true and write a precise review_gate describing what a
  human must approve before anything proceeds. Artifacts start with status="draft".
- DO NOT re-score opportunities. The match_score is authoritative; explain and
  prioritize using it, and flag anything marked requires_human_review.

Deliverables:
- strategic_summary: a concise portfolio-level read — where to focus and why.
- decision_points: the real forks (bid/no-bid, teaming vs. prime, which to drop),
  each with options, a recommendation, evidence-cited reasoning.
- artifacts: one pursuit_brief per recommended target (kind="pursuit_brief"),
  carrying its traceability.
"""


def _result_to_context(r: AssessmentResult) -> dict:
    return {
        "opportunity_id": r.opportunity_id,
        "match_score": r.match_score,
        "why_this_match": r.why_this_match,
        "signals": [
            {"dimension": s.dimension, "score": s.score, "evidence": s.evidence}
            for s in r.signals
        ],
        "traceability": r.traceability,
        "requires_human_review": r.requires_human_review,
    }


def contextualize(
    assessments: list[AssessmentResult],
    client_name: str,
    engine: Optional[DecisionEngine] = None,
) -> DecisionReport:
    """Run the contextualization decision layer over ranked assessments."""
    engine = engine or DecisionEngine()
    context = {
        "client_name": client_name,
        "framework": "Assess -> Target -> Execute",
        "assessed_opportunities": [_result_to_context(r) for r in assessments],
    }
    return engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context=context,
        schema=DecisionReport,
    )
