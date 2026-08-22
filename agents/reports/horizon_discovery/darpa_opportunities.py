"""Offline DARPA research-program mapper for the Developing Horizon."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery._stored_program import (
    stored_program_records,
)
from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)


@register_horizon_source
class DarpaOpportunitiesHorizonAdapter(OfficialHorizonAdapter):
    name = "darpa_opportunities"
    result_key = "darpa_opportunities"
    coverage_agency = "DARPA"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in list(stored_program_records(payload, source=self.name))[:8]:
            title = str(row.get("title") or "").strip()
            signal_type = str(row.get("signal_type") or "").strip()
            if not title or not signal_type:
                continue
            bits = [
                f'DARPA {signal_type.replace("_", " ")}: "{title}"'
            ]
            if row.get("published_at"):
                bits.append(f"published {str(row['published_at']).strip()}")
            summary = str(row.get("summary") or "").strip()
            if summary:
                bits.append(summary[:240])
            entry = program_entry(
                " · ".join(bits),
                row["canonical_url"],
                "agency_announcement",
                agency=row["agency"],
                source_record_id=row["record_id"],
                retrieved_at=row["retrieved_at"],
                data_as_of=row["data_as_of"],
            )
            if entry:
                out.append(entry)
        return out
