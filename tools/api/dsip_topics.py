"""Defense SBIR/STTR Innovation Portal active topics (PROGRAM-tier).

The public DSIP Topics application is an Angular client.  Its anonymous topic
page reads three official JSON routes on ``www.dodsbirsttr.mil``:

* ``/core/api/public/dropdown/lookup`` for the current release-status ids;
* ``/topics/api/public/topics/search`` for the complete active topic list; and
* ``/topics/api/public/topics/{topic_id}/details`` for objectives, keywords,
  descriptions, and phase information.

Only rows whose official status is ``Pre-Release`` or ``Open`` are retained.
They are planning/program evidence and are deliberately exposed through
``enrich()`` only; they can never become ``RawOpportunity`` or live-notice
evidence through this adapter.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.program_cache import ProgramPull, cached_program_pull

TOPICS_APP_URL = "https://www.dodsbirsttr.mil/topics-app/"
STATUS_URL = "https://www.dodsbirsttr.mil/core/api/public/dropdown/lookup"
SEARCH_URL = "https://www.dodsbirsttr.mil/topics/api/public/topics/search"
DETAIL_URL = (
    "https://www.dodsbirsttr.mil/topics/api/public/topics/{topic_id}/details"
)
HEADERS = {
    # DSIP serves this browser application behind a WAF.  This identifies an
    # ordinary standards-compatible browser request while naming our client.
    "User-Agent": (
        "Mozilla/5.0 (compatible; Federal-Sales-OS/1.0; "
        "+mailto:william.tyler.johnson@gmail.com)"
    ),
    "Accept": "application/json",
}
ACTIVE_STATUSES = frozenset({"Open", "Pre-Release"})
PAGE_SIZE = int(os.environ.get("LILA_DSIP_PAGE_SIZE", "100"))
MAX_PAGES = int(os.environ.get("LILA_DSIP_MAX_PAGES", "10"))
DETAIL_DELAY_S = float(os.environ.get("LILA_DSIP_DETAIL_DELAY_S", "0.05"))
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "dsip"
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^a-z0-9]+")
_COMPONENT_ALIASES = {
    "ARMY": "United States Army Department of the Army",
    "CBD": "Chemical and Biological Defense",
    "DARPA": "Defense Advanced Research Projects Agency",
    "DHA": "Defense Health Agency",
    "DLA": "Defense Logistics Agency",
    "DTRA": "Defense Threat Reduction Agency",
    "MDA": "Missile Defense Agency",
    "NAVY": "United States Navy Department of the Navy",
    "OSD": "Office of the Secretary of Defense",
    "SOCOM": "United States Special Operations Command",
    "USAF": "United States Air Force Department of the Air Force",
    "USMC": "United States Marine Corps",
}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_DSIP_CACHE_DIR", str(_DEFAULT_CACHE)))


def _plain(value: Any) -> str | None:
    """Return readable deterministic text from DSIP's HTML-rich fields."""

    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        parts = [_plain(item) for item in value]
        text = "; ".join(part for part in parts if part)
        return text or None
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    text = _SPACE_RE.sub(" ", text).strip()
    return text or None


def _iso_date(value: Any) -> str | None:
    """Normalize DSIP epoch-millisecond dates without local-time drift."""

    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raw = str(value).strip()
        return raw or None
    # DSIP currently sends epoch milliseconds; tolerate epoch seconds in
    # recorded fixtures or a future compatible response.
    if abs(number) > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()


def _normalized(value: Any) -> str:
    return _SPACE_RE.sub(" ", _WORD_RE.sub(" ", (_plain(value) or "").casefold())).strip()


def _query_terms(query: SourceQuery) -> list[str]:
    terms: set[str] = set()
    for value in query.keywords:
        normalized = _normalized(str(value).strip().strip('"'))
        if normalized:
            terms.add(normalized)
    return sorted(terms)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


def _record_haystack(row: dict) -> str:
    fields = (
        "title",
        "topic_code",
        "component",
        "command",
        "program",
        "solicitation_title",
        "keywords",
        "objective",
        "description",
        "phase_1_description",
        "phase_2_description",
        "phase_3_description",
        "technology_areas",
        "focus_areas",
    )
    return _normalized(" ".join(str(row.get(field) or "") for field in fields))


def _agency_matches(row: dict, query: SourceQuery) -> bool:
    if not query.agencies:
        return True
    component = str(row.get("component") or "").upper()
    agency_haystack = _normalized(
        " ".join(
            str(value or "")
            for value in (
                row.get("agency"),
                row.get("component"),
                row.get("command"),
                _COMPONENT_ALIASES.get(component),
            )
        )
    )
    return any(
        _contains_phrase(agency_haystack, agency)
        for agency in (_normalized(value) for value in query.agencies)
        if agency
    )


