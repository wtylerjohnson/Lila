"""Canonical data contracts for the LILA.

Every artifact that crosses a stage boundary (ingestion -> assess -> target ->
execute) is one of these Pydantic models. This is what "deterministic output"
means in practice: stages exchange validated objects, not free-form text.

Lineage of an opportunity:
    RawOpportunity        -> from API (Discovery Trigger)
    SourceDocument        -> from Browser Tool (Contextual Enrichment Gate)
    FusedOpportunity      -> API + document, post hygiene check (Data Fusion Output)
    AssessmentResult      -> FusedOpportunity scored vs. CapabilityProfile
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


# --------------------------------------------------------------------------- #
# Client side
# --------------------------------------------------------------------------- #
class CapabilityProfile(BaseModel):
    """The client we are selling for. Drives the Assess engine."""

    client_name: str
    naics_codes: list[str] = Field(default_factory=list)
    psc_codes: list[str] = Field(default_factory=list)
    set_aside_eligibility: list[str] = Field(
        default_factory=list,
        description="e.g. ['8(a)', 'WOSB', 'SDVOSB', 'HUBZone']",
    )
    tech_stack: list[str] = Field(default_factory=list)
    past_performance_keywords: list[str] = Field(default_factory=list)
    target_agencies: list[str] = Field(default_factory=list)
    min_contract_value: Optional[float] = None
    max_contract_value: Optional[float] = None


# --------------------------------------------------------------------------- #
# Step 1 — Intake (form submission)
# --------------------------------------------------------------------------- #
class IntakeSubmission(BaseModel):
    """What the client fills out in the online intake form (intake/form.html).

    Deliberately light on required fields — the profiling layer infers NAICS,
    keywords, and strategy from the free-text + the website scrape.
    """

    client_name: str
    website: Optional[HttpUrl] = None
    contact_email: Optional[str] = None
    primary_services: str = Field(default="", description="free text: what they do")
    differentiators: str = Field(default="", description="free text: why they win")
    past_performance: str = Field(default="", description="free text: notable past contracts")
    certifications: list[str] = Field(
        default_factory=list, description="e.g. ['SDVOSB', '8(a)', 'WOSB', 'ISO 9001']"
    )
    known_naics: list[str] = Field(default_factory=list)
    target_agencies: list[str] = Field(default_factory=list)
    geographic_focus: Optional[str] = None
    uei: Optional[str] = None
    submitted_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Ingestion lineage
# --------------------------------------------------------------------------- #
class OpportunityContact(BaseModel):
    """A point of contact attached to a notice (e.g. SAM.gov pointOfContact)."""

    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_type: Optional[str] = Field(default=None, description="e.g. 'primary' | 'secondary'")


class RawOpportunity(BaseModel):
    """Untouched API record. Never a final deliverable on its own."""

    source: str = Field(description="'sam.gov' | 'usaspending.gov'")
    source_id: str
    title: str
    agency: Optional[str] = None
    naics_code: Optional[str] = None
    psc_code: Optional[str] = None
    set_aside: Optional[str] = None
    posted_date: Optional[date] = None
    response_deadline: Optional[date] = None
    temporal_evidence: dict = Field(default_factory=dict, description="Source-bound exact times; legacy date fields remain display projections")
    estimated_value: Optional[float] = None
    api_url: Optional[HttpUrl] = None
    contacts: list[OpportunityContact] = Field(
        default_factory=list, description="named POCs on the notice (feeds the contact plan)"
    )
    raw_payload: dict = Field(default_factory=dict, description="full API record for traceability")


class SourceDocument(BaseModel):
    """Result of the Contextual Enrichment Gate (Browser Tool retrieval)."""

    document_url: HttpUrl
    document_type: str = Field(description="'SOW' | 'RFP' | 'agency_page' | 'PWS' | ...")
    full_text: str
    retrieved_at: datetime
    semantic_tags: list[str] = Field(
        default_factory=list,
        description="agency jargon mapped to the standardized taxonomy",
    )


class DiscrepancySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"  # halts promotion, forces human review


class Discrepancy(BaseModel):
    """Raised when API metadata disagrees with the source document."""

    field: str
    api_value: Optional[str]
    document_value: Optional[str]
    severity: DiscrepancySeverity
    note: str


class FusedOpportunity(BaseModel):
    """Data Fusion Output: API metadata + document context, hygiene-checked.

    This — not RawOpportunity — is the unit the Assess engine consumes.
    """

    raw: RawOpportunity
    document: Optional[SourceDocument] = None
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    fused_summary: str = Field(description="synthesized from BOTH api + document")
    requires_human_review: bool = False  # True if any BLOCKER discrepancy exists
    provenance: list[str] = Field(
        default_factory=list,
        description="ordered list of source URLs/doc ids that produced this record",
    )


# --------------------------------------------------------------------------- #
# Assess engine output
# --------------------------------------------------------------------------- #
class MatchSignal(BaseModel):
    """One scored dimension of the capability match, with its evidence."""

    dimension: str = Field(description="'naics' | 'set_aside' | 'tech' | 'past_perf' | ...")
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(description="cite the data/source that justifies the score")


class AssessmentResult(BaseModel):
    """A FusedOpportunity scored against a CapabilityProfile."""

    opportunity_id: str
    client_name: str
    match_score: float = Field(ge=0.0, le=1.0, description="weighted aggregate of signals")
    signals: list[MatchSignal]
    why_this_match: str = Field(description="human-readable justification")
    traceability: list[str] = Field(description="source URLs/docs backing this assessment")
    requires_human_review: bool = False
    assessed_at: datetime


class ForecastRecord(BaseModel):
    """One agency procurement-forecast line — STATED INTENT, never a live deal.

    Forecasts are statutorily required projections (P.L. 100-656), revised or
    cancelled routinely. This type exists so the distinction is structural:
    ForecastRecord flows only through the signals layer; nothing downstream
    can confuse it with a RawOpportunity because it never becomes one.
    """

    source: str = Field(description="registered adapter name, e.g. 'dhs_apfs'")
    source_id: str = Field(description="the agency's forecast identifier (e.g. APFS number)")
    agency: str = Field(description="department, e.g. 'DHS'")
    component: Optional[str] = Field(default=None, description="sub-org, e.g. 'CBP', 'CISA'")
    title: str
    description: Optional[str] = None
    naics_code: Optional[str] = None
    naics_label: Optional[str] = None
    estimated_value_range: Optional[str] = Field(
        default=None, description="agency's stated band, e.g. '$5M to $10M'")
    anticipated_solicitation: Optional[str] = Field(
        default=None, description="quarter/FY or date the agency SAYS it will solicit")
    anticipated_solicitation_close: Optional[str] = Field(
        default=None,
        description="closing date the agency forecast states; not a live response deadline",
    )
    anticipated_award: Optional[str] = None
    fiscal_year: Optional[str] = None
    award_type: Optional[str] = Field(default=None, description="contract type/vehicle as stated")
    set_aside: Optional[str] = None
    small_business_program: Optional[str] = None
    small_business_poc: Optional[str] = Field(
        default=None, description="small-business specialist contact if listed")
    url: str = Field(description="link to the agency's forecast record")
    retrieved_at: datetime
    data_as_of: Optional[str] = Field(
        default=None,
        description=(
            "source-published effective or snapshot date, when stated; "
            "never synthesized from retrieval time"
        ),
    )
    forecast_status: Optional[str] = Field(default=None, description="agency workflow state")
    psc: Optional[str] = Field(default=None, description="product/service code if stated")
    incumbent_stated: Optional[str] = Field(
        default=None, description="incumbent contractor IF the agency states one")
    predecessor_contract_id: Optional[str] = Field(
        default=None,
        description="current or predecessor contract identifier IF the agency states one",
    )
    source_fields: dict[str, object] = Field(
        default_factory=dict,
        description="additional agency-published fields preserved without reinterpretation",
    )
    first_seen: Optional[str] = Field(
        default=None, description="store-maintained: date this line first appeared")
    last_seen: Optional[str] = Field(
        default=None, description="store-maintained: date of the latest pull carrying it")
    record_hash: Optional[str] = Field(
        default=None, description="store-maintained content hash for change detection")
