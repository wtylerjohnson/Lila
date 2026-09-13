"""Internal Research Picture synthesis from recorded source evidence.

Produces data/review/<slug>.research_picture.md and a searches JSON sidecar.
Source excerpts, research inferences and qualification remain distinct.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from agents.decisions.engine import DecisionEngine, research_engine
from agents.decisions.research_evidence import (
    EvidenceSource, ResearchClaim, build_registry, checked_claim, claim_text,
    classification, clean_text, coverage_gaps, source_record,
    source_temporal_assessment, source_action_evidence,
)

from tools.relevance.temporal import clock_context, instant

LAYER = "research-picture"

SYSTEM_PROMPT = """\
You are the synthesis layer of a LILA. You receive the
DISTILLED output of a parallel sweep across many sources: SAM.gov notices with
triage verdicts, award completion dates with incumbents, subaward prime
flows, federal news, Federal Register and Regulations.gov documents, and CISA
KEV velocity; all for one client's offering.

Use evidence_registry as the only factual basis. It contains recorded passages,
not automatically verified truth. Echo exact source_id and passage_id values;
include an exact quote for each claim. Use typed claims for procurement_state,
offering_fit, value, incumbent, vehicle_route and next_action. Source_fact
statements are rendered from their cited excerpts. Use inference for reasoned
connections and proposed actions, never to fill missing procurement facts.
A contract completion date alone is not a recompete. A channel relationship
does not establish access to a procurement. Web rows remain discovery signals,
even when their URL points to a government site. Unknown timing stays unknown.

Use top_opportunities only for supplied source IDs; do not invent solicitation
IDs. Each entry needs claims. Use narrative_claims with section tags for the
headline, demand signals, market structure, watchlist and next action. Legacy
free-text fields remain readable for old artifacts but unreferenced prose will
be withheld. Report missing evidence in gaps. Neither triage nor this synthesis
qualifies a lead or approves a requirement. Source failures are not an empty
market. Respect the source_coverage_verdict and named gaps.

