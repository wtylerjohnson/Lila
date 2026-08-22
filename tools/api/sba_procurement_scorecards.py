"""SBA annual Small Business Procurement Scorecard data.

Scorecards are agency-level performance context.  They do not identify a live
opportunity, a vendor's eligibility, or the set-aside strategy for a future
procurement.  Current-year SBA pages render from the official yearly JSON used
here; the adapter projects only documented decision-relevant fields.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Optional

from tools.api._accepted_wave import (FetchBytes, fetch_bytes, payload,
                                      receipt)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source


SOURCE_NAME = "sba_procurement_scorecards"
MODE = "live_sba_procurement_scorecard_json"
DEFAULT_FISCAL_YEAR = 2025
URL_TEMPLATE = (
    "https://legacy.sba.gov/sites/default/files/data/agency-scorecards-{year}.json"
)
LANDING_URL = (
    "https://legacy.sba.gov/document/support-small-business-procurement-scorecard-overview"
)
CATEGORIES = ("sb", "sdb", "wosb", "sdvosb", "hz")
_TAG = re.compile(r"<[^>]+>")


def _clean_html(value: Any) -> Optional[str]:
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    text = " ".join(text.split())
    return text or None


def _department_code(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    try:
        return f"{int(value):04d}"
    except (TypeError, ValueError):
        return None


def _matches(row: dict[str, Any], query: SourceQuery) -> bool:
    terms = [str(value).strip().casefold()
             for value in (*query.agencies, *query.keywords)
             if str(value).strip()]
    if not terms:
        return False
    code = _department_code(row.get("department_id")) or ""
    haystack = " ".join(str(row.get(key) or "") for key in (
        "department_friendly_name", "department_name", "department_acronym",
    )).casefold()
    return any(term == code or term in haystack for term in terms)


def _category(row: dict[str, Any], category: str) -> dict[str, Any]:
    return {
        "prime_dollars_stated": row.get(f"prime_{category}_dollars"),
        "prime_achievement_stated": row.get(f"prime_{category}_cfy_achievement"),
        "prime_goal_stated": row.get(f"prime_{category}_cfy_goal"),
        "subcontract_dollars_stated": row.get(f"sub_{category}_dollars"),
        "subcontract_achievement_stated": row.get(f"sub_{category}_cfy_achievement"),
        "subcontract_goal_stated": row.get(f"sub_{category}_cfy_goal"),
        "vendor_count_current_stated": row.get(f"category_{category}_cfy_vendor_count"),
        "vendor_count_change_stated": row.get(f"category_{category}_percent_change"),
    }


def _normalize(row: dict[str, Any], year: int, url: str) -> Optional[dict[str, Any]]:
    code = _department_code(row.get("department_id"))
    name = str(row.get("department_friendly_name") or "").strip()
    if not code or not name:
        return None
    return {
        "record_id": f"sba-scorecard:{year}:{code}",
        "fiscal_year": int(row.get("fiscal_year") or year),
        "agency": name,
        "agency_acronym": row.get("department_acronym"),
        "agency_grade": row.get("agency_grade"),
        "agency_score_stated": row.get("agency_score"),
        "prime_total_score_stated": row.get("prime_total_score"),
        "subcontract_total_score_stated": row.get("sub_total_score"),
        "categories": {category: _category(row, category)
                       for category in CATEGORIES},
        "prime_footnotes": _clean_html(row.get("prime_footnotes")),
        "subcontract_footnotes": _clean_html(row.get("sub_footnotes")),
        "_join_keys": {"toptier_agency_code": code, "fiscal_year": year},
        "citation_url": url,
        "semantics": (
            "annual agency small-business goal performance; not a live "
            "opportunity or future set-aside determination"
        ),
    }


class SbaProcurementScorecardsSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(
        self, fetcher: Optional[FetchBytes] = None,
        fiscal_year: int = DEFAULT_FISCAL_YEAR,
    ) -> None:
        self._fetcher = fetcher or fetch_bytes
        self.fiscal_year = fiscal_year

    @property
    def data_url(self) -> str:
        return URL_TEMPLATE.format(year=self.fiscal_year)

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use scorecards()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            blob, _headers = self._fetcher(
                self.data_url, timeout=15.0, retries=1)
            rows = json.loads(blob.decode("utf-8-sig"))
            if isinstance(rows, list) and rows and all(
                    isinstance(row, dict) for row in rows):
                return True, (
                    f"official FY{self.fiscal_year} scorecards reachable "
                    f"({len(rows)} records)"
                )
            return False, "scorecard endpoint returned an unexpected shape"
        except Exception as exc:  # noqa: BLE001
            return False, f"scorecards unavailable: {exc}"

    def scorecards(self, query: SourceQuery) -> dict[str, Any]:
        if not query.agencies and not query.keywords:
            return payload(
                source=self.name, mode=MODE, items=[], status="partial",
                limitations=[
                    "unscoped retrieval refused; pass target agency names, acronyms, or codes",
                ],
            )
        try:
            blob, headers = self._fetcher(
                self.data_url, timeout=30.0, retries=2)
            native = json.loads(blob.decode("utf-8-sig"))
            if not isinstance(native, list):
                raise ValueError("scorecard response is not a JSON list")
        except Exception as exc:  # noqa: BLE001
            return payload(
                source=self.name, mode=MODE, items=[], status="failed",
                limitations=[f"scorecard retrieval or parse failed: {exc}"],
            )
        items: list[dict[str, Any]] = []
        dropped = 0
        for row in native:
            if not isinstance(row, dict):
                dropped += 1
                continue
            if not _matches(row, query):
                continue
            normalized = _normalize(row, self.fiscal_year, self.data_url)
            if normalized is None:
                dropped += 1
            else:
                items.append(normalized)
        limit = max(1, min(query.limit, 25))
        total = len(items)
        items = items[:limit]
        limitations = [
            "scorecards are annual agency-level performance, not opportunity evidence",
            "dollar and percentage values remain publisher-stated strings; no inferred arithmetic",
            "the official yearly JSON is an undocumented SBA web-application asset and may move",
        ]
        if dropped:
            limitations.append(f"ignored {dropped} malformed records")
        if total > limit:
            limitations.append(f"kept first {limit} of {total} scoped records")
        source_receipt = receipt(
            source=self.name, url=self.data_url,
            query={"agencies": query.agencies, "keywords": query.keywords,
                   "fiscal_year": self.fiscal_year, "limit": query.limit},
            blob=blob, normalized=items, headers=headers,
            content_kind="application/json",
        )
        return payload(
            source=self.name, mode=MODE, items=items,
            status="partial" if dropped or total > limit else "complete",
            limitations=limitations, source_receipt=source_receipt,
            total_matched=total,
            public_detail=f"Official SBA FY{self.fiscal_year} procurement scorecards",
        )


try:
    register_source(SbaProcurementScorecardsSource)
except ValueError:
    pass
