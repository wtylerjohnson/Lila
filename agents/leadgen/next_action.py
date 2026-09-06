"""CurrentNextAction: the single next operator action."""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import Field

from ._base import SCHEMA_VERSION, _FrozenContract
from .enums import CommunicationPermission, NextActionVerb


class CurrentNextAction(_FrozenContract):
    """One dated (when a clock exists) next action. Owner is the operator."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    verb: NextActionVerb
    object: str = Field(min_length=1)
    due: Optional[date] = None
    blocked_by: Optional[str] = None
    owner: str = "operator"
    communication_permission: CommunicationPermission = (
        CommunicationPermission.NONE)
