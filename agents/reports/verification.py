"""Per-figure freshness gate and figure-dispute arbitration (2026-07-16).

Freshness is a property of the FIGURE, not the document. A deliverable dated
13 JUL 2026 carrying a figure pulled 02 JUN 2026 was previously invisible as
a risk; this module makes it a gate event. Policy text: the arbitration rule
set lives at docs/VERIFICATION_POLICY.md and this module is its enforcement.

Three checks, all riding the existing lint-battery -> QA-flag -> DRAFT chain
(a violation forces DRAFT; release_state and the .qa.json sidecar machinery
are untouched):

- STALE: a cited figure whose retrieved_at is older than
  FRESHNESS_MAX_AGE_DAYS at render time fails with a re-pull instruction
  naming the adapter and the Command Center step.
- UNKNOWN_FRESHNESS: no recoverable retrieval time fails the same way; a
  guess never substitutes.
- DISPUTED: a figure the extract and verify passes disagree on blocks release
  until resolved. Primary federal records (USAspending, SAM) win over
  secondary sources automatically; two disagreeing primaries or an
  unreachable primary freeze the figure for a human. Every resolution is
  logged append-only; no model resolves a dispute over a figure it authored.

The resolution log follows the contact-graph observations.jsonl pattern:
append-only JSONL, one model row per line, derived state always rebuildable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from agents.reports.facts import (
    UNKNOWN_FRESHNESS,
    Fact,
    FactPack,
    SourceSystem,
)
from agents.reports.lint import LintViolation

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Config constant, never a literal at a call site: the maximum age, in days,
#: of any figure rendered in a client artifact, measured retrieved_at ->
#: render time. Also bounds the allowed divergence between the computed
#: data-current date and the document date.
FRESHNESS_MAX_AGE_DAYS = 14

#: The arbitration policy's primary federal records. Everything else is
#: secondary and can never override a primary record.
PRIMARY_SOURCE_SYSTEMS = frozenset({SourceSystem.USASPENDING, SourceSystem.SAM})

#: Decider identity for the automatic primary-record rule. Never a model.
POLICY_DECIDER = "policy:primary_record"

#: A resolution whose decision is this sentinel keeps the figure blocked.
FROZEN = "frozen"

#: source system -> (adapter to name in the re-pull instruction, Command
#: Center step that re-pulls it). Report builds ride the dashboard/API only,
#: so the instruction names the sanctioned step, never an ad-hoc script.
REPULL_BY_SYSTEM: dict[SourceSystem, tuple[str, str]] = {
    SourceSystem.USASPENDING: ("USAspending adapter (tools/api)", "searches"),
    SourceSystem.SAM: ("SAM.gov adapter (tools/api)", "searches"),
    SourceSystem.APFS: ("forecast adapters (tools/api/forecasts)", "searches"),
    SourceSystem.AGENCY_DOC: ("official-source collectors", "searches"),
    SourceSystem.ANALYST: ("analyst-owned reference; refresh the compiled "
                           "source", "n/a (analyst data)"),
}

_CITE_RE = re.compile(r"\[F(\d+)\]")


def cited_fact_ids(html_text: str) -> set[str]:
    """The rendered-figure set: every [F#] citation in the client artifact."""
    return {f"F{n}" for n in _CITE_RE.findall(html_text or "")}


def _repull_instruction(fact: Fact) -> str:
    adapter, step = REPULL_BY_SYSTEM.get(
        fact.source_system or SourceSystem.ANALYST,
        ("originating adapter", "searches"))
    record = f" (record {fact.source_record_id})" if fact.source_record_id else ""
    return (f"re-pull via Command Center step '{step}' using the {adapter}"
            f"{record} before release")


def _cited_facts(pack: FactPack, cited: Iterable[str]) -> list[Fact]:
    wanted = set(cited)
    return [f for f in pack.facts if f.id in wanted]


def freshness_violations(
    pack: FactPack,
    cited: Iterable[str],
    *,
    now: datetime,
    max_age_days: int = FRESHNESS_MAX_AGE_DAYS,
    resolutions: Optional[list["FigureResolution"]] = None,
) -> list[LintViolation]:
    """Hard-fail checks over every figure the client artifact renders.

    `now` is the render moment (UTC); an explicit as_of replay may pass its
    own. Only cited facts gate: an internal-only fact ages without blocking."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    v: list[LintViolation] = []
    for f in sorted(_cited_facts(pack, cited), key=lambda x: int(x.id[1:])):
        if f.retrieved_at is None or f.freshness == UNKNOWN_FRESHNESS:
            v.append(LintViolation(
                rule="FRESHNESS_UNKNOWN",
                detail=(f"{f.id} renders a figure with no recoverable "
                        f"retrieval time (UNKNOWN_FRESHNESS); "
                        f"{_repull_instruction(f)}"),
                excerpt=f.text[:120]))
        else:
            age_days = (now - f.retrieved_at).total_seconds() / 86400.0
            if age_days > max_age_days:
                v.append(LintViolation(
                    rule="FRESHNESS_STALE",
                    detail=(f"{f.id} renders a figure retrieved "
                            f"{f.retrieved_at.date().isoformat()}, "
                            f"{int(age_days)} days before render time "
                            f"(limit {max_age_days}); "
                            f"{_repull_instruction(f)}"),
                    excerpt=f.text[:120]))
        if f.disputed and not is_resolved(f, resolutions or []):
            v.append(LintViolation(
                rule="FIGURE_DISPUTED",
                detail=(f"{f.id} is disputed between extract and verify "
                        f"passes and carries no resolution; the figure is "
                        f"frozen until resolved per "
                        f"docs/VERIFICATION_POLICY.md"),
                excerpt=f.text[:120]))
    return v


def data_current_date(pack: FactPack, cited: Iterable[str]) -> Optional[date]:
    """min(retrieved_at) across rendered figures; the ONLY sanctioned source
    for a client artifact's 'data current' line. None when nothing is cited
    or any cited figure lacks a retrieval time (no honest minimum exists)."""
    facts = _cited_facts(pack, cited)
    if not facts:
        return None
    times = [f.retrieved_at for f in facts]
    if any(t is None for t in times):
        return None
    return min(times).date()


def data_current_violations(
    pack: FactPack,
    cited: Iterable[str],
    document_date: date,
    *,
    max_age_days: int = FRESHNESS_MAX_AGE_DAYS,
) -> list[LintViolation]:
    """Pre-render check: the computed data-current date may not diverge from
    the document date by more than the freshness threshold."""
    computed = data_current_date(pack, cited)
    if computed is None:
        return []  # the per-figure UNKNOWN checks already hard-fail this build
    divergence = (document_date - computed).days
    if divergence > max_age_days:
        return [LintViolation(
            rule="DATA_CURRENT_DIVERGENT",
            detail=(f"document is dated {document_date.isoformat()} but its "
                    f"oldest rendered figure was retrieved "
                    f"{computed.isoformat()}, {divergence} days earlier "
                    f"(limit {max_age_days}); re-pull the stale figures "
                    f"before render"))]
    return []


# ── figure-dispute resolution log (append-only, observations.jsonl pattern) ──

class FigureResolution(BaseModel):
    """One append-only resolution row: figure, both values, records cited,
    decision, decider, timestamp (the exact field set the policy names)."""

    figure: str = Field(description="stable figure key (system:record id)")
    figure_label: str = Field(description="human description of the figure")
    values: list[dict] = Field(
        description="the competing values, one dict per side (verbatim)")
    records_cited: list[str] = Field(
        description="record ids/URLs consulted for the decision")
    decision: str = Field(
        description=f"the resolved value, or '{FROZEN}' to keep it blocked")
    decider: str = Field(
        description="human id or policy rule; a disputant can never decide")
    decided_at: datetime


def figure_key(fact: Fact) -> str:
    """Stable dispute identity across builds (fact ids are per-build)."""
    system = fact.source_system.value if fact.source_system else "unknown"
    if fact.source_record_id:
        return f"{system}:{fact.source_record_id}"
    digest = hashlib.sha256((fact.source or "").encode("utf-8")).hexdigest()[:12]
    return f"{system}:{digest}"


def resolution_log_path(client_name: str, *, log_dir: Optional[str] = None) -> str:
    slug = client_name.lower().replace(" ", "_")
    base = log_dir or os.path.join(_ROOT, "data", "state", "verification")
    return os.path.join(base, f"{slug}.resolutions.jsonl")


def append_resolution(
    client_name: str,
    resolution: FigureResolution,
    *,
    fact: Optional[Fact] = None,
    log_dir: Optional[str] = None,
) -> str:
    """Append one resolution row. Append-only: rows are never rewritten or
    deleted; a superseding decision is a NEW row (latest row wins at read).

    Enforces the no-self-resolution rule mechanically when the disputed fact
    is supplied: a decider who authored one of the competing values cannot
    decide the dispute."""
    if fact is not None:
        disputants = {cv.observed_by for cv in fact.competing_values}
        if resolution.decider in disputants:
            raise ValueError(
                f"decider '{resolution.decider}' authored a competing value "
                f"for this figure; no model resolves a dispute over a figure "
                f"it authored (docs/VERIFICATION_POLICY.md)")
    if resolution.decided_at.tzinfo is None:
        raise ValueError("decided_at must be timezone-aware UTC")
    path = resolution_log_path(client_name, log_dir=log_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(resolution.model_dump_json() + "\n")
    return path


def load_resolutions(
    client_name: str, *, log_dir: Optional[str] = None,
) -> list[FigureResolution]:
    """Read the append-only log. A malformed line is skipped, not repaired:
    the gate fails closed (missing resolution keeps a dispute blocking)."""
    path = resolution_log_path(client_name, log_dir=log_dir)
    rows: list[FigureResolution] = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(FigureResolution(**json.loads(line)))
            except (ValueError, TypeError):
                continue
    return rows


def is_resolved(fact: Fact, resolutions: list[FigureResolution]) -> bool:
    """Latest logged decision for this figure, if any, and it is not FROZEN."""
    key = figure_key(fact)
    decision = None
    for row in resolutions:
        if row.figure == key:
            decision = row.decision
    return decision is not None and decision != FROZEN


def arbitrate(fact: Fact, *, now: datetime) -> Optional[FigureResolution]:
    """The automatic leg of the arbitration policy.

    Exactly one distinct primary-record value among the competing values ->
    the primary record wins automatically (decided by RULE, not by a model).
    Two disagreeing primaries, or no reachable primary record, -> None: the
    figure stays frozen and a human resolves it."""
    if not fact.disputed or not fact.competing_values:
        return None
    primaries = [cv for cv in fact.competing_values
                 if cv.source_system in PRIMARY_SOURCE_SYSTEMS]
    primary_values = {json.dumps(cv.value, sort_keys=True, default=str)
                      for cv in primaries}
    if len(primary_values) != 1:
        return None
    winner = primaries[0]
    return FigureResolution(
        figure=figure_key(fact),
        figure_label=fact.text[:120],
        values=[cv.model_dump(mode="json") for cv in fact.competing_values],
        records_cited=[str(cv.source_record_id or cv.source_url or "")
                       for cv in fact.competing_values],
        decision=json.dumps(winner.value, default=str),
        decider=POLICY_DECIDER,
        decided_at=now,
    )
