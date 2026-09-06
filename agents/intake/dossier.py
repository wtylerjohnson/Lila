"""Canonical company dossier for Step 1 mastery.

The dossier is the evidence-backed company understanding. Claim states are
closed literals so a later retrieval or review surface cannot invent
corroboration. Persistence is a sidecar next to the review packet, not a
silent field on IntakeStrategy.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from agents.intake.extract import (
    MAX_CUSTOMERS,
    RelatedEntity,
    aviation_codes_to_park,
    citation_is_official,
    citation_mismatches_rival,
    customer_excerpt_ok,
    cap_customers,
    is_known_customer,
    known_customers_in_text,
    excerpt_from,
    excerpt_supports_name,
    excerpt_supports_rival,
    extract_surface,
    is_customer_name,
    is_discrete_name,
    is_error_page,
    is_garbage_text,
    is_news_path_citation,
    is_plausible_naics_code,
    is_product_name,
    is_sku_fragment_rival,
    naics_search_role,
    recall_competitors,
    recall_customers,
    site_has_usable_text,
    usable_site_text,
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
    kept_out_naics: list[DossierNaics] = Field(default_factory=list)
    related_entities: list[RelatedEntity] = Field(default_factory=list)
    competitors: list[Claim] = Field(default_factory=list)
    customers: list[Claim] = Field(default_factory=list)
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


def _best_citation(
    citations: list[str],
    official_domain: Optional[str],
    name: str = "",
) -> Optional[str]:
    cites = [c for c in (citations or []) if c]
    official = [c for c in cites if citation_is_official(c, official_domain)]
    pool = official or [
        c for c in cites if not str(c).casefold().endswith(".pdf")
    ] or cites
    token = re.sub(r"[^a-z0-9]+", "", (name or "").casefold())
    if token and len(token) >= 3:
        for cite in pool:
            compact = re.sub(r"[^a-z0-9]+", "", str(cite).casefold())
            if token in compact:
                return cite
    return pool[0] if pool else None


def _evidence_for_name(
    name: str,
    probe_blobs: list[tuple[str, str, list[str]]],
    scrape_text: str,
    fallback_url: Optional[str],
    *,
    require_needle: bool = False,
    scrape=None,
    official_domain: Optional[str] = None,
    prefer_site: bool = False,
    require_compare: bool = False,
) -> tuple[str, Optional[str]]:
    needles = [name]
    parts = [t for t in name.split() if t.casefold() not in {
        "the", "a", "an", "and", "of", "for"}]
    if parts and parts[-1].casefold() != name.casefold():
        needles.append(parts[-1])
    pages = []
    if scrape is not None:
        pages = [
            p for p in (getattr(scrape, "pages", None) or [])
            if getattr(p, "text", "").strip() and not is_error_page(p.text)
        ]

    def from_pages() -> Optional[tuple[str, Optional[str]]]:
        ranked = []
        token = re.sub(r"[^a-z0-9]+", "", name.casefold())
        for page in pages:
            text = page.text or ""
            url = getattr(page, "url", None)
            score = 0
            if token and url and token in re.sub(r"[^a-z0-9]+", "", url.casefold()):
                score += 2
            if official_domain and citation_is_official(url or "", official_domain):
                score += 1
            if url and require_compare and re.search(
                    r"compare|alternativ|versus|/vs", url, re.I):
                score += 3
            if url and require_compare and re.search(
                    r"/news|/press|/blog|/media", url, re.I):
                score -= 2
            ranked.append((score, page, text, url))
        ranked.sort(key=lambda row: row[0], reverse=True)
        for needle in needles:
            for _score, _page, text, url in ranked:
                if needle.casefold() not in text.casefold():
                    continue
                snippet = excerpt_from(text, needle=needle)
                if require_compare:
                    if not snippet or not excerpt_supports_rival(name, snippet):
                        continue
                    if citation_mismatches_rival(
                            url or "", name, official_domain, excerpt=snippet):
                        continue
                elif snippet and excerpt_supports_name(name, snippet):
                    return snippet, url or fallback_url
                else:
                    continue
                if snippet:
                    return snippet, url or fallback_url
        if scrape_text and scrape_text.strip() and not is_error_page(scrape_text):
            for needle in needles:
                if needle.casefold() not in scrape_text.casefold():
                    continue
                snippet = excerpt_from(scrape_text, needle=needle)
                if require_compare:
                    if snippet and excerpt_supports_rival(name, snippet):
                        if not citation_mismatches_rival(
                                fallback_url or "", name, official_domain,
                                excerpt=snippet):
                            return snippet, fallback_url
                    continue
                if snippet and excerpt_supports_name(name, snippet):
                    return snippet, fallback_url
        return None

    def from_probes() -> Optional[tuple[str, Optional[str]]]:
        for needle in needles:
            for _title, findings, citations in probe_blobs:
                if needle.casefold() not in findings.casefold():
                    continue
                snippet = excerpt_from(findings, needle=needle)
                cite = _best_citation(citations, official_domain, name)
                if require_compare:
                    if not snippet or not excerpt_supports_rival(name, snippet):
                        continue
                    if citation_mismatches_rival(
                            cite or "", name, official_domain, excerpt=snippet):
                        continue
                    return snippet, cite or fallback_url
                if snippet and excerpt_supports_name(name, snippet):
                    return snippet, cite or fallback_url
        return None

    if prefer_site:
        hit = from_pages()
        if hit:
            return hit
        hit = from_probes()
        if hit:
            return hit
    else:
        hit = from_probes()
        if hit:
            return hit
        hit = from_pages()
        if hit:
            return hit
    if require_needle:
        return "", None
    for _title, findings, citations in probe_blobs:
        snippet = excerpt_from(findings)
        if snippet:
            return snippet, _best_citation(
                citations, official_domain, name) or fallback_url
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
    kept_out_naics: list[DossierNaics] = []
    naics: list[DossierNaics] = []
    competitors: list[Claim] = []
    customers: list[Claim] = []
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
    scrape_text = usable_site_text(scrape, max_chars=24000)
    if scrape is not None:
        root = getattr(scrape, "root_url", None) or identity.website
        pages = getattr(scrape, "pages", None) or []
        if scrape_text:
            add_ev("website", excerpt_from(scrape_text), root)
        elif pages and any(
                is_error_page(getattr(p, "text", "") or "") for p in pages):
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
        if (
            not name or not is_discrete_name(name) or is_garbage_text(name)
            or not is_product_name(name)
        ):
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
        snippet, url = _evidence_for_name(
            name, probe_blobs, scrape_text, identity.website,
            scrape=scrape, official_domain=identity.official_domain,
            prefer_site=True)
        eid = add_ev("web_probe" if url or snippet else "capability_ingest",
                     snippet or name, url)
        offerings.append(_claim(
            name, ClaimState.INFERRED, [eid] if eid else [],
            "discrete product name extracted from identity-bound research",
        ))
        existing_off.add(name.casefold())

    for name in surface.competitors:
        if is_sku_fragment_rival(name):
            continue
        snippet, url = _evidence_for_name(
            name, probe_blobs, scrape_text, identity.website,
            require_needle=True, scrape=scrape,
            official_domain=identity.official_domain, prefer_site=True,
            require_compare=True)
        if not snippet or not excerpt_supports_rival(name, snippet):
            continue
        if is_news_path_citation(url or ""):
            continue
        if citation_mismatches_rival(
                url or "", name, identity.official_domain, excerpt=snippet):
            continue
        eid = add_ev("website", snippet, url or identity.website)
        if not eid:
            continue
        competitors.append(_claim(
            name, ClaimState.COMPANY_ASSERTED, [eid],
            "named rival on the official site",
        ))

    for name in surface.customers:
        snippet, url = _evidence_for_name(
            name, probe_blobs, scrape_text, identity.website,
            require_needle=True, scrape=scrape,
            official_domain=identity.official_domain, prefer_site=True)
        if not snippet or not excerpt_supports_name(name, snippet):
            continue
        if not customer_excerpt_ok(snippet, url or "") and not is_known_customer(
                name, client_name=client_name):
            continue
        eid = add_ev("website", snippet, url or identity.website)
        if not eid:
            continue
        customers.append(_claim(
            name, ClaimState.COMPANY_ASSERTED, [eid],
            "named customer or case-study proof on the official site",
        ))

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
    stay: list[DossierNaics] = []
    for entry in naics:
        if (
            entry.role == "boundary"
            and entry.state != ClaimState.COMPANY_ASSERTED
        ):
            kept_out_naics.append(entry)
        else:
            stay.append(entry)
    naics = stay

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
            if not real and scrape_text and not is_error_page(scrape_text):
                real = excerpt_from(scrape_text)
            if real:
                add_ev("web_probe", real, url)
        for err in getattr(research, "errors", None) or []:
            unknowns.append(str(err))

    seen_cust = {c.text.casefold() for c in customers}
    seen_comp = {c.text.casefold() for c in competitors}
    for ev in evidence:
        excerpt = ev.excerpt or ""
        if not excerpt.strip():
            continue
        official = ev.source_kind in {"website", "capability_ingest"} or (
            citation_is_official(ev.url or "", identity.official_domain)
        )
        if not official:
            continue
        if customer_excerpt_ok(excerpt, ev.url or ""):
            for name in recall_customers(excerpt, client_name):
                key = name.casefold()
                if key in seen_cust or not is_customer_name(
                        name, client_name=client_name):
                    continue
                if not excerpt_supports_name(name, excerpt):
                    continue
                customers.append(_claim(
                    name, ClaimState.COMPANY_ASSERTED, [ev.evidence_id],
                    "named customer promoted from evidenced site text",
                ))
                seen_cust.add(key)
        for name in known_customers_in_text(excerpt, client_name):
            key = name.casefold()
            if key in seen_cust:
                continue
            if not excerpt_supports_name(name, excerpt):
                continue
            customers.append(_claim(
                name, ClaimState.COMPANY_ASSERTED, [ev.evidence_id],
                "allowlisted customer promoted from official evidence",
            ))
            seen_cust.add(key)
        for name in recall_competitors(excerpt, client_name):
            key = name.casefold()
            if key in seen_comp or is_sku_fragment_rival(name):
                continue
            if not excerpt_supports_rival(name, excerpt):
                continue
            if is_news_path_citation(ev.url or ""):
                continue
            if citation_mismatches_rival(
                    ev.url or "", name, identity.official_domain,
                    excerpt=excerpt):
                continue
            competitors.append(_claim(
                name, ClaimState.COMPANY_ASSERTED, [ev.evidence_id],
                "named rival promoted from a compare claim in evidence",
            ))
            seen_comp.add(key)

    pages = [
        p for p in (getattr(scrape, "pages", None) or [])
        if (getattr(p, "text", "") or "").strip() and not is_error_page(p.text)
    ]
    for page in pages:
        text = page.text or ""
        url = getattr(page, "url", None) or identity.website
        if is_news_path_citation(url or ""):
            for name in known_customers_in_text(text, client_name):
                key = name.casefold()
                if key in seen_cust:
                    continue
                snippet = excerpt_from(text, needle=name)
                if not snippet or not excerpt_supports_name(name, snippet):
                    continue
                if not customer_excerpt_ok(snippet, url or ""):
                    continue
                eid = add_ev("website", snippet, url)
                if not eid:
                    continue
                customers.append(_claim(
                    name, ClaimState.COMPANY_ASSERTED, [eid],
                    "allowlisted customer promoted from official-site text",
                ))
                seen_cust.add(key)
            continue
        for name in recall_competitors(text, client_name):
            key = name.casefold()
            if key in seen_comp or is_sku_fragment_rival(name):
                continue
            snippet = excerpt_from(text, needle=name)
            if not snippet or not excerpt_supports_rival(name, snippet):
                continue
            if citation_mismatches_rival(
                    url or "", name, identity.official_domain, excerpt=snippet):
                continue
            eid = add_ev("website", snippet, url)
            if not eid:
                continue
            competitors.append(_claim(
                name, ClaimState.COMPANY_ASSERTED, [eid],
                "named rival promoted from official-site competition text",
            ))
            seen_comp.add(key)
        for name in known_customers_in_text(text, client_name):
            key = name.casefold()
            if key in seen_cust:
                continue
            snippet = excerpt_from(text, needle=name)
            if not snippet or not excerpt_supports_name(name, snippet):
                continue
            eid = add_ev("website", snippet, url)
            if not eid:
                continue
            customers.append(_claim(
                name, ClaimState.COMPANY_ASSERTED, [eid],
                "allowlisted customer promoted from official-site text",
            ))
            seen_cust.add(key)

    exclude_blob = " ".join(k.term for k in kept_out)
    hay = " ".join(
        [scrape_text]
        + [e.excerpt or "" for e in evidence]
        + [blob[1] for blob in probe_blobs]
        + [k.term for k in kept_out]
    )
    parked = {n.code for n in kept_out_naics}
    core_ids = {n.code for n in naics}
    for code in aviation_codes_to_park(exclude_blob, hay):
        if code in parked:
            continue
        if code in core_ids:
            moved = [n for n in naics if n.code == code]
            naics = [n for n in naics if n.code != code]
            for entry in moved:
                entry.role = "boundary"
                kept_out_naics.append(entry)
            parked.add(code)
            continue
        snippet = excerpt_from(hay, needle=code)
        if not snippet or code not in snippet:
            snippet = (
                f"Aviation is a kept_out namesake; NAICS {code} stays "
                f"out of the search lane"
            )
        eid = add_ev("web_probe", snippet)
        kept_out_naics.append(DossierNaics(
            code=code, role="boundary",
            rationale=snippet[:240],
            state=ClaimState.INFERRED,
            evidence_ids=[eid] if eid else [],
        ))
        parked.add(code)

    kept_names = cap_customers(
        [c.text for c in customers if is_customer_name(
            c.text, client_name=client_name)],
        limit=MAX_CUSTOMERS,
    )
    keep_c = {n.casefold() for n in kept_names}
    customers = [c for c in customers if c.text.casefold() in keep_c][:MAX_CUSTOMERS]

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
    if identity.is_bound and not site_has_usable_text(scrape) and not (
            ingest or {}).get("capabilities"):
        unknowns.append(
            "official site ingest was thin; offerings and competitors "
            "are not invented from generic web search"
        )
    if identity.is_bound and not competitors:
        unknowns.append(
            "no official-site competitors were evidenced; the rival list "
            "is unknown rather than guessed from generic search"
        )
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
        CoverageCell(
            topic="competitors",
            status="covered" if competitors else "missing",
            note=f"{len(competitors)} official-site rival(s)",
        ),
        CoverageCell(
            topic="customers",
            status="covered" if customers else "missing",
            note=f"{len(customers)} customer proof point(s)",
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
        kept_out_naics=kept_out_naics,
        related_entities=related_entities,
        competitors=competitors,
        customers=customers,
        summary=" ".join(summary_bits),
    )
