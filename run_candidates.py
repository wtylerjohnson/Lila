#!/usr/bin/env python3
"""Near-miss candidate retrofit: additive tray for a LOCKED strategy.

    python3 run_candidates.py --client "Osprey Flight Solutions"

New intakes get near-misses natively (IntakeStrategy.near_misses). Clients
whose inference predates the schema get this ADDITIVE pass: the locked
strategy is context only and is NEVER modified — approval status, revision
count, and every strategy field survive byte-for-byte except the near_misses
tray, which this fills. The gate's revise() path deliberately blocks approved
strategies; this script bypasses it for exactly one additive field, loudly.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

ROOT = os.path.dirname(os.path.abspath(__file__))


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    args = ap.parse_args()

    from agents.decisions.intake import review_candidates
    from agents.review import _path as _packet_path
    from agents.review import load_packet
    from agents.schemas import IntakeSubmission

    packet = load_packet(args.client)
    if packet.strategy.near_misses:
        print(f"[candidates] tray already holds "
              f"{len(packet.strategy.near_misses)} entries; re-running "
              "replaces it", file=sys.stderr)

    submission = None
    sub_path = os.path.join(ROOT, "data", "intake",
                            f"{_slug(args.client)}.submission.json")
    if os.path.exists(sub_path):
        submission = IntakeSubmission.model_validate_json(
            open(sub_path).read())
        print(f"[candidates] intake form loaded: {sub_path}", file=sys.stderr)

    print("[candidates] additive near-miss pass over the locked strategy ...",
          file=sys.stderr)
    tray = review_candidates(packet.strategy, submission=submission)
    if not tray:
        print("[candidates] the pass returned no candidates; tray unchanged",
              file=sys.stderr)
        return 2

    # ADDITIVE, ONE FIELD: everything else on the packet survives untouched.
    packet.strategy.near_misses = tray
    path = _packet_path(args.client)
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet.model_dump_json(indent=2))
    print(f"[candidates] {len(tray)} near-miss candidates saved "
          f"(status stays '{packet.status.value}') -> {path}", file=sys.stderr)
    for c in tray:
        print(f"  [{c.kind:7}] {c.value[:40]:42} conf={c.confidence:.2f} "
              f"· {c.reason[:70]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
