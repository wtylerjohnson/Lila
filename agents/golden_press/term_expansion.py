"""Widening a capability term into language a contracting officer wrote.

THE MEASUREMENT THAT FORCED THIS (2026-08-06). Every one of apexanalytix's
eight approved core terms matched ZERO notices in a 330,641-notice store:

    supplier onboarding             0        onboarding          497
    improper payment prevention     0        improper payment     13
    AP recovery audit               0        recovery audit        4
    ERP procure-to-pay integration  0        procure-to-pay        4
    payment fraud prevention        0
    third-party risk management     0
    supplier risk management        0
    bank account validation         0

The vocabulary was not narrow, it was DEAD. The matcher
(term_tiers._find_all) requires the contiguous literal phrase with word
boundaries, so "improper payment prevention" cannot match "prevention of
improper payments" and "supplier onboarding" cannot match "vendor
onboarding". Marketing writes noun stacks; contracting officers do not.

THREE DETERMINISTIC WIDENINGS, in increasing distance from the approved
term. Every variant carries the term it came from and the rule that made
it, so an operator can see exactly why a word entered the search.

  head        drop trailing abstract nouns from a noun stack
              "improper payment prevention" -> "improper payment"
  morphology  singular/plural and hyphen/space forms
              "improper payment" -> "improper payments"
  synonym     federal procurement equivalents from a reference file
              "supplier" -> "vendor", "AP" -> "accounts payable"

WHAT THIS DOES NOT DO. It never invents a concept and never edits the
approved frame. Capability terms are an Analyst Layer value and the
operator owns them (L11); this widens how a term is MATCHED, exactly as the
polysemy corpus tunes how a term is guarded. Every variant is still passed
through the existing tiered screen, so the negative-context guards, the
tech-code qualifier and the distinctive-name ladder all still apply. A
widening cannot admit a record the guards would reject.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

TERM_EXPANSION_VERSION = "term_expansion.v1.2026-08-06"

_ROOT = Path(__file__).resolve().parents[2]
SYNONYMS_ENV = "LILA_TERM_SYNONYMS"
_SYNONYMS_DEFAULT = _ROOT / "data" / "reference" / "term_synonyms.json"

# Abstract nouns that end a marketing noun stack and never appear in the
# solicitation phrasing. Dropping them is what turns a vendor phrase into a
# procurement phrase. Order matters: longest suffix first.
_STACK_TAILS = (
    "prevention", "management", "integration", "optimization", "optimisation",
    "automation", "modernization", "modernisation", "transformation",
    "enablement", "orchestration", "assurance", "governance", "intelligence",
    "analytics", "platform", "solution", "solutions", "capability",
    "capabilities", "services", "service", "software", "tooling", "suite",
)

# A head this short is not a concept, it is a word. "risk", "payment",
# "audit" alone would drag in the whole store, and the tiered screen would
# then be doing all the work with none of the context.
_MIN_HEAD_TOKENS = 2

_cache: dict = {}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _tokens(value: Any) -> list:
    return [t for t in re.split(r"[^A-Za-z0-9]+", _clean(value)) if t]


def load_synonyms() -> dict:
    """Federal procurement equivalents, from the reference file.

    Data, not code, exactly like the persona ladder and the polysemy corpus:
    an operator adds an equivalence without a code change. A missing file
    weakens widening and never fails a press.
    """
    path = Path(os.environ.get(SYNONYMS_ENV, "") or _SYNONYMS_DEFAULT)
    key = str(path)
    if key in _cache:
        return _cache[key]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"groups": []}
    groups = payload.get("groups") or []
    index: dict = {}
    for group in groups:
        words = [_clean(w).casefold() for w in (group.get("equivalent") or [])
                 if _clean(w)]
        for word in words:
            index.setdefault(word, set()).update(w for w in words if w != word)
    _cache[key] = index
    return index


def head_forms(term: str) -> list:
    """Progressively drop trailing abstract nouns from a noun stack."""
    toks = _tokens(term)
    out = []
    while len(toks) > _MIN_HEAD_TOKENS and toks[-1].casefold() in _STACK_TAILS:
        toks = toks[:-1]
        out.append(" ".join(toks))
    return out


def morphology_forms(term: str) -> list:
    """Singular/plural and hyphen/space variants of the SAME words.

    Deliberately crude: federal text says "improper payments" as often as
    "improper payment", and "procure-to-pay" as often as "procure to pay".
    No stemmer, because a stemmer would also produce forms nobody writes.
    """
    base = _clean(term)
    out = set()
    toks = base.split()
    if toks:
        last = toks[-1]
        if last.lower().endswith("s") and len(last) > 3:
            out.add(" ".join(toks[:-1] + [last[:-1]]))
        elif not last.lower().endswith("s"):
            out.add(" ".join(toks[:-1] + [last + "s"]))
    if "-" in base:
        out.add(base.replace("-", " "))
    if " " in base and len(base.split()) <= 4:
        out.add(base.replace(" ", "-"))
    return [f for f in out if _clean(f) and _clean(f).casefold() != base.casefold()]


def synonym_forms(term: str, synonyms: Optional[dict] = None) -> list:
    """Swap ONE span at a time for a federal equivalent.

    SPANS, NOT TOKENS. The equivalence that matters most is usually
    multi-word ("third-party risk" -> "vendor risk", "procure-to-pay" ->
    "purchase to pay"), and a per-token lookup can never fire on those. The
    first build did exactly that and left five of eight terms unrecovered.
    Longest span first, so the most specific equivalence wins.

    One span only. Swapping two at once compounds the distance from the
    approved term and produces phrases the operator never sanctioned.
    """
    index = synonyms if synonyms is not None else load_synonyms()
    toks = _clean(term).split()
    out = []
    for width in range(len(toks), 0, -1):
        for i in range(0, len(toks) - width + 1):
            span = " ".join(toks[i:i + width]).casefold()
            for alt in sorted(index.get(span, ())):
                swapped = toks[:i] + [alt] + toks[i + width:]
                out.append(" ".join(swapped))
    return out


def qualifier_forms(term: str) -> list:
    """Drop a LEADING qualifier that narrows a phrase nobody narrows.

    "AP recovery audit" is how a vendor writes it; a contracting officer
    writes "recovery audit" (4 hits) or "payment recapture". Dropping the
    leading acronym or single qualifier recovers the phrase the officer
    actually used. Only ever drops ONE leading token, and only when what
    remains is still a concept rather than a bare word.
    """
    toks = _tokens(term)
    if len(toks) - 1 < _MIN_HEAD_TOKENS:
        return []
    lead = toks[0]
    # an acronym, or a short qualifier that is not itself the concept
    if len(lead) <= 4 or lead.isupper():
        return [" ".join(toks[1:])]
    return []


def expand_term(term: str, *, synonyms: Optional[dict] = None) -> list:
    """Every deterministic variant of one approved term, with its basis.

    The approved term itself is always first and is never altered. Variants
    are deduped, and each states the rule that produced it so the receipt
    can show why a word is being searched.
    """
    base = _clean(term)
    if not base:
        return []
    index = synonyms if synonyms is not None else load_synonyms()
    rows = [{"term": base, "basis": "approved", "from": base}]
    seen = {base.casefold()}

    def _add(value, basis):
        key = _clean(value).casefold()
        if not key or key in seen or len(key) < 4:
            return
        seen.add(key)
        rows.append({"term": _clean(value), "basis": basis, "from": base})

    heads = head_forms(base)
    for head in heads:
        _add(head, "head: trailing abstract noun dropped")
    quals = []
    for stem in [base] + heads:
        for form in qualifier_forms(stem):
            _add(form, "qualifier: leading acronym dropped")
            quals.append(form)
    # morphology and synonyms apply to the approved term AND to every
    # narrowed form, because the narrowed form is usually the one that
    # actually appears in a solicitation
    for stem in [base] + heads + quals:
        for form in morphology_forms(stem):
            _add(form, "morphology")
        for form in synonym_forms(stem, index):
            _add(form, "synonym")
    return rows


def expand_vocabulary(terms: Any, *,
                      synonyms: Optional[dict] = None) -> dict:
    """Expand a whole capability frame. Returns the search list and a receipt.

    The receipt is the point: it names how many variants each approved term
    produced, so a frame whose every term is dead is visible as a frame
    problem rather than presenting as an empty market.
    """
    index = synonyms if synonyms is not None else load_synonyms()
    rows: list = []
    per_term: dict = {}
    seen: set = set()
    for term in (terms or []):
        expanded = expand_term(term, synonyms=index)
        kept = []
        for row in expanded:
            key = row["term"].casefold()
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            kept.append(row["term"])
        if _clean(term):
            per_term[_clean(term)] = kept
    return {
        "version": TERM_EXPANSION_VERSION,
        "terms": [r["term"] for r in rows],
        "rows": rows,
        "per_term": per_term,
        "approved_count": len([t for t in (terms or []) if _clean(t)]),
        "expanded_count": len(rows),
    }


def measure_yield(expansion: dict, *, conn) -> dict:
    """How many stored notices each variant actually reaches. Zero network.

    THE RECEIPT THAT WOULD HAVE CAUGHT THE INCIDENT. An approved frame whose
    every term yields zero is a dead frame, and this states it as a frame
    finding rather than letting it present as an absent market.
    """
    if conn is None:
        return {"attempted": False, "why": "no notice store available"}
    per: dict = {}
    for row in (expansion or {}).get("rows") or []:
        term = row["term"]
        hits = conn.execute(
            "SELECT COUNT(*) n FROM notices WHERE title LIKE ? "
            "OR description_prefix LIKE ?",
            (f"%{term}%", f"%{term}%")).fetchone()["n"]
        per[term] = {"hits": hits, "basis": row["basis"], "from": row["from"]}
    approved = {t: per.get(t, {}).get("hits", 0)
                for t in (expansion.get("per_term") or {})}
    dead = sorted(t for t, n in approved.items() if not n)
    recovered = {}
    for term, variants in (expansion.get("per_term") or {}).items():
        if approved.get(term):
            continue
        gained = sum(per.get(v, {}).get("hits", 0) for v in variants
                     if v.casefold() != term.casefold())
        if gained:
            recovered[term] = gained
    return {
        "attempted": True,
        "per_variant": per,
        "approved_term_hits": approved,
        "dead_approved_terms": dead,
        "recovered_by_widening": recovered,
        "frame_is_dead": bool(dead) and len(dead) == len(approved),
    }
