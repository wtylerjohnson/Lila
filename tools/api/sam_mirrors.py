"""Category-term sweep across SAM.gov mirror services (operator order,
2026-08-18).

SAM.gov opportunity pages render client-side: a raw GET of a sam.gov/opp URL
returns an empty shell, so an empty fetch is never evidence of absence. The
mirror services below carry server-rendered copies of the same notices plus
award context, which makes them the corroboration route for cited sam.gov
URLs and a proxy read on usaspending.gov data. Every sweep attempts every
mirror and receipts what happened; a walled mirror is a receipt, never a
silent skip.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from tools.api._http import get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, cap_disclosed, register_source

SOURCE_NAME = "sam_mirrors"
MAX_TERMS = 8
MAX_SAMPLE_TITLES = 5
FETCH_TIMEOUT_S = 12.0

#: The operator-named roster (2026-08-18). Access notes are measured, dated
#: facts, not guesses; re-measure before changing one. GDIC is GDI
#: Consulting (gdicwins.com), named by the operator with the mirrors.
MIRRORS: tuple[dict, ...] = (
    {
        "id": "highergov",
        "label": "HigherGov",
        "home": "https://www.highergov.com/",
        "search": "https://www.highergov.com/contract-opportunity/?searchText={terms}",
        "access": "public search page, scripted fetch answered 200 (2026-08-18)",
        "roles": ("sam_mirror", "usaspending_proxy"),
    },
    {
        "id": "govtribe",
        "label": "GovTribe",
        "home": "https://govtribe.com/",
        "search": "https://govtribe.com/opportunity?text={terms}",
        "access": "bot-walled: scripted fetch answered HTTP 403 (2026-08-18); "
                  "content is account-viewable in a browser",
        "roles": ("sam_mirror", "usaspending_proxy"),
    },
    {
        "id": "govchime",
        "label": "GovChime",
        "home": "https://www.govchime.com/",
        "search": "https://www.govchime.com/search?q={terms}",
        "access": "public, scripted fetch of the home page answered 200 "
                  "(2026-08-18); search route unconfirmed",
        "roles": ("sam_mirror",),
    },
    {
        "id": "g2xchange",
        "label": "G2Xchange",
        "home": "https://g2xchange.com/",
        "search": "https://g2xchange.com/?s={terms}",
        "access": "bot-walled: scripted fetch answered HTTP 403 (2026-08-18); "
                  "content is public in a browser",
        "roles": ("sam_mirror", "press"),
    },
    {
        "id": "orangeslices",
        "label": "OrangeSlices AI",
        "home": "https://orangeslices.ai/",
        "search": "https://orangeslices.ai/?s={terms}",
        "access": "public, scripted fetch answered 200 (2026-08-18)",
        "roles": ("sam_mirror", "press"),
    },
    {
        "id": "gdic",
        "label": "GDI Consulting (GDIC)",
        "home": "https://www.gdicwins.com/",
        "search": "https://www.gdicwins.com/?s={terms}",
        "access": "public, operator-named 2026-08-18",
        "roles": ("sam_mirror", "press"),
    },
)

SAM_CLIENT_SIDE_NOTE = (
    "SAM.gov opportunity pages render client-side; an empty raw GET of a "
    "sam.gov/opp URL is never evidence of absence. Cited sam.gov URLs ride "
    "store or mirror corroboration."
)

_TAG_STRIP = re.compile(r"<(?:script|style)\b.*?</(?:script|style)>",
                        re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)


def _visible_text(html: str) -> str:
    return " ".join(_TAGS.sub(" ", _TAG_STRIP.sub(" ", html)).split())


def _anchor_texts(html: str) -> list[str]:
    out = []
    for raw in _ANCHOR.findall(html):
        text = " ".join(_TAGS.sub(" ", raw).split())
        if 8 <= len(text) <= 160:
            out.append(text)
    return out


def _occurrences(text: str, term: str) -> int:
    pattern = re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I)
    return len(pattern.findall(text))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@register_source
class SamMirrors(DataSource):
    """One bounded category-term read per mirror per sweep."""

    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery):  # enrichment-only adapter
        raise NotImplementedError("sam_mirrors is consumed via category_sweep")

    def category_sweep(self, query: SourceQuery) -> dict[str, Any]:
        terms = [t for t in (query.keywords or []) if str(t).strip()][:MAX_TERMS]
        sites: list[dict] = []
        records: list[dict] = []
        errors: list[str] = []
        for mirror in MIRRORS:
            url = mirror["search"].format(
                terms=quote_plus(" ".join(terms))) if terms else mirror["home"]
            row: dict[str, Any] = {
                "mirror": mirror["id"],
                "label": mirror["label"],
                "roles": list(mirror["roles"]),
                "access": mirror["access"],
                "url_queried": url,
                "terms_queried": terms,
                "retrieved_at": _now(),
            }
            try:
                html = get_text(url, timeout=FETCH_TIMEOUT_S, retries=1)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                row["ok"] = False
                row["http_status"] = status
                errors.append(f"{mirror['id']}: HTTP {status}")
                sites.append(row)
                continue
            except httpx.HTTPError as exc:
                row["ok"] = False
                row["error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"{mirror['id']}: {type(exc).__name__}")
                sites.append(row)
                continue
            text = _visible_text(html)
            row["ok"] = True
            per_term = {t: _occurrences(text, t) for t in terms}
            row["term_occurrences"] = per_term
            matched_titles = [
                a for a in _anchor_texts(html)
                if any(_occurrences(a, t) for t in terms)
            ]
            row.update(cap_disclosed(matched_titles, MAX_SAMPLE_TITLES,
                                     key="sample_titles"))
            sites.append(row)
            for term, count in per_term.items():
                if count:
                    records.append({
                        "record_id": f"sam-mirror:{mirror['id']}:{term}",
                        "mirror": mirror["id"],
                        "term": term,
                        "occurrences": count,
                        "url_queried": url,
                        "retrieved_at": row["retrieved_at"],
                    })
        payload: dict[str, Any] = {
            "records": records,
            "sites": sites,
            "note": SAM_CLIENT_SIDE_NOTE,
            "source_url": MIRRORS[0]["home"],
        }
        if errors:
            payload["errors"] = errors
        return payload
