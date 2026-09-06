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
     "Name this company's shipped product families from official product pages. "
     "Include the network operating system, controllers or cloud-management "
     "platforms, visibility or telemetry fabrics, identity or zero-trust "
     "network products (for example CloudVision AGNI / Guardian for Network "
     "Identity when this vendor publishes them), and numbered switch or "
     "router families (for example the 7050X series and sibling X/R families "
     "when this vendor publishes them). Quote each name as printed. "
     "Do not treat GSA schedules, SEWP vehicles, or award IDs as products. "
     "Cite official product-page URLs."),
    ("federal_footprint",
     "Does this company sell to the US federal government? "
     "Quote any six-digit NAICS or industry classification the company or "
     "its SAM / GSA listing states, including the sentence that contains "
     "the code. Also note SAM registration, GSA / MAS / SEWP vehicles, and "
     "recent awards, but do not list those vehicle or award IDs as products. "
     "If no NAICS appears in sources, say none found. Cite the source "
     "sentence for every code."),
    ("channels",
     "Which resellers, distributors, or teaming partners carry this company "
     "into government? Cite partner pages or award text."),
    ("competitors",
     "Which named rival vendors or products do buyers evaluate against this "
     "company? Cite analyst notes, reviews, or market reporting."),
    ("boundaries",
     "Name other companies or brands that share this name or a close spelling. "
     "Search specifically for a music label or records company, an aviation "
     "or aircraft-support firm, a project-management or PPM vendor, and any "
     "lookalike spelling. Quote each namesake's full name "
     "(for example a Records label, an Aviation firm, a lookalike "
     "spelling, or an aircraft-support firm when those namesakes exist). "
     "These are collisions, not this company's offerings. Cite sources."),
    ("classifications",
     "Quote any NAICS, PSC, or industry classification THIS bound company "
     "states for itself, with the six-digit code and the sentence that "
     "contains it. Do not copy industry codes that belong to a namesake "
     "or collision firm (aviation, music/records, aircraft support). "
     "Do not invent codes or lift forecast/solicitation IDs. "
     "If none appear in sources, say none found."),
)


_PROBE_SYSTEM = """\
You are researching ONE already-identified company. The official domain is
binding. Do not switch to a different firm that shares the name. Use web
search and the official product pages. Cite real URLs.

Actively seek: (1) named product families including identity products such
as CloudVision AGNI / Guardian for Network Identity and numbered switch
families such as the 7050X series when the vendor publishes them; (2) any
stated six-digit NAICS or industry classification, quoted with the source
sentence; (3) namesake collisions (music/records, aviation, lookalike
spellings, aircraft support). Do not treat GSA, SEWP, or award IDs as
products. If a probe cannot be answered from public sources, say so and
return no invented names.
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
            f"Search official product pages and SAM/GSA listings when relevant. "
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
