"""Pluggable data-source layer.

Adding a new API (GovWin, FPDS, a state portal, a CRM feed) means writing one
adapter class and decorating it with @register_source — no changes to the Assess,
Target, or Execute engines. Every adapter normalizes its native payload into the
shared RawOpportunity contract, so downstream stages never see vendor-specific shapes.

Two adapter kinds:
  - DISCOVERY  : finds candidate opportunities (SAM.gov)            -> RawOpportunity[]
  - ENRICHMENT : adds context to a known opportunity (USAspending)  -> dict (award history, etc.)
"""

from __future__ import annotations

import abc
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from agents.schemas import RawOpportunity


class SourceKind(str, Enum):
    DISCOVERY = "discovery"
    ENRICHMENT = "enrichment"


class SourceQuery(BaseModel):
    """Normalized query passed to every discovery source.

    Each adapter translates these fields into its own API's parameters and ignores
    what it can't express — so callers write one query, not one per vendor.
    """

    naics_codes: list[str] = Field(default_factory=list)
    psc_codes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    set_asides: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list)
    posted_from: Optional[date] = None
    posted_to: Optional[date] = None
    #: the business rule: opportunities must still be actionable. deadline_from is
    #: normally TODAY — never surface a deal the client can no longer respond to.
    deadline_from: Optional[date] = None
    deadline_to: Optional[date] = None
    limit: int = 100

    def title_contains(self) -> Optional[str]:
        """Keywords joined for APIs that accept a single free-text title filter."""
        return " ".join(self.keywords) if self.keywords else None


class DataSource(abc.ABC):
    """Base class for every external data source."""

    #: unique key, e.g. "sam.gov" — used in registry lookup and provenance strings
    name: str
    kind: SourceKind
    #: flip to False to keep an adapter registered but out of the default fan-out
    enabled: bool = True

    def healthcheck(self) -> tuple[bool, str]:
        """Cheap live probe: (ok, detail). The API-switch lights render this,
        so it must reflect REALITY — reachability or key presence — never just
        configuration. Default says so honestly rather than faking green."""
        return True, "no probe implemented"

    @abc.abstractmethod
    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        """Discovery sources return candidate opportunities.

        Enrichment-only sources may raise NotImplementedError here and expose
        their own methods (e.g. award_history) consumed directly by an engine.
        """
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class SourceRegistry:
    """Holds one instance per registered source, keyed by name."""

    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}

    def add(self, source: DataSource) -> None:
        if source.name in self._sources:
            raise ValueError(f"duplicate source name: {source.name!r}")
        self._sources[source.name] = source

    def get(self, name: str) -> DataSource:
        return self._sources[name]

    def all(self) -> list[DataSource]:
        return list(self._sources.values())

    def enabled(self, kind: SourceKind | None = None) -> list[DataSource]:
        from tools.toggles import is_enabled  # env switch: LILA_ENABLE_<NAME>

        return [
            s
            for s in self._sources.values()
            if s.enabled and is_enabled(s.name, True) and (kind is None or s.kind == kind)
        ]


#: the process-wide registry; adapters register into it at import time
REGISTRY = SourceRegistry()


def cap_disclosed(matched: list, cap: int, *, key: str = "items") -> dict:
    """A lane's output cap, said out loud (audit finding, 2026-07-30).

    Eight enrichment lanes computed every match and then silently kept the
    first N: a broad-keyword client on a busy news week lost matches with
    nothing in the payload saying so, and the capped list read as a census.
    The cap stays - payload bounds are legitimate - but the silence does
    not. Every capped lane now speaks this one dialect: the kept slice
    under ``key``, the true ``total_matched``, and ``truncated: True`` only
    when something was actually cut. Order is preserved, never re-ranked:
    re-sorting would change which items survive the cap, and sweep
    approvals bind to exact sweep contents.
    """
    out: dict = {
        key: matched[:cap],
        "total_matched": len(matched),
        # This helper is applied only after the adapter has screened its
        # declared source boundary.  The retained list is a bounded selection,
        # not an interrupted pull.  Runtime failures remain separate ``errors``
        # or provenance markers and still fail coverage closed.
        "boundary_complete": True,
        "selection_cap": cap,
    }
    if len(matched) > cap:
        out["truncated"] = True
        out["truncated_note"] = (f"kept first {cap} of {len(matched)} "
                                 f"matches in arrival order")
    return out


def register_source(cls: type[DataSource]) -> type[DataSource]:
    """Class decorator: instantiate the adapter and add it to REGISTRY."""
    source = cls()
    from tools.api.source_catalog import SOURCE_BY_ADAPTER
    if source.name not in SOURCE_BY_ADAPTER:
        raise ValueError(
            f"uncataloged source adapter: {source.name!r}; add SourceSpec "
            "identity/result/coverage/consumer metadata before registration"
        )
    REGISTRY.add(source)
    return cls


def discover(query: SourceQuery, registry: SourceRegistry = REGISTRY) -> list[RawOpportunity]:
    """Fan out a query across all enabled discovery sources and merge results.

    Dedup is per (source, source_id) — cross-source records are kept distinct on
    purpose (a SAM notice and a related USAspending award are different artifacts).
    """
    seen: set[tuple[str, str]] = set()
    merged: list[RawOpportunity] = []
    for source in registry.enabled(SourceKind.DISCOVERY):
        for opp in source.search(query):
            key = (opp.source, opp.source_id)
            if key not in seen:
                seen.add(key)
                merged.append(opp)
    return merged
