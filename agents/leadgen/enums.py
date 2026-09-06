"""Closed enum registry for lead-gen contracts.

These namespaces are new. They do not extend targeting personas, Assess
recommendations, or Market Map slot ids. Targeting motion ``rule_id``
values T1/T2/T3 stay on ``TargetingRuleId``. Lead disposition uses
``LeadTier`` values ``LEAD_T1|LEAD_T2|WATCH|HOLD|REJECT`` so a serialized
lead can never be mistaken for a Band 09 rule.

Lifecycle stages are owned by Assess (``LifecycleStage``) and re-exported
here so lead-gen does not fork them. Seller path literals reuse
``PartnerDirection`` plus a fail-closed ``path_unknown``.
"""

from __future__ import annotations

from enum import Enum

from agents.assess.contracts import LifecycleStage, PartnerDirection


class LeadTier(str, Enum):
    """Lead disposition. Not a targeting rule, persona tier, or Assess gate.

    Persist under the field name ``lead_tier``. Display as "lead T1" /
    "lead T2", never as a bare "T1".
    """

    LEAD_T1 = "LEAD_T1"
    LEAD_T2 = "LEAD_T2"
    WATCH = "WATCH"
    HOLD = "HOLD"
    REJECT = "REJECT"


class TargetingRuleId(str, Enum):
    """Closed copy of Band 09 targeting ``rule_id`` literals.

    The rule owner remains ``agents.golden_press.targeting_rules`` and
    ``data/reference/targeting_personas.json``. This enum exists so a
    ``BuyingMotion`` can cite a targeting rule without using ``LeadTier``.
    Persist under the field name ``targeting_rule_id``.
    """

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class CommercialMotionKind(str, Enum):
    """What buying event is in motion. Includes live bid; not a lead tier."""

    LIVE_BID = "live_bid"
    RENEWAL = "renewal"
    DISPLACEMENT = "displacement"
    ADJACENCY = "adjacency"


class PathwayKind(str, Enum):
    """Public, already-published route a seller can use."""

    SAM_NOTICE = "sam_notice"
    AGENCY_FORECAST = "agency_forecast"
    PUBLISHED_POC = "published_poc"
    VEHICLE_ORDERING = "vehicle_ordering"
    INDUSTRY_DAY = "industry_day"
    OFFICIAL_RULEMAKING = "official_rulemaking"


class SellerPathKind(str, Enum):
    """How this seller can transact. Path is not a named person.

    ``prime_to_sub`` through ``vehicle_access`` match ``PartnerDirection``.
    ``path_unknown`` is fail-closed and cannot carry LEAD_T1/LEAD_T2.
    """

    PRIME_TO_SUB = PartnerDirection.PRIME_TO_SUB.value
    SUB_TO_PRIME = PartnerDirection.SUB_TO_PRIME.value
    JOINT_VENTURE = PartnerDirection.JOINT_VENTURE.value
    CHANNEL_RESELLER = PartnerDirection.CHANNEL_RESELLER.value
    VEHICLE_ACCESS = PartnerDirection.VEHICLE_ACCESS.value
    PATH_UNKNOWN = "path_unknown"


class Contactability(str, Enum):
    """Whether a published route to a buyer or holder exists.

    Enrichment and Apollo are not contactability. A Target-door lock is
    a permission state, not proof that nobody is published.
    """

    PUBLISHED_POC = "published_poc"
    ORGANIZATION_ONLY = "organization_only"
    ENRICHMENT_REQUIRED = "enrichment_required"
    NOT_CONTACTABLE = "not_contactable"


class CommunicationPermission(str, Enum):
    """Whether any outbound communication is allowed.

    Default is ``none``. This flag never sends mail. Auto-outreach is
    out of scope. ``outreach_authorized`` means both existing operator
    doors are open; it is not a send instruction.
    """

    NONE = "none"
    CONFIRM_PUBLISHED = "confirm_published"
    TARGET_DOOR_LOCKED = "target_door_locked"
    OUTREACH_AUTHORIZED = "outreach_authorized"


class LeadReadiness(str, Enum):
    """Operational readiness of a LeadRow. Distinct from ``lead_tier``."""

    ACTIONABLE = "actionable"
    WATCHING = "watching"
    HELD = "held"
    CLOSED = "closed"


class AssessmentSubjectKind(str, Enum):
    """Parent subject grain. These are Assess ledger records, not leads."""

    LIVE_SOLICITATION = "live_solicitation"
    DEVELOPING_THESIS = "developing_thesis"
    PARTNER_LINK = "partner_link"
    MOTION_SUBJECT = "motion_subject"


class NextActionVerb(str, Enum):
    """Closed next-action verbs. No invented-person outreach verbs."""

    REVIEW_REQUIREMENT_SPAN = "review_requirement_span"
    FINISH_ATTACHMENT_INVENTORY = "finish_attachment_inventory"
    CONFIRM_SAM_CENSUS = "confirm_sam_census"
    REFRESH_STALE_COVERAGE = "refresh_stale_coverage"
    CONFIRM_PUBLISHED_POC = "confirm_published_poc"
    WATCH_TRIGGER = "watch_trigger"
    VALIDATE_PARTNER_PATH = "validate_partner_path"
    TARGET_DOOR_STILL_LOCKED = "target_door_still_locked"


LEAD_TIER_LABELS = {
    LeadTier.LEAD_T1: "lead T1",
    LeadTier.LEAD_T2: "lead T2",
    LeadTier.WATCH: "lead WATCH",
    LeadTier.HOLD: "lead HOLD",
    LeadTier.REJECT: "lead REJECT",
}

TARGETING_RULE_LABELS = {
    TargetingRuleId.T1: "targeting rule T1",
    TargetingRuleId.T2: "targeting rule T2",
    TargetingRuleId.T3: "targeting rule T3",
}

TIER_READINESS = {
    LeadTier.LEAD_T1: LeadReadiness.ACTIONABLE,
    LeadTier.LEAD_T2: LeadReadiness.ACTIONABLE,
    LeadTier.WATCH: LeadReadiness.WATCHING,
    LeadTier.HOLD: LeadReadiness.HELD,
    LeadTier.REJECT: LeadReadiness.CLOSED,
}

ACTIONABLE_PATHWAY_KINDS = frozenset({
    PathwayKind.SAM_NOTICE,
    PathwayKind.PUBLISHED_POC,
    PathwayKind.VEHICLE_ORDERING,
})

ENUM_REGISTRY = {
    "stage": LifecycleStage,
    "commercial_motion": CommercialMotionKind,
    "pathway": PathwayKind,
    "seller_path": SellerPathKind,
    "contactability": Contactability,
    "communication_permission": CommunicationPermission,
    "lead_tier": LeadTier,
    "targeting_rule_id": TargetingRuleId,
    "lead_readiness": LeadReadiness,
    "assessment_subject": AssessmentSubjectKind,
    "next_action_verb": NextActionVerb,
}


def enum_registry_values() -> dict[str, tuple[str, ...]]:
    """Stable value lists for tests and JSON Schema export."""

    return {
        name: tuple(member.value for member in enum)
        for name, enum in ENUM_REGISTRY.items()
    }
