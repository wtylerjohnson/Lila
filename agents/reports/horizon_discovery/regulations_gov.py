"""Regulations.gov program-signal adapter."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)


@register_horizon_source
class RegulationsGovHorizonAdapter(OfficialHorizonAdapter):
    name = "regulations_gov"
    result_key = "regulations_gov"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for keyword, items in payload.items():
            if not isinstance(items, list):
                continue
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                bits = [
                    f"Regulations.gov {item.get('type', 'document')} "
                    f"({item.get('posted', '')}): \"{item.get('title', '')}\""
                ]
                if item.get("docket"):
                    bits.append(f"docket {item['docket']}")
                if item.get("comment_open"):
                    ending = item.get("comment_ends") or "date not stated"
                    bits.append(f"comment period open through {ending}")
                bits.append(f"matched: {keyword}")
                entry = program_entry(
                    " · ".join(bits),
                    item.get("url"),
                    "regulatory",
                    agency=item.get("agency"),
                    component=item.get("component"),
                    agencies=item.get("agencies"),
                    source_record_id=(
                        item.get("document_id") or item.get("id")
                        or item.get("docket")),
                    retrieved_at=item.get("retrieved_at"),
                    data_as_of=item.get("data_as_of") or item.get("posted"),
                )
                if entry:
                    out.append(entry)
        return out
