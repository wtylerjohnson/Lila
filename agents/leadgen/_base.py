"""Shared frozen base and schema version for lead-gen contracts.

Copied shape (not types) from ``agents.assess.contracts._FrozenContract``:
frozen after validation, lists accepted at construct time, JSON arrays on
dump. ``extra=forbid`` matches the Candidate Review boundary so unknown
lead fields cannot silently land on Assess-shaped objects.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


SCHEMA_VERSION = "leadgen.contracts.v1"


class _FrozenContract(BaseModel):
    """Immutable, strict lead-gen boundary object."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
