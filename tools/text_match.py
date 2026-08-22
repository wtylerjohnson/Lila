"""Boundary-safe phrase matching shared by federal source adapters.

Retrieval is deliberately inclusive, but substring matching is not inclusion:
it turns short product and agency tokens into unrelated words (``AGNI`` in
``magnitude``, ``DISA`` in ``disaster``).  These helpers keep phrase matching
case-insensitive, tolerate federal hyphen/spacing drift, and require real token
boundaries at both ends.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable


_DOTTED_INITIALISM = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}")


def _normalized_phrase(value: str) -> str:
    """Normalize both sides of a match without erasing meaningful symbols.

    Federal and product text drifts between punctuation variants: ``C3.ai``
    versus ``C3 AI``, ``command & control`` versus ``command and control``,
    and ``C++`` versus ``C plus plus``.  Tokenizing only the query made those
    forms asymmetric and, worse, collapsed ``C++`` to the unrelated token
    ``C``.  Normalize both term and evidence to the same token language before
    applying boundaries.
    """
    text = str(value or "")
    text = _DOTTED_INITIALISM.sub(
        lambda match: match.group(0).replace(".", ""), text)
    text = text.replace("&", " and ")
    text = re.sub(
        r"\++",
        lambda match: " " + " ".join("plus" for _ in match.group(0)) + " ",
        text,
    )
    return " ".join(re.findall(r"[A-Za-z0-9]+", text)).casefold()


@lru_cache(maxsize=4096)
def phrase_pattern(term: str) -> re.Pattern[str] | None:
    """Compile a boundary-safe pattern that searches the original evidence.

    Callers such as the SAM extract preserve the returned match span as source
    evidence, so this public helper must match raw text.  Normalizing only in
    ``phrase_matches`` would make the boolean helper work while every direct
    pattern consumer silently missed ordinary mixed-case text.
    """
    raw = str(term or "").strip().strip('"')
    normalized = _normalized_phrase(term)
    if not normalized:
        return None

    def _token_pattern(token: str) -> str:
        if token == "and":
            return r"(?:and|&)"
        if token == "plus":
            return r"(?:plus|\+)"
        if token.isalpha() and 2 <= len(token) <= 5:
            dotted = r"\.?".join(re.escape(char) for char in token)
            return rf"(?:{re.escape(token)}|{dotted}\.?)"
        return re.escape(token)

    normalized_pattern = r"[^A-Za-z0-9]+".join(
        _token_pattern(token) for token in normalized.split()
    )
    exact_pattern = re.sub(r"(?:\\ )+", r"\\s+", re.escape(raw))
    phrase = rf"(?:{exact_pattern}|{normalized_pattern})"
    return re.compile(
        rf"(?<![A-Za-z0-9]){phrase}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def phrase_matches(text: str, term: str) -> bool:
    pattern = phrase_pattern(term)
    return (
        pattern is not None
        and pattern.search(str(text or "")) is not None
    )


def matching_phrases(text: str, terms: Iterable[str]) -> list[str]:
    return [
        term for raw in terms
        if (term := str(raw or "").strip().strip('"'))
        and phrase_matches(text, term)
    ]
