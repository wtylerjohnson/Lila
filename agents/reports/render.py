"""Deterministic Markdown renderer: draft + FactPack -> the deliverable file.

Citations [F#] become numbered source footnotes so the client sees references,
not internal fact ids. Rendering is pure string work — nothing here can drift.
"""

from __future__ import annotations

import re

from agents.reports.facts import FactPack
from agents.reports.schemas import ReportBundle, ReportDraft

_CITE_RE = re.compile(r"\s*\[F(\d+)\]")


def _logo_header() -> list[str]:
    """GTM logo on every client document — markdown deliverables carry it as an
    inline HTML img (renders in the dashboard preview and any md->HTML export)."""
    from agents.reports.capture_brief import _gtm_logo_b64, _gtm_logo_mime
    try:
        b64 = _gtm_logo_b64()
    except RuntimeError:
        return []  # asset genuinely unavailable: md reports stay text-only
    return [f'<img id="gtmLogoImg" src="data:{_gtm_logo_mime()};base64,{b64}" '
            'alt="GTM — Go To Market" width="140">', ""]


def render_markdown(bundle: ReportBundle, pack: FactPack) -> str:
    draft: ReportDraft = bundle.draft

    # Map cited fact ids -> footnote numbers, in order of first appearance.
    order: list[str] = []
    for m in _CITE_RE.finditer(draft.full_text()):
        fid = f"F{m.group(1)}"
        if fid not in order and pack.get(fid):
            order.append(fid)
    footnote = {fid: i + 1 for i, fid in enumerate(order)}

    def _sub(m: re.Match) -> str:
        fid = f"F{m.group(1)}"
        return f"[^{footnote[fid]}]" if fid in footnote else ""

    lines = [*_logo_header(), f"# {draft.title}", ""]
    lines += [f"*Prepared for {draft.client_name} · data as of {pack.as_of.isoformat()}*", ""]
    for s in draft.sections:
        lines += [f"## {s.heading}", "", _CITE_RE.sub(_sub, s.body).strip(), ""]

    if bundle.warnings:
        lines += ["## Data notes", ""]
        lines += [f"- {w}" for w in bundle.warnings]
        lines += [""]

    if order:
        lines += ["## Sources", ""]
        for fid in order:
            f = pack.get(fid)
            lines.append(f"[^{footnote[fid]}]: {f.text} ({f.source})")
        lines += [""]

    if not bundle.lint_ok or (bundle.audit and not bundle.audit.passed):
        lines += ["---", "**DO NOT SEND — QA gate failed.** See the .qa.json alongside this file.", ""]

    return "\n".join(lines)
