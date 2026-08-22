"""Offline DSIP active-topic mapper for the Developing Horizon."""

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

_ACTIVE_STATUSES = frozenset({"Open", "Pre-Release"})


def _matched_terms(row: Mapping[str, Any]) -> str:
    values = row.get("matched_terms")
    if not isinstance(values, list):
        return ""
    return ", ".join(
        str(value).strip()
        for value in values
        if isinstance(value, str) and value.strip()
    )


@register_horizon_source
class DsipTopicsHorizonAdapter(OfficialHorizonAdapter):
    name = "dsip_topics"
    result_key = "dsip_topics"
    coverage_agency = "Department of Defense"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        out: list[ProgramBankEntry] = []
        for row in list(stored_program_records(payload, source=self.name))[:8]:
            title = str(row.get("title") or "").strip()
            status = str(row.get("status") or "").strip()
            topic_code = str(row.get("topic_code") or "").strip()
            if (
                not title
                or status not in _ACTIVE_STATUSES
                or not topic_code
                or row.get("live_solicitation") is not False
                or row.get("promotion_eligible") is not False
            ):
                continue
            bits = [f'DSIP {status} topic {topic_code}: "{title}"']
            if row.get("open_date"):
                bits.append(f"opens {str(row['open_date']).strip()}")
            if row.get("close_date"):
                bits.append(f"closes {str(row['close_date']).strip()}")
            matched = _matched_terms(row)
            if matched:
                bits.append(f"matched terms {matched}")
            entry = program_entry(
                " · ".join(bits),
                row["canonical_url"],
                "agency_announcement",
                agency=row["agency"],
                component=row.get("component"),
                source_record_id=row["record_id"],
                retrieved_at=row["retrieved_at"],
                data_as_of=row["data_as_of"],
            )
            if entry:
                out.append(entry)
        return out
