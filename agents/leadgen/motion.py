"""BuyingMotion: one buying event on one buyer / clock.

Do not edit ``agents/golden_press/motions.py``. That module batches
outreach title sets. This object is an opportunity-grain factor of a
LeadRow.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from agents.assess.contracts import LifecycleStage, ProjectedWindow

from ._base import SCHEMA_VERSION, _FrozenContract, require_aware
from .enums import CommercialMotionKind, TargetingRuleId


class BuyingMotion(_FrozenContract):
    """What buying event is in motion. Not a targeting spec and not a lead."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    motion_id: str = Field(min_length=1)
    parent_subject_id: str = Field(min_length=1)
    kind: CommercialMotionKind
    stage: LifecycleStage
    buyer_agency: str = Field(min_length=1)
    buyer_component: str | None = None
    buyer_office: str | None = None
    clock: date | None = None
    window: ProjectedWindow | None = None
    targeting_rule_id: TargetingRuleId | None = None
    recorded_at: datetime | None = None

    @model_validator(mode="after")
    def _buyer_and_clock_are_required(self) -> BuyingMotion:
        if self.clock is None and self.window is None:
            raise ValueError(
                "buying motion requires a clock or projected window")
        if self.recorded_at is not None:
            require_aware(self.recorded_at, "buying motion recorded_at")
        return self
