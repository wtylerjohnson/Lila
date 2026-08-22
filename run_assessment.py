#!/usr/bin/env python3
"""Run or resume the one-pause client assessment chain.

    python3 run_assessment.py --client riverbed
    python3 run_assessment.py --client riverbed --go --by wtjohnson
    python3 run_assessment.py --client riverbed --restart-from COMPOSING
    python3 run_assessment.py --client riverbed --refresh
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from agents.assessment_chain import (
    RESTARTABLE_STATES,
    AssessmentChain,
    AssessmentStateError,
    PauseRequired,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", required=True, help="canonical client slug")
    parser.add_argument(
        "--by", default=os.environ.get("USER") or "assessment-cli",
        help="operator identity recorded on manual transitions",
    )
    parser.add_argument(
        "--go", action="store_true",
        help="write the taxonomy GO marker and continue (only at the pause)",
    )
    parser.add_argument(
        "--restart-from", choices=RESTARTABLE_STATES,
        help="append a restart transition and resume from this state",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="delegate the complete refresh press to run_refresh_press.py",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = AssessmentChain().run(
            args.client,
            by=args.by,
            go=args.go,
            restart_from=args.restart_from,
            refresh=args.refresh,
        )
    except (AssessmentStateError, PauseRequired, ValueError) as exc:
        print(f"[assessment] REFUSED: {exc}", file=sys.stderr)
        return 2

    for row in result.transitions:
        print(
            f"[assessment] {result.slug} -> {row['state']} "
            f"at {row['at']} by {row['by']}: {row['note']}",
            file=sys.stderr,
        )
    print(json.dumps({
        "slug": result.slug,
        "state": result.state,
        "paused": result.paused,
        "failure": result.failure,
    }, sort_keys=True))
    return 2 if result.state == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())

