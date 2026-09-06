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
     "Start from this company's official-domain product, solutions, and "
     "platform pages. Name shipped product families as printed there "
     "(operating system, controllers, visibility fabrics such as DANZ "
     "Monitoring Fabric, identity products such as CloudVision AGNI / "
     "Guardian for Network Identity, numbered switch families such as "
     "7050X). Do not write a generic industry essay. Do not treat GSA "
     "schedules, SEWP vehicles, or award IDs as products. "
     "Cite official product-page URLs."),
    ("customers",
     "Start from the official-domain customers, case-studies, and "
     "industries pages. Name customers, sectors served, and case-study "
     "proof points. Quote the sentence that names each customer. "
     "Do not invent logos. If the site names none, say none found."),
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
     "Start from official-domain compare, why-us, vs, alternatives, or "
     "competitive pages. Quote each named rival vendor or product with "
     "the source sentence. External analyst notes are secondary and must "
     "not replace site-stated rivals."),
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
binding. After bind, the company's own website is the primary source for
products, competitors, customers, and proof points. External web search is
secondary: use it for NAICS filings, namesake collisions, and federal
footprint, not to invent what the company sells.

Actively seek on the official site: (1) named product families including
identity products such as CloudVision AGNI / Guardian for Network Identity,
DANZ Monitoring Fabric, and numbered switch families such as the 7050X
series when the vendor publishes them; (2) named rivals on compare / vs /
alternatives pages; (3) named customers and case studies. Quote source
sentences. Do not treat GSA, SEWP, or award IDs as products. If a probe
cannot be answered from public sources, say so and return no invented names.
"""

_SITE_THIN_NOTE = (
    "Official-site scrape or headless render returned no usable HTML "
    "(empty body, WAF challenge, or interstitial). For products, "
    "customers, and competitors you MUST open official-domain URLs and "
    "cite those URLs. Do not use Wikipedia, Crunchbase, or generic "
    "company blurbs as the primary picture of what they sell."
)

_SITE_ANCHORED = frozenset({"offerings", "customers", "competitors"})


def run_structured_probes(
    identity: IdentityResolution,
    engine,
    *,
    specs: tuple[tuple[str, str], ...] = PROBE_SPECS,
    official_urls: Optional[list[str]] = None,
    site_thin: bool = False,
) -> list[ResearchProbe]:
    """Run named probes against the bound identity. Soft-fail per probe."""
    if engine is None or not identity.is_bound:
        return []
    label = identity.bound_name or identity.query_name
    domain = identity.official_domain or identity.website or ""
    hubs = [u for u in (official_urls or []) if u][:16]
    hub_line = ""
    if hubs:
        hub_line = " Official URLs to open: " + "; ".join(hubs) + "."
    out: list[ResearchProbe] = []
    for name, focus in specs:
        query = (
            f"Company: {label}. Official domain: {domain}. "
            f"Start from official-site sections relevant to this probe "
            f"(products, customers, compare, about, partners)."
            f"{hub_line} "
            f"Probe: {focus}"
        )
        if site_thin and name in _SITE_ANCHORED:
            query = f"{_SITE_THIN_NOTE} {query}"
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
