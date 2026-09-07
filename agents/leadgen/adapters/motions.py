"""target_actions / motion rows -> BuyingMotion factors.

Target-action rows are person-grain and are not LeadRows. This module
does not import Apollo or ``targeting_rules``. Motion construction is
owned by ``agents.leadgen.from_assess``.
"""

from agents.leadgen.from_assess import coerce_target_actions, draft_lead_rows

__all__ = [
    "coerce_target_actions",
    "draft_lead_rows",
]
