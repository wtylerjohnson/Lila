"""Offline SBIR.gov topic mapper for the Developing Horizon."""

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
class SbirTopicsHorizonAdapter(OfficialHorizonAdapter):
    name = "sbir_gov"
    result_key = "sbir_gov"
    coverage_agency = "SBIR/STTR participating agencies"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in stored_program_records(payload, source=self.name):
            if not row.get("matched_terms"):
                continue
            title = str(row.get("title") or "").strip()
            number = str(row.get("number") or "").strip()
            if not title or not number:
                continue
            bits = [f'SBIR/STTR topic {number}: "{title}"']
            if row.get("open_date"):
                bits.append(f"opens {str(row['open_date']).strip()}")
            if row.get("close_date"):
                bits.append(f"closes {str(row['close_date']).strip()}")
            objective = str(row.get("objective") or "").strip()
            if objective:
                bits.append(objective[:240])
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
