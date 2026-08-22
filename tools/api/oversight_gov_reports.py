"""Scoped federal Inspector General report search on Oversight.gov.

Oversight.gov exposes no public JSON API or export.  Its official Drupal view
is therefore parsed conservatively, with a small server-side query and no
follow-up crawl.  Reports are oversight context, not proof that a supplier is
nonresponsible or that an agency will buy a remedy.
"""

from __future__ import annotations

import hashlib
import html
import re
from typing import Any, Optional
from urllib.parse import urlencode

from tools.api._accepted_wave import (FetchBytes, fetch_bytes, payload,
                                      receipt)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source


SOURCE_NAME = "oversight_gov_reports"
MODE = "live_oversight_gov_scoped_html"
BASE_URL = "https://www.oversight.gov"
SEARCH_URL = f"{BASE_URL}/reports/federal"
_TAG = re.compile(r"<[^>]+>")
_ROW_SPLIT = re.compile(r'(?=<tr class="listing-table__row table-row")')
MAX_QUERY_TERMS = 4
MAX_QUERY_CHARS = 180


def _text(fragment: Any) -> Optional[str]:
    cleaned = html.unescape(_TAG.sub(" ", str(fragment or "")))
    cleaned = " ".join(cleaned.split())
    return cleaned or None


def _cell(segment: str, label: str) -> Optional[str]:
    match = re.search(
        rf'<td\b[^>]*data-label="{re.escape(label)}"[^>]*>(.*?)</td>',
        segment, re.I | re.S,
    )
    return _text(match.group(1)) if match else None


def _highlight(segment: str, field_class: str) -> Optional[str]:
    match = re.search(
        rf'field--name-{re.escape(field_class)}(?:\s|\").*?'
        rf'<div class="field__item[^>]*>(.*?)</div>',
        segment, re.I | re.S,
    )
    return _text(match.group(1)) if match else None


