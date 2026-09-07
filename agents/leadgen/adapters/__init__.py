"""Adapter doors onto the lead-gen contracts.

The Assess / target_actions draft mapper is ``draft_lead_rows``.
The Press Lead Gen stub cites intake via ``cite_dossier`` / ``cite_profile``
and does not import ``agents.intake``. Coverage and Market Map adapters
remain later workstreams.
"""

from .assess import AssessLeadDrafts, draft_lead_rows
from .intake import cite_dossier, cite_profile

__all__ = [
    "AssessLeadDrafts",
    "cite_dossier",
    "cite_profile",
    "draft_lead_rows",
]
