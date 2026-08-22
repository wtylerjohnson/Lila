"""Report decision layers (Step 4, Claude): composer, specificity critic, editor.

Three separate agents with three narrow jobs, chained by agents/reports/build.py:

  COMPOSER — writes the draft from the FactPack. May not state anything the pack
             does not contain; cites [F#] on every claim.
  CRITIC   — controls drift and vagueness: audits the draft against the pack,
             returns violations; the composer revises until the critic passes
             (bounded rounds).
  EDITOR   — language pass: cuts LLM verbosity and filler. Style is enforced
             afterwards by the deterministic lint (agents/reports/lint.py); the
             editor gets one retry with lint violations attached.
"""

from __future__ import annotations

from typing import Optional

from agents.decisions.engine import DecisionEngine
from agents.reports.facts import FactPack
from agents.reports.lint import BANNED_PHRASES
from agents.reports.schemas import ReportDraft, ReportKind, SpecificityAudit

_COMPOSE_TEASER = """\
You write the TEASER report of a LILA: a short, sharp
free deliverable that proves we know this market. Its client-facing name is
"Federal Snapshot" — use that as the title and call it "this snapshot" in copy.
Exactly three sections:

1. "Market size" — the federal spend in the client's NAICS lanes: award counts,
   dollars obligated, median and largest awards. Name the window the data covers.
2. "Who is winning" — the incumbent competitors, by name.
3. "Five opportunities" — the top five identified opportunities: title, agency,
   set-aside, value if known, response deadline. If fewer than five facts exist,
   list what exists and state the count plainly. NEVER pad.

Rules — these are the product:
- Every claim cites a fact id in square brackets, e.g. [F3]. You may ONLY state
  what the provided facts contain. No industry generalities, no filler context.
- Numbers must match their cited fact exactly.
- Plain, direct sentences. No em dashes. Short is good.
- If warnings note data gaps, state them in one honest line, not around them.
"""

_COMPOSE_PRE_ASSESSMENT = """\
You write the PRE-ASSESSMENT report of a LILA: the paid
flagship deliverable, a comprehensive opportunity map. Sections:

1. "Read this first" — 3-5 sentences: the size of the client's federal lane, who
   holds it today, and the single most actionable finding.
2. "Market" — per-NAICS spend: award counts, dollars, median/max, agency scope.
3. "Competitive field" — incumbents by name, per NAICS lane.
4. "Opportunity map" — EVERY qualified opportunity, grouped by issuing agency:
   title, ids, NAICS, set-aside, value, deadline, fit verdict, and the specific
   reason it fits (from the fit rationale facts).
5. "Pursuit order" — a ranked sequence with the reason each item earns its rank
   (deadline pressure, fit strength, set-aside advantage). Only rank what exists.
6. "Gaps and honest caveats" — data warnings, weak spots, what we could not verify.

Rules — these are the product:
- Every claim cites a fact id in square brackets, e.g. [F3]. You may ONLY state
  what the provided facts contain. No industry generalities, no filler.
- Numbers must match their cited fact exactly.
- Plain, direct sentences. No em dashes.
- Specificity is the value: names, numbers, dates, ids. A vague sentence is a defect.
"""

_CRITIC_SYSTEM = """\
You are the specificity critic of a LILA. You receive a
report draft and the FactPack it was built from. Your only job is finding defects:

- unsupported_claim: any statement not backed by the fact it cites (or citing nothing)
- number_mismatch: any figure that differs from the cited fact
- vague: hedged or generic phrasing where a fact permits a specific one ("several
  agencies" when facts name them; "significant spend" when a dollar figure exists)
- missing_citation: claims with no [F#]
- drift: content beyond the FactPack's scope (industry background, invented context,
  advice not grounded in a fact)

Check EVERY sentence against the pack. Quote the offending text exactly and say
precisely how to fix it, including the correct fact id. passed=true only if there
are zero violations. Do not comment on style or tone; that is another agent's job.
"""

_EDITOR_SYSTEM = """\
You are the language editor of a LILA. Rewrite the draft's
prose to be plain and tight. Cut LLM verbosity: filler transitions, hedge words,
consultant vocabulary, throat-clearing, restating the heading inside the section.

HARD constraints:
- Do not add, remove, or alter any fact, number, name, date, or [F#] citation.
- Do not reorder or rename sections. Edit sentences only.
- No em dashes. Use commas or periods.
- Banned vocabulary (non-exhaustive): {banned}.
- Shorter is better. If a sentence adds no information, delete it.
"""


def compose_report(
    pack: FactPack,
    kind: ReportKind,
    engine: DecisionEngine,
    audit: Optional[SpecificityAudit] = None,
    previous: Optional[ReportDraft] = None,
) -> ReportDraft:
    """First draft, or a revision if a failed audit + previous draft are supplied."""
    system = _COMPOSE_TEASER if kind == ReportKind.TEASER else _COMPOSE_PRE_ASSESSMENT
    context: dict = {"fact_pack": pack.context(), "report_kind": kind.value}
    if audit and previous:
        context["previous_draft"] = previous.model_dump(mode="json")
        context["failed_audit"] = audit.model_dump(mode="json")
        context["instruction"] = (
            "Revise the previous draft to clear EVERY violation in failed_audit. "
            "Change nothing that was not flagged."
        )
    return engine.deliberate(
        layer="report_compose", system_prompt=system, context=context, schema=ReportDraft
    )


def audit_report(pack: FactPack, draft: ReportDraft, engine: DecisionEngine) -> SpecificityAudit:
    return engine.deliberate(
        layer="report_audit",
        system_prompt=_CRITIC_SYSTEM,
        context={"fact_pack": pack.context(), "draft": draft.model_dump(mode="json")},
        schema=SpecificityAudit,
    )


def edit_report(
    draft: ReportDraft,
    engine: DecisionEngine,
    lint_violations: Optional[list[str]] = None,
) -> ReportDraft:
    context: dict = {"draft": draft.model_dump(mode="json")}
    if lint_violations:
        context["lint_violations"] = lint_violations
        context["instruction"] = "Your previous edit failed lint. Fix exactly these."
    return engine.deliberate(
        layer="report_edit",
        system_prompt=_EDITOR_SYSTEM.format(banned=", ".join(BANNED_PHRASES[:24])),
        context=context,
        schema=ReportDraft,
    )
