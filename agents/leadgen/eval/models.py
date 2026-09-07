"""Eval.v0 scorecard types. Not leadgen.contracts.v1.

Overlays carry skeptic facts the frozen LeadRow / parent forbid
(extra=forbid). They do not mutate Assess or mint leads.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from agents.leadgen._base import _FrozenContract
from agents.leadgen.contracts import LeadRow, OpportunityAssessment
from agents.leadgen.traces import DecisionTrace

SCHEMA_VERSION = "leadgen.eval.v0"


class CheckVerdict(str, Enum):
    """Per-check result. NA does not fail a family."""

    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"


class EmailStatus(str, Enum):
    """Contact-email verification. UNVERIFIED cannot satisfy C3."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    ABSENT = "ABSENT"


class EvalOverlay(_FrozenContract):
    """Skeptic facts keyed by lead_id or assessment_id."""

    schema_version: Literal["leadgen.eval.v0"] = SCHEMA_VERSION
    subject_id: str = Field(min_length=1)
    email_status: EmailStatus = EmailStatus.ABSENT
    email: str | None = None
    auth_only: bool = False
    solicitation_only: bool | None = None
    receipt: str | None = None
    product_fit_cites: tuple[str, ...] = Field(default_factory=tuple)
    demand_cites: tuple[str, ...] = Field(default_factory=tuple)
    need_cites: tuple[str, ...] = Field(default_factory=tuple)
    funding_cites: tuple[str, ...] = Field(default_factory=tuple)
    contestability_cites: tuple[str, ...] = Field(default_factory=tuple)
    blocker_cites: tuple[str, ...] = Field(default_factory=tuple)
    outreach_doors_open: bool = False


class CheckResult(_FrozenContract):
    """One PASS/FAIL/NA row on the scorecard."""

    schema_version: Literal["leadgen.eval.v0"] = SCHEMA_VERSION
    check_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    title: str = Field(min_length=1)
    verdict: CheckVerdict
    receipt: str = Field(min_length=1)
    scope: Literal["lead", "parent", "pack"]
    subject_id: str = Field(min_length=1)
    lead_tier: str | None = None


class ScoreInput(_FrozenContract):
    """Normalized press / draft / eval pack plus overlays."""

    schema_version: Literal["leadgen.eval.v0"] = SCHEMA_VERSION
    parents: tuple[OpportunityAssessment, ...] = Field(default_factory=tuple)
    leads: tuple[LeadRow, ...] = Field(default_factory=tuple)
    traces: tuple[DecisionTrace, ...] = Field(default_factory=tuple)
    overlays: tuple[EvalOverlay, ...] = Field(default_factory=tuple)
    quota_keys: tuple[str, ...] = Field(default_factory=tuple)
    source_label: str = "pack"


class Scorecard(_FrozenContract):
    """Deterministic scorecard. REJECT rows are never omitted."""

    schema_version: Literal["leadgen.eval.v0"] = SCHEMA_VERSION
    source_label: str
    checks: tuple[CheckResult, ...]
    lead_ids: tuple[str, ...]
    reject_lead_ids: tuple[str, ...]
    invented_row_count: int = 0

    @property
    def failed_check_ids(self) -> tuple[str, ...]:
        return tuple(
            item.check_id for item in self.checks
            if item.verdict is CheckVerdict.FAIL
        )
