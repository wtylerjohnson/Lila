"""Identity resolution BEFORE any deep company research.

A company name is not an identity. Two firms can share a name; a weak
website guess can scrape the wrong one and poison every later claim.
This gate produces candidates, binds an official domain only when the
evidence is unique enough, and otherwise abstains with one question.

Zero opportunity search. Zero outreach. Scope is not inferred here.
"""

from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from agents.intake import IDENTITY_BIND_MIN_CONFIDENCE
from tools.entity_lineage import company_matches, normalize_company


class IdentityCandidate(BaseModel):
    name: str
    official_domain: Optional[str] = None
    website: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    evidence_urls: list[str] = Field(default_factory=list)


class IdentityRoster(BaseModel):
    """Structured extraction of possible companies for a name query."""

    candidates: list[IdentityCandidate] = Field(default_factory=list)
    ambiguity_note: str = ""


class IdentityResolution(BaseModel):
    query_name: str
    status: Literal["bound", "abstain", "blocked"]
    bound_name: Optional[str] = None
    official_domain: Optional[str] = None
    website: Optional[str] = None
    website_source: str = "not_found"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    question: Optional[str] = None
    rationale: str = ""
    errors: list[str] = Field(default_factory=list)

    @property
    def is_bound(self) -> bool:
        return self.status == "bound" and bool(self.official_domain or self.website)


_FIND_IDENTITY_SYSTEM = """\
You are resolving which real company a short name refers to. Use web search.
Return DISTINCT legal entities that reasonably match the name, not directories
(LinkedIn, Crunchbase, Bloomberg, SAM listings as the company itself).
Prefer the company's own domain. If two companies share the name, list both.
Do not invent a URL. If you cannot tell them apart, say so in ambiguity_note.
"""

_STRUCTURE_IDENTITY_SYSTEM = """\
Extract official-company candidates from the findings. Each candidate needs a
name, optional official homepage URL, a 0..1 confidence, and a short rationale
grounded in the findings. official_domain is the registrable host (example.com),
not a path. Do not invent URLs. If the name is ambiguous, keep more than one
candidate and say why in ambiguity_note.
"""


def registrable_domain(url: Optional[str]) -> Optional[str]:
    """Host without leading www. Empty or unparseable input yields None."""
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _same_company(left: str, right: str) -> bool:
    if not (left or "").strip() or not (right or "").strip():
        return False
    if normalize_company(left) == normalize_company(right):
        return True
    return company_matches(left, right) or company_matches(right, left)


def _merge_same_door(candidates: list[IdentityCandidate]) -> list[IdentityCandidate]:
    """Collapse suffix/punctuation variants of one door; keep the strongest."""
    merged: list[IdentityCandidate] = []
    for cand in candidates:
        if not (cand.name or "").strip() and not cand.website:
            continue
        hit = None
        for existing in merged:
            same_name = _same_company(existing.name, cand.name)
            same_domain = (
                existing.official_domain
                and cand.official_domain
                and existing.official_domain == cand.official_domain
            )
            if same_name or same_domain:
                hit = existing
                break
        if hit is None:
            if cand.website and not cand.official_domain:
                cand.official_domain = registrable_domain(cand.website)
            merged.append(cand)
            continue
        if cand.confidence > hit.confidence:
            if cand.website and not cand.official_domain:
                cand.official_domain = registrable_domain(cand.website)
            merged[merged.index(hit)] = cand
        else:
            for url in cand.evidence_urls:
                if url not in hit.evidence_urls:
                    hit.evidence_urls.append(url)
    return merged


def _form_candidate(query_name: str, website: Optional[str]) -> Optional[IdentityCandidate]:
    domain = registrable_domain(website)
    if not domain and not website:
        return None
    return IdentityCandidate(
        name=query_name,
        official_domain=domain,
        website=website,
        confidence=1.0 if website else 0.0,
        rationale="official website supplied on the intake form",
        evidence_urls=[website] if website else [],
    )


