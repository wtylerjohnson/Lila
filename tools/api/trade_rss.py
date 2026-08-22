"""Trade-press RSS adapter — the federal news layer (live, no key required).

Feeds the capture brief's "Federal News Assessment" section automatically: the
same outlets the manual workflow proved out (IC News surfaced both the STAR RFI
and four news items in one day). Items are keyword-filtered against the client's
strategy lanes; every item carries its link and publish date, which the report
layer converts to recency labels.

Stdlib XML parsing on purpose — no feedparser dependency to install.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.text_match import matching_phrases

FEEDS = {
    "Intelligence Community News": "https://intelligencecommunitynews.com/feed/",
    "CyberScoop": "https://cyberscoop.com/feed/",
    "DefenseScoop": "https://defensescoop.com/feed/",
    "Federal News Network": "https://federalnewsnetwork.com/feed/",
}


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
        desc = (item.findtext("description") or "").strip()
        if title and link:
            items.append({
                "title": title, "url": link, "published": pub,
                "summary": desc[:300], "source": source,
            })
    return items


def _matches(item: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = item["title"] + " " + item.get("summary", "")
    return bool(matching_phrases(hay, keywords))


@register_source
class TradeRssSource(DataSource):
    name = "trade_rss"
    kind = SourceKind.ENRICHMENT


    def healthcheck(self) -> tuple[bool, str]:
        up, down = 0, []
        for source, url in FEEDS.items():
            try:
                get_text(url, timeout=5.0, retries=1)
                up += 1
            except Exception:  # noqa: BLE001
                down.append(source)
        if up == 0:
            return False, "all feeds unreachable"
        note = f"{up}/{len(FEEDS)} feeds up" + (f" (down: {', '.join(down)})" if down else "")
        return True, note

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")
    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Return {'items': [...]} — keyword-matched news across the feed set.

        A dead feed never kills the sweep: errors are recorded per-feed and the
        rest proceed. The report layer needs links above all; items without a
        URL never leave this adapter.
        """
        items: list[dict] = []
        errors: dict[str, str] = {}
        for source, url in FEEDS.items():
            try:
                parsed = _parse_items(get_text(url), source)
            except Exception as e:  # noqa: BLE001 — isolate per-feed failures
                errors[source] = str(e)
                continue
            items.extend(i for i in parsed if _matches(i, query.keywords))
        return {**cap_disclosed(items, 40), "errors": errors}
