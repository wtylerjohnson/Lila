"""Per-client capability taxonomy: versioned, human-editable, three tiers.

The taxonomy is the client's capability vocabulary as an explicit,
reviewable artifact at clients/<slug>/capability_taxonomy.json (beside the
capability profile). CORE terms are the client's own capability language
and the only path to relevance. ADJACENT terms contribute but are never
sufficient alone. EXCLUDE terms are kill-rules for known false-positive
shapes (a hardware bill of materials is not an SBOM market; a biological
SBOM collision is not software).

Match modes per term:
  exact_phrase · case-insensitive phrase on word boundaries, verbatim.
  stemmed      · light suffix-stemming per token ('reachability
                 analyses' matches 'reachability analysis').
  acronym      · uppercase token that ALSO requires one of its expansion
                 context words in the same text (standalone SCA is Service
                 Component Architecture or Sudden Cardiac Arrest in
                 federal text; software/composition co-occurrence is what
                 makes it ours).

Versioning: the integer version bumps on every human edit; every scored
verdict and every rendered deliverable's INTERNAL trail records the
version that scored it. The file itself is git-versioned.
"""
from __future__ import annotations

import json
import os
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from tools.relevance.scope import EngagementScope

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PhraseAlias(BaseModel):
    """Reviewed buyer wording for one concept, never another scoring vote."""

    phrase: str = Field(min_length=1)
    context_any: list[str] = Field(default_factory=list)
    note: str = ""


class CapabilityEvidence(BaseModel):
    evidence_id: str
    url: str
    reviewed_at: str
    basis: str


class RetrievalMapping(BaseModel):
    term: str = Field(min_length=1)
    disposition: Literal["supported", "exploratory", "excluded"]
    capability: Optional[str] = None
    reason: str = Field(min_length=1)


class TaxonomyTerm(BaseModel):
    """One capability term with its match mode."""

    term: str
    mode: Literal["exact_phrase", "stemmed", "acronym"] = "stemmed"
    expansion_context: list[str] = Field(
        default_factory=list,
        description="acronym mode only: at least one of these words must "
                    "co-occur in the same text for the acronym to count")
    note: str = ""
    aliases: list[PhraseAlias] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class KillRule(BaseModel):
    """A known false-positive shape. A firing rule kills capability matches
    in its span window (scope='span') or the whole record (scope='record')."""

    term: str
    scope: Literal["span", "record"] = "span"
    reason: str = ""
    category: Literal["identity", "functional"] = "identity"


class CodeUniverse(BaseModel):
    """The client's code boundary: strictly a PRE-FILTER that may exclude.
    A code inside the universe contributes zero relevance; a record outside
    it needs strong capability evidence to pass, flagged OFF-CODE SIGNAL."""

    naics: list[str] = Field(default_factory=list)
    psc: list[str] = Field(default_factory=list)


