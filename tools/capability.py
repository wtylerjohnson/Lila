"""Client capability profile — the PRIMARY evidence layer (2026-07-09).

Architectural inversion, permanent: capability keywords are the primary
evidence for teaming doors, recompete relevance, and thread scoring; NAICS
codes are a coarse boundary filter ONLY. NAICS marks where a contract is
filed, not what a vendor sells — lane-level findings are client-invariant
and therefore not client intelligence.

The profile is a REQUIRED first-class object at clients/<slug>/profile.json
(JSON, not YAML: the repo is JSON-native and carries no yaml dependency; the
schema is exactly the operator spec's). A sweep refuses to run without a
populated profile. Report builds over legacy artifacts degrade gracefully
with mandatory labels instead of blocking.

Scoring (operator spec): capability-term match core=3 / adjacent=1, plus
mission-component bonus +2; an excluded-term hit vetoes the record to 0.
Chips: STRONG >= 4, MODERATE == 3, EARLY 1-2. The score is stored on the
fact record so the report can cite it.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _ui_url() -> str:
    """Where the Control Room ACTUALLY is.

    This message hardcoded 8321. The port is overridable via LILA_UI_PORT and
    the dev harness does override it, so a human following this text went to
    a dead port while the server ran elsewhere. It now reports the running
    port. The DEFAULT stays 8321, which is what run_ui.py, .claude/launch.json
    and CLAUDE.md already agree on.
    """
    return f"http://127.0.0.1:{os.environ.get('LILA_UI_PORT', '8321')}"



ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENTS_DIR = os.path.join(ROOT, "clients")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


class CapabilityTerms(BaseModel):
    core: list[str] = Field(default_factory=list,
                            description="terms that describe the product itself")
    adjacent: list[str] = Field(default_factory=list,
                                description="buyer-behavioral adjacency terms")
    excluded: list[str] = Field(default_factory=list,
                                description="terms that false-positive into the lanes")


class ClientProfile(BaseModel):
    client_name: str
    display_name: Optional[str] = Field(
        default=None,
        exclude=True,
        description=(
            "optional client-facing capitalization; identity and paths still "
            "come from client_name"
        ),
    )
    capability_terms: CapabilityTerms
    named_competitors_and_incumbents: list[str] = Field(default_factory=list)
    mission_components: list[str] = Field(
        default_factory=list,
        description="agency components with the relevant mission; WEIGHT results, never exclude")
    naics_boundary: list[str] = Field(
        default_factory=list,
        description="coarse boundary filter only; lane totals are context, never evidence")

    @field_validator("display_name")
    @classmethod
    def _clean_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("profile display_name cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def _display_name_is_the_same_client(self):
        if self.display_name and _slug(self.display_name) != _slug(
                self.client_name):
            raise ValueError(
                "profile display_name must preserve the client_name slug")
        return self

    def is_populated(self) -> bool:
        return bool(self.capability_terms.core and self.naics_boundary)


class ProfileMissing(RuntimeError):
    """No profile, no sweep. Generic sweeps are the failure mode being killed."""


def profile_path(client_name: str) -> str:
    return os.path.join(CLIENTS_DIR, _slug(client_name), "profile.json")


def load_profile(client_name: str) -> Optional[ClientProfile]:
    path = profile_path(client_name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return ClientProfile(**json.load(f))


def client_display_name(
    client_name: str,
    *,
    profile: Optional[ClientProfile] = None,
) -> str:
    """Presentation spelling without creating a second client identity.

    ``client_name`` remains the review, sweep, receipt, and artifact identity.
    A profile may refine capitalization only when the requested name, profile
    identity, and presentation spelling all resolve to the same slug.
    """
    owned = profile or load_profile(client_name)
    if owned is None:
        return client_name
    if _slug(owned.client_name) != _slug(client_name):
        raise ValueError(
            "profile client_name does not match the requested client")
    return owned.display_name or owned.client_name


def require_profile(client_name: str) -> ClientProfile:
    """The sweep-side gate: refuse to run without a populated profile."""
    p = load_profile(client_name)
    if p is None or not p.is_populated():
        raise ProfileMissing(
            f"no populated capability profile at {profile_path(client_name)} "
            "(capability_terms.core and naics_boundary are required). "
            "No profile, no sweep: a NAICS-only sweep produces client-"
            "invariant findings. Onboard this client in the Control Room: "
            f"open {_ui_url()} and press + New Client (profile, "
            "capability taxonomy, and engagement scope, validated on save).")
    return p


def scaffold_profile(client_name: str) -> str:
    """Derive a DRAFT profile from the human-approved strategy: capability
    keywords -> core, technology/search terms -> adjacent, agency terms ->
    mission components, strategy NAICS -> boundary. Named competitor
    products and excluded terms are the operator's subject knowledge; the
    draft leaves loud placeholders. Never overwrites an existing profile."""
    path = profile_path(client_name)
    if os.path.exists(path):
        raise FileExistsError(f"profile already exists: {path}")
    review = os.path.join(ROOT, "data", "review",
                          f"{_slug(client_name)}.review.json")
    if not os.path.exists(review):
        raise FileNotFoundError(
            f"no approved strategy at {review}; run intake/approval first")
    with open(review, encoding="utf-8") as f:
        strategy = (json.load(f).get("strategy") or {})
    core, adjacent, mission, naics = [], [], [], []
    for k in strategy.get("keywords") or []:
        term = str(k.get("term") or "").strip()
        cat = str(k.get("category") or "")
        cat = getattr(cat, "value", cat)
        for part in term.split("/"):
            part = part.split("(")[0].strip(" .,;:-")
            if not part:
                continue
            if cat == "capability":
                core.append(part)
            elif cat in ("technology", "search_term"):
                adjacent.append(part)
            elif cat == "agency":
                mission.append(part)
            elif cat == "naics":
                naics.append(part)
    profile = ClientProfile(
        client_name=client_name,
        capability_terms=CapabilityTerms(
            core=core, adjacent=adjacent,
            excluded=["TODO: terms that false-positive in these lanes"]),
        named_competitors_and_incumbents=[
            "TODO: competitor/incumbent product names that mark a relevant buyer"],
        mission_components=mission,
        naics_boundary=naics,
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile.model_dump(mode="json"), f, indent=2)
    return path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="capability profile tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scaffold", help="draft a profile from the approved strategy")
    sc.add_argument("--client", required=True)
    args = ap.parse_args()
    if args.cmd == "scaffold":
        out = scaffold_profile(args.client)
        print(f"draft profile -> {out}")
        print("Review it before sweeping: fill named_competitors_and_"
              "incumbents and excluded (the TODO markers), then re-run "
              "the sweep.")


def _term_rx(term: str) -> re.Pattern:
    # word-boundary phrase match, case-insensitive; '/'-compounds split upstream
    return re.compile(r"\b" + re.escape(term.strip().lower()).replace(r"\ ", r"\s+")
                      + r"\b", re.I)


def term_regex(term: str) -> re.Pattern:
    """Public boundary-aware matcher shared by strict evidence adapters."""
    return _term_rx(term)


class TermMatch(BaseModel):
    score: int
    tier: str  # "core" | "adjacent" | "excluded" | "none"
    matched: list[str] = Field(default_factory=list)
    mission_hit: str = ""

    @property
    def chip(self) -> str:
        if self.score >= 4:
            return "STRONG"
        if self.score == 3:
            return "MODERATE"
        if self.score >= 1:
            return "EARLY"
        return "NONE"


def match_capability(profile: ClientProfile, *texts: str,
                     component: str = "") -> TermMatch:
    """Score one record's text against the profile. Deterministic, citable:
    the matched phrases ride back for inline evidence rendering."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob.strip():
        return TermMatch(score=0, tier="none")
    for term in profile.capability_terms.excluded:
        if term and _term_rx(term).search(blob):
            return TermMatch(score=0, tier="excluded", matched=[term])
    hits_core = [t for t in profile.capability_terms.core
                 if t and _term_rx(t).search(blob)]
    hits_adj = [t for t in profile.capability_terms.adjacent
                if t and _term_rx(t).search(blob)]
    base = 3 if hits_core else (1 if hits_adj else 0)
    tier = "core" if hits_core else ("adjacent" if hits_adj else "none")
    comp_blob = (component or "") + " " + blob
    mission = next((mc for mc in profile.mission_components
                    if mc and _term_rx(mc).search(comp_blob.lower())), "")
    score = base + (2 if (mission and base) else 0)
    return TermMatch(score=score, tier=tier,
                     matched=hits_core + hits_adj, mission_hit=mission)


