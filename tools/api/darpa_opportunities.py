"""DARPA Opportunities RSS adapter (PROGRAM-tier research signals).

The feed mixes programs, proposers days, SBIR topics, shopping notices, and
other agency engagement. Those are valuable forming signals, but this adapter
never promotes an RSS item to a live solicitation.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.api._http import get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.program_cache import ProgramPull, cached_program_pull
from tools.text_match import matching_phrases

FEED_URL = "https://www.darpa.mil/rss/opportunities.xml"
INDEX_URL = "https://www.darpa.mil/work-with-us/opportunities"
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "darpa"


class _DescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.official_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        host = (urlparse(href).hostname or "").lower()
        if host == "darpa.mil" or host.endswith(".darpa.mil"):
            self.official_links.append(href)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_DARPA_CACHE_DIR", str(_DEFAULT_CACHE)))


def _publication(raw: str | None) -> tuple[str | None, date | None]:
    if not raw:
        return None, None
    try:
        value = parsedate_to_datetime(raw)
        return value.isoformat(), value.date()
    except (TypeError, ValueError):
        return raw.strip() or None, None


def _item_type(title: str) -> str:
    lowered = title.lower()
    if "proposers day" in lowered:
        return "proposers_day"
    if lowered.startswith("sbir:"):
        return "sbir_topic"
    if "shopping notice" in lowered:
        return "shopping_notice"
    if "pitch day" in lowered:
        return "pitch_day"
    return "research_program"


def _parse_rss(xml_text: str) -> tuple[list[dict], str]:
    root = ET.fromstring(xml_text)
    rows: list[dict] = []
    published_dates: list[date] = []
    _channel_stamp, channel_date = _publication(
        root.findtext("./channel/lastBuildDate")
        or root.findtext("./channel/pubDate")
    )
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title") or "").split())
        guid = " ".join((item.findtext("guid") or "").split())
        if not title:
            continue
        parser = _DescriptionParser()
        parser.feed(item.findtext("description") or "")
        parser.close()
        description = " ".join(" ".join(parser.parts).split()) or None
        feed_link = " ".join((item.findtext("link") or "").split())
        canonical = parser.official_links[0] if parser.official_links else feed_link
        if not canonical:
            canonical = INDEX_URL
        published_at, published_date = _publication(item.findtext("pubDate"))
        if published_date:
            published_dates.append(published_date)
        stable_id = guid.split(" at ", 1)[0].strip() or canonical or title
        rows.append(
            {
                "record_id": f"darpa:{stable_id}",
                "canonical_url": canonical,
                "kind": "agency_announcement",
                "signal_type": _item_type(title),
                "title": title,
                "agency": "Defense Advanced Research Projects Agency",
                "agency_acronym": "DARPA",
                "summary": description,
                "published_at": published_at,
                "feed_url": FEED_URL,
            }
        )
    if not rows:
        raise ValueError("DARPA opportunities RSS contained no parseable items")
    dated = [*published_dates, *([channel_date] if channel_date else [])]
    if not dated:
        raise ValueError(
            "DARPA opportunities RSS has no item or channel publication date"
        )
    data_as_of = max(dated).isoformat()
    return rows, data_as_of


def _fetch_live() -> ProgramPull:
    rows, data_as_of = _parse_rss(get_text(FEED_URL))
    return ProgramPull(
        rows,
        data_as_of,
        source_attempts=[{"source": "darpa_opportunities_rss", "status": "success"}],
        limitations=(
            "The feed includes programs and engagement events; a formal award or "
            "solicitation must be verified at its canonical notice source"
        ),
    )


def _matches(row: dict, query: SourceQuery) -> bool:
    haystack = " ".join(str(value) for value in row.values() if value is not None).lower()
    keywords = [word.strip('"') for word in query.keywords if word.strip('"')]
    agencies = [agency.lower() for agency in query.agencies if agency]
    return (not keywords or bool(matching_phrases(haystack, keywords))) and (
        not agencies or any(agency in haystack for agency in agencies)
    )


def _darpa_rank(row: dict) -> tuple:
    published = str(row.get("published_at") or "")
    try:
        stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        newest = -stamp.timestamp()
    except ValueError:
        newest = float("inf")
    return (
        newest,
        str(row.get("title") or "").casefold(),
        str(row.get("record_id") or ""),
    )


@register_source
class DarpaOpportunitiesSource(DataSource):
    name = "darpa_opportunities"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            rows, _data_as_of = _parse_rss(get_text(FEED_URL, timeout=8.0, retries=1))
            return True, f"official DARPA feed returned {len(rows)} program signals"
        except Exception as exc:  # noqa: BLE001
            return False, f"DARPA opportunities RSS unavailable: {exc}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("PROGRAM-tier enrichment source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        records, provenance = cached_program_pull(
            source=self.name,
            canonical_url=FEED_URL,
            cache_dir=_cache_dir(),
            fetch_live=_fetch_live,
        )
        candidates = [row for row in records if _matches(row, query)]
        candidates.sort(key=_darpa_rank)
        matched = candidates[: query.limit]
        return {
            "records": matched,
            "_provenance": {
                **provenance,
                "records_matched": len(candidates),
                "candidate_total": len(candidates),
                "selected_count": len(matched),
                "truncated": len(matched) < len(candidates),
                "selection_order": "newest official publication first",
            },
        }
