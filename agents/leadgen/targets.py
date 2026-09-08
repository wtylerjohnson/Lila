"""Evidence-bound seller actions. Contact data does not confer authority."""
from datetime import datetime
from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from agents.assess.contracts import EvidenceRef

from ._base import _FrozenContract, require_aware


class LeadTarget(_FrozenContract):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    source_kind: Literal["government_published", "company_published", "apollo"]
    source_url: HttpUrl
    email: str | None = None
    phone: str | None = None
    contact_status: str = Field(min_length=1)
    route: str = Field(min_length=1)
    reason_to_contact: str = Field(min_length=1)
    next_ask: str = Field(min_length=1)
    authority_boundary: str = Field(min_length=1)
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)


class LeadResearch(_FrozenContract):
    """Research priority is independent of qualification and outreach permission."""
    status: Literal["technical_qualification", "known_opportunity", "investigation", "deprioritized", "rejected"]
    priority: bool = False
    rationale: str = Field(min_length=1)
    buyer_requirement: str = Field(min_length=1)
    fit_hypothesis: str = Field(min_length=1)
    route: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    next_ask: str = Field(min_length=1)
    open_questions: tuple[str, ...] = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def honest_disposition(self):
        require_aware(self.reviewed_at, "research reviewed_at")
        if self.priority and self.status in {"deprioritized", "rejected"}:
            raise ValueError("removed candidates cannot remain in the priority view")
        return self
