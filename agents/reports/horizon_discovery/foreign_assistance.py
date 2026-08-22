"""Offline ForeignAssistance.gov budget mapper for Developing Horizon."""

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


def _amount(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return f"${value:,.2f}"


@register_horizon_source
class ForeignAssistanceHorizonAdapter(OfficialHorizonAdapter):
    name = "foreign_assistance"
    result_key = "foreign_assistance"
    coverage_agency = "U.S. Government foreign assistance"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in list(stored_program_records(payload, source=self.name))[:8]:
            title = str(row.get("title") or "").strip()
            fiscal_year = str(row.get("fiscal_year") or "").strip()
            if not title or not fiscal_year:
                continue
            bits = [
                f'ForeignAssistance.gov FY{fiscal_year} budget request: '
                f'"{title}"'
            ]
            current = _amount(row.get("current_amount"))
            if current:
                bits.append(f"current amount {current}")
            entry = program_entry(
                " · ".join(bits),
                row["canonical_url"],
                "budget_line",
                agency=row["agency"],
                source_record_id=row["record_id"],
                retrieved_at=row["retrieved_at"],
                data_as_of=row["data_as_of"],
            )
            if entry:
                out.append(entry)
        return out
