"""Watchdog reports — GAO + every agency Inspector General (RSS, no key).

Compelled-demand signals: a GAO finding or an IG report criticizing an
agency's current capability forces a documented response, and procurement
often follows. Oversight.gov aggregates ALL federal IG reports; GAO publishes
its full report stream. Both verified live 2026-07-09.

Same pattern as the DoD award wire: stdlib RSS parse, capability-keyword
filter with the matched terms recorded, per-feed failure isolation.
"""

from __future__ import annotations

import re
import hashlib
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from typing import Any

from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.text_match import matching_phrases

FEEDS = {
    "GAO": "https://www.gao.gov/rss/reports.xml",
    "Oversight.gov (IG)": "https://www.oversight.gov/rss.xml",
}

_TAG = re.compile(r"<[^>]+>")


def _parse_items(xml_text: str, source: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    root_name = str(root.tag).rsplit("}", 1)[-1].casefold()
    has_channel = any(
        str(child.tag).rsplit("}", 1)[-1].casefold() == "channel"
        for child in root
    )
    if root_name != "rss" or not has_channel:
        raise ValueError(f"{source} response was not an RSS feed")
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = _TAG.sub(" ", item.findtext("description") or "").strip()
        if title and link:
            items.append({"title": title, "url": link, "published": pub,
                          "body": desc, "source": source})
    return items


def _matched_terms(item: dict, keywords: list[str]) -> list[str]:
    hay = item["title"] + " " + item.get("body", "")
    return matching_phrases(hay, keywords)


@register_source
class WatchdogRssSource(DataSource):
    name = "watchdogs"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        up, down = 0, []
        for source, url in FEEDS.items():
            try:
                get_text(url, timeout=8.0, retries=1)
                up += 1
            except Exception:  # noqa: BLE001
                down.append(source)
        if not up:
            return False, "all watchdog feeds unreachable"
        return True, (f"{up}/{len(FEEDS)} feeds up"
                      + (f" (down: {', '.join(down)})" if down else ""))

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """{'items': [...], 'total_reports': N} — GAO/IG reports whose text
        hits the client's capability terms, matched terms recorded (the WHY
        survives to the fact bank). A dead feed never kills a sweep."""
        items: list[dict] = []
        total = 0
        errors: dict[str, str] = {}
        inventory, attempts = [], []
        retrieved_at = datetime.now(timezone.utc).isoformat()
        for source, url in FEEDS.items():
            try:
                parsed = _parse_items(get_text(url), source)
            except Exception as e:  # noqa: BLE001 — isolate per-feed failures
                errors[source] = str(e)
                attempts.append({'source': source, 'url': url, 'status': 'failed', 'error': str(e), 'retrieved_at': retrieved_at})
                continue
            attempts.append({'source': source, 'url': url, 'status': 'success', 'parsed': len(parsed), 'retrieved_at': retrieved_at})
            total += len(parsed)
            for i in parsed:
                i = {**i, 'retrieved_at': retrieved_at,
                     'content_sha256': hashlib.sha256((i['title'] + '\n' + i['body']).encode()).hexdigest()}
                inventory.append(i)
                hits = _matched_terms(i, query.keywords)
                if hits:
                    items.append({**i, "matched": hits})
        return {**cap_disclosed(items, 20), "total_reports": total,
                "errors": errors, "parsed_inventory": inventory,
                "source_attempts": attempts, "retrieved_at": retrieved_at,
                "coverage_boundary": "Current RSS inventory only; no complete archive or agency/component census",
                "screened_records": total, "matched_before_cap": len(items)}
