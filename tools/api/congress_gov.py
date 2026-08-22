"""Congress.gov bill wire — money FORMING, upstream of every forecast.

Appropriations lines, authorizations, and program language presage
procurement by quarters. The v3 API has no server-side text search (the
query param is ignored; verified live 2026-07-09), so this is the wire
pattern: pull the most recently UPDATED bills of the current congress,
keyword-filter titles against the client's capability terms, and flag
appropriations/NDAA vehicles. Every kept bill carries its constructed
congress.gov URL. Free key: CONGRESS_GOV_API_KEY.
"""

from __future__ import annotations

import os
from typing import Any

from tools.api._http import get_json
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)

BASE = "https://api.congress.gov/v3"
CURRENT_CONGRESS = int(os.environ.get("LILA_CONGRESS_NUMBER", "119"))
PAGES = 2          # 2 x 250 most recently updated bills per pull
_TYPE_SLUG = {"hr": "house-bill", "s": "senate-bill",
              "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
              "hconres": "house-concurrent-resolution",
              "sconres": "senate-concurrent-resolution",
              "hres": "house-resolution", "sres": "senate-resolution"}
_MONEY_VEHICLES = ("appropriation", "ndaa", "national defense authorization",
                   "continuing resolution", "authorization act")


def _bills(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("bills"), list):
        raise ValueError("Congress.gov response omitted the required bills list")
    rows = payload["bills"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Congress.gov bills contained a non-object row")
    for row in rows:
        required = ("congress", "type", "number", "title")
        if any(not str(row.get(field) or "").strip() for field in required):
            raise ValueError(
                "Congress.gov bill omitted congress, type, number, or title"
            )
    return rows


def bill_url(congress: int, bill_type: str, number: str) -> str:
    slug = _TYPE_SLUG.get((bill_type or "").lower(), "house-bill")
    return f"https://www.congress.gov/bill/{congress}th-congress/{slug}/{number}"


@register_source
class CongressGovSource(DataSource):
    name = "congress_gov"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("CONGRESS_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        if not self._key:
            return False, "CONGRESS_GOV_API_KEY missing (free: api.congress.gov)"
        try:
            d = get_json(f"{BASE}/bill?format=json&limit=1&api_key={self._key}",
                         timeout=10.0, retries=1)
            return bool(d.get("bills")), "congress.gov v3 reachable"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """{'items': [...], 'total_scanned': N} — recently updated bills whose
        titles hit capability terms, matched terms recorded, money vehicles
        flagged. No key -> honest empty with a note, never an exception."""
        if not self._key:
            # AN ERROR, NOT A NOTE (2026-07-30): the note-shaped return made
            # attempt_row report ok=True and the client-facing coverage band
            # called a lane that gathered NOTHING "RETURNED". A missing key
            # is a failure to gather, and failures are named.
            return {"items": [], "total_scanned": 0,
                    "error": "CONGRESS_GOV_API_KEY not set; lane cannot "
                             "gather (set the key or skip the lane)",
                    "note": "CONGRESS_GOV_API_KEY not set"}
        items, scanned, offset = [], 0, 0
        for _ in range(PAGES):
            try:
                d = get_json(f"{BASE}/bill/{CURRENT_CONGRESS}?format=json"
                             f"&limit=250&offset={offset}&sort=updateDate+desc"
                             f"&api_key={self._key}")
            except Exception as e:  # noqa: BLE001 — a page failure keeps what we have
                return {**cap_disclosed(items, 20),
                        "total_scanned": scanned, "error": str(e)}
            bills = _bills(d)
            if not bills:
                break
            scanned += len(bills)
            for b in bills:
                title = b.get("title") or ""
                low = title.lower()
                hits = [k for k in query.keywords
                        if k and k.lower().strip('"') in low]
                vehicle = next((v for v in _MONEY_VEHICLES if v in low), None)
                if not hits:
                    continue
                items.append({
                    "title": title,
                    "bill": f"{b.get('type', '')}{b.get('number', '')}",
                    "latest_action": (b.get("latestAction") or {}).get("text"),
                    "action_date": (b.get("latestAction") or {}).get("actionDate"),
                    "matched": hits,
                    "money_vehicle": vehicle,
                    "url": bill_url(b.get("congress") or CURRENT_CONGRESS,
                                    b.get("type") or "", str(b.get("number") or "")),
                })
            offset += 250
        return {**cap_disclosed(items, 20), "total_scanned": scanned}
