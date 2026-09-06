"""DecisionTrace: minimal why-this-parent-and-child record.

Outcome is a later market fact and is not modeled here. A missing
outcome is not lead-tier REJECT.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from agents.assess.contracts import GateStatus

from ._base import SCHEMA_VERSION, _FrozenContract, require_aware, require_unique


class DecisionTrace(_FrozenContract):
    """Minimal append-oriented trace a LeadRow can cite. No I/O."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    trace_id: str = Field(min_length=1)
    assess_run_id: str = Field(min_length=1)
    as_of: datetime
    parent_assessment_id: Optional[str] = None
    lead_id: Optional[str] = None
    assess_gate: Optional[GateStatus] = None
    steps: tuple[str, ...] = Field(default_factory=tuple)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    notes: str = ""

    @model_validator(mode="after")
    def _trace_clock_and_ids(self) -> "DecisionTrace":
        require_aware(self.as_of, "decision trace as_of")
        require_unique(self.evidence_ids, "decision trace evidence_ids")
        return self
