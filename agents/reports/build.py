"""Report build chain: compose -> critic loop -> editor -> deterministic lint.

The chain never ships silently on failure. If the critic still fails after the
revision budget, or lint still fails after the editor's retry, the bundle carries
the violations and requires_human_review stays true — the runner shows you exactly
what is wrong at the review gate.

A deterministic number check backs up the LLM critic: every dollar figure in the
draft must literally appear in a fact the sentence cites. Belt and suspenders;
the belt is not an LLM.
"""

from __future__ import annotations

import re
from typing import Optional

from agents.decisions.arbiters import run_arbiters
from agents.decisions.engine import DecisionEngine
from agents.decisions.report_layers import compose_report, edit_report
from agents.reports.facts import FactPack
from agents.reports.lint import lint_text
from agents.reports.schemas import (
    ReportBundle,
    ReportDraft,
    ReportKind,
    ReportSection,
    SpecificityAudit,
    SpecIssue,
    SpecViolation,
)

_CITE_RE = re.compile(r"\[F(\d+)\]")
_MONEY_RE = re.compile(r"\$[\d][\d,]*")


def verify_numbers(draft: ReportDraft, pack: FactPack) -> list[SpecViolation]:
    """Every dollar figure in a sentence must appear in a fact that sentence cites."""
    violations: list[SpecViolation] = []
    for section in draft.sections:
        for sentence in re.split(r"(?<=[.!?])\s+", section.body):
            monies = _MONEY_RE.findall(sentence)
            if not monies:
                continue
            cited = [pack.get(f"F{m}") for m in _CITE_RE.findall(sentence)]
            cited_text = " ".join(f.text for f in cited if f)
            for money in monies:
                if money not in cited_text:
                    violations.append(SpecViolation(
                        section=section.heading,
                        quote=sentence.strip()[:120],
                        issue=SpecIssue.NUMBER_MISMATCH,
                        fix=f"{money} does not appear in the cited fact(s); use the fact's exact figure",
                    ))
    return violations


def build_report(
    pack: FactPack,
    kind: ReportKind,
    engine: Optional[DecisionEngine] = None,
    max_revisions: int = 2,
) -> ReportBundle:
    engine = engine or DecisionEngine()

    # 1. COMPOSE + 2. ARBITERS (primary critic + any active external judges)
    draft = compose_report(pack, kind, engine)
    audit: Optional[SpecificityAudit] = None
    arbiter_audits: dict = {}
    rounds = 0
    for rounds in range(1, max_revisions + 2):
        audit, arbiter_audits = run_arbiters(pack, draft, engine)
        hard = verify_numbers(draft, pack)
        if hard:
            audit.passed = False
            audit.violations = list(audit.violations) + hard
        if audit.passed or rounds > max_revisions:
            break
        draft = compose_report(pack, kind, engine, audit=audit, previous=draft)

    # 3. EDITOR (language pass) + deterministic lint gate, one retry
    edited = edit_report(draft, engine)
    lint = lint_text(edited.full_text(), valid_fact_ids=pack.ids())
    if not lint.ok:
        edited = edit_report(
            edited, engine,
            lint_violations=[f"{v.rule}: {v.detail} | {v.excerpt}" for v in lint.violations],
        )
        lint = lint_text(edited.full_text(), valid_fact_ids=pack.ids())

    # The editor must not have changed the facts: re-run the hard number check.
    post_edit = verify_numbers(edited, pack)
    if post_edit and audit:
        audit.passed = False
        audit.violations = list(audit.violations) + post_edit

    # 4. STANDING SECTIONS (deterministic — rendered from reference data, not the
    # LLM, so they bypass the critic/lint gates by design and cannot drift).
    # Vehicle considerations requested by GTM Group for every client report.
    from tools.vehicles import vehicles_heading, vehicles_section_body

    edited.sections.append(
        ReportSection(heading=vehicles_heading(), body=vehicles_section_body())
    )

    edited.requires_human_review = True
    return ReportBundle(
        draft=edited,
        audit=audit,
        arbiter_audits=arbiter_audits,
        audit_rounds=rounds,
        lint_ok=lint.ok,
        lint_violations=[f"{v.rule}: {v.detail}" for v in lint.violations],
        warnings=pack.warnings,
    )
