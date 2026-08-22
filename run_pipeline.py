#!/usr/bin/env python3
"""End-to-end: produce specific, verified SAM.gov data + an LLM fit rationale.

    python3 run_pipeline.py --profile data/raw/client_capability_profile.example.json \
        --posted-from 09/01/2024 --posted-to 09/30/2024 [--no-llm] [--limit 20]

Stages:
  1. DISCOVER  — SAM.gov live pull (needs SAM_GOV_API_KEY)        -> RawOpportunity[]
  2. VERIFY    — deterministic field checks                       -> keep verified only
  3. CORROBORATE — USAspending market evidence per opportunity    -> incumbents, typical $
  4. FIT       — Claude 'why this is a fit' rationale (needs ANTHROPIC_API_KEY)

Output: data/cleaned/pipeline_<from>_<to>.json  (also printed as a summary)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()  # pick up SAM_GOV_API_KEY / ANTHROPIC_API_KEY from .env

from agents.schemas import CapabilityProfile  # noqa: E402
from tools.api import SourceQuery  # noqa: E402
from tools.api.sam_gov import SamGovSource  # noqa: E402
from tools.api.usaspending import UsaSpendingSource  # noqa: E402
from tools.api.verification import verify_opportunity  # noqa: E402


def _parse_date(s: str):
    return datetime.strptime(s, "%m/%d/%Y").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--posted-from", required=True, help="MM/DD/YYYY")
    ap.add_argument("--posted-to", required=True, help="MM/DD/YYYY")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--no-llm", action="store_true", help="skip the Claude fit rationale")
    args = ap.parse_args()

    profile = CapabilityProfile.model_validate_json(open(args.profile).read())
    query = SourceQuery(
        naics_codes=profile.naics_codes,
        set_asides=profile.set_aside_eligibility,
        posted_from=_parse_date(args.posted_from),
        posted_to=_parse_date(args.posted_to),
        limit=args.limit,
    )

    # 1. DISCOVER
    print(f"[1/4] SAM.gov discovery for {profile.client_name} ...", file=sys.stderr)
    sam = SamGovSource()
    raw = sam.search(query)
    print(f"      {len(raw)} notices pulled", file=sys.stderr)

    # 2. VERIFY
    verified = []
    for opp in raw:
        vr = verify_opportunity(opp)
        if vr.verified:
            verified.append((opp, vr))
    print(f"[2/4] {len(verified)}/{len(raw)} passed verification", file=sys.stderr)

    usa = UsaSpendingSource()
    fit_engine = None
    if not args.no_llm:
        from agents.decisions.engine import DecisionEngine

        fit_engine = DecisionEngine()

    records = []
    for opp, vr in verified:
        # 3. CORROBORATE
        evidence = {}
        if opp.naics_code:
            evidence = usa.market_evidence(opp.naics_code, agency=opp.agency)

        record = {
            "opportunity": json.loads(opp.model_dump_json()),
            "verification": json.loads(vr.model_dump_json()),
            "market_evidence": evidence,
        }

        # 4. FIT (LLM)
        if fit_engine is not None:
            from agents.decisions.fit import assess_fit

            rationale = assess_fit(opp, profile, evidence, engine=fit_engine)
            record["fit_rationale"] = json.loads(rationale.model_dump_json())
            print(
                f"      {opp.source_id}  {rationale.verdict.value}  "
                f"(conf {rationale.confidence})",
                file=sys.stderr,
            )
        records.append(record)

    out_dir = os.path.join(os.path.dirname(__file__), "data", "cleaned")
    os.makedirs(out_dir, exist_ok=True)
    stamp = f"{args.posted_from}_{args.posted_to}".replace("/", "")
    out_path = os.path.join(out_dir, f"pipeline_{stamp}.json")
    with open(out_path, "w") as f:
        json.dump({"client": profile.client_name, "records": records}, f, indent=2)
    print(f"[done] {len(records)} verified records -> {out_path}", file=sys.stderr)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
