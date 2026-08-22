#!/usr/bin/env python3
"""Step 4: build a client report — teaser (free) or pre-assessment (flagship).

    python3 run_report.py --client "PlanetiQ" --kind teaser
    python3 run_report.py --client "PlanetiQ" --kind pre-assessment

Chain: FactPack (deterministic, sourced) -> composer -> specificity critic
(drift control, bounded revision loop) -> language editor -> deterministic lint.
Output: data/reports/<client>.<kind>.md (+ .qa.json with the audit trail).

The report NEVER ships itself: if any gate failed, the file is stamped DO NOT SEND
and the QA trail says exactly why. Needs ANTHROPIC_API_KEY and the artifacts from
run_searches.py / run_qualify.py.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.alerts import desktop_alert  # noqa: E402
from agents.reports.build import build_report  # noqa: E402
from agents.reports.facts import build_fact_pack  # noqa: E402
from agents.reports.render import render_markdown  # noqa: E402
from agents.reports.schemas import ReportKind  # noqa: E402
from agents.review import ReviewPacket, _slug  # noqa: E402

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports")

_KINDS = {"teaser": ReportKind.TEASER, "pre-assessment": ReportKind.PRE_ASSESSMENT}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--kind", required=True, choices=sorted(_KINDS))
    ap.add_argument("--max-revisions", type=int, default=2,
                    help="critic->composer revision rounds before failing loud")
    args = ap.parse_args()

    kind = _KINDS[args.kind]
    print(f"[facts] building FactPack for {args.client} ...", file=sys.stderr)
    pack = build_fact_pack(args.client)
    print(f"        {len(pack.facts)} facts "
          f"({len(pack.market_fact_ids)} market, {len(pack.competitor_fact_ids)} competitor, "
          f"{len(pack.opportunity_fact_ids)} opportunity), {len(pack.warnings)} warning(s)",
          file=sys.stderr)
    if not pack.facts:
        print("[stop] no facts — run run_searches.py and run_qualify.py first.", file=sys.stderr)
        return 1

    print(f"[chain] compose -> critic -> editor ({kind.value}) ...", file=sys.stderr)
    bundle = build_report(pack, kind, max_revisions=args.max_revisions)
    status = "PASS" if (bundle.lint_ok and bundle.audit and bundle.audit.passed) else "FAILED GATES"
    print(f"        critic rounds: {bundle.audit_rounds}, lint: {'ok' if bundle.lint_ok else 'FAIL'} "
          f"-> {status}", file=sys.stderr)

    os.makedirs(REPORT_DIR, exist_ok=True)
    base = os.path.join(REPORT_DIR, f"{_slug(args.client)}.{kind.value}")
    md_path = base + ".md"
    with open(md_path, "w") as f:
        f.write(render_markdown(bundle, pack))
    with open(base + ".qa.json", "w") as f:
        f.write(bundle.model_dump_json(indent=2))

    fake_packet = ReviewPacket.model_construct(client_name=args.client)
    desktop_alert(fake_packet, md_path)
    print(f"[done] report -> {md_path}", file=sys.stderr)
    print(f"       QA     -> {base}.qa.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
