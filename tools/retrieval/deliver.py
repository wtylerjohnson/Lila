"""Desktop delivery for retrieval research reports (operator order,
2026-08-19: "put report into desktop folder per protocol; if it's not
protocol, make it so").

The house delivery protocol copies gate-clean client deliverables to
~/Desktop/<Client>/ with pretty names. Research-depth measurement
reports now ride the same door with one hard difference: they carry a
loud INTERNAL RESEARCH banner and never wear the client template, so a
measurement can never be mistaken for a certified deliverable. The
release gates are untouched; this is an additive lane for internal
review files, the same trust posture as .INTERNAL-REVIEW.pdf exports.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BANNER = ("INTERNAL RESEARCH REPORT. Retrieval measurement output, not a "
          "certified client deliverable. No release gate has reviewed this "
          "file.")


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def render_html(report: dict, client_label: str) -> str:
    generated = report.get("generated_at") or datetime.now(
        timezone.utc).isoformat()
    parts: list[str] = []
    parts.append(
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_esc(client_label)} federal research depth</title>"
        "<style>body{font:14px/1.45 -apple-system,Segoe UI,sans-serif;"
        "margin:24px;color:#16212b;background:#fff}"
        ".banner{background:#8a1f11;color:#fff;padding:10px 14px;"
        "font-weight:700;border-radius:6px}"
        "h1{font-size:22px;margin:18px 0 4px}h2{font-size:16px;"
        "margin:22px 0 6px}table{border-collapse:collapse;width:100%;"
        "margin:8px 0}th,td{border:1px solid #cdd6de;padding:5px 8px;"
        "text-align:left;vertical-align:top}th{background:#eef2f5}"
        ".sent{color:#42525f;font-size:12px}"
        ".meta{color:#5a6a76;font-size:12px}</style></head><body>")
    parts.append(f"<div class='banner'>{_esc(BANNER)}</div>")
    parts.append(f"<h1>{_esc(client_label)}: federal research depth</h1>")
    parts.append(f"<div class='meta'>generated {_esc(generated)} · "
                 "lila retrieval stack (FTS5 BM25 + bge-base dense + RRF, "
                 "guards armed)</div>")

    book = report.get("book") or {}
    if book:
        parts.append("<h2>Measured federal book (USASpending, "
                     "recipient-exact)</h2>")
        parts.append(f"<p>{_esc(book.get('awards'))} awards, "
                     f"${book.get('total', 0):,.0f} total obligations.</p>")
        parts.append("<table><tr><th>Awarding agency</th>"
                     "<th>Office</th><th>Obligated</th></tr>")
        for agency, offices in sorted(
                (book.get("by_agency") or {}).items(),
                key=lambda x: -sum(x[1].values())):
            for office, amt in sorted(offices.items(), key=lambda x: -x[1]):
                parts.append(f"<tr><td>{_esc(agency)}</td>"
                             f"<td>{_esc(office)}</td>"
                             f"<td>${amt:,.0f}</td></tr>")
        parts.append("</table>")

    receipts = report.get("receipts") or {}
    if receipts:
        parts.append("<h2>Retrieval receipts per frame</h2>")
        parts.append("<table><tr><th>frame</th><th>returned</th>"
                     "<th>term rows</th><th>dense-only</th>"
                     "<th>guard-rejected</th><th>award-history excluded</th>"
                     "<th>family-deduped</th></tr>")
        for variant, rec in receipts.items():
            guards = rec.get("guard_rejected") or {}
            parts.append(
                f"<tr><td>{_esc(variant)}</td>"
                f"<td>{_esc(rec.get('returned'))}</td>"
                f"<td>{_esc(rec.get('term_route_rows'))}</td>"
                f"<td>{_esc(rec.get('dense_only_rows'))}</td>"
                f"<td>{_esc(sum(guards.values()) if guards else 0)}</td>"
                f"<td>{_esc(rec.get('award_history_excluded'))}</td>"
                f"<td>{_esc(rec.get('family_deduped_away'))}</td></tr>")
        parts.append("</table>")

    rows = report.get("union_rows") or report.get("rows") or []
    if rows:
        parts.append(f"<h2>Candidates (union, {len(rows)} rows; "
                     "top 100 shown)</h2>")
        parts.append("<table><tr><th>#</th><th>route</th><th>frame</th>"
                     "<th>agency</th><th>title</th><th>due</th>"
                     "<th>matched sentence</th></tr>")
        for row in rows[:100]:
            parts.append(
                f"<tr><td>{_esc(row.get('rank'))}</td>"
                f"<td>{_esc(row.get('match_route'))}</td>"
                f"<td>{_esc(row.get('variant', ''))}</td>"
                f"<td>{_esc(str(row.get('agency') or '')[:36])}</td>"
                f"<td>{_esc(str(row.get('title') or '')[:110])}</td>"
                f"<td>{_esc(str(row.get('deadline') or '')[:10])}</td>"
                f"<td class='sent'>{_esc(str(row.get('matched_sentence') or '')[:220])}"
                "</td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


def deliver(json_path: Path, client_label: str, *,
            desktop_dir: Optional[Path] = None) -> Path:
    report = json.loads(Path(json_path).read_text(encoding="utf-8"))
    html_text = render_html(report, client_label)
    base = Path(desktop_dir) if desktop_dir else (
        Path.home() / "Desktop" / client_label)
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = base / f"{client_label}_Federal_Research_Depth_{stamp}.html"
    out.write_text(html_text, encoding="utf-8")
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--client", required=True)
    args = ap.parse_args(argv)
    out = deliver(Path(args.artifact), args.client)
    print(f"DELIVERED {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
