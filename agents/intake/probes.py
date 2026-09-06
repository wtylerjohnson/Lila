"""Structured web research probes (not one blob).

Each probe answers one question about the already-bound company. Findings
and citations stay per-probe so the adversarial pass can argue against
external evidence rather than a fused essay.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from agents.intake.identity import IdentityResolution


class ResearchProbe(BaseModel):
    name: str
    query: str
    findings: str = ""
    citations: list[str] = Field(default_factory=list)
    error: Optional[str] = None


PROBE_SPECS: tuple[tuple[str, str], ...] = (
    ("offerings",
     "What products, platforms, and services does this company sell? "
     "Name them as the company names them. Cite official pages."),
    ("federal_footprint",
     "What federal or SLED contracting footprint exists: SAM registration, "
     "GSA or other vehicles, awards, set-asides? Cite primary sources."),
    ("channels",
     "Which resellers, distributors, or teaming partners carry this company "
     "into government? Cite partner pages or award text."),
    ("competitors",
     "Which named rival vendors or products do buyers evaluate against this "
     "company? Cite analyst notes, reviews, or market reporting."),
    ("boundaries",
     "What adjacent work does this company NOT do? Note name collisions and "
     "unrelated firms that share the name. Cite sources."),
)


_PROBE_SYSTEM = """\
You are researching ONE already-identified company. The official domain is
binding. Do not switch to a different firm that shares the name. Use web
search. Cite real URLs. If the probe cannot be answered from public sources,
say so and return no invented names.
"""


def run_structured_probes(
    identity: IdentityResolution,
    engine,
    *,
    specs: tuple[tuple[str, str], ...] = PROBE_SPECS,
) -> list[ResearchProbe]:
    """Run named probes against the bound identity. Soft-fail per probe."""
    if engine is None or not identity.is_bound:
        return []
    label = identity.bound_name or identity.query_name
    domain = identity.official_domain or identity.website or ""
    out: list[ResearchProbe] = []
    for name, focus in specs:
        query = (
            f"Company: {label}. Official domain: {domain}. "
            f"Probe: {focus}"
        )
        probe = ResearchProbe(name=name, query=query)
        try:
            findings, citations = engine.web_research(
                system_prompt=_PROBE_SYSTEM, query=query, max_uses=3)
            probe.findings = findings or ""
            probe.citations = list(citations or [])
            if not str(probe.findings).strip() or not probe.citations:
                probe.error = "probe returned no cited findings"
        except Exception as exc:  # noqa: BLE001
            probe.error = str(exc)
        out.append(probe)
    return out
