"""CISA Known Exploited Vulnerabilities velocity adapter."""

from __future__ import annotations

from typing import Any, Mapping

from agents.reports.horizon_discovery.base import (
    OfficialHorizonAdapter,
    ProgramBankEntry,
    program_entry,
    register_horizon_source,
)

@register_horizon_source
class CisaKevHorizonAdapter(OfficialHorizonAdapter):
    name = "cisa_kev"
    result_key = "cisa_kev"
    coverage_agency = "CISA"

    def map_payload(self, payload: Mapping[str, Any]) -> list[ProgramBankEntry]:
        matched = [item for item in (payload.get("matched") or [])
                   if isinstance(item, dict)]
        recent = payload.get("recent_count")
        window = payload.get("window_days")
        total = payload.get("total")
        source = payload.get("catalog_url")
        out: list[ProgramBankEntry] = []

        # Velocity is useful only when the same official payload also contains
        # a capability-matched KEV row.  This keeps generic cyber activity from
        # becoming a client-invariant thesis.
        if matched and isinstance(recent, int) and isinstance(window, int):
            total_clause = f", from {total} catalog entries" if isinstance(total, int) else ""
            entry = program_entry(
                f"CISA KEV catalog velocity: {recent} vulnerabilities added "
                f"in the last {window} days{total_clause}; capability-matched "
                f"entries are present in this source pull",
                source,
                "cisa_kev",
                retrieved_at=payload.get("retrieved_at"),
                data_as_of=payload.get("data_as_of"),
            )
            if entry:
                out.append(entry)

        for item in matched[:8]:
            identity = " ".join(
                str(item.get(key) or "").strip()
                for key in ("vendor", "product")
                if str(item.get(key) or "").strip()
            )
            bits = [f"CISA KEV {item.get('cve', '')}: {identity}".strip()]
            if item.get("added"):
                bits.append(f"added {item['added']}")
            if item.get("ransomware"):
                bits.append(f"known ransomware campaign use: {item['ransomware']}")
            entry = program_entry(
                " · ".join(bits),
                source,
                "cisa_kev",
                source_record_id=item.get("cve"),
                retrieved_at=item.get("retrieved_at"),
                data_as_of=item.get("data_as_of") or item.get("added"),
            )
            if entry:
                out.append(entry)
        return out
