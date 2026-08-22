#!/usr/bin/env python3
"""Build the standing recompete calendar for a client (L16).

    python3 run_recompete.py --client "NETSCOUT" [--months 24]

USASpending only (no SAM quota, no LLM): awards in the profile's capability
lanes with period-of-performance end inside the window, incumbents resolved
through the entity crosswalk, ranked by decomposable attack value with the
client's own paper segregated to the DEFEND list. Artifact:
data/state/recompete/<slug>.json; the dashboard renders it, the fact layer
cites it (tier=program).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--months", type=int, default=24)
    args = ap.parse_args()

    from tools.capability import ProfileMissing, load_profile
    profile = load_profile(args.client)
    if profile is None:
        raise ProfileMissing(
            f"clients/<slug>/profile.json required for {args.client}: "
            f"python3 -m tools.capability scaffold --client \"{args.client}\"")

    slug = "".join(ch if ch.isalnum() else "_" for ch in args.client).strip("_").lower()
    searches = None
    from agents.review import sweep_artifact_path
    try:
        p = sweep_artifact_path(args.client)  # L19 (also fixes the old cwd-relative path)
    except FileNotFoundError:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "cleaned", f"searches_{slug}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            searches = json.load(f)

    from tools.api.recompete import build_calendar
    cal = build_calendar(profile, searches=searches, months=args.months)
    print(f"[recompete] {cal['population']} expiring awards in window · "
          f"{len(cal['attack'])} attack / {len(cal['defend'])} defend",
          file=sys.stderr)
    for r in cal["attack"][:5]:
        print(f"  {r['score']:>3} · {r['pop_end']} · {r['recipient']} · "
              f"{r.get('awarding_office') or r.get('awarding_agency')}",
              file=sys.stderr)
    print(f"[out] data/state/recompete/{slug}.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
