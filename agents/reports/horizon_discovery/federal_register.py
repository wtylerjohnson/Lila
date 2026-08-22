"""Federal Register program-signal adapter."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)

_RESERVED_PAYLOAD_KEYS = frozenset({
    "_attempts", "disabled", "error", "errors", "note",
})


@register_horizon_source
class FederalRegisterHorizonAdapter(OfficialHorizonAdapter):
    name = "federal_register"
    result_key = "federal_register"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for keyword, items in payload.items():
            if keyword in _RESERVED_PAYLOAD_KEYS:
                continue
            if not isinstance(items, list):
                continue
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                raw_agencies = item.get("agencies") or []
                agencies = [
                    value.get("name") if isinstance(value, dict) else value
                    for value in raw_agencies
                ]
                entry = program_entry(
                    f"Federal Register {item.get('type', 'document')} "
                    f"({item.get('publication_date', '')}): "
                    f"\"{item.get('title', '')}\" [matched: {keyword}]",
                    item.get("html_url") or item.get("url"),
                    "regulatory",
                    agencies=agencies,
                    source_record_id=(
                        item.get("document_number") or item.get("id")),
                    retrieved_at=item.get("retrieved_at"),
                    data_as_of=(
                        item.get("data_as_of") or item.get("publication_date")),
                )
                if entry:
                    out.append(entry)
        return out
