"""Full USASpending award schema, renewal clock, vehicle, and rival paper.

Operator orders, 2026-08-18. For every client award and every rival award
(work order A3): current and potential period-of-performance end, parent
IDV identity and description, awarding office, funding office, and
subawards, from the USASpending award-detail and subawards endpoints.
The award-schema store produces the renewal clock (days to current and
potential end against an explicit as-of date) and the vehicle (parent IDV,
prefix-classified by decision_rules.classify_vehicle, with the parent's
own description and ordering-window dates on the row).

A3 rival paper: the rival side of the schema store grouped by awarding
agency and office, each row reseller-resolved to the OEM (a recipient that
names the OEM is direct paper; a known channel recipient carrying the OEM
in the award description is reseller-carried, the same named-reseller
semantics the client ledger applies in rollups.resolve_relationship), plus
the FPDS ATOM action stream per rival over a bounded, disclosed trailing
window. USASpending carries the FY22-to-date history; the ATOM leg carries
the freshest modifications.

Detail truth, measured 2026-08-18: the parent rides detail["parent_award"]
(an object), NOT a flat parent_award_piid field; detail carries
subaward_count, so the subawards endpoint is called only where the count
is nonzero. The generated id itself encodes parent identity
(CONT_AWD_{piid}_{agency}_{parent}_{parentAgency}) and is the fallback.

Zero LLM, zero SAM quota; USASpending is the free layer and bash is the
sanctioned lane for this diagnostic/offline runner. Store writes ride
tools.artifacts.atomic_write_json (validated, fsynced, content-addressed
history).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from tools.api._http import get_json, post_json

SCHEMA_VERSION = 1
API_ROOT = "https://api.usaspending.gov/api/v2"
SUBAWARD_PAGE_LIMIT = 25
SUBAWARD_CAP = 100
DEFAULT_FPDS_DAYS = 120
_ROOT = Path(__file__).resolve().parents[2]
AWARD_SCHEMA_DIR = _ROOT / "data" / "state" / "award_schema"
RIVAL_PAPER_DIR = _ROOT / "data" / "state" / "rival_paper"

#: Channel recipients that carry OEM paper. Mirrors the named-reseller
#: semantics of rollups.resolve_relationship for standalone stores; extend
#: from measured award rows, never from guesses.
RESELLERS = (
    "carahsoft", "thundercat", "immix", "ec america", "fcn",
    "dlt solutions", "shi international", "cdw", "insight public",
    "govplace", "iron bow", "red river", "world wide technology",
    "metgreen", "mundo systems", "four points", "sterling computers",
    "govsmart", "presidio", "affigent", "unicom", "softwareone",
    "minburn", "blue tech", "govconnection", "counter trade", "spathe",
    "tvar", "swish",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_gid(gid: str) -> dict[str, Optional[str]]:
    """CONT_AWD_{piid}_{agency}_{parent}_{parentAgency} to its parts.

    USASpending writes the literal token -NONE- for an absent segment. IDV
    ids (CONT_IDV_{piid}_{agency}) carry no parent segments.
    """
    parts = str(gid or "").split("_")
    out: dict[str, Optional[str]] = {
        "piid": None, "agency": None,
        "parent_piid": None, "parent_agency": None,
    }

    def _clean(value: Optional[str]) -> Optional[str]:
        return None if value in (None, "", "-NONE-") else value

    if len(parts) >= 4 and parts[0] == "CONT" and parts[1] == "AWD":
        out["piid"] = _clean(parts[2])
        out["agency"] = _clean(parts[3])
        if len(parts) >= 6:
            out["parent_piid"] = _clean(parts[4])
            out["parent_agency"] = _clean(parts[5])
    elif len(parts) >= 4 and parts[0] == "CONT" and parts[1] == "IDV":
        out["piid"] = _clean(parts[2])
        out["agency"] = _clean(parts[3])
    return out


def parent_gid_for(parsed: dict[str, Optional[str]]) -> Optional[str]:
    if parsed.get("parent_piid") and parsed.get("parent_agency"):
        return f"CONT_IDV_{parsed['parent_piid']}_{parsed['parent_agency']}"
    return None


def _agency_block(block: Any) -> dict[str, Optional[str]]:
    block = block or {}
    return {
        "toptier": ((block.get("toptier_agency") or {}).get("name")),
        "subtier": ((block.get("subtier_agency") or {}).get("name")),
        "office": block.get("office_agency_name"),
    }


def fetch_award_detail(gid: str, *, fetch: Callable = get_json) -> tuple[Optional[dict], Optional[str]]:
    try:
        return fetch(f"{API_ROOT}/awards/{gid}/"), None
    except Exception as exc:  # noqa: BLE001 - one award never sinks the run
        return None, f"{type(exc).__name__}: {exc}"


def fetch_subawards(gid: str, *, fetch: Callable = post_json,
                    cap: int = SUBAWARD_CAP) -> dict[str, Any]:
    rows: list[dict] = []
    page = 1
    while len(rows) < cap:
        try:
            data = fetch(f"{API_ROOT}/subawards/", json={
                "award_id": gid, "limit": SUBAWARD_PAGE_LIMIT, "page": page,
                "sort": "amount", "order": "desc"})
        except Exception as exc:  # noqa: BLE001
            return {"rows": rows, "error": f"{type(exc).__name__}: {exc}"}
        results = data.get("results") or []
        for row in results:
            rows.append({
                "subaward_number": row.get("subaward_number"),
                "recipient": row.get("recipient_name"),
                "amount": row.get("amount"),
                "action_date": row.get("action_date"),
                "description": (row.get("description") or "")[:200],
            })
        if not (data.get("page_metadata") or {}).get("hasNext"):
            break
        page += 1
    return {"rows": rows[:cap], "total_fetched": len(rows),
            "selection_cap": cap, "truncated": len(rows) > cap}


def _days_from(as_of: date, value: Optional[str]) -> Optional[int]:
    try:
        return (date.fromisoformat(str(value)[:10]) - as_of).days
    except (TypeError, ValueError):
        return None


def schema_row(entry: dict, *, as_of: date,
               parent_cache: dict[str, dict],
               fetch_get: Callable = get_json,
               fetch_post: Callable = post_json,
               pause_s: float = 0.0) -> dict:
    """One award's full schema row; errors ride the row, never raise."""
    from agents.golden_press.decision_rules import classify_vehicle

    gid = entry["generated_id"]
    parsed = parse_gid(gid)
    row: dict[str, Any] = {
        "generated_id": gid,
        "side": entry.get("side"),
        "label": entry.get("label"),
        "usaspending_url": f"https://www.usaspending.gov/award/{gid}/",
        "source": "usaspending_api.award_detail",
        "source_url": f"{API_ROOT}/awards/{gid}/",
        "retrieved_at": _now_iso(),
    }
    detail, err = fetch_award_detail(gid, fetch=fetch_get)
    if err or not isinstance(detail, dict):
        row["error"] = err or "empty detail payload"
        return row
    pop = detail.get("period_of_performance") or {}
    parent_obj = detail.get("parent_award") or {}
    parent_gid = (parent_obj.get("generated_unique_award_id")
                  or parent_gid_for(parsed))
    awarding = _agency_block(detail.get("awarding_agency"))
    funding = _agency_block(detail.get("funding_agency"))
    row.update({
        "piid": detail.get("piid") or parsed["piid"],
        "type": detail.get("type"),
        "type_description": detail.get("type_description"),
        "description": (detail.get("description") or "")[:400],
        "recipient": ((detail.get("recipient") or {}).get("recipient_name")),
        "total_obligation": detail.get("total_obligation"),
        "date_signed": detail.get("date_signed"),
        "pop_start": pop.get("start_date"),
        "pop_current_end": pop.get("end_date"),
        "pop_potential_end": pop.get("potential_end_date"),
        "awarding": awarding,
        "funding": funding,
        "renewal": {
            "as_of": as_of.isoformat(),
            "days_to_current_end": _days_from(as_of, pop.get("end_date")),
            "days_to_potential_end": _days_from(
                as_of, pop.get("potential_end_date")),
        },
        "vehicle": {
            "parent_piid": parsed["parent_piid"],
            "parent_agency": parsed["parent_agency"],
            "parent_gid": parent_gid,
            "vehicle_class": classify_vehicle(
                parsed["parent_piid"], parsed["parent_agency"]),
        },
        "subaward_count": detail.get("subaward_count"),
        "total_subaward_amount": detail.get("total_subaward_amount"),
    })
    headroom = row["renewal"]
    if (headroom["days_to_current_end"] is not None
            and headroom["days_to_potential_end"] is not None):
        headroom["option_headroom_days"] = (
            headroom["days_to_potential_end"]
            - headroom["days_to_current_end"])
    if parent_gid:
        if parent_gid not in parent_cache:
            pdetail, perr = fetch_award_detail(parent_gid, fetch=fetch_get)
            if pdetail:
                ppop = pdetail.get("period_of_performance") or {}
                parent_cache[parent_gid] = {
                    "piid": pdetail.get("piid"),
                    "description": (pdetail.get("description") or "")[:300],
                    "type_description": pdetail.get("type_description"),
                    "pop_current_end": ppop.get("end_date"),
                    "pop_potential_end": ppop.get("potential_end_date"),
                    "usaspending_url":
                        f"https://www.usaspending.gov/award/{parent_gid}/",
                    "retrieved_at": _now_iso(),
                }
            else:
                parent_cache[parent_gid] = {"error": perr}
            if pause_s:
                time.sleep(pause_s)
        row["vehicle"]["parent"] = parent_cache[parent_gid]
    count = row.get("subaward_count") or 0
    if count:
        row["subawards"] = fetch_subawards(gid, fetch=fetch_post)
        row["subawards"]["declared_count"] = count
    else:
        row["subawards"] = {"rows": [], "declared_count": count}
    return row


