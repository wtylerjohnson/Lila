"""Shared capability-query vocabulary for federal source adapters.

Approved strategy keywords carry several kinds of information. Capability,
technology, and explicit search terms describe what the client sells; agency,
set-aside, and NAICS terms describe where or how the government may buy it.
Only the first group belongs in a full-text opportunity or award screen.
"""

from __future__ import annotations

from typing import Optional


CAPABILITY_QUERY_CATEGORIES = frozenset({
    "capability", "technology", "search_term",
})


def capability_query_terms(keywords: list, *, limit: Optional[int] = None
                           ) -> list[str]:
    """Return stable, de-duplicated product/capability query phrases.

    Strategy ``Keyword`` models and their serialized dicts are both accepted.
    Slash compounds become independent queries and parenthetical abbreviations
    do not make a phrase impossible to match. ``limit=None`` is the complete
    vocabulary used by the quota-free SAM extract; metered APIs may set a cap.
    """
    out: list[str] = []
    seen: set[str] = set()
    for keyword in keywords or []:
        term = (keyword.get("term") if isinstance(keyword, dict)
                else getattr(keyword, "term", None))
        category = (keyword.get("category") if isinstance(keyword, dict)
                    else getattr(keyword, "category", None))
        category = getattr(category, "value", category)
        if not term or category not in CAPABILITY_QUERY_CATEGORIES:
            continue
        for part in str(term).split("/"):
            phrase = part.split("(")[0].strip(" .,;:-")
            key = phrase.casefold()
            if len(phrase) < 3 or key in seen:
                continue
            seen.add(key)
            out.append(phrase)
            if limit is not None and len(out) >= limit:
                return out
    return out


def term_yield(phrases: list[str], *, conn=None,
               sample_cap: int = 3) -> list[dict]:
    """Per-term evidence of what the vocabulary actually finds on SAM:
    ``{term, count, sample_titles}`` against the accumulated notice store.

    THE TITLES ARE THE RECEIPT; THE COUNT IS ONLY THE INDEX (operator
    doctrine, 2026-07-31). A bare count misleads in both directions: "APM: 3"
    reads as signal until the titles show a Navy sling assembly, a part
    number, and an Assistant Program Manager named Tony Prudhomme; and a
    zero reads as a broken screen until the sweep's own record shows the
    client's market moves as reseller renewals that never post publicly.
    "SLING ASSEMBLY, APM VERTICAL LIFT" sitting next to the count ends the
    argument in two seconds, without a script or a session.

    Deliberately NO verdict field: no dead-vocabulary alarm, no health
    grade. Riverbed's all-zero vocabulary was TRUE, and an alarm that fires
    on a true zero teaches distrust of true zeros. Counts plus verbatim
    titles carry the information without asserting that something broke.

    Reads the durable store, not the daily extract, so the receipt exists
    even on a morning the upstream extract is broken. An empty or missing
    store yields [] and the caller says so; this never raises into a sweep.
    """
    import re as _re

    from tools.notice_store import connect as _connect

    cleaned = [" ".join(str(p or "").split()) for p in (phrases or [])]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        return []
    owned = conn is None
    try:
        conn = conn or _connect()
        rows = conn.execute(
            "SELECT title, description_prefix FROM notices").fetchall()
    except Exception:  # noqa: BLE001 - a receipt never sinks a sweep
        return []
    finally:
        if owned and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    if not rows:
        return []
    docs = [(f"{r['title'] or ''} {r['description_prefix'] or ''}",
             str(r["title"] or "")) for r in rows]
    out: list[dict] = []
    for phrase in cleaned:
        pattern = _re.compile(
            r"(?<![A-Za-z0-9])" + _re.escape(phrase) + r"(?![A-Za-z0-9])",
            _re.I)
        count = 0
        samples: list[str] = []
        for text, title in docs:
            if pattern.search(text):
                count += 1
                if len(samples) < sample_cap:
                    cleaned_title = " ".join(title.split())
                    if cleaned_title and cleaned_title not in samples:
                        samples.append(cleaned_title)
        out.append({"term": phrase, "count": count,
                    "sample_titles": samples})
    return out


def append_term_yield_log(entry: dict, *, path: Optional[str] = None) -> None:
    """One JSONL line per sweep: the corpus of which vocabulary forms ever
    appear on SAM. After ten clients this is the empirical basis for term
    weighting; until then it is deliberately just a file append - nothing
    is built on top of it, per the same ruling that scoped the receipt.
    """
    import json as _json
    import os as _os

    target = path or _os.environ.get(
        "LILA_TERM_YIELD_LOG",
        _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))),
            "data", "state", "term_yield_log.jsonl"))
    try:
        _os.makedirs(_os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:  # noqa: BLE001 - the log is a bonus, never a blocker
        pass


def title_glance(title: str, limit: int = 96) -> str:
    """A console-width title that never deletes the payload.

    SAM titles front-load boilerplate and back-load content: "REQUIREMENTS-
    26-4191 NOTICE OF INTENT TO SOLE-SOURCE TO INNOVASEA" carries its entire
    payload in the final token, and the receipt's first live output
    right-truncated exactly one word before the word that decided it
    (operator catch, 2026-07-31). Right-truncation of this title convention
    deletes the deciding word as a CLASS defect, so the glance keeps the
    head AND the tail and cuts the middle. Display only: the receipt's
    stored sample_titles are always verbatim.
    """
    text = " ".join(str(title or "").split())
    if len(text) <= limit:
        return text
    head = text[: max(8, (limit - 1) * 2 // 5)].rstrip()
    tail = text[-(limit - 1 - len(head)):].lstrip()
    return f"{head}…{tail}"
