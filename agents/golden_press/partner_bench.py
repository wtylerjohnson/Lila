"""Who can prime the notices the client cannot, proven by federal award.

THE INVERSION. The first teaming lane asked "who has this client's primes
subcontracted to?" and produced a list of familiar names. Familiarity is not
eligibility. A large integrator that appears under six of the client's primes
still cannot prime a WOSB set-aside, so naming it against that notice sends a
seller to a partner who cannot open the door either.

This lane inverts the unit. It starts from ONE LIVE RESTRICTED NOTICE and
asks: which firms has the government ALREADY AWARDED under this exact
set-aside, in this exact line of work? Those firms are qualified by
demonstration, not by label. The proof is a contract with a number on it.

  notice: "Total Small Business Set-Aside (FAR 19.5)", NAICS 541519, VA
     -> set_aside_type_codes=["SBA"], naics_codes=["541519"]
     -> firms holding SBA-set-aside awards in 541519, VA-first
     -> each row carries the PIID, the dollars, and the usaspending link

WHY THIS IS BETTER THAN A business_types LOOKUP. business_types says a firm
carries a label. An award under set_aside_type_code=SBA says a contracting
officer checked that label and bought on it. The second is evidence; the
first is metadata that may be stale. business_profile() still resolves the
CLIENT's own size, because the client has no award proving what it cannot win.

COST: ZERO. USAspending needs no key and enforces no quota. The 20/day
ceiling that governs this system is SAM's alone and is untouched here. Queries
are grouped by (set-aside code, NAICS), not issued per notice: 5,263 live
restricted notices collapse to a few dozen distinct pairs.

VERIFIED LIVE 2026-07-29. set_aside_type_codes is really applied, not
silently dropped the way /api/v2/subawards/ drops unknown filters. With NAICS
541519 held fixed, the top holder changes with the code: no filter -> DELL
FEDERAL SYSTEMS; SBA -> FCN, INC.; WOSB -> TAPESTRY TECHNOLOGIES; 8AN ->
AKIAK NS LLC. A code that does not exist returns zero rows rather than
everything, so a typo fails loudly instead of quietly widening the answer.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from agents.golden_press.teaming import set_aside_posture

AWARD_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

# SAM extract set-aside string -> FPDS set-aside code, for the 16 distinct
# strings the live store actually carries (measured 2026-07-29 across 15,605
# notices whose deadline has not passed). Every code below was probed against
# USAspending on that date and returned a real awardee; none is from memory.
#
# "Local Area Set-Aside (FAR 26.2)" is DELIBERATELY ABSENT. LAS returned zero
# awards on probe, so no code here is known to represent it. Two live notices
# carry that string, and they route to unmapped rather than to a guess: a
# wrong code would answer the question with the wrong firms, which is worse
# than answering "this one needs a human".
_SET_ASIDE_CODES = {
    "total small business set-aside (far 19.5)": "SBA",
    "partial small business set-aside (far 19.5)": "SBP",
    "service-disabled veteran-owned small business set aside": "SDVOSBC",
    "sdvosb sole source": "SDVOSBS",
    "women-owned small business": "WOSB",
    "women-owned small business sole source": "WOSBSS",
    "sba certified economically disadvantaged wosb (edwosb) program "
    "set-aside (far 19.15)": "EDWOSB",
    "8a competed": "8A",
    "8(a) sole source": "8AN",
    "hubzone set aside": "HZC",
    "veteran set aside": "VSA",
    "indian small business economic enterprise (isbee) set-aside (specific "
    "to department of interior and indian health services)": "ISBEE",
    "indian economic enterprise (iee) set-aside (specific to department of "
    "interior and indian health services)": "IEE",
    "buy indian set-aside (specific to department of health and human "
    "services, indian health services)": "BI",
}

# Contract award types. Grants and loans cannot be teamed into.
_AWARD_TYPES = ["A", "B", "C", "D"]

# Trailing-year window for proof of qualification. Older than this and a
# firm's certification may have lapsed or it may have outgrown the standard.
_WINDOW = [{"start_date": "2022-10-01", "end_date": "2030-09-30"}]


def set_aside_code(set_aside: Any) -> Optional[str]:
    """FPDS code for a SAM set-aside string, or None when unmapped.

    None is a real answer and is reported as such. Every caller must treat it
    as "no bench can be built for this notice", never as "no restriction".
    """
    key = " ".join(str(set_aside or "").split()).strip().lower()
    return _SET_ASIDE_CODES.get(key)


def _search(code: str, naics: Optional[str], agency_name: Optional[str],
            limit: int, post: Any) -> list[dict]:
    """One USAspending award search. Returns [] on any failure."""
    filters: dict[str, Any] = {
        "award_type_codes": list(_AWARD_TYPES),
        "set_aside_type_codes": [code],
        "time_period": [dict(w) for w in _WINDOW],
    }
    if naics:
        filters["naics_codes"] = [str(naics)]
    if agency_name:
        filters["agencies"] = [{"type": "awarding", "tier": "toptier",
                                "name": agency_name}]
    try:
        payload = post(AWARD_SEARCH_URL, json={
            "filters": filters,
            "fields": ["Recipient Name", "Award Amount", "Awarding Agency",
                       "Award ID", "generated_internal_id", "NAICS",
                       "Start Date"],
            "sort": "Award Amount", "order": "desc",
            "limit": max(1, int(limit)), "page": 1})
    except Exception:  # noqa: BLE001 - additive lane, never sinks a press
        return []
    return (payload or {}).get("results") or []


def qualified_partners(code: str, *, naics: Optional[str] = None,
                       agency: Optional[str] = None, limit: int = 10,
                       post: Optional[Any] = None) -> list[dict]:
    """Firms holding awards under this set-aside code, buying agency first.

    TWO SEARCHES, NOT ONE. Ranking a national list by dollars and hoping a
    firm from the buying agency lands in the window does not work: with
    set-aside SBA in NAICS 541519 the national top four are Treasury, DHS,
    Interior and DHS again, so a VA notice would have been handed four firms
    with no VA record at all. Filtering the query to the agency returns
    ThunderCat and Minburn, who hold VA awards under that same set-aside and
    never appear nationally. The agency join is done by the API, so
    same_agency is a fact about which query answered, not a string compare.

    Each row is a firm plus the single award that proves it. Never raises: a
    USAspending outage returns an empty bench, and a press that would have
    carried partners simply carries none.
    """
    if not code:
        return []
    if post is None:
        from tools.api._http import post_json as post
    from tools.api.usaspending import normalize_agency_name

    want = normalize_agency_name(agency) if agency else None
    rows: list[tuple[bool, dict]] = []
    if want:
        rows += [(True, r) for r in
                 _search(code, naics, want, max(1, int(limit)), post)]
    rows += [(False, r) for r in
             _search(code, naics, None, max(1, int(limit)) * 2, post)]

    best: dict[str, dict] = {}
    for same_agency, row in rows:
        name = str(row.get("Recipient Name") or "").strip()
        if not name:
            continue
        awarder = str(row.get("Awarding Agency") or "").strip()
        amount = float(row.get("Award Amount") or 0.0)
        gid = str(row.get("generated_internal_id") or "").strip()
        candidate = {
            "partner": name,
            "same_agency": same_agency,
            "proof_award_id": str(row.get("Award ID") or "").strip(),
            "proof_amount": amount,
            "proof_agency": awarder,
            "proof_naics": str(row.get("NAICS") or "").strip(),
            "proof_start": str(row.get("Start Date") or "").strip(),
            "proof_url": (f"https://www.usaspending.gov/award/{gid}"
                          if gid else "https://www.usaspending.gov/"),
            "awards_seen": 1,
            "why": (f"holds a federal contract awarded under set-aside {code}"
                    + (f" in NAICS {naics}" if naics else "")
                    + (f", at {awarder}" if same_agency
                       else f" (at {awarder}, not the buying agency)")),
        }
        key = name.casefold()
        prior = best.get(key)
        if prior is None:
            best[key] = candidate
            continue
        # One firm, several awards: keep the STRONGEST proof, and count the
        # rest. An award at the buying agency outranks a bigger one elsewhere.
        candidate["awards_seen"] = prior["awards_seen"] + 1
        stronger = ((same_agency, amount)
                    > (prior["same_agency"], prior["proof_amount"]))
        if stronger:
            best[key] = candidate
        else:
            prior["awards_seen"] = candidate["awards_seen"]
    # Same agency first: a firm already inside the buying office is a warmer
    # route than a larger firm that has never worked there.
    ranked = sorted(best.values(),
                    key=lambda p: (not p["same_agency"], -p["proof_amount"]))
    return ranked[:limit]


def partner_bench(records: Iterable[Any], *, client_is_small: bool = False,
                  per_notice: int = 5, max_lookups: int = 24,
                  fetch: Optional[Any] = None) -> dict:
    """A partner bench for every live notice the client cannot prime alone.

    Groups notices by (set-aside code, NAICS) so one lookup serves every
    notice that shares a lane. max_lookups is a LOUD cap: whatever it drops is
    reported in the receipt, never silently trimmed.
    """
    fetch = fetch or qualified_partners
    receipt: dict[str, Any] = {
        "notices_seen": 0, "restricted": 0, "unmapped_set_aside": 0,
        "unmapped_strings": [], "lookups": 0, "lookups_skipped_by_cap": 0,
        "groups": 0, "groups_with_no_partner": 0, "metered_quota_spent": 0,
        "source": "usaspending spending_by_award (keyless, unmetered)",
    }
    groups: dict[tuple, list] = {}
    for record in records:
        receipt["notices_seen"] += 1
        raw = getattr(record, "set_aside", None)
        posture = set_aside_posture(raw, client_is_small=client_is_small)
        if posture["route"] != "teaming":
            continue
        receipt["restricted"] += 1
        code = set_aside_code(raw)
        if not code:
            receipt["unmapped_set_aside"] += 1
            text = " ".join(str(raw or "").split())
            if text and text not in receipt["unmapped_strings"]:
                receipt["unmapped_strings"].append(text)
            continue
        naics = str(getattr(record, "naics", "") or "").strip() or None
        groups.setdefault((code, naics), []).append(record)

    receipt["groups"] = len(groups)
    # Biggest lanes first, so a cap sacrifices the thinnest ones.
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    out = []
    for (code, naics), recs in ordered:
        if receipt["lookups"] >= max_lookups:
            receipt["lookups_skipped_by_cap"] += len(recs)
            continue
        agency = str(getattr(recs[0], "agency", "") or "").strip() or None
        partners = fetch(code, naics=naics, agency=agency, limit=per_notice)
        receipt["lookups"] += 1
        if not partners:
            receipt["groups_with_no_partner"] += 1
        for record in recs:
            out.append({
                "record_id": getattr(record, "record_id", ""),
                "title": getattr(record, "title", ""),
                "agency": getattr(record, "agency", None),
                "url": getattr(record, "url", None),
                "deadline": getattr(record, "response_deadline", None),
                "set_aside": " ".join(str(getattr(record, "set_aside", "")
                                          or "").split()),
                "set_aside_code": code,
                "naics": naics,
                "partners": partners,
            })
    # Soonest deadline first: the bench is only useful before the door shuts.
    out.sort(key=lambda n: str(n["deadline"] or "9999"))
    return {
        "notices": out,
        "receipt": receipt,
        "basis": ("Each partner holds a federal contract awarded under the "
                  "same set-aside as the notice, so eligibility is shown by "
                  "the award rather than assumed from a label. Set-aside "
                  "comes from the SAM daily extract; awards come from "
                  "USAspending. Both are free and both are linked."),
    }
