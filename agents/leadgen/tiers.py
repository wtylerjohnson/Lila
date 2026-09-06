"""Lead-tier and targeting-rule namespaces.

``LeadTier`` is the only lead disposition enum. ``TargetingRuleId`` is a
typed mirror of Band 09 ``rule_id`` values so the two T1 tokens cannot
share a type. See ``docs/CONVENTIONS.md`` (lead-gen contracts).
"""

from .enums import (  # noqa: F401
    LEAD_TIER_LABELS,
    TARGETING_RULE_LABELS,
    LeadTier,
    TargetingRuleId,
)
