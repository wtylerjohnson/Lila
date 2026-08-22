"""USAspending.gov adapter — enrichment + corroboration (live, no key required).

Used to VERIFY and contextualize a SAM.gov opportunity against real spending:
who actually wins this kind of work, at what dollar values, and whether the issuing
agency genuinely buys this NAICS. Feeds the LLM fit-logic as market evidence.

API: https://api.usaspending.gov/  (public, no auth)
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any, Optional  # noqa: F401

from tools.api._http import post_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.query_terms import capability_query_terms

AWARD_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AGENCY_CATEGORY_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_agency/"
SUBAGENCY_CATEGORY_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_subagency/"


def _category_url(agency: "str | None") -> str:
    """The category lives in the URL PATH (2026-07-10 fix: a body field
    named category is ignored). Scoped pulls break down by SUB-agency so
    a departmental focus lists its components."""
    return SUBAGENCY_CATEGORY_URL if agency else AGENCY_CATEGORY_URL
# Contract award type codes (A/B/C/D) — excludes grants/loans/IDV.
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]
AWARD_LINK = "https://www.usaspending.gov/award/"

# USAspending toptier names use lowercase connectives ("Department of Defense").
# str.title() produced "Department Of Defense", which matches NOTHING — the
# reason agency-scoped evidence fell back to NAICS-wide in every recorded run.
_LOWER_WORDS = {"of", "the", "and", "for", "in", "on"}

# Common SAM/strategy spellings -> exact USAspending toptier names.
_TOPTIER_ALIASES = {
    "DEPT OF DEFENSE": "Department of Defense",
    "DEPARTMENT OF DEFENSE": "Department of Defense",
    "DOD": "Department of Defense",
    "HOMELAND SECURITY, DEPARTMENT OF": "Department of Homeland Security",
    "DHS": "Department of Homeland Security",
    "JUSTICE, DEPARTMENT OF": "Department of Justice",
    "DOJ": "Department of Justice",
    "VETERANS AFFAIRS, DEPARTMENT OF": "Department of Veterans Affairs",
    "INTERIOR, DEPARTMENT OF THE": "Department of the Interior",
    "COMMERCE, DEPARTMENT OF": "Department of Commerce",
    "ENERGY, DEPARTMENT OF": "Department of Energy",
    "STATE, DEPARTMENT OF": "Department of State",
    "TREASURY, DEPARTMENT OF THE": "Department of the Treasury",
    "TRANSPORTATION, DEPARTMENT OF": "Department of Transportation",
    "AGRICULTURE, DEPARTMENT OF": "Department of Agriculture",
    "LABOR, DEPARTMENT OF": "Department of Labor",
    "HEALTH AND HUMAN SERVICES, DEPARTMENT OF": "Department of Health and Human Services",
    "GENERAL SERVICES ADMINISTRATION": "General Services Administration",
    "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION": "National Aeronautics and Space Administration",
    "ENVIRONMENTAL PROTECTION AGENCY": "Environmental Protection Agency",
}


def _smart_title(text: str) -> str:
    words = text.strip().split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if (i > 0 and lw in _LOWER_WORDS) else lw.capitalize())
    return " ".join(out)


def normalize_agency_name(agency: str | None) -> Optional[str]:
    """Best-effort map a SAM fullParentPathName or strategy agency string to a
    USAspending toptier name.

    SAM:         'VETERANS AFFAIRS, DEPARTMENT OF.VA TECHNOLOGY ACQUISITION CENTER'
    USAspending: 'Department of Veterans Affairs'
    Returns None if it can't confidently transform — callers fall back to NAICS-only.
    """
    if not agency:
        return None
    head = agency.split(".")[0].strip()  # top segment before the org path
    alias = _TOPTIER_ALIASES.get(head.upper())
    if alias:
        return alias
    # "<NAME>, DEPARTMENT OF" -> "Department of <Name>"
    if "," in head and head.upper().endswith("DEPARTMENT OF"):
        name = _smart_title(head.split(",")[0])
        return f"Department of {name}"
    return _smart_title(head)


def _build_filters(naics_code: str, agency: str | None, years_back: int,
                   keywords: list[str] | None = None) -> dict:
    today = date.today()
    end = today.isoformat()
    start = f"{today.year - years_back}-01-01"
    filters: dict[str, Any] = {
        "award_type_codes": CONTRACT_TYPE_CODES,
        "naics_codes": [naics_code],
        "time_period": [{"start_date": start, "end_date": end}],
    }
    if agency:
        filters["agencies"] = [
            {"type": "awarding", "tier": "toptier", "name": agency}
        ]
    if keywords:
        filters["keywords"] = keywords
    return filters


def addressable_terms(keywords: list) -> list[str]:
    """Deterministic query terms for the keyword-matched (addressable) slice.

    Accepts strategy Keyword objects or their dicts. Keeps product/technology
    categories only, splits slash-compounds into atomic phrases, drops
    fragments under the API's 3-char minimum, dedupes case-insensitively,
    caps at 20 terms. The terms used are persisted with every result, so the
    scoping is always inspectable."""
    return capability_query_terms(keywords, limit=20)


def _result_rows(
    payload: Any,
    *,
    context: str,
    row_kind: str = "award",
) -> list[dict]:
    """Return a validated USAspending result list.

    A 2xx response with an error/message envelope is not a screened zero.  The
    coverage layer treats a clean empty list as complete within its boundary,
    so adapters must distinguish the official ``results: []`` shape from an
    unexpected response schema before returning.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(
            f"{context} response omitted the required results list"
        )
    rows = payload["results"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{context} results contained a non-object row")
    for row in rows:
        if row_kind == "award":
            if not str(
                row.get("generated_internal_id") or row.get("Award ID") or ""
            ).strip():
                raise ValueError(f"{context} result omitted award identity")
        elif row_kind == "category":
            amount = row.get("amount")
            if (
                not str(row.get("name") or "").strip()
                or not str(row.get("code") or "").strip()
                or isinstance(amount, bool)
                or not isinstance(amount, (int, float))
            ):
                raise ValueError(
                    f"{context} category result omitted name, code, or "
                    "numeric amount"
                )
        else:
            raise ValueError(f"unsupported USAspending row kind: {row_kind}")
    return rows


def parse_awards(payload: dict) -> list[dict]:
    """Normalize spending_by_award results into compact, citable records."""
    out: list[dict] = []
    for r in _result_rows(payload, context="USAspending award search"):
        internal_id = r.get("generated_internal_id")
        out.append(
            {
                "award_id": r.get("Award ID"),
                "recipient": r.get("Recipient Name"),
                "amount": r.get("Award Amount"),
                "awarding_agency": r.get("Awarding Agency"),
                "start_date": r.get("Period of Performance Start Date"),
                "url": f"{AWARD_LINK}{internal_id}" if internal_id else None,
            }
        )
    return out


def summarize_market(awards: list[dict]) -> dict:
    """Aggregate evidence the fit-logic can cite: incumbents and typical dollar size."""
    amounts = [a["amount"] for a in awards if isinstance(a.get("amount"), (int, float))]
    recipients = [a["recipient"] for a in awards if a.get("recipient")]
    top_incumbents = [
        name for name, _ in _counts(recipients)[:5]
    ]
    return {
        "award_count": len(awards),
        "total_obligated": round(sum(amounts), 2) if amounts else 0.0,
        "median_award": round(statistics.median(amounts), 2) if amounts else None,
        "max_award": max(amounts) if amounts else None,
        "top_incumbents": top_incumbents,
    }


def _counts(items: list[str]) -> list[tuple[str, int]]:
    tally: dict[str, int] = {}
    for i in items:
        tally[i] = tally.get(i, 0) + 1
    return sorted(tally.items(), key=lambda kv: kv[1], reverse=True)


@register_source
class UsaSpendingSource(DataSource):
    name = "usaspending.gov"
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
        raise NotImplementedError("USAspending is an enrichment source — use award_history().")

    def award_history(
        self, naics_code: str, agency: str | None = None, limit: int = 50, years_back: int = 3
    ) -> list[dict]:
        """Comparable historical contract awards for a NAICS (optionally by agency)."""
        body = {
            "filters": _build_filters(naics_code, agency, years_back),
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Awarding Agency",
                "Period of Performance Start Date",
            ],
            "limit": min(limit, 100),
            "sort": "Award Amount",
            "order": "desc",
        }
        return parse_awards(post_json(AWARD_SEARCH_URL, json=body))

    def agency_breakdown(self, naics_code: str, *, agency: str | None = None,
                         limit: int = 10, years_back: int = 3) -> list[dict]:
        """Per-agency obligated dollars for a NAICS — the TAM agency map's
        ground truth. One aggregate call covers every agency at once (the
        spending_by_category endpoint), so 'DHS has $X out in your lane' is a
        citable figure, not a top-50 sample sum.

        AGENCY PROJECTION (2026-07-10): with an agency filter, the breakdown
        switches to SUB-agency so a departmental focus lists its components
        (CBP, TSA, ...) instead of restating the department — the focused
        assessment derives from the same strategy, scoped, never in
        conflict with it."""
        body = {
            "filters": _build_filters(naics_code, agency, years_back),
            "limit": min(limit, 25),
            "page": 1,
        }
        payload = post_json(_category_url(agency), json=body)
        return [
            {"agency": r.get("name"), "total_obligated": r.get("amount"),
             "agency_code": r.get("code"),
             "source": AGENCY_CATEGORY_URL}
            for r in _result_rows(
                payload,
                context="USAspending agency-category search",
                row_kind="category",
            )
            if r.get("name")
        ]

    def keyword_slice(self, naics_code: str, keywords: list[str], *,
                      agency: str | None = None,
                      years_back: int = 3, award_limit: int = 25) -> dict:
        """The ADDRESSABLE slice of one NAICS lane: obligations on awards
        matching any of the client's capability/technology keywords. Totals
        come from the aggregate endpoint (complete within the top 25 awarding
        agencies, so slightly conservative — never inflated); a small award
        list rides along for provenance."""
        body = {
            "filters": _build_filters(naics_code, agency, years_back, keywords=keywords),
            "limit": 25,
            "page": 1,
        }
        payload = post_json(_category_url(agency), json=body)
        breakdown = [
            {"agency": r.get("name"), "total_obligated": r.get("amount"),
             "agency_code": r.get("code"), "source": AGENCY_CATEGORY_URL}
            for r in _result_rows(
                payload,
                context="USAspending keyword-category search",
                row_kind="category",
            )
            if r.get("name")
        ]
        total = round(sum(r["total_obligated"] for r in breakdown
                          if isinstance(r.get("total_obligated"), (int, float))), 2)
        awards_body = {
            "filters": _build_filters(naics_code, agency, years_back, keywords=keywords),
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Period of Performance Start Date"],
            "limit": min(award_limit, 100),
            "sort": "Award Amount",
            "order": "desc",
        }
        awards = parse_awards(post_json(AWARD_SEARCH_URL, json=awards_body))
        return {
            "keywords_used": keywords,
            "total_obligated": total,
            "window_years": years_back,
            "agency_breakdown": breakdown,
            "awards": awards,
            "source": AGENCY_CATEGORY_URL,
        }

    def expiring_awards(self, naics_code: str, *, window_days: int = 548,
                        years_back: int = 5, limit: int = 100,
                        max_pages: int = 5) -> list[dict]:
        """Contracts in the lane whose period of performance ends inside the
        forward window — the recompete calendar, visible before any notice
        posts. Richer than the SAM contract-awards slice (dollars, agency,
        citable award URL) and quota-free.

        Basis is honest about its blind spot: the pull covers awards ACTED ON
        in the last `years_back` years (spending_by_award filters on action
        date, not end date), so a long-running contract untouched for longer
        can be missed. Every row carries its award-profile URL."""
        today = date.today()
        window_end = today + timedelta(days=window_days)
        body = {
            "filters": _build_filters(naics_code, None, years_back),
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Awarding Sub Agency",
                       "Start Date", "End Date", "NAICS", "PSC",
                       "Description", "generated_internal_id"],
            "limit": min(limit, 100),
            "sort": "Award Amount",
            "order": "desc",
        }
        page_size = min(limit, 100)
        rows: list[dict] = []
        seen: set[str] = set()
        for page in range(1, max(1, max_pages) + 1):
            payload = post_json(AWARD_SEARCH_URL, json={
                **body, "limit": page_size, "page": page,
            })
            batch = _result_rows(
                payload, context="USAspending expiring-award search"
            )
            for r in batch:
                end = r.get("End Date")
                if not end or not (
                    today.isoformat() <= end <= window_end.isoformat()
                ):
                    continue
                internal_id = r.get("generated_internal_id")
                key = str(internal_id or (
                    r.get("Award ID"), end, r.get("Recipient Name")
                ))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "award_id": r.get("Award ID"),
                    "generated_internal_id": internal_id,
                    "recipient": r.get("Recipient Name"),
                    "amount": r.get("Award Amount"),
                    "amount_basis": "USAspending Award Amount",
                    "awarding_agency": r.get("Awarding Agency"),
                    "awarding_sub_agency": r.get("Awarding Sub Agency"),
                    "start_date": r.get("Start Date"),
                    "end_date": end,
                    "naics": naics_code,
                    "psc": r.get("PSC"),
                    # capability matching runs on the award description
                    # (2026-07-09 inversion); lanes are boundary only
                    "description": r.get("Description"),
                    "url": f"{AWARD_LINK}{internal_id}" if internal_id else None,
                    "retrieved_page": page,
                })
            page_meta = payload.get("page_metadata") or {}
            has_next = page_meta.get("hasNext")
            if has_next is False or len(batch) < page_size:
                break
        rows.sort(key=lambda x: x["end_date"])
        return rows

    def recipient_awards(self, recipient_name: str, *, years_back: int = 7,
                         limit: int = 50) -> list[dict]:
        """The client's OWN federal awards — proven delivery, cited. The
        pipeline undersold Osprey ("one $50K State PO") because nothing ever
        queried the client's award history; this is that query. Name match is
        USAspending's recipient_search_text (matches legal-name variants)."""
        today = date.today()
        body = {
            "filters": {
                "award_type_codes": CONTRACT_TYPE_CODES,
                "recipient_search_text": [recipient_name],
                "time_period": [{
                    "start_date": f"{today.year - years_back}-01-01",
                    "end_date": today.isoformat()}],
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Awarding Sub Agency",
                       "Start Date", "End Date", "NAICS", "Description"],
            "limit": min(limit, 100),
            "sort": "Award Amount",
            "order": "desc",
        }
        payload = post_json(AWARD_SEARCH_URL, json=body)
        rows: list[dict] = []
        for r in _result_rows(
            payload, context="USAspending recipient-award search"
        ):
            internal_id = r.get("generated_internal_id")
            rows.append({
                "award_id": r.get("Award ID"),
                "recipient": r.get("Recipient Name"),
                "amount": r.get("Award Amount"),
                "awarding_agency": r.get("Awarding Agency"),
                "awarding_sub_agency": r.get("Awarding Sub Agency"),
                "start_date": r.get("Start Date"),
                "end_date": r.get("End Date"),
                "naics": r.get("NAICS"),
                "description": r.get("Description"),
                "url": f"{AWARD_LINK}{internal_id}" if internal_id else None,
            })
        return rows

    def market_evidence(
        self, naics_code: str, agency: str | None = None,
        keywords: list[str] | None = None,
    ) -> dict:
        """Citable corroboration bundle for the fit-logic: top awards + market summary.

        USAspending filters on toptier agency NAMES, which won't match a SAM
        fullParentPathName. If an agency-scoped query returns nothing, fall back to
        NAICS-only so the evidence is never empty, and record the scope used.
        Persists the FULL award list (not just 5 samples) so per-agency and
        per-competitor rollups can be recomputed offline, plus the per-agency
        aggregate breakdown for the TAM math.
        """
        scope = normalize_agency_name(agency)
        awards = self.award_history(naics_code, agency=scope, limit=50)
        if scope and not awards:
            awards = self.award_history(naics_code, agency=None, limit=50)
            scope = None  # fell back to NAICS-wide
        try:
            breakdown = self.agency_breakdown(naics_code, agency=scope)
        except Exception as e:  # noqa: BLE001 — aggregate is additive, never fatal
            breakdown = [{"error": str(e)}]
        addressable = None
        if keywords:
            try:
                addressable = self.keyword_slice(naics_code, keywords,
                                                 agency=scope)
            except Exception as e:  # noqa: BLE001 — additive, never fatal
                addressable = {"error": str(e), "keywords_used": keywords}
        return {
            "naics_code": naics_code,
            "agency_filter_used": scope,
            "agency_requested": agency,
            "summary": summarize_market(awards),
            "sample_awards": awards[:5],
            "awards": awards,
            "agency_breakdown": breakdown,
            "addressable": addressable,
        }


