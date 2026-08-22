"""Award-expiry calendar (L16) — every award in the client's capability
lanes expiring inside 24 months, ranked by decomposable attack value.

USAspending period-of-performance end dates are account-planning clocks, not
proof of a recompete. This productizes the expiring-award pulls the reports did
ad hoc. Evidence tier is PROGRAM (stable USAspending records): expiry math
never implies a successor or notice. A recompete/follow-on claim requires an
independent forecast or notice and live SAM re-verification.

Dollar honesty (the buyer-map dollar-ranking rule, applied here from day one):
`obligated` is dollars obligated to date on the award; `potential_ceiling` is
base-and-all-options when fetched from the award detail endpoint, else None
with `ceiling_note` saying so. The two are never conflated, summed, or ranked
interchangeably; ranking uses obligated, labeled as such.

Incumbents resolve through the entity crosswalk (L14 hard dependency): the
DEFEND split and the product-competitor factor both key on canonical doors,
never raw recipient strings.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from tools.api._http import post_json, get_json
from tools.entity_lineage import (
    canonical_name, company_matches, normalize_company, resolve_entity,
)

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DETAIL_URL = "https://api.usaspending.gov/api/v2/awards/{id}/"
AWARD_FIELDS = ["Award ID", "Recipient Name", "Award Amount",
                "Awarding Agency", "Awarding Sub Agency",
                "Funding Agency", "Funding Sub Agency",
                "Start Date", "End Date", "Description",
                "Contract Award Type", "NAICS", "PSC", "Set Aside Type",
                "generated_internal_id"]
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]  # definitive + orders under IDVs

_DEFAULT_STORE = Path(__file__).resolve().parents[2] / "data" / "state" / "recompete"
_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "recompete_weights.json"

WINDOW_MONTHS = 24
NEAR_WINDOW_DAYS = 274  # ~9 months: positioning window still open


def _store_dir() -> Path:
    return Path(os.environ.get("LILA_RECOMPETE_DIR", str(_DEFAULT_STORE)))


def load_weights() -> dict:
    try:
        with open(os.environ.get("LILA_RECOMPETE_WEIGHTS", str(_WEIGHTS_PATH)),
                  encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items()
                    if not k.startswith("_")}
    except (OSError, ValueError):
        # config missing is loud, not fatal: zero weights make every score 0
        return {}


# ── ingestion ────────────────────────────────────────────────────────────────

def _lane_filter(naics: str, years_back: int) -> dict:
    today = date.today()
    return {
        "award_type_codes": CONTRACT_TYPE_CODES,
        "naics_codes": [naics],
        "time_period": [{
            "start_date": (today - timedelta(days=365 * years_back)).isoformat(),
            "end_date": today.isoformat()}],
    }


def _code(v) -> Optional[str]:
    """USASpending returns NAICS/PSC as either 'code' strings or
    {'code': ..., 'description': ...} dicts; store the code string."""
    if isinstance(v, dict):
        return v.get("code")
    return str(v) if v not in (None, "") else None


def _row(a: dict, naics: str) -> dict:
    """One search result -> a stored calendar row. Obligated only; ceiling
    arrives from the detail endpoint for top rows, labeled when absent."""
    recipient_raw = a.get("Recipient Name") or ""
    cid = resolve_entity(recipient_raw)
    return {
        "award_id": a.get("Award ID"),
        "internal_id": a.get("generated_internal_id"),
        "recipient_raw": recipient_raw,
        "recipient": (canonical_name(cid) if cid else None) or recipient_raw,
        "recipient_door": cid,
        "awarding_agency": a.get("Awarding Agency"),
        "awarding_office": a.get("Awarding Sub Agency"),
        "funding_agency": a.get("Funding Agency"),
        "funding_office": a.get("Funding Sub Agency"),
        "naics": _code(a.get("NAICS")) or naics,
        "psc": _code(a.get("PSC")),
        "description": (a.get("Description") or "")[:400],
        "obligated": a.get("Award Amount"),
        "obligated_label": "dollars obligated to date (USAspending Award Amount)",
        "potential_ceiling": None,
        "ceiling_note": "ceiling not fetched (award detail endpoint, top rows only)",
        "pop_end": a.get("End Date"),
        "pop_start": a.get("Start Date"),
        "award_type": a.get("Contract Award Type"),
        "set_aside": a.get("Set Aside Type"),
        "url": (f"https://www.usaspending.gov/award/{a['generated_internal_id']}"
                if a.get("generated_internal_id") else None),
    }


def pull_calendar(profile, *, months: int = WINDOW_MONTHS, years_back: int = 5,
                  per_lane_limit: int = 100) -> list[dict]:
    """Awards in the profile's NAICS lanes with PoP end inside the window.
    Client-side end-date filter (the API's time_period matches activity, not
    expiry). No SAM quota touched; USASpending is unmetered."""
    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=30 * months)).isoformat()
    rows: dict[str, dict] = {}
    for naics in (profile.naics_boundary or []):
        payload: dict[str, Any] = {
            "filters": _lane_filter(naics, years_back),
            "fields": AWARD_FIELDS, "subawards": False,
            "limit": min(per_lane_limit, 100),
            "sort": "Award Amount", "order": "desc",
        }
        try:
            results = post_json(SEARCH_URL, json=payload).get("results") or []
        except Exception:  # noqa: BLE001 — one lane never kills the calendar
            continue
        for a in results:
            end = a.get("End Date") or ""
            if not (today <= end <= horizon):
                continue
            key = a.get("generated_internal_id") or a.get("Award ID") or ""
            if key and key not in rows:
                rows[key] = _row(a, naics)
    return sorted(rows.values(), key=lambda r: r.get("pop_end") or "9999")


def attach_ceilings(rows: list[dict], top_n: int = 25) -> None:
    """Base-and-all-options ceiling for the top rows, from the award detail
    endpoint. Obligated and ceiling stay separate fields, separately labeled."""
    for r in rows[:top_n]:
        iid = r.get("internal_id")
        if not iid:
            continue
        try:
            d = get_json(DETAIL_URL.format(id=iid))
        except Exception:  # noqa: BLE001
            continue
        ceiling = d.get("base_and_all_options") or d.get("base_and_all_options_value")
        if isinstance(ceiling, (int, float)) and ceiling > 0:
            r["potential_ceiling"] = ceiling
            r["ceiling_note"] = ("base and all options value "
                                 "(USAspending award detail)")


# ── scoring ──────────────────────────────────────────────────────────────────

def _is_task_order(award_type: Optional[str]) -> bool:
    t = (award_type or "").upper()
    return any(k in t for k in ("DELIVERY ORDER", "TASK ORDER", "BPA CALL", "IDV"))


def score_calendar(rows: list[dict], profile, *, buyer_agencies: set | None = None,
                   weights: dict | None = None) -> dict:
    """{'attack': ranked rows with score+decomposition, 'defend': client-
    incumbent rows}. Every score decomposes into cited factors (L16)."""
    from tools.capability import match_capability
    w = weights if weights is not None else load_weights()
    named = [n for n in (profile.named_competitors_and_incumbents or []) if n]
    client_norm = normalize_company(profile.client_name)
    buyer_agencies = {a.lower() for a in (buyer_agencies or set()) if a}

    attack, defend = [], []
    for r in rows:
        recipient = r.get("recipient_raw") or ""
        # the client's own paper is a renewal, not an attack target
        if (normalize_company(recipient) == client_norm
                or company_matches(recipient, profile.client_name)):
            r2 = dict(r)
            r2["defend"] = True
            r2["defend_note"] = ("client is the incumbent; renewal defense, "
                                 "never an attack row")
            defend.append(r2)
            continue
        decomp = []
        m = match_capability(profile, r.get("description") or "",
                             component=f"{r.get('awarding_office') or ''} "
                                       f"{r.get('awarding_agency') or ''}")
        if m.score >= 1:
            decomp.append({
                "factor": "capability_match", "points": w.get("capability_match", 0),
                "evidence": f"award description matches capability terms: "
                            f"{', '.join(sorted(m.matched)[:4])}"})
        door = r.get("recipient_door")
        is_product_comp = bool(door) and any(
            company_matches(canonical_name(door) or "", nm)
            or company_matches(nm, canonical_name(door) or "")
            for nm in named) and normalize_company(
                canonical_name(door) or "") != client_norm
        if is_product_comp:
            decomp.append({
                "factor": "product_competitor_incumbent",
                "points": w.get("product_competitor_incumbent", 0),
                "evidence": f"incumbent {r.get('recipient')} is a profile-named "
                            f"product competitor (crosswalk door: {door})"})
        end = r.get("pop_end") or ""
        near = (date.today() + timedelta(days=NEAR_WINDOW_DAYS)).isoformat()
        if end and end <= near:
            decomp.append({
                "factor": "expiry_within_9_months",
                "points": w.get("expiry_within_9_months", 0),
                "evidence": f"period of performance ends {end}; positioning "
                            f"window still open"})
        agency_hit = next((a for a in (r.get("awarding_agency"),
                                       r.get("awarding_office"),
                                       r.get("funding_agency"))
                           if a and a.lower() in buyer_agencies), None)
        if agency_hit:
            decomp.append({
                "factor": "buyer_map_agency", "points": w.get("buyer_map_agency", 0),
                "evidence": f"{agency_hit} appears in the client's buyer map / "
                            f"installed base"})
        if not _is_task_order(r.get("award_type")):
            decomp.append({
                "factor": "single_award_vehicle",
                "points": w.get("single_award_vehicle", 0),
                "evidence": f"{r.get('award_type') or 'definitive contract'}: "
                            f"a direct award-holder account-planning route, "
                            f"not a task order under a multi-award IDIQ"})
        r2 = dict(r)
        r2["score"] = min(100, sum(d["points"] for d in decomp))
        r2["score_decomposition"] = decomp
        attack.append(r2)
    attack.sort(key=lambda x: (-x["score"], x.get("pop_end") or "9999"))
    return {"attack": attack, "defend": defend}


# ── store + orchestration ────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name).strip("_").lower()


def build_calendar(profile, *, searches: Optional[dict] = None,
                   months: int = WINDOW_MONTHS) -> dict:
    """Pull, score, persist, return the standing calendar for a client."""
    rows = pull_calendar(profile, months=months)
    attach_ceilings(rows)
    buyer_agencies: set = set()
    bm = ((searches or {}).get("results") or {}).get("incumbent_buyer_map") or {}
    for b in bm.get("buyers") or []:
        for k in (b.get("buyer"), b.get("agency")):
            if k:
                buyer_agencies.add(k)
    scored = score_calendar(rows, profile, buyer_agencies=buyer_agencies)
    out = {"client": profile.client_name, "window_months": months,
           "generated": date.today().isoformat(),
           "population": len(rows), **scored,
           "dollar_note": ("obligated = dollars obligated to date; "
                           "potential_ceiling = base and all options where "
                           "fetched; never conflated")}
    os.makedirs(_store_dir(), exist_ok=True)
    with open(_store_dir() / f"{_slug(profile.client_name)}.json", "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def load_calendar(client_name: str) -> Optional[dict]:
    try:
        with open(_store_dir() / f"{_slug(client_name)}.json",
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
