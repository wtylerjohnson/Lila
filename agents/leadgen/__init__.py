"""Lead-gen contracts. Additive sibling of ``agents.assess``.

Opportunity assessment is the parent. LeadRow is the child. This package
does not replace ``AssessRun``, change Market Map release, or launch
outreach.
"""

from ._base import SCHEMA_VERSION
from .contracts import LeadRow, OpportunityAssessment
from .enums import (
    ENUM_REGISTRY,
    LEAD_TIER_LABELS,
    TARGETING_RULE_LABELS,
    AssessmentSubjectKind,
    CommercialMotionKind,
    CommunicationPermission,
    Contactability,
    LeadReadiness,
    LeadTier,
    NextActionVerb,
    PathwayKind,
    SellerPathKind,
    TargetingRuleId,
    enum_registry_values,
)
from .from_assess import AssessLeadDrafts, draft_lead_rows
from .ids import compose_assessment_id, compose_lead_id, compose_trace_id
from .motion import BuyingMotion
from .next_action import CurrentNextAction
from .pathway import ActionableExternalPathway, PublishedContact
from .press import (
    BUILD_PLAN_STEPS,
    PressLeadGenError,
    PressLeadGenReceipt,
    leadgen_receipt_path,
    run_press,
)
from .seller_path import SellerTransactionPath
from .traces import DecisionTrace

__all__ = [
    "BUILD_PLAN_STEPS",
    "ENUM_REGISTRY",
    "LEAD_TIER_LABELS",
    "SCHEMA_VERSION",
    "TARGETING_RULE_LABELS",
    "ActionableExternalPathway",
    "AssessLeadDrafts",
    "AssessmentSubjectKind",
    "BuyingMotion",
    "CommercialMotionKind",
    "CommunicationPermission",
    "Contactability",
    "CurrentNextAction",
    "DecisionTrace",
    "LeadReadiness",
    "LeadRow",
    "LeadTier",
    "NextActionVerb",
    "OpportunityAssessment",
    "PathwayKind",
    "PressLeadGenError",
    "PressLeadGenReceipt",
    "PublishedContact",
    "SellerPathKind",
    "SellerTransactionPath",
    "TargetingRuleId",
    "compose_assessment_id",
    "compose_lead_id",
    "compose_trace_id",
    "draft_lead_rows",
    "enum_registry_values",
    "leadgen_receipt_path",
    "run_press",
]
