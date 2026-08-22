#!/usr/bin/env python3
"""Step 1: intake form + website scrape -> Claude strategy/keywords -> Review Gate.

    python3 run_intake.py --submission acme_submission.json

Pipeline:
  1a. INTAKE   — load the client's form submission (from intake/form.html)
  1b. SCRAPE   — fetch + read their website / public data (live)
   →  PROFILE   — Claude reads both and writes keywords + a pursuit strategy + 3 searches
   →  ALERT      — persist as PENDING and alert you to review/approve (no search runs yet)

Needs ANTHROPIC_API_KEY for the profiling step.
After you approve, run run_searches.py.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()  # pick up SAM_GOV_API_KEY / ANTHROPIC_API_KEY from .env

from agents.alerts import desktop_alert  # noqa: E402
from agents.decisions.intake import build_strategy  # noqa: E402
from agents.company_research import research_company  # noqa: E402
from agents.review import request_approval  # noqa: E402
from agents.schemas import IntakeSubmission  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--submission", required=True, help="intake JSON from the form")
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--no-scrape", action="store_true", help="skip website find+scrape worker")
    ap.add_argument("--no-websearch", action="store_true", help="skip general company web search worker")
    args = ap.parse_args()

    submission = IntakeSubmission.model_validate_json(open(args.submission).read())
    print(f"[1a] intake loaded: {submission.client_name}", file=sys.stderr)

    print("[1b] company research: site scrape + web search (parallel) ...", file=sys.stderr)
    research = research_company(
        submission.client_name,
        website=str(submission.website) if submission.website else None,
        max_pages=args.max_pages,
        do_scrape=not args.no_scrape,
        do_web=not args.no_websearch,
    )
    if research.website:
        n = len(research.scrape.pages) if research.scrape else 0
        print(f"     site: {research.website} ({research.website_source}), {n} page(s) read", file=sys.stderr)
    else:
        print("     site: not found — proceeding on form + web research", file=sys.stderr)
    if research.web_findings:
        print(f"     web: {len(research.web_citations)} citation(s)", file=sys.stderr)
    for err in research.errors:
        print(f"     [warn] {err}", file=sys.stderr)
    if not research.web_citations:
        # truth purge 2026-08-03: the apexanalytix packet shipped product-only
        # entities off exactly this state, silently. Say it where the operator
        # is looking.
        print("     [warn] web research produced no cited findings; the "
              "competitor/reseller side will be scrape-grounded only. The "
              "review gate must not read product-only entities as a "
              "researched-empty rival market.", file=sys.stderr)

    print("[profile] Claude building strategy + keywords ...", file=sys.stderr)
    strategy = build_strategy(submission, research.scrape, research=research)

    request_approval(strategy, alert_fn=desktop_alert)  # persists PENDING + desktop alert
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
