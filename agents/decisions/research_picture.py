"""Research Picture: Claude's synthesis pass over ALL search inputs.

The reason this system beats a plain Claude Max search: a web search gives
prose; the fan-out gives structured ground truth (verified notices, contract
expirations, subaward flows, dockets, exploited-vuln velocity). This layer is
where that becomes an advantage; Claude Max reads every source's output
TOGETHER and writes the research picture a human strategist would: what's hot,
what the demand signals say, how the market is structured, what to watch, and
the single next action.

Every search run produces this as a standing artifact:
    data/review/<slug>.research_picture.md   (+ embedded in the searches JSON)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from agents.decisions.engine import DecisionEngine, research_engine

LAYER = "research-picture"

SYSTEM_PROMPT = """\
You are the synthesis layer of a LILA. You receive the
DISTILLED output of a parallel sweep across many sources: SAM.gov notices with
triage verdicts, expiring contracts (recompetes) with incumbents, subaward prime
flows, federal news, Federal Register and Regulations.gov documents, and CISA
KEV velocity; all for one client's offering.

Write the research picture a senior capture strategist would after reading all
of it side by side. Connect ACROSS sources: a pursue-grade notice plus a
matching recompete plus a rule in comment period is a story, not three rows.
Ground every claim in the provided data; reference notice ids, incumbent
names, docket ids. Never invent. If the data is thin somewhere, say so in gaps
rather than papering over it.

If operator_focus names agencies, the OPERATOR has focused this engagement
there at the gate: open the headline by naming the focus ("Engagement focus:
DHS"), lead the ranking and the demand story with evidence at those agencies
and their components, and only then widen to the rest of the market. The focus
is emphasis, never a filter — report the whole market and never discard or
zero-weight off-focus evidence. If operator_focus is all-agencies, say nothing
about focus."""


class TopOpportunity(BaseModel):
    id: str = Field(description="source_id of the notice, echoed exactly")
    title: str
    why_now: str = Field(description="1-2 sentences; cross-reference other sources where they connect")
    deadline: Optional[str] = None


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
        out["web_leads"] = [{"title": w.get("title"), "url": w.get("url") or w.get("api_url")}
                            for w in web[:10]]
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
    return engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context={
            "client_name": client_name,
            "client_pursuit_strategy": strategy_summary,
            "as_of": date.today().isoformat(),
            "operator_focus": operator_focus or {"all": True},
            "sweep": distill(results),
        },
        schema=ResearchPicture,
    )


# What each source contributes that a plain web search cannot. Deterministic ;
# computed from the sweep, never claimed by the model.
_PROVENANCE_LABELS = [
    ("notice_counts", None,
     "SAM.gov notices with structured NAICS/deadline/set-aside fields, "
     "each screened pursue/monitor/discard"),
    ("recompetes", "expiring contracts",
     "award records with completion dates; recompete timing invisible to web search"),
    ("incumbents", "ranked incumbents",
     "who holds the ground today, from award data"),
    ("subaward_primes", "teaming primes",
     "who pushes this category of work to subs (USAspending flows)"),
    ("legislative_docs", "bills/reports/laws",
     "full-text legislative hits; funding proof with citations"),
    ("federal_register_docs", "Federal Register docs",
     "program formation 6-18 months pre-RFP"),
    ("regulations_docs", "regulatory dockets",
     "comment windows = lawful early engagement channels"),
    ("grant_programs", "grant programs",
     "assistance-side demand (posted + forecasted)"),
    ("sbir_solicitations", "SBIR/STTR topics",
     "R&D-stage demand forming 1-3 years out"),
    ("forecast_signals", "agency forecast signals",
     "stated procurement intent (P.L. 100-656); pre-RFP positioning windows, not live deals"),
    ("agency_outlays", "agency outlay lines",
     "Treasury MTS: money actually moving, not just appropriated"),
    ("legal_decisions", "GAO legal decisions",
     "bid protests = contested awards and incumbency weakness"),
    ("public_filings", "public-company filings",
     "SEC EDGAR: competitors' own federal-exposure disclosures"),
    ("fedramp_matches", "FedRAMP marketplace entries",
     "cloud authorization status = who can sell today"),
    ("buying_offices", "buying offices",
     "SAM Federal Hierarchy: the office level where deals actually close"),
    ("news", "trade-press items", "curated federal IT press, keyword-matched"),
    ("global_news", "global news articles", "GDELT machine-scale coverage, 14 days"),
    ("web_leads", "web leads", "plain web search (the baseline everyone has)"),
]


def render_provenance(sweep: dict) -> str:
    """The 'why this beats a bare Claude search' section, from counts, not claims."""
    lines = ["", "---", "", "## Source provenance; what this run saw",
             "*A plain web search sees only the open web. This picture was "
             "synthesized from structured government data a web search cannot "
             "query:*", ""]
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


def render_markdown(p: ResearchPicture, sweep: Optional[dict] = None) -> str:
    lines = [
        f"# Research Picture · {p.client_name}",
        f"*GTM Group synthesis from the full source sweep, {date.today().isoformat()}*",
        "",
        f"**{p.headline}**",
        "",
        "## Top opportunities",
    ]
    for i, o in enumerate(p.top_opportunities, 1):
        dl = f"; due {o.deadline}" if o.deadline else ""
        lines.append(f"{i}. **{o.title}** (`{o.id}`{dl})")
        lines.append(f"   {o.why_now}")
    lines += ["", "## Demand signals"]
    lines += [f"- {s}" for s in p.demand_signals]
    lines += ["", "## Market structure", p.market_structure, "", "## Watchlist"]
    lines += [f"- {w}" for w in p.watchlist]
    lines += ["", "## Next action", f"**{p.next_action}**"]
    if p.gaps:
        lines += ["", "## Gaps (what this sweep could not answer)"]
        lines += [f"- {g}" for g in p.gaps]
    md = "\n".join(lines)
    if sweep:
        md += "\n" + render_provenance(sweep)
    return md