If operator_focus names agencies, the OPERATOR has focused this engagement
there at the gate: open the headline by naming the focus ("Engagement focus:
DHS"), lead the ranking and the demand story with evidence at those agencies
and their components, and only then widen to the rest of the market. The focus
is emphasis, never a filter; report the whole market and never discard or
zero-weight off-focus evidence. If operator_focus is all-agencies, say nothing
about focus."""


class TopOpportunity(BaseModel):
    id: str = Field(description="source_id of the notice, echoed exactly")
    title: str
    why_now: str = Field(description="1-2 sentences; cross-reference other sources where they connect")
    deadline: Optional[str] = None
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=12)
    classification: str = "research_signal"
    validation_status: str = "UNVALIDATED"
    source_url: Optional[str] = None
    temporal_status: dict = Field(default_factory=dict)
    action_evidence: list[dict] = Field(default_factory=list)


class ResearchPicture(BaseModel):
    client_name: str
    headline: str = Field(description="2-3 sentences: the state of play for this client, today")
    top_opportunities: list[TopOpportunity] = Field(max_length=5)
    demand_signals: list[str] = Field(
        description="each grounded in a news item, docket, KEV datum, or budget fact from the inputs")
    market_structure: str = Field(
        description="incumbents, expiring contracts, prime/sub flows; who holds the ground and when it shakes loose")
    watchlist: list[str] = Field(
        description="monitor-grade items and forming programs worth a weekly glance")
    next_action: str = Field(description="the single most valuable move, one sentence")
    gaps: list[str] = Field(default_factory=list,
                            description="what this sweep could not answer; honesty, not filler")

    narrative_claims: list[ResearchClaim] = Field(default_factory=list, max_length=24)
    research_signals: list[TopOpportunity] = Field(default_factory=list)
    evidence_registry: dict[str, EvidenceSource] = Field(default_factory=dict)
    validation_issues: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    validation_version: Optional[str] = None
    evidence_as_of: Optional[str] = None
    assessment_clocks: dict = Field(default_factory=dict)
    operator_focus_names: list[str] = Field(default_factory=list)


_VALIDATION_VERSION = "research-picture.evidence.v4"
_VERIFY_ACTION = "Verify original notice identity, current procurement state, requirements and available action."


def validate_picture(p: ResearchPicture, registry: dict[str, EvidenceSource],
                     gaps: list[str], as_of: datetime, *, mode='current',
                     presented_at=None, clocks=None) -> ResearchPicture:
    """Ignore model-supplied status/registry; rebuild visible prose from checked claims."""
    context = clock_context(as_of, mode=mode, presented_at=presented_at, supplied=clocks)
    as_of = instant(context['buying_status_as_of'])
    issues = list(p.validation_issues) if p.validation_version == _VALIDATION_VERSION else []
    from tools.relevance.taxonomy import load_taxonomy, derived_taxonomy
    try:
        taxonomy = load_taxonomy(p.client_name) or derived_taxonomy(p.client_name)
    except (OSError, ValueError):
        taxonomy = None
    if taxonomy is None:
        gaps = [*gaps, 'Client capability vocabulary unavailable; requested offering support cannot be confirmed.']
    def check(claims, source_id=None):
        accepted = []
        for claim in claims:
            verified, reason = checked_claim(claim, registry)
            if (not reason and source_id and claim.basis == 'source_fact'
                    and claim.kind != 'context'
                    and any(ref.source_id != source_id for ref in claim.evidence)):
                reason = f'source-specific {claim.kind} cites a different source than {source_id}'
            if reason:
                issues.append(f"{claim.kind}: {reason}")
            else:
                accepted.append(verified)
        return accepted

    opportunities = []
    seen = set()
    for item in p.top_opportunities:
        source = registry.get(item.id)
        if source is None:
            issues.append(f"Rejected opportunity: unknown source ID {item.id}")
            continue
        if item.id in seen:
            issues.append(f"Repeated source ID {item.id} omitted.")
            continue
        seen.add(item.id)
        claims = check(item.claims, item.id)
        # An opportunity's cited facts must include its own source, not a
        # different notice whose text happens to look attractive.
        if not any(ref.source_id == item.id for c in claims for ref in c.evidence):
            claims = []
            issues.append(f"{item.id}: no evidence bound to this source; verify before promotion.")
        kind = classification(source, claims, as_of, taxonomy, context)
        timing = source_temporal_assessment(source, as_of, context)
        timing['current_action'] = 'confirmed_current_action' if kind == 'confirmed_opportunity' else 'not_established'
        action_evidence = source_action_evidence(source, claims, as_of)
        if kind != 'confirmed_opportunity' and any(c.kind == 'offering_fit' for c in claims):
            issues.append(f'{item.id}: current requested offering and response action require source-bound support.')
        why = " ".join(claim_text(c, registry) for c in claims)
        if kind != "confirmed_opportunity":
            why = (why + " " + _VERIFY_ACTION).strip()
        else:
            why += " Original notice evidence checked; fit, access and seller readiness require existing qualification."
        opportunities.append(TopOpportunity(
            id=source.source_id, title=source.title, deadline=source.deadline,
            source_url=source.url, claims=claims, classification=kind,
            validation_status="EXCERPTS_CHECKED_NOT_QUALIFIED" if claims else "VERIFICATION_REQUIRED",
            why_now=why, temporal_status=timing, action_evidence=action_evidence))
    narrative = check(p.narrative_claims)
    sections = {key: [] for key in ('headline', 'demand_signals', 'market_structure', 'watchlist', 'next_action')}
    for claim in narrative:
        sections[claim.section].append(claim_text(claim, registry))
    if not narrative:
        issues.append('Unreferenced narrative withheld; source-backed claims are required.')
    signals = [TopOpportunity(
        id=s.source_id, title=s.title, deadline=s.deadline, source_url=s.url,
        why_now=_VERIFY_ACTION, classification="research_signal",
        validation_status="VERIFICATION_REQUIRED")
        for s in registry.values() if s.lane == 'web' and s.source_id not in seen]
    for item in opportunities + signals:
        source = registry[item.id]
        if not source.retrieved_at:
            gaps = gaps + [f'{item.id}: retrieval timestamp not recorded.']
        if not source.deadline:
            gaps = gaps + [f'{item.id}: response deadline unknown.']
    # Model gaps are suggestions, not proven source failures.
    model_gaps = ([g for g in p.gaps if g.startswith('Suggested verification: ')]
                  if p.validation_version == _VALIDATION_VERSION else
                  [f'Suggested verification: {clean_text(g)}' for g in p.gaps])
    return p.model_copy(update={
        'headline': ' '.join(sections['headline']) or 'Research signals require original-source verification.',
        'top_opportunities': opportunities, 'research_signals': signals,
        'demand_signals': sections['demand_signals'],
        'market_structure': ' '.join(sections['market_structure']) or 'No source-backed market claim supplied.',
        'watchlist': sections['watchlist'],
        'next_action': ' '.join(sections['next_action']) or _VERIFY_ACTION,
        'gaps': list(dict.fromkeys(gaps + model_gaps + issues)),
        'evidence_gaps': list(dict.fromkeys(gaps)),
        'narrative_claims': narrative, 'evidence_registry': registry,
        'validation_issues': list(dict.fromkeys(issues)),
        'validation_version': _VALIDATION_VERSION, 'evidence_as_of': as_of.isoformat(),
        'assessment_clocks': context,
    })


def revalidate_picture(p: ResearchPicture, results: Optional[dict] = None, *,
                       mode='current', reference_as_of=None, presented_at=None, clocks=None) -> ResearchPicture:
    """One observational projection for saved JSON, Markdown and native UI."""
    if isinstance(results, dict):
        registry, gaps = build_registry(results, distill(results))
    else:
        registry, gaps = {}, []
    if not registry:
        gaps.append('Original source results unavailable; a saved evidence registry cannot validate itself.')
        if not p.validation_version:
            gaps.append('Legacy picture has no validated evidence registry.')
    # Saved/model clocks never select replay or pin current presentation.
    shown = presented_at if presented_at is not None else datetime.now(timezone.utc)
    if mode == 'historical':
        if reference_as_of is None:
            raise ValueError('Historical replay requires a trusted caller reference_as_of')
        as_of = reference_as_of
    elif mode == 'current':
        if reference_as_of is not None:
            raise ValueError('Use explicit historical mode for a historical reference clock')
        as_of = shown
    else:
        raise ValueError('Research Picture mode must be current or historical')
    verified = validate_picture(p, registry, gaps, as_of, mode=mode, presented_at=shown, clocks=clocks)
    if p.operator_focus_names:
        names = ', '.join(clean_text(name) for name in p.operator_focus_names)
        verified = verified.model_copy(update={'headline': f'Engagement focus: {names}. {verified.headline}'})
    return verified


def project_saved_picture(data: dict, *, mode='current', reference_as_of=None,
                          presented_at=None, clocks=None) -> Optional[dict]:
    results = data.get('results') or {}
    raw = results.get('research_picture')
    if not isinstance(raw, dict) or raw.get('error'):
        return None
    try:
        picture = ResearchPicture.model_validate(raw)
    except ValidationError:
        return {'headline': 'Research Picture evidence could not be validated.',
                'next_action': _VERIFY_ACTION, 'top': [], 'research_cards': [],
                'signals': [], 'watchlist': [], 'market': '',
                'gaps': ['Saved picture schema is invalid; original evidence review is required.']}
    p = revalidate_picture(picture, results, mode=mode, reference_as_of=reference_as_of,
                           presented_at=presented_at, clocks=clocks)
    return {'headline': p.headline, 'next_action': p.next_action,
            'top': [t.model_dump() for t in p.top_opportunities],
            'research_cards': [t.model_dump() for t in p.research_signals],
            'signals': p.demand_signals, 'watchlist': p.watchlist,
            'market': p.market_structure, 'gaps': p.gaps,
            'evidence_as_of': p.evidence_as_of, 'assessment_clocks': p.assessment_clocks}


def distill(results: dict) -> dict:
    """Compress the raw fan-out into a compact, model-ready digest."""
    out: dict = {}

    sam = results.get("sam.gov")
    triage = results.get("triage") or {}
    if isinstance(sam, list):
        def slim(o):
            oid = o.get("source_id") or o.get("notice_id")
            t = triage.get(oid) or {} if isinstance(triage, dict) else {}
            return {"id": oid, "title": o.get("title"), "agency": o.get("agency"),
                    "type": o.get("notice_type") or o.get("type"),
                    "deadline": o.get("response_deadline") or o.get("deadline"),
                    "triage": t.get("verdict"), "triage_reason": t.get("reason")}
        slimmed = [slim(o) for o in sam]
        out["pursue_notices"] = [o for o in slimmed if o["triage"] == "pursue"][:15]
        out["monitor_notices"] = [o for o in slimmed if o["triage"] == "monitor"][:15]
        out["notice_counts"] = {"total": len(slimmed),
                                "pursue": sum(1 for o in slimmed if o["triage"] == "pursue"),
                                "monitor": sum(1 for o in slimmed if o["triage"] == "monitor")}

    aw = results.get("contract_awards")
    if isinstance(aw, dict) and not aw.get("error"):
        out["recompetes"] = (aw.get("recompetes") or [])[:10]
        out["incumbents"] = (aw.get("incumbents") or [])[:8]

    subs = results.get("subawards")
    if isinstance(subs, dict) and not subs.get("error"):
        out["subaward_primes"] = (subs.get("primes") or [])[:8]

    news = results.get("news")
    if isinstance(news, dict):
        out["news"] = [{"title": i.get("title"), "source": i.get("source"),
                        "published": i.get("published")}
                       for i in (news.get("items") or [])[:12]]

    for key, label in (("federal_register", "federal_register_docs"),
                       ("regulations_gov", "regulations_docs")):
        docs = results.get(key)
        if isinstance(docs, dict) and not docs.get("error"):
            flat = []
            for kw, items in docs.items():
                if isinstance(items, list):
                    for d in items[:3]:
                        flat.append({"keyword": kw, "title": d.get("title"),
                                     "type": d.get("type"),
                                     "comment_open": d.get("comment_open"),
                                     "comment_ends": d.get("comment_ends"),
                                     "docket": d.get("docket")})
            out[label] = flat[:12]

    treas = results.get("treasury_fiscal")
    if isinstance(treas, dict) and treas.get("matched_lines"):
        out["agency_outlays"] = treas["matched_lines"][:8]

    gao = results.get("gao_legal")
    if isinstance(gao, dict) and gao.get("items"):
        out["legal_decisions"] = [{"title": i.get("title"), "protest": i.get("protest"),
                                   "published": i.get("published")}
                                  for i in gao["items"][:8]]

    calc = results.get("calc_rates")
    if isinstance(calc, dict) and not calc.get("error") and calc:
        out["labor_rates"] = {k: v for k, v in list(calc.items())[:5]
                              if isinstance(v, dict)}

    edgar = results.get("sec_edgar")
    if isinstance(edgar, dict) and not edgar.get("error") and edgar:
        flat = []
        for term, v in edgar.items():
            if isinstance(v, dict):
                for c in (v.get("companies") or [])[:4]:
                    flat.append({"term": term, **c})
        if flat:
            out["public_filings"] = flat[:10]

    fr = results.get("fedramp")
    if isinstance(fr, dict) and fr.get("matched"):
        out["fedramp_matches"] = fr["matched"][:8]

    fh = results.get("federal_hierarchy")
    if isinstance(fh, dict) and not fh.get("error") and fh:
        flat = []
        for agency, orgs in fh.items():
            if isinstance(orgs, list):
                for o in orgs[:4]:
                    flat.append({"agency": agency, "office": o.get("name"),
                                 "type": o.get("type")})
        if flat:
            out["buying_offices"] = flat[:12]

    for key, label, cap in (("grants_gov", "grant_programs", 8),
                            ("sbir_gov", "sbir_solicitations", 6),
                            ("gdelt", "global_news", 10)):
        data = results.get(key)
        if isinstance(data, dict) and not data.get("error"):
            flat = []
            program_rows = data.get("records")
            if isinstance(program_rows, list):
                matched_program_rows = [
                    row for row in program_rows
                    if isinstance(row, dict) and row.get("matched_terms")
                ]
                for d in matched_program_rows[:cap]:
                    if not isinstance(d, dict):
                        continue
                    projected = {
                        k: v for k, v in d.items()
                        if k in ("title", "agency", "status", "url",
                                 "domain", "seen", "program")
                    }
                    projected["close"] = d.get("close_date")
                    projected["url"] = d.get("canonical_url")
                    flat.append(projected)
            else:
                for kw, items in data.items():
                    if isinstance(items, list):
                        for d in items[:3]:
                            flat.append({"keyword": kw, **{
                                k: v for k, v in d.items()
                                if k in ("title", "agency", "status", "close",
                                         "url", "domain", "seen", "program")
                            }})
            if flat:
                out[label] = flat[:cap]

    fc = results.get("forecast_signals")
    if isinstance(fc, dict) and fc.get("matched"):
        out["forecast_signals"] = [
            {"title": m.get("title"), "component": m.get("component"),
             "anticipated": m.get("anticipated_solicitation"),
             "band": m.get("estimated_value_range"), "score": m.get("score"),
             "note": "agency-stated intent (forecast), NOT a live opportunity"}
            for m in fc["matched"][:8]]
        d = fc.get("delta") or {}
        if any(d.get(k) for k in ("new", "moved", "disappeared")):
            out["forecast_movement"] = {k: d.get(k, [])[:4]
                                        for k in ("new", "moved", "disappeared")}

    gi = results.get("govinfo")
    if isinstance(gi, dict) and not gi.get("error"):
        flat = []
        for kw, items in gi.items():
            if isinstance(items, list):
                for d in items[:3]:
                    flat.append({"keyword": kw, "title": d.get("title"),
                                 "collection": d.get("collection"), "date": d.get("date")})
        out["legislative_docs"] = flat[:12]

    kev = results.get("cisa_kev")
    if isinstance(kev, dict) and not kev.get("error"):
        out["kev"] = {"recent_count": kev.get("recent_count"),
                      "window_days": kev.get("window_days"),
                      "matched": (kev.get("matched") or [])[:5]}

    web = results.get("web")
    if isinstance(web, list):
        # Remains under web_leads so the existing client-compose quarantine
        # removes all web evidence along with these discovery records.
        out["web_leads"] = [source_record("web", f"web[{i}]", w).model_dump()
                            for i, w in enumerate(web[:10]) if isinstance(w, dict)]
    out["source_coverage_verdict"] = results.get("source_coverage_verdict")
    out["evidence_gaps"] = coverage_gaps(results)

    return out


def compose_research_picture(
    client_name: str,
    strategy_summary: str,
    results: dict,
    engine: Optional[DecisionEngine] = None,
    operator_focus: Optional[dict] = None,
) -> ResearchPicture:
    """operator_focus is the gate's search_scope (L12): recorded by the
    operator, threaded here so the synthesis ORIENTS on the engagement's
    focus. It is never a filter and never modified agent-side."""
    engine = engine or research_engine()
    sweep = distill(results)
    registry, gaps = build_registry(results, sweep)
    as_of = datetime.now(timezone.utc)
    draft = engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context={
            "client_name": client_name,
            "client_pursuit_strategy": strategy_summary,
            "as_of": as_of.isoformat(),
            "operator_focus": operator_focus or {"all": True},
            "sweep": sweep,
            "evidence_registry": {key: value.model_dump() for key, value in registry.items()},
            "evidence_gaps": gaps,
        },
        schema=ResearchPicture,
    )
    verified = validate_picture(draft.model_copy(update={"client_name": client_name, "operator_focus_names": []}), registry, gaps, as_of)
    focus = operator_focus or {}
    if not focus.get("all"):
        agencies = focus.get("agencies") or []
        names = [a.get("abbr") or a.get("name") for a in agencies if isinstance(a, dict)]
        names = [clean_text(name) for name in names if isinstance(name, str) and name]
        if names:
            verified = verified.model_copy(update={
                "headline": f'Engagement focus: {", ".join(names)}. {verified.headline}',
                "operator_focus_names": [name for name in names]})
    return verified


# Recorded source categories and counts, not procurement conclusions.
_PROVENANCE_LABELS = [
    ("notice_counts", None,
     "SAM.gov notices with structured NAICS/deadline/set-aside fields, "
     "each screened pursue/monitor/discard"),
    ("recompetes", "expiring contracts",
     "award records with completion dates; future procurement requires separate evidence"),
    ("incumbents", "ranked incumbents",
     "recipient summaries from award data; current incumbency requires verification"),
    ("subaward_primes", "teaming primes",
     "recorded subaward flows; specific procurement access remains unverified"),
    ("legislative_docs", "bills/reports/laws",
     "matched legislative records; procurement funding requires separate evidence"),
    ("federal_register_docs", "Federal Register docs",
     "recorded Federal Register documents and available comment metadata"),
    ("regulations_docs", "regulatory dockets",
     "recorded docket and comment-window metadata"),
    ("grant_programs", "grant programs",
     "assistance-side demand (posted + forecasted)"),
    ("sbir_solicitations", "SBIR/STTR topics",
     "recorded R&D topics; current actionability requires verification"),
    ("forecast_signals", "agency forecast signals",
     "stated procurement intent (P.L. 100-656); pre-RFP positioning windows, not live deals"),
    ("agency_outlays", "agency outlay lines",
     "Treasury MTS: money actually moving, not just appropriated"),
    ("legal_decisions", "GAO legal decisions",
     "protests and other recorded GAO legal decisions"),
    ("public_filings", "public-company filings",
     "SEC EDGAR: competitors' own federal-exposure disclosures"),
    ("fedramp_matches", "FedRAMP marketplace entries",
     "marketplace entries; procurement eligibility requires separate evidence"),
    ("buying_offices", "buying offices",
     "SAM Federal Hierarchy: the office level where deals actually close"),
    ("news", "trade-press items", "curated federal IT press, keyword-matched"),
    ("global_news", "global news articles", "GDELT machine-scale coverage, 14 days"),
    ("web_leads", "web leads", "plain web search (the baseline everyone has)"),
]


def render_provenance(sweep: dict) -> str:
    """Describe collected source categories without inferring an available buy."""
    lines = ["", "---", "", "## Source provenance; what this run saw",
             "*Recorded source counts describe collection, not verified opportunities or market completeness.*", ""]
    n_lines = 0
    for key, noun, why in _PROVENANCE_LABELS:
        if key == "notice_counts":
            nc = sweep.get("notice_counts") or {}
            if nc.get("total"):
                lines.append(f"- **{nc['total']} SAM.gov notices** "
                             f"({nc.get('pursue', 0)} pursue-grade after triage) "
                             f"; {why}")
                n_lines += 1
            continue
        items = sweep.get(key)
        if isinstance(items, dict):
            continue
        if items:
            lines.append(f"- **{len(items)} {noun}**; {why}")
            n_lines += 1
    kev = sweep.get("kev")
    if isinstance(kev, dict) and kev.get("recent_count"):
        lines.append(f"- **{kev['recent_count']} exploited vulns** added to CISA KEV "
                     f"in {kev.get('window_days')}d; live threat context")
        n_lines += 1
    rates = sweep.get("labor_rates")
    if isinstance(rates, dict) and rates:
        lines.append(f"- **awarded labor-rate distributions for {len(rates)} "
                     f"categories**; GSA CALC+: what the government already pays")
        n_lines += 1
    if not n_lines:
        return ""
    return "\n".join(lines)


def render_markdown(p: ResearchPicture, sweep: Optional[dict] = None,
                    *, results: Optional[dict] = None, mode='current', reference_as_of=None,
                    presented_at=None, clocks=None) -> str:
    # Rebuild every visible claim even if the persisted version marker exists.
    p = revalidate_picture(p, results, mode=mode, reference_as_of=reference_as_of,
                           presented_at=presented_at, clocks=clocks)
    lines = [
        f"# Research Picture · {clean_text(p.client_name)}",
        f"*GTM Group research synthesis; {p.assessment_clocks['mode']} assessment at {p.evidence_as_of}; presented at {p.assessment_clocks['presented_at']}*",
        "", p.headline, "",
        "*Source excerpts establish traceability. Inferences require verification; existing qualification still applies.*",
    ]
    labels = {
        'confirmed_opportunity': ('Documented notices current at historical assessment; qualification required'
                                  if p.assessment_clocks['mode'] == 'historical' else
                                  'Documented current notices; qualification required'),
        'research_signal': 'Research signals; verification required',
        'historical_market_evidence': 'Historical market evidence',
        'channel_fact': 'Channel facts; opportunity access unverified',
    }
    for kind, label in labels.items():
        items = [o for o in p.top_opportunities + p.research_signals if o.classification == kind]
        if not items:
            continue
        lines += ['', f'## {label}']
        for i, o in enumerate(items, 1):
            title = clean_text(o.title)
            link = f'[{title}](<{o.source_url}>)' if o.source_url else title
            dl = f'; recorded deadline {clean_text(o.deadline)}' if o.deadline else '; deadline unknown'
            lines += [f'{i}. **{link}** ({clean_text(o.id)}{dl})', f'   {o.why_now}']
            if o.temporal_status:
                lines.append(f"   Response window: {clean_text(o.temporal_status['response_window'])}; source freshness: {clean_text(o.temporal_status['source_freshness'])}; current action: {clean_text(o.temporal_status['current_action'])}.")
    lines += ["", "## Demand signals"]
    lines += [f"- {s}" for s in p.demand_signals]
    lines += ["", "## Market structure", p.market_structure, "", "## Watchlist"]
    lines += [f"- {w}" for w in p.watchlist]
    lines += ["", "## Next action", p.next_action]
    if p.gaps:
        lines += ["", "## Gaps (what this sweep could not answer)"]
        lines += [f"- {g}" for g in p.gaps]
    md = "\n".join(lines)
    if sweep:
        md += "\n" + render_provenance(sweep)
    return md
