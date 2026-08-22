"""Intake profiling layer (Step 1, Claude).

Reads the client's intake submission + the website scrape + the general company
web research (agents/company_research.py) and produces an IntakeStrategy: inferred
NAICS, categorized keywords, an opportunity-pursuit strategy, and at minimum three
concrete search specs (sam.gov, usaspending.gov, web).

This output is NOT executed automatically — it is handed to a human Review Gate.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field, model_validator

from agents.decisions.engine import DecisionEngine
from agents.decisions.schemas import IntakeStrategy, TermCandidate
from agents.schemas import IntakeSubmission
from tools.scrape.site import ScrapeBundle

if TYPE_CHECKING:  # avoid import cycle at runtime; only needed for typing
    from agents.company_research import CompanyResearch

LAYER = "intake"

SYSTEM_PROMPT = """\
You are the intake/profiling layer of a LILA. You are given
a client's intake form, a scrape of their website, and general web research about the
company (news, awards, certifications, federal footprint — with citation URLs). Read
and UNDERSTAND what this company does, then produce a pursuit strategy and the search
plan to find federal opportunities for them.

Produce:
- pursuit_strategy: a concise opportunity-pursuit strategy for THIS client — where
  their federal opportunity is, how they should position, prime vs. teaming, set-aside
  leverage.
- inferred_naics: the NAICS codes their work maps to (infer from services even if the
  form omits them; include any the form provided).
- naics_meta: exactly one record for every inferred_naics code. Give each code its
  official/local title when known, classify its role as core or boundary, set
  origin='system', leave note blank, and write a concrete evidence-grounded rationale
  explaining why THIS client's work belongs in that NAICS search lane. The naics_meta
  code set and order must exactly match inferred_naics; never emit an unexplained code.
- keywords: categorized terms that will drive the searches — capability, technology,
  agency, set_aside, naics, and free-text search_term. Each with a short rationale.
- target_agencies and set_aside_angles grounded in what they do and their certifications.
- research_entities: the PROPER NAMES the research surfaced, each with kind and a
  one-line evidence-grounded rationale (plus the source URL when you have one):
    * kind='product'    — the client's own named product lines / platforms, spelled
      exactly as a federal award description or reseller catalog would spell them.
    * kind='competitor' — named rival companies selling into the client's market.
    * kind='reseller'   — reseller / distributor / channel partners known to carry
      the client's products into government (e.g. from partner pages or award text).
  These names drive an award-description retrieval lane, so exact spelling matters
  and generic phrases do not belong here (those are keywords). Only names the form,
  scrape, or web research actually evidences — an empty list is the correct output
  when research surfaced none; never pad or guess.
- near_misses: the keywords and NAICS codes you CONSIDERED but cut, 4-10 entries, each
  with kind ('keyword' or 'naics'), the category it would join, ONE line covering why it
  was close AND why it lost, and a 0-1 confidence. These surface to the human as a
  promotable-candidate tray — the human often knows the lane better than the scrape.
  Real considerations only; never pad the tray.
- searches: AT MINIMUM one SearchSpec each for source "sam.gov", "usaspending.gov",
  and "web", with the specific query_terms / naics_codes / set_asides each should use
  and why.

Rules:
- GROUND everything in the provided form + scrape. Do not invent capabilities the
  client doesn't show evidence of. Populate sources_reviewed with the URLs/inputs used.
- If company_web_research is null, empty, or carries web_research_cited=false, the
  rival/channel evidence is MISSING, not researched-empty: research_entities may then
  be product-only, but your review_gate MUST name the gap explicitly (the reviewer has
  to know the competitor and reseller sides are unpopulated for lack of evidence and
  should re-run intake research or supply rivals at the gate). Never present a
  product-only entity list as a researched competitive landscape.
- This is a recommendation for human approval: set requires_human_review=true and write
  a precise review_gate describing what the human must approve before searches run.
