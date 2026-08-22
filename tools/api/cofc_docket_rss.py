"""Court of Federal Claims recent-docket RSS adapter.

Entries are litigation-timing risk signals.  Caption-name joins to a vendor or
GAO protester are explicitly inference-grade and never become opportunity or
award assertions.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

from tools.api._connector_wave3 import failure, matches, result, stable_id, text
from tools.api._http import get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "cofc_docket_rss"
FEED_URL = "https://ecf.cofc.uscourts.gov/cgi-bin/rss_outside.pl"
CASE_RE = re.compile(r"^(\d:\d{2}-(cv|vv)-\d{5})\s+(.+)$", re.I)
ENTRY_RE = re.compile(r"^\[([^]]+)]")
MAX_ITEMS = 200


def _vendor_key(caption: str) -> Optional[str]:
    left = re.split(r"\s+v\.?\s+", caption, maxsplit=1, flags=re.I)[0]
    left = re.sub(r"\b(?:INCORPORATED|INC|LLC|L\.L\.C|CORPORATION|CORP)\b", "", left, flags=re.I)
    value = re.sub(r"[^A-Z0-9]+", " ", left.upper()).strip()
    return value or None


def _parse_feed(blob: bytes) -> tuple[list[dict[str, Any]], Optional[str]]:
    root = ET.fromstring(blob)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("COFC RSS has no channel")
    built = text(channel.findtext("lastBuildDate"))
    rows: list[dict[str, Any]] = []
    for item in channel.findall("item")[:MAX_ITEMS]:
        title = text(item.findtext("title")) or ""
        found = CASE_RE.match(title)
        if not found or found.group(2).casefold() != "cv":
            continue
        case_number, _, caption = found.groups()
        # Civil cases against the United States are the relevant acquisition
        # litigation subset; vaccine (vv) traffic is intentionally excluded.
        if not re.search(r"\bv\.?\s+USA(?:/|\b)", caption, re.I):
            continue
        description = text(item.findtext("description")) or ""
        entry = ENTRY_RE.match(description)
        guid = text(item.findtext("guid"))
        published = text(item.findtext("pubDate"))
        try:
            published_iso = parsedate_to_datetime(published).isoformat() if published else None
        except (TypeError, ValueError):
            published_iso = published
        rows.append({
            "record_id": stable_id(SOURCE_NAME, guid or case_number, published, description),
            "case_number": case_number,
            "caption": caption,
            "entry_type": text(entry.group(1)) if entry else None,
            "published_at": published_iso,
            "docket_report_url": text(item.findtext("link")),
            "guid": guid,
            "vendor_name_inference_key": _vendor_key(caption),
            "_join_keys": {"court_case_number": case_number,
                           "vendor_name_inference": _vendor_key(caption)},
            "evidence_semantics": "litigation_timing_risk",
        })
    return rows, built


class CofcDocketRssSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch_text: Optional[Callable[..., str]] = None) -> None:
        self._fetch_text = fetch_text or get_text

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use litigation_events()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            rows, _ = _parse_feed(self._fetch_text(
                FEED_URL, timeout=10.0, retries=1).encode("iso-8859-1"))
            return True, f"official COFC recent-entry RSS reachable ({len(rows)} civil USA entries)"
        except Exception as exc:  # noqa: BLE001
            return False, f"COFC RSS unavailable: {exc}"

    def litigation_events(self, query: SourceQuery) -> dict[str, Any]:
        try:
            raw_text = self._fetch_text(FEED_URL, timeout=20.0, retries=2)
            blob = raw_text.encode("iso-8859-1", errors="replace")
            native, built = _parse_feed(blob)
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, mode="live_official_rss", url=FEED_URL,
                           error=exc)
        terms = [*query.keywords, *query.agencies]
        matched = [row for row in native if matches(row, terms)] if terms else native
        cap = max(1, min(query.limit, MAX_ITEMS))
        kept = matched[:cap]
        limitations = [
            "entries are litigation timing-risk telemetry, not live opportunities or award facts",
            "the public feed is short-retention; absence does not prove no litigation",
            "PACER-linked docket reports may require an account and fees and are not fetched",
            "caption-name joins are inference-grade; the feed carries no PIID, UEI, or solicitation number",
        ]
        if len(matched) > cap:
            limitations.append(f"kept first {cap} of {len(matched)} feed matches")
        return result(
            self.name, items=kept, total_matched=len(matched),
            status="partial" if len(matched) > cap else "complete",
            mode="live_official_rss_civil_usa_scope", limitations=limitations,
            url=FEED_URL, query={"case_type": "cv", "defendant": "USA", "terms": terms},
            raw=blob, content_kind="application/rss+xml", data_as_of=built,
        )


try:
    register_source(CofcDocketRssSource)
except ValueError:
    pass
