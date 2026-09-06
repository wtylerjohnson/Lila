"""Deterministic adapters from the dossier onto existing intake consumers.

IntakeStrategy, the capability profile generator, and hybrid.frame_lanes
keep their contracts. This module copies evidenced fields across; it does
not invent scope.preset, exclusions, or operator notes.
"""

from __future__ import annotations

from typing import Optional

from agents.decisions.schemas import (
    IntakeStrategy,
    Keyword,
    KeywordCategory,
    NaicsEntry,
    ResearchEntity,
    SearchSpec,
)
from agents.intake.dossier import ClaimState, CompanyDossier
from agents.intake.extract import (
    _RIVAL_PRODUCTS,
    asserted_owned,
    excerpt_from,
    is_exclusion_name,
    is_plausible_naics_code,
    is_product_name,
    naics_search_role,
    site_has_usable_text,
)
from agents.schemas import IntakeSubmission


_CATEGORY = {
    "capability": KeywordCategory.CAPABILITY,
    "technology": KeywordCategory.TECHNOLOGY,
    "naics": KeywordCategory.NAICS,
    "agency": KeywordCategory.AGENCY,
    "set_aside": KeywordCategory.SET_ASIDE,
    "search_term": KeywordCategory.SEARCH_TERM,
}


def _keywords(dossier: CompanyDossier) -> list[Keyword]:
    out: list[Keyword] = []
    seen: set[str] = set()
    for row in dossier.keywords:
        term = " ".join((row.term or "").split())
        key = term.casefold()
        if not term or key in seen or not is_product_name(term):
            continue
        if row.state == ClaimState.DISPUTED:
            continue
        seen.add(key)
        cat = _CATEGORY.get((row.category or "capability").casefold(),
                            KeywordCategory.CAPABILITY)
        out.append(Keyword(
            term=term, category=cat,
            rationale=(row.rationale or "evidenced in the company dossier")[:240],
            origin="system",
        ))
    return out


def _kept_out(dossier: CompanyDossier) -> list[Keyword]:
    out: list[Keyword] = []
    seen: set[str] = set()
    for row in dossier.kept_out:
        term = " ".join((row.term or "").split())
        key = term.casefold()
        if not term or key in seen or not is_exclusion_name(
                term, client_name=dossier.client_name):
            continue
        seen.add(key)
        out.append(Keyword(
            term=term, category=KeywordCategory.SEARCH_TERM,
            rationale=(row.rationale or "evidenced name collision")[:240],
            origin="system",
            note="polysemy exclusion from intake identity/boundary research",
        ))
    return out


def _naics(dossier: CompanyDossier, submission: Optional[IntakeSubmission]):
    codes: list[str] = []
    meta: list[NaicsEntry] = []
    kept_meta: list[NaicsEntry] = []
    seen: set[str] = set()
    exclude_blob = " ".join(k.term for k in dossier.kept_out)
    parked = list(getattr(dossier, "kept_out_naics", None) or [])
    for row in list(dossier.naics) + parked:
        if row.code in seen or row.state == ClaimState.DISPUTED:
            continue
        if not is_plausible_naics_code(row.code):
            continue
        seen.add(row.code)
        parked_set = {n.code for n in parked}
        role = row.role
        if row.code in parked_set:
            role = "boundary"
        elif row.state != ClaimState.COMPANY_ASSERTED:
            role = naics_search_role(
                row.code, row.rationale, exclude_blob=exclude_blob)
            if row.role == "boundary":
                role = "boundary"
        entry = NaicsEntry(
            code=row.code, title=row.title, role=role, origin="system",
            rationale=row.rationale or (
                "six-digit NAICS retained from evidenced intake inputs"
            ),
            note=(
                "namesake or collision industry; not a search lane"
                if role == "boundary" else ""
            ),
        )
        if role == "core":
            codes.append(row.code)
            meta.append(entry)
        else:
            kept_meta.append(entry)
    if submission is not None:
        for raw in submission.known_naics or []:
            code = str(raw).strip()
            if is_plausible_naics_code(code) and code not in seen:
                seen.add(code)
                codes.append(code)
                meta.append(NaicsEntry(
                    code=code, role="core", origin="system",
                    rationale="six-digit NAICS supplied on the intake form "
                              "and retained by the dossier adapter",
                ))
    return codes, meta, kept_meta


