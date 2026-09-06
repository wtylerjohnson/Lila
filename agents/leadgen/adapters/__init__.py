"""Adapter doors onto the lead-gen contracts.

The Assess / target_actions draft mapper is ``draft_lead_rows``.
Intake, coverage, and Market Map adapters remain later workstreams.
"""

from .assess import AssessLeadDrafts, draft_lead_rows

__all__ = ["AssessLeadDrafts", "draft_lead_rows"]