def build_award_schema(client_slug: str, entries: list[dict], *,
                       as_of: date,
                       out_dir: Optional[Path] = None,
                       fetch_get: Callable = get_json,
                       fetch_post: Callable = post_json,
                       pause_s: float = 0.08,
                       progress: Optional[Callable[[str], None]] = None,
                       ) -> tuple[Path, dict]:
    parent_cache: dict[str, dict] = {}
    awards: dict[str, dict] = {}
    for i, entry in enumerate(sorted(entries,
                                     key=lambda e: e["generated_id"]), 1):
        row = schema_row(entry, as_of=as_of, parent_cache=parent_cache,
                         fetch_get=fetch_get, fetch_post=fetch_post,
                         pause_s=pause_s)
        awards[row["generated_id"]] = row
        if pause_s:
            time.sleep(pause_s)
        if progress and i % 50 == 0:
            progress(f"{i}/{len(entries)} awards "
                     f"(parents cached={len(parent_cache)})")
    counts = {
        "awards": len(awards),
        "client": sum(1 for r in awards.values() if r.get("side") == "client"),
        "rival": sum(1 for r in awards.values() if r.get("side") == "rival"),
        "errors": sum(1 for r in awards.values() if r.get("error")),
        "with_pop_current_end": sum(
            1 for r in awards.values() if r.get("pop_current_end")),
        "with_pop_potential_end": sum(
            1 for r in awards.values() if r.get("pop_potential_end")),
        "with_parent_idv": sum(
            1 for r in awards.values()
            if (r.get("vehicle") or {}).get("parent_gid")),
        "with_awarding_office": sum(
            1 for r in awards.values()
            if (r.get("awarding") or {}).get("office")),
        "with_funding_office": sum(
            1 for r in awards.values()
            if (r.get("funding") or {}).get("office")),
        "with_subawards": sum(
            1 for r in awards.values()
            if (r.get("subawards") or {}).get("rows")),
        "unique_parents": len(parent_cache),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "client": client_slug,
        "as_of": as_of.isoformat(),
        "generated_at": _now_iso(),
        "counts": counts,
        "awards": awards,
        "parents": parent_cache,
    }
    out_dir = Path(out_dir) if out_dir else AWARD_SCHEMA_DIR
    out_path = out_dir / f"{client_slug}.json"
    from tools.artifacts import atomic_write_json
    atomic_write_json(out_path, payload)
    return out_path, payload


