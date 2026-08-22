"""Regulations.gov v4 adapter — proposed rules + comment dockets (policy layer).

Rules create requirements; requirements create procurement. A proposed rule in
the client's domain is demand signal 6–18 months early, and an OPEN COMMENT
docket is more than signal — it's a lawful engagement channel: a substantive
comment puts the client's name and expertise in front of the program office
before any solicitation exists.

API: https://open.gsa.gov/api/regulationsgov/
    GET https://api.regulations.gov/v4/documents
        ?filter[searchTerm]=...&sort=-postedDate&api_key=...
Key: free api.data.gov key (https://api.data.gov/signup/), env DATA_GOV_API_KEY.
The same key later unlocks GovInfo.
"""

from __future__ import annotations

import os
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

DOCS_URL = "https://api.regulations.gov/v4/documents"
SIGNUP = "https://api.data.gov/signup/"


def _documents(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(
            "Regulations.gov response omitted the required data list"
        )
    rows = payload["data"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Regulations.gov data contained a non-object row")
    for row in rows:
        attributes = row.get("attributes")
        if (not str(row.get("id") or "").strip()
                or not isinstance(attributes, dict)
                or not str(attributes.get("title") or "").strip()):
            raise ValueError(
                "Regulations.gov document omitted id, attributes, or title"
            )
    return rows


@register_source
class RegulationsGovSource(DataSource):
    name = "regulations_gov"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("DATA_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, f"DATA_GOV_API_KEY missing — free 2-min signup: {SIGNUP}"
        try:
            get_json(DOCS_URL, params={"api_key": self._api_key, "page[size]": 5},
                     timeout=5.0, retries=1)
            return True, "api.regulations.gov reachable, key accepted"
        except Exception as e:  # noqa: BLE001
            return False, f"key set but call failed: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Newest documents per keyword lane: {keyword: [doc, ...]}.

        Comment-period docs are the gold — flagged so the report layer can call
        out the engagement window, not just the signal.
        """
        if not self._api_key:
            raise RuntimeError(
                f"DATA_GOV_API_KEY not set. Free signup: {SIGNUP} — then add "
                "DATA_GOV_API_KEY=... to .env and restart."
            )
        out: dict[str, Any] = {}
        for kw in (query.keywords or [])[:6]:
            payload = get_json(
                DOCS_URL,
                params={
                    "api_key": self._api_key,
                    "filter[searchTerm]": kw.strip('"'),
                    "sort": "-postedDate",
                    "page[size]": 5,
                },
            )
            docs = []
            for d in _documents(payload):
                a = d.get("attributes") or {}
                doc_id = d.get("id")
                docs.append({
                    "id": doc_id,
                    "title": a.get("title"),
                    "type": a.get("documentType"),
                    "agency": a.get("agencyId"),
                    "posted": a.get("postedDate"),
                    "docket": a.get("docketId"),
                    "comment_open": bool(a.get("openForComment")),
                    "comment_ends": a.get("commentEndDate"),
                    "url": f"https://www.regulations.gov/document/{doc_id}" if doc_id else None,
                })
            if docs:
                out[kw] = docs
        return out
