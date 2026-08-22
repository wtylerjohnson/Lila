"""Structured contract for Claude-powered decision layers.

A decision layer takes deterministic, scored data (e.g. AssessmentResult[]) and asks
Claude to contextualize it: think through strategy, surface explicit DECISION POINTS
for a human to rule on, and draft ARTIFACTS ready for review. The output is a
validated DecisionReport — never free-form prose — so it slots into the same
deterministic/HITL/traceable pipeline as every other stage.

These models are used as the structured-output schema for `client.messages.parse`,
so Claude is forced to return exactly this shape.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field


class ArtifactKind(str, Enum):
    PURSUIT_BRIEF = "pursuit_brief"
    CAPABILITY_STATEMENT = "capability_statement"
    EMAIL_HOOK = "email_hook"
    WIN_THEME = "win_theme"
    RISK_MEMO = "risk_memo"


class DecisionOption(BaseModel):
    """One choice the human could make at a decision point."""

    label: str = Field(description="short name, e.g. 'Bid', 'No-bid', 'Teaming'")
    rationale: str = Field(description="why this option is on the table")
    confidence: float = Field(ge=0.0, le=1.0, description="model's confidence this is right")
    is_recommended: bool = False


class DecisionPoint(BaseModel):
    """An explicit fork the system hands to a human to resolve."""

    key: str = Field(description="stable slug, e.g. 'bid_no_bid:OPP-001'")
    question: str
    options: list[DecisionOption]
    recommendation: str = Field(description="the recommended option's label")
    reasoning: str = Field(description="contextual justification, grounded in the evidence")
    evidence: list[str] = Field(
        default_factory=list,
        description="source URLs / document refs that back the reasoning (traceability)",
    )
    requires_human_input: bool = True


class Artifact(BaseModel):
    """A review-ready deliverable Claude drafted for a human to approve/edit."""

    kind: ArtifactKind
    title: str
    body_markdown: str
    opportunity_id: Optional[str] = None
    status: str = Field(default="draft", description="always starts as 'draft' — never auto-sent")
    traceability: list[str] = Field(default_factory=list)


class KeywordCategory(str, Enum):
    CAPABILITY = "capability"
    TECHNOLOGY = "technology"
    NAICS = "naics"
    AGENCY = "agency"
    SET_ASIDE = "set_aside"
    SEARCH_TERM = "search_term"


class Keyword(BaseModel):
    term: str
    category: KeywordCategory
    rationale: str = Field(description="why this keyword, grounded in the scrape/form")
    # Keyword-workshop provenance (2026-07-12, additive/backward-compatible).
    # The intake composer emits none of these; defaults preserve legacy packets.
    origin: str = Field(
        default="system",
        description="'system' (inferred) | 'consultant' (added) | 'edited'")
    edited_from: Optional[str] = Field(
        default=None, description="prior wording when a consultant refined it")
    source: Optional[str] = Field(
        default=None, description="where the term was found (provenance)")
    note: Optional[str] = Field(
        default=None,
        description="consultant rationale, or the reason a term was kept out")


class TermCandidate(BaseModel):
    """A keyword or NAICS the inference CONSIDERED but cut — surfaced to the
    human at the strategy gate as a promotable candidate. The near-miss tray
    exists because the human often knows the lane better than the scrape."""

    value: str
    kind: str = Field(description="'keyword' | 'naics'")
    category: Optional[KeywordCategory] = Field(
        default=None, description="for kind='keyword': the category it would join")
    reason: str = Field(description="one line: why it was close AND why it was cut")
    confidence: float = Field(ge=0.0, le=1.0)


NaicsCode = Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class NaicsEntry(BaseModel):
    """A NAICS code with consultant-grade metadata (NAICS Boundary Workshop,
    2026-07-12, additive/backward-compatible).

    `inferred_naics: list[str]` stays the AUTHORITATIVE search boundary; this is
    the parallel provenance/decision record the workshop reads and writes. The
    new intake generation emits one grounded entry per boundary code. Every
    default still preserves legacy packets, where a boundary code with no entry
    reads as system-inferred and is flagged for explanation in the workshop.
    NAICS remains a coarse boundary filter, never a capability claim."""

    code: NaicsCode = Field(description="the six-digit NAICS code")
    title: str = Field(
        default="", description="official/local label when available")
    role: Literal["core", "boundary"] = Field(
        default="boundary",
        description="'core' (exact/primary) | 'boundary' (adjacent/broad)")
    origin: Literal["system", "consultant", "restored", "edited"] = Field(
        default="system",
        description="'system' | 'consultant' | 'restored' | 'edited'")
    rationale: str = Field(
        default="", description="why this code belongs in the boundary")
    note: str = Field(
        default="", description="operator note, or the reason it was kept out")


class ResearchEntity(BaseModel):
    """A proper name the company research surfaced that entity-award retrieval
    can search verbatim (GOLDEN_BUILD Phase 1, 2026-07-24; additive).

    Three kinds: the client's named product lines, named competitors in the
    client's market, and known reseller/channel partners. These names, exactly
    as federal award descriptions would spell them, drive the USAspending
    entity-award lane. Research output first; when research returns no
    cited findings the OPERATOR may supply rows at the review gate
    (revise(), 2026-08-03) with operator provenance in the rationale."""

    name: str = Field(description="the exact proper name, as award text would spell it")
    kind: Literal["product", "competitor", "reseller"]
    rationale: str = Field(
        default="",
        description="one line: the research evidence that grounds this name")
    source: str = Field(
        default="", description="URL or input that evidenced the name, when known")


class SearchSpec(BaseModel):
    """A concrete query the orchestrator will launch against one source post-approval."""

    source: str = Field(description="'sam.gov' | 'usaspending.gov' | 'web'")
    query_terms: list[str] = Field(default_factory=list)
    naics_codes: list[NaicsCode] = Field(default_factory=list)
    set_asides: list[str] = Field(default_factory=list)
    rationale: str


class IntakeStrategy(BaseModel):
    """Claude's read of the intake + scrape: keywords + pursuit strategy + search plan.

    This is what a human reviews and approves before any search launches.
    """

    client_name: str
    pursuit_strategy: str = Field(description="the opportunity-pursuit strategy narrative")
    keywords: list[Keyword] = Field(default_factory=list)
    inferred_naics: list[NaicsCode] = Field(default_factory=list)
    target_agencies: list[str] = Field(default_factory=list)
    set_aside_angles: list[str] = Field(default_factory=list)
    searches: list[SearchSpec] = Field(
        default_factory=list,
        description="at minimum one each for sam.gov, usaspending.gov, and web",
    )
    near_misses: list[TermCandidate] = Field(
        default_factory=list,
        description="keywords/NAICS considered but cut, 4-10, each with the "
                    "reason it was close and the reason it lost — the human's "
                    "promotable-candidate tray; never padded")
    kept_out: list[Keyword] = Field(
        default_factory=list,
        description="terms the consultant removed from the search but preserved "
                    "for restoration (Keyword Workshop, 2026-07-12); never "
                    "destructively deleted. The intake composer leaves this "
                    "empty; it only fills through operator decisions at the gate.")
    naics_meta: list[NaicsEntry] = Field(
        default_factory=list,
        description="consultant-grade metadata for the boundary codes (NAICS "
                    "Boundary Workshop, 2026-07-12; additive). inferred_naics "
                    "stays authoritative. New intake generation supplies one "
                    "grounded rationale per code; legacy packets may load with "
                    "this empty and remain backward-compatible.")
    research_entities: list[ResearchEntity] = Field(
        default_factory=list,
        description="client product names, competitor names, and reseller/"
                    "channel partners surfaced by live research (GOLDEN_BUILD "
                    "Phase 1, 2026-07-24; additive, legacy packets load "
                    "empty). Feeds the USAspending entity-award retrieval "
                    "lane. Empty means research surfaced none; never padded.")
    kept_out_naics: list[NaicsEntry] = Field(
        default_factory=list,
        description="NAICS codes the consultant moved out of the boundary but "
                    "preserved for restoration (code, rationale, provenance, "
                    "note); never destructively deleted. Empty on legacy "
                    "packets and until an operator decision at the gate.")
    confidence: float = Field(ge=0.0, le=1.0)
    sources_reviewed: list[str] = Field(
        default_factory=list, description="URLs/inputs the strategy was derived from"
    )
    requires_human_review: bool = True
    review_gate: str = Field(
        description="one line: what the human must approve before searches launch"
    )


class WebRelevance(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WebLead(BaseModel):
    """One result from the general web search (Claude web_search) — kept string-only
    so it is structured-output friendly."""

    title: str
    url: str
    summary: str
    signal_type: str = Field(description="e.g. 'RFI', 'forecast', 'industry day', 'incumbent'")
    relevance: WebRelevance


class WebLeadList(BaseModel):
    leads: list[WebLead] = Field(default_factory=list)


class FitVerdict(str, Enum):
    STRONG_FIT = "strong_fit"
    PARTIAL_FIT = "partial_fit"
    WEAK_FIT = "weak_fit"
    NO_FIT = "no_fit"


class MatchedCapability(BaseModel):
    """One client capability tied to specific evidence in the verified opportunity."""

    capability: str = Field(description="the client capability/past-performance area")
    evidence: str = Field(description="the exact requirement/text in the opportunity it maps to")


class FitRationale(BaseModel):
    """LLM-generated 'why this is a fit' — grounded in VERIFIED SAM + USAspending data.

    Every claim must trace to the provided opportunity fields or market evidence; the
    model explains fit, it does not invent requirements.
    """

    opportunity_id: str
    verdict: FitVerdict
    fit_summary: str = Field(description="the narrative: why we think this fits the client")
    matched_capabilities: list[MatchedCapability] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="capability gaps vs. the requirement")
    risks: list[str] = Field(default_factory=list)
    market_evidence_note: str = Field(
        description="how USAspending award history corroborates (incumbents, typical $)"
    )
    recommended_action: str = Field(description="e.g. 'pursue as prime', 'team', 'no-bid'")
    confidence: float = Field(ge=0.0, le=1.0)
    citations: list[str] = Field(
        default_factory=list, description="SAM uiLink + USAspending award URLs backing the call"
    )
    requires_human_review: bool = True


class OrgType(str, Enum):
    AGENCY_OFFICE = "agency_office"
    INCUMBENT = "incumbent"
    PRIME = "prime"
    TEAMING_PARTNER = "teaming_partner"
    OTHER = "other"


class KnownPoc(BaseModel):
    """A POC that came from the source data itself (e.g. SAM pointOfContact) — verified."""

    opportunity_id: str
    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    org: Optional[str] = Field(default=None, description="issuing agency/office")


class ContactSearchSpec(BaseModel):
    """One people-search a CRM/contact finder (Apollo etc.) should run."""

    org_name: str
    org_type: OrgType
    domain: Optional[str] = Field(default=None, description="org web domain if known — improves match rate")
    person_titles: list[str] = Field(description="titles to search, e.g. 'Contracting Officer', 'VP Capture'")
    seniorities: list[str] = Field(default_factory=list, description="e.g. 'director', 'vp', 'c_suite'")
    locations: list[str] = Field(default_factory=list)
    rationale: str = Field(description="why these people matter for this pursuit — cite the opportunity/evidence")
    related_opportunity_ids: list[str] = Field(default_factory=list)


class ContactPlan(BaseModel):
    """Step-3 output: who to talk to, and where a contact finder should look next.

    known_pocs echo ONLY contacts present in the verified source data. searches are
    recommendations for the contact finder (Apollo handoff) — a human reviews the
    plan before any search runs.
    """

    client_name: str
    strategy_note: str = Field(description="the contact/outreach angle in 2-4 sentences")
    known_pocs: list[KnownPoc] = Field(default_factory=list)
    searches: list[ContactSearchSpec] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    review_gate: str = ""
    generated_at: Optional[datetime] = None


class DecisionReport(BaseModel):
    """The full output of one decision layer — the unit that hits a Review Gate."""

    layer: str = Field(description="which decision layer produced this, e.g. 'contextualize'")
    strategic_summary: str = Field(description="the 'think through the strategy' narrative")
    decision_points: list[DecisionPoint] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    citations: list[str] = Field(
        default_factory=list, description="every source the report leaned on"
    )
    requires_human_review: bool = True
    review_gate: str = Field(
        description="one line stating exactly what a human must approve before proceeding"
    )
    generated_at: Optional[datetime] = None


class BuyerPersona(BaseModel):
    """One job title worth pursuing, derived from the Assess-stage evidence."""

    title: str = Field(description="exact searchable job title, e.g. 'Director, Cyber Threat Intelligence'")
    function: str = Field(description="'mission/program' | 'security operations' | 'procurement/contracting' | 'channel/prime'")
    seniority: str = Field(description="Apollo seniority bucket: 'manager' | 'director' | 'vp' | 'c_suite' | 'staff'")
    why: str = Field(description="why this title buys/champions the client's offering — cite assess evidence")
    org_types: list[str] = Field(default_factory=list, description="where this title lives: agency office, incumbent, prime, reseller")


class TitleStrategy(BaseModel):
    """TARGET-stage opener: the best titles to pursue, deliberated from the whole
    Assess picture (offering, strategy, qualified opportunities, market evidence)
    BEFORE any org-by-org search planning."""

    client_name: str
    buying_committee_note: str = Field(description="2-3 sentences: how this purchase actually gets decided")
    personas: list[BuyerPersona] = Field(min_length=4, description="ranked, strongest first")
    citations: list[str] = Field(default_factory=list)
    generated_at: Optional[datetime] = None
