"""DOE CMEI/EERE eXCHANGE public funding and market-research listings.

The portal mixes NOFOs, lab calls, notices of intent, RFIs, and teaming-list
notices.  Those categories remain explicit: none is normalized into a federal
contract opportunity, and an NOI/RFI is not represented as available funding.
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

from tools.api._accepted_wave import (FetchBytes, fetch_bytes, payload,
                                      receipt)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source


SOURCE_NAME = "doe_eere_exchange"
MODE = "live_doe_cmei_exchange_html"
BASE_URL = "https://eere-exchange.energy.gov/"
_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(
    r'(<tr id="ctl00_cphMainContent_FoaDetailGridControl_gvFoaList_DXDataRow\d+"'
    r'.*?</tr>)', re.I | re.S,
)


def _text(fragment: Any) -> Optional[str]:
    cleaned = html.unescape(_TAG.sub(" ", str(fragment or "")))
    cleaned = " ".join(cleaned.split())
    return cleaned or None


def _cell(segment: str, column: int) -> Optional[str]:
    match = re.search(
        rf'id="[^"]*_cell\d+_{column}_[^"]*"[^>]*>(.*?)</(?:a|span)>',
        segment, re.I | re.S,
    )
    return _text(match.group(1)) if match else None


def _fragment(segment: str) -> Optional[str]:
    match = re.search(
        r'id="[^"]*_cell\d+_2_[^"]*"[^>]*href="([^"]+)"',
        segment, re.I,
    )
    return html.unescape(match.group(1)) if match else None


def _stage(value: Optional[str]) -> str:
    text = str(value or "").casefold()
    if "teaming" in text:
        return "teaming-list"
    if "request for information" in text or "rfi" in text:
        return "market-research-rfi"
    if "notice of intent" in text or "noi" in text:
        return "advance-intent-notice"
    if "lab call" in text:
        return "assistance-lab-call"
    if "funding" in text or "nofo" in text or "foa" in text:
        return "assistance-funding-notice"
    return "program-notice"


def _parse_list(doc: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    dropped = 0
    for segment in _ROW.findall(doc):
        number, title = _cell(segment, 1), _cell(segment, 2)
        if not number or not title:
            dropped += 1
            continue
        fragment = _fragment(segment)
        guid_match = re.search(r"FoaId([0-9a-f-]{20,})", fragment or "", re.I)
        guid = guid_match.group(1) if guid_match else None
        announcement_type = _cell(segment, 3)
        stage = _stage(announcement_type or title)
        citation = BASE_URL + (fragment or "")
        records.append({
            "record_id": f"doe-exchange:{number}",
            "announcement_number": number,
            "title": title,
            "announcement_type": announcement_type,
            "signal_stage": stage,
            "program_office": _cell(segment, 4),
            "rfi_deadline_stated": _cell(segment, 6),
            "letter_of_intent_deadline_stated": _cell(segment, 7),
            "concept_paper_deadline_stated": _cell(segment, 8),
            "full_application_deadline_stated": _cell(segment, 9),
            "citation_url": citation,
            "teaming_list_url": (
                f"{BASE_URL}TeamingPartners.aspx?foaid={guid}"
                if stage == "teaming-list" and guid else None
            ),
            "_join_keys": {
                "foa_number": number if number.startswith("DE-FOA-") else None,
                "announcement_number": number,
                "foa_guid": guid,
            },
            "contract_opportunity": False,
            "semantics": (
                "DOE assistance/program signal; NOI and RFI rows are advance "
                "market signals, not funding or contract opportunities"
            ),
        })
    return records, dropped


def _matches(record: dict[str, Any], terms: list[str]) -> list[str]:
    haystack = " ".join(str(record.get(key) or "") for key in (
        "announcement_number", "title", "announcement_type", "program_office",
    )).casefold()
    return [term for term in terms if term in haystack]


class DoeEereExchangeSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetcher: Optional[FetchBytes] = None) -> None:
        self._fetcher = fetcher or fetch_bytes

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use announcements()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            blob, _headers = self._fetcher(BASE_URL, timeout=30.0, retries=1)
            records, _dropped = _parse_list(blob.decode("utf-8", errors="replace"))
            if records:
                return True, f"official DOE eXCHANGE listing reachable ({len(records)} rows)"
            return False, "DOE eXCHANGE page had no recognizable listing rows"
        except Exception as exc:  # noqa: BLE001
            return False, f"DOE eXCHANGE unavailable: {exc}"

    def announcements(self, query: SourceQuery) -> dict[str, Any]:
        terms = [str(value).strip().strip('"').casefold()
                 for value in query.keywords if str(value).strip()]
        if not terms:
            return payload(
                source=self.name, mode=MODE, items=[], status="partial",
                limitations=[
                    "unscoped portal mirror refused; pass capability, program, or announcement terms",
                ],
            )
        try:
            blob, headers = self._fetcher(BASE_URL, timeout=45.0, retries=2)
            records, dropped = _parse_list(blob.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            return payload(
                source=self.name, mode=MODE, items=[], status="failed",
                limitations=[f"DOE eXCHANGE retrieval or parse failed: {exc}"],
            )
        matched: list[dict[str, Any]] = []
        for record in records:
            hits = _matches(record, terms)
            if hits:
                matched.append({**record, "matched_terms": hits})
        limit = max(1, min(query.limit, 100))
        total = len(matched)
        items = matched[:limit]
        limitations = [
            "the official ASP.NET listing has no documented API; markup drift is a standing risk",
            "portal listing retrieval is whole-page, but normalization is keyword-scoped and output-bounded",
            "NOI, RFI, lab call, funding notice, and teaming-list semantics remain distinct",
            "DOE assistance/program notices are not represented as federal contract opportunities",
        ]
        if dropped:
            limitations.append(f"ignored {dropped} malformed listing rows")
        if total > limit:
            limitations.append(f"kept first {limit} of {total} scoped announcements")
        source_receipt = receipt(
            source=self.name, url=BASE_URL,
            query={"keywords": query.keywords, "limit": query.limit},
            blob=blob, normalized=items, headers=headers,
            content_kind="text/html",
        )
        return payload(
            source=self.name, mode=MODE, items=items,
            status="partial" if dropped or total > limit else "complete",
            limitations=limitations, source_receipt=source_receipt,
            total_matched=total,
            public_detail="Official DOE CMEI/EERE eXCHANGE scoped listing",
        )


try:
    register_source(DoeEereExchangeSource)
except ValueError:
    pass