# --------------------------------------------------------------------------- #
# Resolving a bare identifier to the award it names
# --------------------------------------------------------------------------- #
# TWO API RULES, LEARNED BY READING A 422 INSTEAD OF GUESSING AT ONE (2026-08-07).
#
# 1 AWARD TYPE CODES MUST COME FROM ONE GROUP. Contracts (A/B/C/D) and IDVs
#   (IDV_*) are separate groups, and a body mixing them is rejected outright
#   with "must only contain types from one group". A PIID can be either, so
#   resolving one identifier means asking twice, once per group, and keeping
#   whichever answers.
#
# 2 THE SORT KEY MUST BE PRESENT IN `fields`. Sorting by "Award Amount" while
#   omitting it from the requested fields is also a 422, and the message does
#   not say so. `AWARD_FIELDS` therefore always carries the sort key.
#
# Seventeen identifiers returned 422 for both reasons before this was written,
# which reads identically to "these awards do not exist". Fifteen of them are
# real awards worth $85,549,824.90.
AWARD_TYPE_GROUPS = {
    "contract": ["A", "B", "C", "D"],
    "idv": ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C",
            "IDV_D", "IDV_E"],
}

AWARD_FIELDS = ["Award ID", "Recipient Name", "Award Amount",
                "Awarding Agency", "Awarding Sub Agency", "Start Date",
                "End Date", "Description"]

