"""Step 1b — Company research: website scrape + general web search, in parallel.

Two workers run simultaneously (they answer different questions):

  SITE  — locate the company's official website (Claude web_search, only if the
          intake form omitted it) and scrape it (tools/scrape/site.py).
  WEB   — general web search on the company itself: news, contract awards,
          certifications, partnerships, federal footprint.

Both results feed the profiling layer (agents/decisions/intake.py) as grounded
context. Failures are soft: an unreachable site or an empty search never blocks
intake — the strategy is simply built from whatever evidence was gathered.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel, Field

from agents.decisions.engine import DecisionEngine
from tools.scrape.site import ScrapeBundle, scrape_site

_FIND_SITE_SYSTEM = """\
You are locating the OFFICIAL website of a specific company. Use web search. Prefer
the company's own domain over directories (LinkedIn, Crunchbase, SAM.gov listings,
Bloomberg). Return the homepage URL and say how confident you are and why. If you
cannot find an official site, say so explicitly.
"""

_STRUCTURE_SITE_SYSTEM = """\
Extract the single best official-website candidate from the findings. url must be a
real URL from the findings (homepage preferred) or null if none was found.
confidence is 0..1. Do not invent a URL.
"""

_COMPANY_WEB_SYSTEM = """\
You are researching a specific company as a federal-sales analyst. Using web search,
find PUBLIC, current information about THIS company: what they do, notable customers,
contract awards or federal/SLED footprint (SAM registration, GSA schedules, prior
awards), certifications and set-asides (8(a), SDVOSB, WOSB, HUBZone, ISO, CMMC),
partnerships/teaming, reseller/distributor channels into government, the competitive
landscape (the NAMED rival vendors and products buyers evaluate against this company,
from analyst comparisons, reviews, or market reporting), size and locations, recent
news. Prefer primary sources. Cite real URLs — do not fabricate. If two companies
share the name, note the ambiguity.
"""


class WebsiteGuess(BaseModel):
    url: Optional[str] = None
    confidence: float = 0.0
    rationale: str = ""


class CompanyResearch(BaseModel):
    """Everything Step 1b learned about the company, with provenance."""

    company_name: str
    website: Optional[str] = None
    website_source: str = Field(
        default="form", description="'form' | 'web_search' | 'not_found'"
    )
    scrape: Optional[ScrapeBundle] = None
    web_findings: str = ""
    web_citations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list, description="soft failures, for the log")

    def context(self) -> dict:
        """Shape handed to the profiling layer (compact, citable).

        `web_research_cited` and `errors` ride along (truth purge,
        2026-08-03) so the profiling layer can tell a researched-empty rival
        market from missing evidence and say so at the review gate."""
        return {
            "company_name": self.company_name,
            "website": self.website,
            "website_source": self.website_source,
            "web_findings": self.web_findings,
            "web_citations": self.web_citations,
            "web_research_cited": bool(self.web_citations),
            "errors": list(self.errors),
        }


def find_website(company_name: str, engine: DecisionEngine) -> WebsiteGuess:
    """Locate the official site via Claude web_search; never raises past a guess."""
    findings, _ = engine.web_research(
        system_prompt=_FIND_SITE_SYSTEM,
        query=f"Find the official website of the company: {company_name}",
        max_uses=3,
    )
    if not findings:
        return WebsiteGuess(rationale="web search returned nothing")
    return engine.structure(
        instructions=_STRUCTURE_SITE_SYSTEM, findings=findings, schema=WebsiteGuess
    )


def _log(msg: str) -> None:
    """Progress heartbeat. Streams into the job console so long calls never look hung."""
    import sys

    print(f"     {msg}", file=sys.stderr, flush=True)


def _site_worker(
    research: CompanyResearch, engine: DecisionEngine, max_pages: int
) -> None:
    try:
        if not research.website:
            _log("[site] locating official website (Claude web search) ...")
            guess = find_website(research.company_name, engine)
            if guess.url:
                research.website = guess.url
                research.website_source = "web_search"
                _log(f"[site] found {guess.url}")
            else:
                research.website_source = "not_found"
                _log("[site] no official site found; proceeding without scrape")
                return
        _log(f"[site] scraping {research.website} ...")
        research.scrape = scrape_site(research.website, max_pages=max_pages)
        _log(f"[site] done: {len(research.scrape.pages)} page(s) read")
    except Exception as exc:  # noqa: BLE001 — research is best-effort, never blocks intake
        research.errors.append(f"site: {exc}")
        _log(f"[site] failed (continuing): {exc}")


def _web_worker(research: CompanyResearch, engine: DecisionEngine) -> None:
    query = (
        f"Research the company '{research.company_name}'"
        + (f" (website: {research.website})" if research.website else "")
        + ". Focus on federal contracting relevance, including named "
          "competitors and reseller/channel partners into government."
    )
    try:
        _log("[web] researching the company (Claude web search, can take a few minutes) ...")
        findings, citations = engine.web_research(
            system_prompt=_COMPANY_WEB_SYSTEM, query=query)
        # TRUTH PURGE (operator filing, 2026-08-03). The apexanalytix intake
        # shipped product-only entities because this worker returned zero
        # cited findings and the flow read that as success: '[web] done:
        # 0 citation(s)' in the job log, no error, no gap disclosed, and the
        # review gate saw a product-only packet with nothing saying the
        # rival side was MISSING EVIDENCE rather than a researched empty.
        # An uncited result is a failed research attempt: retry once, and if
        # still uncited, record the error so intake and the gate see it.
        if not str(findings or "").strip() or not citations:
            _log("[web] result carried no cited findings; retrying once ...")
            findings, citations = engine.web_research(
                system_prompt=_COMPANY_WEB_SYSTEM, query=query)
        research.web_findings = findings
        research.web_citations = citations
        if not str(findings or "").strip() or not citations:
            research.errors.append(
                "web: research returned no cited findings after a retry; "
                "competitor/reseller evidence is MISSING, not researched-empty")
            _log("[web] FAILED to produce cited findings after a retry; the "
                 "rival/channel side is missing evidence, not empty")
        else:
            _log(f"[web] done: {len(citations)} citation(s)")
    except Exception as exc:  # noqa: BLE001
        research.errors.append(f"web: {exc}")
        _log(f"[web] failed (continuing): {exc}")


def research_company(
    company_name: str,
    website: Optional[str] = None,
    engine: Optional[DecisionEngine] = None,
    max_pages: int = 5,
    do_scrape: bool = True,
    do_web: bool = True,
) -> CompanyResearch:
    """Run the SITE and WEB workers in parallel and return the merged bundle."""
    research = CompanyResearch(
        company_name=company_name,
        website=website,
        website_source="form" if website else "not_found",
    )
    if not (do_scrape or do_web):
        return research
    from agents.decisions.engine import research_engine

    engine = engine or research_engine()  # fast model: research, not deliberation

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = []
        if do_scrape:
            futures.append(pool.submit(_site_worker, research, engine, max_pages))
        if do_web:
            futures.append(pool.submit(_web_worker, research, engine))
        for f in futures:
            f.result()  # workers trap their own errors; this just joins
    return research
