"""Pursuit dossiers; deadline-aware DEPTH on pursue-grade notices only.

Breadth found it; triage ruled it worth a human's time; this layer reads the
actual solicitation text and produces what a capture strategist writes before
committing bid hours: scope, evaluation criteria, submission mechanics,
incumbent signals, win themes, red flags. Depth is rationed by the deadline
clock and a hard per-run budget; the system never spends a deep fetch on a
discard.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from agents.decisions.engine import DecisionEngine, research_engine

LAYER = "pursuit-dossier"

SYSTEM_PROMPT = """\
You are the depth layer of a LILA. You receive ONE
pursue-grade federal notice with its full description text and attachment
list, plus the client's pursuit strategy. Write the dossier a senior capture
strategist needs before committing bid hours.

Ground everything in the provided text; quote or closely paraphrase the
solicitation's own language for criteria and requirements; never invent
requirements that are not present. If the description is thin, say so in
red_flags rather than padding. The fit_verdict must be earned by the evidence,
not optimism."""


class PursuitDossier(BaseModel):
    id: str = Field(description="the notice's source_id, echoed exactly")
    title: str
    scope_summary: str = Field(description="2-4 sentences: what the buyer actually wants")
    evaluation_criteria: list[str] = Field(
        description="how offers are judged, from the text; empty if not stated")
    submission_requirements: list[str] = Field(
        description="mechanics: format, page limits, portals, due dates, POCs")
    incumbent_signals: list[str] = Field(
        description="named vendors, brand-name refs, or 'appears new' evidence")
    vehicle: Optional[str] = Field(default=None,
                                   description="contract vehicle if stated (GSA MAS, SEWP, IDIQ...)")
    fit_verdict: str = Field(description="one of: strong_fit / conditional_fit / stretch")
    win_themes: list[str] = Field(description="2-4 angles the client should lead with")
    red_flags: list[str] = Field(default_factory=list,
                                 description="wired-for-incumbent signs, impossible timelines, thin data")
    next_action: str = Field(description="the single next move, one sentence")


def select_targets(notices: list[dict], verdicts: dict, budget: int) -> list[dict]:
    """Pursue-grade only, most urgent deadline first, hard budget cap.

    Notices with no deadline (RFIs/sources-sought) rank after dated ones ;
    a ticking clock outranks an open-ended ask.
    """
    pursue = []
    for n in notices:
        nid = n.get("source_id") or n.get("id")
        v = (verdicts.get(nid) or {}).get("verdict") if isinstance(verdicts, dict) else None
        if v == "pursue":
            pursue.append(n)
    pursue.sort(key=lambda n: (n.get("response_deadline") or n.get("deadline") or "9999-99"))
    return pursue[:max(0, budget)]


def compose_dossier(
    client_name: str,
    strategy_summary: str,
    notice: dict,
    depth: dict,
    engine: Optional[DecisionEngine] = None,
) -> PursuitDossier:
    engine = engine or research_engine()
    return engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context={
            "client_name": client_name,
            "client_pursuit_strategy": strategy_summary,
            "as_of": date.today().isoformat(),
            "notice": {
                "id": notice.get("source_id") or notice.get("id"),
                "title": notice.get("title"),
                "agency": notice.get("agency"),
                "type": notice.get("notice_type") or notice.get("type"),
                "naics": notice.get("naics_code"),
                "set_aside": notice.get("set_aside"),
                "deadline": notice.get("response_deadline") or notice.get("deadline"),
                "url": notice.get("api_url") or notice.get("url"),
            },
            "full_description": (depth.get("description") or "")[:14000],
            "attachments": depth.get("attachments"),
            "depth_fetch_errors": depth.get("errors") or [],
        },
        schema=PursuitDossier,
    )


_FIT_LABEL = {"strong_fit": "STRONG FIT", "conditional_fit": "CONDITIONAL FIT",
              "stretch": "STRETCH"}


def render_dossier_markdown(d: PursuitDossier, notice: dict) -> str:
    url = notice.get("api_url") or notice.get("url") or ""
    deadline = notice.get("response_deadline") or notice.get("deadline") or "open / not stated"
    lines = [
        f"## {d.title}",
        f"`{d.id}` · {notice.get('agency') or ';'} · **due {deadline}** · "
        f"**{_FIT_LABEL.get(d.fit_verdict, d.fit_verdict.upper())}**"
        + (f" · [notice]({url})" if url else ""),
        "",
        d.scope_summary,
        "",
        "**Evaluation criteria**" if d.evaluation_criteria else "**Evaluation criteria**; not stated in the notice",
    ]
    lines += [f"- {c}" for c in d.evaluation_criteria]
    if d.submission_requirements:
        lines += ["", "**Submission requirements**"]
        lines += [f"- {r}" for r in d.submission_requirements]
    if d.incumbent_signals:
        lines += ["", "**Incumbent signals**"]
        lines += [f"- {s}" for s in d.incumbent_signals]
    if d.vehicle:
        lines += ["", f"**Vehicle:** {d.vehicle}"]
    lines += ["", "**Win themes**"]
    lines += [f"- {w}" for w in d.win_themes]
    if d.red_flags:
        lines += ["", "**Red flags**"]
        lines += [f"- {r}" for r in d.red_flags]
    lines += ["", f"**Next action:** {d.next_action}", ""]
    return "\n".join(lines)
