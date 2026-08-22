"""Offline DoD budget-exhibit mapper for the Developing Horizon."""

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


def _reported_values(row: Mapping[str, Any]) -> str:
    values = row.get("values")
    if not isinstance(values, dict):
        return ""
    rendered = []
    for label in sorted(values):
        value = values[label]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        shown = f"{value:,.12g}"
        rendered.append(f"{str(label).strip()} {shown}")
    return "; ".join(rendered[:4])


@register_horizon_source
class DodBudgetExhibitsHorizonAdapter(OfficialHorizonAdapter):
    name = "dod_budget_exhibits"
    result_key = "dod_budget_exhibits"
    coverage_agency = "Department of Defense"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in list(stored_program_records(payload, source=self.name))[:8]:
            title = str(row.get("title") or "").strip()
            initiative = str(row.get("initiative") or "").strip()
            if not title or not initiative:
                continue
            bits = [
                f'DoD budget exhibit ({initiative.replace("_", " ")}): '
                f'"{title}"'
            ]
            if row.get("budget_year"):
                bits.append(f"budget year {str(row['budget_year']).strip()}")
            values = _reported_values(row)
            if values:
                bits.append(f"reported values {values}")
            entry = program_entry(
                " · ".join(bits),
                row["canonical_url"],
                "budget_line",
                agency=row["agency"],
                component=row.get("component"),
                source_record_id=row["record_id"],
                retrieved_at=row["retrieved_at"],
                data_as_of=row["data_as_of"],
            )
            if entry:
                out.append(entry)
        return out
