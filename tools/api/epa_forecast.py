"""EPA Acquisition Forecast public Oracle APEX adapter.

The adapter retrieves only the first publisher-bounded page from the Current
and Canceled views and says so.  Forecast and cancellation rows are planning
intent; they are deliberately kept out of RawOpportunity discovery.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urljoin

from tools.api import _http
from tools.api._connector_wave3 import failure, matches, result, scoped_terms, text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "epa_forecast"
BASE_URL = "https://ordspub.epa.gov/ords/forecast/"
LANDING_URL = BASE_URL + "f?p=122:1"
CURRENT_PAGE = 2
CANCELED_PAGE = 20
MAX_RETURNED = 100
_SESSION_RE = re.compile(r"f\?p=122:(?:10|12|13):(\d+)")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = self.in_row = self.in_cell = False
        self.header_cell = False
        self.headers: list[str] = []
        self.row: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.parts: list[str] = []
        self.href: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_d = dict(attrs)
        if tag == "table" and "a-IRR-table" in (attrs_d.get("class") or ""):
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row, self.row = True, []
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell, self.header_cell, self.parts = True, tag == "th", []
            self.href = None
        elif self.in_cell and tag == "a":
            self.href = attrs_d.get("href")

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag in {"th", "td"}:
            value = text(html.unescape(" ".join(self.parts))) or ""
            if self.href and not self.header_cell:
                value = f"{value}|href={html.unescape(self.href)}"
            self.row.append(value)
            self.in_cell = False
        elif self.in_row and tag == "tr":
            if not self.headers and self.row:
                self.headers = self.row
            elif self.headers and self.row:
                self.rows.append(dict(zip(self.headers, self.row)))
            self.in_row = False
        elif self.in_table and tag == "table":
            self.in_table = False


def _parse_page(page_html: str, status: str) -> tuple[list[dict[str, Any]], bool]:
    parser = _TableParser()
    parser.feed(page_html)
    out: list[dict[str, Any]] = []
    for native in parser.rows:
        record_number = text(native.get("Record Number"))
        description = text(native.get("Description"))
        if not record_number or not description:
            continue
        view = native.get("View Record", "")
        href = view.split("|href=", 1)[1] if "|href=" in view else None
        out.append({
            "record_id": f"epa-forecast:{re.sub(r'[^A-Z0-9]+', '-', record_number.upper()).strip('-')}",
            "record_number": record_number,
            "description": description,
            "naics_code": text(native.get("NAICS Code")),
            "procurement_method": text(native.get("Procurement Method")),
            "estimated_value_band": text(native.get("Estimated Dollars")),
            "target_award_year": text(native.get("Target Award Year")),
            "target_award_quarter": text(native.get("Target Award Quarter")),
            "place_of_performance": text(native.get("Place Of Performance")),
            "forecast_status": status,
            "session_detail_url": urljoin(BASE_URL, href) if href else None,
            "_join_keys": {"epa_forecast_record_number": record_number,
                           "naics": text(native.get("NAICS Code"))},
            "evidence_semantics": "planning_intent_not_live_opportunity",
        })
    return out, "data-pagination=" in page_html


def _live_pages() -> dict[str, str]:
    with _http._client(30.0) as client:
        landing = client.get(LANDING_URL)
        landing.raise_for_status()
        found = _SESSION_RE.search(landing.text)
        if not found:
            raise ValueError("EPA APEX session id absent from landing page")
        session = found.group(1)
        pages: dict[str, str] = {}
        for status, page in (("current", CURRENT_PAGE), ("canceled", CANCELED_PAGE)):
            response = client.get(BASE_URL + f"f?p=122:{page}:{session}:::{page}::")
            response.raise_for_status()
            pages[status] = response.text
        return pages


class EpaForecastSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch_pages: Optional[Callable[[], dict[str, str]]] = None) -> None:
        self._fetch_pages = fetch_pages or _live_pages

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("forecast evidence is enrichment-only; use forecast_records()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            pages = self._fetch_pages()
            rows = sum((len(_parse_page(body, status)[0]) for status, body in pages.items()), 0)
            return rows > 0, f"EPA public forecast APEX reachable ({rows} bounded rows parsed)"
        except Exception as exc:  # noqa: BLE001
            return False, f"EPA forecast unavailable: {exc}"

    def forecast_records(self, query: SourceQuery) -> dict[str, Any]:
        terms = scoped_terms(query)
        if not terms:
            return result(
                self.name, items=[], status="partial", mode="refused_unscoped",
                url=LANDING_URL,
                limitations=["unscoped APEX pull refused; pass keywords, NAICS, or EPA office terms"],
            )
        try:
            pages = self._fetch_pages()
            native: list[dict[str, Any]] = []
            paged = False
            for status, body in pages.items():
                rows, has_more = _parse_page(body, status)
                native.extend(rows)
                paged = paged or has_more
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, mode="live_public_apex_bounded", url=LANDING_URL,
                           error=exc)
        matched = [row for row in native if matches(row, terms)]
        cap = max(1, min(query.limit, MAX_RETURNED))
        kept = matched[:cap]
        limitations = [
            "EPA forecast rows and cancellations are planning evidence, not live solicitations",
            "session-checksummed detail URLs are ephemeral and must not be persisted as durable citations",
            "only the first publisher-bounded page of each current/canceled view was inspected",
        ]
        if paged:
            limitations.append("APEX reported additional pages; results are explicitly partial")
        status = "partial" if paged or len(matched) > cap else "complete"
        return result(
            self.name, items=kept, total_matched=len(matched), status=status,
            mode="live_public_apex_first_page_scoped_local_filter",
            limitations=limitations, url=LANDING_URL,
            query={"terms": terms, "limit": cap, "views": ["current", "canceled"]},
            receipt_payload=pages,
            content_kind="application/json; profile=canonical-apex-pages",
        )


try:
    register_source(EpaForecastSource)
except ValueError:
    pass
