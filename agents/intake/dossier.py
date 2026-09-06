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


def build_dossier(
    *,
    client_name: str,
    identity: IdentityResolution,
    submission=None,
    research=None,
    product_ingest: Optional[dict] = None,
    probes: Optional[list] = None,
) -> CompanyDossier:
    """Deterministic dossier from identity + form + ingest + probes.

    No model call. LLM structuring, when used, happens in the pipeline after
    this baseline exists so an offline path still produces a valid artifact.
    """
    evidence: list[EvidenceItem] = []
    offerings: list[Claim] = []
    boundaries: list[Claim] = []
    channels: list[Claim] = []
    keywords: list[DossierKeyword] = []
    naics: list[DossierNaics] = []
    statements: list[RetrievalUnit] = []
    unknowns: list[str] = []
    n = 0

    def add_ev(kind: str, excerpt: str, url: Optional[str] = None) -> str:
        nonlocal n
        n += 1
        eid = _eid("E", n)
        evidence.append(EvidenceItem(
            evidence_id=eid, url=url, source_kind=kind,  # type: ignore[arg-type]
            excerpt=(excerpt or "")[:500],
        ))
        return eid

    add_ev("identity", identity.rationale or identity.status,
           identity.website)
    if not identity.is_bound:
        unknowns.append(
            identity.question
            or "official company identity is not bound"
        )

    if submission is not None:
        services = (getattr(submission, "primary_services", None) or "").strip()
        if services:
            eid = add_ev("form", services)
            offerings.append(_claim(
                services, ClaimState.COMPANY_ASSERTED, [eid],
                "stated on the intake form",
            ))
        diffs = (getattr(submission, "differentiators", None) or "").strip()
        if diffs:
            eid = add_ev("form", diffs)
            offerings.append(_claim(
                diffs, ClaimState.COMPANY_ASSERTED, [eid],
                "form differentiator",
            ))
        past = (getattr(submission, "past_performance", None) or "").strip()
        if past:
            add_ev("form", past)
        for code in getattr(submission, "known_naics", None) or []:
            raw = str(code).strip()
            if raw.isdigit() and len(raw) == 6:
                eid = add_ev("form", f"form NAICS {raw}")
                naics.append(DossierNaics(
                    code=raw, role="core",
                    rationale="six-digit NAICS supplied on the intake form",
                    state=ClaimState.COMPANY_ASSERTED,
                    evidence_ids=[eid],
                ))
        for cert in getattr(submission, "certifications", None) or []:
            if str(cert).strip():
                eid = add_ev("form", str(cert))
                channels.append(_claim(
                    f"certification {cert}", ClaimState.COMPANY_ASSERTED,
                    [eid], "form certification",
                ))

    scrape = getattr(research, "scrape", None) if research is not None else None
    if scrape is not None:
        text = scrape.combined_text(max_chars=4000) if hasattr(scrape, "combined_text") else ""
        root = getattr(scrape, "root_url", None) or identity.website
        if text.strip():
            eid = add_ev("website", text[:400], root)
            if not offerings:
                offerings.append(_claim(
                    f"website copy from {root}", ClaimState.COMPANY_ASSERTED,
                    [eid], "homepage and product-adjacent pages were read",
                ))
        pages = getattr(scrape, "pages", None) or []
        if not pages:
            unknowns.append("website scrape returned no pages")

    ingest = product_ingest or {}
    for row in ingest.get("capabilities") or []:
        name = (row.get("name") if isinstance(row, dict) else str(row) or "").strip()
        found_on = (row.get("found_on") if isinstance(row, dict) else "") or identity.website
        if not name:
            continue
        eid = add_ev("capability_ingest", name, found_on)
        offerings.append(_claim(
            name, ClaimState.COMPANY_ASSERTED, [eid],
            "named on the company's own product or solutions page",
        ))
        keywords.append(DossierKeyword(
            term=name, category="capability",
            rationale="product-surface crawl of the bound official domain",
            state=ClaimState.COMPANY_ASSERTED, evidence_ids=[eid],
        ))
        statements.append(RetrievalUnit(
            statement=f"{client_name} sells {name}.",
            offering=name, state=ClaimState.COMPANY_ASSERTED,
            evidence_ids=[eid],
        ))

    for probe in probes or []:
        title = getattr(probe, "name", None) or (
            probe.get("name") if isinstance(probe, dict) else "probe")
        findings = getattr(probe, "findings", None) or (
            probe.get("findings") if isinstance(probe, dict) else "")
        citations = list(getattr(probe, "citations", None) or (
            probe.get("citations") if isinstance(probe, dict) else []) or [])
        if not str(findings or "").strip():
            continue
        eid = add_ev("web_probe", f"{title}: {str(findings)[:300]}",
                     citations[0] if citations else None)
        kind = str(title).casefold()
        if "channel" in kind or "reseller" in kind:
            channels.append(_claim(
                str(findings)[:240], ClaimState.INFERRED, [eid],
                "structured web probe; not yet independently corroborated",
            ))
        elif "boundar" in kind or "not " in kind:
            boundaries.append(_claim(
                str(findings)[:240], ClaimState.INFERRED, [eid],
                "structured web probe naming what the company does not sell",
            ))
        elif "compet" in kind:
            channels.append(_claim(
                str(findings)[:240], ClaimState.INFERRED, [eid],
                "competitor names from a web probe; treat as inferred",
            ))
        else:
            offerings.append(_claim(
                str(findings)[:240], ClaimState.INFERRED, [eid],
                f"structured web probe {title}",
            ))

    if research is not None:
        for url in getattr(research, "web_citations", None) or []:
            add_ev("web_probe", "citation", url)
        for err in getattr(research, "errors", None) or []:
            unknowns.append(str(err))

    seen_kw = {k.term.casefold() for k in keywords}
    for off in offerings:
        term = " ".join(off.text.split())[:80]
        if term and term.casefold() not in seen_kw and off.state != ClaimState.UNKNOWN:
            keywords.append(DossierKeyword(
                term=term, category="capability",
                rationale=off.rationale or "offering text",
                state=off.state, evidence_ids=list(off.evidence_ids),
            ))
            seen_kw.add(term.casefold())
        if off.text and not any(
                s.offering.casefold() == term.casefold() for s in statements):
            statements.append(RetrievalUnit(
                statement=f"{client_name}: {term}",
                offering=term, state=off.state,
                evidence_ids=list(off.evidence_ids),
            ))

    if identity.is_bound and not offerings:
        unknowns.append("identity is bound but no offerings were evidenced")
    if identity.is_bound and not naics:
        unknowns.append("no six-digit NAICS has been evidenced yet")

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
        summary=" ".join(summary_bits),
    )