def _entities(dossier: CompanyDossier) -> list[ResearchEntity]:
    out: list[ResearchEntity] = []
    seen: set[tuple[str, str]] = set()
    for off in dossier.offerings:
        name = " ".join(off.text.split())[:80]
        key = ("product", name.casefold())
        if not name or key in seen or off.state == ClaimState.DISPUTED:
            continue
        if not is_product_name(name):
            continue
        if off.state not in (ClaimState.COMPANY_ASSERTED, ClaimState.CORROBORATED,
                             ClaimState.INFERRED):
            continue
        seen.add(key)
        src = off.evidence_ids[0] if off.evidence_ids else ""
        out.append(ResearchEntity(
            name=name, kind="product",
            rationale=off.rationale or "asserted offering",
            source=src,
        ))
    for rival in getattr(dossier, "competitors", None) or []:
        name = " ".join(rival.text.split())[:80]
        key = ("competitor", name.casefold())
        if not name or key in seen or rival.state == ClaimState.DISPUTED:
            continue
        if not rival.evidence_ids:
            continue
        seen.add(key)
        src = rival.evidence_ids[0]
        out.append(ResearchEntity(
            name=name, kind="competitor",
            rationale=rival.rationale or "named rival on the official site",
            source=src,
        ))
    for ch in dossier.channels:
        text = ch.text.casefold()
        kind = "reseller" if "reseller" in text or "distributor" in text or "channel" in text else "competitor"
        if "certification" in text:
            continue
        if kind == "competitor":
            continue
        name = " ".join(ch.text.split())[:80]
        key = (kind, name.casefold())
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(ResearchEntity(
            name=name, kind=kind,  # type: ignore[arg-type]
            rationale=ch.rationale or "channel or rival named in research",
        ))
    return out


def _searches(codes: list[str], keywords: list[Keyword],
              set_asides: list[str]) -> list[SearchSpec]:
    terms = [k.term for k in keywords
             if k.category in (KeywordCategory.CAPABILITY,
                               KeywordCategory.TECHNOLOGY,
                               KeywordCategory.SEARCH_TERM)][:8]
    return [
        SearchSpec(
            source="sam.gov", query_terms=terms, naics_codes=codes,
            set_asides=set_asides,
            rationale="capability vocabulary against live SAM notices; "
                      "launches only after approval",
        ),
        SearchSpec(
            source="usaspending.gov", query_terms=terms, naics_codes=codes,
            rationale="award and incumbent lane using the same capability terms",
        ),
        SearchSpec(
            source="web", query_terms=terms,
            rationale="bounded public-web corroboration, not a census",
        ),
    ]


def retrieval_frame(dossier: CompanyDossier) -> dict:
    """Shape hybrid.frame_lanes expects, derived from the dossier."""
    products = [
        u.offering for u in dossier.capability_statements
        if u.offering and is_product_name(u.offering)
        and u.offering.casefold() not in _RIVAL_PRODUCTS
    ]
    rivals = [
        c.text.split(".")[0][:80]
        for c in (getattr(dossier, "competitors", None) or [])
        if c.text and c.evidence_ids
    ]
    if not rivals:
        rivals = [
            c.text.split(".")[0][:80] for c in dossier.channels
            if "competitor" in (c.rationale or "").casefold()
            or "rival" in c.text.casefold()
        ]
    customers = [
        c.text.split(".")[0][:80]
        for c in (getattr(dossier, "customers", None) or [])
        if c.text and c.evidence_ids
    ]
    tier2 = [k.term for k in dossier.keywords
             if k.category in ("capability", "technology", "search_term")
             and is_product_name(k.term)]
    statements = [
        u.statement for u in dossier.capability_statements
        if u.statement.strip() and is_product_name(u.offering or "")
    ]
    frame = {
        "client_name": dossier.client_name,
        "as_ordered": {
            "tier1": products[:12] or [dossier.client_name],
            "tier2": tier2[:24],
            "tier3": [],
        },
        "screen_routing": {
            "tier1_client_names": products[:8] or [dossier.client_name],
            "tier1_rival_names": rivals[:8],
            "customer_names": customers[:8],
        },
        "competitors": rivals[:12],
        "customers": customers[:12],
    }
    lanes = None
    try:
        from tools.retrieval.hybrid import frame_lanes
        lanes = frame_lanes(frame, statements)
        # Drop the live vocab object; persist JSON-safe query sets only.
        lanes = {
            "bm25_terms": list(lanes.get("bm25_terms") or []),
            "dense_queries": list(lanes.get("dense_queries") or []),
            "tier2": list(lanes.get("tier2") or []),
        }
    except Exception:  # noqa: BLE001 - retrieval shape is additive
        lanes = {"bm25_terms": tier2, "dense_queries": statements,
                 "tier2": tier2}
    return {
        "schema_version": "intake_retrieval.v1",
        "capability_statements": statements,
        "competitors": rivals[:12],
        "customers": customers[:12],
        "frame": frame,
        "lanes": lanes,
    }


