"""GAO and Inspector General program-signal adapter."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)


@register_horizon_source
class WatchdogHorizonAdapter(OfficialHorizonAdapter):
    name = "watchdogs"
    result_key = "watchdogs"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for item in (payload.get("items") or [])[:8]:
            if not isinstance(item, dict):
                continue
            entry = program_entry(
                f"{item.get('source')} report ({item.get('published', '')}): "
                f"{item.get('title', '')} · {str(item.get('body', ''))[:120]} "
                f"[matched: {', '.join(item.get('matched') or [])}]",
                item.get("url"),
                "oversight",
                agency=item.get("agency"),
                component=item.get("component"),
                source_record_id=(
                    item.get("report_id") or item.get("id")),
                retrieved_at=item.get("retrieved_at"),
                data_as_of=item.get("data_as_of") or item.get("published"),
            )
            if entry:
                out.append(entry)
        return out