def bind_identity(
    query_name: str,
    candidates: list[IdentityCandidate],
    *,
    form_website: Optional[str] = None,
    min_confidence: float = IDENTITY_BIND_MIN_CONFIDENCE,
) -> IdentityResolution:
    """Deterministic bind / abstain / blocked decision. No model calls."""
    query = (query_name or "").strip()
    if not query:
        return IdentityResolution(
            query_name=query_name or "",
            status="blocked",
            rationale="company name is required",
            question="What is the company's exact legal or trading name?",
        )

    roster = _merge_same_door(list(candidates))
    form = _form_candidate(query, form_website)
    if form is not None:
        # A form URL is an assertion, not proof. It still has to survive a
        # rival candidate that looks equally official.
        already = any(
            c.official_domain == form.official_domain
            or (c.website and form.website and c.website.rstrip("/") == form.website.rstrip("/"))
            for c in roster
        )
        if not already:
            roster.append(form)

    viable = [c for c in roster if c.confidence >= min_confidence and (
        c.official_domain or c.website)]
    viable.sort(key=lambda c: c.confidence, reverse=True)

    if len(viable) == 1:
        chosen = viable[0]
        source = "form" if (
            form is not None
            and form.official_domain
            and form.official_domain == chosen.official_domain
        ) else "web_search"
        return IdentityResolution(
            query_name=query,
            status="bound",
            bound_name=chosen.name or query,
            official_domain=chosen.official_domain,
            website=chosen.website or (
                f"https://{chosen.official_domain}" if chosen.official_domain else None
            ),
            website_source=source,
            confidence=chosen.confidence,
            candidates=roster,
            rationale=chosen.rationale or "unique official-domain candidate above the bind floor",
        )

    if len(viable) >= 2:
        top, rival = viable[0], viable[1]
        same_domain = (
            top.official_domain
            and rival.official_domain
            and top.official_domain == rival.official_domain
        )
        if same_domain or _same_company(top.name, rival.name):
            source = "form" if (
                form is not None
                and form.official_domain == top.official_domain
            ) else "web_search"
            return IdentityResolution(
                query_name=query,
                status="bound",
                bound_name=top.name or query,
                official_domain=top.official_domain,
                website=top.website or (
                    f"https://{top.official_domain}" if top.official_domain else None
                ),
                website_source=source,
                confidence=top.confidence,
                candidates=roster,
                rationale="multiple mentions collapse to one official domain",
            )
        labels = []
        for c in viable[:3]:
            label = c.name or "unnamed"
            if c.official_domain:
                label += f" ({c.official_domain})"
            labels.append(label)
        question = (
            "Which company is this engagement for: " + "; ".join(labels) + "?"
        )
        return IdentityResolution(
            query_name=query,
            status="abstain",
            candidates=roster,
            question=question,
            rationale=(
                "two or more official-domain candidates cleared the bind floor; "
                "refusing to guess prevents wrong-company contamination"
            ),
        )

    # Nothing cleared the floor.
    if form is not None and form.official_domain:
        # Form URL with no competing high-confidence rival: bind the form.
        return IdentityResolution(
            query_name=query,
            status="bound",
            bound_name=query,
            official_domain=form.official_domain,
            website=form.website,
            website_source="form",
            confidence=form.confidence,
            candidates=roster or [form],
            rationale="form-supplied official website; no rival candidate cleared the bind floor",
        )

    weak = [c for c in roster if c.official_domain or c.website]
    if weak:
        question = (
            f"What is the official website for {query}? "
            "A low-confidence guess was withheld to avoid scraping the wrong firm."
        )
        return IdentityResolution(
            query_name=query,
            status="abstain",
            candidates=roster,
            question=question,
            rationale=(
                f"no candidate reached the {min_confidence:.2f} bind floor; "
                "website guess confidence is a hard gate, not a hint"
            ),
        )

    return IdentityResolution(
        query_name=query,
        status="abstain",
        candidates=roster,
        question=f"What is the official website for {query}?",
        rationale="no official domain could be bound from the name alone",
    )


def resolve_identity(
    query_name: str,
    *,
    website: Optional[str] = None,
    engine=None,
    min_confidence: float = IDENTITY_BIND_MIN_CONFIDENCE,
) -> IdentityResolution:
    """Resolve identity, optionally using a research engine for candidates.

    When ``engine`` is omitted the decision is form-and-name only: a supplied
    website binds, a bare name abstains. That is the honest offline path.
    """
    query = (query_name or "").strip()
    form_url = (str(website).strip() if website else None) or None
    if engine is None:
        return bind_identity(query, [], form_website=form_url,
                             min_confidence=min_confidence)

    try:
        findings, citations = engine.web_research(
            system_prompt=_FIND_IDENTITY_SYSTEM,
            query=(
                f"Identify the official company named {query!r}"
                + (f" (hint website: {form_url})" if form_url else "")
                + ". List other firms that share this name if they exist."
            ),
            max_uses=3,
        )
    except Exception as exc:  # noqa: BLE001 - identity fails closed, never guesses
        resolution = bind_identity(query, [], form_website=form_url,
                                   min_confidence=min_confidence)
        resolution.errors.append(f"identity search: {exc}")
        if not resolution.is_bound:
            resolution.status = "abstain"
            resolution.question = resolution.question or (
                f"What is the official website for {query}?"
            )
            resolution.rationale = (
                "identity search failed; refusing to proceed on the name alone"
            )
        return resolution

    roster = IdentityRoster(candidates=[])
    if findings:
        try:
            roster = engine.structure(
                instructions=_STRUCTURE_IDENTITY_SYSTEM,
                findings=findings,
                schema=IdentityRoster,
            )
        except Exception as exc:  # noqa: BLE001
            extra = IdentityCandidate(
                name=query,
                rationale=f"unstructured findings (structure failed: {exc})",
                evidence_urls=list(citations or []),
            )
            roster = IdentityRoster(candidates=[extra],
                                    ambiguity_note=str(exc))

    for cand in roster.candidates:
        if cand.website and not cand.official_domain:
            cand.official_domain = registrable_domain(cand.website)
        for url in citations or []:
            if url not in cand.evidence_urls:
                cand.evidence_urls.append(url)

    resolution = bind_identity(
        query, roster.candidates, form_website=form_url,
        min_confidence=min_confidence,
    )
    if roster.ambiguity_note and resolution.status == "bound":
        # A model-stated ambiguity overrides a lucky unique extract.
        others = [c for c in resolution.candidates
                  if c.official_domain and c.official_domain != resolution.official_domain]
        if others:
            resolution = bind_identity(
                query,
                resolution.candidates,
                form_website=None,
                min_confidence=min_confidence,
            )
            if resolution.status == "bound" and others:
                labels = [resolution.bound_name or query]
                labels += [c.name for c in others[:2] if c.name]
                resolution.status = "abstain"
                resolution.bound_name = None
                resolution.official_domain = None
                resolution.website = None
                resolution.website_source = "not_found"
                resolution.question = (
                    "Identity search flagged ambiguity. Which company: "
                    + "; ".join(labels) + "?"
                )
                resolution.rationale = roster.ambiguity_note
    return resolution