AWARD_SORT = "Award Amount"


def resolve_award(piid: str, *, since: str = "2015-01-01",
                  until: str = "2026-12-31", poster: Any = None) -> list:
    """Every award record carrying this PIID, across both type groups.

    Returns [] when the identifier names no award. That is a real answer and
    not a failure: a sources-sought number such as N0003026SSN7001 is a
    NOTICE, and asking an award API for it correctly returns nothing.
    """
    send = poster or post_json
    identifier = " ".join(str(piid or "").split())
    if not identifier:
        return []
    rows: list = []
    failures: list[str] = []
    for kind, codes in AWARD_TYPE_GROUPS.items():
        body = {
            "filters": {"award_type_codes": codes, "award_ids": [identifier],
                        "time_period": [{"start_date": since,
                                         "end_date": until}]},
            "fields": AWARD_FIELDS, "limit": 25,
            "sort": AWARD_SORT, "order": "desc",
        }
        try:
            payload = send(AWARD_SEARCH_URL, json=body)
            group_rows = _result_rows(
                payload,
                context=f"USAspending {kind} PIID resolution",
            )
        except Exception as exc:                              # noqa: BLE001
            failures.append(f"{kind}: {exc}")
            continue
        for row in group_rows:
            row = dict(row)
            row["_award_group"] = kind
            rows.append(row)
    if failures:
        raise RuntimeError(
            "USAspending PIID resolution was incomplete: "
            + "; ".join(failures)
        )
    rows.sort(key=lambda r: -float(r.get(AWARD_SORT) or 0))
    return rows