def strategy_from_dossier(
    dossier: CompanyDossier,
    submission: Optional[IntakeSubmission] = None,
    *,
    sources_reviewed: Optional[list[str]] = None,
) -> IntakeStrategy:
    """Build a reviewable IntakeStrategy without calling the model."""
    keywords = _keywords(dossier)
    codes, meta, kept_naics = _naics(dossier, submission)
    entities = _entities(dossier)
    kept_out = _kept_out(dossier)
    set_asides = []
    agencies = []
    if submission is not None:
        set_asides = [c for c in (submission.certifications or []) if c]
        agencies = [a for a in (submission.target_agencies or []) if a]
    sources = list(sources_reviewed or [])
    if dossier.identity.website:
        sources.append(dossier.identity.website)
    for ev in dossier.evidence:
        if ev.url and ev.url not in sources:
            sources.append(ev.url)
    if not sources:
        sources = ["intake_dossier"]

    if dossier.identity.is_bound:
        gate = (
            "Review the company dossier, yield titles, and adversarial "
            "receipts, then confirm engagement scope. Approval does not "
            "invent scope.preset and does not launch searches by itself."
        )
        pursuit = dossier.summary or (
            f"Pursue federal work that matches evidenced offerings for "
            f"{dossier.client_name}."
        )
        searches = _searches(codes, keywords, set_asides)
        confidence = max(0.15, min(dossier.identity.confidence, 0.85))
    else:
        gate = dossier.identity.question or (
            "Identity is unresolved. Answer the clarifying question "
            "before any search vocabulary is treated as this company."
        )
        pursuit = (
            f"Identity unresolved for {dossier.client_name}. "
            + (dossier.identity.rationale or "")
        )
        searches = []
        confidence = 0.0
        # Do not emit NAICS/keywords that would look like a bound company.
        keywords = []
        codes, meta, kept_naics = [], [], []
        entities = []
        kept_out = []

    return IntakeStrategy(
        client_name=dossier.client_name,
        pursuit_strategy=pursuit.strip(),
        keywords=keywords,
        inferred_naics=codes,
        naics_meta=meta,
        kept_out_naics=kept_naics,
        target_agencies=agencies,
        set_aside_angles=set_asides,
        searches=searches,
        research_entities=entities,
        kept_out=kept_out,
        confidence=confidence,
        sources_reviewed=sources,
        requires_human_review=True,
        review_gate=gate,
    )


def apply_site_honesty(strategy: IntakeStrategy, identity, research) -> IntakeStrategy:
    """Keep review_gate honest when official-site ingest did not render."""
    if not getattr(identity, "is_bound", False):
        return strategy
    if site_has_usable_text(getattr(research, "scrape", None)):
        return strategy
    if getattr(identity, "website_source", "") == "not_found":
        return strategy
    scrape = getattr(research, "scrape", None)
    failures = list(getattr(scrape, "render_failures", None) or [])
    pages = list(getattr(scrape, "pages", None) or [])
    fetched = len(failures) or len(pages)
    note = (
        "Official-site ingest failed to render usable pages"
        + (f" ({fetched} fetched were empty or interstitial)" if fetched else "")
        + ". E2 is not a pass. Do not treat generic web research as the site."
    )
    gate = (getattr(strategy, "review_gate", None) or "").strip()
    low = gate.casefold()
    if "failed to render" in low or "scrape returned nothing" in low:
        return strategy
    new_gate = f"{note} {gate}".strip() if gate else note
    copier = getattr(strategy, "model_copy", None)
    if callable(copier):
        return copier(update={"review_gate": new_gate})
    strategy.review_gate = new_gate
    return strategy


def apply_kept_out(strategy: IntakeStrategy, dossier: CompanyDossier) -> IntakeStrategy:
    """Seed strategy kept_out lanes from evidenced name and NAICS collisions."""
    extras = _kept_out(dossier)
    _, _, kept_naics = _naics(dossier, None)
    updates: dict = {}
    current_kept = list(getattr(strategy, "kept_out", None) or [])
    have = {k.term.casefold() for k in current_kept}
    merged = list(current_kept)
    for row in extras:
        if row.term.casefold() in have:
            continue
        merged.append(row)
        have.add(row.term.casefold())
    if merged != current_kept:
        updates["kept_out"] = merged
    current_naics = list(getattr(strategy, "kept_out_naics", None) or [])
    have_n = {getattr(e, "code", "") for e in current_naics}
    merged_n = list(current_naics)
    for entry in kept_naics:
        if entry.code in have_n:
            continue
        merged_n.append(entry)
        have_n.add(entry.code)
    if [getattr(e, "code", "") for e in merged_n] != [
            getattr(e, "code", "") for e in current_naics]:
        updates["kept_out_naics"] = merged_n
    if not updates:
        return strategy
    copier = getattr(strategy, "model_copy", None)
    if callable(copier):
        return copier(update=updates)
    for key, val in updates.items():
        setattr(strategy, key, val)
    return strategy


