"""Offline official-source adapters for the Developing Horizon fact bank.

The live API work already happened in the client sweep.  Adapters in this
package consume only the supplied sweep payload and normalize citable program
signals.  They deliberately do not implement ``DataSource`` or ``search``;
their output cannot enter the live-opportunity fan-out.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urlparse

from tools.api.provenance import provenance_from_payload

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgramBankEntry:
    """One official PROGRAM-tier row ready for Horizon H-id assignment."""

    text: str
    source: str
    kind: str
    agency: Optional[str] = None
    component: Optional[str] = None
    agencies: tuple[str, ...] = ()
    source_record_id: Optional[str] = None
    retrieved_at: Optional[str] = None
    data_as_of: Optional[str] = None

    def bank_row(self) -> dict[str, Any]:
        buyers = []
        for value in (*self.agencies, self.agency):
            clean = str(value or "").strip()
            if clean and clean not in buyers:
                buyers.append(clean)
        scope: dict[str, Any]
        if buyers:
            scope = {"kind": "agency", "agencies": buyers}
            if self.component:
                scope["component"] = self.component.strip()
        else:
            scope = {"kind": "government_wide"}
        row = {
            "text": self.text.strip(),
            "source": self.source.strip(),
            "kind": self.kind,
            "tier": "program",
            "scope": scope,
        }
        if self.source_record_id:
            row["source_record_id"] = self.source_record_id
        if self.retrieved_at:
            row["retrieved_at"] = self.retrieved_at
        if self.data_as_of:
            row["data_as_of"] = self.data_as_of
        return row


def program_entry(
    text: Any,
    source: Any,
    kind: str,
    *,
    agency: Any = None,
    component: Any = None,
    agencies: Any = None,
    source_record_id: Any = None,
    retrieved_at: Any = None,
    data_as_of: Any = None,
) -> Optional[ProgramBankEntry]:
    """Build one row only when its citation is an official HTTPS source."""
    clean_text = str(text or "").strip()
    clean_source = str(source or "").strip()
    if not clean_text or not clean_source:
        return None
    try:
        parsed = urlparse(clean_source)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or not (
        host.endswith(".gov") or host.endswith(".mil")
    ):
        return None
    buyer_values = tuple(
        str(value).strip()
        for value in (agencies if isinstance(agencies, (list, tuple)) else ())
        if str(value or "").strip()
    )
    return ProgramBankEntry(
        text=clean_text,
        source=clean_source,
        kind=kind,
        agency=str(agency).strip() if agency else None,
        component=str(component).strip() if component else None,
        agencies=buyer_values,
        source_record_id=(
            str(source_record_id).strip() if source_record_id else None),
        retrieved_at=str(retrieved_at).strip() if retrieved_at else None,
        data_as_of=str(data_as_of).strip() if data_as_of else None,
    )


class OfficialHorizonAdapter(ABC):
    """Registered offline mapper for one supplied official-source payload."""

    name: str
    result_key: str
    coverage_agency: str = "government-wide"

    def pull(self, results: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Purely map one stored payload; live collection owns pull coverage."""
        if self.result_key not in results:
            return []
        payload = results.get(self.result_key)
        if not isinstance(payload, dict):
            return []
        problem = payload.get("error") or (
            "source disabled" if payload.get("disabled") else ""
        )
        if problem:
            return []
        try:
            entries = [entry for entry in self.map_payload(payload) if entry]
        except Exception as exc:  # noqa: BLE001 - one source never sinks Horizon
            _LOG.warning(
                "Horizon mapper %s rejected its stored payload: %s",
                self.name,
                exc,
            )
            return []
        envelope = provenance_from_payload(payload, source=self.name)
        default_retrieved = (
            envelope.retrieved_at.isoformat()
            if envelope is not None and envelope.retrieved_at else None
        )
        default_as_of = envelope.data_as_of if envelope is not None else None
        rows = []
        for entry in entries:
            row = entry.bank_row()
            if default_retrieved and not row.get("retrieved_at"):
                row["retrieved_at"] = default_retrieved
            if default_as_of and not row.get("data_as_of"):
                row["data_as_of"] = default_as_of
            rows.append(row)
        return _dedupe(rows)

    @abstractmethod
    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        """Pure payload mapper.  It performs no network or artifact reads."""
        raise NotImplementedError


def _dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row["kind"], row["source"], row["text"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


class HorizonDiscoveryRegistry:
    """Small signals-only registry, intentionally separate from tools.api."""

    def __init__(self) -> None:
        self._adapters: dict[str, OfficialHorizonAdapter] = {}

    def add(self, adapter: OfficialHorizonAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"duplicate Horizon discovery source: {adapter.name!r}")
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> OfficialHorizonAdapter:
        return self._adapters[name]

    def all(self) -> tuple[OfficialHorizonAdapter, ...]:
        return tuple(self._adapters.values())


REGISTRY = HorizonDiscoveryRegistry()


def register_horizon_source(
    cls: type[OfficialHorizonAdapter],
) -> type[OfficialHorizonAdapter]:
    REGISTRY.add(cls())
    return cls
