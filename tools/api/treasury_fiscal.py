"""Treasury FiscalData adapter — is appropriated money actually MOVING? (keyless)

MTS Table 5 (verified live 2026-07-03) reports monthly + FYTD outlays by
agency/classification line. Appropriations say what Congress intends; outlays
show what agencies are spending right now — the difference between a budget
line and a live market.

API: https://api.fiscaldata.treasury.gov/services/api/fiscal_service
     /v1/accounting/mts/mts_table_5?sort=-record_date&page[size]=N   (no key)
Rows: {record_date, classification_desc, current_fytd_gross_outly_amt, ...}
"""

from __future__ import annotations

from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

MTS5_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
            "/v1/accounting/mts/mts_table_5")


def _rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Treasury Fiscal Data response omitted the required data list")
    rows = payload["data"]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Treasury Fiscal Data contained a non-object row")
        if not str(row.get("record_date") or "").strip():
            raise ValueError("Treasury Fiscal Data row omitted record_date")
        if not str(row.get("classification_desc") or "").strip():
            raise ValueError(
                "Treasury Fiscal Data row omitted classification_desc"
            )
        if "current_fytd_gross_outly_amt" not in row:
            raise ValueError(
                "Treasury Fiscal Data row omitted current FYTD gross outlays"
            )
    return rows


def _amount(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


@register_source
class TreasuryFiscalSource(DataSource):
    name = "treasury_fiscal"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            rows = _rows(get_json(
                MTS5_URL,
                params={"page[size]": 1},
                timeout=8.0,
                retries=1,
            ))
            return True, (
                "fiscaldata.treasury.gov reachable "
                f"(keyless; {len(rows)} sampled row)"
            )
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """FYTD outlays for classification lines matching the target agencies."""
        agencies = [a.lower() for a in (query.agencies or []) if a] or None
        payload = get_json(MTS5_URL, params={
            "sort": "-record_date", "page[size]": 400,
        })
        rows = _rows(payload)
        as_of = rows[0].get("record_date") if rows else None
        matched: dict[str, dict] = {}
        for r in rows:
            if r.get("record_date") != as_of:
                break  # only the newest month's snapshot
            desc = (r.get("classification_desc") or "").strip().rstrip(":")
            amt = _amount(r.get("current_fytd_gross_outly_amt"))
            if amt is None or not desc:
                continue
            if agencies and not any(a in desc.lower() for a in agencies):
                continue
            cur = matched.get(desc)
            if cur is None or amt > cur["fytd_gross_outlays"]:
                matched[desc] = {"line": desc, "fytd_gross_outlays": amt,
                                 "as_of": r.get("record_date")}
        top = sorted(matched.values(), key=lambda x: -x["fytd_gross_outlays"])[:12]
        return {"as_of": as_of, "matched_lines": top,
                "note": "FYTD gross outlays, MTS Table 5 — money moving, not just appropriated"}