def _next_eid(dossier: CompanyDossier) -> str:
    nums = []
    for ev in dossier.evidence:
        raw = (ev.evidence_id or "").lstrip("E")
        if raw.isdigit():
            nums.append(int(raw))
    return f"E{(max(nums) if nums else 0) + 1:03d}"


def _link_naics_evidence(dossier: CompanyDossier, code: str, snippet: str):
    from agents.intake.dossier import EvidenceItem

    for ev in dossier.evidence:
        if code in (ev.excerpt or "") and ev.evidence_id:
            return ev.evidence_id
    text = (snippet or "").strip()
    if not text or code not in text or len(text.split()) < 5:
        return None
    eid = _next_eid(dossier)
    dossier.evidence.append(EvidenceItem(
        evidence_id=eid, source_kind="web_probe", excerpt=text[:500],
    ))
    return eid


def merge_strategy_into_dossier(
    dossier: CompanyDossier, strategy: IntakeStrategy,
) -> CompanyDossier:
    """Copy discrete strategy products/NAICS into a thin dossier.

    Inferred only. Does not invent scope.preset. Blob terms are dropped.
    """
    from agents.intake.dossier import (
        Claim, ClaimState, DossierKeyword, DossierNaics, RetrievalUnit,
    )

    repaired = dossier.model_copy(deep=True)
    have_off = {o.text.casefold() for o in repaired.offerings}
    have_kw = {k.term.casefold() for k in repaired.keywords}
    have_n = {n.code for n in repaired.naics}
    changed = False

    for ent in strategy.research_entities or []:
        if getattr(ent, "kind", "") != "product":
            continue
        name = " ".join((ent.name or "").split())
        if not is_product_name(name) or name.casefold() in have_off:
            continue
        blob = " ".join(e.excerpt or "" for e in repaired.evidence)
        if name.casefold() not in blob.casefold():
            continue
        if not asserted_owned(blob, name, client_name=repaired.client_name):
            continue
        repaired.offerings.append(Claim(
            text=name, state=ClaimState.INFERRED,
            rationale="discrete product retained from the strategy composer",
        ))
        have_off.add(name.casefold())
        changed = True

    for kw in strategy.keywords or []:
        term = " ".join((kw.term or "").split())
        if not is_product_name(term) or term.casefold() in have_kw:
            continue
        cat = getattr(getattr(kw, "category", None), "value", None) or "capability"
        if str(cat).casefold() not in {"capability", "technology", "search_term"}:
            continue
        repaired.keywords.append(DossierKeyword(
            term=term, category=str(cat).casefold(),
            rationale=(kw.rationale or "strategy keyword")[:240],
            state=ClaimState.INFERRED,
        ))
        have_kw.add(term.casefold())
        changed = True

    meta = {e.code: e for e in (strategy.naics_meta or [])}
    for code in strategy.inferred_naics or []:
        raw = str(code).strip()
        if raw in have_n or not is_plausible_naics_code(raw):
            continue
        entry = meta.get(raw)
        why = (getattr(entry, "rationale", None) or "").strip()
        hay = " ".join(
            [e.excerpt or "" for e in repaired.evidence]
            + [why, getattr(strategy, "pursuit_strategy", "") or ""]
        )
        snippet = excerpt_from(hay, needle=raw) if raw in hay else ""
        if not snippet and why and len(why.split()) >= 5 and raw in why:
            snippet = why
        eid = _link_naics_evidence(repaired, raw, snippet)
        if not eid:
            continue
        if len(why.split()) < 5:
            why = snippet if len((snippet or "").split()) >= 5 else (
                f"company research cited NAICS {raw} for the bound firm"
            )
        exclude_blob = " ".join(k.term for k in repaired.kept_out)
        role = getattr(entry, "role", None) or naics_search_role(
            raw, why or snippet, exclude_blob=exclude_blob)
        if naics_search_role(raw, why or snippet, exclude_blob=exclude_blob) == "boundary":
            role = "boundary"
        repaired.naics.append(DossierNaics(
            code=raw, title=getattr(entry, "title", "") or "",
            role=role,
            rationale=why, state=ClaimState.INFERRED,
            evidence_ids=[eid],
        ))
        have_n.add(raw)
        changed = True

    have_stmt = {s.offering.casefold() for s in repaired.capability_statements if s.offering}
    for off in repaired.offerings:
        if not is_product_name(off.text) or off.text.casefold() in have_stmt:
            continue
        repaired.capability_statements.append(RetrievalUnit(
            statement=f"{repaired.client_name} sells {off.text}.",
            offering=off.text, state=off.state,
            evidence_ids=list(off.evidence_ids),
        ))
        have_stmt.add(off.text.casefold())
        changed = True

    if not changed:
        return dossier
    # drop the stale "no NAICS" unknown if we now have codes
    if repaired.naics:
        repaired.unknowns = [
            u for u in repaired.unknowns
            if "no six-digit NAICS" not in u
        ]
    return repaired
