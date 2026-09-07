"""Thin skeptic scorer CLI for press/draft LeadRow JSON.

    python -m agents.leadgen.eval.score --help
    python -m agents.leadgen.eval.score --pack <receipt-or-eval.json>

Zero LLM. Zero network. Does not touch Step 1 intake extract.
Does not participate in lila_release.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .checks import score_input
from .load import ScoreLoadError, load_score_input
from .models import ScoreInput, Scorecard
from .render import render_csv, render_markdown

TINY_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "tiny_pack.json"
)

DESCRIPTION = (
    "Skeptic-grade objective scoring for OpportunityAssessment parents "
    "and LeadRow children. Input is press/draft LeadRow JSON plus "
    "parents. Output is per-check PASS/FAIL scorecards (MD/CSV). "
    "LEAD_T1/LEAD_T2 require A-F PASS. Solicitation-only T1/T2 fail. "
    "UNVERIFIED email cannot PASS C3. Auth-only cannot PASS D3. "
    "REJECT receipts are never silently dropped. "
    "A frozen Arista (or any client) pack: "
    "--pack path/to/<slug>.leadgen.json --md scorecard.md --csv "
    "scorecard.csv. Tiny fixture: "
    f"--pack {TINY_FIXTURE}."
)


def score_pack(
    payload: Any,
    overlays: Any = None,
    *,
    source_label: str | None = None,
) -> Scorecard:
    """Score one press receipt, draft batch, or eval pack."""

    pack = load_score_input(
        payload, overlays, source_label=source_label)
    return score_input(pack)


def score_score_input(pack: ScoreInput) -> Scorecard:
    return score_input(pack)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.leadgen.eval.score",
        description=DESCRIPTION,
    )
    parser.add_argument(
        "--pack",
        default=None,
        help=(
            "Path to a press receipt, draft batch, or eval.v0 pack "
            "(parents + leads + optional traces/overlays). Example: "
            f"{TINY_FIXTURE}"
        ),
    )
    parser.add_argument(
        "--overlays",
        default=None,
        help=(
            "Optional eval overlay JSON keyed by lead_id or "
            "assessment_id (email_status, auth_only, receipts)"
        ),
    )
    parser.add_argument(
        "--md",
        default=None,
        help="Write the Markdown scorecard to this path",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="Write the CSV scorecard to this path",
    )
    parser.add_argument(
        "--format",
        choices=("json", "md", "csv"),
        default="json",
        help="Stdout format (default json)",
    )
    args = parser.parse_args(argv)
    if args.pack is None:
        parser.print_help()
        print(
            "\nerror: --pack is required to score "
            "(see tiny fixture path above)",
            file=sys.stderr,
        )
        return 2
    try:
        card = score_pack(args.pack, args.overlays)
    except ScoreLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.md:
        path = Path(args.md)
        path.write_text(render_markdown(card), encoding="utf-8")
        print(f"[out] {path}", file=sys.stderr)
    if args.csv:
        path = Path(args.csv)
        path.write_text(render_csv(card), encoding="utf-8")
        print(f"[out] {path}", file=sys.stderr)
    if args.format == "md":
        text = render_markdown(card)
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    elif args.format == "csv":
        sys.stdout.write(render_csv(card))
    else:
        json.dump(
            card.model_dump(mode="json"), sys.stdout, indent=2,
            default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
