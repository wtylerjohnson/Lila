"""Shared test fixtures: a known client profile + hand-built opportunities."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.schemas import (
    CapabilityProfile,
    FusedOpportunity,
    RawOpportunity,
    SourceDocument,
)

PROFILE = CapabilityProfile(
    client_name="Acme Federal Solutions LLC",
    naics_codes=["541512", "541519"],
    psc_codes=["D307"],
    set_aside_eligibility=["SDVOSB", "8(a)"],
    tech_stack=["AWS GovCloud", "Kubernetes", "Zero Trust", "Python"],
    past_performance_keywords=["cloud migration", "FedRAMP", "DevSecOps"],
    target_agencies=["Department of Veterans Affairs", "GSA"],
    min_contract_value=250_000,
    max_contract_value=25_000_000,
)


def raw(**over) -> RawOpportunity:
    base = dict(
        source="sam.gov",
        source_id="OPP-001",
        title="Cloud Migration Support Services",
        agency="Department of Veterans Affairs",
        naics_code="541512",
        psc_code="D307",
        set_aside="SDVOSB",
        estimated_value=4_000_000.0,
        api_url="https://sam.gov/opp/OPP-001",
    )
    base.update(over)
    return RawOpportunity(**base)


def fused(
    raw_opp: RawOpportunity | None = None,
    doc_text: str = (
        "Statement of Work: the Department requires AWS GovCloud cloud migration "
        "with Kubernetes orchestration, a Zero Trust architecture, FedRAMP "
        "authorization, and DevSecOps pipelines built in Python."
    ),
    requires_review: bool = False,
    semantic_tags: list[str] | None = None,
) -> FusedOpportunity:
    r = raw_opp or raw()
    doc = SourceDocument(
        document_url="https://sam.gov/opp/OPP-001/sow.pdf",
        document_type="SOW",
        full_text=doc_text,
        retrieved_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
        # Default to no tags so doc_text alone drives tech/past-perf coverage.
        # Pass tags explicitly to test semantic-mapping contributions.
        semantic_tags=semantic_tags or [],
    )
    return FusedOpportunity(
        raw=r,
        document=doc,
        discrepancies=[],
        fused_summary=f"{r.title} for {r.agency}. {doc_text[:80]}",
        requires_human_review=requires_review,
        provenance=[str(r.api_url), str(doc.document_url)],
    )


FIXED_NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
