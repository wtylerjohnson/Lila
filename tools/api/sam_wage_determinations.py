"""SAM.gov public wage-determination search metadata (WDOL successor).

This is reference enrichment for wage-determination identifiers and geographic
applicability.  Search records do not contain occupation-level wage rates and
must never be modeled as solicitations, awards, or labor-price estimates.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

from tools.api._accepted_wave import (FetchBytes, fetch_bytes, payload,
                                      receipt)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source


SOURCE_NAME = "sam_wage_determinations"
MODE = "live_sam_wage_determination_hal_search"
API_URL = "https://sam.gov/api/prod/sgs/v1/search/"
LANDING_URL = "https://sam.gov/wage-determinations"
HEADERS = {"Accept": "application/hal+json"}
MAX_QUERY_TERMS = 8


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(
                value / 1000 if value > 10_000_000_000 else value,
                tz=timezone.utc,
            ).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc).isoformat()
    except ValueError:
        return text


def _counties(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(value, list):
        return ([{"code": row.get("code"), "name": row.get("value")}
                 for row in value if isinstance(row, dict)], [])
    if not isinstance(value, dict):
        return [], []
    include = value.get("include") if isinstance(value.get("include"), list) else []
    exclude = value.get("exclude") if isinstance(value.get("exclude"), list) else []
    project = lambda rows: [  # noqa: E731 - compact typed projection
        {"code": row.get("code"), "name": row.get("value")}
        for row in rows if isinstance(row, dict)
    ]
    return project(include), project(exclude)


def _locations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    states = value.get("states")
    if not isinstance(states, list):
        state = value.get("state")
        states = [state] if isinstance(state, dict) else []
    result = []
    for state in states:
        if not isinstance(state, dict):
            continue
        included, excluded = _counties(state.get("counties"))
        result.append({
            "state_code": state.get("code"),
            "state": state.get("name"),
            "statewide": bool(state.get("isStateWide")) or any(
                str(row.get("name") or "").casefold() == "statewide"
                for row in included
            ),
            "counties_included": included,
            "counties_excluded": excluded,
        })
    return result


def _references(row: dict[str, Any]) -> list[str]:
    values = [row.get("fullReferenceNumber"), row.get("shortReferenceNumber"),
              row.get("cbaNumber"), row.get("title")]
    for native in row.get("allReferenceNumbers") or []:
        if isinstance(native, dict):
            values.append(native.get("wdNumber"))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def _normalize(row: dict[str, Any], url: str) -> Optional[dict[str, Any]]:
    references = _references(row)
    native_id = str(row.get("_id") or "").strip()
    if not native_id and not references:
        return None
    type_obj = row.get("type") if isinstance(row.get("type"), dict) else {}
    wd_type = type_obj.get("code") or row.get("_type")
    primary = references[0] if references else native_id
    return {
        "record_id": f"sam-wd:{native_id or primary}",
        "reference_number": primary,
        "all_reference_numbers": references,
        "wage_determination_type": wd_type,
        "wage_determination_type_label": type_obj.get("value"),
        "revision_number": row.get("revisionNumber"),
        "publish_date": _iso(row.get("publishDate")),
        "modified_date": _iso(row.get("modifiedDate")),
        "active": row.get("isActive"),
        "latest": row.get("isLatest"),
        "standard": row.get("isStandard"),
        "locations": _locations(row.get("location")),
        "citation_url": url,
        "_join_keys": {
            "wage_determination_numbers": references,
            "state_codes": [location.get("state_code")
                            for location in _locations(row.get("location"))],
        },
        "wage_rates_present": False,
        "semantics": (
            "wage-determination reference metadata for notice/document enrichment; "
            "not an opportunity, award, or labor-price estimate"
        ),
    }


def _params(query: SourceQuery) -> tuple[dict[str, Any], bool]:
    terms = [str(value).strip().strip('"') for value in query.keywords
             if str(value).strip()]
    capped = len(terms) > MAX_QUERY_TERMS
    terms = terms[:MAX_QUERY_TERMS]
    result: dict[str, Any] = {
        "index": "wd", "page": 0,
        "size": max(1, min(query.limit, 50)),
        "sort": "-relevance", "q": " ".join(terms), "qMode": "ALL",
    }
    return result, capped


class SamWageDeterminationsSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetcher: Optional[FetchBytes] = None) -> None:
        self._fetcher = fetcher or fetch_bytes

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError(
            "reference-enrichment source; use wage_determinations()")

    def healthcheck(self) -> tuple[bool, str]:
        params = {"index": "wd", "page": 0, "size": 1,
                  "sort": "-modifiedDate"}
        url = API_URL + "?" + urlencode(params)
        try:
            blob, _headers = self._fetcher(
                url, headers=HEADERS, timeout=15.0, retries=1)
            body = json.loads(blob.decode("utf-8"))
            page = body.get("page") if isinstance(body, dict) else None
            if isinstance(page, dict) and isinstance(page.get("totalElements"), int):
                return True, (
                    "official public SAM wage-determination search reachable "
                    f"({page['totalElements']} indexed records)"
                )
            return False, "SAM wage search returned an unexpected HAL shape"
        except Exception as exc:  # noqa: BLE001
            return False, f"SAM wage-determination search unavailable: {exc}"

    def wage_determinations(self, query: SourceQuery) -> dict[str, Any]:
        if not query.keywords:
            return payload(
                source=self.name, mode=MODE, items=[], status="partial",
                limitations=[
                    "unscoped wage-determination corpus search refused; pass WD/CBA or locality terms",
                ],
            )
        params, terms_capped = _params(query)
        url = API_URL + "?" + urlencode(params)
        try:
            blob, headers = self._fetcher(
                url, headers=HEADERS, timeout=30.0, retries=2)
            body = json.loads(blob.decode("utf-8"))
            embedded = body.get("_embedded") if isinstance(body, dict) else None
            native = embedded.get("results") if isinstance(embedded, dict) else None
            page = body.get("page") if isinstance(body, dict) else None
            total = page.get("totalElements") if isinstance(page, dict) else None
            if native is None and total == 0:
                native = []
            if not isinstance(native, list):
                raise ValueError("SAM response has no HAL results list")
        except Exception as exc:  # noqa: BLE001
            return payload(
                source=self.name, mode=MODE, items=[], status="failed",
                limitations=[f"SAM wage search retrieval or parse failed: {exc}"],
            )
        items: list[dict[str, Any]] = []
        dropped = 0
        for row in native:
            normalized = _normalize(row, url) if isinstance(row, dict) else None
            if normalized is None:
                dropped += 1
            else:
                items.append(normalized)
        page = body.get("page") if isinstance(body, dict) else {}
        total = page.get("totalElements") if isinstance(page, dict) else len(items)
        if not isinstance(total, int):
            total = len(items)
        limitations = [
            "the public HAL endpoint is an undocumented SAM UI backend with no SLA",
            "search metadata has WD identifiers, revisions, and geography but no occupation wage-rate table",
            "records enrich solicitation references and place of performance; they are not opportunities or awards",
        ]
        if terms_capped:
            limitations.append(f"used first {MAX_QUERY_TERMS} keyword terms")
        if dropped:
            limitations.append(f"ignored {dropped} malformed search rows")
        if total > len(items):
            limitations.append(
                f"one bounded page returned {len(items)} of {total} source matches")
        source_receipt = receipt(
            source=self.name, url=url, query=params, blob=blob,
            normalized=items, headers=headers,
            content_kind="application/hal+json",
        )
        return payload(
            source=self.name, mode=MODE, items=items,
            status=("partial" if terms_capped or dropped or total > len(items)
                    else "complete"),
            limitations=limitations, source_receipt=source_receipt,
            total_matched=total,
            public_detail="Official public SAM.gov wage-determination search metadata",
        )


try:
    register_source(SamWageDeterminationsSource)
except ValueError:
    pass
