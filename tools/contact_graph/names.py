"""Name-normalization primitives for the contact graph.

Deliberately conservative: lowercase, strip punctuation, collapse whitespace —
and nothing more. We do NOT drop middle names or initials, because "Jane A. Doe"
and "Jane B. Doe" are different people and must not collapse into one profile.
That means "Jane A. Doe" and "Jane Doe" also stay distinct here; whether they are
the same person is an *ambiguous* judgment left to human review (see resolve.py),
never an automatic merge.
"""
from __future__ import annotations

import re
from typing import Optional

_PUNCT = re.compile(r"[.,;]+")
_WS = re.compile(r"\s+")


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, de-punctuate, collapse whitespace. '' for empty/None."""
    if not name:
        return ""
    s = _PUNCT.sub(" ", name.strip().lower())
    return _WS.sub(" ", s).strip()


def name_tokens(name: Optional[str]) -> list[str]:
    n = normalize_name(name)
    return n.split() if n else []


def core_key(name: Optional[str]) -> Optional[tuple[str, str]]:
    """(first, last) token pair — used only to *flag* near-matches for review,
    never to merge. Returns None if we can't form a first+last pair."""
    toks = name_tokens(name)
    if len(toks) < 2:
        return None
    return (toks[0], toks[-1])


# ── POC-field hygiene (2026-07-12) ──────────────────────────────────────────
# SAM POC blocks bleed contact plumbing into the name field: trailing phone
# digits glued to a surname ("COURTNEY BOWERSOCK6146939970"), whole rows that
# are only a label + number ("Telephone: 2157372522"), and switchboard
# placeholders published as phones ("0000000000"). Derivation cleans what it
# reads; the append-only observation store is never rewritten.

_PHONE_RUN = re.compile(
    r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}\b|\d{7,}")
_CONTACT_LABEL = re.compile(
    r"^(telephone|phone|tel|fax|e-?mail|cell|mobile)\b[\s:.\-]*", re.IGNORECASE)


def split_name_and_phone(raw: Optional[str]) -> tuple[str, Optional[str]]:
    """(clean person name, phone digits salvaged from the name field).

    A row whose name field carries no person once the plumbing is stripped
    ("Telephone: 215...", bare digits) returns ('', digits): it can still
    contribute a channel, never a profile.
    """
    if not raw:
        return "", None
    s = _WS.sub(" ", str(raw).strip())
    s = _CONTACT_LABEL.sub("", s)
    phone = None
    m = _PHONE_RUN.search(s)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 7:
            phone = digits
            s = f"{s[:m.start()]} {s[m.end():]}"
    s = _WS.sub(" ", s).strip(" \t,;:·|/-")
    if not re.search(r"[A-Za-z]{2}", s):
        return "", phone
    return s, phone


def plausible_phone(value: Optional[str]) -> bool:
    """Digits-shaped and not a placeholder. 7-15 digits, more than one
    distinct digit ("0000000000" and kin are switchboard placeholders, never
    a reachable channel)."""
    digits = re.sub(r"\D", "", str(value or ""))
    if not 7 <= len(digits) <= 15:
        return False
    return len(set(digits)) > 1
