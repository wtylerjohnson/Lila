"""Learn the federal vocabulary from federal text, instead of guessing it.

THE FAILURE THIS ENDS (operator, 2026-08-06): "this keeps happening".

Three times in one day a client's approved frame missed the language the
government actually writes, and three times the fix was me hand-editing a
synonym list AFTER the miss:

  apexanalytix's eight core terms matched ZERO of 330,641 notices
  "AP recovery audit" could not reach "recovery audit"
  "improper payment prevention" could not reach "payment integrity", even
  though the Payment Integrity Information Act is the law ABOUT improper
  payments, because my own map had them in two disconnected groups

A hand-maintained list is reactive by construction: it only ever learns
from the miss you already noticed. This module makes the inference layer
learn from the corpus instead.

THE KEY ASSET IS ALREADY ON DISK. 330,641 real federal notices, written by
contracting officers. Nobody has to guess what they say: it is measurable.

TWO PASSES, BOTH DETERMINISTIC, NO MODEL.

  1 MINE   take the notices the current frame DOES match (the seed set),
           extract the phrases that appear there far more often than in the
           store at large, and propose them. A phrase that is 40x more
           common among payment-integrity notices than in federal text
           generally is what that subject is called.
  2 MEASURE every candidate against the store BEFORE it is proposed, so the
           operator reviews a term with its real yield and sample titles
           attached, never a suggestion.

WHY LIFT AND NOT FREQUENCY. "shall be" and "the contractor" are the most
common phrases in every notice ever written. Lift asks a different question:
how much MORE common is this phrase in the relevant set than everywhere
else. That is what separates a term of art from boilerplate.

NOTHING HERE EDITS THE FRAME. It proposes, with evidence. The frame stays
operator-owned (L11), and a proposed term reaches a search only after the
operator approves it, exactly as the keyword workshop already works.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

TERM_DISCOVERY_VERSION = "term_discovery.v1.2026-08-06"

# A candidate has to clear all three or it is noise.
MIN_SEED_HITS = 3          # appears in at least this many relevant notices
MIN_LIFT = 4.0             # this much more common in the seed set than at large
MAX_STORE_SHARE = 0.05     # appearing in >5% of ALL notices makes it boilerplate

# Phrase lengths worth proposing. Single words are too ambiguous to add to a
# frame unaided ("payment", "risk"); five-word runs are sentence fragments.
NGRAM_MIN, NGRAM_MAX = 2, 4

# Federal boilerplate that clears lift by accident, usually because it rides
# a document template rather than a subject.
_STOPPHRASE = re.compile(
    r"\b(shall be|will be|the contractor|the government|this notice|"
    r"sources sought|request for information|small business|set aside|"
    r"north american industry|classification system|response date|"
    r"points? of contact|contracting officer|place of performance|"
    r"period of performance|in accordance|see attached|questions? must)\b")

_WORD = re.compile(r"[a-z][a-z0-9'\-]+")

# A term of art is a NOUN PHRASE. A fragment that opens or closes on a
# function word is a sentence slice that happens to be rare as an exact
# string: "information be" cleared 78x lift over 2,621 notices, and
# "in preparation of" and "questions this is" cleared it too. Lift alone
# cannot tell a subject from a slice; grammar can.
_EDGE_FUNCTION_WORDS = frozenset("""
a an the this that these those and or but if then than as at by for from in
into of on to with without within under over be been being is are was were
am shall will would should could may might must can do does did have has had
not no nor so such very more most much many any all each every either neither
per via upon about above below after before during while when where which who
only just also both same other another such only
whom whose what why how it its it's their there here they them we our your you
""".split())


# A verb ANYWHERE makes the phrase a clause, not a subject. Edge checking
# alone let "proprietary information be submitted" through: it opens and
# closes on content words and is still a sentence fragment.
_CLAUSE_MARKERS = frozenset("""
be been being is are was were am shall will would should could may might must
do does did have has had can cannot submit submitted provide provided include
included require required ensure must
""".split())


def _is_noun_phrase(phrase: str) -> bool:
    """Whether a candidate reads as a subject rather than a sentence slice."""
    words = phrase.split()
    if len(words) < 2:
        return False
    if words[0] in _EDGE_FUNCTION_WORDS or words[-1] in _EDGE_FUNCTION_WORDS:
        return False
    if any(w in _CLAUSE_MARKERS for w in words):
        return False
    # a phrase that is mostly glue carries no subject either
    content = [w for w in words if w not in _EDGE_FUNCTION_WORDS]
    return len(content) >= max(2, len(words) - 1)


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip()


def _ngrams(text: str) -> set:
    """Distinct 2-to-4 word phrases in one notice, boilerplate removed."""
    words = _WORD.findall(_norm(text))
    out: set = set()
    for size in range(NGRAM_MIN, NGRAM_MAX + 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if len(phrase) < 8 or _STOPPHRASE.search(phrase):
                continue
            out.add(phrase)
    return out


# A NAICS code broad enough to hold this many notices describes an industry,
# not a subject, and seeds nothing useful. 541512 (Computer Systems Design)
# holds 1,414; 541219 (Other Accounting Services) holds 100 and every one is
# in apexanalytix's actual world.
NAICS_SEED_MAX = 600


def _naics_seed_codes(conn, codes: Any) -> list:
    """The operator's approved NAICS codes, narrowest first, broad ones cut.

    THE WAY OUT OF THE CHICKEN AND EGG. Lexical seeding needs good words to
    find the notices you would learn good words from: apexanalytix's frame
    seeded THREE notices. A NAICS boundary is STRUCTURAL, already approved by
    the operator, and needs no vocabulary at all, so it can define the
    relevant set before a single term is known.
    """
    ranked = []
    for code in (codes or []):
        code = re.sub(r"[^0-9]", "", str(code or ""))
        if len(code) < 4:
            continue
        row = conn.execute(
            "SELECT COUNT(*) n FROM notices WHERE naics LIKE ?",
            (f"{code}%",)).fetchone()
        n = int(row["n"] if hasattr(row, "keys") else row[0])
        if 0 < n <= NAICS_SEED_MAX:
            ranked.append((n, code))
    ranked.sort()
    return [code for _, code in ranked]


def _seed_notices(conn, terms: Any, *, limit: int = 400,
                  naics: Any = None) -> list:
    """The relevant set: notices the frame matches, plus the approved
    structural boundary. Either route alone is thin; together they are the
    subject."""
    clauses, params = [], []
    for code in (naics or []):
        clauses.append("naics LIKE ?")
        params.append(f"{code}%")
    for term in terms:
        t = _norm(term)
        if len(t) < 4:
            continue
        clauses.append("(title LIKE ? OR description_prefix LIKE ?)")
        params.extend([f"%{t}%", f"%{t}%"])
    if not clauses:
        return []
    sql = ("SELECT notice_id, title, description_prefix, naics, psc, agency "
           "FROM notices WHERE " + " OR ".join(clauses) +
           " ORDER BY posted DESC LIMIT ?")
    return conn.execute(sql, (*params, limit)).fetchall()


# How many notices to read for the BACKGROUND frequency. One scan, held in
# memory, instead of a LIKE scan per candidate: the first build ran 600
# full-table scans over 330,641 rows and did not finish inside two minutes.
BACKGROUND_SAMPLE = 12000


def _background_counts(conn, *, sample: int = BACKGROUND_SAMPLE,
                       exclude: Any = None) -> tuple:
    """(ngram -> notices containing it, notices sampled). ONE query.

    A uniform sample answers "how common is this phrase in federal text at
    large" precisely enough to rank lift, and it turns the expensive part of
    this module from O(candidates x store) into O(store) once.

    THE SEED SET IS EXCLUDED. Leaving it in makes the relevant notices part
    of their own baseline, which depresses every lift by exactly the amount
    that matters: a term used by all 40 relevant notices reads as common
    rather than as distinctive. Negligible against 330,641 notices and
    decisive against a small store, and wrong either way.
    """
    excluded = {str(x) for x in (exclude or [])}
    rows = conn.execute(
        "SELECT notice_id, title, description_prefix FROM notices "
        "ORDER BY notice_id LIMIT ?", (sample + len(excluded),)).fetchall()
    rows = [r for r in rows if str(r["notice_id"]) not in excluded][:sample]
    counts: Counter = Counter()
    for row in rows:
        text = " ".join(str(row[k] or "") for k in ("title",
                                                    "description_prefix"))
        counts.update(_ngrams(text))
    return counts, len(rows)


def _store_share(conn, phrase: str, total: int) -> tuple:
    """(hits, share) for one phrase across the WHOLE store.

    Exact, and therefore expensive, so it runs only for the handful of
    candidates that already cleared lift against the sample.
    """
    row = conn.execute(
        "SELECT COUNT(*) n FROM notices WHERE title LIKE ? OR "
        "description_prefix LIKE ?", (f"%{phrase}%", f"%{phrase}%")).fetchone()
    hits = int(row["n"] if hasattr(row, "keys") else row[0])
    return hits, (hits / total if total else 0.0)


def mine_candidates(conn, seed_terms: Any, *, limit: int = 400,
                    max_candidates: int = 25, naics: Any = None) -> dict:
    """Phrases the relevant notices use that federal text at large does not.

    Pure measurement over the local store: no network, no model, no credits.
    Every candidate returns with its real yield and sample titles so the
    operator approves evidence rather than a suggestion.
    """
    receipt = {
        "version": TERM_DISCOVERY_VERSION,
        "seed_terms": [str(t) for t in (seed_terms or [])],
        "network_calls": 0, "model_calls": 0, "credits_spent": 0,
    }
    total_row = conn.execute("SELECT COUNT(*) n FROM notices").fetchone()
    total = int(total_row["n"] if hasattr(total_row, "keys") else total_row[0])
    receipt["store_size"] = total

    seed_codes = _naics_seed_codes(conn, naics)
    receipt["naics_seed_codes"] = seed_codes
    receipt["naics_seed_dropped_as_too_broad"] = [
        c for c in (naics or []) if c not in seed_codes]
    seeds = _seed_notices(conn, seed_terms, limit=limit, naics=seed_codes)
    receipt["seed_notices"] = len(seeds)
    if not seeds:
        receipt["why"] = ("the current frame matches no stored notice, so "
                          "there is no relevant set to learn from; widen the "
                          "frame by hand once, then this can run")
        return {"candidates": [], "receipt": receipt}

    counts: Counter = Counter()
    for row in seeds:
        text = " ".join(str(row[k] or "") for k in ("title",
                                                    "description_prefix"))
        counts.update(_ngrams(text))

    background, sampled = _background_counts(
        conn, exclude=[r["notice_id"] for r in seeds])
    receipt["background_sample"] = sampled

    known = {_norm(t) for t in (seed_terms or [])}
    ranked: list = []
    for phrase, seed_hits in counts.most_common(800):
        if seed_hits < MIN_SEED_HITS:
            break
        # ALREADY-COVERED means LESS specific, not merely overlapping. A
        # phrase CONTAINED in a frame term adds nothing ("supplier" under
        # "supplier onboarding"), but one that EXTENDS a frame term is the
        # more precise federal wording and is exactly what this exists to
        # find: with "audit" in the frame, "audit remediation" is the term
        # of art that surfaces the DIA RFI, and an earlier build discarded
        # it for sharing a word.
        if not _is_noun_phrase(phrase):
            continue
        if phrase in known or any(phrase in k for k in known):
            continue
        bg = background.get(phrase, 0)
        bg_share = (bg / sampled) if sampled else 0.0
        if bg_share > MAX_STORE_SHARE:
            continue                       # boilerplate
        seed_share = seed_hits / len(seeds)
        # An unseen phrase is treated as one occurrence, not zero: dividing
        # by zero would rank every typo above every real term of art.
        lift = seed_share / (bg_share or (1.0 / max(sampled, 1)))
        if lift < MIN_LIFT:
            continue
        ranked.append((lift, seed_hits, seed_share, phrase))

    ranked.sort(key=lambda r: (-r[0], -r[1]))
    candidates: list = []
    for lift, seed_hits, seed_share, phrase in ranked[:max_candidates]:
        # EXACT store count only for what survived, so the operator reviews a
        # real yield rather than a sampled estimate.
        store_hits, share = _store_share(conn, phrase, total)
        if not store_hits:
            continue
        candidates.append({
            "term": phrase,
            "seed_hits": seed_hits,
            "seed_share": round(seed_share, 4),
            "store_hits": store_hits,
            "store_share": round(share, 6),
            "lift": round(lift, 1),
            "why": (f"appears in {seed_hits} of {len(seeds)} notices this "
                    f"frame already matches, and is {lift:.0f}x more common "
                    f"there than in federal text at large"),
            "samples": _samples(conn, phrase),
        })
    receipt["candidates_proposed"] = len(candidates)
    receipt["thresholds"] = {"min_seed_hits": MIN_SEED_HITS,
                             "min_lift": MIN_LIFT,
                             "max_store_share": MAX_STORE_SHARE}
    return {"candidates": candidates, "receipt": receipt}


def _samples(conn, phrase: str, *, n: int = 3) -> list:
    """THE TITLES ARE THE RECEIPT (the term-yield doctrine). A count reads as
    signal until the titles show it matched something unrelated."""
    rows = conn.execute(
        "SELECT title, COALESCE(subtier, agency) org FROM notices "
        "WHERE title LIKE ? OR description_prefix LIKE ? "
        "ORDER BY posted DESC LIMIT ?", (f"%{phrase}%", f"%{phrase}%", n)
    ).fetchall()
    return [{"title": " ".join(str(r["title"] or "").split())[:90],
             "org": " ".join(str(r["org"] or "").split())[:40]} for r in rows]


def propose_for_profile(profile: Any, conn, *, expand: bool = False) -> dict:
    """Candidate frame additions for one client, measured and evidenced.

    SEEDS FROM THE ORIGINAL FRAME, NOT THE WIDENED ONE (measured 2026-08-06).
    Widening exists for RECALL at match time and it carries false positives
    by design; using it to define the relevant set imports every one of them.
    The first build seeded from the widened list, whose 'registration' variant
    matched SAM entity-registration boilerplate, and discovery duly proposed
    "notarized letter is mandatory" as a term of art. Precision defines the
    subject; recall searches it.
    """
    terms = ((profile.get("capability_terms") or {}).get("core")
             if isinstance(profile, dict) else None) or []
    seeds = list(terms)
    if expand:
        from agents.golden_press.term_expansion import expand_vocabulary
        seeds = expand_vocabulary(list(terms))["terms"]
    naics = (profile.get("naics_boundary") if isinstance(profile, dict)
             else None) or []
    out = mine_candidates(conn, seeds, naics=naics)
    out["receipt"]["client"] = (profile.get("client_name")
                                if isinstance(profile, dict) else None)
    out["receipt"]["frame_terms"] = len(terms)
    out["receipt"]["seed_terms_after_expansion"] = len(seeds)
    return out


def render_review(proposal: dict) -> str:
    """The operator's review sheet. Plain text: term, yield, why, samples."""
    receipt = proposal.get("receipt") or {}
    lines = [
        f"TERM DISCOVERY · {receipt.get('client') or 'client'}",
        f"  store {receipt.get('store_size', 0):,} notices · "
        f"seed set {receipt.get('seed_notices', 0)} · "
        f"{receipt.get('candidates_proposed', 0)} candidates · "
        f"0 model calls · 0 credits",
        "",
    ]
    for candidate in (proposal.get("candidates") or []):
        lines.append(f"  {candidate['term']}")
        lines.append(f"      {candidate['store_hits']:,} stored notices · "
                     f"lift {candidate['lift']}x · {candidate['why']}")
        for sample in candidate.get("samples") or []:
            lines.append(f"      · {sample['title']}  [{sample['org']}]")
        lines.append("")
    if not proposal.get("candidates"):
        lines.append(f"  no candidate cleared the thresholds. "
                     f"{receipt.get('why', '')}")
    return "\n".join(lines)