class CapabilityTaxonomy(BaseModel):
    client_name: str
    version: int = Field(ge=1)
    updated: str = Field(description="ISO date of the last human edit")
    core: list[TaxonomyTerm] = Field(default_factory=list)
    adjacent: list[TaxonomyTerm] = Field(default_factory=list)
    exclude: list[KillRule] = Field(default_factory=list)
    evidence: list[CapabilityEvidence] = Field(default_factory=list)
    retrieval_mappings: list[RetrievalMapping] = Field(default_factory=list)
    code_universe: Optional[CodeUniverse] = None
    engagement_scope: Optional[EngagementScope] = Field(
        default=None,
        description="the in-scope agency universe; absent means "
                    "UNSCOPED, never a guessed default")

    @model_validator(mode="after")
    def validate_reviewed_vocabulary(self):
        terms = {t.term: t for t in [*self.core, *self.adjacent]}
        evidence = {e.evidence_id for e in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("duplicate capability evidence identity")
        for term in terms.values():
            if any(i not in evidence for i in term.evidence_ids):
                raise ValueError("unknown capability evidence identity")
            if term.aliases and not term.evidence_ids:
                raise ValueError("buyer-language aliases require capability evidence")
            if any(not a.phrase.strip() or any(not c.strip() for c in a.context_any)
                   for a in term.aliases):
                raise ValueError("empty buyer-language alias or context")
        seen = set()
        for mapping in self.retrieval_mappings:
            key = mapping.term.strip().casefold()
            if not key or key in seen:
                raise ValueError("empty or duplicate retrieval mapping")
            seen.add(key)
            if mapping.capability is not None and mapping.capability not in terms:
                raise ValueError("unknown mapped capability")
            if mapping.disposition == "supported" and (
                    mapping.capability is None or not terms[mapping.capability].evidence_ids):
                raise ValueError("supported retrieval mapping requires evidenced capability")
        return self


def retrieval_vocabulary(taxonomy: CapabilityTaxonomy) -> list[str]:
    """One definition for retrieval and matching; context is checked at match time.

    Exploratory entries retrieve only. This never edits the operator strategy.
    Existing taxonomies without reviewed mappings retain their original vocabulary.
    """
    values = []
    for term in [*taxonomy.core, *taxonomy.adjacent]:
        values.extend([term.term, *(a.phrase for a in term.aliases)])
    values.extend(m.term for m in taxonomy.retrieval_mappings
                  if m.disposition != "excluded")
    out = []
    seen = set()
    for value in values:
        key = value.strip().casefold()
        if key not in seen:
            out.append(value.strip())
            seen.add(key)
    return out


def vocabulary_receipt(taxonomy: CapabilityTaxonomy,
                       query_terms: list[str]) -> dict:
    """Audit actual wire terms, including operator terms outside the mapping."""
    import hashlib
    payload = taxonomy.model_dump(mode="json")
    known = {m.term.casefold(): m.model_dump() for m in taxonomy.retrieval_mappings}
    for term in [*taxonomy.core, *taxonomy.adjacent]:
        for phrase in [term.term, *(a.phrase for a in term.aliases)]:
            known.setdefault(phrase.casefold(), {
                "term": phrase, "disposition": "supported" if term.evidence_ids else "legacy",
                "capability": term.term, "reason": "Taxonomy phrase; match context rules still apply."})
    return {
        "taxonomy_client": taxonomy.client_name, "taxonomy_version": taxonomy.version,
        "definition_sha256": hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "wire_terms": [known.get(t.casefold(), {
            "term": t, "disposition": "unmapped_operator_term", "capability": None,
            "reason": "Retrieval only; no inferred capability or gate change."}) for t in query_terms],
    }


def taxonomy_path(client_name: str) -> str:
    from tools.capability import _slug
    return os.path.join(_ROOT, "clients", _slug(client_name),
                        "capability_taxonomy.json")


def load_taxonomy(client_name: str) -> Optional[CapabilityTaxonomy]:
    """The client's taxonomy file, schema-validated; None when absent."""
    path = taxonomy_path(client_name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return CapabilityTaxonomy(**json.load(f))


def derived_taxonomy(client_name: str) -> Optional[CapabilityTaxonomy]:
    """Version-0-style fallback DERIVED from the capability profile for
    clients without a curated taxonomy yet: profile core/adjacent terms as
    stemmed matches, no kill-rules, no code universe. Marked by version=1
    with a DERIVED note; calibration output names the derivation so a
    human knows the vocabulary was not curated for the engine."""
    from tools.capability import load_profile
    profile = load_profile(client_name)
    if profile is None:
        return None
    terms = getattr(profile, "capability_terms", None)
    if terms is None:
        return None
    return CapabilityTaxonomy(
        client_name=client_name,
        version=1,
        updated="1970-01-01",
        core=[TaxonomyTerm(term=t, mode="stemmed",
                           note="DERIVED from capability profile")
              for t in (terms.core or [])],
        adjacent=[TaxonomyTerm(term=t, mode="stemmed",
                               note="DERIVED from capability profile")
                  for t in (terms.adjacent or [])],
    )
