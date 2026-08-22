#!/usr/bin/env python3
"""Targeted USAspending refresh: attach the keyword-matched (addressable)
slice to an EXISTING sweep artifact without re-running the sweep.

    python3 run_market_refresh.py --client "Thinklogical"

Why this exists: run_searches.py rebuilds the whole artifact (skipped sources
come back EMPTY), and a full sweep spends SAM quota and LLM triage. This
touches only results["usaspending.gov"][*].addressable — the market slice the
scoreboard's grounded TAM reads — and records fetched_at per lane. Zero SAM
calls, zero LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

ROOT = os.path.dirname(os.path.abspath(__file__))


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _refresh_subawards(artifact: dict, naics: list[str]) -> None:
    """Attach prime->sub edges + subcontracting-demand rows to an existing
    artifact (results['subawards']), zero SAM quota. Existing keys survive."""
    from agents.partnering.config import SUBCONTRACTING_PLAN_THRESHOLD_USD
    from tools.api.usaspending_subawards import SubawardsSource
    src = SubawardsSource()
    block = artifact["results"].setdefault("subawards", {})
    edges: dict = {}
    demand: dict = {}
    for code in naics[:6]:
        try:
            edges[code] = src.edges(code)
            print(f"[lane {code}] {len(edges[code])} sub edges", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — a lane failure keeps the rest
            edges[code] = [{"error": str(e)}]
            print(f"[lane {code}] edges FAILED: {e}", file=sys.stderr)
        if SUBCONTRACTING_PLAN_THRESHOLD_USD is not None:
            try:
                demand[code] = src.demand_awards(
                    code, SUBCONTRACTING_PLAN_THRESHOLD_USD)
                print(f"[lane {code}] {len(demand[code])} demand rows "
                      f"(> ${SUBCONTRACTING_PLAN_THRESHOLD_USD:,.0f}, "
                      "other-than-small)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                demand[code] = [{"error": str(e)}]
                print(f"[lane {code}] demand FAILED: {e}", file=sys.stderr)
    small: dict = {}
    for code in naics[:6]:
        try:
            small[code] = src.small_awards(code)
            print(f"[lane {code}] {len(small[code])} small-sourced awards",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            small[code] = [{"error": str(e)}]
            print(f"[lane {code}] small awards FAILED: {e}", file=sys.stderr)
    block["edges"] = edges
    block["demand"] = {"threshold_usd": SUBCONTRACTING_PLAN_THRESHOLD_USD,
                       "rows_by_naics": demand}
    block["small_awards"] = small
    block["subawards_refreshed_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")


def _refresh_expiring(artifact: dict, naics: list[str]) -> None:
    """Attach the recompete calendar (results['expiring_awards']): contracts in
    the client's lanes ending inside the forward window, from USAspending —
    dollars, agency, and a citable award URL per row. Zero SAM quota, zero LLM.
    Rows dedupe by award id across lanes; the earliest end date sorts first."""
    from tools.api.usaspending import UsaSpendingSource
    src = UsaSpendingSource()
    by_id: dict = {}
    errors: list[str] = []
    for code in naics[:6]:
        try:
            rows = src.expiring_awards(code)
            print(f"[lane {code}] {len(rows)} awards ending in the window",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — a lane failure keeps the rest
            errors.append(f"{code}: {e}")
            print(f"[lane {code}] expiring FAILED: {e}", file=sys.stderr)
            continue
        for r in rows:
            key = r.get("award_id") or r.get("url") or id(r)
            if key not in by_id:
                by_id[key] = r
    rows = sorted(by_id.values(), key=lambda x: x.get("end_date") or "9999")
    artifact["results"]["expiring_awards"] = {
        "rows": rows,
        "basis": ("period-of-performance end inside 18 months, awards acted on "
                  "in the last 5 years, top-100-by-dollars per NAICS lane — "
                  "USAspending"),
        "errors": errors,
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"[refresh] recompete calendar: {len(rows)} distinct awards ending "
          f"in window", file=sys.stderr)


def _refresh_dod_contracts(artifact: dict, packet: dict) -> None:
    """Attach the DoD award wire (results['dod_contracts']): defense.gov daily
    contract announcements hitting the client's capability terms. Who is
    winning in the lanes right now — teaming targets with fresh dollars."""
    from tools.api.base import SourceQuery
    from tools.api.dod_contracts import DodContractsSource
    from tools.api.usaspending import addressable_terms
    cap_terms = addressable_terms(
        (packet.get("strategy") or {}).get("keywords") or [])
    d = DodContractsSource().enrich(SourceQuery(keywords=cap_terms))
    d["refreshed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifact["results"]["dod_contracts"] = d
    print(f"[dod-contracts] {len(d['items'])} keyword-matched of "
          f"{d.get('total_announcements', 0)} announcements", file=sys.stderr)


def _refresh_budget(artifact: dict, packet: dict) -> None:
    """Attach spend pressure (results['budget_pressure']): current-FY
    budgetary resources vs obligations for the client's target agencies plus
    the sweep's top market agencies. Keyless, mechanical demand signal."""
    from tools.api.base import SourceQuery
    from tools.api.usaspending_budget import BudgetPressureSource
    names = list((packet.get("strategy") or {}).get("target_agencies") or [])
    for b in (artifact["results"].get("usaspending.gov") or []):
        for row in ((b.get("addressable") or {}).get("agency_breakdown") or [])[:3]:
            if row.get("agency") and row["agency"] not in names:
                names.append(row["agency"])
    d = BudgetPressureSource().enrich(SourceQuery(agencies=names))
    d["refreshed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifact["results"]["budget_pressure"] = d
    print(f"[budget] {len(d['rows'])} agencies with current-FY execution",
          file=sys.stderr)


def _refresh_watchdogs(artifact: dict, packet: dict) -> None:
    """Attach compelled-demand reports (results['watchdogs']): GAO + all-IG
    items hitting the client's capability terms."""
    from tools.api.base import SourceQuery
    from tools.api.usaspending import addressable_terms
    from tools.api.watchdog_rss import WatchdogRssSource
    cap_terms = addressable_terms(
        (packet.get("strategy") or {}).get("keywords") or [])
    d = WatchdogRssSource().enrich(SourceQuery(keywords=cap_terms))
    d["refreshed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifact["results"]["watchdogs"] = d
    print(f"[watchdogs] {len(d['items'])} keyword-matched of "
          f"{d.get('total_reports', 0)} reports", file=sys.stderr)


def _refresh_congress(artifact: dict, packet: dict) -> None:
    """Attach the bill wire (results['congress']): recently updated bills
    hitting the client's capability terms; money vehicles flagged."""
    from tools.api.base import SourceQuery
    from tools.api.congress_gov import CongressGovSource
    from tools.api.usaspending import addressable_terms
    cap_terms = addressable_terms(
        (packet.get("strategy") or {}).get("keywords") or [])
    d = CongressGovSource().enrich(SourceQuery(keywords=cap_terms))
    d["refreshed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifact["results"]["congress"] = d
    # Do not leave a stale legacy alias that a downstream compatibility read
    # could mistake for a second/current collector result.
    artifact["results"].pop("congress_gov", None)
    print(f"[congress] {len(d['items'])} keyword-matched of "
          f"{d.get('total_scanned', 0)} bills", file=sys.stderr)


def _refresh_client_awards(artifact: dict, client: str) -> None:
    """Attach the client's OWN federal award history
    (results['client_awards']) — proven delivery with citable award URLs.
    Fixes the underselling failure mode: a client with $2M+ of past federal
    delivery read as 'one small PO' because nothing queried their history."""
    from tools.api.usaspending import UsaSpendingSource
    rows = UsaSpendingSource().recipient_awards(client)
    total = sum(r["amount"] for r in rows
                if isinstance(r.get("amount"), (int, float)))
    artifact["results"]["client_awards"] = {
        "rows": rows,
        "total_amount": round(total, 2),
        "basis": "USAspending recipient-name search, prime awards, 7-year window",
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"[client-awards] {len(rows)} awards, ${total:,.0f} total proven "
          f"federal delivery", file=sys.stderr)


def _refresh_forecasts(artifact: dict, client: str, packet: dict) -> dict:
    """Refuse the retired DHS-only writer without mutating its inputs.

    The canonical forecast envelope is a cross-source census owned by
    ``run_searches.py``. Replacing it with one legacy APFS pull would silently
    discard source coverage and provenance, so this compatibility seam returns
    one named failure and an exact sanctioned fix surface instead.
    """

    del artifact, packet
    command = shlex.join([
        "python3", "run_searches.py", "--client", client,
        "--skip", "web", "triage", "picture", "--preserve-skipped",
    ])
    return {
        "stage": "forecasts",
        "reason": (
            "run_market_refresh.py --forecasts is retired because its "
            "DHS-only result would replace the canonical multi-source "
            "forecast envelope"
        ),
        "fix_surface": command,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--subawards", action="store_true",
                    help="also refresh prime->sub edges + subcontracting-demand "
                         "rows (partnering engine inputs)")
    ap.add_argument("--expiring", action="store_true",
                    help="also refresh the recompete calendar (contracts in the "
                         "client's lanes ending inside 18 months — horizon input)")
    ap.add_argument("--forecasts", action="store_true",
                    help="retired: exits with the sanctioned multi-source "
                         "run_searches.py fix surface and changes nothing")
    ap.add_argument("--client-awards", action="store_true",
                    help="also refresh the client's own federal award history "
                         "(proven delivery, cited — horizon + compose input)")
    ap.add_argument("--dod", action="store_true",
                    help="also refresh the DoD award wire (defense.gov daily "
                         "contract announcements in the client's lanes)")
    ap.add_argument("--budget", action="store_true",
                    help="also refresh spend pressure (current-FY unobligated "
                         "balances at the client's agencies — horizon input)")
    ap.add_argument("--watchdogs", action="store_true",
                    help="also refresh GAO + IG reports hitting the client's "
                         "capability terms (compelled-demand signals)")
    ap.add_argument("--congress", action="store_true",
                    help="also refresh the congress.gov bill wire (money "
                         "forming — capability-matched bills, vehicles flagged)")
    args = ap.parse_args()

    if args.forecasts:
        failure = _refresh_forecasts({}, args.client, {})
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 2

    slug = _slug(args.client)
    from agents.review import sweep_artifact_path
    path = sweep_artifact_path(args.client)  # L19: gate-designated artifact
    review = os.path.join(ROOT, "data", "review", f"{slug}.review.json")
    if not os.path.exists(path):
        print(f"[refresh] no sweep artifact at {path} — run the search step first",
              file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        artifact = json.load(f)
    packet = {}
    if os.path.exists(review):
        with open(review, encoding="utf-8") as f:
            packet = json.load(f)

    from tools.api.usaspending import UsaSpendingSource, addressable_terms
    terms = addressable_terms((packet.get("strategy") or {}).get("keywords") or [])
    if not terms:
        print("[refresh] no capability/technology keywords in the approved "
              "strategy — nothing to scope the addressable slice with", file=sys.stderr)
        return 2
    print(f"[refresh] {len(terms)} keyword terms: {', '.join(terms[:6])} …", file=sys.stderr)

    bundles = (artifact.get("results") or {}).get("usaspending.gov")
    if not isinstance(bundles, list) or not bundles:
        print("[refresh] artifact has no usaspending lane bundles", file=sys.stderr)
        return 2

    src = UsaSpendingSource()
    total = 0.0
    for b in bundles:
        naics = b.get("naics_code")
        if not naics:
            continue
        try:
            s = src.keyword_slice(naics, terms)
        except Exception as e:  # noqa: BLE001 — a lane failure must not lose the rest
            b["addressable"] = {"error": str(e), "keywords_used": terms}
            print(f"[lane {naics}] FAILED: {e}", file=sys.stderr)
            continue
        s["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        b["addressable"] = s
        total += s["total_obligated"]
        print(f"[lane {naics}] addressable ${s['total_obligated'] / 1e6:.1f}M "
              f"across {len(s['agency_breakdown'])} agencies", file=sys.stderr)

    if args.subawards:
        _refresh_subawards(artifact,
                           [b.get("naics_code") for b in bundles if b.get("naics_code")])
    if args.expiring:
        _refresh_expiring(artifact,
                          [b.get("naics_code") for b in bundles if b.get("naics_code")])
    if args.client_awards:
        _refresh_client_awards(artifact, args.client)
    if args.dod:
        _refresh_dod_contracts(artifact, packet)
    if args.budget:
        _refresh_budget(artifact, packet)
    if args.watchdogs:
        _refresh_watchdogs(artifact, packet)
    if args.congress:
        _refresh_congress(artifact, packet)

    artifact["generated_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    from tools.artifacts import atomic_write_json
    atomic_write_json(path, artifact)
    # Creation-free (2026-07-12): refresh an existing strict run only; the
    # cutover pointer is operator-owned (tools/assess_refresh.py).
    from tools.assess_refresh import refresh_current_assess_run_if_active
    print("[assess-ledger] after market inputs: "
          + refresh_current_assess_run_if_active(args.client, sweep_path=path),
          file=sys.stderr)
    print(f"[refresh] total addressable ${total / 1e6:.1f}M over 3yr "
          f"(${total / 3e6:.1f}M/yr) -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