def _money(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    digits = re.sub(r"[^0-9-]", "", value)
    try:
        return int(digits)
    except ValueError:
        return None


def _stable_id(number: Optional[str], url: str) -> str:
    if number:
        safe = re.sub(r"[^A-Z0-9.-]+", "-", number.upper()).strip("-")
        if safe:
            return f"oversight:{safe}"
    return "oversight:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _parse_reports(doc: str) -> tuple[list[dict[str, Any]], int]:
    reports: list[dict[str, Any]] = []
    dropped = 0
    for segment in _ROW_SPLIT.split(doc):
        if not segment.startswith('<tr class="listing-table__row table-row'):
            continue
        href_match = re.search(
            r'<a\b[^>]*href="(/reports/[^"]+)"[^>]*>\s*View Report',
            segment, re.I | re.S,
        )
        title = _cell(segment, "Report Title")
        if not href_match or not title:
            dropped += 1
            continue
        url = BASE_URL + html.unescape(href_match.group(1))
        number = _highlight(segment, "field-report-number")
        issued_match = re.search(r'<time\b[^>]*datetime="([^"]+)"', segment, re.I)
        report = {
            "record_id": _stable_id(number, url),
            "title": title,
            "report_number": number,
            "date_issued": issued_match.group(1) if issued_match else None,
            "agency_reviewed": _cell(segment, "Agency Reviewed / Investigated"),
            "report_type": _cell(segment, "Type"),
            "location": _cell(segment, "Location"),
            "submitting_oig": _highlight(segment, "field-report-submitting-oig"),
            "description": _highlight(segment, "body"),
            "recommendation_count": _money(_highlight(
                segment, "field-report-number-of-recs")),
            "net_questioned_costs": _money(_highlight(
                segment, "field-net-questioned-costs")),
            "funds_for_better_use": _money(_highlight(
                segment, "field-net-funds-for-better-use")),
            "citation_url": url,
            "_join_keys": {
                "agency_name": _cell(segment, "Agency Reviewed / Investigated"),
                "report_number": number,
            },
            "semantics": (
                "Inspector General report context; not a procurement opportunity "
                "or automatic supplier-responsibility determination"
            ),
        }
        reports.append(report)
    total_match = re.search(r"Displaying\s+\d+\s*-\s*\d+\s+of\s+([\d,]+)", doc)
    total = int(total_match.group(1).replace(",", "")) if total_match else len(reports)
    return reports, dropped if reports or not total else dropped + total


def _query_params(query: SourceQuery) -> tuple[dict[str, Any], bool]:
    candidates = [str(value).strip().strip('"')
                  for value in (*query.keywords, *query.agencies)
                  if str(value).strip()]
    terms: list[str] = []
    seen: set[str] = set()
    for term in candidates:
        key = term.casefold()
        proposed = " ".join([*terms, term])
        if key in seen:
            continue
        if len(terms) >= MAX_QUERY_TERMS or len(proposed) > MAX_QUERY_CHARS:
            continue
        seen.add(key)
        terms.append(term)
    capped = len(seen) < len({term.casefold() for term in candidates})
    result: dict[str, Any] = {
        "search_api_fulltext": " ".join(terms),
        "items_per_page": max(10, min(query.limit, 50)),
    }
    if query.posted_from:
        result["field_report_date_issued[min]"] = query.posted_from.isoformat()
    if query.posted_to:
        result["field_report_date_issued[max]"] = query.posted_to.isoformat()
    return result, capped


class OversightGovReportsSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetcher: Optional[FetchBytes] = None) -> None:
        self._fetcher = fetcher or fetch_bytes

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use reports()")

    def healthcheck(self) -> tuple[bool, str]:
        params = {"search_api_fulltext": "information technology",
                  "items_per_page": 10}
        url = SEARCH_URL + "?" + urlencode(params)
        try:
            blob, _headers = self._fetcher(url, timeout=15.0, retries=1)
            text = blob.decode("utf-8", errors="replace")
            if "listing-table" in text and "Displaying" in text:
                return True, "official scoped federal OIG report search reachable"
            return False, "Oversight.gov returned an unexpected search page"
        except Exception as exc:  # noqa: BLE001
            return False, f"Oversight.gov unavailable: {exc}"

    def reports(self, query: SourceQuery) -> dict[str, Any]:
        if not query.keywords and not query.agencies:
            return payload(
                source=self.name, mode=MODE, items=[], status="partial",
                limitations=["unscoped report crawl refused; pass capability or agency terms"],
            )
        params, terms_capped = _query_params(query)
        url = SEARCH_URL + "?" + urlencode(params)
        try:
            blob, headers = self._fetcher(url, timeout=30.0, retries=2)
            reports, dropped = _parse_reports(blob.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            return payload(
                source=self.name, mode=MODE, items=[], status="failed",
                limitations=[f"Oversight.gov retrieval or parse failed: {exc}"],
            )
        limit = max(1, min(query.limit, 50))
        total = len(reports)
        items = reports[:limit]
        limitations = [
            "HTML is the only public search surface; Drupal markup drift is a standing risk",
            "one bounded server-side result page is read; report PDFs and recommendation nodes are not crawled",
            "IG findings are oversight context, not proof of future procurement or supplier nonresponsibility",
        ]
        if terms_capped:
            limitations.append(
                f"bounded search used at most {MAX_QUERY_TERMS} terms and "
                f"{MAX_QUERY_CHARS} query characters")
        if dropped:
            limitations.append(f"ignored {dropped} malformed listing rows")
        if total > limit:
            limitations.append(f"kept first {limit} of {total} returned rows")
        source_receipt = receipt(
            source=self.name, url=url, query=params, blob=blob,
            normalized=items, headers=headers, content_kind="text/html",
        )
        return payload(
            source=self.name, mode=MODE, items=items,
            status=("partial" if terms_capped or dropped or total > limit
                    else "complete"),
            limitations=limitations, source_receipt=source_receipt,
            total_matched=total,
            public_detail="Official Oversight.gov scoped federal OIG report search",
        )


try:
    register_source(OversightGovReportsSource)
except ValueError:
    pass
