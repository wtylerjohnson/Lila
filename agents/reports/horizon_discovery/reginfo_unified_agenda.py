"""Offline RegInfo Unified Agenda mapper for the Developing Horizon."""

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


def _timetable(row: Mapping[str, Any]) -> str:
    events = []
    for value in row.get("timetable") or []:
        if not isinstance(value, dict):
            continue
        action = str(value.get("action") or "").strip()
        stated_date = str(value.get("date") or "").strip()
        if action or stated_date:
            events.append(" ".join(part for part in (action, stated_date) if part))
    return "; ".join(events[:3])


@register_horizon_source
class RegInfoUnifiedAgendaHorizonAdapter(OfficialHorizonAdapter):
    name = "reginfo_unified_agenda"
    result_key = "reginfo_unified_agenda"
    coverage_agency = "agenda-publishing agencies"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in list(stored_program_records(payload, source=self.name))[:8]:
            title = str(row.get("title") or "").strip()
            rin = str(row.get("rin") or "").strip()
            if not title or not rin:
                continue
            bits = [f'Unified Agenda rule: "{title}"', f"RIN {rin}"]
            if row.get("stage"):
                bits.append(str(row["stage"]).strip())
            timetable = _timetable(row)
            if timetable:
                bits.append(f"planned actions {timetable}")
            entry = program_entry(
                " · ".join(bits),
                row["canonical_url"],
                "regulatory",
                agency=row["agency"],
                component=row.get("component"),
                source_record_id=row["record_id"],
                retrieved_at=row["retrieved_at"],
                data_as_of=row["data_as_of"],
            )
            if entry:
                out.append(entry)
        return out
