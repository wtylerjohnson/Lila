#!/usr/bin/env python3
"""Step 1: name-only company mastery -> review packet (optional auto-approve).

    python3 run_intake.py --client "Acme Federal Solutions LLC"
    python3 run_intake.py --submission acme_submission.json

A company name is the only required input. The intake form is optional
enrichment. Identity is resolved before any deep scrape or web probe.
Opportunity search and lead generation do not run here.

The review gate mechanism (packet, journal, approve.py, load_approved) is
preserved. By default LILA_ENABLE_INTAKE_AUTO_APPROVE is ON and decide()
runs after the artifacts exist, so the flow does not wait for a click.
Set the toggle off (or pass --no-auto-approve) to restore the human click.
scope.preset is never invented.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.alerts import desktop_alert  # noqa: E402
from agents.decisions.engine import DecisionEngine, research_engine  # noqa: E402
from agents.intake.pipeline import (  # noqa: E402
    run_step1,
    submission_from_client_name,
)
from tools.scrape.site import SITE_MASTERY_MAX_PAGES  # noqa: E402
from agents.schemas import IntakeSubmission  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--client", help="company name only (required input)")
    src.add_argument("--submission", help="optional intake JSON from the form")
    ap.add_argument("--website", help="optional official website enrichment")
    ap.add_argument("--max-pages", type=int, default=SITE_MASTERY_MAX_PAGES)
    ap.add_argument("--no-scrape", action="store_true",
                    help="skip website find+scrape worker")
    ap.add_argument("--no-websearch", action="store_true",
                    help="skip identity search and structured web probes")
    ap.add_argument(
        "--no-auto-approve", action="store_true",
        help="leave the packet PENDING (restore the human click for this run)",
    )
    return ap


def load_submission(args: argparse.Namespace) -> IntakeSubmission:
    if args.submission:
        submission = IntakeSubmission.model_validate_json(
            open(args.submission).read())
        if args.website and not submission.website:
            submission = submission.model_copy(
                update={"website": args.website})
        return submission
    return submission_from_client_name(args.client, website=args.website)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    submission = load_submission(args)
    print(f"[1] name-only intake: {submission.client_name}", file=sys.stderr)
    print("[1] identity resolution before deep research ...", file=sys.stderr)

    do_web = not args.no_websearch
    result = run_step1(
        submission,
        engine=DecisionEngine(),
        research_engine=research_engine() if do_web else None,
        max_pages=args.max_pages,
        do_scrape=not args.no_scrape,
        do_web=do_web,
        auto_approve=False if args.no_auto_approve else None,
        alert_fn=desktop_alert,
    )
    ident = result.identity
    print(f"     identity: {ident.status}"
          + (f" · {ident.official_domain}" if ident.official_domain else ""),
          file=sys.stderr)
    if ident.question:
        print(f"     question: {ident.question}", file=sys.stderr)
    if result.dossier:
        print(f"     dossier: {len(result.dossier.offerings)} offerings, "
              f"{len(result.dossier.unknowns)} unknowns", file=sys.stderr)
    store = (result.yield_receipt or {}).get("store") or {}
    print(f"     yield store: {store.get('status', 'unknown')}",
          file=sys.stderr)
    if result.adversarial:
        print(f"     adversarial: complete={result.adversarial.complete} "
              f"passed={result.adversarial.passed}", file=sys.stderr)
    ready = result.readiness or {}
    receipts = ready.get("receipts") or []
    failed = [r["id"] for r in receipts if not r.get("ok")]
    print(f"     readiness: "
          + ("all E1-E8 passed" if ready.get("all_ok")
             else ("failed " + ", ".join(failed) or "incomplete")),
          file=sys.stderr)
    if result.auto_approved:
        print("[gate] auto-passthrough via decide() "
              "(LILA_ENABLE_INTAKE_AUTO_APPROVE); searches still not launched",
              file=sys.stderr)
    else:
        print("[gate] PENDING human review; searches are blocked",
              file=sys.stderr)
    for err in result.errors:
        print(f"     [warn] {err}", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
