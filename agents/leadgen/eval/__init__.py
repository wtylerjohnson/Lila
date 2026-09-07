"""Skeptic-grade LeadRow / parent scoring. Additive; not a release door."""

from .checks import is_solicitation_only, score_input, score_pack
from .load import ScoreLoadError, load_score_input
from .models import (
    SCHEMA_VERSION,
    CheckResult,
    CheckVerdict,
    EmailStatus,
    EvalOverlay,
    Scorecard,
    ScoreInput,
)
from .render import render_csv, render_markdown

__all__ = [
    "SCHEMA_VERSION",
    "CheckResult",
    "CheckVerdict",
    "EmailStatus",
    "EvalOverlay",
    "ScoreInput",
    "ScoreLoadError",
    "Scorecard",
    "is_solicitation_only",
    "load_score_input",
    "render_csv",
    "render_markdown",
    "score_input",
    "score_pack",
]
