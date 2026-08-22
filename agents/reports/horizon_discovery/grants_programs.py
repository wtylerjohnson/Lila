"""Offline Grants.gov assistance-program mapper for Developing Horizon."""

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
class GrantsProgramsHorizonAdapter(OfficialHorizonAdapter):
    name = "grants_gov"
    result_key = "grants_gov"
    coverage_agency = "grant-making agencies"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in stored_program_records(payload, source=self.name):
            title = str(row.get("title") or "").strip()
            number = str(row.get("number") or "").strip()
            if not title:
                continue
            heading = "Grants.gov assistance program"
            if number:
                heading += f" {number}"
            bits = [f'{heading}: "{title}"']
            if row.get("status"):
                bits.append(str(row["status"]).strip())
            if row.get("open_date"):
                bits.append(f"opens {str(row['open_date']).strip()}")
            if row.get("close_date"):
                bits.append(f"closes {str(row['close_date']).strip()}")
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
