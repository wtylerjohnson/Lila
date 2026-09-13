"""Typed contracts for the three Assess intelligence ledgers.

These models are additive and are consumed by the strict ledger adapters,
immutable run persistence, Control Room review API, and Horizon projection.
The established report path remains independently available while these
contracts establish the evidence boundaries its strict sections must honor:

* live solicitations are NOTICE-tier records;
* developing opportunities are explicit, falsifiable theses over evidence;
* partner opportunities are linked to an Assess record and carry specific
  capability or acquisition-access evidence;
* release remains an operator decision after source coverage is visible.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from agents.assess.source_clock import SourceAcquisition, acquired_at, clock_instant


class _FrozenContract(BaseModel):
    """Immutable base for every validated Assess boundary object.

    Tuple annotations below keep nested containers immutable as well. Pydantic
    still accepts ordinary lists at construction and serializes tuples as JSON
    arrays, so adapters do not need special construction or persistence code.
    """

    model_config = ConfigDict(frozen=True)


class ScopeMode(str, Enum):
    ALL = "all"
    AGENCY = "agency"


class ScopeAgency(_FrozenContract):
    name: str = Field(min_length=1)
    abbr: Optional[str] = None


class AssessScope(_FrozenContract):
    """The operator-owned collection boundary for one Assess run."""

    mode: ScopeMode = ScopeMode.ALL
    agencies: tuple[ScopeAgency, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _scope_is_unambiguous(self) -> "AssessScope":
        if self.mode == ScopeMode.ALL and self.agencies:
            raise ValueError("all-federal scope cannot also name agencies")
        if self.mode == ScopeMode.AGENCY and not self.agencies:
            raise ValueError("agency scope requires at least one agency")
        return self


class EvidenceTier(str, Enum):
    NOTICE = "notice"
    PROGRAM = "program"
    MARKET = "market"
    DISCOVERY = "discovery"


class EvidenceKind(str, Enum):
    NOTICE = "notice"
    AGENCY_FORECAST = "agency_forecast"
    AWARD = "award"
    BUDGET = "budget"
    LEGISLATION = "legislation"
    REGULATION = "regulation"
    AGENCY_ANNOUNCEMENT = "agency_announcement"
    WATCHDOG = "watchdog"
    SUBAWARD = "subaward"
    VEHICLE = "vehicle"
    NEWS = "news"
    WEB_LEAD = "web_lead"


class EvidenceUse(str, Enum):
    REQUIREMENT = "requirement"
    TIMING = "timing"
    FUNDING = "funding"
    BUYER = "buyer"
    CAPABILITY = "capability"
    ACCESS = "access"
    INCUMBENT = "incumbent"
    COUNTEREVIDENCE = "counterevidence"


class EvidenceRef(_FrozenContract):
    """One immutable source-backed observation used by an Assess record."""

    evidence_id: str = Field(min_length=1)
    tier: EvidenceTier
    kind: EvidenceKind
    source_name: str = Field(min_length=1)
    source_url: HttpUrl
    retrieved_at: Optional[datetime] = None
    source_acquisition: Optional[SourceAcquisition] = None
    excerpt: str = Field(min_length=1)
    observed_date: Optional[date] = None
    effective_date: Optional[date] = None
    record_hash: Optional[str] = None
    primary_source: bool = False
    supports: tuple[EvidenceUse, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _acquisition_matches_source(self):
        # None is archival schema1, not permission to claim acquisition in v2.
        if self.source_acquisition is not None:
            if self.source_acquisition.binding_sha256 != self.record_hash:
                raise ValueError("source acquisition is not bound to this evidence record")
            expected = clock_instant(self.source_acquisition)
            if expected != self.retrieved_at or (self.retrieved_at is not None and (
                    self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None)):
                raise ValueError("retrieved_at must equal the supported source acquisition or be null")
        return self


class LiveClassification(str, Enum):
    BID_NOW = "bid_now"
    MARKET_RESEARCH = "market_research"
    PRESOLICITATION = "presolicitation"
    SPECIAL_NOTICE = "special_notice"
    AMENDMENT = "amendment"
    AWARDED_OR_CLOSED = "awarded_or_closed"
    EXCLUDED = "excluded"
    UNSCREENED = "unscreened"


class LiveRecommendation(str, Enum):
    PURSUE = "pursue"
    MONITOR = "monitor"
    NO_BID = "no_bid"
    RESEARCH = "research"
    NONE = "none"


class NoticeAttachment(_FrozenContract):
    name: str = Field(min_length=1)
    media_type: Optional[str] = None
    resource_id: Optional[str] = None
    source_url: Optional[HttpUrl] = None


class LiveSolicitation(_FrozenContract):
    """One authoritative notice record in the live-solicitation census."""

    record_id: str = Field(min_length=1)
    notice_id: str = Field(min_length=1)
    solicitation_number: Optional[str] = None
    title: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    component: Optional[str] = None
    office: Optional[str] = None
    classification: LiveClassification
    response_deadline: Optional[date] = None
    authoritative_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    requirement_excerpt: Optional[str] = None
    requirement_review_evidence_id: Optional[str] = None
    requirement_reviewed_by: Optional[str] = None
    requirement_reviewed_at: Optional[datetime] = None
    attachments: tuple[NoticeAttachment, ...] = Field(default_factory=tuple)
    attachment_inventory_count: Optional[int] = Field(default=None, ge=0)
    attachment_inventory_hash: Optional[str] = None
    attachment_inventory_valid: bool = False
    attachments_checked: bool = False
    attachment_reviewed_by: Optional[str] = None
    attachment_reviewed_at: Optional[datetime] = None
    attachment_gap: Optional[str] = None
    fit_trace: tuple[str, ...] = Field(default_factory=tuple)
    recommendation: LiveRecommendation = LiveRecommendation.NONE
    exclusion_reason: Optional[str] = None
    verified_at: datetime

    @model_validator(mode="after")
    def _live_record_is_notice_grounded(self) -> "LiveSolicitation":
        if any(e.tier != EvidenceTier.NOTICE or e.kind != EvidenceKind.NOTICE
               for e in self.authoritative_evidence):
            raise ValueError("live solicitations require NOTICE-tier notice evidence")
        if any(not e.primary_source for e in self.authoritative_evidence):
            raise ValueError("live solicitations require primary notice evidence")
        evidence_urls = [urlparse(str(e.source_url))
                         for e in self.authoritative_evidence]
        evidence_hosts = [
            (parsed.hostname or "").lower()
            for parsed in evidence_urls
        ]
        if any(parsed.scheme != "https" for parsed in evidence_urls) \
                or any(host != "sam.gov" and not host.endswith(".sam.gov")
                       for host in evidence_hosts):
            raise ValueError("live solicitation evidence must resolve to SAM.gov")
        notice_key = self.notice_id.strip().lower()

        def _url_has_notice(parsed) -> bool:
            segments = [unquote(part).strip().lower()
                        for part in parsed.path.split("/") if part.strip()]
            query_ids = [
                value.strip().lower()
                for key, values in parse_qs(parsed.query).items()
                if key.lower() in {"noticeid", "notice_id", "id"}
                for value in values
            ]
            return notice_key in segments or notice_key in query_ids

        if not any(_url_has_notice(parsed) for parsed in evidence_urls):
            raise ValueError("live record notice id must match its SAM evidence URL")
        if self.classification == LiveClassification.EXCLUDED \
                and not self.exclusion_reason:
            raise ValueError("excluded records require an exclusion reason")
        if self.classification == LiveClassification.EXCLUDED \
                and self.recommendation not in (
                    LiveRecommendation.NO_BID, LiveRecommendation.NONE):
            raise ValueError("excluded records cannot recommend pursuit or monitoring")
        if self.classification == LiveClassification.AWARDED_OR_CLOSED \
                and self.recommendation not in (
                    LiveRecommendation.NO_BID, LiveRecommendation.NONE):
            raise ValueError("closed records cannot recommend pursuit or monitoring")
        if self.classification == LiveClassification.UNSCREENED \
                and not self.attachment_gap:
            raise ValueError("unscreened records require a visible gap")
        if self.classification == LiveClassification.UNSCREENED \
                and self.recommendation not in (
                    LiveRecommendation.RESEARCH, LiveRecommendation.NONE):
            raise ValueError("unscreened records cannot recommend pursuit or monitoring")
        review_values = (
            self.requirement_review_evidence_id,
            self.requirement_reviewed_by,
            self.requirement_reviewed_at,
        )
        if any(value is not None for value in review_values):
            if not all(value is not None for value in review_values):
                raise ValueError(
                    "requirement review evidence, reviewer, and timestamp are atomic")
            if not self.requirement_excerpt:
                raise ValueError("requirement reviews require an exact source excerpt")
            reviewed_evidence = next((
                evidence for evidence in self.authoritative_evidence
                if evidence.evidence_id == self.requirement_review_evidence_id
                and EvidenceUse.REQUIREMENT in evidence.supports
            ), None)
            if reviewed_evidence is None:
                raise ValueError(
                    "requirement review must bind requirement-support evidence")
            if self.requirement_excerpt not in reviewed_evidence.excerpt:
                raise ValueError(
                    "reviewed requirement excerpt must be an exact evidence span")
            reviewed_at = self.requirement_reviewed_at
            if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
                raise ValueError("requirement review timestamp must be timezone-aware")
            if reviewed_evidence.retrieved_at is None:
                raise ValueError("requirement review acquisition chronology is unknown")
            if reviewed_at < reviewed_evidence.retrieved_at:
                raise ValueError("requirement review cannot predate its evidence")
            if reviewed_at > self.verified_at:
                raise ValueError("requirement review cannot postdate verification")
        attachment_review_values = (
            self.attachment_reviewed_by, self.attachment_reviewed_at)
        if self.attachments_checked:
            if (not self.attachment_inventory_valid
                    or not self.attachment_inventory_hash
                    or self.attachment_inventory_count != len(self.attachments)):
                raise ValueError(
                    "verified attachment state requires a reconciled inventory")
        if any(value is not None for value in attachment_review_values):
            if not all(value is not None for value in attachment_review_values):
                raise ValueError("attachment reviewer and timestamp are atomic")
            if not self.attachments:
                raise ValueError(
                    "zero-attachment notices cannot carry attachment review metadata")
            attachment_reviewed_at = self.attachment_reviewed_at
            if (attachment_reviewed_at.tzinfo is None
                    or attachment_reviewed_at.utcoffset() is None):
                raise ValueError("attachment review timestamp must be timezone-aware")
            if attachment_reviewed_at > self.verified_at:
                raise ValueError("attachment review cannot postdate verification")
            if any(e.retrieved_at is None for e in self.authoritative_evidence):
                raise ValueError("attachment review acquisition chronology is unknown")
            if attachment_reviewed_at < max(
                    evidence.retrieved_at
                    for evidence in self.authoritative_evidence):
                raise ValueError("attachment review cannot predate its notice evidence")
            if not self.attachments_checked:
                raise ValueError("attachment review must mark the inventory checked")
        if self.classification == LiveClassification.BID_NOW:
            if not self.requirement_excerpt:
                raise ValueError("bid-now records require a requirement excerpt")
            if not self.fit_trace:
                raise ValueError("bid-now records require a capability fit trace")
            if self.recommendation != LiveRecommendation.PURSUE:
                raise ValueError("bid-now records require a pursue recommendation")
            if not self.response_deadline \
                    or self.response_deadline <= self.verified_at.date():
                raise ValueError("bid-now records require a future actionable deadline")
            if not self.attachments_checked or self.attachment_gap:
                raise ValueError("bid-now records require completed attachment review")
            if self.attachments and not all(
                    value is not None for value in attachment_review_values):
                raise ValueError(
                    "bid-now records with attachments require human inventory review")
            if not all(value is not None for value in review_values):
                raise ValueError(
                    "bid-now records require explicit human requirement review")
            requirement_evidence = [
                evidence for evidence in self.authoritative_evidence
                if EvidenceUse.REQUIREMENT in evidence.supports
            ]
            if not requirement_evidence:
                raise ValueError("bid-now records require requirement-support evidence")
            if not any(self.requirement_excerpt in evidence.excerpt
                       for evidence in requirement_evidence):
                raise ValueError(
                    "bid-now requirement excerpt must be an exact evidence span")
            if not any(
                    evidence.evidence_id in trace
                    for evidence in requirement_evidence
                    for trace in self.fit_trace):
                raise ValueError(
                    "bid-now fit trace must cite requirement evidence")
            if any(e.retrieved_at is None for e in requirement_evidence):
                raise ValueError("bid-now requirement acquisition is unknown")
            if any(evidence.retrieved_at > self.verified_at
                   for evidence in requirement_evidence):
                raise ValueError(
                    "bid-now requirement evidence cannot postdate verification")
            if any(evidence.observed_date
                   and evidence.retrieved_at.date() < evidence.observed_date
                   for evidence in requirement_evidence):
                raise ValueError(
                    "bid-now requirement evidence predates its SAM posting")
        return self


class LifecycleStage(str, Enum):
    EARLY_SIGNAL = "early_signal"
    FUNDED_INTENT = "funded_intent"
    ACQUISITION_PLANNING = "acquisition_planning"
    MARKET_RESEARCH = "market_research"
    PRESOLICITATION = "presolicitation"
    LIVE_SOLICITATION = "live_solicitation"
    AWARD = "award"
    RECOMPETE = "recompete"


class EvidenceStrength(str, Enum):
    EARLY = "early"
    MODERATE = "moderate"
    STRONG = "strong"


class IntelligenceStatus(str, Enum):
    PROPOSED = "proposed"
    RESEARCH_NEEDED = "research_needed"
    APPROVED = "approved"
    RETIRED = "retired"
    INVALIDATED = "invalidated"


class ProjectedWindow(_FrozenContract):
    label: str = Field(min_length=1)
    start: Optional[date] = None
    end: Optional[date] = None

    @model_validator(mode="after")
    def _date_order(self) -> "ProjectedWindow":
        if self.start and self.end and self.end < self.start:
            raise ValueError("projected window end precedes start")
        return self


class OpportunityThesis(_FrozenContract):
    """A falsifiable prediction about the next federal procurement event."""

    thesis_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    predicted_event: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    component: Optional[str] = None
    program: Optional[str] = None
    office: Optional[str] = None
    lifecycle_stage: LifecycleStage
    projected_window: ProjectedWindow
    likely_acquisition_path: Optional[str] = None
    incumbent: Optional[str] = None
    estimated_value_range: Optional[str] = None
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    inference_chain: str = Field(min_length=1)
    counterevidence: tuple[EvidenceRef, ...] = Field(default_factory=tuple)
    falsifier: str = Field(min_length=1)
    watch_trigger: str = Field(min_length=1)
    monitoring_cadence: str = Field(min_length=1)
    evidence_strength: EvidenceStrength
    status: IntelligenceStatus = IntelligenceStatus.PROPOSED

    @model_validator(mode="after")
    def _strength_follows_evidence(self) -> "OpportunityThesis":
        if self.evidence_strength in (EvidenceStrength.MODERATE,
                                      EvidenceStrength.STRONG):
            authoritative = [
                e for e in self.evidence
                if e.primary_source and e.kind not in (
                    EvidenceKind.NEWS, EvidenceKind.WEB_LEAD)
            ]
            if not authoritative:
                raise ValueError(
                    "moderate or strong theses require authoritative primary evidence"
                )
        return self


class PartnerDirection(str, Enum):
    PRIME_TO_SUB = "prime_to_sub"
    SUB_TO_PRIME = "sub_to_prime"
    JOINT_VENTURE = "joint_venture"
    CHANNEL_RESELLER = "channel_reseller"
    VEHICLE_ACCESS = "vehicle_access"


class PartnerOpportunity(_FrozenContract):
    """A specific evidence-backed access path linked to Assess intelligence."""

    partner_id: str = Field(min_length=1)
    partner_name: str = Field(min_length=1)
    linked_assess_ids: tuple[str, ...] = Field(min_length=1)
    direction: PartnerDirection
    role_hypothesis: str = Field(min_length=1)
    client_needs_partner: str = Field(min_length=1)
    partner_needs_client: str = Field(min_length=1)
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    vehicle_or_channel: Optional[str] = None
    risks: tuple[str, ...] = Field(default_factory=tuple)
    next_validation_step: str = Field(min_length=1)
    status: IntelligenceStatus = IntelligenceStatus.PROPOSED

    @model_validator(mode="after")
    def _partner_has_specific_basis(self) -> "PartnerOpportunity":
        if not any(EvidenceUse.CAPABILITY in e.supports
                   or EvidenceUse.ACCESS in e.supports
                   for e in self.evidence):
            raise ValueError(
                "partner opportunities require capability-specific or access evidence"
            )
        return self


class SourceLane(str, Enum):
    LIVE = "live"
    HORIZON = "horizon"
    PARTNER = "partner"


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_REGISTERED = "not_registered"


class SourceCoverage(_FrozenContract):
    source: str = Field(min_length=1)
    lane: SourceLane
    status: CoverageStatus
    required: bool = False
    records_screened: int = Field(default=0, ge=0)
    total_available: Optional[int] = Field(default=None, ge=0)
    note: str = ""


class LiveSolicitationLedger(_FrozenContract):
    run_id: str
    client_name: str
    profile_version: str
    scope: AssessScope
    as_of: datetime
    records: tuple[LiveSolicitation, ...] = Field(default_factory=tuple)


class OpportunityThesisLedger(_FrozenContract):
    run_id: str
    client_name: str
    profile_version: str
    scope: AssessScope
    as_of: datetime
    items: tuple[OpportunityThesis, ...] = Field(default_factory=tuple)


class PartnerOpportunityLedger(_FrozenContract):
    run_id: str
    client_name: str
    profile_version: str
    scope: AssessScope
    as_of: datetime
    items: tuple[PartnerOpportunity, ...] = Field(default_factory=tuple)


class GateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AssessRun(_FrozenContract):
    """The complete Assess result. Release always requires a human decision."""

    run_id: str
    client_name: str
    profile_version: str
    scope: AssessScope
    as_of: datetime
    coverage: tuple[SourceCoverage, ...] = Field(default_factory=tuple)
    live: LiveSolicitationLedger
    horizon: OpportunityThesisLedger
    partners: PartnerOpportunityLedger
    approval_status: GateStatus = GateStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    partial_release_approved: bool = False
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def _run_identity_and_gate_are_consistent(self) -> "AssessRun":
        for ledger in (self.live, self.horizon, self.partners):
            if (ledger.run_id, ledger.client_name, ledger.profile_version,
                    ledger.scope, ledger.as_of) != (
                        self.run_id, self.client_name, self.profile_version,
                        self.scope, self.as_of):
                raise ValueError(
                    "Assess ledgers must share run, client, profile, scope, and as-of")
        all_evidence = [e for record in self.live.records for e in record.authoritative_evidence]
        all_evidence += [e for item in self.horizon.items for e in (*item.evidence, *item.counterevidence)]
        all_evidence += [e for item in self.partners.items for e in item.evidence]
        if self.run_id.startswith("assess:v1:") and any(e.source_acquisition is not None for e in all_evidence):
            raise ValueError("legacy v1 Assess run cannot contain v2 source acquisition")
        if self.run_id.startswith("assess:v2:") and any(e.source_acquisition is None for e in all_evidence):
            raise ValueError("v2 Assess evidence requires explicit source acquisition provenance")
        if self.run_id.startswith("assess:v2:"):
            for item in (*self.horizon.items, *self.partners.items):
                if item.status == IntelligenceStatus.APPROVED and any(acquired_at(e) is None for e in item.evidence):
                    raise ValueError("approved intelligence requires known source acquisition")
        if any(record.verified_at > self.as_of
               or any(evidence.retrieved_at is not None and evidence.retrieved_at > self.as_of
                      for evidence in record.authoritative_evidence)
               for record in self.live.records):
            raise ValueError("live evidence or verification postdates run as-of")
        if any(evidence.retrieved_at is not None and evidence.retrieved_at > self.as_of
               for thesis in self.horizon.items
               for evidence in (*thesis.evidence, *thesis.counterevidence)):
            raise ValueError("Horizon evidence postdates run as-of")
        if any(evidence.retrieved_at is not None and evidence.retrieved_at > self.as_of
               for partner in self.partners.items
               for evidence in partner.evidence):
            raise ValueError("partner evidence postdates run as-of")
        if self.approved_at and self.approved_at > self.as_of:
            raise ValueError("Assess approval postdates run as-of")
        live_ids = [record.record_id for record in self.live.records]
        thesis_ids = [item.thesis_id for item in self.horizon.items]
        partner_ids = [item.partner_id for item in self.partners.items]
        for label, values in (("live", live_ids), ("thesis", thesis_ids),
                              ("partner", partner_ids)):
            if len(values) != len(set(values)):
                raise ValueError(f"Assess {label} ids must be unique")
        valid_links = set(live_ids) | set(thesis_ids)
        if any(link not in valid_links for item in self.partners.items
               for link in item.linked_assess_ids):
            raise ValueError("partner opportunities must link to this Assess run")
        coverage_keys = [(row.lane, row.source) for row in self.coverage]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise ValueError("Assess source coverage rows must be unique by lane and source")
        sam_rows = [row for row in self.coverage
                    if row.lane == SourceLane.LIVE and row.source == "sam.gov"]
        if len(sam_rows) != 1 or sam_rows[0].required is not True:
            raise ValueError(
                "Assess runs require one mandatory SAM.gov live coverage row")
        if self.approval_status == GateStatus.APPROVED:
            if not self.approved_by or not self.approved_at:
                raise ValueError("approved Assess runs require approver and timestamp")
        elif self.approved_by or self.approved_at:
            raise ValueError("pending or rejected Assess runs cannot carry approval metadata")
        return self

    def blocking_sources(self) -> list[SourceCoverage]:
        return [c for c in self.coverage
                if c.required and c.status != CoverageStatus.COMPLETE]

    def can_release(self) -> bool:
        # model_copy(update=...) intentionally skips Pydantic validation. Treat
        # any such inconsistent copy as non-releasable until it is round-trip
        # validated, even though normal instances are frozen after validation.
        try:
            checked = type(self).model_validate(self.model_dump(mode="python"))
        except Exception:  # noqa: BLE001 - a malformed gate must fail closed
            return False
        if (checked.approval_status != GateStatus.APPROVED
                or not checked.approved_by or not checked.approved_at
                or checked.requires_human_review is not True):
            return False
        # Old snapshots remain readable; their timestamp alone cannot grant a
        # new freshness-sensitive release or approval.
        required = [e for r in checked.live.records
                    if r.requirement_reviewed_at or r.attachment_reviewed_at
                    or r.classification == LiveClassification.BID_NOW
                    for e in r.authoritative_evidence]
        required += [e for t in checked.horizon.items if t.status == IntelligenceStatus.APPROVED
                     for e in (*t.evidence, *t.counterevidence)]
        required += [e for p in checked.partners.items if p.status == IntelligenceStatus.APPROVED
                     for e in p.evidence]
        if any(acquired_at(e) is None for e in required):
            return False
        return (not checked.blocking_sources()
                or checked.partial_release_approved)