def resolve_carrier(recipient: Optional[str], oem: Optional[str]) -> str:
    low = (recipient or "").casefold()
    if oem and oem.casefold() in low:
        return "direct"
    if any(reseller in low for reseller in RESELLERS):
        return "reseller"
    return "other-carrier"


def fpds_recent_actions(rival: str, *, days: int = DEFAULT_FPDS_DAYS,
                        puller: Optional[Callable] = None) -> dict:
    """Bounded trailing window of FPDS ATOM actions naming the rival.

    USASpending carries the FY22-to-date history; this leg exists for the
    freshest modifications only, and its window is disclosed on the store.
    """
    if puller is None:
        from tools.api.base import REGISTRY
        from tools.api.fpds_atom import _last_mod_query
        adapter = REGISTRY.get("fpds_atom")
        end = datetime.now(timezone.utc).date()
        start = end - __import__("datetime").timedelta(days=max(1, days))
        q = _last_mod_query(start, end) + ' VENDOR_NAME:"%s"' % rival

        def puller(query: str = q) -> dict:  # noqa: PLR0206
            return adapter._pull(query)  # noqa: SLF001 - house engine reuse

    payload = puller()
    actions = payload.get("actions") if isinstance(payload, dict) else None
    return {
        "window_days": days,
        "actions": actions or [],
        "status": ("failed" if isinstance(payload, dict)
                   and payload.get("error") else "returned"),
        "error": (payload or {}).get("error")
                 if isinstance(payload, dict) else None,
        "retrieved_at": _now_iso(),
    }