def incumbent_hits(profile: ClientProfile, *texts: str) -> list[str]:
    """Named competitor/incumbent products present in the text — the layer
    that maps buyer -> incumbent product -> contracting office."""
    blob = " ".join(t for t in texts if t).lower()
    return [n for n in profile.named_competitors_and_incumbents
            if n and _term_rx(n).search(blob)]


def fit_trace(profile: ClientProfile, notice: dict,
              screen_reason: str = "") -> dict:
    """SHOW THE WORK (2026-07-10): per-opportunity provenance the client can
    inspect — which capability terms matched, whether the NAICS code sits in
    the boundary, which mission component weighted it, and the screen
    inference. Deterministic; renders under every opportunity card, because
    shown work builds trust."""
    rp = (notice or {}).get("raw_payload") or {}
    text = f"{notice.get('title') or ''} {rp.get('description_snippet') or ''}"
    comp = f"{rp.get('subtier') or ''} {rp.get('agency') or notice.get('agency') or ''}"
    m = match_capability(profile, text, component=comp)
    naics = str(notice.get("naics_code") or rp.get("naics") or "")
    return {
        "matched_terms": m.matched[:6], "tier": m.tier,
        "score": m.score, "chip": m.chip,
        "mission_component": m.mission_hit,
        "naics": naics,
        "naics_in_boundary": naics in set(profile.naics_boundary),
        "screen_inference": (screen_reason or "").strip()[:220],
    }
