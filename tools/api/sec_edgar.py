"""SEC EDGAR full-text search adapter — competitor federal exposure (keyless).

Public companies must disclose material customer concentration. Full-text
search of recent 10-K/10-Q filings for the client's category terms shows which
public competitors talk about federal revenue and how — free competitor sizing
no web search assembles reliably.

API (EDGAR full-text search, keyless; SEC requires a User-Agent identifying
the requester — https://www.sec.gov/os/accessing-edgar-data):
    GET https://efts.sec.gov/LATEST/search-index?q="..."&forms=10-K
    Response: {"hits": {"total": {...}, "hits": [{"_source": {"display_names":
              [...], "file_date", "root_forms", ...}}]}}
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": "GTM Group LILA william.tyler.johnson@gmail.com",
           "Accept": "application/json"}
FORMS = "10-K,10-Q"


def _ui_link(term: str) -> str:
    return f"https://www.sec.gov/edgar/search/#/q=%22{quote(term)}%22&forms={FORMS}"


@register_source
class SecEdgarSource(DataSource):
    name = "sec_edgar"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            get_json(FTS_URL, params={"q": '"cybersecurity"', "forms": "10-K"},
                     headers=HEADERS, timeout=8.0, retries=1)
            return True, "EDGAR full-text search reachable (keyless)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Recent 10-K/10-Q filings mentioning each keyword: who + when + link."""
        out: dict[str, Any] = {}
        # THE KEYWORD CAP IS DISCLOSED TOO (2026-07-30): screening 4 of 12
        # terms while reporting per-term results read as full coverage.
        terms = list(query.keywords or [])
        out["keywords_screened"] = min(len(terms), 4)
        out["keywords_total"] = len(terms)
        if len(terms) > 4:
            out["keywords_truncated"] = True
        for kw in terms[:4]:
            term = kw.strip('"')
            payload = get_json(FTS_URL, params={"q": f'"{term}"', "forms": FORMS},
                               headers=HEADERS)
            hits = ((payload.get("hits") or {}).get("hits")) or []
            rows = []
            seen: set[str] = set()
            for h in hits[:12]:
                src = h.get("_source") or {}
                names = src.get("display_names") or []
                name = names[0] if names else None
                if not name or name in seen:
                    continue
                seen.add(name)
                rows.append({"company": name,
                             "form": src.get("root_forms") or src.get("file_type"),
                             "filed": src.get("file_date")})
            if rows:
                entry = {"companies": rows[:8], "search_url": _ui_link(term),
                         "hits_total": len(hits)}
                if len(hits) > 12 or len(rows) > 8:
                    entry["truncated"] = True
                out[term] = entry
        from tools.api.provenance import make_provenance_envelope
        partial = bool(out.get("keywords_truncated"))
        out["_provenance"] = make_provenance_envelope(
            "edgar",
            status="partial" if partial else "complete",
            mode="live_bounded_full_text_search",
            retrieval_mode="live",
            record_count=sum(
                len(value.get("companies", []))
                for key, value in out.items()
                if not key.startswith("_") and isinstance(value, dict)
            ),
            limitations=(
                [f"Screened 4 of {len(terms)} approved terms."]
                if partial else []
            ),
            public_detail=(
                f"SEC EDGAR full-text context · screened "
                f"{min(len(terms), 4)} of {len(terms)} approved terms"
            ),
        ).model_dump(mode="json")
        return out