def build_rival_paper(client_slug: str, schema_payload: dict, *,
                      out_dir: Optional[Path] = None,
                      fpds_days: int = DEFAULT_FPDS_DAYS,
                      fpds: Optional[Callable[[str], dict]] = None,
                      skip_fpds: bool = False) -> tuple[Path, dict]:
    """A3: the rival side of the schema store, by agency and office."""
    by_agency: dict[str, dict[str, list]] = {}
    rows: list[dict] = []
    rivals: set[str] = set()
    for row in schema_payload["awards"].values():
        if row.get("side") != "rival" or row.get("error"):
            continue
        oem = row.get("label") or "rival"
        if oem not in ("ledger-rival",):
            rivals.add(oem)
        agency = ((row.get("awarding") or {}).get("toptier")
                  or "UNKNOWN AGENCY")
        office = ((row.get("awarding") or {}).get("office")
                  or (row.get("awarding") or {}).get("subtier")
                  or "UNKNOWN OFFICE")
        paper = {
            "oem": oem,
            "carrier": resolve_carrier(row.get("recipient"), oem),
            "awardee": row.get("recipient"),
            "piid": row.get("piid"),
            "generated_id": row["generated_id"],
            "obligated": row.get("total_obligation"),
            "date_signed": row.get("date_signed"),
            "pop_current_end": row.get("pop_current_end"),
            "pop_potential_end": row.get("pop_potential_end"),
            "days_to_current_end": (row.get("renewal") or {}).get(
                "days_to_current_end"),
            "vehicle_class": (row.get("vehicle") or {}).get("vehicle_class"),
            "parent_piid": (row.get("vehicle") or {}).get("parent_piid"),
            "funding_office": (row.get("funding") or {}).get("office"),
            "description": (row.get("description") or "")[:240],
            "usaspending_url": row.get("usaspending_url"),
            "source": row.get("source"),
            "source_url": row.get("source_url"),
            "retrieved_at": row.get("retrieved_at"),
        }
        rows.append(paper)
        by_agency.setdefault(agency, {}).setdefault(office, []).append(paper)
    for agency in by_agency:
        for office in by_agency[agency]:
            by_agency[agency][office].sort(
                key=lambda r: -(r.get("obligated") or 0))
    fpds_leg: dict[str, dict] = {}
    if not skip_fpds:
        for rival in sorted(rivals):
            fpds_leg[rival] = (fpds(rival) if fpds
                               else fpds_recent_actions(rival,
                                                        days=fpds_days))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "client": client_slug,
        "as_of": schema_payload["as_of"],
        "generated_at": _now_iso(),
        "counts": {
            "rows": len(rows),
            "agencies": len(by_agency),
            "offices": sum(len(v) for v in by_agency.values()),
            "per_oem": {
                oem: sum(1 for r in rows if r["oem"] == oem)
                for oem in sorted({r["oem"] for r in rows})},
            "per_carrier": {
                c: sum(1 for r in rows if r["carrier"] == c)
                for c in sorted({r["carrier"] for r in rows})},
        },
        "by_agency": by_agency,
        "fpds_recent_actions": fpds_leg,
        "fpds_window_note": (
            f"FPDS ATOM leg is a bounded {fpds_days}-day trailing window "
            "for freshest modifications; FY22-to-date history rides the "
            "USASpending rows above." if not skip_fpds
            else "FPDS ATOM leg skipped this build."),
    }
    out_dir = Path(out_dir) if out_dir else RIVAL_PAPER_DIR
    out_path = out_dir / f"{client_slug}.json"
    from tools.artifacts import atomic_write_json
    atomic_write_json(out_path, payload)
    return out_path, payload


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", required=True)
    ap.add_argument("--entries", required=True,
                    help="JSON file: [{generated_id, side, label}, ...]")
    ap.add_argument("--as-of", default=None,
                    help="explicit as-of date (frozen-clock doctrine); "
                         "defaults to today")
    ap.add_argument("--fpds-days", type=int, default=DEFAULT_FPDS_DAYS)
    ap.add_argument("--skip-fpds", action="store_true")
    args = ap.parse_args(argv)

    as_of = (date.fromisoformat(args.as_of) if args.as_of else date.today())
    entries = json.loads(Path(args.entries).read_text(encoding="utf-8"))
    path, payload = build_award_schema(
        args.client, entries, as_of=as_of,
        progress=lambda msg: print(f"  {msg}", flush=True))
    print(f"AWARD_SCHEMA {json.dumps(payload['counts'])}")
    print(f"WROTE {path}")
    rp_path, rp = build_rival_paper(args.client, payload,
                                    fpds_days=args.fpds_days,
                                    skip_fpds=args.skip_fpds)
    print(f"RIVAL_PAPER {json.dumps(rp['counts'])}")
    fpds_status = {k: v.get('status') for k, v in
                   rp["fpds_recent_actions"].items()}
    print(f"FPDS_LEG {json.dumps(fpds_status)}")
    print(f"WROTE {rp_path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
