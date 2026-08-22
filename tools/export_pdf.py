"""Batch PDF export — headless Chrome over the shipped HTML deliverables.

HARD RULE: every gate runs on the HTML BEFORE any PDF exists. A failed gate
produces no PDF, ever — a DO-NOT-SEND document must never have a
shippable-looking PDF sibling. The gate stack here is the FULL set (identity,
contacts, terminology, counts, white-label, forecast containment) no matter
which pipeline wrote the HTML: the PDF boundary is where "shippable" is
decided.

The views' print stylesheet (navigation and print control hidden, links as
plain text, house print-safe palette) is what Chrome renders, so the PDF is
the send-around copy by construction.

    python3 tools/export_pdf.py data/reports/<slug>.assessment.client.html ...

PDFs land beside their HTML with the same base name and inherit its
output-directory and gitignore rules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# the gate imports live in agents/ — make the CLI form (python3
# tools/export_pdf.py) resolve them exactly like the imported form does
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# standard install locations, checked in order; PATH lookups after
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/opt/homebrew/bin/chromium",
]
WHICH_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]


class ChromeNotFound(RuntimeError):
    pass


def _atomic_write_text(path: str, text: str) -> None:
    """Replace normalized HTML without risking a partial client artifact."""
    parent = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.",
                               suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def find_chrome() -> str:
    """Resolve the headless-Chrome binary, or fail with an actionable message."""
    env = os.environ.get("LILA_CHROME")
    if env and os.path.exists(env):
        return env
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    for name in WHICH_NAMES:
        w = shutil.which(name)
        if w:
            return w
    raise ChromeNotFound(
        "headless Chrome not found: install Google Chrome or Chromium, or set "
        "LILA_CHROME to the browser binary. Checked: "
        + ", ".join(CHROME_CANDIDATES + WHICH_NAMES))


def export_pdf(html_path: str, pdf_path: str | None = None,
               chrome: str | None = None, timeout: int = 120) -> str:
    """Render one HTML file to PDF. No gating here — use export_deliverable
    for anything a client could ever see."""
    chrome = chrome or find_chrome()
    html_path = os.path.abspath(html_path)
    if pdf_path is None:
        pdf_path = os.path.splitext(html_path)[0] + ".pdf"
    pdf_path = os.path.abspath(pdf_path)
    cmd = [chrome, "--headless", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--no-pdf-header-footer",
           f"--print-to-pdf={pdf_path}", f"file://{html_path}"]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.exists(pdf_path) \
            or os.path.getsize(pdf_path) == 0:
        raise RuntimeError(
            f"PDF export failed for {html_path} (rc={proc.returncode}): "
            + proc.stderr.decode(errors="replace")[-400:])
    return pdf_path


def run_gates(html_text: str) -> list:
    """The full hard-gate stack, identical for every caller."""
    from agents.reports.lint import (
        lint_brief_identity, lint_client_bleed, lint_client_terminology,
        lint_contact_rendering, lint_counts, lint_emdash,
        lint_federal_link_construction, lint_forecast_context, lint_horizon,
        lint_sam_workspace_links, lint_whitelabel,
    )
    return (lint_sam_workspace_links(html_text).violations
            + lint_federal_link_construction(html_text).violations
            + lint_brief_identity(html_text).violations
            + lint_contact_rendering(html_text).violations
            + lint_client_terminology(html_text).violations
            + lint_counts(html_text).violations
            + lint_whitelabel(html_text).violations
            + lint_forecast_context(html_text).violations
            + lint_horizon(html_text).violations
            + lint_client_bleed(html_text).violations
            + lint_emdash(html_text).violations)


def export_deliverable(html_path: str, pdf_path: str | None = None,
                       chrome: str | None = None,
                       link_checks_enabled: bool = True,
                       expected_federal_links=(),
                       ) -> tuple[str | None, list]:
    """Gates BEFORE Chrome: returns (pdf_path, []) only when every gate
    passes; otherwise (None, violations) and no PDF file exists."""
    from agents.reports.lint import LintViolation
    if "DO-NOT-SEND" in os.path.basename(html_path):
        # belt and braces: a stamped file is refused by NAME even if its
        # content would pass the gates today
        return None, [LintViolation(
            rule="do_not_send_stamp",
            detail=f"{os.path.basename(html_path)} is stamped DO-NOT-SEND — "
                   "no PDF is ever produced for a stamped artifact")]
    with open(html_path, encoding="utf-8") as f:
        html_text = f.read()
    if "DO NOT SEND" in html_text:
        # the internal view self-labels in its eyebrow; a document that says
        # DO NOT SEND never gets a send-around-shaped sibling
        return None, [LintViolation(
            rule="do_not_send_content",
            detail="document is self-labeled DO NOT SEND — no PDF is produced "
                   "for internal artifacts")]

    # Link normalization is a byte-preserving post-render transform and runs
    # before the ordinary release lints.  A malformed workspace URL remains in
    # the string and is an unsuppressible exporter refusal with section context.
    from agents.reports.links import (
        format_workspace_link_issue, normalize_sam_workspace_links,
    )
    normalized = normalize_sam_workspace_links(html_text)
    if normalized.remaining:
        return None, [LintViolation(
            rule="sam_workspace_link",
            detail=format_workspace_link_issue(issue),
            excerpt=issue.url,
        ) for issue in normalized.remaining]
    if normalized.html != html_text:
        _atomic_write_text(html_path, normalized.html)
    html_text = normalized.html

    violations = run_gates(html_text)
    if violations:
        return None, violations

    # This is the final client boundary: external links are checked on the
    # exact normalized bytes Chrome will receive.  UNVERIFIABLE links remain
    # warning-only and are preserved in an adjacent INTERNAL sidecar.
    from agents.reports.link_integrity import (
        render_link_integrity_internal_md, run_client_link_gate,
    )
    from tools.atomic_io import atomic_write_text
    link_outcome = run_client_link_gate(
        html_text, enabled=link_checks_enabled,
        expected_federal_links=expected_federal_links)
    sidecar_path = (
        html_path[:-len(".html")] + ".link-integrity.internal.md"
        if html_path.lower().endswith(".html")
        else html_path + ".link-integrity.internal.md"
    )
    try:
        if (link_outcome.violations or link_outcome.manual_checks
                or link_outcome.claim_warnings):
            atomic_write_text(
                sidecar_path,
                render_link_integrity_internal_md(
                    os.path.basename(html_path), link_outcome),
            )
        elif os.path.exists(sidecar_path):
            os.remove(sidecar_path)
    except OSError as exc:
        print(f"[links] INTERNAL sidecar unavailable ({exc})", file=sys.stderr)
    if link_outcome.violations:
        return None, list(link_outcome.violations)
    return export_pdf(html_path, pdf_path, chrome), []


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Gated batch PDF export.")
    ap.add_argument("html", nargs="+", help="HTML deliverables to export")
    args = ap.parse_args(argv)
    rc = 0
    for path in args.html:
        pdf, violations = export_deliverable(path)
        if pdf:
            print(f"[pdf] {pdf}")
        else:
            rc = 2
            for v in violations[:5]:
                print(f"[pdf] REFUSED {path}: {v.rule} — {v.detail}",
                      file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
