#!/usr/bin/env python3
"""Approve or reject a pending intake strategy — the Human Review Gate.

    python3 approve.py "Acme Federal Solutions LLC" --approve [--note "..."]
    python3 approve.py "Acme Federal Solutions LLC" --reject  --note "tighten NAICS"

Until a strategy is approved here, run_searches.py refuses to launch any search.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.review import decide  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("client_name")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--reject", action="store_true")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    packet = decide(args.client_name, approve=args.approve, note=args.note)
    print(f"{packet.client_name}: {packet.status.value}")
    if packet.is_approved:
        print("Approved — run: python3 run_searches.py --client "
              f"\"{packet.client_name}\" --posted-from MM/DD/YYYY --posted-to MM/DD/YYYY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
