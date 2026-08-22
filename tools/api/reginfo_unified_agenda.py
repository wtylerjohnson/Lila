"""RegInfo Unified Agenda XML adapter (PROGRAM-tier policy demand).

The Unified Agenda describes actions agencies plan to issue. It is useful
forming-demand evidence, but it is not a procurement notice and is never
eligible for the live-solicitation ledger.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

from tools.api._http import get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.program_cache import ProgramPull, cached_program_pull
from tools.text_match import matching_phrases

INDEX_URL = "https://www.reginfo.gov/public/do/eAgendaXmlReport"
VIEW_URL = "https://www.reginfo.gov/public/do/eAgendaViewRule"
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "reginfo"
_XML_NAME = re.compile(r"REGINFO_RIN_DATA_(\d+)\.xml", re.IGNORECASE)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if _XML_NAME.search(href):
            self.links.append(urljoin(INDEX_URL, href))


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    text = " ".join(" ".join(parser.parts).split())
    return text or None


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_REGINFO_CACHE_DIR", str(_DEFAULT_CACHE)))


def _xml_version(url: str) -> int:
    match = _XML_NAME.search(url)
    return int(match.group(1)) if match else 0


def _discover_xml_url() -> tuple[str, list[dict]]:
    override = os.environ.get("LILA_REGINFO_XML_URL")
    if override:
        return override, [{"source": "configured_xml_url", "status": "success"}]
    parser = _LinkParser()
    parser.feed(get_text(INDEX_URL))
    parser.close()
    if not parser.links:
        raise RuntimeError("RegInfo XML report index contained no agenda XML link")
    return max(parser.links, key=_xml_version), [
        {"source": "xml_report_index", "status": "success"}
    ]


def _node_text(node: ET.Element, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    value = " ".join(found.text.split()).strip()
    return value or None


def _parse_xml(xml_text: str) -> tuple[list[dict], str]:
    root = ET.fromstring(xml_text)
    run_date = str(root.attrib.get("RUN_DATE") or "").strip()
    data_as_of = run_date[:10] if re.match(r"\d{4}-\d{2}-\d{2}", run_date) else run_date
    rows: list[dict] = []
    for item in root.findall(".//RIN_INFO"):
        rin = _node_text(item, "RIN")
        title = _node_text(item, "RULE_TITLE")
        publication_id = _node_text(item, "PUBLICATION/PUBLICATION_ID")
        if not rin or not title:
            continue
        canonical = f"{VIEW_URL}?{urlencode({'RIN': rin, 'pubId': publication_id or ''})}"
        timetable = []
        for event in item.findall("TIMETABLE_LIST/TIMETABLE"):
            action = _node_text(event, "TTBL_ACTION")
            stated_date = _node_text(event, "TTBL_DATE")
            if action or stated_date:
                timetable.append(
                    {
                        "action": action,
                        "date": stated_date,
                        "federal_register_citation": _node_text(
                            event, "FR_CITATION"
                        ),
                    }
                )
        parent = _node_text(item, "PARENT_AGENCY/NAME")
        component = _node_text(item, "AGENCY/NAME")
        rows.append(
            {
                "record_id": f"reginfo:{rin}:{publication_id or 'current'}",
                "canonical_url": canonical,
                "kind": "regulation",
                "rin": rin,
                "publication_id": publication_id,
                "title": title,
                "agency": parent or component,
                "component": component if parent and component != parent else None,
                "agency_acronym": (
                    _node_text(item, "PARENT_AGENCY/ACRONYM")
                    or _node_text(item, "AGENCY/ACRONYM")
                ),
                "abstract": _plain_text(_node_text(item, "ABSTRACT")),
                "stage": _node_text(item, "RULE_STAGE"),
                "status": _node_text(item, "RIN_STATUS"),
                "priority": _node_text(item, "PRIORITY_CATEGORY"),
                "major": _node_text(item, "MAJOR"),
                "cfr": [
                    " ".join((node.text or "").split())
                    for node in item.findall("CFR_LIST/CFR")
                    if (node.text or "").strip()
                ],
                "timetable": timetable,
            }
        )
        if not data_as_of and publication_id:
            data_as_of = publication_id
    if not data_as_of:
        raise ValueError("RegInfo XML has no RUN_DATE or publication identifier")
    if not rows:
        raise ValueError("RegInfo XML contained no parseable agenda records")
    return rows, data_as_of


def _fetch_live() -> ProgramPull:
    xml_url, attempts = _discover_xml_url()
    rows, data_as_of = _parse_xml(get_text(xml_url))
    attempts.append(
        {"source": "unified_agenda_xml", "status": "success", "url": xml_url}
    )
    return ProgramPull(
        rows,
        data_as_of,
        source_attempts=attempts,
        limitations=(
            "Agenda dates are agency plans that may slip, change, or be withdrawn; "
            "they are not procurement commitments or live solicitations"
        ),
    )


def _matches(row: dict, query: SourceQuery) -> bool:
    haystack = " ".join(
        str(value)
        for key, value in row.items()
        if key != "timetable" and value is not None
    ).lower()
    keywords = [word.strip('"') for word in query.keywords if word.strip('"')]
    agencies = [agency.lower() for agency in query.agencies if agency]
    return (not keywords or bool(matching_phrases(haystack, keywords))) and (
        not agencies or any(agency in haystack for agency in agencies)
    )


def _agenda_rank(row: dict) -> tuple:
    """Soonest stated action first; unknown dates sort last."""
    planned = []
    for event in row.get("timetable") or []:
        if not isinstance(event, dict):
            continue
        match = re.search(
            r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})",
            str(event.get("date") or ""),
        )
        if match:
            month = int(match.group("month")) or 13
            day = int(match.group("day")) or 32
            planned.append((int(match.group("year")), month, day))
    next_action = min(planned, default=(9999, 13, 32))
    return (
        next_action,
        str(row.get("title") or "").casefold(),
        str(row.get("record_id") or ""),
    )


@register_source
class RegInfoUnifiedAgendaSource(DataSource):
    name = "reginfo_unified_agenda"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            xml_url, _attempts = _discover_xml_url()
            return True, f"official Unified Agenda XML published: {xml_url}"
        except Exception as exc:  # noqa: BLE001
            return False, f"RegInfo XML unavailable: {exc}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("PROGRAM-tier enrichment source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        records, provenance = cached_program_pull(
            source=self.name,
            canonical_url=INDEX_URL,
            cache_dir=_cache_dir(),
            fetch_live=_fetch_live,
        )
        candidates = [row for row in records if _matches(row, query)]
        candidates.sort(key=_agenda_rank)
        matched = candidates[: query.limit]
        return {
            "records": matched,
            "_provenance": {
                **provenance,
                "records_matched": len(candidates),
                "candidate_total": len(candidates),
                "selected_count": len(matched),
                "truncated": len(matched) < len(candidates),
                "selection_order": "soonest planned agenda action",
            },
        }
