"""Vendor and press sources swept on every run (operator order, 2026-08-18).

Trade press already rides trade_rss and watchdog_rss. This adapter adds the
vendor newsroom lane: the roster at data/reference/vendor_press_sources.json
(env override LILA_VENDOR_PRESS_ROSTER for hermetic tests) names each
vendor's press page, and every sweep reads each page for federal-market
headlines and for the sweep's own category terms. Roster paths churn; a 404
is receipted per site, never fatal and never silent.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from tools.api._http import get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, cap_disclosed, register_source

SOURCE_NAME = "vendor_press"
FETCH_TIMEOUT_S = 12.0
MAX_TERMS = 8
MAX_RECORDS = 40
ROSTER_PATH = (Path(__file__).resolve().parents[2]
               / "data" / "reference" / "vendor_press_sources.json")

#: Headlines qualify on a federal marker or on a sweep category term.
FEDERAL_MARKERS = (
    "federal", "government", "dod", "department of defense", "pentagon",
    "fedramp", "govcloud", "public sector", "agency", "contract award",
    "blanket purchase", "gsa", "sewp", "task order",
)

_TAG_STRIP = re.compile(r"<(?:script|style)\b.*?</(?:script|style)>",
                        re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_HEADLINE = re.compile(
    r"<(?:a|h1|h2|h3)\b[^>]*>(.*?)</(?:a|h1|h2|h3)>", re.I | re.S)


def roster_path() -> Path:
    override = os.environ.get("LILA_VENDOR_PRESS_ROSTER")
    return Path(override) if override else ROSTER_PATH


def load_roster() -> list[dict]:
    try:
        data = json.loads(roster_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [e for e in (entries or []) if isinstance(e, dict)
            and str(e.get("vendor") or "").strip()
            and str(e.get("url") or "").startswith("http")]


def _headline_texts(html: str) -> list[str]:
    out = []
    for raw in _HEADLINE.findall(html):
        text = " ".join(_TAGS.sub(" ", raw).split())
        if 12 <= len(text) <= 200:
            out.append(text)
    return out


def _matches(text: str, terms: list[str]) -> bool:
    low = text.casefold()
    if any(marker in low for marker in FEDERAL_MARKERS):
        return True
    for term in terms:
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(term)
                     + r"(?![A-Za-z0-9])", text, re.I):
            return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@register_source
class VendorPress(DataSource):
    """One bounded newsroom read per rostered vendor per sweep."""

    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery):  # enrichment-only adapter
        raise NotImplementedError("vendor_press is consumed via press_items")

    def press_items(self, query: SourceQuery) -> dict[str, Any]:
        terms = [t for t in (query.keywords or []) if str(t).strip()][:MAX_TERMS]
        roster = load_roster()
        sites: list[dict] = []
        matched: list[dict] = []
        errors: list[str] = []
        for entry in roster:
            row: dict[str, Any] = {
                "vendor": entry["vendor"],
                "url_queried": entry["url"],
                "path_verified": bool(entry.get("path_verified")),
                "retrieved_at": _now(),
            }
            try:
                html = get_text(entry["url"], timeout=FETCH_TIMEOUT_S,
                                retries=1)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                row["ok"] = False
                row["http_status"] = status
                errors.append(f"{entry['vendor']}: HTTP {status}")
                sites.append(row)
                continue
            except httpx.HTTPError as exc:
                row["ok"] = False
                row["error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"{entry['vendor']}: {type(exc).__name__}")
                sites.append(row)
                continue
            row["ok"] = True
            headlines = [h for h in _headline_texts(html)
                         if _matches(h, terms)]
            row["headlines_matched"] = len(headlines)
            sites.append(row)
            for headline in headlines:
                matched.append({
                    "record_id": f"vendor-press:{entry['vendor']}:"
                                 f"{abs(hash(headline)) % 10**10}",
                    "vendor": entry["vendor"],
                    "headline": headline,
                    "url_page": entry["url"],
                    "retrieved_at": row["retrieved_at"],
                })
        payload: dict[str, Any] = cap_disclosed(matched, MAX_RECORDS,
                                                key="records")
        payload["sites"] = sites
        payload["roster_size"] = len(roster)
        payload["source_url"] = str(roster_path())
        if not roster:
            payload["errors"] = ["vendor press roster missing or empty"]
        elif errors:
            payload["errors"] = errors
        return payload
