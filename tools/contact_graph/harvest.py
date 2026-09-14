"""Harvest paths — turn notices LILA already processed into observations.

Two entry points share one rule: harvesting is an append-only *side effect*. It
writes observations and nothing else (no index rebuild, no mutation), so it can
never change the outcome of the sweep it rides on. Gated by the existing toggle
pattern — LILA_ENABLE_CONTACT_GRAPH, default on.

  harvest_opportunities  — a list of notices/opportunities from one source.
  harvest_from_results   — a whole sweep's `results` dict; pulls the POC-bearing
                           sources out of it. This is the inline hook.

A notice with no usable POC on disk is a *coverage gap*, counted and skipped —
never an error. Live re-enrichment is out of scope (backfill makes zero API
calls; inline only sees what the sweep already fetched).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from tools.contact_graph.extract import observations_from_notice
from tools.contact_graph.store import ContactGraphStore
from tools.toggles import is_enabled

TOGGLE = "contact_graph"

# Notice-shaped sources. Scoped forecasts use their separate source-aware
# extractor below; they must never inherit SAM URLs or notice semantics.
POC_SOURCES = ("sam.gov",)


def contact_graph_enabled() -> bool:
    return is_enabled(TOGGLE, default=True)


@dataclass
class HarvestSummary:
    enabled: bool = True
    notices: int = 0
    with_pocs: int = 0
    coverage_gaps: int = 0  # processed but no usable POC on disk
    observations_written: int = 0
    observations_skipped: int = 0  # dedupe: already stored (idempotent re-run)

    def merge(self, other: "HarvestSummary") -> "HarvestSummary":
        self.notices += other.notices
        self.with_pocs += other.with_pocs
        self.coverage_gaps += other.coverage_gaps
        self.observations_written += other.observations_written
        self.observations_skipped += other.observations_skipped
        return self

    def __str__(self) -> str:
        if not self.enabled:
            return "contact graph disabled (LILA_ENABLE_CONTACT_GRAPH=off)"
        return (
            f"{self.observations_written} observations from {self.with_pocs}/{self.notices} "
            f"notices ({self.coverage_gaps} coverage gaps, "
            f"{self.observations_skipped} already stored)"
        )


def harvest_opportunities(
    opportunities: Iterable[Any],
    *,
    source: str = "sam.gov",
    store: Optional[ContactGraphStore] = None,
    harvested_at: Optional[datetime] = None,
    force: bool = False,
) -> HarvestSummary:
    """Extract + append observations for a list of notices from one source.

    `force=True` skips the toggle check (the caller already checked it).
    """
    summary = HarvestSummary()
    if not force and not contact_graph_enabled():
        summary.enabled = False
        return summary

    store = store or ContactGraphStore()
    harvested_at = harvested_at or datetime.now()

    batch = []
    for opp in opportunities or []:
        summary.notices += 1
        obs = observations_from_notice(opp, source=source, harvested_at=harvested_at)
        if obs:
            summary.with_pocs += 1
            batch.extend(obs)
        else:
            summary.coverage_gaps += 1

    result = store.append(batch)
    summary.observations_written = result.written
    summary.observations_skipped = result.skipped
    return summary


def harvest_from_results(
    results: dict,
    *,
    store: Optional[ContactGraphStore] = None,
    harvested_at: Optional[datetime] = None,
    force: bool = False,
) -> HarvestSummary:
    """Inline hook: harvest every POC-bearing source list in a sweep's results."""
    summary = HarvestSummary()
    if not force and not contact_graph_enabled():
        summary.enabled = False
        return summary

    store = store or ContactGraphStore()
    harvested_at = harvested_at or datetime.now()

    for src in POC_SOURCES:
        lst = (results or {}).get(src)
        if isinstance(lst, list) and lst:
            summary.merge(
                harvest_opportunities(
                    lst, source=src, store=store, harvested_at=harvested_at, force=True
                )
            )
    # Harvest only the scoped matched/research population, never all-market rows.
    from tools.contact_graph.forecasts import observations_from_forecast
    seen_forecasts = set()
    for key in ("forecasts", "forecast_signals"):
        family = (results or {}).get(key) or {}
        if not isinstance(family, dict):
            continue
        records = list(family.get("matched") or [])
        records += [c["record"] for c in family.get("research_candidates") or []
                    if isinstance(c, dict) and isinstance(c.get("record"), dict)]
        for record in records:
            if not isinstance(record, dict):
                continue
            fingerprint = json.dumps(record, sort_keys=True, default=str)
            if fingerprint in seen_forecasts:
                continue
            seen_forecasts.add(fingerprint)
            obs = observations_from_forecast(record, harvested_at=harvested_at)
            summary.notices += 1
            if obs:
                summary.with_pocs += 1
                result = store.append(obs)
                summary.observations_written += result.written
                summary.observations_skipped += result.skipped
            else:
                summary.coverage_gaps += 1
    return summary
