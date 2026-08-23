#!/usr/bin/env python3
"""Release one complete, certified LILA bundle through existing gates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from agents.golden_press.product_bundle import (
    ProductReleaseBlocked,
    ProductReleaseError,
    build_complete_bundle,
)
from tools.slug import client_slug


ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--release", action="store_true",
                        help="required explicit external-release switch")
    parser.add_argument("--no-desktop", action="store_true",
                        help="retain the sealed bundle under data/releases only")
    args = parser.parse_args(argv)
    slug = client_slug(args.client)
    try:
        result = build_complete_bundle(
            client_name=args.client, slug=slug, root=ROOT,
            as_of=args.as_of, release_requested=args.release,
            deliver_to_desktop=not args.no_desktop)
    except ProductReleaseBlocked as exc:
        print(json.dumps({"status": "blocked", "problems": exc.problems},
                         indent=2), file=sys.stderr)
        return 2
    except ProductReleaseError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2),
              file=sys.stderr)
        return 1
    print(f"[out:lila-bundle] {result.bundle_path}")
    print(f"[out:lila-product] {result.html_path}")
    print(json.dumps({"status": "release", **result.__dict__}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
