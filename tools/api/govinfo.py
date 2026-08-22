"""GovInfo adapter — bill text, committee reports, public laws (funding proof).

The capture-brief dollar anchor problem: a notice says WHAT an agency wants,
but appropriations language proves the program is FUNDED. GovInfo full-text
search across BILLS / CRPT (committee reports) / PLAW (public laws) surfaces
the client's keywords inside actual legislative text — the strongest "this
money is real" citation a brief can carry.

API (grounded from https://api.govinfo.gov/docs/):
    POST https://api.govinfo.gov/search   header X-Api-Key
        {"query": "...", "pageSize": N, "offsetMark": "*",
         "sorts": [{"field": "publishdate", "sortOrder": "DESC"}]}
    Response: {"count": N, "packages": [{packageId, title, dateIssued,
               collectionCode, ...}]}
Key: the api.data.gov key (env DATA_GOV_API_KEY) — same one as Regulations.gov.
"""

from __future__ import annotations

import os
from typing import Any

from tools.api._http import get_json, post_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

BASE = os.environ.get("LILA_GOVINFO_URL", "https://api.govinfo.gov")
COLLECTIONS = "BILLS OR CRPT OR PLAW"  # bills, committee reports, public laws
SIGNUP = "https://api.data.gov/signup/"


def _packages(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("GovInfo response must be an object")
    rows = None
    for key in ("packages", "results"):
        if key in payload:
            rows = payload[key]
            break
    if not isinstance(rows, list):
        raise ValueError("GovInfo response omitted the required packages list")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("GovInfo packages contained a non-object row")
    for row in rows:
        if not str(row.get("packageId") or "").strip():
            raise ValueError("GovInfo package omitted packageId")
    return rows


@register_source
class GovInfoSource(DataSource):
    name = "govinfo"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("DATA_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, f"DATA_GOV_API_KEY missing — free 2-min signup: {SIGNUP}"
        try:
            get_json(f"{BASE}/collections", params={"api_key": self._api_key},
                     timeout=5.0, retries=1)
            return True, "api.govinfo.gov reachable, key accepted"
        except Exception as e:  # noqa: BLE001
            return False, f"key set but call failed: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def funded_demand(self, terms: list[str], *, page_size: int = 5) -> dict[str, Any]:
        """FUNDED-DEMAND SCAN (2026-07-09): core capability terms against
        budget/appropriations text (BUDGET, committee reports, hearings).
        Returns citable document hits; dollar-line extraction from full text
        is a recorded limitation, not faked. Empty is a valid finding."""
        if not self._api_key:
            raise RuntimeError(f"DATA_GOV_API_KEY not set. Free signup: {SIGNUP}")
        out: dict[str, Any] = {"hits": {}, "calls": 0,
                               "collections": "BUDGET OR CRPT OR CHRG",
                               "limitation": ("document-level citations only; "
                                              "dollar-line extraction requires "
                                              "full-text download, recorded as "
                                              "PROPOSED in LEARNINGS")}
        for term in terms:
            payload = post_json(
                f"{BASE}/search",
                json={"query": f'"{term}" AND collection:(BUDGET OR CRPT OR CHRG)',
                      "pageSize": page_size, "offsetMark": "*",
                      "sorts": [{"field": "publishdate", "sortOrder": "DESC"}]},
                headers={"X-Api-Key": self._api_key})
            out["calls"] += 1
            pkgs = []
            for p in _packages(payload):
                pid = p.get("packageId")
                pkgs.append({"id": pid, "title": p.get("title"),
                             "collection": p.get("collectionCode") or p.get("docClass"),
                             "date": p.get("dateIssued") or p.get("lastModified"),
                             "url": f"https://www.govinfo.gov/app/details/{pid}"
                                    if pid else None})
            if pkgs:
                out["hits"][term] = pkgs
        return out

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Legislative hits per keyword lane: {keyword: [package, ...]}.

        Each package links straight to govinfo.gov — a citable primary source
        for the brief's funding narrative.
        """
        if not self._api_key:
            raise RuntimeError(
                f"DATA_GOV_API_KEY not set. Free signup: {SIGNUP} — then add "
                "DATA_GOV_API_KEY=... to .env and restart."
            )
        out: dict[str, Any] = {}
        for kw in (query.keywords or [])[:6]:
            term = kw.strip('"')
            payload = post_json(
                f"{BASE}/search",
                json={
                    "query": f'"{term}" AND collection:({COLLECTIONS})',
                    "pageSize": 5,
                    "offsetMark": "*",
                    "sorts": [{"field": "publishdate", "sortOrder": "DESC"}],
                },
                headers={"X-Api-Key": self._api_key},
            )
            pkgs = []
            for p in _packages(payload):
                pid = p.get("packageId")
                pkgs.append({
                    "id": pid,
                    "title": p.get("title"),
                    "collection": p.get("collectionCode") or p.get("docClass"),
                    "date": p.get("dateIssued") or p.get("lastModified"),
                    "url": f"https://www.govinfo.gov/app/details/{pid}" if pid else None,
                })
            if pkgs:
                out[kw] = pkgs
        return out