"""


class GeneratedIntakeStrategy(IntakeStrategy):
    """Strict output contract for a newly generated intake strategy.

    Stored ``IntakeStrategy`` packets remain additive and legacy-readable, including
    packets created before ``naics_meta`` existed. New inference has a higher bar:
    every authoritative boundary code must arrive with its own grounded explanation.
    """

    @model_validator(mode="after")
    def _every_naics_code_has_metadata(self) -> "GeneratedIntakeStrategy":
        codes = list(self.inferred_naics)
        if not codes:
            raise ValueError("new intake output must infer at least one NAICS code")
        if len(set(codes)) != len(codes):
            raise ValueError("new intake output must not duplicate inferred NAICS codes")
        meta_codes = [entry.code for entry in self.naics_meta]
        if meta_codes != codes:
            raise ValueError(
                "new intake output must provide one ordered naics_meta entry "
                "for every inferred_naics code"
            )
        missing = [
            entry.code
            for entry in self.naics_meta
            if len(entry.rationale.strip()) < 30
            or len(entry.rationale.split()) < 5
        ]
        if missing:
            raise ValueError(
                "new intake output must explain every inferred NAICS code: "
                + ", ".join(missing)
            )
        normalized_rationales = [
            " ".join(entry.rationale.split()).casefold()
            for entry in self.naics_meta
        ]
        if len(set(normalized_rationales)) != len(normalized_rationales):
            raise ValueError(
                "new intake output must provide a distinct explanation for each "
                "inferred NAICS code"
            )
        wrong_origin = [entry.code for entry in self.naics_meta
                        if entry.origin != "system"]
        if wrong_origin:
            raise ValueError(
                "new intake NAICS metadata must begin with system provenance: "
                + ", ".join(wrong_origin)
            )
        noted = [entry.code for entry in self.naics_meta if entry.note.strip()]
        if noted:
            raise ValueError(
                "new intake NAICS metadata cannot invent operator notes: "
                + ", ".join(noted)
            )
        if self.kept_out_naics:
            raise ValueError(
                "new intake output cannot invent operator-kept-out NAICS codes"
            )
        if self.kept_out:
            raise ValueError("new intake output cannot invent operator-kept-out keywords")
        edited_keywords = [
            keyword.term
            for keyword in self.keywords
            if keyword.origin != "system"
            or keyword.edited_from is not None
            or bool((keyword.note or "").strip())
        ]
        if edited_keywords:
            raise ValueError(
                "new intake output cannot invent operator keyword history: "
                + ", ".join(edited_keywords)
            )
        if not self.sources_reviewed or any(
                not source.strip() for source in self.sources_reviewed):
            raise ValueError(
                "new intake output must identify the sources used for its recommendations"
            )
        if not self.requires_human_review:
            raise ValueError("new intake output must remain subject to human review")
        blank_entities = [
            entity.kind
            for entity in self.research_entities
            if not entity.name.strip() or not entity.rationale.strip()
        ]
        if blank_entities:
            raise ValueError(
                "new intake research entities need a name and a grounded "
                "rationale; blank: " + ", ".join(blank_entities)
            )
        entity_keys = [
            (entity.kind, " ".join(entity.name.split()).casefold())
            for entity in self.research_entities
        ]
        if len(set(entity_keys)) != len(entity_keys):
            raise ValueError(
                "new intake research entities must not repeat a (kind, name) pair"
            )
        return self


def _submission_context(s: IntakeSubmission) -> dict:
    data = s.model_dump(mode="json")
    return data


def _valid_known_naics(submission: IntakeSubmission) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(code).strip()
        for code in submission.known_naics
        if re.fullmatch(r"[0-9]{6}", str(code).strip())
    ))


def _generated_strategy_schema(
    required_naics: tuple[str, ...],
) -> type[GeneratedIntakeStrategy]:
    """Bind form-provided codes into model validation so supported routes can
    self-correct an omission; every route fails loud rather than fabricating a
    post-model rationale."""

    class SubmissionGeneratedIntakeStrategy(GeneratedIntakeStrategy):
        @model_validator(mode="after")
        def _retains_form_naics(self) -> "SubmissionGeneratedIntakeStrategy":
            omitted = [code for code in required_naics
                       if code not in self.inferred_naics]
            if omitted:
                raise ValueError(
                    "new intake output omitted form-provided NAICS codes: "
                    + ", ".join(omitted)
                )
            return self

    return SubmissionGeneratedIntakeStrategy


def build_strategy(
    submission: IntakeSubmission,
    scrape: Optional[ScrapeBundle] = None,
    engine: Optional[DecisionEngine] = None,
    research: Optional["CompanyResearch"] = None,
) -> IntakeStrategy:
    """Run the profiling layer over the intake form + site scrape + web research."""
    engine = engine or DecisionEngine()
    generated_schema = _generated_strategy_schema(_valid_known_naics(submission))
    context = {
        "intake_form": _submission_context(submission),
        "website_scrape": {
            "root_url": scrape.root_url if scrape else None,
            "sources": scrape.sources if scrape else [],
            "content": scrape.combined_text() if scrape else "",
        },
        "company_web_research": research.context() if research else None,
    }
    result = engine.deliberate(
        layer=LAYER,
        system_prompt=SYSTEM_PROMPT,
        context=context,
        schema=generated_schema,
    )
    # Injected test engines may return the base model directly. Revalidate at
    # this seam so the generation contract cannot be bypassed by transport.
    return generated_schema.model_validate(result.model_dump(mode="json"))


_CANDIDATES_SYSTEM = """\
You review a LOCKED, human-approved federal pursuit strategy and produce ONLY
the near-miss tray: keywords and NAICS codes that are CLOSE to the locked set
but not in it — terms a human operator should consider when refining searches.
Ground every candidate in the provided strategy, intake form, and research;
never invent capabilities. Each entry: kind ('keyword' or 'naics'), the
category it would join, ONE line covering why it is close AND what kept it
out of the locked set, and a 0-1 confidence. 4-10 real considerations; never
pad. Do NOT restate terms already in the locked strategy."""


class CandidateReview(BaseModel):
    client_name: str
    near_misses: list[TermCandidate] = Field(default_factory=list)


def review_candidates(
    strategy: IntakeStrategy,
    submission: Optional[IntakeSubmission] = None,
    research: Optional["CompanyResearch"] = None,
    engine: Optional[DecisionEngine] = None,
) -> list[TermCandidate]:
    """ADDITIVE near-miss pass for an already-locked strategy: the locked
    fields are context, never touched; the tray is the only output. Used to
    retrofit clients whose inference predates the near-miss schema."""
    engine = engine or DecisionEngine()
    context = {
        "locked_strategy": strategy.model_dump(mode="json"),
        "intake_form": _submission_context(submission) if submission else None,
        "company_web_research": research.context() if research else None,
    }
    out = engine.deliberate(
        layer="intake-candidates",
        system_prompt=_CANDIDATES_SYSTEM,
        context=context,
        schema=CandidateReview,
    )
    locked = {k.term.lower() for k in strategy.keywords} | {
        c.lower() for c in strategy.inferred_naics}
    return [c for c in out.near_misses if c.value.lower() not in locked]
