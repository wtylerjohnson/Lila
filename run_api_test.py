#!/usr/bin/env python3
"""Fire EVERY registered API at once and print a rack report.

    python3 run_api_test.py            # healthchecks only (fast, near-free)
    python3 run_api_test.py --live     # + one micro-query per source
                                       #   (spends 1 SAM call; entity vetting 2)

All sources run in parallel with hard per-source timeouts. Exit code 0 when
every ENABLED source is up; 1 otherwise — safe to use as a preflight before
demos or client runs.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

import tools.api  # noqa: E402,F401 — registers all adapters
from tools.api import REGISTRY, SourceQuery  # noqa: E402
from tools.toggles import is_enabled  # noqa: E402

MICRO = {
    # cheapest real query per source, for --live
    "usaspending.gov": lambda s: s.market_evidence("541512"),
    "usaspending_subawards": lambda s: s.enrich(SourceQuery(naics_codes=["541512"])),
    "federal_register": lambda s: s.enrich(SourceQuery(keywords=["cybersecurity"])),
    "trade_rss": lambda s: s.enrich(SourceQuery(keywords=[])),
    "cisa_kev": lambda s: s.enrich(SourceQuery(keywords=["cisco"])),
    "regulations_gov": lambda s: s.enrich(SourceQuery(keywords=["cybersecurity"])),
    "govinfo": lambda s: s.enrich(SourceQuery(keywords=["cybersecurity"])),
    "grants_gov": lambda s: s.enrich(SourceQuery(keywords=["cybersecurity"])),
    "sbir_gov": lambda s: s.enrich(SourceQuery(keywords=["cyber"])),
    "gdelt": lambda s: s.enrich(SourceQuery(keywords=["threat intelligence"])),
    "treasury_fiscal": lambda s: s.enrich(SourceQuery(agencies=["Homeland"])),
    "gao_legal": lambda s: s.enrich(SourceQuery(keywords=["protest"])),
    "calc_rates": lambda s: s.enrich(SourceQuery(keywords=["security analyst"])),
    "sec_edgar": lambda s: s.enrich(SourceQuery(keywords=["threat intelligence"])),
    "fedramp": lambda s: s.enrich(SourceQuery(keywords=["security"])),
    "dhs_apfs": lambda s: s.forecasts(),
    # federal_hierarchy excluded from --live: spends metered SAM quota.
    "contract_awards": lambda s: s.enrich(SourceQuery(naics_codes=["541512"])),
    "entity_vetting": lambda s: s.vet("Carahsoft Technology Corp."),
    # sam.gov + web excluded from --live micro queries on purpose:
    # sam.gov burns scarce rate limit on a full search; web spends a Max-plan call.
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="also run one micro-query per source (costs noted above)")
    ap.add_argument("--timeout", type=float, default=25.0, help="per-source seconds")
    args = ap.parse_args()

    sources = REGISTRY.all()
    print(f"Querying {len(sources)} sources in parallel "
          f"({'healthcheck + micro-query' if args.live else 'healthcheck only'})...\n")

    def probe(src):
        t0 = time.monotonic()
        enabled = is_enabled(src.name, True) and src.enabled
        if not enabled:
            return src.name, "OFF", "switched off in .env", 0.0
        try:
            ok, detail = src.healthcheck()
        except Exception as e:  # noqa: BLE001
            return src.name, "DOWN", f"healthcheck crashed: {e}", time.monotonic() - t0
        if not ok:
            return src.name, "DOWN", detail, time.monotonic() - t0
        if args.live and src.name in MICRO:
            try:
                MICRO[src.name](src)
                detail += " · live micro-query OK"
            except Exception as e:  # noqa: BLE001
                return src.name, "DOWN", f"health OK but live query failed: {e}", \
                    time.monotonic() - t0
        return src.name, "UP", detail, time.monotonic() - t0

    rows, failed = [], []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {ex.submit(probe, s): s.name for s in sources}
        for fut in as_completed(futs, timeout=args.timeout * 2):
            rows.append(fut.result())

    width = max(len(r[0]) for r in rows) + 2
    for name, state, detail, dt in sorted(rows):
        mark = {"UP": "🟢", "DOWN": "🔴", "OFF": "⚪"}[state]
        print(f"  {mark} {name:<{width}} {state:<5} {dt:5.1f}s  {detail}")
        if state == "DOWN":
            failed.append(name)

    total = time.monotonic() - started
    print(f"\n{len(rows) - len(failed)}/{len(rows)} sources ready · "
          f"wall time {total:.1f}s (parallel)")
    if failed:
        print(f"NOT READY: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
