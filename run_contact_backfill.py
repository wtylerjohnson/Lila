#!/usr/bin/env python3
"""Backfill the contact graph from on-disk artifacts. ZERO live API calls.

    python3 run_contact_backfill.py                 # harvest + rebuild the index
    python3 run_contact_backfill.py --no-index      # harvest only, skip rebuild
    python3 run_contact_backfill.py --workspace DIR  # point at a different data/ dir

Walks the cleaned search artifacts, the raw search cache, and the permanent
notice cache; writes ContactObservations for every POC found, preserving each
notice's original posted date. Idempotent: safe to re-run — already-stored
observations are skipped, never duplicated. Notices with no POC data on disk are
logged as coverage gaps and skipped (live re-enrichment is out of scope).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.contact_graph.backfill import backfill  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill the federal contact graph (offline).")
    ap.add_argument("--workspace", help="path to the data/ directory (default: repo data/)")
    ap.add_argument("--no-index", action="store_true", help="skip rebuilding the derived index")
    args = ap.parse_args()

    report = backfill(
        workspace=Path(args.workspace) if args.workspace else None,
        rebuild_index=not args.no_index,
    )
    print(report.summary())

    # backfill is a research pass too — the outreach rail grows with it.
    # Non-blocking: a growth failure must not fail a successful backfill.
    try:
        from tools.contact_graph.outreach import OutreachList
        grown = OutreachList().grow_from_research(
            data_dir=Path(args.workspace) if args.workspace else None)
        print(f"[outreach] +{grown['added']} outreach-relevant contacts (list now {grown['total']})")
    except Exception as e:  # noqa: BLE001 — side effect only
        print(f"[outreach] growth skipped (non-blocking): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
