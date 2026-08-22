"""Sub and teaming routes: where a client fits when it cannot bid alone.

THE GAP THIS CLOSES. Every lane in this system answers "what can the client
bid?" Nothing answered "what can the client bid THROUGH?", and for a large
business that is most of the market. Measured on the live store, in
apexanalytix's corridor: 2,574 of 12,084 notices (21%) carry a small-business
set-aside. A large business cannot prime any of them. Today the report shows
them as opportunities or not at all; neither is true.

TWO FREE SOURCES, NEITHER PREVIOUSLY TOUCHED.

  SET-ASIDE, from the daily extract, already stored. It says which notices are
  closed to a large prime and therefore only reachable as a subcontractor or
  through a small-business partner. Costs nothing: the column is already in
  the notice store.

  SUBAWARDS, from USAspending /api/v2/subawards/. USAspending needs NO KEY and
  has NO QUOTA; only SAM is metered. Given a prime award the pack already
  holds, this returns who that prime actually subcontracted to, with dollars.
  That is an observed teaming relationship, not an inferred one. Verified live
  2026-07-29 against real awards in the apexanalytix pack: an HHS award
  returned Amazon Web Services, NTT America and True Zero Technologies; an
  80TECH award returned General Dynamics IT and CACI.

WHAT THIS IS NOT. It does not predict who will team with whom. It reports who
HAS teamed, and which doors are closed to a direct bid. Every row carries the
federal record it came from.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

SUBAWARD_URL = "https://api.usaspending.gov/api/v2/subawards/"


def _squash(text: Any) -> str:
    """Company name reduced to letters and digits, for name comparison only.

    "APEX ANALYTIX LLC" and "apexanalytix" differ by a space and a suffix,
    which is enough to make an exact lookup miss a firm that is really there.
    """
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

# Set-aside strings the extract actually writes, measured on the 2026-07-29
# store. "No Set aside used" reads like a restriction and is the opposite of
# one; counting it as restricted overstated the closed market by 55%.
# AFFIRMATIVE statements of open competition. An EMPTY field is deliberately
# NOT here, and that was a real bug in the first cut of this module.
#
# Measured on the 78,577-notice store: 43,096 rows (54.8%) carry an empty
# set_aside, and 7,519 separately carry the literal string "No Set aside
# used". Those are different epistemic states. The second is the government
# saying "this is open"; the first is the government saying nothing. Treating
# them alike asserted "full and open, any responsible source may bid" over
# more than half the store on the basis of a BLANK FIELD, which is exactly
# the class of confident negative this system exists to refuse. A
# subcontractor-first client acting on it would skip doors that may be shut.
_OPEN = {"none", "no set aside used", "n/a"}

# A sole-source set-aside is not a teaming route, it is a closed door: the
# agency has already named who it intends to award. It is reported, but never
# as an opportunity to partner into.
_SOLE_SOURCE = re.compile(r"sole\s*source", re.I)


def set_aside_posture(set_aside: Any, *, client_is_small: bool = False) -> dict:
    """How a client can reach this notice, given its own size.

    Returns route, whether a direct bid is possible, and the reason.

    client_is_small is LOOKED UP, not asked. The first cut of this made it an
    operator parameter on the reasoning that no federal dataset states it.
    That was wrong: USAspending's recipient profile carries business_types,
    keyless and public, and business_profile() below resolves it. Pass the
    looked-up value. It stays a parameter only so a caller can override a
    firm the lookup cannot resolve, and None is a real answer meaning
    unknown, which is different from large.
    """
    raw = " ".join(str(set_aside or "").split())
    key = raw.strip().lower()
    if not key:
        # UNKNOWN IS ITS OWN ANSWER. can_prime is None, not True: a seller
        # must check this notice rather than assume a door is open.
        return {"route": "unknown", "can_prime": None, "set_aside": "",
                "why": "the notice states no set-aside either way; treat as "
                       "unverified rather than open, and check the notice"}
    if key in _OPEN:
        return {"route": "direct", "can_prime": True, "set_aside": raw,
                "why": "full and open: any responsible source may bid"}
    if _SOLE_SOURCE.search(raw):
        return {"route": "closed", "can_prime": False, "set_aside": raw,
                "why": "sole source: the agency has already named its intended "
                       "awardee, so this is intelligence, not an opening"}
    if client_is_small:
        return {"route": "direct", "can_prime": True, "set_aside": raw,
                "why": f"restricted to {raw}, and the client qualifies"}
    return {"route": "teaming", "can_prime": False, "set_aside": raw,
            "why": f"restricted to {raw}: a large business cannot prime this "
                   f"and reaches it only as a subcontractor or through a "
                   f"qualifying partner"}


def fetch_subawards(award_id: str, *, limit: int = 25,
                    post: Optional[Any] = None) -> list[dict]:
    """Who a prime actually subcontracted to, from USAspending.

    FREE. USAspending requires no key and enforces no daily quota; the 20/day
    ceiling that governs everything else here is SAM's alone. A failure
    returns an empty list rather than raising: teaming is additive evidence
    and must never be able to sink a press.
    """
    if not award_id:
        return []
    if post is None:
        from tools.api._http import post_json as post
    try:
        payload = post(SUBAWARD_URL,
                       json={"award_id": str(award_id), "limit": int(limit),
                             "page": 1})
    except Exception:  # noqa: BLE001 - additive lane, never fatal
        return []
    rows = (payload or {}).get("results") or []
    out = []
    for row in rows:
        name = str(row.get("recipient_name") or "").strip()
        if not name:
            continue
        out.append({
            "subawardee": name,
            "amount": float(row.get("amount") or 0.0),
            "description": str(row.get("description") or "").strip(),
            "action_date": str(row.get("action_date") or "").strip(),
            "subaward_number": str(row.get("subaward_number") or "").strip(),
            "prime_award_id": str(award_id),
        })
    return out


def subcontracting_practice(awards: Iterable[Any], *, fetch: Optional[Any] = None,
                            page: int = 50, cap: int = 12) -> dict:
    """Which primes actually subcontract, and which only resell.

    THE DISCRIMINATOR THIS FOUND. For a client that can only sub, the useful
    question about a prime is not how big it is, it is whether it builds teams
    at all. Measured live 2026-07-29 on the twelve largest primes in NAICS
    541519: Leidos, Northrop (twice) and Serco all carry subaward records;
    Dell Federal, World Wide Technology, CDW-G, Four Points, V3Gate, All
    Points and Colossal carry NONE. That is resellers against integrators, and
    it separates a prime worth calling from a dead end. Four of twelve.

    Ranked by subaward dollars. TRUNCATION IS DECLARED: USAspending pages, so
    a prime that fills the page is reported as at-least, never as a total.
    """
    fetch = fetch or fetch_subawards
    rows: list[dict] = []
    for award in list(awards or [])[:cap]:
        gid = (award.get("generated_internal_id")
               if isinstance(award, dict)
               else getattr(award, "generated_internal_id", None))
        name = (award.get("Recipient Name") if isinstance(award, dict)
                else getattr(award, "recipient_name", None)) or ""
        if not gid:
            continue
        subs = fetch(gid, limit=page)
        truncated = len(subs) >= page
        rows.append({
            "prime": str(name),
            "prime_award_id": str(gid),
            "subaward_count": len(subs),
            "subaward_dollars": round(sum(s.get("amount", 0.0) for s in subs), 2),
            "truncated": truncated,
            "subs_to": sorted({s["subawardee"] for s in subs})[:8],
            "takes_subs": bool(subs),
            "why": ("holds subaward records, so it builds teams and can carry "
                    "a subcontractor" if subs else
                    "no subaward record on this award: it may resell rather "
                    "than subcontract, so a teaming approach here is unproven"),
        })
    rows.sort(key=lambda r: (-r["subaward_dollars"], -r["subaward_count"]))
    takes = [r for r in rows if r["takes_subs"]]
    return {
        "primes": rows,
        "takes_subs": len(takes),
        "examined": len(rows),
        "any_truncated": any(r["truncated"] for r in rows),
        "basis": ("Subaward records come from USAspending, keyless and "
                  "unmetered. A prime with no subaward record is reported as "
                  "unproven for teaming, never as refusing to team: absence "
                  "of a record is not a record of absence."),
    }


def teaming_rollup(pack: Any, *, client_aliases: Optional[Iterable[str]] = None,
                   fetch: Optional[Any] = None, cap: int = 12) -> dict:
    """Observed teaming partners across the pack's prime awards.

    Ranked by how many DISTINCT primes each subawardee appears under, then by
    dollars. A firm that subs to one prime is that prime's supplier; a firm
    that subs to five is a teaming partner with a federal practice, and that
    is the one worth a call.
    """
    fetch = fetch or fetch_subawards
    aliases = {str(a).strip().casefold()
               for a in (client_aliases or ()) if str(a).strip()}
    client = str(getattr(pack, "client_name", "") or "").strip().casefold()
    if client:
        aliases.add(client)

    records = [r for r in (getattr(pack, "records", []) or [])
               if getattr(r, "lane", "") == "L2_entity_award"]
    seen_awards = 0
    partners: dict[str, dict] = {}
    for record in records[:cap]:
        gid = getattr(record, "generated_internal_id", None)
        if not gid:
            continue
        subs = fetch(gid)
        seen_awards += 1
        for sub in subs:
            key = sub["subawardee"].casefold()
            if key in aliases:
                continue          # the client subbing to itself is not teaming
            slot = partners.setdefault(sub["subawardee"], {
                "subawardee": sub["subawardee"], "primes": set(),
                "dollars": 0.0, "subawards": 0, "examples": []})
            slot["primes"].add(getattr(record, "recipient", None) or gid)
            slot["dollars"] = round(slot["dollars"] + sub["amount"], 2)
            slot["subawards"] += 1
            if len(slot["examples"]) < 3:
                slot["examples"].append({
                    "prime": getattr(record, "recipient", None),
                    "prime_award": gid,
                    "amount": sub["amount"],
                    "description": sub["description"][:120],
                    "url": getattr(record, "url", None),
                })
    ranked = sorted(
        ({**v, "primes": sorted(str(p) for p in v["primes"]),
          "prime_count": len(v["primes"])} for v in partners.values()),
        key=lambda r: (-r["prime_count"], -r["dollars"]))
    return {
        "source": "USAspending /api/v2/subawards/ (keyless, unmetered)",
        "prime_awards_examined": seen_awards,
        "partners_found": len(ranked),
        "partners": ranked,
        "basis": ("Observed subcontract relationships on THIS pack's prime "
                  "awards. Ranked by distinct primes first: a firm subbing to "
                  "one prime is that prime's supplier, a firm subbing to "
                  "several has a federal practice. Nothing here predicts a "
                  "future teaming; it reports one that already happened."),
    }


def restricted_lanes(records: Iterable[Any], *,
                     client_is_small: bool = False) -> dict:
    """Split notices by whether the client can bid them at all.

    FOUR buckets, not three. The fourth exists because half the store does
    not state a set-aside, and an unstated one is not an open one.
    """
    buckets: dict[str, list] = {"direct": [], "teaming": [],
                                "closed": [], "unknown": []}
    for record in records:
        posture = set_aside_posture(getattr(record, "set_aside", None),
                                    client_is_small=client_is_small)
        buckets[posture["route"]].append({
            "record_id": getattr(record, "record_id", ""),
            "title": getattr(record, "title", ""),
            "agency": getattr(record, "agency", None),
            "url": getattr(record, "url", None),
            **posture})
    return {
        "client_is_small": client_is_small,
        **buckets,
        "counts": {k: len(v) for k, v in buckets.items()},
        "basis": ("Set-aside comes from the daily extract and is already "
                  "stored, so this costs nothing. Whether the CLIENT is a "
                  "small business is looked up, not asked: USAspending's "
                  "recipient profile carries business_types, keyless and "
                  "public. Notices that state no set-aside are counted as "
                  "unknown, never as open."),
    }


# --------------------------------------------------------------------------- #
# business size and set-aside eligibility, LOOKED UP not asked
# --------------------------------------------------------------------------- #
# The first cut of this module made client size an operator parameter, on the
# reasoning that no federal dataset states it. That was wrong, and the
# operator was right to push back: this is an intelligence engine, and the
# answer is free and public.
#
# USAspending /api/v2/recipient/<id>/ returns business_types, a controlled
# vocabulary that states size and every socioeconomic category the firm holds.
# Keyless, unmetered. Verified live 2026-07-29:
#   BOOZ ALLEN HAMILTON INC     other_than_small_business
#   CARAHSOFT TECHNOLOGY        other_than_small_business
#   TRUE ZERO TECHNOLOGIES LLC  small_business, veteran_owned_business
#   IT PARTNERS, INC.           small_business, women_owned_small_business
#   SHOREPOINT LLC              small_business
#
# That last one is the whole point of the lane: a WOSB set-aside is a closed
# door to a large client and an open one to IT Partners, which is exactly the
# partner worth calling about it.
RECIPIENT_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
RECIPIENT_PROFILE_URL = "https://api.usaspending.gov/api/v2/recipient/{rid}/"

# business_types value -> the set-aside families it unlocks
_ELIGIBILITY = {
    "small_business": ("Total Small Business Set-Aside", "Partial Small Business"),
    "women_owned_small_business": ("Women-Owned Small Business", "WOSB"),
    "service_disabled_veteran_owned_business": (
        "Service-Disabled Veteran-Owned Small Business", "SDVOSB"),
    "veteran_owned_business": ("Veteran-Owned Small Business", "VOSB"),
    "historically_underutilized_business_zone_hubzone_firm": ("HUBZone",),
    "8a_program_participant": ("8(a)",),
    "minority_owned_business": ("8(a)",),
}


def business_profile(name: str, *, post=None, get=None) -> dict:
    """Size and socioeconomic categories for a firm, from USAspending.

    FREE and KEYLESS. Returns is_small=None when the firm cannot be resolved,
    which is honestly different from "large": an unregistered or renamed
    entity is unknown, not big.
    """
    if not str(name or "").strip():
        return {"name": name, "resolved": False, "is_small": None,
                "business_types": [], "why": "no name given"}
    if post is None or get is None:
        from tools.api._http import get_json, post_json
        post = post or post_json
        get = get or get_json
    try:
        found = post(RECIPIENT_SEARCH_URL, json={
            "filters": {"award_type_codes": ["A", "B", "C", "D"],
                        "recipient_search_text": [str(name)],
                        "time_period": [{"start_date": "2019-10-01",
                                         "end_date": "2030-09-30"}]},
            "fields": ["Recipient Name", "recipient_id"], "limit": 1, "page": 1})
        results = (found or {}).get("results") or []
        if not results:
            return {"name": name, "resolved": False, "is_small": None,
                    "business_types": [],
                    "why": "no federal prime award found for this name"}
        # THE FIRST ROW IS NOT AUTOMATICALLY THE RIGHT COMPANY.
        # recipient_search_text is a full-text search, so a near miss comes
        # back looking exactly like a hit. Probed 2026-07-29: a prefix search
        # for "apexanalytix" surfaces APEXA, a different firm entirely, and
        # taking row zero would have reported ITS size as the client's.
        #
        # The match is ONE-DIRECTIONAL on purpose. The candidate must carry
        # the whole name asked for ("APEX ANALYTIX LLC" for "apexanalytix");
        # a SHORTER name may never claim the match by being a prefix of what
        # was asked, which is exactly how APEXA got through.
        want = _squash(name)
        matched = next((r for r in results
                        if _squash(r.get("Recipient Name")).startswith(want)),
                       None)
        if matched is None:
            return {"name": name, "resolved": False, "is_small": None,
                    "business_types": [],
                    "why": (f"no federal recipient carries this name; closest "
                            f"was {results[0].get('Recipient Name')!r}, which "
                            f"is a different firm")}
        rid = matched.get("recipient_id")
        profile = get(RECIPIENT_PROFILE_URL.format(rid=rid)) or {}
    except Exception as exc:  # noqa: BLE001 - additive; never sinks a press
        return {"name": name, "resolved": False, "is_small": None,
                "business_types": [], "why": f"lookup failed: {type(exc).__name__}"}
    types = list(profile.get("business_types") or [])
    # THE GOVERNMENT'S OWN RECORD CAN SAY BOTH, AND OFTEN DOES.
    # Observed live 2026-07-29: OSPREY FLIGHT SOLUTIONS, INC. carries
    # small_business AND other_than_small_business together. That is not a
    # data error. Size is judged against the NAICS size standard of each
    # individual award, so a firm can be small in one line of work and large
    # in another, and business_types is the union across everything it holds.
    #
    # The first cut resolved that silently to False. A single flag cannot
    # answer a question whose answer depends on the notice, so a contradiction
    # now reports as unknown and says why, rather than picking a side and
    # sounding certain.
    small = "small_business" in types
    large = "other_than_small_business" in types
    conflict = small and large
    is_small = None if (conflict or not types) else (True if small else
                                                     (False if large else None))
    eligible = sorted({label for t in types
                       for label in _ELIGIBILITY.get(t, ())})
    why = ("USAspending recipient profile, keyless and public. "
           "business_types is the government's own classification.")
    if conflict:
        why = ("USAspending records this firm as BOTH small_business and "
               "other_than_small_business. Size is set per award against the "
               "NAICS size standard, so it can be small in one line of work "
               "and large in another; the answer depends on the notice.")
    return {
        "name": profile.get("name") or name,
        "uei": profile.get("uei"),
        "resolved": True,
        "is_small": is_small,
        "size_conflict": conflict,
        "business_types": types,
        "set_asides_eligible": eligible,
        "url": f"https://www.usaspending.gov/recipient/{rid}",
        "why": why,
    }


def partner_opens_the_door(partner_types: Iterable[str], set_aside: Any) -> bool:
    """Does this partner qualify for the set-aside the client cannot bid?

    The teaming question stated exactly: a WOSB set-aside is closed to a large
    client and open to a women-owned small business, so that partner is the
    route in, not merely a familiar name.
    """
    raw = " ".join(str(set_aside or "").split()).lower()
    if not raw:
        return False
    for t in partner_types or ():
        for label in _ELIGIBILITY.get(t, ()):
            if label.lower() in raw:
                return True
    return False
