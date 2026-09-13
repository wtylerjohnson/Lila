"""Lead-gen production contracts: parent assessment and child LeadRow.

Additive sibling of ``agents.assess.contracts``. These models wrap Assess
identity; they do not replace ``AssessRun``, ``LiveSolicitation``, or
any Market Map slot. A parent remains valid with zero children.

LeadRow is the four-factor product:

    BuyingMotion x ActionableExternalPathway x SellerTransactionPath
    x CurrentNextAction

plus readiness / disposition fields. A constructor that receives only a
notice, only a motion, or only a contact fails.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator, model_serializer

from agents.assess.contracts import (
    AssessScope,
    ResearchSubject,
    LifecycleStage,
    LiveClassification,
    LiveRecommendation,
)

from ._base import SCHEMA_VERSION, _FrozenContract, require_aware, require_unique
from .enums import (
    ACTIONABLE_PATHWAY_KINDS,
    TIER_READINESS,
    AssessmentSubjectKind,
    CommunicationPermission,
    Contactability,
    LeadReadiness,
    LeadTier,
    SellerPathKind,
)
from .motion import BuyingMotion
from .next_action import CurrentNextAction
from .pathway import ActionableExternalPathway
from .seller_path import SellerTransactionPath
from .targets import LeadResearch, LeadTarget


class OpportunityAssessment(_FrozenContract):
    """Parent pointer at one Assess subject.

    This is not ``AssessRun`` and not ``AssessmentResult``. It copies the
    run / subject identity a LeadRow must cite. Parents may have an empty
    ``lead_ids`` list so opportunity identification does not depend on
    lead gen.
    """

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    assessment_id: str = Field(min_length=1)
    assess_run_id: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    scope: AssessScope
    as_of: datetime
    subject_kind: AssessmentSubjectKind
    subject_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    solicitation_number: str | None = None
    notice_id: str | None = None
    lifecycle: LifecycleStage | None = None
    live_classification: LiveClassification | None = None
    live_recommendation: LiveRecommendation | None = None
    requirement_span: str | None = None
    dossier_schema_version: str | None = None
    identity_status: str | None = None
    lead_ids: tuple[str, ...] = Field(default_factory=tuple)
    research_subject: ResearchSubject | None = None
    research: LeadResearch | None = None
    targets: tuple[LeadTarget, ...] = ()

    @model_serializer(mode="wrap")
    def legacy_parent_serialization(self, handler):
        value=handler(self)
        if self.research_subject is None and self.research is None and not self.targets:
            for name in ("research_subject", "research", "targets"):
                value.pop(name, None)
        return value

    @model_validator(mode="after")
    def _parent_identity_is_coherent(self) -> OpportunityAssessment:
        if self.subject_kind == AssessmentSubjectKind.RESEARCH_SUBJECT:
            if self.research_subject is None or self.research_subject.subject_id != self.subject_id or self.notice_id or self.lead_ids:
                raise ValueError("research parent requires exact subject and zero live children")
        elif self.research_subject is not None:
            raise ValueError("research subject cannot be relabeled as a legacy parent")
        require_aware(self.as_of, "opportunity assessment as_of")
        require_unique(self.lead_ids, "opportunity assessment lead_ids")
        if (self.subject_kind == AssessmentSubjectKind.LIVE_SOLICITATION
                and not self.notice_id):
            raise ValueError(
                "live_solicitation parent requires notice_id")
        return self


class LeadRow(_FrozenContract):
    """Child lead: four required factors plus readiness and disposition."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    lead_id: str = Field(min_length=1)
    parent_assessment_id: str = Field(min_length=1)
    assess_run_id: str = Field(min_length=1)
    buying_motion: BuyingMotion
    external_pathway: ActionableExternalPathway
    seller_path: SellerTransactionPath
    next_action: CurrentNextAction
    lead_tier: LeadTier
    readiness: LeadReadiness
    contactability: Contactability
    communication_permission: CommunicationPermission
    decision_trace_id: str = Field(min_length=1)
    outcome_id: str | None = None
    targets: tuple[LeadTarget, ...] = Field(default_factory=tuple)
    research: LeadResearch | None = None
    company_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _four_factors_and_disposition_agree(self) -> LeadRow:
        if TIER_READINESS[self.lead_tier] != self.readiness:
            raise ValueError(
                "lead readiness must match lead_tier "
                f"({self.lead_tier.value} -> "
                f"{TIER_READINESS[self.lead_tier].value})")
        if self.contactability != self.external_pathway.contactability:
            raise ValueError(
                "lead contactability must match the pathway contactability")
        if (self.communication_permission
                != self.next_action.communication_permission):
            raise ValueError(
                "lead communication_permission must match the next action")
        if self.lead_tier in (LeadTier.LEAD_T1, LeadTier.LEAD_T2):
            if self.external_pathway.kind not in ACTIONABLE_PATHWAY_KINDS:
                raise ValueError(
                    "LEAD_T1/LEAD_T2 require an actionable external pathway")
            if self.seller_path.kind == SellerPathKind.PATH_UNKNOWN:
                raise ValueError(
                    "LEAD_T1/LEAD_T2 cannot use an unknown seller path")
        return self