def resolve_awards(piids: Any, **kw) -> dict:
    """Bulk PIID resolution. Absent identifiers are simply absent."""
    out: dict = {}
    for piid in dict.fromkeys(str(p or "").strip() for p in (piids or [])):
        if not piid:
            continue
        rows = resolve_award(piid, **kw)
        if rows:
            out[piid] = rows
    return out


def recipient_awards(name: str, *, since: str = "2019-01-01",
                     until: str = "2026-12-31", limit: int = 50,
                     poster: Any = None) -> list:
    """Every federal award a named company holds as prime recipient.

    THE QUESTION A COMPETITION SECTION ACTUALLY HAS TO ANSWER. Naming a
    product rival is the market's answer; whether that rival is WINNING
    FEDERAL WORK is a different question, and it is answerable for free.
    Measured on the apexanalytix rival set, 5 of 10 named rivals hold
    $281,185,258.63 in federal awards while apexanalytix holds none. That
    contrast is the finding, and no amount of award co-occurrence would
    have produced it.

    Uses `recipient_search_text`, which matches the recipient name rather
    than an identifier, so a rival with no known UEI still resolves.
    """
    send = poster or post_json
    company = " ".join(str(name or "").split())
    if not company:
        return []
    rows: list = []
    failures: list[str] = []
    for kind, codes in AWARD_TYPE_GROUPS.items():
        body = {
            "filters": {"award_type_codes": codes,
                        "recipient_search_text": [company],
                        "time_period": [{"start_date": since,
                                         "end_date": until}]},
            "fields": AWARD_FIELDS, "limit": min(limit, 100),
            "sort": AWARD_SORT, "order": "desc",
        }
        try:
            payload = send(AWARD_SEARCH_URL, json=body)
            group_rows = _result_rows(
                payload,
                context=f"USAspending {kind} recipient-award search",
            )
        except Exception as exc:                              # noqa: BLE001
            failures.append(f"{kind}: {exc}")
            continue
        for row in group_rows:
            row = dict(row)
            row["_award_group"] = kind
            rows.append(row)
    if failures:
        raise RuntimeError(
            "USAspending recipient-award search was incomplete: "
            + "; ".join(failures)
        )
    rows.sort(key=lambda r: -float(r.get(AWARD_SORT) or 0))
    return rows
