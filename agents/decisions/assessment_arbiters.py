"""Arbiter panel over the ASSESSMENT compose (client-flow F).

The markdown-report path has always had independent judges (arbiters.py:
primary Anthropic critic + dormant cross-vendor arbiters, consensus required).
This adapts the SAME panel to the capture-brief compose: the composed
CaptureBriefContent is flattened into a ReportDraft the critics know how to
audit against the FactPack, every active arbiter judges it, and violations
feed one recompose pass in the caller.

The OpenAI antagonist stays dormant until .env carries OPENAI_API_KEY +
LILA_ENABLE_ARBITER_OPENAI=on (arbiters.py docstring) — the moment it does,
every assessment build gets a cross-vendor adversarial review with zero code
changes here. Arbiter names and verdicts are INTERNAL (stderr + sidecar);
white-label holds client-side.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from agents.decisions.arbiters import merge_audits, run_arbiters
from agents.decisions.engine import DecisionEngine
from agents.reports.facts import FactPack
from agents.reports.schemas import (
    ReportDraft, ReportKind, ReportSection, SpecificityAudit,
)

__all__ = ["content_as_draft", "arbitrate_assessment", "merge_audits",
           "verify_fixes"]


def content_as_draft(content) -> ReportDraft:
    """Flatten CaptureBriefContent into the draft shape the critics audit.
    Composed prose only — deterministic template copy is the renderer's and
    is gated by the lint stack, not the panel."""
    sections = [
        ReportSection(heading="Thesis", body="\n\n".join(content.thesis)),
        ReportSection(heading="Marquee stats", body="\n".join(
            f"{s.number}{s.accent}: {s.context}" for s in content.stats)),
        ReportSection(heading="Action callout", body=content.action_callout),
        ReportSection(heading="Kill lines",
                      body=f"{content.kill_line_opps}\n{content.kill_line_news}"),
        ReportSection(heading="Partner callout", body=content.partner_callout),
        ReportSection(heading="Footer verification", body=content.footer_verification),
    ]
    for i, o in enumerate(content.opportunities):
        sections.append(ReportSection(
            heading=f"Opportunity {i + 1}: {o.headline}",
            body=(f"{o.agency_name} ({o.agency_abbr}) · {o.fit} · "
                  f"notice {o.notice_id} · due {o.due_label}\n{o.body}")))
    for label, items in (("Funding/policy news", content.news_funding),
                         ("Threat news", content.news_threat),
                         ("Agency news", content.news_agency),
                         ("Market news", content.news_market)):
        if items:
            sections.append(ReportSection(
                heading=label,
                body="\n".join(f"{n.recency} · {n.headline}: {n.why}"
                               for n in items)))
    return ReportDraft(client_name=content.client_name,
                       kind=ReportKind.ASSESSMENT,
                       title=f"{content.client_name} · Federal Opportunity Assessment",
                       sections=sections)


def arbitrate_assessment(
    pack: FactPack, content, engine: Optional[DecisionEngine] = None,
) -> tuple[SpecificityAudit, dict, Optional[str]]:
    """Run the full panel over composed content. Returns (merged verdict,
    per-arbiter audits, recompose directive or None). The caller owns the
    recompose (it holds the review context) and the re-audit."""
    merged, audits = run_arbiters(pack, content_as_draft(content),
                                  engine or DecisionEngine())
    directive = None
    if not merged.passed and merged.violations:
        fixes = "; ".join(
            f"{v.issue.value} in {v.section}: '{v.quote[:90]}' -> {v.fix}"
            for v in merged.violations[:12])
        directive = ("ARBITER PANEL FAILED THE DRAFT. Fix exactly these and "
                     "change nothing else: " + fixes)
    return merged, audits, directive


class FixCheck(BaseModel):
    resolved: bool
    reason: str = ""


class FixVerification(BaseModel):
    checks: list[FixCheck]


_VERIFY_SYSTEM = """You verify whether SPECIFIC, previously-raised violations
have been resolved in the current draft. Judge ONLY the listed violations
against the draft text provided: for each, is the objectionable claim fixed
exactly or removed? You are not auditing the document afresh; new
observations are out of scope. Be strict about the listed items and silent
about everything else."""


def verify_fixes(pack: FactPack, content, prior_violations: list[dict],
                 engine: Optional[DecisionEngine] = None) -> tuple[bool, list[str]]:
    """The promote-press audit (2026-07-10): a fresh full audit of a large
    draft finds NEW marginal nits every pass, so zero-flags-by-rerolling
    never terminates. The panel's word is final means its FIX LIST is the
    contract: verify each previously-raised violation is resolved, and
    consensus is met by construction. Full audits remain for fresh
    compositions."""
    if not prior_violations:
        return True, []
    engine = engine or DecisionEngine()
    draft = content_as_draft(content)
    result = engine.deliberate(
        layer="assessment-fix-verify",
        system_prompt=_VERIFY_SYSTEM,
        context={
            "prior_violations": [
                {"section": v.get("section"), "quote": v.get("quote"),
                 "required_fix": v.get("fix")} for v in prior_violations],
            "current_draft": draft.model_dump(mode="json"),
            "facts": [f.model_dump(mode="json") for f in pack.facts],
        },
        schema=FixVerification,
    )
    checks = list(result.checks)[:len(prior_violations)]
    unresolved = [f"{prior_violations[i].get('section')}: {c.reason[:140]}"
                  for i, c in enumerate(checks) if not c.resolved]
    # count mismatch = unverifiable = unresolved (never silently pass)
    if len(checks) < len(prior_violations):
        unresolved += [f"{v.get('section')}: not verified"
                       for v in prior_violations[len(checks):]]
    return not unresolved, unresolved
