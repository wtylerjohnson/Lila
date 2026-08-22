"""ForeignAssistance.gov budget API adapter (PROGRAM-tier funded demand).

The source is the governmentwide foreign-assistance platform. This adapter
pulls President's Budget Request rows grouped by funding agency and account,
selects the latest fiscal year present, and matches them locally. Agency
identity is retained so bounded client scopes can exclude defense or other
out-of-scope rows. Requests are upstream demand signals, not solicitations.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.program_cache import ProgramPull, cached_program_pull
from tools.text_match import matching_phrases

API_URL = "https://foreignassistance.gov/data-api/by-funding-agency.json"
DATA_URL = "https://foreignassistance.gov/data"
PRESIDENT_BUDGET_REQUEST = 18
PAGE_SIZE = 10_000
DEFAULT_MAX_PAGES = 20
MAX_PAGES = int(
    os.environ.get("LILA_FOREIGN_ASSISTANCE_MAX_PAGES", str(DEFAULT_MAX_PAGES))
)
_DEFAULT_CACHE = (
    Path(__file__).resolve().parents[2] / "data" / "cache" / "foreign_assistance"
)


def _cache_dir() -> Path:
    return Path(
        os.environ.get("LILA_FOREIGN_ASSISTANCE_CACHE_DIR", str(_DEFAULT_CACHE))
    )


def _rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("ForeignAssistance API response must be an object")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("ForeignAssistance API response has no data list")
    return [row for row in rows if isinstance(row, dict)]


def _total_pages(payload: Any, *, expected_page: int) -> int:
    """Validate pagination so page one cannot masquerade as a census."""
    if not isinstance(payload, dict):
        raise ValueError("ForeignAssistance API response must be an object")
    page_info = payload.get("page_info")
    if not isinstance(page_info, dict):
        raise ValueError("ForeignAssistance response has no page_info object")
    raw_total = page_info.get("total_pages")
    if isinstance(raw_total, bool):
        raise ValueError("ForeignAssistance total_pages is invalid")
    try:
        total = int(raw_total)
    except (TypeError, ValueError):
        raise ValueError("ForeignAssistance total_pages is invalid") from None
    if total < 1:
        raise ValueError("ForeignAssistance total_pages must be positive")
    raw_current = page_info.get("current_page")
    if raw_current is not None:
        try:
            current = int(raw_current)
        except (TypeError, ValueError):
            raise ValueError(
                "ForeignAssistance current_page is invalid"
            ) from None
        if current != expected_page:
            raise ValueError(
                "ForeignAssistance current_page does not match request"
            )
    return total


def _record_id(row: dict) -> str:
    if row.get("id") is not None:
        return f"foreign-assistance:{row['id']}"
    basis = "\x1f".join(
        str(row.get(key) or "")
        for key in (
            "funding_agency_id",
            "funding_account_id",
            "country_code",
            "transaction_type_id",
            "fiscal_year",
        )
    )
    return "foreign-assistance:" + hashlib.sha256(basis.encode()).hexdigest()[:20]


def _normalize_latest(rows: list[dict]) -> tuple[list[dict], str]:
    fiscal_years = [
        int(str(row.get("fiscal_year")))
        for row in rows
        if str(row.get("fiscal_year") or "").isdigit()
    ]
    if not fiscal_years:
        raise ValueError("ForeignAssistance budget response has no fiscal year")
    latest = max(fiscal_years)
    data_as_of = f"FY{latest}"
    records = []
    for row in rows:
        if str(row.get("fiscal_year")) != str(latest):
            continue
        agency = str(row.get("funding_agency_name") or "").strip()
        if not agency:
            continue
        account = (
            row.get("funding_account_name")
            or "Unspecified foreign-assistance funding account"
        )
        country = row.get("country_name") or "Country/region not stated"
        records.append(
            {
                "record_id": _record_id(row),
                "canonical_url": DATA_URL,
                "kind": "budget",
                "title": f"{account} - {country} - President's Budget Request",
                "agency": agency,
                "funding_agency_id": row.get("funding_agency_id"),
                "funding_agency_acronym": row.get("funding_agency_acronym"),
                "funding_account_id": row.get("funding_account_id"),
                "funding_account": account,
                "transaction_type": row.get("transaction_type_name"),
                "fiscal_year": str(latest),
                "country_code": row.get("country_code"),
                "country": country,
                "current_amount": row.get("current_amount"),
                "constant_amount": row.get("constant_amount"),
                "data_as_of": data_as_of,
            }
        )
    if not records:
        raise ValueError("ForeignAssistance latest fiscal year contained no budget rows")
    return records, data_as_of


def _fetch_live() -> ProgramPull:
    all_rows: list[dict] = []
    attempts: list[dict] = []
    total_pages: int | None = None
    page = 1
    while page <= min(total_pages or 1, MAX_PAGES):
        payload = get_json(
            API_URL,
            params={
                "transaction_type_id": PRESIDENT_BUDGET_REQUEST,
                "per_page": PAGE_SIZE,
                "page": page,
            },
        )
        batch = _rows(payload)
        all_rows.extend(batch)
        reported_total = _total_pages(payload, expected_page=page)
        if total_pages is None:
            total_pages = reported_total
        elif reported_total != total_pages:
            raise ValueError(
                "ForeignAssistance total_pages changed during pagination"
            )
        attempts.append(
            {
                "source": "foreign_assistance_api",
                "status": "success",
                "page": page,
                "records": len(batch),
            }
        )
        page += 1
    records, data_as_of = _normalize_latest(all_rows)
    if total_pages is None:
        raise ValueError("ForeignAssistance pagination did not run")
    truncated = total_pages > MAX_PAGES
    return ProgramPull(
        records,
        data_as_of,
        partial=truncated,
        source_attempts=attempts,
        limitations=(
            "President's Budget Requests are neither enacted allocations nor "
            "solicitations; records are funding-agency/account aggregates"
            + (
                f"; API paging was capped at {MAX_PAGES} of {total_pages} pages"
                if truncated
                else ""
            )
        ),
    )


def _matches(row: dict, query: SourceQuery) -> bool:
    haystack = " ".join(str(value) for value in row.values() if value is not None)
    keywords = [word.strip('"') for word in query.keywords if word.strip('"')]
    if keywords and not matching_phrases(haystack, keywords):
        return False
    if not query.agencies:
        return True

    # Scope before ranking/capping.  Filtering after ``query.limit`` allowed a
    # government-wide high-dollar row to displace every relevant agency row.
    from tools.agencies import find, matches_record

    agency_text = str(
        row.get("agency") or row.get("funding_agency_name") or "")
    for requested in query.agencies:
        agency = find(requested)
        if agency and matches_record(agency_text, agency):
            return True
        if not agency and matching_phrases(agency_text, [requested]):
            return True
    return False


def _assistance_rank(row: dict) -> tuple:
    value = row.get("current_amount")
    amount = (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else 0.0
    )
    return (
        -amount,
        str(row.get("agency") or "").casefold(),
        str(row.get("title") or "").casefold(),
        str(row.get("record_id") or ""),
    )


@register_source
class ForeignAssistanceSource(DataSource):
    name = "foreign_assistance"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            payload = get_json(
                API_URL,
                params={
                    "transaction_type_id": PRESIDENT_BUDGET_REQUEST,
                    "per_page": 10,
                    "page": 1,
                },
                timeout=8.0,
                retries=1,
            )
            return True, f"official API returned {len(_rows(payload))} budget rows"
        except Exception as exc:  # noqa: BLE001
            return False, f"ForeignAssistance.gov API unavailable: {exc}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("PROGRAM-tier enrichment source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        records, provenance = cached_program_pull(
            source=self.name,
            canonical_url=DATA_URL,
            cache_dir=_cache_dir(),
            fetch_live=_fetch_live,
        )
        candidates = [row for row in records if _matches(row, query)]
        candidates.sort(key=_assistance_rank)
        matched = candidates[: query.limit]
        return {
            "records": matched,
            "_provenance": {
                **provenance,
                "records_matched": len(candidates),
                "candidate_total": len(candidates),
                "selected_count": len(matched),
                "truncated": len(matched) < len(candidates),
                "selection_order": "largest current budget request first",
            },
        }
