"""Defense.gov daily contract announcements — the DoD award wire (no key).

Every business day DoD publishes contract awards over the announcement
threshold: awardee, dollars, work description, place of performance, and the
awarding component, in prose. As a horizon feed it answers two questions the
notice stream can't: who is WINNING work in the client's lanes right now
(teaming targets with fresh dollars), and which primes' wins name work the
client could sub on.

RSS verified live 2026-07-09 (text/xml, RSS 2.0). Stdlib parsing, same
pattern as trade_rss. Keyword filtering happens here (announcements are
verbose); each kept item carries its defense.gov link for citation.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.text_match import matching_phrases

FEED_URL = ("https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx"
            "?ContentType=9&Site=945&max=30")

_TAG = re.compile(r"<[^>]+>")


def _parse_items(xml_text: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    root_name = str(root.tag).rsplit("}", 1)[-1].casefold()
    has_channel = any(
        str(child.tag).rsplit("}", 1)[-1].casefold() == "channel"
        for child in root
    )
    if root_name != "rss" or not has_channel:
        raise ValueError("Defense.gov response was not an RSS feed")
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = _TAG.sub(" ", item.findtext("description") or "").strip()
        if title and link:
            items.append({"title": title, "url": link, "published": pub,
                          "body": desc, "source": "defense.gov contracts"})
    return items


def _matched_terms(item: dict, keywords: list[str]) -> list[str]:
    hay = item["title"] + " " + item.get("body", "")
    return matching_phrases(hay, keywords)


@register_source
class DodContractsSource(DataSource):
    name = "dod_contracts"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            n = len(_parse_items(get_text(FEED_URL, timeout=10.0, retries=1)))
            return bool(n), f"defense.gov contracts feed up ({n} announcements)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """{'items': [...], 'total_announcements': N} — announcements whose
        text hits the client's keywords, each with its matched terms recorded
        (the WHY survives to the fact bank). Body text is truncated for the
        artifact; the link carries the full announcement."""
        try:
            parsed = _parse_items(get_text(FEED_URL))
        except Exception as e:  # noqa: BLE001 — a dead feed never kills a sweep
            return {"items": [], "total_announcements": 0, "error": str(e)}
        items = []
        for i in parsed:
            hits = _matched_terms(i, query.keywords)
            if hits:
                items.append({**i, "body": i["body"][:400], "matched": hits})
        return {**cap_disclosed(items, 20),
                "total_announcements": len(parsed)}
