#!/usr/bin/env python3
"""Re-run Claude triage + research picture on an EXISTING search artifact.

    python3 run_picture.py --client "Recorded Future"

When the data fan-out succeeded but the Claude layer failed (CLI signed out,
usage-limit window, etc.), the notices are already on disk — re-running the
whole search wastes time and quota. This re-screens what's there and writes
the research picture, updating the same artifact the control room reads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--skip", nargs="*", default=[], help="'triage' and/or 'picture'")
    ap.add_argument("--guidance", default=None,
                    help="operator review guidance to refine the re-triage "
                         "(never invents a fit the evidence cannot bear)")
    args = ap.parse_args()

    from agents.review import load_approved
    strategy = load_approved(args.client)
    # Preserve the prior approval artifact for audit. Its sweep hash no longer
    # authorizes release after the replacement artifact commits.

    slug = "".join(c if c.isalnum() else "_" for c in args.client).strip("_").lower()
    from agents.review import sweep_artifact_path
    path = sweep_artifact_path(args.client)  # L19: gate-designated artifact, loud on miss
    if not os.path.exists(path):
        print(f"[picture] no search artifact at {path} — run the search first.",
              file=sys.stderr)
        return 1
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    results = out.get("results") or {}
    sam = results.get("sam.gov")
    sam = sam if isinstance(sam, list) else []
    print(f"[picture] loaded {len(sam)} notices from the existing artifact "
          f"(no sources re-queried, no quota spent)", file=sys.stderr)

    if not sam:
        from run_searches import _empty_sam_decision_receipt
        results['decision_coverage_verdict'] = _empty_sam_decision_receipt(results)

    strategy_summary = getattr(strategy, "pursuit_strategy", "") or ""

    if "triage" not in args.skip and sam:
        from agents.decisions.triage import triage_notices
        print(f"[triage] screening {len(sam)} notices ...", file=sys.stderr)
        if args.guidance:
            print(f"[triage] operator guidance in effect: {args.guidance[:80]}",
                  file=sys.stderr)
        verdicts = triage_notices(args.client, strategy_summary, sam,
                                  directive=args.guidance)
        out["results"]["triage"] = verdicts
        from run_searches import _decision_coverage_receipt
        results["decision_coverage_verdict"] = _decision_coverage_receipt(sam, verdicts)
        counts: dict = {}
        for v in verdicts.values():
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        print(f"[triage] {counts.get('pursue', 0)} pursue · "
              f"{counts.get('monitor', 0)} monitor · {counts.get('discard', 0)} discard"
              + (f" · {counts.get('unscreened', 0)} unscreened"
                 if counts.get("unscreened") else ""), file=sys.stderr)
        print(f"[RESULT] {counts.get('pursue', 0)} PURSUE-GRADE OPPORTUNITIES "
              f"(screened from {len(sam)})", file=sys.stderr)

    from tools.capability import require_profile
    from agents.assess.investigations import run_investigations
    from tools.artifacts import atomic_write_json
    # Keep the original source-availability cutoff on retries.
    try:
        out['results']['upstream_investigations'] = run_investigations(out, require_profile(args.client))
    except Exception as exc:
        out['results']['upstream_investigations'] = {'state': 'failed', 'error': str(exc), 'items': []}
    atomic_write_json(path, out)

    if "picture" not in args.skip:
        from agents.decisions.research_picture import (
            compose_research_picture, distill, render_markdown,
        )
        print("[picture] Anthropic synthesizing the research picture ...", file=sys.stderr)
        try:
            picture = compose_research_picture(args.client, strategy_summary, out["results"],
                                               operator_focus=out.get("search_scope"))
            out["results"]["research_picture"] = json.loads(picture.model_dump_json())
            review_dir = os.path.join(os.path.dirname(__file__), "data", "review")
            os.makedirs(review_dir, exist_ok=True)
            md_path = os.path.join(review_dir, f"{slug}.research_picture.md")
            with open(md_path, "w") as f:
                f.write(render_markdown(picture, sweep=distill(out["results"]),
                                        results=out["results"]))
            print(f"[picture] HEADLINE: {picture.headline}", file=sys.stderr)
            print(f"[picture] artifact -> {md_path}", file=sys.stderr)
        except Exception as exc:
            out['results']['research_picture'] = {'error': str(exc)}
            print(f"[picture] failed; subject investigations preserved: {exc}", file=sys.stderr)

    out["synthesis_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    from tools.artifacts import atomic_write_json
    atomic_write_json(path, out)
    try:
        from agents.assess.ledger import materialize_current_assess_run
        materialize_current_assess_run(args.client, sweep_path=path)
        print("[assess-ledger] refreshed after screen/picture", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - picture remains the primary output
        print(f"[assess-ledger] refresh skipped (non-blocking): {e}",
              file=sys.stderr)
    print(f"[done] -> {path}", file=sys.stderr)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
