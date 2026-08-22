"""GAO Legal Products adapter — bid-protest + Comptroller decisions (keyless).

Feed verified 2026-07-03: https://www.gao.gov/rss/reportslegal.xml carries
summaries of Comptroller General decisions and opinions, including bid
protests. A protest on a contract in the client's lane is an incumbency-
weakness signal: the award is contested, the agency may re-evaluate, and the
timeline just moved.

Same stdlib RSS parsing as the trade-press adapter — no new dependencies.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.text_match import matching_phrases

FEED_URL = "https://www.gao.gov/rss/reportslegal.xml"


def _parse_items(xml_text: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    root_name = str(root.tag).rsplit("}", 1)[-1].casefold()
    has_channel = any(
        str(child.tag).rsplit("}", 1)[-1].casefold() == "channel"
        for child in root
    )
    if root_name != "rss" or not has_channel:
        raise ValueError("GAO legal response was not an RSS feed")
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title and link:
            items.append({
                "title": title, "url": link,
                "published": (item.findtext("pubDate") or "").strip(),
                "summary": (item.findtext("description") or "").strip()[:300],
            })
    return items


@register_source
class GaoLegalSource(DataSource):
    name = "gao_legal"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            get_text(FEED_URL, timeout=8.0, retries=1)
            return True, "gao.gov legal feed reachable (keyless)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Recent legal decisions, keyword-matched; 'protest' flagged explicitly."""
        items = _parse_items(get_text(FEED_URL))
        kws = [k.strip('"') for k in (query.keywords or []) if k.strip()]
        out = []
        for it in items:
            hay = (it["title"] + " " + it["summary"]).lower()
            matched = matching_phrases(hay, kws)
            is_protest = "protest" in hay or it["title"].lower().startswith("b-")
            if matched or (is_protest and not kws):
                out.append({**it, "matched": matched[:4], "protest": is_protest})
        return {**cap_disclosed(out, 15), "feed_total": len(items)}
