"""USAspending budget-execution program-signal adapter."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)


@register_horizon_source
class BudgetLinesHorizonAdapter(OfficialHorizonAdapter):
    name = "budget_pressure"
    result_key = "budget_pressure"
    coverage_agency = "scope agencies"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in (payload.get("rows") or [])[:8]:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(key), (int, float))
                for key in ("budgetary_resources", "obligated", "unobligated")
            ):
                continue
            pct = row.get("pct_obligated")
            pct_clause = f" ({pct}%)" if isinstance(pct, (int, float)) else ""
            entry = program_entry(
                f"{row.get('agency')} FY{row.get('fiscal_year')} budget execution: "
                f"${row['budgetary_resources'] / 1e9:,.1f}B resources, "
                f"${row['obligated'] / 1e9:,.1f}B obligated{pct_clause}, "
                f"${row['unobligated'] / 1e9:,.1f}B unobligated",
                row.get("url"),
                "budget_line",
                agency=row.get("agency"),
                source_record_id=(
                    row.get("record_id") or row.get("agency_code")),
                retrieved_at=row.get("retrieved_at"),
                data_as_of=row.get("data_as_of") or row.get("as_of"),
            )
            if entry:
                out.append(entry)
        return out
