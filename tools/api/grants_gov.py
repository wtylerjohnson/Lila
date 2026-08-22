"""Grants.gov adapter — assistance-side demand (keyless, no quota).

Contracts are half the federal wallet. Grants surface the OTHER demand lane:
state/local cyber grants, research programs, infrastructure money that flows
to buyers who then procure the client's category. A forecasted grant program
is budget already committed — early, citable demand signal.

API (grounded from https://www.grants.gov/api/ — Search2 endpoint, no key):
    POST https://api.grants.gov/v1/api/search2
        {"keyword": "...", "rows": N, "oppStatuses": "forecasted|posted"}
    Response: {"data": {"oppHits": [{"id","number","title","agencyName",
               "openDate","closeDate","oppStatus"}], "hitCount": N}}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.api._http import post_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
ROWS_PER_PAGE = 100
MAX_KEYWORDS = 6
MAX_PAGES_PER_KEYWORD = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(value: Any) -> str | None:
    """Normalize the two date shapes documented/observed from Search2."""
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


@register_source
class GrantsGovSource(DataSource):
    name = "grants_gov"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            post_json(SEARCH_URL, json={"keyword": "test", "rows": 1},
                      timeout=8.0, retries=1)
            return True, "api.grants.gov reachable (keyless)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Open + forecasted grant programs per keyword lane."""
        retrieved = _utc_now()
        retrieved_at = retrieved.isoformat(timespec="seconds")
        data_as_of = retrieved.date().isoformat()
        out: dict[str, Any] = {}
        program_rows: dict[str, dict[str, Any]] = {}
        source_attempts: list[dict[str, Any]] = []
        limitations: list[str] = []
        keywords = list(query.keywords or [])
        selected_keywords = keywords[:MAX_KEYWORDS]
        if len(selected_keywords) < len(keywords):
            limitations.append(
                f"keyword lanes capped at {MAX_KEYWORDS} of {len(keywords)}"
            )
        for kw in selected_keywords:
            rows: list[dict[str, Any]] = []
            hit_count = 0
            received = 0
            pages = 0
            count_known = False
            paging_uncertain = False
            for page in range(MAX_PAGES_PER_KEYWORD):
                start = page * ROWS_PER_PAGE
                payload = post_json(SEARCH_URL, json={
                    "keyword": kw.strip('"'),
                    "rows": ROWS_PER_PAGE,
                    "startRecordNum": start,
                    "oppStatuses": "forecasted|posted",
                })
                data = payload.get("data") or {}
                hits = data.get("oppHits") or []
                raw_count = data.get("hitCount")
                try:
                    hit_count = max(hit_count, int(raw_count))
                    count_known = True
                except (TypeError, ValueError):
                    if not count_known:
                        hit_count = max(hit_count, start + len(hits))
                pages += 1
                received += len(hits)
                for h in hits:
                    if not isinstance(h, dict):
                        continue
                    agency_code = str(h.get("agencyCode") or "").strip()
                    agency_name = str(
                        h.get("agencyName") or h.get("agency") or ""
                    ).strip()
                    agency = agency_code or agency_name
                    open_date = _iso_date(h.get("openDate"))
                    close_date = _iso_date(h.get("closeDate"))
                    oid = h.get("id")
                    url = (
                        f"https://www.grants.gov/search-results-detail/{oid}"
                        if oid else None
                    )
                    legacy = {
                        "id": oid,
                        "number": h.get("number"),
                        "title": h.get("title"),
                        "agency": agency,
                        "status": h.get("oppStatus"),
                        "open": open_date,
                        "close": close_date,
                        "url": url,
                    }
                    if agency_code:
                        legacy["agency_code"] = agency_code
                    if agency_name:
                        legacy["agency_name"] = agency_name
                    rows.append(legacy)
                    title = str(h.get("title") or "").strip()
                    if oid and url and agency and title:
                        identity = f"grants_gov:{oid}"
                        normalized = {
                            "record_id": identity,
                            "canonical_url": url,
                            "source": "grants_gov",
                            "tier": "program",
                            "kind": "assistance_opportunity",
                            "agency": agency,
                            "title": title,
                            "number": h.get("number"),
                            "status": h.get("oppStatus"),
                            "open_date": open_date,
                            "close_date": close_date,
                            "matched_terms": [],
                            "promotion_eligible": False,
                            "live_solicitation": False,
                            "retrieved_at": retrieved_at,
                            "data_as_of": data_as_of,
                        }
                        if agency_code:
                            normalized["agency_code"] = agency_code
                        if agency_name:
                            normalized["agency_name"] = agency_name
                        program = program_rows.setdefault(identity, normalized)
                        if kw not in program["matched_terms"]:
                            program["matched_terms"].append(kw)
                if not count_known:
                    if len(hits) >= ROWS_PER_PAGE:
                        paging_uncertain = True
                    break
                if not hits or start + len(hits) >= hit_count:
                    break
            if received < hit_count:
                limitations.append(
                    f"{kw!r} paging capped at {received} of {hit_count} hits"
                )
            if paging_uncertain:
                limitations.append(
                    f"{kw!r} returned a full page without Search2 hitCount; "
                    "additional pages cannot be proven absent"
                )
            attempt_partial = received < hit_count or paging_uncertain
            source_attempts.append({
                "source": "search2",
                "keyword": kw,
                "status": "partial" if attempt_partial else "success",
                "pages": pages,
                "records": received,
                "hit_count": hit_count,
            })
            if rows:
                out[kw] = rows
        records = sorted(
            program_rows.values(),
            key=lambda row: (
                str(row.get("close_date") or "9999-12-31"),
                str(row.get("title") or ""),
                str(row.get("record_id") or ""),
            ),
        )
        out["records"] = records
        partial = bool(limitations)
        out["_provenance"] = {
            "source": "grants_gov",
            "mode": "live_official_api",
            "status": "partial" if partial else "success",
            "tier": "program",
            "promotion_eligible": False,
            "retrieved_at": retrieved_at,
            "data_as_of": data_as_of,
            "stale": False,
            "partial": partial,
            "records_received": len(records),
            "records_matched": len(records),
            "consumer": "prospective_horizon",
            "source_attempts": source_attempts,
        }
        if limitations:
            out["_provenance"]["limitations"] = "; ".join(limitations)
        return out
