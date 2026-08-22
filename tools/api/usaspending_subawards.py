"""USAspending subawards adapter — teaming-target intel (live, no key required).

The prime/sub flow answers the Target stage's money question: which primes
already buy the client's kind of work as subcontracts, and at what volume.
Those primes become teaming targets in the contact plan and the Target report.

API: POST https://api.usaspending.gov/api/v2/search/spending_by_award/
with "subawards": true — same endpoint the main adapter uses, subaward rows back.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from tools.api._http import post_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
# Sub-Award Description is the PRIMARY evidence field (2026-07-09 inversion):
# capability matching runs on what the money was FOR, not which lane filed it
FIELDS = ["Sub-Award ID", "Sub-Awardee Name", "Sub-Award Amount",
          "Prime Recipient Name", "Prime Award ID", "Action Date",
          "Sub-Award Description", "Awarding Agency", "Awarding Sub Agency"]


def _result_rows(
    payload: Any,
    *,
    context: str,
    identity_fields: tuple[str, ...] = ("Sub-Award ID", "Prime Award ID"),
    identity_label: str = "subaward",
) -> list[dict]:
    """Validate the official result envelope before declaring a clean zero."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"{context} response omitted the required results list")
    rows = payload["results"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{context} results contained a non-object row")
    for row in rows:
        if not any(str(row.get(field) or "").strip()
                   for field in identity_fields):
            raise ValueError(
                f"{context} result omitted {identity_label} identity"
            )
    return rows


@register_source
class SubawardsSource(DataSource):
    name = "usaspending_subawards"
    kind = SourceKind.ENRICHMENT


    def healthcheck(self) -> tuple[bool, str]:
        try:
            from tools.api._http import get_json
            get_json("https://api.usaspending.gov/api/v2/references/agency/456/",
                     timeout=5.0, retries=1)
            return True, "api.usaspending.gov reachable"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def edges(self, naics_code: str, agency: str | None = None, *,
              years_back: int = 3, limit: int = 100) -> list[dict]:
        """Prime->sub EDGES for one NAICS lane: which primes issued subawards,
        to whom, at what volume — the 'proven teamer' signal. Full rows, each
        citable (sub-award id, prime award id, amounts, dates)."""
        from tools.api.usaspending import normalize_agency_name
        start = f"{date.today().year - years_back}-01-01"
        filters: dict[str, Any] = {
            "naics_codes": [naics_code],
            "time_period": [{"start_date": start,
                             "end_date": date.today().isoformat()}],
            "award_type_codes": ["A", "B", "C", "D"],
        }
        scope = normalize_agency_name(agency)
        if scope:
            filters["agencies"] = [{"type": "awarding", "tier": "toptier",
                                    "name": scope}]
        payload = {"filters": filters, "fields": FIELDS, "subawards": True,
                   "limit": min(limit, 100), "order": "desc",
                   "sort": "Sub-Award Amount"}
        rows = _result_rows(
            post_json(SEARCH_URL, json=payload), context="USAspending subaward edge"
        )
        return [{
            "subaward_id": r.get("Sub-Award ID"),
            "sub": r.get("Sub-Awardee Name"),
            "amount": r.get("Sub-Award Amount"),
            "prime": r.get("Prime Recipient Name"),
            "prime_award_id": r.get("Prime Award ID"),
            "date": r.get("Action Date"),
            "description": r.get("Sub-Award Description"),
            "awarding_agency": r.get("Awarding Agency"),
            "awarding_sub_agency": r.get("Awarding Sub Agency"),
            "agency_filter_used": scope,
            "source": SEARCH_URL,
        } for r in rows]

    def demand_awards(self, naics_code: str, threshold_usd: float, *,
                      years_back: int = 3, limit: int = 100) -> list[dict]:
        """Subcontracting-DEMAND rows: above-threshold prime awards whose
        recipients USAspending categorizes other_than_small_business. An
        other-than-small awardee is definitionally outside small-business
        set-asides, so these are the awards where a subcontracting plan
        applies — the recipient size is SOURCED from the API's recipient
        business-category filter, never assumed."""
        start = f"{date.today().year - years_back}-01-01"
        payload = {
            "filters": {
                "naics_codes": [naics_code],
                "time_period": [{"start_date": start,
                                 "end_date": date.today().isoformat()}],
                "award_type_codes": ["A", "B", "C", "D"],
                "recipient_type_names": ["other_than_small_business"],
                "award_amounts": [{"lower_bound": threshold_usd}],
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Period of Performance Start Date"],
            "limit": min(limit, 100), "order": "desc", "sort": "Award Amount",
        }
        rows = _result_rows(
            post_json(SEARCH_URL, json=payload),
            context="USAspending subcontracting-demand award",
            identity_fields=("generated_internal_id", "Award ID"),
            identity_label="award",
        )
        return [{
            "award_id": r.get("Award ID"),
            "recipient": r.get("Recipient Name"),
            "amount": r.get("Award Amount"),
            "awarding_agency": r.get("Awarding Agency"),
            "start_date": r.get("Period of Performance Start Date"),
            "size_basis": "recipient_type_names=other_than_small_business "
                          "(USAspending recipient business-category filter)",
            "source": SEARCH_URL,
        } for r in rows]

    def small_awards(self, naics_code: str, *, years_back: int = 3,
                     limit: int = 100) -> list[dict]:
        """Awards in a lane whose recipients USAspending categorizes
        small_business — the citable size source for PRIME_WITH_SUBS partner
        candidates. A company never appears in the small-partner pool without
        this filter behind it."""
        start = f"{date.today().year - years_back}-01-01"
        payload = {
            "filters": {
                "naics_codes": [naics_code],
                "time_period": [{"start_date": start,
                                 "end_date": date.today().isoformat()}],
                "award_type_codes": ["A", "B", "C", "D"],
                "recipient_type_names": ["small_business"],
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Period of Performance Start Date"],
            "limit": min(limit, 100), "order": "desc", "sort": "Award Amount",
        }
        rows = _result_rows(
            post_json(SEARCH_URL, json=payload),
            context="USAspending small-business award",
            identity_fields=("generated_internal_id", "Award ID"),
            identity_label="award",
        )
        return [{
            "award_id": r.get("Award ID"),
            "recipient": r.get("Recipient Name"),
            "amount": r.get("Award Amount"),
            "awarding_agency": r.get("Awarding Agency"),
            "start_date": r.get("Period of Performance Start Date"),
            "size_basis": "recipient_type_names=small_business "
                          "(USAspending recipient business-category filter)",
            "source": SEARCH_URL,
        } for r in rows]

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Aggregate subawards in the client's NAICS lanes.

        Returns {'primes': ranked rollup, 'sample': ...} (legacy keys,
        unchanged) plus 'edges': {naics: [full prime->sub rows]} — the
        commit-1 convention: full lists persisted, never samples.
        """
        if not query.naics_codes:
            return {"primes": [], "sample": [], "edges": {}}
        start = (date.today() - timedelta(days=730)).isoformat()
        payload = {
            "filters": {
                "naics_codes": list(query.naics_codes)[:6],
                "time_period": [{"start_date": start, "end_date": date.today().isoformat()}],
                "award_type_codes": ["A", "B", "C", "D"],
            },
            "fields": FIELDS,
            "subawards": True,
            "limit": 100,
            "order": "desc",
            "sort": "Sub-Award Amount",
        }
        data = post_json(SEARCH_URL, json=payload)
        rows = _result_rows(data, context="USAspending subaward search")

        primes: dict[str, dict] = defaultdict(lambda: {"subaward_count": 0, "total": 0.0})
        for r in rows:
            prime = r.get("Prime Recipient Name") or "UNKNOWN"
            primes[prime]["subaward_count"] += 1
            primes[prime]["total"] += float(r.get("Sub-Award Amount") or 0)
        ranked = [
            {"name": k, **v} for k, v in
            sorted(primes.items(), key=lambda kv: kv[1]["total"], reverse=True)
        ][:15]
        sample = [
            {"prime": r.get("Prime Recipient Name"), "sub": r.get("Sub-Awardee Name"),
             "amount": r.get("Sub-Award Amount"), "date": r.get("Action Date")}
            for r in rows[:10]
        ]
        edges: dict[str, list] = {}
        for code in list(query.naics_codes)[:6]:
            try:
                edges[code] = self.edges(code)
            except Exception as e:  # noqa: BLE001 — additive, never fatal
                edges[code] = [{"error": str(e)}]
        return {"primes": ranked, "sample": sample, "edges": edges}
