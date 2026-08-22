#!/usr/bin/env python3
"""Print the three derived rollups for a pack. Offline, read-only.

    python3 tools/pack_rollups.py <evidence_pack.json> [--alias "TD SYNNEX"]...
    python3 tools/pack_rollups.py <pack> --json out.json

Reads a pack already on disk and prints category_spend, prime_rollup and
competitor_landscape as tables. No sweep, no network, no model call, and the
pack file is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.records import EvidencePack  # noqa: E402
from agents.golden_press.rollups import (  # noqa: E402
    build_rollups, format_tables,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack", help="path to a *.evidence_pack.json")
    ap.add_argument("--alias", action="append", default=None,
                    help="client entity alias; repeatable. Overrides the "
                         "pack's client_entity_aliases when given.")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    raw = open(args.pack, "rb").read()
    print(f"PACK   {os.path.abspath(args.pack)}")
    print(f"       sha1  {hashlib.sha1(raw).hexdigest()}")
    print(f"       bytes {len(raw):,}")
    pack = EvidencePack.model_validate(json.loads(raw))
    print(f"       client {pack.client_name}  records {len(pack.records)}")
    print()

    aliases = args.alias if args.alias else None
    rollups = build_rollups(pack, aliases)
    print(format_tables(rollups, client=pack.client_name))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rollups, fh, indent=1)
        print(f"\n[out] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
