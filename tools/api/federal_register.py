"""Federal Register adapter — program-formation signal (live, no key required).

Rules, notices, and executive documents surface 6–18 months before the
solicitations they eventually fund: screening mandates, new program offices,
security directives. Searching the client's strategy keywords here gives the
Assess stage forward signal that SAM.gov cannot show yet.

API: https://www.federalregister.gov/developers/documentation/api/v1  (public, no auth)
"""

from __future__ import annotations

from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

DOCS_URL = "https://www.federalregister.gov/api/v1/documents.json"


def _results(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(
            "Federal Register response omitted the required results list"
        )
    rows = payload["results"]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Federal Register results contained a non-object row")
        if not str(row.get("title") or "").strip():
            raise ValueError("Federal Register result omitted its title")
        if not str(row.get("html_url") or "").strip():
            raise ValueError("Federal Register result omitted its document URL")
    return rows


@register_source
class FederalRegisterSource(DataSource):
    name = "federal_register"
    kind = SourceKind.ENRICHMENT


    def healthcheck(self) -> tuple[bool, str]:
        try:
            rows = _results(get_json(
                DOCS_URL, params={"per_page": 1}, timeout=5.0, retries=1,
            ))
            return True, f"federalregister.gov reachable ({len(rows)} sampled row)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")
    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Return recent documents per keyword lane: {keyword: [doc, ...]}."""
        out: dict[str, Any] = {}
        for kw in (query.keywords or [])[:8]:
            payload = get_json(
                DOCS_URL,
                params={
                    "conditions[term]": kw,
                    "order": "newest",
                    "per_page": 5,
                    "fields[]": ["title", "type", "abstract", "publication_date",
                                 "html_url", "agencies"],
                },
            )
            docs = []
            for d in _results(payload):
                docs.append({
                    "title": d.get("title"),
                    "type": d.get("type"),
                    "abstract": (d.get("abstract") or "")[:400],
                    "publication_date": d.get("publication_date"),
                    "url": d.get("html_url"),
                    "agencies": [a.get("name") for a in (d.get("agencies") or [])
                                 if isinstance(a, dict)],
                })
            if docs:
                out[kw] = docs
        return out
