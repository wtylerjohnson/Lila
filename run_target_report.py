#!/usr/bin/env python3
"""TARGET-stage client deliverable: render the contact plan in the locked format.

    python3 run_target_report.py --client "Recorded Future"

Reads data/review/<slug>.contacts.json (from run_target.py). Deterministic —
no LLM at render time. Output:
    data/reports/<slug>.target_report.html
    ~/Desktop/<Client>/<Client>_Target_Report_<date>.html   (QA-clean only)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.reports.target_report import qa_target_report, render_target_report  # noqa: E402
from agents.reports.link_integrity import (  # noqa: E402
    render_link_integrity_internal_md,
    run_client_link_gate,
)
from agents.review import REVIEW_DIR, _slug  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, "data", "reports")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--offline", action="store_true",
                    help="skip external link requests; list them for manual check")
    args = ap.parse_args()
    slug = _slug(args.client)

    plan_path = os.path.join(REVIEW_DIR, f"{slug}.contacts.json")
    if not os.path.exists(plan_path):
        print(f"[target-report] no contact plan at {plan_path} — run the contact step first.",
              file=sys.stderr)
        return 1
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    titles = None
    titles_path = os.path.join(REVIEW_DIR, f"{slug}.titles.json")
    if os.path.exists(titles_path):
        with open(titles_path, encoding="utf-8") as f:
            titles = json.load(f)
    vetting = None
    vetting_path = os.path.join(REVIEW_DIR, f"{slug}.vetting.json")
    if os.path.exists(vetting_path):
        with open(vetting_path, encoding="utf-8") as f:
            vetting = json.load(f)

    problems = qa_target_report(plan)

    html = render_target_report(plan, titles=titles, vetting=vetting)
    from agents.reports.links import normalize_sam_workspace_links
    links = normalize_sam_workspace_links(html)
    html = links.html
    # the target report is client HTML too: same hard gates as the assessment —
    # no internal terminology, every rendered contact graded + sourced, and the
    # GTM logo present
    from agents.reports.lint import (
        lint_client_terminology, lint_contact_rendering, lint_gtm_logo,
        lint_federal_link_construction, lint_sam_workspace_links,
        lint_whitelabel,
    )
    problems += [v.detail for v in (lint_sam_workspace_links(html).violations
                                    + lint_federal_link_construction(html).violations
                                    + lint_client_terminology(html).violations
                                    + lint_contact_rendering(html).violations
                                    + lint_gtm_logo(html).violations
                                    + lint_whitelabel(html).violations)]
    link_outcome = run_client_link_gate(html, enabled=not args.offline)
    problems += [violation.detail for violation in link_outcome.violations]

    for p in problems:
        print(f"[QA] FAIL: {p}", file=sys.stderr)
    stamp = "" if not problems else ".DO-NOT-SEND"
    os.makedirs(REPORT_DIR, exist_ok=True)
    artifact_base = os.path.join(REPORT_DIR, f"{slug}.target_report")
    out = artifact_base + f"{stamp}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[out] {out}", file=sys.stderr)

    sidecar = artifact_base + ".link-integrity.internal.md"
    try:
        if link_outcome.manual_checks or link_outcome.claim_warnings:
            with open(sidecar, "w", encoding="utf-8") as f:
                f.write(render_link_integrity_internal_md(
                    f"{args.client} · target report", link_outcome))
            print(f"[links] INTERNAL sidecar -> {sidecar}", file=sys.stderr)
        elif os.path.exists(sidecar):
            os.remove(sidecar)
    except OSError as exc:
        print(f"[links] INTERNAL sidecar unavailable ({exc})", file=sys.stderr)

    if not problems:
        folder = os.path.expanduser(f"~/Desktop/{args.client}")
        os.makedirs(folder, exist_ok=True)
        pretty = os.path.join(
            folder, f"{args.client.replace(' ', '_')}_Target_Report_{date.today().isoformat()}.html"
        )
        shutil.copy(out, pretty)
        print(f"[out] {pretty}", file=sys.stderr)
        print(f"Target report built and QA-clean -> Desktop/{args.client}/", file=sys.stderr)
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
