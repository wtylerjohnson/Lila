"""Acquisition provenance, distinct from assessment and local-file clocks.

These receipts describe the sanctioned adapter's input. They are not standalone
source authentication; immutable run binding and original-source gates still apply.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tools.relevance.temporal import source_time


class SourceAcquisition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["assess.source-acquisition.v1"] = "assess.source-acquisition.v1"
    status: Literal["known", "missing", "invalid", "date_only", "unknown_timezone",
                    "conflict", "untrusted"]
    basis: Literal["sam_notice_depth", "horizon_fact_bank", "none"]
    component: Literal["notice_description", "notice_payload", "horizon_fact", "subaward_record", "research_record"]
    original_field: str
    raw_values: tuple[str, ...] = ()
    input_types: tuple[Literal["text", "non_text", "missing"], ...] = ()
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent(self):
        if len(self.input_types) != len(self.raw_values):
            raise ValueError("original source clock types must be retained")
        if self.component == "research_record" and self.basis != "none":
            raise ValueError("research record acquisition remains untrusted")
        expected = _status(self.raw_values, self.basis, self.input_types)
        if self.status != expected:
            raise ValueError("source acquisition status disagrees with original clock values")
        if self.basis == "sam_notice_depth" and (
                self.component != "notice_description" or self.original_field != "source_depth.retrieved_at"):
            raise ValueError("SAM acquisition must bind native description depth")
        if self.basis == "horizon_fact_bank" and (
                self.component != "horizon_fact" or self.original_field != "fact.retrieved_at"):
            raise ValueError("Horizon acquisition must bind its fact-bank field")
        return self


def _status(values, basis, types):
    if not values or all(kind == "missing" or (kind == "text" and not raw.strip())
                         for raw, kind in zip(values, types)):
        return "missing"
    if basis == "none":
        return "untrusted"
    if "non_text" in types:
        return "invalid"
    parsed = [source_time(None if kind == "missing" else value)
              for value, kind in zip(values, types)]
    keys = {(p["parse_status"], p["instant_utc"] or p["raw"]) for p in parsed}
    if len(keys) > 1:
        return "conflict"
    return "known" if parsed[0]["parse_status"] == "aware" else parsed[0]["parse_status"]


def acquisition_clock(value, *, basis, component, field, binding, competing=()):
    values, types = [], []
    for item in (value, *competing):
        raw = item if isinstance(item, str) else json.dumps(item, sort_keys=True, default=str)
        kind = "missing" if item is None else "text" if isinstance(item, str) else "non_text"
        if (raw, kind) not in zip(values, types):
            values.append(raw)
            types.append(kind)
    return SourceAcquisition(status=_status(values, basis, types), basis=basis,
                             component=component, original_field=field,
                             raw_values=tuple(values), input_types=tuple(types), binding_sha256=binding)


def clock_instant(clock):
    if clock is None:
        return None
    clock = SourceAcquisition.model_validate(
        clock.model_dump() if isinstance(clock, SourceAcquisition) else clock)
    if clock.status != "known":
        return None
    return datetime.fromisoformat(source_time(clock.raw_values[0])["instant_utc"])


def acquired_at(evidence):
    """Validated acquisition only; a legacy timestamp has unresolved origin."""
    get = evidence.get if isinstance(evidence, dict) else lambda k, d=None: getattr(evidence, k, d)
    clock = get("source_acquisition")
    try:
        stamp = clock_instant(clock)
        binding = clock.get("binding_sha256") if isinstance(clock, dict) else getattr(clock, "binding_sha256", None)
        recorded = get("retrieved_at")
        actual = source_time(recorded.isoformat() if isinstance(recorded, datetime) else recorded)["instant_utc"]
        if not stamp or binding != get("record_hash") or actual != stamp.isoformat():
            return None
        return stamp
    except (TypeError, ValueError):
        return None


def acquisition_summary(evidence):
    rows = list(evidence)
    dates = [acquired_at(row) for row in rows]
    known = sum(stamp is not None for stamp in dates)
    complete = bool(rows) and known == len(rows)
    return {"status": "complete" if complete else "incomplete",
            "known": known, "total": len(rows),
            "oldest_known_at": min((d for d in dates if d is not None), default=None).isoformat()
            if known else None,
            "source_as_of": min(dates).isoformat() if complete else None}
