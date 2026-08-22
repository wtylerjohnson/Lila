"""Report contracts: the draft object that moves through compose -> audit -> edit."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ReportKind(str, Enum):
    TEASER = "teaser"
    PRE_ASSESSMENT = "pre_assessment"
    ASSESSMENT = "assessment"  # composed capture-brief content, adapted for
    #                            the arbiter panel (client-flow F)


class ReportSection(BaseModel):
    heading: str
    body: str = Field(description="Markdown prose. Every claim cites a fact id like [F3].")


class ReportDraft(BaseModel):
    client_name: str
    kind: ReportKind
    title: str
    sections: list[ReportSection] = Field(default_factory=list)
    fact_ids_used: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    generated_at: Optional[datetime] = None

    def full_text(self) -> str:
        return "\n\n".join(f"{s.heading}\n{s.body}" for s in self.sections)


class SpecIssue(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"      # not backed by any cited fact
    VAGUE = "vague"                              # 'several agencies', 'significant spend'
    NUMBER_MISMATCH = "number_mismatch"          # figure differs from the cited fact
    MISSING_CITATION = "missing_citation"
    DRIFT = "drift"                              # content beyond the FactPack's scope


class SpecViolation(BaseModel):
    section: str
    quote: str = Field(description="the exact offending text")
    issue: SpecIssue
    fix: str = Field(description="what specifically to change, with the right fact id")


class SpecificityAudit(BaseModel):
    passed: bool
    violations: list[SpecViolation] = Field(default_factory=list)
    note: str = ""


class ReportBundle(BaseModel):
    """What the runner persists: the draft plus its QA trail."""

    draft: ReportDraft
    audit: Optional[SpecificityAudit] = None
    arbiter_audits: dict[str, SpecificityAudit] = Field(
        default_factory=dict, description="per-judge results from the final round (who flagged what)"
    )
    audit_rounds: int = 0
    lint_ok: bool = False
    lint_violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
