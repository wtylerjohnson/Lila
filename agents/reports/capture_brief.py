"""Capture brief — the client-approved GTM Group HTML report (locked Jul 2 2026).

Two halves, deliberately separated:
  - compose_capture_brief(): the LLM (via DecisionEngine, Max-plan routed) fills a
    strict Pydantic schema from the FactPack. Language rules are in the system
    prompt; every URL must come from a fact source.
  - render_capture_brief(): deterministic assembly into the approved HTML using
    the extracted stylesheet (templates/capture_brief.css) and the exact
    component markup. Charts are computed from the data, so they can't disagree
    with the text. Vehicles section renders from data/reference/vehicles.json.

The format is LOCKED — the client approved this exact design. Content varies;
the container does not.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.decisions.engine import DecisionEngine
from agents.reports.facts import FactPack
from tools.vehicles import load_vehicles

_TPL = Path(__file__).resolve().parent / "templates"
_GTM_LOGO = os.path.expanduser("~/Desktop/Desktop/GTMLogo-sm-154w.webp")

FitChip = Literal["DIRECT FIT", "STRONG FIT", "TEAMING PLAY", "R&D LANE"]
_CHIP_CLASS = {
    "DIRECT FIT": "fit-direct", "STRONG FIT": "fit-strong",
    "TEAMING PLAY": "fit-adjacent", "R&D LANE": "fit-strong",
}


class CBOpportunity(BaseModel):
    headline: str
    agency_abbr: str = Field(description="short badge text, e.g. 'DOS', 'USSF'")
    agency_name: str
    fit: FitChip
    notice_id: str
    url: str = Field(description="SAM.gov notice URL from a cited fact")
    due_label: str = Field(description="e.g. 'Jul 14, 2026, 11:00 a.m. ET'")
    days_to_due: int = Field(ge=0, description="days from report date; 0+ only")
    body: str = Field(description="the opportunity in four beats, present-forward: "
                      "WHY NOW (the timing trigger), ENTRY ROUTE (teaming door / "
                      "vehicle / SBIR / direct bid), NEXT MOVE (the concrete next "
                      "action). The SOURCE is the cited fact (notice_id + url). No "
                      "audit language, no 'screened out', no client homework")
    urgent: bool = False


class CBNewsItem(BaseModel):
    recency: str = Field(description="'This week' / 'Last week' / '2 weeks ago' — never a past date")
    headline: str
    url: str
    why: str


class CBStat(BaseModel):
    number: str
    accent: str = ""
    context: str


class CBBudgetBar(BaseModel):
    label: str = Field(description="e.g. 'SEWP VI ceiling', 'DOD cyberspace FY26'")
    amount_label: str = Field(description="e.g. '$60B · 10 YRS'")
    relative: float = Field(gt=0, le=1, description="bar length vs the largest figure (largest = 1.0)")


class CaptureBriefContent(BaseModel):
    client_name: str
    subtitle: str
    meta_prepared_for: str
    thesis: list[str] = Field(min_length=3, max_length=3)
    stats: list[CBStat] = Field(min_length=5, max_length=5)
    action_callout: str = Field(description="the nearest-gate action, one sentence, with date")
    budget_bars: list[CBBudgetBar] = Field(
        default_factory=list,
        description="3-5 verified budget figures for the money-in-motion chart; only cited dollar facts")
    opportunities: list[CBOpportunity] = Field(
        default_factory=list,
        description="live pursue-grade opportunities; [] when NO notice is "
                    "pursue-grade this cycle — NEVER invent one to fill the "
                    "section. A zero-opportunity assessment is a valid, honest "
                    "outcome that stands on the market thesis and the watchlist")
    kill_line_opps: str
    news_funding: list[CBNewsItem]
    news_threat: list[CBNewsItem]
    news_agency: list[CBNewsItem]
    news_market: list[CBNewsItem]
    kill_line_news: str
    partner_callout: str = Field(description="'drive the [partner] motion' action framing")
    pipeline: list[CBOpportunity] = Field(default_factory=list, description="forming programs; days_to_due=0, due_label=phase language")
    footer_verification: str


_SYSTEM = """You compose GTM Group Federal Opportunity Assessments. Rules, non-negotiable:
- The deliverable's client-facing name is "Federal Opportunity Assessment"; in copy refer to it as "this assessment" / "the assessment", never any other name.
- NEVER write a literal number for a count of pursuits, notices, opportunities, competitors, agencies, dossiers, or watchlist items. Write {{COUNT:name}} (digits) or {{COUNT_WORD:name}} (spelled out) instead; available names: pursuits, pursue_notices, monitor_notices, competitors, agencies, dossiers, watchlist_entries, news_items, teaming_plays, teaming_watch. The renderer substitutes the ground-truth value; a freewritten count fails the build. COUNT tokens are PIPELINE tallies only: a quantity stated inside a cited fact (a pool of 8 primes, 16 subawards, a rank denominator) is a FACT figure — write the literal number WITH its [F#] citation, never a COUNT token. The tokens resolve to pipeline tallies; a mis-named token renders a false zero beside a cited dollar figure and fails the build. Token semantics under AGENCY FOCUS: 'agencies' counts rows of the market table, and in an agency-focused assessment that is 1 (the focus agency itself). Mission components (CBP, TSA, USCIS, a service branch) are NEVER 'agencies' and have NO token: a component or incumbent-seat tally is a FACT figure from the cited buyer-map facts — write the literal number with its [F#] citations.
- Ground every claim in the provided facts; every URL must come from a fact's source field. Never invent notice IDs, dates, or dollar figures.
- If NO notice is pursue-grade this cycle, return opportunities: []; NEVER invent a live opportunity or a deadline. BUILD THE ASSESSMENT AROUND what IS real and forward: pipeline MAY carry monitor-grade adjacencies and capability-matched forecast lines as forming/teaming windows, PROVIDED every entry uses a real notice id and URL from the cited facts, phase or window language for timing, and TEAMING PLAY / R&D LANE fit framing (never DIRECT FIT for an adjacency). The zero is NEVER the story. This assessment sells the engagement as well as the intelligence: LEAD with what the sweep DID find, one layer down; forming windows (the developing horizon), teaming paths with primes named from award evidence, subcontract conduits, monitor-grade adjacencies, the recompete calendar, the addressable spend. The thesis opens on where effort goes next; the marquee stats showcase the found layers. The clean screen is NEVER stated in the client deliverable, by you or the renderer: no screen credential, no census reconciliation, no headline about it, no apology. Your thesis and stats carry the opportunity set and the forward motion only.
- All dates read present-forward: deadlines and windows only. Posted dates become 'New this week'; news timing uses recency labels; program history uses phase language ('prototype phase complete'). Policy and budget signals (memos, BODs, appropriations, GAO findings) carry STATUS framing, never bare dates: 'in force since May 2026', 'enacted for FY26', 'standing gap since 2023'. Never render a signals table with a bare Date column; a reader must never mistake a signal timeline for a solicitation list.
- Client-facing enthusiasm with analyst honesty: reframe hedges as motion ('teaming play', 'the production follow-on is the prize ahead'); the footer carries the verification trail, the body carries momentum.
- NEVER tell the client what they already know: their own contracts, award history, certifications, or record must not be restated as findings. The assessment surfaces only what they DON'T know; buyer behavior, forming windows, competitor and teaming motion, market structure.
- CAPABILITY EVIDENCE IS PRIMARY (permanent): prefer facts marked CAPABILITY-VERIFIED or INCUMBENT-PRODUCT SIGNAL for teaming, recompete, and buyer-behavior claims. NAICS lane totals are BOUNDARY CONTEXT: at most one context sentence in the whole document, never a stat card, budget bar, addressable-market claim, or thesis support.
- CLIENT-FILE DOCTRINE (2026-07-10, permanent): the client file contains only VERIFIED claims and FORWARD motion. Nothing that grades our own prior work appears in it: a finding that did not verify gets no chip, no row, no caveat, no footnote; it is OMITTED (the internal trail carries it automatically). Facts labeled 'lane-level evidence only' or 'FINDING: zero' are INTERNAL: never cite them, never restate them, never caveat around them. A negative is never rendered as a negative: delete it, or convert it to a forward action (no record on four notices becomes 'stand up a live watch on those postings', never 'no record found'). Banned in client copy: 'unverified', 'not verified', 'did not verify', 'no record', 'not capability-verified', 'pending verification', 'zero matched', 'could not confirm'.
- SUPERLATIVES (largest, biggest, #1, top, leads): copy them ONLY from a fact that states the rank explicitly; never infer one by comparing facts yourself. PRIORITY is not RANK: when an entity is your recommended focus but a fact ranks it below #1, say 'the priority teaming target' or 'the highest-evidence door' and state the real rank ('#2 of 8 by subaward dollars'); never place 'top/leads' next to its dollar figure. If two facts carry a LINEAGE NOTE naming one corporate door, never present those entities as separate targets, and repeat the note wherever either is named.
- OPPORTUNITY-SET DOCTRINE (hard rule): this deliverable is an opportunity SET, never an audit. Present ONLY the strongest opportunities; weak or rejected items are OMITTED silently, never as 'screened out', a rejection count, a census reconciliation, or a 'held closed' line. There is NO clean-screen credential, NO 'screened N of M', NO QA-appendix reference, and NO client homework ('verify', 'confirm', 'pending verification', 'you should') anywhere in your copy: the audit trail lives in the internal view, never here. Each opportunity carries four beats: WHY NOW (the timing trigger), ENTRY ROUTE (how they get in: teaming door, vehicle, SBIR, direct bid), NEXT MOVE (the concrete next action), and SOURCE (the [F#] citation / URL).
- PUNCTUATION: never use an em dash anywhere; use commas, colons, periods, or middots instead.
- High signal, zero padding. Kill-lines are one sentence, editorial, confident."""


# ── Full-picture review: the reasoning weight lands HERE, at the final product.
# Before the brief is composed, one deliberation reads EVERYTHING the pipeline
# produced — screened notices, research picture, pursuit dossiers, market
# structure — and writes the directive the composer must follow. The brief is
# the conclusion of all prior work, not another summary of the fact pack.

_REVIEW_SYSTEM = """\
You are the final reviewer of a LILA, writing the
directive for the client-facing Federal Opportunity Assessment; the LAST document, the
conclusion of every stage before it. You receive everything the pipeline
gathered: the approved strategy, screened notices with verdicts,
deep pursuit dossiers, and market data. Web-search synthesis (the research
picture and general-web leads) is INTERNAL-ONLY and deliberately absent from
your inputs: web-search assertions never reach a client artifact.

Your job is judgment, not summary: name the thesis the whole body of evidence
supports, the cross-stage connections that must not be lost (a dossier's
incumbent signal + an expiring contract + a docket window is ONE story), the
facts the assessment must include with their source ids, and what must NOT be
claimed because the evidence is thin. Be specific and ruthless; this
directive is what makes the final product smarter than any single stage.

When ZERO notices are pursue-grade, your directive must point the emphasis one
layer down; the forming windows, teaming paths, sub conduits, and adjacencies
the evidence supports. The clean screen is a one-line credential in your
directive, never its lead: a reader finishing the assessment should know where
effort goes next, not what wasn't there.

Any client_footprint you receive is INTERNAL: the client's own record. Let it
sharpen your judgment; which buyers are proven, which patterns their history
validates, where emphasis belongs; but your directive must never instruct the
composer to recount it. We never tell the client what they already know; their
footprint stays in our own logic and improves what we tell them that they
don't.

Fact discipline binds your directive too: state ranks, dollars, and dates
exactly as the facts carry them. SUPERLATIVES (largest, top, leads, #1) may be
copied ONLY from a fact that states the rank explicitly. PRIORITY is not RANK:
when your emphasis puts an entity first but a fact ranks it #2 of 8, write
'priority teaming target, #2 of 8 by subaward dollars', never 'top'. A
must_include that misstates a fact poisons every compose downstream; the
arbiter panel will fail the draft on your words (the 2026-07-09 Osprey review
called the #2 subaward prime 'top' and cost the build two audit rounds).

The fact_pack in your context is the composer's ONLY citable universe. Anchor
every must_include and every emphasis item to its fact id (F#). If a sweep
item you want emphasized carries NO fact id, it does not go in the directive:
a directive pushing an uncitable item forces the composer to invent, and the
panel kills the draft (2026-07-09 Osprey: the review pushed Project Pioneer,
OASIS+, and a DHS sole-source cluster before the pack carried facts for them
— three straight failed builds)."""


class FullPictureReview(BaseModel):
    thesis_directive: str = Field(
        description="2-3 sentences: the story the whole evidence base supports")
    storyline_connections: list[str] = Field(
        description="cross-stage connections the assessment must carry, each citing its pieces")
    must_include: list[str] = Field(
        description="facts/opportunities that MUST appear, with source ids")
    do_not_claim: list[str] = Field(default_factory=list,
                                    description="tempting claims the evidence does not support")
    emphasis_ranking: list[str] = Field(
        description="opportunity ids/names in the order they deserve weight")


# TRUTH PURGE (operator filing, 2026-08-03): web-search assertions must not
# reach any client artifact. QUARANTINED, not deleted: the research picture
# stays operator-visible (data/review/<slug>.research_picture.md, the Control
# Room picture view, the internal sidecar trail) but no longer feeds the
# compose chain, so uncited web-search claims cannot shape client copy. The
# review weighs the F#-anchored fact pack plus structured stage outputs only.
# The env flag re-admits it for an explicit operator experiment; default OFF.
PICTURE_TO_COMPOSE_ENV = "LILA_COMPOSE_SEES_PICTURE"

# The web-search-derived members of the compose `everything` bundle. The
# research picture is synthesized over web/news lanes; web_leads are the raw
# general-web rows inside the distilled sweep.
_WEB_ASSERTION_KEYS = ("research_picture",)
_WEB_ASSERTION_SWEEP_KEYS = ("web_leads",)


def quarantine_web_assertions(everything: dict) -> tuple[dict, list[str]]:
    """(cleaned, removed): strip web-search assertion surfaces from the
    compose bundle unless the operator explicitly re-admits them."""
    flag = os.environ.get(PICTURE_TO_COMPOSE_ENV, "").strip().casefold()
    if flag in {"1", "true", "on", "yes"}:
        return everything, []
    removed: list[str] = []
    cleaned = dict(everything)
    for key in _WEB_ASSERTION_KEYS:
        if key in cleaned:
            cleaned.pop(key)
            removed.append(key)
    sweep = cleaned.get("source_sweep")
    if isinstance(sweep, dict):
        for key in _WEB_ASSERTION_SWEEP_KEYS:
            if key in sweep:
                sweep = dict(sweep)
                sweep.pop(key)
                cleaned["source_sweep"] = sweep
                removed.append(f"source_sweep.{key}")
    return cleaned, removed


def compose_full_picture(
    client_name: str,
    everything: dict,
    engine: Optional[DecisionEngine] = None,
) -> FullPictureReview:
    from agents.decisions.engine import research_engine
    engine = engine or research_engine()  # Sonnet: fast, and the directive is bounded
    cleaned, _removed = quarantine_web_assertions(everything)
    return engine.deliberate(
        layer="capture-brief-review",
        system_prompt=_REVIEW_SYSTEM,
        context={"client_name": client_name, **cleaned},
        schema=FullPictureReview,
    )


def compose_capture_brief(pack: FactPack, engine: Optional[DecisionEngine] = None,
                          review: Optional[FullPictureReview] = None,
                          directive: Optional[str] = None) -> CaptureBriefContent:
    engine = engine or DecisionEngine()
    context: dict = pack.model_dump(mode="json")
    if review is not None:
        context = {
            "fact_pack": context,
            "full_picture_review": review.model_dump(mode="json"),
            "instruction": ("The full_picture_review is the synthesis of every prior "
                            "pipeline stage. Follow its thesis, connections, "
                            "must-includes and emphasis ranking; never contradict "
                            "its do_not_claim list."),
        }
    if directive:  # a QA/scope/arbiter directive is operator-side law. It is
        # PREPENDED and declared to win: the 2026-07-09 Osprey fail appended the
        # arbiter fix list after "follow the review", and the composer sided
        # with the review's GDIT-first emphasis over the fact's #2-of-8 rank
        # through two audits. The review ranks priorities; it never overrides
        # a fact's stated rank or a fix list.
        if "instruction" in context:
            context["instruction"] = (
                directive + " The directive above is a hard constraint from "
                "the QA layer: where it contradicts the full_picture_review, "
                "the directive wins. " + context["instruction"])
        else:
            context = {"fact_pack": context, "instruction": directive}
    return engine.deliberate(
        layer="capture-brief",
        system_prompt=_SYSTEM,
        context=context,
        schema=CaptureBriefContent,
    )


def cached_full_picture_review(
        client: str, *, scope: str, everything: dict,
        engine: Optional[DecisionEngine] = None,
        strict_identity: Optional[str] = None):
    """(review, cache_hit). The full-picture review is itself an LLM output;
    left uncached it varies per press and busts every section key downstream
    (the Press B lesson, 2026-07-11). Keyed on its exact inputs, an unchanged
    evidence bundle reuses the review AND therefore the sections.

    strict_identity (Cycle 4, 2026-07-12): the current strict run token. The
    review is the coherence anchor every section follows, so a strict-truth
    flip must re-price it even when the sweep-derived bundle is unchanged.
    None omits the key: legacy digests stay byte-identical."""
    from agents.reports.compose_cache import (
        cached_call, canonical_digest, section_key,
    )
    review_basis: dict = {"everything": everything,
                          "system": _REVIEW_SYSTEM}
    if strict_identity:
        review_basis["strict_identity"] = strict_identity
    digest = canonical_digest(review_basis)
    key = section_key(client=client, scope=scope, section="review",
                      inputs_digest=digest)
    return cached_call(
        key=key, validator=FullPictureReview.model_validate,
        producer=lambda: compose_full_picture(client, everything, engine),
        meta={"section": "review"})


# ── sectioned compose (P2, 2026-07-11) ──────────────────────────────────────
# One monolithic deliberate re-paid the whole document on every press. The
# split composes three sections against the SAME review (the coherence
# anchor), each cached on a canonical digest of exactly its inputs — so an
# unchanged section is a disk hit, and a directive-driven retry re-pays only
# the section it targets. Fact ids are preserved in every subset, so [F#]
# citations stay valid against the full pack at render time.

_SECTION_FIELDS: dict = {
    "narrative": ("client_name", "subtitle", "meta_prepared_for", "thesis",
                  "stats", "action_callout", "budget_bars", "kill_line_opps",
                  "kill_line_news", "footer_verification"),
    "news": ("news_funding", "news_threat", "news_agency", "news_market"),
    "boards": ("opportunities", "pipeline", "partner_callout"),
}

_SECTION_INSTRUCTION = (
    "Compose ONLY these fields of the assessment: {fields}. The remaining "
    "sections are composed separately against the same full_picture_review; "
    "follow the review so the document reads as one voice, and never "
    "reference content you are not composing.")

_SECTION_MODELS: dict = {}


def _section_model(section: str):
    """Pydantic model for one section, built FROM CaptureBriefContent's own
    field definitions so constraints can never drift from the real schema."""
    if section not in _SECTION_MODELS:
        from pydantic import create_model
        fields = {
            name: (CaptureBriefContent.model_fields[name].annotation,
                   CaptureBriefContent.model_fields[name])
            for name in _SECTION_FIELDS[section]
        }
        _SECTION_MODELS[section] = create_model(
            f"CaptureBrief{section.title()}Section", **fields)
    return _SECTION_MODELS[section]


_NEWS_KINDS = frozenset({"market", "context"})
_BOARD_KINDS = frozenset({"opportunity", "competitor"})


def _subset_pack(pack: FactPack, section: str) -> FactPack:
    """Facts relevant to one section, ids PRESERVED (never renumbered).
    narrative anchors on the full pack; news and boards get their slices
    plus every id the pack's own views mark as theirs."""
    if section == "narrative":
        return pack
    if section == "news":
        keep_kinds, keep_ids = _NEWS_KINDS, set(pack.market_fact_ids)
    else:
        keep_kinds = _BOARD_KINDS
        keep_ids = set(pack.opportunity_fact_ids) | set(pack.monitor_fact_ids) \
            | set(pack.competitor_fact_ids) | set(pack.incumbent_product_fact_ids)
    facts = [f for f in pack.facts if f.kind in keep_kinds or f.id in keep_ids]
    kept = {f.id for f in facts}
    return FactPack(
        client_name=pack.client_name, as_of=pack.as_of, facts=facts,
        market_fact_ids=[i for i in pack.market_fact_ids if i in kept],
        competitor_fact_ids=[i for i in pack.competitor_fact_ids if i in kept],
        opportunity_fact_ids=[i for i in pack.opportunity_fact_ids if i in kept],
        monitor_fact_ids=[i for i in pack.monitor_fact_ids if i in kept],
        incumbent_product_fact_ids=[i for i in pack.incumbent_product_fact_ids
                                    if i in kept],
        warnings=list(pack.warnings),
    )


def compose_capture_brief_sectioned(
        pack: FactPack, engine: Optional[DecisionEngine] = None,
        review: Optional[FullPictureReview] = None,
        directive: Optional[str] = None, *,
        client: str, scope: str = "all",
        use_cache: bool = True,
        strict_identity: Optional[str] = None) -> tuple[CaptureBriefContent, dict]:
    """(assembled content, {section: cache_hit}). Same rules, same review,
    three schema-enforced calls instead of one; unchanged inputs never
    re-pay their section.

    strict_identity (Cycle 4, 2026-07-12): current strict run token folded
    into every section digest, so prose priced under one evidence truth is
    never reused under another. None omits the key (legacy digests stay
    byte-identical)."""
    from agents.reports.compose_cache import (
        cached_call, canonical_digest, section_key,
    )
    engine = engine or DecisionEngine()
    assembled: dict = {}
    hits: dict = {}
    for section in ("narrative", "news", "boards"):
        model = _section_model(section)
        sub = _subset_pack(pack, section)
        instruction = _SECTION_INSTRUCTION.format(
            fields=", ".join(_SECTION_FIELDS[section]))
        context: dict = {"fact_pack": sub.model_dump(mode="json"),
                         "instruction": instruction}
        if review is not None:
            context["full_picture_review"] = review.model_dump(mode="json")
            context["instruction"] = (
                instruction + " The full_picture_review is the synthesis of "
                "every prior pipeline stage. Follow its thesis, connections, "
                "must-includes and emphasis ranking; never contradict its "
                "do_not_claim list.")
        if directive:
            # operator-side law wins over the review (2026-07-09 Osprey rule)
            context["instruction"] = (
                directive + " The directive above is a hard constraint from "
                "the QA layer: where it contradicts the full_picture_review, "
                "the directive wins. " + context["instruction"])

        section_basis: dict = {
            "pack": context["fact_pack"],
            "review": context.get("full_picture_review"),
            "directive": directive or "",
            "system": _SYSTEM,
            "section_instruction": instruction,
        }
        if strict_identity:
            section_basis["strict_identity"] = strict_identity
        digest = canonical_digest(section_basis)
        key = section_key(client=client, scope=scope, section=section,
                          inputs_digest=digest)

        def _produce(ctx=context, m=model, sec=section):
            return engine.deliberate(layer=f"capture-brief/{sec}",
                                     system_prompt=_SYSTEM,
                                     context=ctx, schema=m)

        if use_cache:
            result, hit = cached_call(
                key=key, validator=lambda p, m=model: m.model_validate(p),
                producer=_produce,
                meta={"section": section, "n_facts": len(sub.facts)})
        else:
            result, hit = _produce(), False
        hits[section] = hit
        assembled.update({name: getattr(result, name)
                          for name in _SECTION_FIELDS[section]})
    return CaptureBriefContent(**assembled), hits


_EDITOR_SYSTEM = """You are the cold-read editor of a finished GTM Group Federal
Opportunity Assessment. You see ONLY what the client will see; no pipeline
context, no research notes. Read the whole document the way its reader will:
once, quickly, deciding whether these people are worth a meeting.

YOUR PASS HAS TWO PHASES.

PHASE 1, DETERMINE VALUE. Judge every element of the document against the
four axes the deliverable is optimized for:
- WELL PRESENTED: does it read cleanly, build as one storyline, land its
  numbers without clutter or repetition?
- RELEVANT: does it bear on THIS client's product and federal motion, or is
  it generic federal-market wallpaper any vendor could receive?
- NEW TO THE CLIENT: does it tell them something they do not already know?
  Their own contracts, their market's common knowledge, and textbook
  procurement facts are worth zero. Buyer behavior they have not seen,
  windows they have not clocked, and doors they have not mapped are the
  value.
- ACTIONABLE: can the reader DO something with it this quarter — a named
  office, a date, a door, a watch to stand up? Information with no handle
  gets none of the reader's time.

PHASE 2, REVISE TO MAXIMIZE THAT VALUE. Cut or convert what scored low:
generic context, repeated facts, claims without handles. Sharpen what
scored high: lead with the newest and most actionable material, make every
section earn its place, keep momentum toward two outcomes — the reader
moves on the windows, and keeps the team that found them.

Record your Phase-1 judgment in value_assessment (one or two sentences per
axis: what scored low, what you did about it). Put the full revised
document in revised.

CLIENT-FILE DOCTRINE: this document contains only verified claims and
forward motion. Cut anything that grades the work behind it ('unverified',
'no record', 'did not verify', 'not capability-verified', 'pending
verification', 'zero matched'); a negative is never rendered as a negative,
it is cut or converted into a forward action. Policy signals carry status
framing (in force since, enacted), never bare past dates.

HARD LIMITS, violating any of these voids your revision:
- Never alter or introduce numbers, dollar figures, dates, ranks, notice ids,
  URLs, or entity names. Every claim you keep travels with its [F#] citation.
- Never add a claim that is not already in the document.
- Keep every {{COUNT:...}} token exactly as written.
- Keep the schema: same fields, same counts where the schema fixes them
  (3 thesis paragraphs, 5 stats).
You tighten, reorder, reframe, and cut; you never invent."""


class EditorResult(BaseModel):
    value_assessment: dict[str, str] = Field(
        description="Phase-1 judgment per axis: well_presented, relevant, "
                    "new_to_client, actionable — what scored low and what "
                    "the revision did about it")
    revised: CaptureBriefContent


def edit_capture_brief(content: CaptureBriefContent,
                       engine: Optional[DecisionEngine] = None) -> "EditorResult":
    """Whole-document editorial pass, run AFTER fidelity consensus: first
    DETERMINE the report's value (well presented, relevant, new to the
    client, actionable), then REVISE to maximize it. The caller re-audits
    the revision and falls back to the audited draft on any slip — this
    pass can only improve the document, never block the build. The value
    assessment itself is preserved for the internal trail."""
    engine = engine or DecisionEngine()
    return engine.deliberate(
        layer="capture-brief-editor",
        system_prompt=_EDITOR_SYSTEM,
        context={
            "assessment": content.model_dump(mode="json"),
            "instruction": ("Phase 1: judge this assessment's value on the "
                            "four axes. Phase 2: return the full revised "
                            "document that maximizes it."),
        },
        schema=EditorResult,
    )


def patch_capture_brief(content: CaptureBriefContent, fix_directive: str,
                        engine: Optional[DecisionEngine] = None) -> CaptureBriefContent:
    """Surgical repair between audit rounds: apply the panel's fix list to the
    EXISTING draft instead of re-composing from scratch. A full recompose
    re-reasons over the whole pack for minutes and rewrites every sentence,
    growing fresh violation surface each round (2026-07-09 Osprey: round two
    fixed 15 lines and introduced 3 new ones). The patch touches only the
    quoted lines, so audited-clean text survives verbatim and the loop
    converges. Mechanical copy+fix work: the fast tier is the right tier."""
    from agents.decisions.engine import research_engine
    engine = engine or research_engine()
    return engine.deliberate(
        layer="capture-brief-patch",
        system_prompt=_SYSTEM,
        context={
            "current_draft": content.model_dump(mode="json"),
            "fix_list": fix_directive,
            "instruction": (
                "The current_draft is already composed; the arbiter panel "
                "failed ONLY the quoted lines in fix_list. Return the FULL "
                "corrected draft: apply each fix exactly as directed, copy "
                "every other field verbatim, and invent nothing new."),
        },
        schema=CaptureBriefContent,
    )


# Money-in-motion floor: when the FactPack carries at least this many cited
# dollar facts, a composed brief with no budget bars is a QA problem, not a
# stylistic choice. (The 2026-07-05 brief silently shipped without the chart:
# budget_bars has no schema floor and nothing downstream checked it.)
BUDGET_BARS_MIN_DOLLAR_FACTS = 3
_DOLLAR_FACT_RE = re.compile(r"\$\s?[\d,.]+\s*(?:billion|million|thousand|[BMK])?", re.I)


def dollar_fact_count(pack: FactPack) -> int:
    """How many citable facts state a dollar figure — the raw material for the
    money-in-motion chart."""
    return sum(1 for f in pack.facts if _DOLLAR_FACT_RE.search(f.text))


# ────────────────────────── deterministic rendering ──────────────────────────

def _esc(s: str) -> str:
    return html.escape(s, quote=False)


_EMDASH_RUN = re.compile(r"\s*—\s*")


def normalize_house_style(s: str) -> str:
    """House style bans em dashes in deliverable copy (the AI-tell). The
    compose prompts forbid them, but a stray one from the model must never
    stamp a build: normalize composed prose deterministically. lint_emdash
    stays the backstop for template regressions."""
    return _EMDASH_RUN.sub(", ", s) if "—" in s else s


def normalize_content(c: CaptureBriefContent) -> CaptureBriefContent:
    """normalize_house_style over every composed string field, recursively."""
    def walk(v):
        if isinstance(v, str):
            return normalize_house_style(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return CaptureBriefContent(**walk(c.model_dump()))


def _client_terms(text: str) -> str:
    """Client-facing terminology shim for reference-data prose: stored data is
    out of scope to edit, but rendered copy must follow the naming map."""
    return (text.replace("this brief", "this assessment")
                .replace("the brief", "the assessment"))


_FED_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]*\.(?:gov|mil)\b", re.I)


def grade_contact_channels(page: str) -> str:
    """Post-render pass: wrap every bare federal email in composed copy with
    its grade and source from the contact graph, satisfying the constitution
    (any rendered contact carries grade + source). Channels the graph has never
    observed stay bare — the lint gate then fails the build loudly rather than
    ship a claim of currency nobody verified."""
    from tools.contact_graph.grading import grade_for
    from tools.contact_graph.store import ContactGraphStore
    try:
        observations = ContactGraphStore().read_observations()
    except Exception:
        return page  # no graph on this machine — the gate decides
    best: dict[str, tuple] = {}  # channel value -> (observed_at, source_url)
    for o in observations:
        if not o.channel_value:
            continue
        key = o.channel_value.strip().lower()
        cur = best.get(key)
        if cur is None or (o.observed_at and (cur[0] is None or o.observed_at > cur[0])):
            best[key] = (o.observed_at, o.source_url)
    if not best:
        return page
    today = date.today()

    def _wrap(m: re.Match) -> str:
        value = m.group(0)
        # inside a tag (href/src/data-* attribute) or already inside a graded
        # contact block: leave untouched
        before = page[: m.start()]
        open_tag = before.rfind("<")
        if open_tag != -1 and ">" not in before[open_tag:]:
            return value
        block = before.rfind("data-contact")
        if block != -1 and "</span>" not in page[block: m.start()]:
            return value
        hit = best.get(value.strip().lower())
        if hit is None:
            return value  # unknown to the graph: bare, so the gate fails
        grade = grade_for(hit[0], now=today)
        source = html.escape(hit[1] or "", quote=True)
        return (f'<span class="contact" data-contact="1" data-grade="{grade}" '
                f'data-source="{source}">{value}</span>')

    return _FED_EMAIL_RE.sub(_wrap, page)


# Identity assets are TEMPLATE CONSTANTS of the locked format, not generated
# content. A missing or empty asset is a broken installation, not a rendering
# choice — so the loaders below raise instead of silently omitting. (The
# 2026-07-05 11:18 brief shipped with all identity assets missing because a
# render ran mid-asset-migration and every loader degraded to "".)


def _gtm_logo_b64() -> str:
    """GTM logo, repo asset FIRST — the Desktop path failed silently once and
    shipped a brief without the logo. The asset ships with the codebase."""
    asset = _TPL / "gtm_logo.b64"
    if asset.exists():
        b64 = asset.read_text().strip()
        if b64:
            return b64
    try:
        from PIL import Image
        import io
        im = Image.open(_GTM_LOGO).convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        raise RuntimeError(
            "GTM logo asset missing or empty (agents/reports/templates/gtm_logo.b64) "
            "and no fallback source — identity assets are template constants; "
            "restore the asset rather than shipping without it"
        ) from e


def _gtm_logo_mime() -> str:
    m = _TPL / "gtm_logo.mime"
    return m.read_text().strip() if m.exists() else "image/png"


def _cover_art() -> str:
    """The cover's constellation art — part of the approved standard."""
    art = _TPL / "cover_art.svg"
    svg = art.read_text().strip() if art.exists() else ""
    if not svg:
        raise RuntimeError(
            "cover art asset missing or empty (agents/reports/templates/cover_art.svg) "
            "— identity assets are template constants; restore the asset"
        )
    return svg


def _client_monogram(client_name: str) -> str:
    """Deterministic identity plate for clients with no brand mark on file —
    the cover always carries a client logo element, never a bare wordmark."""
    initials = "".join(w[0] for w in client_name.split()[:2] if w).upper() or "?"
    return (
        '<svg class="client-mark client-mark-monogram" width="44" height="44" '
        'viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{_esc(client_name)} monogram">'
        '<rect x="1" y="1" width="42" height="42" rx="8" fill="none" '
        'stroke="var(--ink)" stroke-width="2"/>'
        '<text x="22" y="29" text-anchor="middle" font-family="var(--serif)" '
        f'font-size="18" font-weight="900" fill="var(--ink)">{_esc(initials)}</text></svg>'
    )


_WORDMARK_ASPECT = 2.5  # >= this reads as a wide wordmark, else a square mark


def _client_mark(client_name: str) -> str:
    """Per-client brand mark HTML (client cache, any supported image type),
    else a deterministic monogram. Backward-compatible string return for the
    established callers; the cover lockup uses `_resolve_client_mark` for the
    mark KIND. The .cover-logo block always contains an svg element."""
    return _resolve_client_mark(client_name)[0]


def _resolve_client_mark(client_name: str) -> tuple[str, str]:
    """(mark_html, kind) where kind is 'wordmark', 'square', or 'monogram'.

    Kind is decided from the SUCCESSFULLY RENDERED mark, never a second
    filesystem probe (Cycle-5 review): an empty or unreadable file falls back
    to the monogram AND reports kind 'monogram', so the lockup and the name
    text can never disagree."""
    path = _resolve_client_mark_path(client_name)
    if path is not None:
        if path.suffix.lower() == ".svg":
            try:
                raw = path.read_text().strip()
            except OSError:
                raw = ""
            if raw:
                marked, kind = _mark_svg_as_brand(raw, client_name)
                if marked:
                    return marked, kind
        else:
            raster = _raster_client_mark(path, client_name)
            if raster is not None:
                return raster
    return _client_monogram(client_name), "monogram"


def _resolve_client_mark_path(client_name: str):
    """The client's brand-mark file (client cache, then legacy flat), or None."""
    from tools.brand_marks import client_marks_dir, find_mark
    slug = "".join(ch if ch.isalnum() else "_"
                   for ch in client_name).strip("_").lower()
    mark = find_mark(client_marks_dir(), slug)
    if mark is None:  # legacy flat location (pre client-cache)
        legacy = (Path(__file__).resolve().parents[2] / "data" / "reference"
                  / "marks" / f"{slug}.svg")
        mark = legacy if legacy.exists() else None
    return mark


def client_cover_lockup(client_name: str) -> str:
    """THE shared cover identity lockup for every report renderer (capture
    brief, three-view assessment, Target report) — Cycle-5 review: one helper,
    one rule set. A wide wordmark rides a white identity plate on the navy
    cover with NO adjacent name (the wordmark carries the name itself); a
    square icon or the deterministic monogram keeps the visible client name
    beside it."""
    mark, kind = _resolve_client_mark(client_name)
    if kind == "wordmark":
        return f'<div class="client-plate">{mark}</div>'
    return f'{mark}<div class="rf-wordmark">{_esc(client_name)}</div>'


def _raster_client_mark(path, client_name: str):
    """(html, kind) for a raster mark, sized to its NATIVE aspect so a square
    logo is never letterboxed into a wide frame (Cycle-5 review). None if the
    bytes cannot be read."""
    import base64
    from tools.brand_marks import MARK_MIMES
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    w, h = 160, 48
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 - unreadable dims fall back to a neutral frame
        w, h = 160, 48
    aspect = (w / h) if h else 3.3
    kind = "wordmark" if aspect >= _WORDMARK_ASPECT else "square"
    size_cls = "client-brand-wide" if kind == "wordmark" else "client-brand-square"
    mime = MARK_MIMES.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(data).decode()
    html = (f'<svg class="client-mark {size_cls}" data-brand-asset="1" '
            f'viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="{_esc(client_name)}"><image href="data:{mime};base64,{b64}" '
            f'width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet"/></svg>')
    return html, kind


def _svg_root_aspect(attrs: str) -> float:
    """Native aspect from a root svg's attributes (viewBox first, then width/
    height); both quote styles. 1.0 when indeterminate."""
    vb = re.search(
        r"""viewBox\s*=\s*['"]\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)""",
        attrs, re.I)
    if vb and float(vb.group(2)):
        return float(vb.group(1)) / float(vb.group(2))
    w = re.search(r"""\bwidth\s*=\s*['"]([\d.]+)""", attrs)
    h = re.search(r"""\bheight\s*=\s*['"]([\d.]+)""", attrs)
    if w and h and float(h.group(1)):
        return float(w.group(1)) / float(h.group(1))
    return 1.0


def _mark_svg_as_brand(svg: str, client_name: str) -> tuple[str, str]:
    """(marked_html, kind). Tag an imported client SVG as a brand asset WITHOUT
    touching its native viewBox: the R13-exemption marker and a shape class go
    on the ROOT <svg> tag only, so remediation never reshapes it.

    Robust to real-world assets (Cycle-5 review): the root <svg> is located
    after an optional XML prolog / doctype / comments / whitespace (those are
    dropped for clean inline embedding), attributes are parsed in BOTH quote
    styles, and an existing class/role/aria-label is merged, never duplicated.
    Sizing follows native aspect (wide wordmark vs square)."""
    m = re.search(r"<svg\b", svg, re.I)
    if not m:
        return svg, "square"  # not a parseable svg root; no plate, keep name
    root = m.start()
    tag_end = svg.find(">", root)
    if tag_end == -1:
        return svg, "square"
    attrs = svg[root + 4:tag_end]
    kind = "wordmark" if _svg_root_aspect(attrs) >= _WORDMARK_ASPECT else "square"
    size_cls = "client-brand-wide" if kind == "wordmark" else "client-brand-square"
    needed = ["client-mark", size_cls]
    if not re.search(r"data-brand-asset\s*=", attrs, re.I):
        attrs = ' data-brand-asset="1"' + attrs
    cm = re.search(r"""\bclass\s*=\s*(['"])(.*?)\1""", attrs, re.I | re.S)
    if cm:
        have = cm.group(2).split()
        merged = have + [c for c in needed if c not in have]
        attrs = attrs[:cm.start()] + f'class="{" ".join(merged)}"' + attrs[cm.end():]
    else:
        attrs = f' class="{" ".join(needed)}"' + attrs
    if not re.search(r"\brole\s*=", attrs, re.I):
        attrs += ' role="img"'
    if not re.search(r"\baria-label\s*=", attrs, re.I):
        attrs += f' aria-label="{_esc(client_name)}"'
    # drop any prolog/comment before the root; keep the body after the open tag
    return "<svg" + attrs + ">" + svg[tag_end + 1:], kind


def _agency_strip(opps: list[CBOpportunity]) -> str:
    """Badge strip of the agencies in play; the most urgent runs hot."""
    if not opps:
        return ""
    urgent = min((o for o in opps if o.days_to_due > 0),
                 key=lambda o: o.days_to_due, default=None)
    seen, badges = set(), []
    for o in opps:
        ab = (o.agency_abbr or "").strip()
        if not ab or ab in seen:
            continue
        seen.add(ab)
        hot = " hot" if urgent and o.agency_abbr == urgent.agency_abbr else ""
        badges.append(f'<div class="agency-badge{hot}">{_esc(ab).replace(" ", "<br>")}</div>')
        if len(badges) >= 6:
            break
    return f'<div class="agency-strip">{"".join(badges)}</div>' if badges else ""


def _budget_chart(bars: list["CBBudgetBar"]) -> str:
    # THE TEMPLATE HAS NO SLOT FOR LANE TOTALS (2026-07-09 inversion):
    # NAICS lane figures are boundary context, never money-in-motion
    bars = [b for b in bars
            if not re.search(r"NAICS\s?\d{6}", f"{b.label} {b.amount_label}", re.I)]
    """Money in motion — horizontal bars of verified budget figures.

    R13 SVG SAFETY (2026-07-09): the safety margin exists by construction.
    The left inset is computed from the widest axis label (end-anchored
    text extends LEFT of its x; flush placement was the clip incident);
    value labels are width-checked and move inside the bar with a
    contrast-safe fill when they would breach the right margin. No fixed
    pixel width: the chart scales with its container."""
    from agents.reports.svg_safety import (
        COMPONENT_INSET, SAFE_INSET, end_label_placement, est_text_width,
    )
    if not bars:
        return ""
    LABEL_X, BAR_X, RIGHT = 192, 200, 800
    left_extent = max((est_text_width(b.label, 14) for b in bars[:5]),
                      default=0.0)
    left_inset = max(COMPONENT_INSET, left_extent - LABEL_X + SAFE_INSET)
    rows, y = [], 18
    for b in bars[:5]:
        w = max(60, int(540 * max(0.05, min(1.0, b.relative))))
        vx, anchor, fill = end_label_placement(BAR_X, w, b.amount_label,
                                               13, RIGHT)
        rows.append(
            f'<text x="{LABEL_X}" y="{y+14}" text-anchor="end" font-family="var(--sans)" '
            f'font-size="14" font-weight="600" fill="var(--ink)">{_esc(b.label)}</text>'
            f'<rect x="{BAR_X}" y="{y}" width="{w}" height="20" rx="3" fill="var(--accent)"></rect>'
            f'<text x="{vx:g}" y="{y+15}" '
            f'text-anchor="{anchor}" font-family="var(--mono)" font-size="13" '
            f'font-weight="700" fill="{fill}">{_esc(b.amount_label)}</text>')
        y += 48
    vb = (f"{-left_inset:g} {-COMPONENT_INSET:g} "
          f"{RIGHT + left_inset + COMPONENT_INSET:g} {y + 2 * COMPONENT_INSET:g}")
    return (f'<div class="chart-block"><span class="chart-label">Money in motion · '
            f'verified budget figures</span>'
            f'<svg viewBox="{vb}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="Bar chart of verified federal budget figures">{"".join(rows)}</svg></div>')


def _channel_block(data: dict) -> str:
    """The vehicles section's visual layer: zero-statement + donut + segments.
    Renders from vehicles.json 'channel'; silently absent when not configured."""
    ch = data.get("channel") or {}
    if not ch:
        return ""
    out = []
    if ch.get("zero_number"):
        out.append(f'<div class="zero-statement"><span class="zero-number">{_esc(ch["zero_number"])}</span>'
                   f'<p class="zero-context">{ch.get("zero_statement", "")}</p></div>')
    donut = ch.get("donut") or {}
    segs = donut.get("segments") or []
    if segs:
        total = sum(s.get("value", 0) for s in segs) or 1
        r, circ = 62, 2 * 3.14159 * 62
        offset, circles = 0.0, []
        for s in segs:
            dash = circ * s.get("value", 0) / total
            circles.append(f'<circle cx="105" cy="105" r="{r}" fill="none" stroke="{s.get("color", "#ccc")}" '
                           f'stroke-width="30" stroke-dasharray="{dash:.1f} {circ:.1f}" '
                           f'stroke-dashoffset="{-offset:.1f}"></circle>')
            offset += dash
        legend = "".join(
            f'<span><span class="sw" style="background:{s.get("color")}"></span>'
            f'<strong>{_esc(s.get("label", ""))}</strong> · {_esc(_client_terms(s.get("detail", "")))}</span>'
            for s in segs)
        out.append(
            f'<div class="donut-wrap"><svg width="210" height="210" viewBox="0 0 210 210" '
            f'xmlns="http://www.w3.org/2000/svg" aria-label="{_esc(donut.get("center_label", ""))}">'
            f'<g transform="rotate(-90 105 105)">{"".join(circles)}</g>'
            f'<text x="105" y="100" text-anchor="middle" font-family="Playfair Display, Georgia, serif" '
            f'font-size="30" font-weight="900" fill="var(--ink)">{_esc(donut.get("center_number", ""))}</text>'
            f'<text x="105" y="122" text-anchor="middle" font-family="var(--mono)" font-size="10" '
            f'letter-spacing="2" fill="var(--ink-ghost)">{_esc(donut.get("center_label", ""))}</text></svg>'
            f'<div class="donut-legend">{legend}</div></div>')
    if ch.get("segments"):
        items = "".join(
            f'<div class="segment-item"><span class="segment-entity">{_esc(s.get("entity", ""))}</span>'
            f'<span class="segment-detail">{_client_terms(s.get("detail", ""))}</span></div>'
            for s in ch["segments"])
        out.append(f'<div class="segment-list">{items}</div>')
    return "\n".join(out)


def _stat_cells(stats: list[CBStat]) -> str:
    return "\n".join(
        f'<div class="stat-cell"><span class="stat-number">{_esc(s.number)}'
        f'<span class="accent">{_esc(s.accent)}</span></span>'
        f'<span class="stat-context">{_esc(s.context)}</span></div>'
        for s in stats
    )


_STAT_BAND_SEAL_MEMO: dict = {}


def _stat_band_seal(stats: list[CBStat]) -> str:
    """ONE restrained department seal for the stat band, only when the band is
    GENUINELY single-department (client-directed + Cycle 5 review guard,
    2026-07-12).

    Every stat accent that resolves to an agency is mapped to its parent
    department; the seal renders only when they all share ONE department (and
    at least one is an agency). A mixed-department or all-scope band gets no
    seal (text-only fallback), so a parent seal is never repeated and a
    multi-agency report never wears one department's mark. Non-agency accents
    (companies, qualifiers) are ignored for the determination. Returns the
    inline seal + department name, or '' for the text-only case."""
    key = tuple((s.accent or "").strip() for s in stats)
    if key in _STAT_BAND_SEAL_MEMO:
        return _STAT_BAND_SEAL_MEMO[key]
    out = ""
    try:
        from tools.agencies import AGENCIES, find
        depts = set()
        for s in stats:
            agency = find((s.accent or "").strip())
            if agency is not None:
                depts.add(agency.get("parent") or agency.get("abbr"))
        if len(depts) == 1:
            dept_abbr = next(iter(depts))
            from tools.brand_marks import MARK_MIMES, find_mark
            from agents.reports.views import _seal_dir
            path = find_mark(_seal_dir(), (dept_abbr or "").lower())
            if path is not None:
                import base64
                mime = MARK_MIMES.get(path.suffix.lower(), "image/png")
                uri = ("data:" + mime + ";base64,"
                       + base64.b64encode(path.read_bytes()).decode())
                name = next((a["name"] for a in AGENCIES
                             if a["abbr"] == dept_abbr), dept_abbr)
                out = (f'<img class="stat-band-seal" src="{uri}" '
                       f'alt="{_esc(name or "")} seal">'
                       f'<span class="stat-band-dept">{_esc(name or "")}</span>')
    except Exception:  # noqa: BLE001 - a mark never blocks a render
        out = ""
    _STAT_BAND_SEAL_MEMO[key] = out
    return out


def _opp_section_body(c: CaptureBriefContent, callout: str, opp_cards: str,
                      counts: Optional[dict] = None,
                      horizon: Optional[dict] = None) -> str:
    """Section 01 body. With opportunities: the agency strip, runway and cards.
    With NONE: the section SELLS THE NEXT LAYER DOWN — it points into the
    layers the sweep DID find (adjacencies, teaming, forming windows), with
    the clean screen stated once as the credential, never as the headline.
    Pointers render only for layers actually present in this build."""
    if c.opportunities:
        return (f"{_agency_strip(c.opportunities)}{callout}"
                f'{_runway(c.opportunities)}<div class="plays-grid">{opp_cards}</div>')
    counts = counts or {}
    found: list[str] = []
    if counts.get("monitor_notices"):
        # phrasing keeps >3 words between the digit and any lint-reconciled
        # count noun — the number itself resolves from ground truth
        found.append("{{COUNT:monitor_notices}} monitor-grade adjacencies "
                     "held and worked week over week")
    if counts.get("teaming_watch") or counts.get("teaming_plays"):
        found.append("teaming paths with primes named from award evidence "
                     "(Section 03)")
    if (horizon or {}).get("items"):
        found.append("the forming windows mapped in the Developing Horizon "
                     "(Section 05), each with cited signals and a projected "
                     "window")
    layers = ("; ".join(found) if found
              else "the market read, the watchlist, and the vehicle posture "
                   "that follow")
    return ('<p class="section-thesis">The motion this cycle is one layer '
            f'down, and the sweep found it: {layers}. That is where '
            'positioning effort goes now, ahead of the notices posting. '
            'A standing watch holds on these lanes; the moment a notice '
            'posts, it surfaces here first.</p>'
            f'{callout}')


def _runway(opps: list[CBOpportunity]) -> str:
    """Capture-runway SVG: bar length proportional to days-to-gate, urgent in red."""
    rows = [o for o in opps if o.days_to_due > 0][:5]
    if not rows:
        return ""
    max_days = max(o.days_to_due for o in rows)
    parts = [
        '<div class="chart-block"><span class="chart-label">Capture runway · days from today to each response gate</span>',
        f'<svg viewBox="0 0 800 {58 + 44 * len(rows)}" xmlns="http://www.w3.org/2000/svg">',
        f'<line x1="200" y1="14" x2="200" y2="{14 + 44 * len(rows)}" stroke="var(--rule-hi)" stroke-width="1.5"/>',
        f'<text x="200" y="{38 + 44 * len(rows)}" text-anchor="middle" font-family="var(--mono)" font-size="11" letter-spacing="2" fill="var(--ink-ghost)">TODAY</text>',
    ]
    for i, o in enumerate(rows):
        y = 28 + 44 * i
        w = max(60, int(540 * o.days_to_due / max_days))
        color = "oklch(48% 0.19 25)" if o.urgent else "var(--accent)"
        opacity = 1.0 - 0.12 * i if not o.urgent else 1.0
        label = f"{o.due_label.split(',')[0].upper()} · {o.days_to_due} DAYS"
        parts += [
            f'<text x="192" y="{y + 14}" text-anchor="end" font-family="var(--sans)" font-size="14" font-weight="600" fill="var(--ink)">{_esc(o.agency_abbr)}</text>',
            f'<rect x="200" y="{y}" width="{w}" height="18" rx="3" fill="{color}" opacity="{opacity:.2f}"/>',
            f'<text x="{208 + w}" y="{y + 14}" font-family="var(--mono)" font-size="12" font-weight="600" fill="var(--ink-dim)">{_esc(label)}</text>',
        ]
    parts.append("</svg></div>")
    return "\n".join(parts)


def _ring(days: int) -> str:
    frac = max(0.05, min(1.0, days / 30))
    dash = 289 * frac
    return (
        f'<svg class="ring" width="110" height="110" viewBox="0 0 110 110">'
        f'<circle cx="55" cy="55" r="46" fill="none" stroke="#f2d9d5" stroke-width="9"/>'
        f'<circle cx="55" cy="55" r="46" fill="none" stroke="oklch(48% 0.19 25)" stroke-width="9" '
        f'stroke-dasharray="{dash:.0f} 289" stroke-linecap="round" transform="rotate(-90 55 55)"/>'
        f'<text x="55" y="53" text-anchor="middle" font-family="Playfair Display, Georgia, serif" font-size="34" font-weight="900" fill="oklch(48% 0.19 25)">{days}</text>'
        f'<text x="55" y="74" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="10" font-weight="600" letter-spacing="2" fill="oklch(48% 0.19 25)">DAYS</text></svg>'
    )


def _match_dossier(o: CBOpportunity, dossiers: Optional[list[dict]]) -> Optional[dict]:
    """Join a dossier to its opportunity: by id (either direction), url, or title."""
    for d in dossiers or []:
        did = str(d.get("id") or "")
        if not did:
            continue
        if (did == o.notice_id or did in o.url
                or o.notice_id and o.notice_id in did
                or (d.get("title") or "").strip().lower() == o.headline.strip().lower()):
            return d
    return None


def _depth_block(d: dict) -> str:
    """Dossier depth INSIDE the locked skin — existing classes only."""
    parts = []
    crits = d.get("evaluation_criteria") or []
    if crits:
        parts.append("<strong>Evaluated on:</strong> " + "; ".join(_esc(c) for c in crits[:4]) + ".")
    inc = d.get("incumbent_signals") or []
    if inc:
        parts.append("<strong>Incumbency:</strong> " + " ".join(_esc(s) for s in inc[:2]))
    wins = d.get("win_themes") or []
    if wins:
        parts.append("<strong>How you win:</strong> " + "; ".join(_esc(w) for w in wins[:3]) + ".")
    body = f'<p class="play-body">{" ".join(parts)}</p>' if parts else ""
    tags = ""
    if d.get("vehicle"):
        tags += f'<span class="play-tag">{_esc(d["vehicle"])}</span>'
    for r in (d.get("submission_requirements") or [])[:2]:
        tags += f'<span class="play-tag">{_esc(r[:60])}</span>'
    channel = (f'<div class="play-channel"><span class="play-channel-label">Bid mechanics</span>{tags}</div>'
               if tags else "")
    return body + channel


def _opp_card(o: CBOpportunity, code: str, dossiers: Optional[list[dict]] = None,
              trace: Optional[dict] = None) -> str:
    tags = f'<span class="play-tag"><a href="{html.escape(o.url, quote=True)}">{_esc(o.notice_id)}</a></span>'
    tags += f'<span class="play-tag">{_esc(o.due_label)}</span>'
    d = _match_dossier(o, dossiers)
    depth = _depth_block(d) if d else ""
    # SHOW THE WORK (2026-07-10): the fit is inspectable, not asserted —
    # matched terms, NAICS boundary, mission component, screen inference
    why = ""
    if trace:
        chips = "".join(f'<span class="play-tag">{_esc(t)}</span>'
                        for t in (trace.get("matched_terms") or [])[:5])
        if trace.get("naics"):
            chips += (f'<span class="play-tag">NAICS {_esc(trace["naics"])}'
                      + (" · client lane" if trace.get("naics_in_boundary") else "")
                      + '</span>')
        if trace.get("mission_component"):
            chips += f'<span class="play-tag">{_esc(trace["mission_component"])}</span>'
        chips += f'<span class="play-tag">score {trace.get("score", 0)}</span>'
        inf = (f'<p class="play-body" style="font-size:12.5px;color:var(--ink-ghost);'
               f'margin-top:6px">Analyst inference: {_esc(trace["screen_inference"])}</p>'
               if trace.get("screen_inference") else "")
        why = (f'<div class="play-channel"><span class="play-channel-label">'
               f'Why this fits</span>{chips}</div>{inf}')
    return (
        f'<div class="play-card"><div class="play-header">'
        f'<span class="play-code">{code}</span>'
        f'<span class="play-headline">{_esc(o.headline)}</span>'
        f'<span class="fit-chip {_CHIP_CLASS[o.fit]}">{o.fit}</span></div>'
        f'<p class="play-body">{_esc(o.body)}</p>'
        f'{depth}'
        f'{why}'
        f'<div class="play-channel"><span class="play-channel-label">Notice</span>{tags}</div></div>'
    )


def _news_table(items: list[CBNewsItem]) -> str:
    rows = "".join(
        f'<tr><td class="date-col">{_esc(n.recency)}</td>'
        f'<td class="label-col"><a class="live" data-thirdparty="1" href="{html.escape(n.url, quote=True)}">{_esc(n.headline)}</a></td>'
        f'<td>{_esc(n.why)}</td></tr>'
        for n in items
    )
    return ('<div class="data-table-wrap"><table class="data-table"><thead><tr>'
            "<th>When</th><th>Signal</th><th>Why it matters</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


def _sigbar(counts: list[int]) -> str:
    total = sum(counts) or 1
    colors = ["oklch(46% 0.21 252)", "#2E9FD8", "#5b87ae", "#8fa8c0"]
    labels = ["Funding & policy", "Threat environment", "Agency posture", "Market & competitive"]
    segs = "".join(
        f'<div class="sigbar-seg" style="width:{100 * c / total:.1f}%;background:{colors[i]}">{c}</div>'
        for i, c in enumerate(counts) if c
    )
    legend = "".join(
        f'<span><span class="sw" style="background:{colors[i]};display:inline-block;width:11px;height:11px;'
        f'border-radius:3px;margin-right:7px;vertical-align:-1px"></span>{labels[i]}</span>'
        for i in range(4)
    )
    return (f'<div class="sigbar"><div class="sigbar-track">{segs}</div>'
            f'<div class="sigbar-legend">{legend}</div></div>')


def _vehicles_section() -> str:
    data = load_vehicles()
    confirmed = [v for v in data["vehicles"] if v.get("verified")]
    rows = "".join(
        f'<tr><td class="label-col">{_esc(v["name"])}</td><td>{_esc(v["agency"])}</td>'
        f'<td>{_esc(v["ceiling"])}</td><td>{_esc(v["software_vendor_path"])}</td></tr>'
        for v in confirmed
    )
    return (
        '<div class="data-table-wrap"><span class="data-table-label">Standing vehicle catalog · confirmed entries</span>'
        '<table class="data-table"><thead><tr><th>Vehicle</th><th>Agency</th><th>Ceiling</th><th>Client path</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        ''  # pending-verification list is INTERNAL (client-file doctrine 2026-07-10)
        # provenance tag: this section is compiled reference data, NOT fact-pack
        # material — without the tag the footer's traceability claim is false
        # on its face (2026-07-09 review)
        f'<p style="font-size:12px;color:var(--ink-ghost);margin-top:8px">'
        f"GTM Group compiled vehicle reference, as of "
        f"{_esc((data.get('_meta') or {}).get('updated', 'n/a'))}.</p>"
    )


def _apfs_note(total_records: Optional[int]) -> str:
    """Source credit for the multi-adapter forecast layer."""
    if not total_records:
        return ""
    return (f' <span data-counts-verified="1">Source: official agency '
            f"acquisition forecasts, {total_records:,} published planning records "
            f"screened this sweep.</span>")


def _early_signals_section(forecasts: list[dict], delta: Optional[dict] = None,
                           as_of: Optional[date] = None,
                           total_records: Optional[int] = None) -> str:
    """Agency forecast matches — STATED INTENT, rendered only here, only muted.

    Every forecast row carries data-forecast="1"; the lint gate fails any build
    where that marker appears outside a section.early-signals wrapper.

    Date discipline (2026-07-09 review): an anticipated date already behind
    as_of must NEVER render as forward pipeline. Past-dated lines carry an
    explicit disposition (date passed, unconfirmed; treat as slipped until
    re-verified) and sort below the forward-dated lines. A federal BD reader
    spots an expired anticipation instantly; the report says it first.

    Relevance discipline (2026-07-09 review, round two): a NAICS-only match
    is a LANE screen, not a relevance screen; 541519 lines like Oracle
    maintenance cleared it and rendered as "stated intent" for an aviation-
    risk client. Rendered rows require a capability-keyword hit; lane-only
    matches stay internal and feed the screen count. Zero relevant lines is
    itself the finding, stated plainly: the buying that fits is happening
    outside the published forecast."""
    if not forecasts:
        return ""
    as_of = as_of or date.today()

    def _capability_hit(m: dict) -> bool:
        return any(
            str(reason).startswith(("keywords", "capability"))
            for reason in (m.get("reasons") or [])
        )

    relevant = [m for m in forecasts if _capability_hit(m)]
    lane_only = len(forecasts) - len(relevant)
    if not relevant:
        return f"""
<section class="early-signals" style="border-left:3px solid var(--stone);padding-left:26px">
  <div class="section-opener section-divider"><span class="ghost-num" style="color:var(--ink-ghost);opacity:.35">ES</span>
    <span class="section-opener-code" style="color:var(--stone)">Early Signals · Agency Procurement Forecasts</span>
    <h2 class="section-name" style="color:var(--stone)">Stated<br>Intent</h2>
  </div>
  <p class="section-thesis"><span data-counts-verified="1">Across {len(forecasts)}
  published planning lines cleared for the client&#39;s NAICS lanes this cycle,
  none states intent matching the capability.</span> The buying that fits is
  happening outside the published forecast; that gap is a finding, not a blank.
  The forecast is re-screened on every refresh, and any capability-matching
  line surfaces here first.{_apfs_note(total_records)}</p>
</section>"""
    forecasts = relevant

    def _anticipated(r: dict) -> Optional[date]:
        raw = str(r.get("anticipated_solicitation") or "")
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        return None

    def _key(m: dict):
        r = m.get("record") or m
        d = _anticipated(r)
        # forward lines first (soonest first), then undated, then past-dated
        if d and d >= as_of:
            return (0, d.toordinal())
        if d is None:
            return (1, 0)
        return (2, -d.toordinal())

    rows = []
    for m in sorted(forecasts[:10], key=_key):
        r = m.get("record") or m
        timing = r.get("anticipated_solicitation") or "timing not stated"
        d = _anticipated(r)
        stale = ('<div style="font-size:10.5px;color:oklch(48% 0.19 25)">'
                 'date passed, unconfirmed · treat as slipped until re-verified'
                 '</div>') if d and d < as_of else ""
        val = r.get("estimated_value_range") or ""
        why = "; ".join(m.get("reasons") or [])
        rows.append(
            f'<tr data-forecast="1">'
            f'<td class="date-col" style="color:var(--stone)">{_esc(timing)}{stale}</td>'
            f'<td class="label-col"><a class="live" style="color:var(--stone)" '
            f'href="{html.escape(r.get("url", ""), quote=True)}">{_esc(r.get("title", ""))}</a>'
            f'<div style="font-size:11px;color:var(--ink-ghost)">{_esc(r.get("component") or r.get("agency") or "")}'
            f'{" · " + _esc(val) if val else ""}{" · " + _esc(r.get("set_aside")) if r.get("set_aside") else ""}</div></td>'
            f'<td style="color:var(--ink-ghost)">{_esc(why)}</td></tr>')
    delta_html = ""
    if delta and (delta.get("moved") or delta.get("disappeared")):
        bits = [f"“{_esc(m['title'][:60])}” moved {_esc(str(m.get('from')))} → {_esc(str(m.get('to')))}"
                for m in (delta.get("moved") or [])[:3]]
        bits += [f"“{_esc(m['title'][:60])}” dropped from the forecast"
                 for m in (delta.get("disappeared") or [])[:3]]
        delta_html = ('<p style="font-size:12.5px;color:var(--ink-ghost);margin-top:10px">'
                      '<strong style="color:var(--stone)">Forecast movement since last pull:</strong> '
                      + " · ".join(bits) + "</p>")
    return f"""
<section class="early-signals" style="border-left:3px solid var(--stone);padding-left:26px">
  <div class="section-opener section-divider"><span class="ghost-num" style="color:var(--ink-ghost);opacity:.35">ES</span>
    <span class="section-opener-code" style="color:var(--stone)">Early Signals · Agency Procurement Forecasts</span>
    <h2 class="section-name" style="color:var(--stone)">Stated<br>Intent</h2>
    <p class="section-thesis" style="color:var(--ink-ghost)">Agency-stated intent (forecast), not a live opportunity.
    Statutorily required projections, revised or cancelled routinely; position early, verify before spending bid hours.</p>
  </div>
  <div class="data-table-wrap"><table class="data-table"><thead><tr>
    <th>Anticipated</th><th>Forecast line · component · band</th><th>Why it matches</th>
  </tr></thead><tbody>{"".join(rows)}</tbody></table></div>
  {f'<p style="font-size:12px;color:var(--ink-ghost);margin-top:8px"><span data-counts-verified="1">{lane_only} further planning lines cleared the NAICS-lane screen without a capability match and are not shown.</span></p>' if lane_only else ""}
  {f'<p style="font-size:12px;color:var(--ink-ghost);margin-top:8px">{_apfs_note(total_records)}</p>' if total_records else ""}
  {delta_html}
</section>"""


_HZ_CHIP = {"strong": ("fit-direct", "STRONG SIGNAL"),
            "moderate": ("fit-strong", "MODERATE"),
            "early": ("fit-adjacent", "EARLY READ")}


def _horizon_section(hz: Optional[dict]) -> str:
    """Developing Horizon — APPROVED pattern-scoped forming opportunities,
    rendered as auditable inference chains: verified (cited) → pattern →
    projection (labeled GTM Group judgment) → watching. Every item carries
    data-horizon="1"; the lint gate fails any appearance outside a
    section.developing-horizon wrapper, and any item missing its citation or
    its projection label. Draft sets never reach this function — the caller
    loads via horizon.approved_set()."""
    items = (hz or {}).get("items") or []
    if not items:
        return ""
    hz = json.loads(json.dumps(hz))  # deep copy, then house-style normalize
    def _n(d, *keys):
        for k in keys:
            if isinstance(d.get(k), str):
                d[k] = normalize_house_style(d[k])
    _n(hz, "method_note")
    for it in hz["items"]:
        _n(it, "title", "where", "pattern", "projection", "window", "watching")
        for s in it.get("verified") or []:
            _n(s, "text")
    items = hz["items"]
    cards = []
    for i, it in enumerate(items):
        cls, chip = _HZ_CHIP.get(it.get("confidence", "early"),
                                 ("fit-adjacent", "EARLY READ"))
        sigs = "".join(
            f'<li style="margin:4px 0">{_esc(s.get("text", ""))} '
            f'<a class="live" href="{html.escape(s.get("source", ""), quote=True)}"'
            f' style="font-size:11px">source ↗</a></li>'
            for s in it.get("verified") or [])
        cards.append(
            f'<div class="play-card" data-horizon="1"><div class="play-header">'
            f'<span class="play-code">HZN-{i + 1:02d}</span>'
            f'<span class="play-headline">{_esc(it.get("title", ""))}</span>'
            f'<span class="fit-chip {cls}">{chip}</span></div>'
            f'<p class="play-body" style="margin-bottom:4px"><strong>'
            f'{_esc(it.get("where", ""))}</strong></p>'
            f'<p class="play-body" style="margin-bottom:2px"><strong>Verified'
            f'</strong></p><ul class="play-body" style="margin-top:0">{sigs}</ul>'
            f'<p class="play-body"><strong>Pattern ·</strong> '
            f'{_esc(it.get("pattern", ""))}</p>'
            f'<div class="callout-box"><span class="callout-box-label">'
            f'GTM Group projection · {_esc(it.get("window", ""))}</span>'
            f'<p class="callout-box-text">{_esc(it.get("projection", ""))}</p></div>'
            f'<div class="play-channel"><span class="play-channel-label">Watching'
            f'</span><span class="play-tag">{_esc(it.get("watching", ""))}</span>'
            f'</div></div>')
    method = (f'<p class="section-thesis" style="font-size:12.5px;'
              f'color:var(--ink-ghost)">{_esc(hz.get("method_note", ""))}</p>'
              if hz.get("method_note") else "")
    return f"""
<section class="developing-horizon">
  <div class="section-opener section-divider"><span class="ghost-num">05</span>
    <span class="section-opener-code">Section 05 · Developing Horizon</span>
    <h2 class="section-name">Where the Next<br>Windows Form</h2>
    <p class="section-thesis">Nothing below is a live solicitation. These are
    windows forming: verified signals, the pattern they make, and where GTM
    Group judges positioning effort belongs before any notice posts. Each
    thread is monitored continuously; projections are revisited every cycle
    as new records land.</p>
  </div>
  {method}
  <div class="plays-grid">{"".join(cards)}</div>
</section>"""


def _buyer_map_section(bm: Optional[dict]) -> str:
    """INCUMBENT BUYER MAP: the deterministic centerpiece (2026-07-09).
    Rendered straight from the collector output, never composed: office ->
    products bought -> matched spend -> next PoP end -> sole-source flag.
    Displacement windows (PoP end inside 18 months) lead. The population
    label is mandatory; without it the table does not render."""
    if not bm or not bm.get("buyers") or not bm.get("population_label"):
        return ""
    windows = bm.get("displacement_windows") or []
    pool = [b for b in bm["buyers"]
            if b.get("products") or b.get("sole_source_notices")]
    # selection priority: displacement windows and sole-source trails are
    # the actionable rows; raw matched spend fills the remainder
    pool.sort(key=lambda x: (not x.get("displacement_window"),
                             not x.get("sole_source_notices"),
                             -(x.get("total") or 0)))
    buyers = pool[:12]
    if not buyers:
        return ""

    def _tr(b: dict) -> str:
        prods = ", ".join((b.get("products") or b.get("terms") or [])[:4])
        pop = b.get("next_pop_end") or ""
        win = ('<span class="fit-chip fit-direct">WINDOW</span>'
               if b.get("displacement_window") else "")
        ss = "yes" if b.get("sole_source") else ""
        url = (b.get("records") or [{}])[0].get("url") or ""
        buyer = (f'<a class="live" href="{html.escape(str(url), quote=True)}">'
                 f'{_esc(b["buyer"][:52])}</a>' if url else _esc(b["buyer"][:52]))
        return (f"<tr><td class=\"label-col\">{buyer}</td>"
                f"<td>{_esc(prods)}</td>"
                f"<td>{_esc(_fmt_money_short(b.get('total')))}</td>"
                f"<td>{_esc(pop)} {win}</td><td>{ss}</td></tr>")

    rows = "".join(_tr(b) for b in sorted(
        buyers, key=lambda x: (not x.get("displacement_window"),
                               -(x.get("total") or 0))))
    quotes = ""
    for b in buyers:
        for n in (b.get("sole_source_notices") or [])[:1]:
            if n.get("quote"):
                quotes += (
                    f'<div class="callout-box"><span class="callout-box-label">'
                    f"Sole-source language · {_esc(b['buyer'][:40])}</span>"
                    f'<p class="callout-box-text">"{_esc(n["quote"][:200])}" '
                    f'<a class="live" href="{html.escape(str(n.get("url") or ""), quote=True)}">'
                    f"{_esc(str(n.get('notice_id') or 'notice'))[:20]}</a></p></div>")
    return f"""
<section>
  <div class="section-opener section-divider"><span class="ghost-num">IB</span>
    <span class="section-opener-code">Incumbent Buyer Map · Description-Level Evidence</span>
    <h2 class="section-name">Who Buys This<br>Today</h2>
  </div>
  <p class="section-thesis">Federal buyers with incumbent products or
  capability-matched purchasing on the record, ranked by matched spend.
  A displacement window is a period of performance ending inside 18 months:
  the moment an incumbent subscription can be contested.
  {len(windows)} window(s) open in this map.</p>
  <div class="data-table-wrap"><span class="data-table-label">Buyer -> products -> matched spend -> next PoP end</span>
  <table class="data-table"><thead><tr><th>Buying office</th><th>Products / matched terms</th>
  <th>Matched spend</th><th>Next PoP end</th><th>Sole source</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
  <p style="font-size:12px;color:var(--ink-ghost);margin-top:8px">Population:
  {_esc(bm["population_label"])} · retrieved {_esc(str(bm.get("retrieved_at") or ""))}
 .</p>
  {quotes}
</section>"""


def _fmt_money_short(v) -> str:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


_QA_FLAG_RE = re.compile(r"\[REVIEW: [^\]\n]{1,220}\]")


def _apply_qa_layer(page: str, qa, state: str) -> str:
    """DRAFT state: watermark band, ochre inline flag markers, and the QA
    appendix listing every auto-fix/suppression/flag. RELEASE state returns
    the page untouched — zero flags is a precondition of release, so markers
    can never appear there."""
    if state != "draft":
        return page
    band = ('<div style="position:sticky;top:0;z-index:99;background:#B8860B;'
            'color:#fff;font:700 13px/1.2 IBM Plex Mono,monospace;'
            'letter-spacing:.14em;text-transform:uppercase;padding:9px 18px;'
            'text-align:center">Draft · internal QA copy · not for client '
            'release</div>')
    page = page.replace("<body>", "<body>" + band, 1)
    page = _QA_FLAG_RE.sub(
        lambda m: ('<span style="background:rgba(184,134,11,.14);color:#8a6508;'
                   'font:600 11px IBM Plex Mono,monospace;padding:1px 6px;'
                   f'border-radius:4px">{m.group(0)}</span>'), page)
    if qa is not None:
        rows = "".join(
            f"<tr><td>{_esc(a.rule)}</td><td>{_esc(a.action)}</td>"
            f"<td>{_esc(a.location)}</td><td>{_esc(a.reason)}</td></tr>"
            for a in qa.actions) or ('<tr><td colspan="4">Clean pass: zero '
                                     "interventions.</td></tr>")
        appendix = (
            '<section style="page-break-before:always;padding:34px 0 10px">'
            '<h2 style="font:700 20px IBM Plex Mono,monospace">QA Appendix · '
            "draft only</h2>"
            '<p style="font-size:13px">Every auto-fix, suppression, and open '
            "flag from the graceful QA layer. Flags are resolved by fixing "
            "source data or copy and regenerating, never by hiding the "
            "marker. This page is written to a sidecar, not the client PDF, "
            "on release.</p>"
            '<div class="data-table-wrap"><table class="data-table">'
            "<thead><tr><th>Rule</th><th>Action</th><th>Location</th>"
            f"<th>Reason</th></tr></thead><tbody>{rows}</tbody></table>"
            "</div></section>")
        page = page.replace("</div></body></html>",
                            appendix + "</div></body></html>", 1)
    return page


def render_capture_brief(c: CaptureBriefContent, as_of: Optional[date] = None,
                         dossiers: Optional[list[dict]] = None,
                         forecasts: Optional[list[dict]] = None,
                         forecast_delta: Optional[dict] = None,
                         counts: Optional[dict] = None,
                         horizon: Optional[dict] = None,
                         forecast_total: Optional[int] = None,
                         qa=None, state: str = "release",
                         buyer_map: Optional[dict] = None,
                         fit_traces: Optional[dict] = None) -> str:
    c = normalize_content(c)  # house style over every composed string
    css = (_TPL / "capture_brief.css").read_text()
    as_of = as_of or date.today()
    date_label = as_of.strftime("%B %-d, %Y")
    urgent = min((o for o in c.opportunities if o.days_to_due > 0),
                 key=lambda o: o.days_to_due, default=None)

    # template constants — the loaders raise on missing assets, never omit
    gtm = ('<div class="logo-plate"><img id="gtmLogoImg" '
           f'src="data:{_gtm_logo_mime()};base64,{_gtm_logo_b64()}" '
           'alt="GTM — Go To Market"></div>')
    client_logo_block = client_cover_lockup(c.client_name)
    thesis = "".join(f"<p>{p}</p>" for p in map(_esc, c.thesis))
    opp_cards = "\n".join(_opp_card(o, f"OPP-{i+1:02d}", dossiers,
                                    trace=(fit_traces or {}).get(o.notice_id))
                           for i, o in enumerate(c.opportunities))
    pipe_cards = "\n".join(_opp_card(o, f"PIPE-{i+1:02d}", dossiers,
                                     trace=(fit_traces or {}).get(o.notice_id))
                            for i, o in enumerate(c.pipeline))
    beats = [("1 of 4 · Funding and Policy", c.news_funding), ("2 of 4 · Threat Environment", c.news_threat),
             ("3 of 4 · Agency Posture and Programs", c.news_agency), ("4 of 4 · Market and Competitive", c.news_market)]
    news_html = "\n".join(
        f'<div class="beat"><span class="beat-number">{label}</span>{_news_table(items)}</div>'
        for label, items in beats if items
    )
    news_counts = [len(c.news_funding), len(c.news_threat), len(c.news_agency), len(c.news_market)]

    callout = ""
    if urgent:
        callout = (
            f'<div class="callout-box"><div class="callout-flex">{_ring(urgent.days_to_due)}<div>'
            f'<span class="callout-box-label">Action · {_esc(urgent.due_label.split(",")[0])}</span>'
            f'<p class="callout-box-text">{_esc(c.action_callout)}</p></div></div></div>'
        )

    page = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(c.client_name)} · Federal Opportunity Assessment</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="page">

<header class="cover">
  {_cover_art()}
  <div class="cover-top-row">
    <div class="cover-logo">{client_logo_block}</div>
    <div class="pscg-logo">{gtm}</div>
  </div>
  <span class="cover-eyebrow">Federal Opportunity Assessment · Confidential</span>
  <h1 class="cover-title">{_esc(c.client_name)}</h1>
  <p class="cover-subtitle">{_esc(c.subtitle)}</p>
  <div class="cover-meta">
    <div class="cover-meta-item"><span class="cover-meta-label">Prepared by</span><span class="cover-meta-value">Tyler Johnson · GTM Group</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Prepared for</span><span class="cover-meta-value">{_esc(c.meta_prepared_for)}</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Date</span><span class="cover-meta-value">{date_label}</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Classification</span><span class="cover-meta-value">Proprietary · Prepared for {_esc(c.client_name)}</span></div>
  </div>
  <div class="thesis-block"><span class="thesis-label">Thesis</span><div class="thesis-text">{thesis}</div></div>
</header>

<section class="stat-band">
  <div class="stat-band-head">{_stat_band_seal(c.stats)}<span class="stat-band-label">Verified {date_label} · SAM.gov + primary sources</span></div>
  <div class="stat-grid">{_stat_cells(c.stats)}</div>
</section>

<section>
  <div class="section-opener"><span class="ghost-num">01</span>
    <span class="section-opener-code">Section 01 · Live Opportunities</span>
    <h2 class="section-name">Open<br>Windows</h2>
  </div>
  {_opp_section_body(c, callout, opp_cards, counts=counts, horizon=horizon)}
  <div class="kill-line"><p class="kill-line-text">{_esc(c.kill_line_opps)}</p></div>
</section>

{_buyer_map_section(buyer_map)}

<section>
  <div class="section-opener section-divider"><span class="ghost-num">02</span>
    <span class="section-opener-code">Section 02 · Federal News Assessment</span>
    <h2 class="section-name">The Demand<br>Tailwind</h2>
  </div>
  {_sigbar(news_counts)}
  {_budget_chart(c.budget_bars)}
  {news_html}
  <div class="kill-line"><p class="kill-line-text">{_esc(c.kill_line_news)}</p></div>
</section>

<section>
  <div class="section-opener section-divider"><span class="ghost-num">03</span>
    <span class="section-opener-code">Section 03 · Contract Vehicle Posture</span>
    <h2 class="section-name">Vehicles</h2>
  </div>
  {_channel_block(load_vehicles())}
  {_vehicles_section()}
  <div class="callout-box"><span class="callout-box-label">Action</span>
  <p class="callout-box-text">{_esc(c.partner_callout)}</p></div>
</section>

<section>
  <div class="section-opener section-divider"><span class="ghost-num">04</span>
    <span class="section-opener-code">Section 04 · Program Pipeline</span>
    <h2 class="section-name">What's<br>Forming</h2>
  </div>
  {f'<div class="plays-grid">{pipe_cards}</div>' if c.pipeline else '<p class="section-thesis">No forming programs surfaced this cycle; the pipeline populates as forecasts and pre-solicitations appear.</p>'}
</section>

{_horizon_section(horizon)}

{_early_signals_section(forecasts or [], forecast_delta, as_of=as_of, total_records=forecast_total)}

<footer class="footer">
  <span class="footer-label">Verification &amp; Sources</span>
  <p class="footer-sources">{_esc(c.footer_verification)} Fit assessments and "why it matters" lines are GTM Group analyst judgment. Vehicle-catalog figures are GTM Group compiled reference, as of {_esc((load_vehicles().get("_meta") or {}).get("updated", "n/a"))}. Early Signals derive from the DHS Acquisition Planning Forecast System.</p>
</footer>

</div></body></html>"""
    # The compose prompt tells the model to write {{COUNT:name}} tokens for every
    # count instead of a literal number (so prose can't disagree with ground
    # truth). This legacy path predates that machinery; resolve them here from the
    # canonical counts() the caller supplies. Unknown/leftover tokens are caught
    # by lint_count_tokens downstream (a loud DO-NOT-SEND, never a silent ship).
    if counts is not None:
        from agents.reports.document import resolve_count_tokens
        page = resolve_count_tokens(page, counts)
    # R13: deterministic SVG margin auto-fix (viewBox padding only) runs on
    # every render; anything unparseable is left for lint_svg_geometry
    from agents.reports.svg_safety import autofix_svg_margins
    page, svg_fixes = autofix_svg_margins(page)
    if qa is not None:
        for fx in svg_fixes:
            qa.add("R13", "auto_fix", "svg", fx)
    return _apply_qa_layer(grade_contact_channels(page), qa, state)


_COUNT_TOKEN_RE = re.compile(r"\{\{COUNT(?:_WORD)?:(\w+)\}\}")
_SUPERLATIVE_RE = re.compile(
    r"\b(top|largest|leading|leads|biggest|number one)\b|#1\b", re.I)
_CITED_RANK_RE = re.compile(r"ranked #([2-9]|\d{2,}) of \d+")


def zero_stat_problems(c: CaptureBriefContent, counts: dict) -> list[str]:
    """A marquee stat that RENDERS zero fails the build. The 2026-07-09 Osprey
    report shipped '0 subaward primes' beside GDIT's cited $893.7M: the
    composer put a mis-named COUNT token on a fact-derived card, and the
    arbiters could not see it because they audit pre-substitution text. This
    gate runs on the RESOLVED numbers, before render."""
    problems: list[str] = []
    for s in c.stats:
        resolved = _COUNT_TOKEN_RE.sub(
            lambda m: str(counts.get(m.group(1), 0)), s.number).strip()
        bare = resolved.lstrip("$").rstrip("%+").strip()
        if bare in ("0", "0.0", "zero"):
            cited = re.findall(r"F\d+", f"{s.number} {s.accent} {s.context}")
            problems.append(
                f"stat card renders zero ('{s.number}' -> '{resolved}')"
                + (f" while citing populated facts {cited}" if cited else "")
                + f" — {s.context[:70]}")
    return problems


def _composed_strings(c: CaptureBriefContent) -> list[str]:
    out = list(c.thesis) + [c.action_callout, c.kill_line_opps, c.kill_line_news,
                            c.partner_callout, c.footer_verification]
    out += [f"{s.number}{s.accent}: {s.context}" for s in c.stats]
    out += [f"{o.headline}. {o.body}" for o in c.opportunities + c.pipeline]
    for group in (c.news_funding, c.news_threat, c.news_agency, c.news_market):
        out += [f"{n.headline}: {n.why}" for n in group]
    return out


def reconcile_section01_strict(c: CaptureBriefContent, live_report) -> list[str]:
    """Section-01 reconciliation against the strict NOTICE allowlist (Cycle 4,
    2026-07-12). Empty list = clean.

    Under a CURRENT strict projection every composed live opportunity must
    join an ACTIONABLE ledger record exactly: its notice id must be a strict
    BID_NOW+PURSUE record's notice id and its URL one of that record's NOTICE
    evidence URLs. Legacy triage, raw sweep rows, cached prose, and composer
    memory can never re-introduce a live card the current ledger does not
    carry. Under INVALID the live lane is held closed, so ANY composed live
    opportunity is a violation. ABSENT or None (legacy truth) reconciles
    nothing. Pipeline entries (Section 04) are program-tier forming windows
    with their own id vocabulary and are deliberately exempt.
    """
    if live_report is None:
        return []
    from agents.assess.live_report import LiveReportState
    if live_report.state == LiveReportState.INVALID:
        return [
            "strict live lane is held closed; composed opportunity "
            f"'{o.headline[:40]}' ({o.notice_id}) cannot ship"
            for o in c.opportunities
        ]
    if live_report.state != LiveReportState.CURRENT:
        return []
    allowed: dict[str, set] = {}
    for projected in live_report.actionable:
        record = projected.record
        allowed.setdefault(record.notice_id, set()).update(
            str(evidence.source_url)
            for evidence in record.authoritative_evidence)
    problems: list[str] = []
    for o in c.opportunities:
        urls = allowed.get(o.notice_id)
        if urls is None:
            problems.append(
                f"opportunity '{o.headline[:40]}' cites notice id "
                f"{o.notice_id}, which is outside the strict NOTICE allowlist "
                "for the current run")
        elif o.url not in urls:
            problems.append(
                f"opportunity '{o.headline[:40]}' URL does not match the "
                f"strict NOTICE evidence recorded for {o.notice_id}")
    return problems


def qa_capture_brief(c: CaptureBriefContent, pack: Optional[FactPack] = None) -> list[str]:
    """Deterministic gates. Empty list = clean.

    With the FactPack in hand, also enforces the money-in-motion floor: enough
    cited dollar facts and no budget bars is a compose miss, not a choice —
    and the superlative gate: 'top/largest/#1' beside a citation whose fact
    states a lower rank fails the build (the composer never out-ranks a fact)."""
    problems: list[str] = []
    if pack is not None:
        for text in _composed_strings(c):
            for sentence in re.split(r"(?<=[.;])\s+", text):
                if not _SUPERLATIVE_RE.search(sentence):
                    continue
                for fid in re.findall(r"\[(F\d+)\]", sentence):
                    f = pack.get(fid)
                    m = _CITED_RANK_RE.search(f.text) if f else None
                    # stating the fact's true rank in the same sentence clears
                    # it: the superlative then belongs to another claim
                    if m and m.group(0).split("ranked ")[-1] not in sentence:
                        problems.append(
                            f"superlative contradicts cited rank: '{sentence[:80]}' "
                            f"cites {fid}, which states '{m.group(0)}'")
    if pack is not None and not c.budget_bars:
        n = dollar_fact_count(pack)
        if n >= BUDGET_BARS_MIN_DOLLAR_FACTS:
            problems.append(
                f"no budget bars despite {n} cited dollar facts in the pack "
                f"(threshold {BUDGET_BARS_MIN_DOLLAR_FACTS}) — money-in-motion chart missing"
            )
    for o in c.opportunities:
        if not o.url.startswith("http"):
            problems.append(f"opportunity '{o.headline[:40]}' has no source URL")
        if o.days_to_due < 0:
            problems.append(f"opportunity '{o.headline[:40]}' is past due")
    for group in (c.news_funding, c.news_threat, c.news_agency, c.news_market):
        for n in group:
            if not n.url.startswith("http"):
                problems.append(f"news item '{n.headline[:40]}' has no source URL")
    # zero opportunities is a VALID outcome (nothing pursue-grade this cycle) —
    # a report still ships. The runway check only bites when there ARE
    # opportunities but every one is already past its window.
    if c.opportunities and not any(o.days_to_due > 0 for o in c.opportunities):
        problems.append("opportunities present but none forward-dated — no runway")
    return problems
