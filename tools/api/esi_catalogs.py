"""DoD ESI agreement coverage via reseller catalogs (operator order,
2026-08-18).

esi.mil itself is behind an F5/TSPD challenge: a scripted fetch receives the
challenge stub and a real browser received 'The requested URL was rejected'
with support id 6974556345028838376, measured 2026-08-18. The reseller
catalog pages carry the same agreement facts, and when a catalog is walled
the ESI agreement rides its USASpending IDV award record instead (the
Varonis BPA N6600122A0080, PoP end 2032-09-04 via EC America, was confirmed
that way). Every sweep attempts every catalog and receipts what happened.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from tools.api._http import get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "esi_reseller_catalogs"
FETCH_TIMEOUT_S = 12.0
MAX_TERMS = 12

#: Known ESI carrier catalogs. BPA ids are measured facts with sources, not
#: guesses: Carahsoft holds N66001-21-A-0082, N66001-21-A-0031 and
#: N66001-19-A-0120 (carahsoft.com/news, 2026-08-18); FCN and EC America
#: carry the Varonis ESI BPA N6600122A0080 (USASpending award record).
CATALOGS: tuple[dict, ...] = (
    {
        "id": "esi_mil",
        "label": "DoD ESI official agreements",
        "url": "https://www.esi.mil/",
        "access": "F5/TSPD challenge to scripted fetch; in-browser rejection "
                  "with support id 6974556345028838376 (2026-08-18)",
    },
    {
        "id": "carahsoft_esi",
        "label": "Carahsoft contract vehicles (DoD ESI BPAs)",
        "url": "https://www.carahsoft.com/buy/contract-vehicles",
        "access": "public storefront; ESI deep links churn, receipts carry "
                  "the fetched reality",
    },
    {
        "id": "immixgroup_esi",
        "label": "immixGroup / EC America contract vehicles",
        "url": "https://www.immixgroup.com/contract-vehicles/",
        "access": "bot-walled: scripted fetch answered HTTP 403 (2026-08-18)",
    },
    {
        "id": "fcn_esi",
        "label": "FCN Inc contract vehicles",
        "url": "https://fcnit.com/",
        "access": "public; ESI page path unconfirmed, receipts carry the "
                  "fetched reality",
    },
)

USASPENDING_IDV_FALLBACK = (
    "When a catalog is walled, ESI agreement facts ride USASpending IDV "
    "award records (award detail endpoint, generated id "
    "CONT_IDV_<piid>_<agency>)."
)

_TAG_STRIP = re.compile(r"<(?:script|style)\b.*?</(?:script|style)>",
                        re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _visible_text(html: str) -> str:
    return " ".join(_TAGS.sub(" ", _TAG_STRIP.sub(" ", html)).split())


def _occurrences(text: str, term: str) -> int:
    pattern = re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I)
    return len(pattern.findall(text))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@register_source
class EsiResellerCatalogs(DataSource):
    """One bounded read per ESI carrier catalog per sweep."""

    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery):  # enrichment-only adapter
        raise NotImplementedError(
            "esi_reseller_catalogs is consumed via agreements")

    def agreements(self, query: SourceQuery) -> dict[str, Any]:
        terms = [t for t in (query.keywords or [])
                 if str(t).strip()][:MAX_TERMS]
        catalogs: list[dict] = []
        records: list[dict] = []
        errors: list[str] = []
        for catalog in CATALOGS:
            row: dict[str, Any] = {
                "catalog": catalog["id"],
                "label": catalog["label"],
                "access": catalog["access"],
                "url_queried": catalog["url"],
                "terms_queried": terms,
                "retrieved_at": _now(),
            }
            try:
                html = get_text(catalog["url"], timeout=FETCH_TIMEOUT_S,
                                retries=1)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                row["ok"] = False
                row["http_status"] = status
                errors.append(f"{catalog['id']}: HTTP {status}")
                catalogs.append(row)
                continue
            except httpx.HTTPError as exc:
                row["ok"] = False
                row["error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"{catalog['id']}: {type(exc).__name__}")
                catalogs.append(row)
                continue
            if "TSPD" in html or "Request Rejected" in html:
                row["ok"] = False
                row["error"] = "challenge-walled: F5/TSPD stub served"
                errors.append(f"{catalog['id']}: challenge-walled")
                catalogs.append(row)
                continue
            text = _visible_text(html)
            row["ok"] = True
            per_term = {t: _occurrences(text, t) for t in terms}
            row["term_occurrences"] = per_term
            catalogs.append(row)
            for term, count in per_term.items():
                if count:
                    records.append({
                        "record_id": f"esi-catalog:{catalog['id']}:{term}",
                        "catalog": catalog["id"],
                        "term": term,
                        "occurrences": count,
                        "url_queried": catalog["url"],
                        "retrieved_at": row["retrieved_at"],
                    })
        payload: dict[str, Any] = {
            "records": records,
            "catalogs": catalogs,
            "fallback_note": USASPENDING_IDV_FALLBACK,
            "source_url": CATALOGS[0]["url"],
        }
        if errors:
            payload["errors"] = errors
        return payload
