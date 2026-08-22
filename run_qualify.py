#!/usr/bin/env python3
"""Step 2: consolidate the 3 search outputs into a qualified, fit-rationalized list.

    python3 run_qualify.py --client "PlanetiQ" [--top 5] [--no-llm]

Reads data/cleaned/searches_<client>.json (from run_searches.py) + the approved
strategy, then: merge SAM + web -> verify (web URLs fetched live) -> attach
USAspending market evidence -> Claude fit rationale on the top candidates -> write a
Step-2 deliverable and alert you to review.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.alerts import desktop_alert  # noqa: E402
from agents.qualify import consolidate, qualify  # noqa: E402
from agents.review import ReviewPacket, load_approved  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--top", type=int, default=5, help="how many top candidates get an LLM fit rationale")
    ap.add_argument("--no-llm", action="store_true", help="consolidate + verify only, no fit rationale")
    args = ap.parse_args()

    strategy = load_approved(args.client)  # Step-1 approval still gates Step 2

    if args.no_llm:
        records, _ = consolidate(args.client)
        verified = sum(1 for r in records if r.verified)
        print(f"{args.client}: {len(records)} candidates, {verified} verified (no LLM)", file=sys.stderr)
        for r in records:
            mark = "✓" if r.verified else "✗"
            print(f"  [{mark}] {r.opportunity.source:8} {r.opportunity.title[:60]}", file=sys.stderr)
        return 0

    report = qualify(args.client, strategy, top_n=args.top)
    print(f"{args.client}: {report.candidate_count} candidates, "
          f"{report.verified_count} verified, fit on top {args.top}", file=sys.stderr)

    # Reuse the desktop alert for the Step-2 review.
    out_md = os.path.join("data", "review", f"{args.client.lower().replace(' ', '_')}.qualify.json")
    fake_packet = ReviewPacket.model_construct(client_name=args.client)
    desktop_alert(fake_packet, out_md)
    # Creation-free (2026-07-12): refresh an existing strict run only; the
    # cutover pointer is operator-owned (tools/assess_refresh.py).
    from tools.assess_refresh import refresh_current_assess_run_if_active
    print("[assess-ledger] after qualification: "
          + refresh_current_assess_run_if_active(args.client), file=sys.stderr)
    print("[done] Step-2 deliverable -> data/review/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
