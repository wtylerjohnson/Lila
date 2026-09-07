"""Markdown and CSV scorecard renderers. Internal review copy only."""

from __future__ import annotations

import csv
import io

from .models import CheckResult, Scorecard

MD_INTRO = (
    "Skeptic LeadRow / parent scorecard (eval.v0). "
    "A solicitation is not a lead. REJECT rows are never dropped."
)


def render_markdown(card: Scorecard) -> str:
    lines = [
        "# LeadRow relevance scorecard · eval.v0",
        "",
        MD_INTRO,
        "",
        f"Source: {card.source_label}",
        f"Lead rows scored: {len(card.lead_ids)}",
        f"REJECT rows retained: {len(card.reject_lead_ids)}",
        f"Invented rows: {card.invented_row_count}",
        "",
    ]
    if card.reject_lead_ids:
        lines.append(
            "REJECT receipts: " + ", ".join(card.reject_lead_ids))
        lines.append("")
    for scope, title in (
        ("lead", "Lead rows"),
        ("parent", "Parents"),
        ("pack", "Pack"),
    ):
        scoped = [item for item in card.checks if item.scope == scope]
        if not scoped:
            continue
        lines.append(f"## {title}")
        lines.append("")
        if scope == "pack":
            lines.extend(_table(scoped))
            lines.append("")
            continue
        by_subject: dict[str, list[CheckResult]] = {}
        for item in scoped:
            by_subject.setdefault(item.subject_id, []).append(item)
        for subject_id, rows in by_subject.items():
            tier = rows[0].lead_tier or ""
            headline = f"### `{subject_id}`"
            if tier:
                headline += f" · {tier}"
            failed = [
                item.check_id for item in rows
                if item.verdict.value == "FAIL"
            ]
            if failed:
                headline += " · FAIL " + ", ".join(failed)
            else:
                headline += " · no FAIL"
            lines.append(headline)
            lines.append("")
            lines.extend(_table(rows))
            lines.append("")
    return "\n".join(lines)


def render_csv(card: Scorecard) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow((
        "scope", "subject_id", "lead_tier", "check_id", "family",
        "verdict", "receipt",
    ))
    for item in card.checks:
        writer.writerow((
            item.scope,
            item.subject_id,
            item.lead_tier or "",
            item.check_id,
            item.family,
            item.verdict.value,
            item.receipt,
        ))
    return buf.getvalue()


def _table(rows: list[CheckResult]) -> list[str]:
    lines = [
        "| Check | Family | Verdict | Receipt |",
        "|---|---|---|---|",
    ]
    for item in rows:
        receipt = item.receipt.replace("|", "/")
        lines.append(
            f"| {item.check_id} | {item.family} | "
            f"{item.verdict.value} | {receipt} |"
        )
    return lines
