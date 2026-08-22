"""USAspending budget execution — the spend-pressure layer (no key).

Per-agency budgetary resources vs obligations for the current fiscal year:
how much money an agency HOLDS, how much it has obligated, and therefore how
much sits unobligated as the fiscal year burns down. End-of-FY spend pressure
is the most mechanical demand signal in federal capture; nothing else in the
stack covers it. Verified live 2026-07-09 (DHS FY26: $575.9B resources,
$310.7B obligated).

    GET /api/v2/references/toptier_agencies/          name -> toptier_code
    GET /api/v2/agency/<code>/budgetary_resources/    per-FY execution
"""

from __future__ import annotations

from typing import Any, Optional

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

BASE = "https://api.usaspending.gov/api/v2"


def _fy_row(payload: dict) -> Optional[dict]:
    """The most recent fiscal year's execution row."""
    rows = [r for r in payload.get("agency_data_by_year") or []
            if isinstance(r.get("fiscal_year"), int)]
    return max(rows, key=lambda r: r["fiscal_year"]) if rows else None


@register_source
class BudgetPressureSource(DataSource):
    name = "budget_pressure"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            n = len((get_json(f"{BASE}/references/toptier_agencies/",
                              timeout=10.0, retries=1) or {}).get("results") or [])
            return bool(n), f"toptier agency reference reachable ({n} agencies)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def _resolve_toptier(
        self, names: list[str],
    ) -> tuple[list[dict], list[str], list[str]]:
        """Resolve every distinct requested agency without hiding misses.

        Returns ``(resolved, unresolved, requested)``. Resolved agencies remain
        deduplicated by toptier code, while ``requested_names`` preserves every
        alias that produced the match. That lets the public payload distinguish
        a true zero-row result from an agency name the reference table did not
        recognize.
        """
        payload = get_json(f"{BASE}/references/toptier_agencies/")
        refs = payload.get("results") or []
        out: list[dict] = []
        by_code: dict[str, dict] = {}
        unresolved: list[str] = []
        requested: list[str] = []
        seen_requests: set[str] = set()
        for raw in names:
            clean = (raw or "").strip()
            q = clean.casefold()
            if not q or q in seen_requests:
                continue
            seen_requests.add(q)
            requested.append(clean)
            hit = next((r for r in refs
                        if q == (r.get("agency_name") or "").casefold()
                        or q in (r.get("agency_name") or "").casefold()
                        or (r.get("abbreviation") or "").casefold() == q), None)
            if not hit:
                unresolved.append(clean)
                continue
            code = hit["toptier_code"]
            if code not in by_code:
                entry = {"name": hit["agency_name"],
                         "toptier_code": code,
                         "requested_names": [clean]}
                by_code[code] = entry
                out.append(entry)
            else:
                by_code[code]["requested_names"].append(clean)
        return out, unresolved, requested

    def resolve_toptier(self, names: list[str]) -> list[dict]:
        """Match agency names to toptier codes, order-preserving and deduped."""
        resolved, _, _ = self._resolve_toptier(names)
        return resolved

    def pressure(self, toptier_code: str) -> Optional[dict]:
        """Current-FY execution for one agency: resources, obligated,
        unobligated, percent obligated — every figure straight off the API."""
        url = f"{BASE}/agency/{toptier_code}/budgetary_resources/"
        row = _fy_row(get_json(url))
        if not row:
            return None
        res = row.get("agency_budgetary_resources")
        obl = row.get("agency_total_obligated")
        if not isinstance(res, (int, float)) or not isinstance(obl, (int, float)):
            return None
        return {"toptier_code": toptier_code,
                "fiscal_year": row["fiscal_year"],
                "budgetary_resources": res,
                "obligated": obl,
                "unobligated": round(res - obl, 2),
                "pct_obligated": round(100.0 * obl / res, 1) if res else None,
                "url": url}

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """{'rows': [...]} — execution for every resolvable agency in the
        query. A failed agency is recorded, never silently dropped."""
        resolved, unresolved, requested = self._resolve_toptier(
            list(query.agencies or []))
        rows: list[dict] = []
        errors = {
            name: "agency not found in USAspending toptier reference"
            for name in unresolved
        }
        for a in resolved:
            try:
                p = self.pressure(a["toptier_code"])
            except Exception as e:  # noqa: BLE001 — one agency never sinks the rest
                errors[a["name"]] = str(e)
                continue
            if p:
                rows.append({"agency": a["name"], **p})
            else:
                errors[a["name"]] = (
                    "no valid latest-fiscal-year budget execution row returned"
                )
        return {"rows": rows, "errors": errors,
                "requested_agencies": requested,
                "resolved_agencies": [a["name"] for a in resolved],
                "unresolved_agencies": unresolved,
                "basis": "USAspending agency budgetary resources, latest FY"}
