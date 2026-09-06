"""Canonical company dossier for Step 1 mastery.

The dossier is the evidence-backed company understanding. Claim states are
closed literals so a later retrieval or review surface cannot invent
corroboration. Persistence is a sidecar next to the review packet, not a
silent field on IntakeStrategy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from agents.intake.extract import (
    RelatedEntity,
    excerpt_from,
    excerpt_supports_name,
    extract_surface,
    is_discrete_name,
    is_garbage_text,
    is_plausible_naics_code,
    is_product_name,
    naics_search_role,
)
from agents.intake.identity import IdentityResolution


class ClaimState(str, Enum):
    COMPANY_ASSERTED = "company_asserted"
    CORROBORATED = "corroborated"
    INFERRED = "inferred"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


class EvidenceItem(BaseModel):
    evidence_id: str
    url: Optional[str] = None
    source_kind: Literal[
        "form", "website", "web_probe", "capability_ingest",
        "identity", "notice_store", "operator",
    ]
    excerpt: str = ""
    retrieved_at: Optional[str] = None


class Claim(BaseModel):
    text: str
    state: ClaimState = ClaimState.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class CoverageCell(BaseModel):
    topic: str
    status: Literal["covered", "partial", "missing", "not_applicable"]
    note: str = ""


class RetrievalUnit(BaseModel):
    """One offering-level statement for hybrid.frame_lanes / dense retrieval."""

    statement: str
    offering: str = ""
    state: ClaimState = ClaimState.INFERRED
    evidence_ids: list[str] = Field(default_factory=list)


class DossierKeyword(BaseModel):
    term: str
    category: str = "capability"
    rationale: str = ""
    state: ClaimState = ClaimState.INFERRED
    evidence_ids: list[str] = Field(default_factory=list)


class DossierNaics(BaseModel):
    code: str
    title: str = ""
    role: Literal["core", "boundary"] = "boundary"
    rationale: str = ""
    state: ClaimState = ClaimState.INFERRED
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _six_digit(self) -> "DossierNaics":
        if not (self.code.isdigit() and len(self.code) == 6):
            raise ValueError(f"NAICS code must be six digits: {self.code!r}")
        return self


class CompanyDossier(BaseModel):
    schema_version: str = "intake_dossier.v1"
    client_name: str
    generated_at: str = ""
    identity: IdentityResolution
    evidence: list[EvidenceItem] = Field(default_factory=list)
    offerings: list[Claim] = Field(default_factory=list)
    boundaries: list[Claim] = Field(default_factory=list)
    channels: list[Claim] = Field(default_factory=list)
    coverage: list[CoverageCell] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    keywords: list[DossierKeyword] = Field(default_factory=list)
    naics: list[DossierNaics] = Field(default_factory=list)
    capability_statements: list[RetrievalUnit] = Field(default_factory=list)
    kept_out: list[DossierKeyword] = Field(default_factory=list)
    related_entities: list[RelatedEntity] = Field(default_factory=list)
    summary: str = ""

    @model_validator(mode="after")
    def _stamp(self) -> "CompanyDossier":
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
        return self


def _eid(prefix: str, n: int) -> str:
    return f"{prefix}{n:03d}"


def _claim(text: str, state: ClaimState, eids: list[str], rationale: str) -> Claim:
    return Claim(text=text, state=state, evidence_ids=eids, rationale=rationale)


def _discrete_from_findings(findings: str) -> list[str]:
    from agents.intake.extract import candidate_names
    return candidate_names(findings)


def _evidence_for_name(
    name: str,
    probe_blobs: list[tuple[str, str, list[str]]],
    scrape_text: str,
    fallback_url: Optional[str],
    *,
    require_needle: bool = False,
) -> tuple[str, Optional[str]]:
    needles = [name]
    parts = [t for t in name.split() if t.casefold() not in {
        "the", "a", "an", "and", "of", "for"}]
    if parts and parts[-1].casefold() != name.casefold():
        needles.append(parts[-1])
    for needle in needles:
        for _title, findings, citations in probe_blobs:
            if needle.casefold() not in findings.casefold():
                continue
            snippet = excerpt_from(findings, needle=needle)
            if snippet and excerpt_supports_name(name, snippet):
                return snippet, (citations[0] if citations else fallback_url)
        if scrape_text and not is_garbage_text(scrape_text):
            if needle.casefold() not in scrape_text.casefold():
                continue
            snippet = excerpt_from(scrape_text, needle=needle)
            if snippet and excerpt_supports_name(name, snippet):
                return snippet, fallback_url
    if require_needle:
        return "", None
    for _title, findings, citations in probe_blobs:
        snippet = excerpt_from(findings)
        if snippet:
            return snippet, (citations[0] if citations else fallback_url)
    return name, fallback_url


def build_dossier(
    *,
    client_name: str,
    identity: IdentityResolution,
    submission=None,
    research=None,
    product_ingest: Optional[dict] = None,
    probes: Optional[list] = None,
    product_surface=None,
) -> CompanyDossier:
    """Deterministic dossier from identity + form + ingest + probes.

    No model call. LLM structuring, when used, happens in the pipeline and
    is passed in as ``product_surface`` so an offline path still produces a
    valid artifact. Offerings are discrete names; probe essays and scrape
    load-errors are never promoted as products.
    """
    evidence: list[EvidenceItem] = []
    offerings: list[Claim] = []
    boundaries: list[Claim] = []
    channels: list[Claim] = []
    keywords: list[DossierKeyword] = []
    kept_out: list[DossierKeyword] = []
    naics: list[DossierNaics] = []
    statements: list[RetrievalUnit] = []
    unknowns: list[str] = []
    n = 0

    def add_ev(kind: str, excerpt: str, url: Optional[str] = None) -> Optional[str]:
        nonlocal n
        text = (excerpt or "").strip()
        if not text or text.casefold() == "citation":
            return None
        n += 1
        eid = _eid("E", n)
        evidence.append(EvidenceItem(
            evidence_id=eid, url=url, source_kind=kind,  # type: ignore[arg-type]
            excerpt=text[:500],
        ))
        return eid

    ident_ex = identity.rationale or identity.status
    add_ev("identity", ident_ex, identity.website)
    if not identity.is_bound:
        unknowns.append(
            identity.question
            or "official company identity is not bound"
        )

    if submission is not None:
        services = (getattr(submission, "primary_services", None) or "").strip()
        if services and is_discrete_name(services, allow_one_word=False):
            eid = add_ev("form", services)
            offerings.append(_claim(
                services, ClaimState.COMPANY_ASSERTED,
                [eid] if eid else [],
                "stated on the intake form",
            ))
        elif services:
            add_ev("form", services)
        diffs = (getattr(submission, "differentiators", None) or "").strip()
        if diffs and is_discrete_name(diffs, allow_one_word=False):
            eid = add_ev("form", diffs)
            offerings.append(_claim(
                diffs, ClaimState.COMPANY_ASSERTED,
                [eid] if eid else [],
                "form differentiator",
            ))
        elif diffs:
            add_ev("form", diffs)
        past = (getattr(submission, "past_performance", None) or "").strip()
        if past:
            add_ev("form", past)
        for code in getattr(submission, "known_naics", None) or []:
            raw = str(code).strip()
            if raw.isdigit() and len(raw) == 6:
                eid = add_ev("form", f"intake form supplied six-digit NAICS {raw}")
                naics.append(DossierNaics(
                    code=raw, role="core",
                    rationale="six-digit NAICS supplied on the intake form "
                              "as a company-stated search lane",
                    state=ClaimState.COMPANY_ASSERTED,
                    evidence_ids=[eid] if eid else [],
                ))
        for cert in getattr(submission, "certifications", None) or []:
            if str(cert).strip():
                eid = add_ev("form", str(cert))
                channels.append(_claim(
                    f"certification {cert}", ClaimState.COMPANY_ASSERTED,
                    [eid] if eid else [], "form certification",
                ))

    scrape = getattr(research, "scrape", None) if research is not None else None
    scrape_text = ""
    if scrape is not None:
        scrape_text = (
            scrape.combined_text(max_chars=4000)
            if hasattr(scrape, "combined_text") else ""
        )
        root = getattr(scrape, "root_url", None) or identity.website
        pages = getattr(scrape, "pages", None) or []
        if scrape_text.strip() and not is_garbage_text(scrape_text):
            add_ev("website", excerpt_from(scrape_text), root)
        elif scrape_text.strip() and is_garbage_text(scrape_text):
            unknowns.append(
                "website scrape returned a load-error or interstitial page; "
                "that text is not an offering"
            )
        if not pages:
            unknowns.append("website scrape returned no pages")

    ingest = product_ingest or {}
    for row in ingest.get("capabilities") or []:
        name = (row.get("name") if isinstance(row, dict) else str(row) or "").strip()
        found_on = (row.get("found_on") if isinstance(row, dict) else "") or identity.website
        if not name or not is_discrete_name(name) or is_garbage_text(name):
            continue
        eid = add_ev("capability_ingest", excerpt_from(name), found_on)
        offerings.append(_claim(
            name, ClaimState.COMPANY_ASSERTED, [eid] if eid else [],
            "named on the company's own product or solutions page",
        ))

    probe_blobs: list[tuple[str, str, list[str]]] = []
    for probe in probes or []:
        title = str(getattr(probe, "name", None) or (
            probe.get("name") if isinstance(probe, dict) else "probe") or "probe")
        findings = str(getattr(probe, "findings", None) or (
            probe.get("findings") if isinstance(probe, dict) else "") or "")
        citations = list(getattr(probe, "citations", None) or (
            probe.get("citations") if isinstance(probe, dict) else []) or [])
        if not findings.strip():
            continue
        probe_blobs.append((title, findings, citations))
        cite = citations[0] if citations else None
        kind = title.casefold()
        if is_garbage_text(findings) and len(findings) < 80:
            continue
        real = excerpt_from(findings)
        if real:
            add_ev("web_probe", f"{title}: {real}", cite)
        if "channel" in kind or "reseller" in kind:
            for name in _discrete_from_findings(findings):
                eid = add_ev("web_probe", excerpt_from(findings, needle=name), cite)
                channels.append(_claim(
                    name, ClaimState.INFERRED, [eid] if eid else [],
                    "channel or reseller named in a structured web probe",
                ))
        elif "compet" in kind:
            for name in _discrete_from_findings(findings):
                eid = add_ev("web_probe", excerpt_from(findings, needle=name), cite)
                channels.append(_claim(
                    name, ClaimState.INFERRED, [eid] if eid else [],
                    "competitor names from a web probe; treat as inferred",
                ))

    surface = extract_surface(
        probes=probes,
        scrape=scrape,
        product_ingest=ingest,
        structured=product_surface,
        bound_name=identity.bound_name or "",
        official_domain=identity.official_domain,
        client_name=client_name,
    )

    existing_off = {o.text.casefold() for o in offerings}
    for name in surface.offerings:
        if name.casefold() in existing_off or not is_product_name(name):
            continue
        snippet, url = _evidence_for_name(name, probe_blobs, scrape_text, identity.website)
        eid = add_ev("web_probe" if url or snippet else "capability_ingest",
                     snippet or name, url)
        offerings.append(_claim(
            name, ClaimState.INFERRED, [eid] if eid else [],
            "discrete product name extracted from identity-bound research",
        ))
        existing_off.add(name.casefold())

    seen_n = {row.code for row in naics}
    for row in surface.naics:
        code, why = row[0], row[1]
        prior_role = row[2] if len(row) >= 3 else "core"
        if code in seen_n or not is_plausible_naics_code(code):
            continue
        snippet, url = _evidence_for_name(
            code, probe_blobs, scrape_text, identity.website,
            require_needle=True)
        if not snippet or code not in snippet:
            continue
        eid = add_ev("web_probe", snippet, url)
        if not eid:
            continue
        rationale = why if len((why or "").split()) >= 5 else (
            f"company research cited NAICS {code} against the bound firm"
        )
        role = "boundary" if prior_role == "boundary" else naics_search_role(
            code, snippet or rationale)
        naics.append(DossierNaics(
            code=code, role=role, rationale=rationale,
            state=ClaimState.INFERRED,
            evidence_ids=[eid],
        ))
        seen_n.add(code)

    for name in surface.exclusions:
        snippet, url = _evidence_for_name(
            name, probe_blobs, scrape_text, None, require_needle=True)
        if not snippet or not excerpt_supports_name(name, snippet):
            continue
        eid = add_ev("web_probe", snippet, url)
        if not eid:
            continue
        boundaries.append(_claim(
            name, ClaimState.INFERRED, [eid],
            "name-collision or adjacent work the bound company does not sell",
        ))
        kept_out.append(DossierKeyword(
            term=name, category="exclusion",
            rationale="evidenced name collision; keep out of search vocabulary",
            state=ClaimState.INFERRED,
            evidence_ids=[eid],
        ))

    exclude_blob = " ".join(k.term for k in kept_out)
    for entry in naics:
        if entry.state == ClaimState.COMPANY_ASSERTED:
            continue
        if naics_search_role(
                entry.code, entry.rationale, exclude_blob=exclude_blob) == "boundary":
            entry.role = "boundary"

    related_entities = list(surface.related_entities)
    for rel in related_entities:
        snippet, url = _evidence_for_name(
            rel.name, probe_blobs, scrape_text, rel.official_domain)
        add_ev("web_probe", snippet or rel.rationale or rel.name,
               url or rel.official_domain)

    if research is not None:
        cited_urls = set()
        for _title, findings, citations in probe_blobs:
            for url in citations:
                if url in cited_urls:
                    continue
                cited_urls.add(url)
                real = excerpt_from(findings, needle=url) or excerpt_from(findings)
                if real:
                    add_ev("web_probe", real, url)
        for url in getattr(research, "web_citations", None) or []:
            if url in cited_urls:
                continue
            cited_urls.add(url)
            real = ""
            for _title, findings, _cites in probe_blobs:
                real = excerpt_from(findings)
                if real:
                    break
            if not real and scrape_text and not is_garbage_text(scrape_text):
                real = excerpt_from(scrape_text)
            if real:
                add_ev("web_probe", real, url)
        for err in getattr(research, "errors", None) or []:
            unknowns.append(str(err))

    seen_kw = {k.term.casefold() for k in keywords}
    for off in offerings:
        term = " ".join(off.text.split())
        if not is_product_name(term) and not is_discrete_name(term, allow_one_word=False):
            continue
        if not is_product_name(term) and off.state == ClaimState.INFERRED:
            continue
        if term.casefold() not in seen_kw and off.state != ClaimState.UNKNOWN:
            keywords.append(DossierKeyword(
                term=term, category="capability",
                rationale=off.rationale or "offering text",
                state=off.state, evidence_ids=list(off.evidence_ids),
            ))
            seen_kw.add(term.casefold())
        if not any(s.offering.casefold() == term.casefold() for s in statements):
            statements.append(RetrievalUnit(
                statement=f"{client_name} sells {term}.",
                offering=term, state=off.state,
                evidence_ids=list(off.evidence_ids),
            ))

    if identity.is_bound and not offerings:
        unknowns.append("identity is bound but no offerings were evidenced")
    if identity.is_bound and not naics:
        unknowns.append(
            "no six-digit NAICS has been evidenced yet; the code list is "
            "unknown rather than empty-as-zero"
        )

    coverage = [
        CoverageCell(
            topic="identity",
            status="covered" if identity.is_bound else "missing",
            note=identity.rationale,
        ),
        CoverageCell(
            topic="website",
            status="covered" if scrape is not None and getattr(scrape, "pages", None)
            else ("partial" if identity.website else "missing"),
            note=identity.website or "no official site bound",
        ),
        CoverageCell(
            topic="offerings",
            status="covered" if offerings else "missing",
            note=f"{len(offerings)} offering claim(s)",
        ),
        CoverageCell(
            topic="channels",
            status="partial" if channels else "missing",
            note=f"{len(channels)} channel/cert claim(s)",
        ),
        CoverageCell(
            topic="naics",
            status="covered" if naics else "missing",
            note=f"{len(naics)} evidenced code(s)",
        ),
        CoverageCell(
            topic="retrieval_units",
            status="covered" if statements else "missing",
            note=f"{len(statements)} capability statement(s)",
        ),
    ]

    summary_bits = [f"{client_name}."]
    if identity.is_bound:
        summary_bits.append(
            f"Identity bound to {identity.official_domain or identity.website}."
        )
    else:
        summary_bits.append("Identity is not bound.")
    if offerings:
        summary_bits.append(
            f"Offerings evidenced: {', '.join(o.text[:40] for o in offerings[:5])}."
        )
    if unknowns:
        summary_bits.append(f"Open unknowns: {len(unknowns)}.")

    return CompanyDossier(
        client_name=client_name,
        identity=identity,
        evidence=evidence,
        offerings=offerings,
        boundaries=boundaries,
        channels=channels,
        coverage=coverage,
        unknowns=unknowns,
        keywords=keywords,
        naics=naics,
        capability_statements=statements,
        kept_out=kept_out,
        related_entities=related_entities,
        summary=" ".join(summary_bits),
    )
