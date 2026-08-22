"""Contextual Enrichment Gate (stub) — the heart of Hybrid Fusion.

For every high-probability API match, locate the ORIGINAL source document
(PDF SOW/PWS, RFP, agency page), extract its full text, perform semantic
mapping of agency jargon into the standardized taxonomy, then cross-reference
against the API metadata to raise discrepancies.

Output: a FusedOpportunity. Never returns raw API data as a deliverable.
"""

from __future__ import annotations

from agents.schemas import (
    Discrepancy,
    FusedOpportunity,
    RawOpportunity,
    SourceDocument,
)


def retrieve_source_document(opp: RawOpportunity) -> SourceDocument | None:
    """Use a Browser Tool to locate + download the original source document."""
    raise NotImplementedError("Phase 2 — Contextual Enrichment Gate.")


def semantic_map(full_text: str) -> list[str]:
    """Translate agency-specific jargon into the standardized taxonomy."""
    raise NotImplementedError("Phase 2 — Semantic Mapping.")


def reconcile(raw: RawOpportunity, doc: SourceDocument) -> list[Discrepancy]:
    """Cross-reference API values vs. document full text (amounts, dates, scope)."""
    raise NotImplementedError("Phase 3 — Data Hygiene & Sanity Check.")


def fuse(opp: RawOpportunity) -> FusedOpportunity:
    """Run the full gate: retrieve -> map -> reconcile -> synthesize."""
    raise NotImplementedError("Phase 4 — Data Fusion Output.")
