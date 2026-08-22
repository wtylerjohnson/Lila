"""Engagement-scope leakage diagnostic: read-only, per stored sweep.

For each sweep, tabulate records by awarding/posting department against
the sweep's own declared scope (the gate's intent), and attribute every
out-of-scope record to its ENTRY POINT: which result lane it entered
through, whether that lane's source supports agency filtering, and
whether the stored artifact shows a filter was applied. No sweep is
mutated; the table informs a later pull-time fix.

    .venv/bin/python -m tools.relevance.scope_diagnostic
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: result lane -> (source supports agency filtering, how)
LANE_FILTERABLE = {
    "sam.gov": (True, "SAM search accepts organization filters"),
    "usaspending.gov": (True, "USAspending accepts awarding-agency filters"),
    "subawards": (True, "USAspending subaward search accepts agency filters"),
    "contract_awards": (True, "SAM contract-awards slice accepts agency"),
    "expiring_awards": (True, "USAspending accepts awarding-agency filters"),
    "award_repulls": (True, "board-cited records; scoped by citation"),
    "forecast_signals": (True, "forecast adapters are per-agency by design"),
    "web": (False, "general web search has no agency parameter"),
    "news": (False, "news search has no agency parameter"),
}

from tools.relevance.scope import record_department  # noqa: E402  (single owner)


def sweep_scope_departments(sweep: dict) -> Optional[set[str]]:
    """The sweep's own declared gate scope as department abbrs; None for an
    all-scope or unscoped sweep."""
    sc = sweep.get("search_scope")
    if not isinstance(sc, dict) or sc.get("all") or not sc.get("agencies"):
        return None
    from tools.agency_scope import _department_abbrs
    out: set[str] = set()
    for a in sc["agencies"]:
        for probe in ((a or {}).get("abbr"), (a or {}).get("name")):
            if probe:
                out |= _department_abbrs(str(probe))
    return out or None


def _iter_lane_records(sweep: dict):
    results = sweep.get("results") or {}
    for lane, node in results.items():
        if lane == "sam.gov":
            for n in node or []:
                if isinstance(n, dict):
                    yield lane, n
        elif lane == "usaspending.gov":
            for group in node or []:
                if isinstance(group, dict):
                    for a in group.get("awards") or []:
                        if isinstance(a, dict):
                            yield lane, a
        elif lane in ("award_repulls", "expiring_awards"):
            rows = node.get("rows") if isinstance(node, dict) else node
            for r in rows or []:
                if isinstance(r, dict):
                    yield lane, r
        elif lane == "forecast_signals" and isinstance(node, dict):
            for r in node.get("matched") or []:
                if isinstance(r, dict):
                    yield lane, r
        elif lane == "subawards" and isinstance(node, dict):
            for rows in (node.get("edges") or {}).values():
                for r in rows or []:
                    if isinstance(r, dict):
                        yield lane, r


def diagnose_sweep(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        sweep = json.load(f)
    scope = sweep_scope_departments(sweep)
    by_lane: dict[str, Counter] = defaultdict(Counter)
    leaks: dict[str, list[dict]] = defaultdict(list)
    for lane, record in _iter_lane_records(sweep):
        dept = record_department(record) or "UNRESOLVED"
        by_lane[lane][dept] += 1
        if scope and dept not in ("UNRESOLVED",) and dept not in scope:
            leaks[lane].append({
                "dept": dept,
                "ref": str(record.get("source_id") or record.get("award_id")
                           or record.get("source_id") or "")[:40],
                "title": str(record.get("title")
                             or record.get("description") or "")[:70],
            })
    return {"sweep": os.path.basename(path),
            "client": sweep.get("client"),
            "declared_scope": sorted(scope) if scope else None,
            "by_lane": {k: dict(v) for k, v in by_lane.items()},
            "leaks": dict(leaks)}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    reports = [diagnose_sweep(p) for p in sorted(
        glob.glob(os.path.join(_ROOT, "data", "cleaned", "searches_*.json")))]
    if args.json:
        print(json.dumps(reports, indent=1))
        return 0
    for r in reports:
        scope_label = ("+".join(r["declared_scope"])
                       if r["declared_scope"] else "ALL-SCOPE (no boundary)")
        print(f"\n== {r['sweep']} · client {r['client']} · scope {scope_label}")
        for lane, counts in sorted(r["by_lane"].items()):
            total = sum(counts.values())
            filterable, how = LANE_FILTERABLE.get(lane, (None, "unknown lane"))
            leak_n = len(r["leaks"].get(lane) or [])
            flag = (f"  <- {leak_n} OUT-OF-SCOPE (filterable: {filterable}; "
                    f"{how})" if leak_n else "")
            tops = ", ".join(f"{d}:{n}" for d, n in
                             sorted(counts.items(), key=lambda kv: -kv[1])[:5])
            print(f"   {lane:18} {total:5} records · {tops}{flag}")
        for lane, rows in sorted(r["leaks"].items()):
            for row in rows[:4]:
                print(f"     LEAK {lane}: [{row['dept']}] {row['ref']} "
                      f"{row['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
