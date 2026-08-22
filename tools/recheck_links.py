#!/usr/bin/env python3
"""Standalone link recheck for an ALREADY-DELIVERED report. No press.

    python3 tools/recheck_links.py <report.html> [--json out.json]

Reads a delivered report file, verifies every anchor live, and emits the
same health report the press emits. Read-only with respect to the report:
the file is never modified. Nothing here blocks anything; this is the tool
for answering "are the links in the thing I already sent still good."
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.link_health import (  # noqa: E402
    extract_anchors, health_report, verify_anchors,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", help="path to a delivered report .html")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the full health report here")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--per-domain", type=int, default=2)
    args = ap.parse_args(argv)

    html = open(args.report, encoding="utf-8", errors="replace").read()
    anchors = extract_anchors(html)
    checkable = [a for a in anchors if not a["href"].startswith("#")]
    print(f"[recheck] {os.path.basename(args.report)}: {len(anchors)} anchors "
          f"({len(checkable)} fetchable, "
          f"{len(anchors) - len(checkable)} in-page)", file=sys.stderr)

    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"[recheck] {done}/{total} distinct urls", file=sys.stderr)

    results = verify_anchors(anchors, global_concurrency=args.concurrency,
                             per_domain_concurrency=args.per_domain,
                             progress=progress)
    report = health_report(anchors, results)
    t = report["totals"]
    print(f"\nTOTAL {t['anchors']} anchors · {t['distinct_urls']} distinct urls")
    print(f"  live {t['live']} · redirected {t['redirected']} · "
          f"degraded {t['degraded']} "
          f"({t['degraded_share'] * 100:.1f}% of fetchable) · "
          f"in-page {t['in_page']}")
    print("\nBY CLASS")
    for klass, counts in report["by_class"].items():
        print(f"  {klass:22s} " + " · ".join(
            f"{k} {v}" for k, v in sorted(counts.items())))
    if report["degraded_anchors"]:
        print("\nDEGRADED (row is kept; link renders as plain text)")
        for d in report["degraded_anchors"][:20]:
            print(f"  [{d['klass']}] {d['status'] or d['error']}  "
                  f"{d['href'][:88]}")
    if report["non_canonical"]:
        print("\nNON-CANONICAL (a canonical form is derivable)")
        for n in report["non_canonical"][:20]:
            print(f"  [{n['klass']}] {n['href'][:70]}\n      -> {n['expected'][:70]}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"report_file": os.path.abspath(args.report),
                       "health": report, "results": results}, f, indent=2)
        print(f"\n[out] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
