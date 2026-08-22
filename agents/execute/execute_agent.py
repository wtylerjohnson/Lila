"""EXECUTE engine (stub).

Generates human-review-ready output templates (Capability Statements,
personalized email hooks) for ranked targets. Every artifact cites the source
document that triggered the match and stops at a Review Gate before sending.
"""

from __future__ import annotations

from agents.schemas import AssessmentResult


def generate_capability_statement(target: AssessmentResult) -> str:
    """Draft a tailored capability statement (Markdown) for human review."""
    raise NotImplementedError("Execute engine — built after Target lands.")


def generate_email_hook(target: AssessmentResult) -> str:
    """Draft a personalized outreach hook for human review."""
    raise NotImplementedError("Execute engine — built after Target lands.")
