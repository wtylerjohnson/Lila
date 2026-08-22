"""Term tiers: how much evidence a search term has to bring with it.

THE PROBLEM. Brand-name lanes had the steelhead-trout problem, solved by
requiring the vendor near an ambiguous product name. Contracting-officer
language is generic BY DESIGN, so polysemy is worse, not better. Measured on
the 2026-07-28 extract, screening Riverbed's frame against 78,553 active
notices, twelve rows came back and four of the six distinct notices were the
wrong domain entirely:

    "network management"  -> a State Department health-insurance PROVIDER
                             NETWORK in Bishkek (NAICS 524114)
    "network monitoring"  -> Navy substation protection relays, where a
                             "switch" is an electrical switch (NAICS 335314)
    "observability"       -> Navy stealth research, reducing acoustic and
                             electromagnetic emissions (PSC 1905, combat ships)

None of those are search failures. Every one is a correct string match on a
word that means something else in another industry.

THE LADDER. A term earns its independence:

  TIER 1  distinctive names ("Aternity", "SteelCentral")
          match alone. Already guarded by the dictionary check in sam_lanes.

  TIER 2  multi-word procurement phrases ("network performance monitoring")
          require a tech NAICS or PSC on the record. This is the OR-to-AND
          change: the code gate stops being an alternative route in and
          becomes a requirement. It kills every polysemy case above.

  TIER 3  single common words ("observability", "network")
          require a tech code AND a domain anchor within 400 characters,
          reusing the proximity discipline built for sam_lanes.grade().

Guards live in shared reference data, one file per kind of fact: negative
context comes from data/reference/polysemy_corpus.json (one entry per
witnessed notice, provenance mandatory), anchors and phrase lists from
data/reference/screen_guards.json. Both are shared by every client, because
"provider network is not IT" is a fact about English rather than an
engagement decision. Nothing here is a gate value.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Iterable, Optional

GUARDS_PATH = (Path(__file__).resolve().parents[2]
               / "data" / "reference" / "screen_guards.json")
CORPUS_PATH = (Path(__file__).resolve().parents[2]
               / "data" / "reference" / "polysemy_corpus.json")

TIER_DISTINCTIVE = 1
TIER_PHRASE = 2
TIER_COMMON = 3


@functools.lru_cache(maxsize=4)
def corpus_negative_phrases(path: Optional[str] = None) -> list[str]:
    """Negative-context strings from the polysemy corpus, in entry order.

    Only entries a guard may consume contribute: the entry must name this
    consumer, carry the notice_id that witnessed the collision, and not be
    status 'unproven'. Transcript-only evidence therefore never reaches the
    screen; the provenance rule lives in the data, and this loader is the
    enforcement point (pinned by tests/test_polysemy_corpus.py).
    """
    try:
        data = json.loads(
            Path(path or CORPUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for entry in data.get("entries") or []:
        guard = entry.get("guard") or {}
        if "term_tiers.negative_context" not in (guard.get("consumers") or []):
            continue
        if entry.get("status") == "unproven":
            continue
        if not str(entry.get("notice_id") or "").strip():
            continue
        phrase = str(guard.get("phrase") or "").casefold()
        if phrase and phrase not in out:
            out.append(phrase)
    return out


@functools.lru_cache(maxsize=1)
def load_guards(path: Optional[str] = None,
                corpus_path: Optional[str] = None) -> dict:
    """Shared guards, or empty ones. A missing file weakens screening; it
    never fails a press."""
    try:
        data = json.loads(Path(path or GUARDS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"negative": corpus_negative_phrases(corpus_path),
                "anchors": [], "proximity": 400,
                "vendor_phrases": [], "attributes": []}
    return {
        "negative": corpus_negative_phrases(corpus_path),
        "anchors": [str(t).casefold()
                    for t in (data.get("domain_anchors") or {}).get("terms", [])],
        "proximity": int((data.get("domain_anchors") or {}).get(
            "proximity_chars", 400)),
        "vendor_phrases": [str(p).casefold() for p in
                           (data.get("vendor_category_phrases") or {}).get(
                               "phrases", [])],
        "attributes": [str(p).casefold() for p in
                       (data.get("attribute_not_capability") or {}).get(
                           "phrases", [])],
    }


def tier_of(term: str, *, distinctive_names: Iterable[str] = ()) -> int:
    """Which tier a frame term belongs to."""
    text = " ".join(str(term or "").split())
    if not text:
        return TIER_COMMON
    if text.casefold() in {str(n).casefold() for n in distinctive_names}:
        return TIER_DISTINCTIVE
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", text) if t]
    if len(tokens) >= 2:
        return TIER_PHRASE
    return TIER_COMMON


def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
    if len(needle) < 3:
        return []
    return [(m.start(), m.end()) for m in re.finditer(
        r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])",
        text, re.I)]


def negative_context_hit(text: str, span: tuple[int, int],
                         guards: Optional[dict] = None) -> Optional[str]:
    """The disqualifying phrase sitting near this match, if any."""
    g = guards or load_guards()
    window = g["proximity"]
    lo, hi = max(0, span[0] - window), min(len(text), span[1] + window)
    around = text[lo:hi].casefold()
    for phrase in g["negative"]:
        if phrase and phrase in around:
            return phrase
    return None


def anchor_near(text: str, span: tuple[int, int],
                guards: Optional[dict] = None) -> Optional[str]:
    """The IT-domain anchor near this match, if any."""
    g = guards or load_guards()
    window = g["proximity"]
    lo, hi = max(0, span[0] - window), min(len(text), span[1] + window)
    around = text[lo:hi].casefold()
    for anchor in g["anchors"]:
        if anchor and anchor in around:
            return anchor
    return None


def evaluate_term(term: str, text: str, *, has_tech_code: bool,
                  distinctive_names: Iterable[str] = (),
                  guards: Optional[dict] = None) -> Optional[dict]:
    """Does this term legitimately match this notice? None when it does not.

    Returns the reasoning as well as the verdict, so a rejected match can be
    explained rather than silently dropped.
    """
    g = guards or load_guards()
    spans = _find_all(text, term)
    if not spans:
        return None
    tier = tier_of(term, distinctive_names=distinctive_names)
    for span in spans:
        blocked = negative_context_hit(text, span, g)
        if blocked:
            continue
        if tier == TIER_DISTINCTIVE:
            return {"term": term, "tier": tier, "why": "distinctive name"}
        if not has_tech_code:
            continue                    # tiers 2 and 3 both require the code
        if tier == TIER_PHRASE:
            return {"term": term, "tier": tier,
                    "why": "procurement phrase on a tech-coded record"}
        anchor = anchor_near(text, span, g)
        if anchor:
            return {"term": term, "tier": tier,
                    "why": f"common word on a tech-coded record, anchored by "
                           f"{anchor!r}"}
    # every occurrence failed a guard; say which one, for the receipt
    first_block = negative_context_hit(text, spans[0], g)
    return None if not first_block else {
        "term": term, "tier": tier, "rejected": True,
        "why": f"negative context {first_block!r}"}


def screen_terms_tiered(text: str, terms: Iterable[str], *,
                        has_tech_code: bool,
                        distinctive_names: Iterable[str] = (),
                        guards: Optional[dict] = None) -> list[dict]:
    """Every term that legitimately matches, with its reasoning."""
    out = []
    for term in terms:
        verdict = evaluate_term(term, text, has_tech_code=has_tech_code,
                                distinctive_names=distinctive_names,
                                guards=guards)
        if verdict and not verdict.get("rejected"):
            out.append(verdict)
    return out
