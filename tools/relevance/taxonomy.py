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

from pydantic import BaseModel, Field

from tools.relevance.scope import EngagementScope

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TaxonomyTerm(BaseModel):
    """One capability term with its match mode."""

    term: str
    mode: Literal["exact_phrase", "stemmed", "acronym"] = "stemmed"
    expansion_context: list[str] = Field(
        default_factory=list,
        description="acronym mode only: at least one of these words must "
                    "co-occur in the same text for the acronym to count")
    note: str = ""


class KillRule(BaseModel):
    """A known false-positive shape. A firing rule kills capability matches
    in its span window (scope='span') or the whole record (scope='record')."""

    term: str
    scope: Literal["span", "record"] = "span"
    reason: str = ""


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
    code_universe: Optional[CodeUniverse] = None
    engagement_scope: Optional[EngagementScope] = Field(
        default=None,
        description="the in-scope agency universe; absent means "
                    "UNSCOPED, never a guessed default")


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