def _match_record(row: dict, query: SourceQuery) -> tuple[bool, list[str]]:
    if not _agency_matches(row, query):
        return False, []
    terms = _query_terms(query)
    if not terms:
        return True, []
    haystack = _record_haystack(row)
    matched = [term for term in terms if _contains_phrase(haystack, term)]
    return bool(matched), matched


def _active_status_ids(payload: Any) -> list[int]:
    if not isinstance(payload, list):
        raise ValueError("DSIP release-status lookup was not a list")
    ids: set[int] = set()
    for item in payload:
        if not isinstance(item, dict) or item.get("label") not in ACTIVE_STATUSES:
            continue
        try:
            ids.add(int(item["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(ids) != len(ACTIVE_STATUSES):
        raise ValueError("DSIP status lookup did not name both Open and Pre-Release")
    return sorted(ids)


def _search_params(*, status_ids: list[int], page: int) -> dict[str, Any]:
    search_param = {
        "searchText": None,
        "components": None,
        "programYear": None,
        "solicitationCycleNames": ["openTopics"],
        "releaseNumbers": None,
        "topicReleaseStatus": status_ids,
        "modernizationPriorities": None,
        "sortBy": "finalTopicCode,asc",
    }
    return {
        "searchParam": json.dumps(search_param, separators=(",", ":")),
        "size": PAGE_SIZE,
        "page": page,
    }


def _fetch_listing() -> tuple[list[dict], int, bool, list[dict]]:
    status_payload = get_json(
        STATUS_URL,
        params={
            "type": "topics.release_status",
            "excludeLookupItem": (
                "INACTIVE,READY_FOR_RELEASE,READY_TO_CERTIFY,"
                "READY_TO_REVIEW,REVISION_REQUESTED"
            ),
        },
        headers=HEADERS,
        timeout=20.0,
        retries=2,
    )
    status_ids = _active_status_ids(status_payload)
    attempts = [{"source": "dsip_status_lookup", "status": "success"}]
    by_id: dict[str, dict] = {}
    expected_total = 0
    complete = False
    for page in range(MAX_PAGES):
        payload = get_json(
            SEARCH_URL,
            params=_search_params(status_ids=status_ids, page=page),
            headers=HEADERS,
            timeout=30.0,
            retries=2,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("DSIP topic search returned an invalid payload")
        try:
            expected_total = max(expected_total, int(payload.get("total") or 0))
        except (TypeError, ValueError):
            raise ValueError("DSIP topic search returned an invalid total") from None
        rows = [row for row in payload["data"] if isinstance(row, dict)]
        for row in rows:
            topic_id = str(row.get("topicId") or "").strip()
            if topic_id and row.get("topicStatus") in ACTIVE_STATUSES:
                by_id[topic_id] = row
        if not rows or len(by_id) >= expected_total:
            complete = len(by_id) >= expected_total
            break
    attempts.append(
        {
            "source": "dsip_active_topic_search",
            "status": "success" if complete else "partial",
            "records_received": len(by_id),
            "records_expected": expected_total,
        }
    )
    return [by_id[key] for key in sorted(by_id)], expected_total, complete, attempts


def _detail_url(topic_id: str) -> str:
    return DETAIL_URL.format(topic_id=quote(topic_id, safe=""))


def _topic_managers(row: dict) -> list[dict]:
    if not row.get("showTpoc") or not isinstance(row.get("topicManagers"), list):
        return []
    managers = []
    for raw in row["topicManagers"]:
        if not isinstance(raw, dict):
            continue
        manager = {
            "name": _plain(raw.get("name")),
            "organization": _plain(raw.get("center")),
            "role": _plain(raw.get("assignmentType")),
        }
        if raw.get("emailDisplay") == "Y":
            manager["email"] = _plain(raw.get("email"))
        if raw.get("phoneDisplay") == "Y":
            manager["phone"] = _plain(raw.get("phone"))
        managers.append({key: value for key, value in manager.items() if value})
    return managers


def _map_record(header: dict, detail: dict | None) -> dict:
    topic_id = str(header.get("topicId") or "").strip()
    if not topic_id:
        raise ValueError("DSIP topic row has no topicId")
    details = detail or {}
    status = str(header.get("topicStatus") or "").strip()
    managers = _topic_managers(header)
    return {
        "record_id": f"dsip:{topic_id}",
        "canonical_url": _detail_url(topic_id),
        "kind": "defense_sbir_sttr_topic",
        "signal_type": "small_business_innovation_topic",
        "agency": "Department of Defense",
        "component": _plain(header.get("component")),
        "command": _plain(header.get("command")),
        "program": _plain(header.get("program")),
        "status": status,
        "title": _plain(header.get("topicTitle")) or "Untitled DSIP topic",
        "topic_code": _plain(header.get("topicCode")),
        "solicitation_number": _plain(header.get("solicitationNumber")),
        "solicitation_title": _plain(header.get("solicitationTitle")),
        "cycle_name": _plain(header.get("cycleName")),
        "release_number": header.get("releaseNumber"),
        "pre_release_start_date": _iso_date(header.get("topicPreReleaseStartDate")),
        "pre_release_end_date": _iso_date(header.get("topicPreReleaseEndDate")),
        "open_date": _iso_date(header.get("topicStartDate")),
        "close_date": _iso_date(header.get("topicEndDate")),
        "cmmc_level": _plain(header.get("cmmcLevel")),
        "keywords": _plain(details.get("keywords")),
        "objective": _plain(details.get("objective")),
        "description": _plain(details.get("description")),
        "phase_1_description": _plain(details.get("phase1Description")),
        "phase_2_description": _plain(details.get("phase2Description")),
        "phase_3_description": _plain(details.get("phase3Description")),
        "technology_areas": [
            text for value in (details.get("technologyAreas") or [])
            if (text := _plain(value))
        ],
        "focus_areas": [
            text for value in (details.get("focusAreas") or [])
            if (text := _plain(value))
        ],
        "itar": details.get("itar"),
        "topic_managers": managers,
        "direct_contact_permitted": status == "Pre-Release" and bool(managers),
        "promotion_eligible": False,
        "live_solicitation": False,
    }


def _fetch_live() -> ProgramPull:
    headers, expected, listing_complete, attempts = _fetch_listing()
    records: list[dict] = []
    detail_failures = 0
    for index, header in enumerate(headers):
        topic_id = str(header.get("topicId") or "").strip()
        detail: dict | None = None
        try:
            payload = get_json(
                _detail_url(topic_id),
                headers=HEADERS,
                timeout=30.0,
                retries=2,
            )
            if not isinstance(payload, dict) or str(payload.get("topicId") or "") != topic_id:
                raise ValueError("DSIP topic detail did not match its topicId")
            detail = payload
        except Exception:  # noqa: BLE001 - retain truthful header-only signal
            detail_failures += 1
        records.append(_map_record(header, detail))
        if DETAIL_DELAY_S and index < len(headers) - 1:
            time.sleep(DETAIL_DELAY_S)
    attempts.append(
        {
            "source": "dsip_topic_details",
            "status": "success" if not detail_failures else "partial",
            "records_requested": len(headers),
            "records_received": len(headers) - detail_failures,
        }
    )
    partial = not listing_complete or bool(detail_failures)
    limitation = (
        "DSIP topics are PROGRAM-tier forming-demand evidence, not SAM.gov "
        "live notices. Active coverage is limited to official Pre-Release and "
        "Open topic statuses; archived/closed topics and proposal eligibility "
        "determinations are outside this adapter."
    )
    if detail_failures:
        limitation += (
            f" {detail_failures} of {len(headers)} topic-detail calls failed; "
            "those rows retain official header fields but omit descriptive text."
        )
    if not listing_complete:
        limitation += (
            f" The search returned {len(headers)} of {expected} reported active topics."
        )
    return ProgramPull(
        records=records,
        data_as_of=_today().isoformat(),
        partial=partial,
        source_attempts=attempts,
        limitations=limitation,
    )


@register_source
class DsipTopicsSource(DataSource):
    """Official active Defense SBIR/STTR topics, enrichment-only."""

    name = "dsip_topics"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            payload = get_json(
                STATUS_URL,
                params={"type": "topics.release_status"},
                headers=HEADERS,
                timeout=8.0,
                retries=1,
            )
            ids = _active_status_ids(payload)
            return True, f"official anonymous DSIP API exposes active status ids {ids}"
        except Exception as exc:  # noqa: BLE001
            return False, f"official anonymous DSIP API unavailable: {exc}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("PROGRAM-tier enrichment source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        records, provenance = cached_program_pull(
            source=self.name,
            canonical_url=TOPICS_APP_URL,
            cache_dir=_cache_dir(),
            fetch_live=_fetch_live,
        )
        matched: list[dict] = []
        for row in records:
            is_match, matched_terms = _match_record(row, query)
            if not is_match:
                continue
            decorated = dict(row)
            decorated["matched_terms"] = matched_terms
            matched.append(decorated)
        matched.sort(
            key=lambda row: (
                str(row.get("close_date") or "9999-12-31"),
                str(row.get("topic_code") or ""),
                str(row.get("record_id") or ""),
            )
        )
        candidate_total = len(matched)
        matched = matched[: query.limit]
        return {
            "records": matched,
            "_provenance": {
                **provenance,
                "records_matched": candidate_total,
                "candidate_total": candidate_total,
                "selected_count": len(matched),
                "truncated": len(matched) < candidate_total,
                "selection_order": "soonest active topic close date",
                "active_statuses": sorted(ACTIVE_STATUSES),
                "consumer": "prospective_horizon",
            },
        }
