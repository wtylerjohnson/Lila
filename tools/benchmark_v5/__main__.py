"""Offline v5 custody and evidence-state scoring for new, independently frozen cases."""
import argparse
import json

from .custody import prepare
from .scorer import score
from .synthetic import demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("case")
    p.add_argument("output")
    for field in ("input-trust", "anchor-output", "reviewer-one", "reviewer-two"):
        p.add_argument("--" + field, required=True)
    p = commands.add_parser("score")
    p.add_argument("case")
    p.add_argument("packet_dir")
    p.add_argument("result")
    for field in ("anchor", "support-trust", "support-trust-sha256", "support-dir"):
        p.add_argument("--" + field, required=True)
    p = commands.add_parser("demo", help="Author synthetic fixtures in a NEW directory; never calls a live product/source")
    p.add_argument("output")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.case, args.output, args.input_trust, args.anchor_output,
                             args.reviewer_one, args.reviewer_two)
        elif args.command == "score":
            result = score(args.case, args.packet_dir, args.anchor, args.support_trust,
                           args.support_trust_sha256, args.support_dir, args.result)
        else:
            result = demo(args.output)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, IndexError, AttributeError) as exc:
        parser.exit(2, f"Validation failed; no comparable score: {exc}\n")


if __name__ == "__main__":
    main()
