"""Registered official-source factory for deterministic Horizon signals."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    REGISTRY,
    OfficialHorizonAdapter,
    ProgramBankEntry,
    register_horizon_source,
)

# Registration imports.  Order is the stable factory order.
from agents.reports.horizon_discovery import budget_lines  # noqa: E402,F401
from agents.reports.horizon_discovery import cisa_kev  # noqa: E402,F401
from agents.reports.horizon_discovery import federal_register  # noqa: E402,F401
from agents.reports.horizon_discovery import forecast_store_changes  # noqa: E402,F401
from agents.reports.horizon_discovery import regulations_gov  # noqa: E402,F401
from agents.reports.horizon_discovery import watchdogs  # noqa: E402,F401
# Stored PROGRAM payloads append behind the established adapters, preserving
# their fact-bank identities when an already-collected source gains projection.
from agents.reports.horizon_discovery import dsip_topics  # noqa: E402,F401
from agents.reports.horizon_discovery import reginfo_unified_agenda  # noqa: E402,F401
from agents.reports.horizon_discovery import darpa_opportunities  # noqa: E402,F401
from agents.reports.horizon_discovery import dod_budget_exhibits  # noqa: E402,F401
from agents.reports.horizon_discovery import foreign_assistance  # noqa: E402,F401
from agents.reports.horizon_discovery import grants_programs  # noqa: E402,F401
from agents.reports.horizon_discovery import sbir_topics  # noqa: E402,F401


def pull_official_horizon_sources(
    results: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Run every offline adapter once against the supplied sweep payload."""
    return {adapter.name: adapter.pull(results) for adapter in REGISTRY.all()}


__all__ = [
    "OfficialHorizonAdapter",
    "ProgramBankEntry",
    "REGISTRY",
    "pull_official_horizon_sources",
    "register_horizon_source",
]
