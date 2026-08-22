"""Data hygiene / verification — turns a raw API pull into VERIFIED data.

The system must never present unvetted API output as fact. verify_opportunity runs
deterministic field checks on a RawOpportunity (required fields present, NAICS well-
formed, dates sane and ordered, a public source URL for traceability). Only records
that pass should advance to scoring and the LLM fit-logic.

This is the field-level half of the ingestion protocol's "Data Hygiene & Sanity
Check"; cross-referencing against the source document (browser) is Phase 2.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from agents.schemas import RawOpportunity

_NAICS_RE = re.compile(r"^\d{6}$")


class Severity(str, Enum):
    OK = "ok"
    WARNING = "warning"
    BLOCKER = "blocker"  # fails verification


class FieldCheck(BaseModel):
    field: str
    severity: Severity
    detail: str


class VerificationResult(BaseModel):
    opportunity_id: str
    verified: bool = Field(description="True only if there are no BLOCKER checks")
    checks: list[FieldCheck]
    source_url: Optional[str] = Field(default=None, description="traceability anchor")

    @property
    def issues(self) -> list[str]:
        return [f"{c.field}: {c.detail}" for c in self.checks if c.severity != Severity.OK]


def verify_opportunity(opp: RawOpportunity, today: date | None = None) -> VerificationResult:
    today = today or date.today()
    checks: list[FieldCheck] = []

    def add(field: str, sev: Severity, detail: str) -> None:
        checks.append(FieldCheck(field=field, severity=sev, detail=detail))

    # Identity + traceability are non-negotiable.
    if not opp.source_id:
        add("source_id", Severity.BLOCKER, "missing notice id")
    if not opp.title:
        add("title", Severity.BLOCKER, "missing title")
    if opp.api_url:
        add("api_url", Severity.OK, "source link present")
    else:
        add("api_url", Severity.BLOCKER, "no source URL — record is not traceable")

    # Agency: needed to corroborate against USAspending.
    if not opp.agency:
        add("agency", Severity.WARNING, "no issuing agency — market corroboration limited")

    # NAICS format.
    if opp.naics_code:
        if _NAICS_RE.match(opp.naics_code):
            add("naics_code", Severity.OK, opp.naics_code)
        else:
            add("naics_code", Severity.WARNING, f"malformed NAICS {opp.naics_code!r}")
    else:
        add("naics_code", Severity.WARNING, "no NAICS — capability match weakened")

    # Date sanity.
    if opp.posted_date and opp.posted_date > today:
        add("posted_date", Severity.WARNING, f"posted in the future ({opp.posted_date})")
    if opp.posted_date and opp.response_deadline and opp.response_deadline < opp.posted_date:
        add("response_deadline", Severity.BLOCKER, "deadline precedes posted date")
    if opp.response_deadline and opp.response_deadline < today:
        add("response_deadline", Severity.WARNING, f"already closed ({opp.response_deadline})")

    # Value sanity.
    if opp.estimated_value is not None and opp.estimated_value < 0:
        add("estimated_value", Severity.BLOCKER, "negative value")

    verified = not any(c.severity == Severity.BLOCKER for c in checks)
    return VerificationResult(
        opportunity_id=opp.source_id or "(unknown)",
        verified=verified,
        checks=checks,
        source_url=str(opp.api_url) if opp.api_url else None,
    )
