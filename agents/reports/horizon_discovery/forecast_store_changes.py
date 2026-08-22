"""Forecast-store change-event program-signal adapter."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)


@register_horizon_source
class ForecastStoreChangesHorizonAdapter(OfficialHorizonAdapter):
    name = "forecast_store_changes"
    result_key = "forecast_signals"
    coverage_agency = "forecast-source agencies"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for event in (payload.get("events") or [])[:8]:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "change")
            if kind == "disappeared":
                detail = (
                    f"published forecast line \"{event.get('title', '')}\" is no "
                    "longer present in the latest official-source pull"
                )
            else:
                detail = str(event.get("detail") or event.get("title") or "").strip()
            entry = program_entry(
                f"Forecast change signal ({kind}): {detail}",
                event.get("url"),
                "forecast_delta",
                agency=event.get("agency"),
                component=event.get("component"),
                source_record_id=(
                    event.get("source_id") or event.get("record_id")),
                retrieved_at=event.get("retrieved_at"),
                data_as_of=event.get("data_as_of") or event.get("observed_at"),
            )
            if entry:
                out.append(entry)
        return out
