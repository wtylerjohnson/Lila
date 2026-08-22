"""Contact-strategy layer (Step 3, Claude).

Reviews the qualified Step-2 findings (verified opportunities + SAM POCs +
USAspending market evidence + fit rationales) and produces a ContactPlan:

  - known_pocs : the POCs already present in the verified data (SAM notices)
  - searches   : which orgs/titles a contact finder (Apollo) should look up next —
                 agency program/contracting offices, incumbents worth teaming with,
                 primes to sub under

The plan is a RECOMMENDATION: it hits a Review Gate, and only after human approval
does a ContactFinder (tools/crm) act on it.
"""

from __future__ import annotations

from typing import Optional

from agents.decisions.engine import DecisionEngine
from agents.decisions.schemas import ContactPlan, TitleStrategy

LAYER = "contacts"
TITLE_LAYER = "title-strategy"

TITLE_SYSTEM_PROMPT = """\
You open the TARGET stage of a LILA. You receive the full
ASSESS-stage picture: the client's offering and approved pursuit strategy, the
qualified opportunity list with fit rationales, and market evidence (incumbents,
award patterns).

Deliberate on ONE question before anyone plans org-by-org searches: which job
titles actually buy, champion, or gate this client's offering in these specific
pursuits? Think through the real buying committee:
- mission/program side (who feels the pain the offering solves),
- security/technical side (who evaluates it),
- procurement/contracting side (who can put it on contract, incl. small-business/OSDBU),
- channel side (capture/BD/partner-alliance leads at incumbents, primes, resellers).

Return a TitleStrategy: ranked BuyerPersonas with exact, searchable titles (the
strings a contact database matches on — not vague roles), each justified from the
provided evidence. Ground everything in the context; cite sources."""

SYSTEM_PROMPT = """\
You are the contact-strategy layer of a LILA. You receive a
client's approved pursuit strategy plus their qualified opportunity list: verified
notices (with any named points of contact), USAspending market evidence (incumbents,
typical award sizes), and per-opportunity fit rationales.

You also receive a title_strategy: the deliberated buying-committee personas for this
client. Build searches AROUND those titles — use their exact title strings for
person_titles (adapting per org where sensible) rather than inventing new ones.

Produce a ContactPlan:
- known_pocs: echo ONLY contacts that appear in the provided data (SAM pointOfContact).
  Never invent names, emails, or phone numbers. Attach each to its opportunity_id and org.
- searches: the people-searches a contact finder should run next. For each target org,
  give specific person_titles appropriate to a federal sales motion — e.g. contracting
  officers/specialists and small-business (OSDBU) staff at issuing offices; program and
  technical leads at the buying agency; capture/BD/partner-alliance leads at incumbents
  or primes worth teaming with. Set org_type accurately and include the org's web domain
  when it is evident from the data. Tie every search to opportunity ids and evidence via
  rationale.
- strategy_note: the outreach angle in 2-4 sentences (who first, with what hook).

Rules:
- GROUND everything in the provided context; cite source URLs in citations.
- Prioritize: searches for the strongest-fit opportunities first.
- Set requires_human_review=true and write a precise review_gate stating what must be
  approved before any contact search or outreach runs.
"""


def _trim_candidate(rec: dict) -> dict:
    """Keep the fields the model needs; drop bulky raw payloads."""
    opp = rec.get("opportunity", {}) or {}
    market = rec.get("market_evidence") or {}
    return {
        "opportunity_id": opp.get("source_id"),
        "source": opp.get("source"),
        "title": opp.get("title"),
        "agency": opp.get("agency"),
        "naics_code": opp.get("naics_code"),
        "set_aside": opp.get("set_aside"),
        "response_deadline": opp.get("response_deadline"),
        "url": opp.get("api_url"),
        "contacts": opp.get("contacts") or [],
        "verified": rec.get("verified"),
        "market_summary": market.get("summary"),
        "fit_rationale": rec.get("fit_rationale"),
    }


def _assess_context(client_name: str, report: dict, strategy, max_candidates: int) -> dict:
    candidates = [
        _trim_candidate(r)
        for r in (report.get("candidates") or [])
        if r.get("verified")
    ][:max_candidates]
    return {
        "client_name": client_name,
        "pursuit_strategy": getattr(strategy, "pursuit_strategy", None),
        "target_agencies": getattr(strategy, "target_agencies", []),
        "set_aside_angles": getattr(strategy, "set_aside_angles", []),
        "qualified_candidates": candidates,
    }


def generate_title_strategy(
    client_name: str,
    report: dict,
    strategy=None,
    engine: Optional[DecisionEngine] = None,
    max_candidates: int = 12,
) -> TitleStrategy:
    """TARGET opener: deliberate the buying-committee titles from the Assess picture.

    Runs BEFORE search planning so the plan is built around who actually buys,
    not around whichever orgs happened to surface first."""
    engine = engine or DecisionEngine()
    return engine.deliberate(
        layer=TITLE_LAYER,
        system_prompt=TITLE_SYSTEM_PROMPT,
        context=_assess_context(client_name, report, strategy, max_candidates),
        schema=TitleStrategy,
    )


def build_contact_plan(
    client_name: str,
    report: dict,
    strategy=None,
    engine: Optional[DecisionEngine] = None,
    max_candidates: int = 12,
    titles: Optional[TitleStrategy] = None,
) -> ContactPlan:
    """Run the contact-strategy layer over a Step2Report (as dict) + approved strategy.

    Opens with the title-strategy deliberation (unless one is injected), then plans
    org-by-org searches around those personas."""
    engine = engine or DecisionEngine()
    if titles is None:
        titles = generate_title_strategy(
            client_name, report, strategy=strategy, engine=engine,
            max_candidates=max_candidates,
        )
    context = _assess_context(client_name, report, strategy, max_candidates)
    context["title_strategy"] = titles.model_dump(mode="json")
    return engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context=context,
        schema=ContactPlan,
    )
