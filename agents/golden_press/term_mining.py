"""Mine procurement language from the client's OWN federal records.

THE PROBLEM THIS SOLVES. An intake frame is written in vendor marketing
language. Measured 2026-07-29, Riverbed's approved frame returned ZERO
solicitations against 78,577 stored notices: not one of "unified
observability", "digital employee experience" or "AIOps automated remediation"
appears verbatim in any federal notice, because no contracting officer writes
that way. Three of those terms are already listed in
data/reference/screen_guards.json as vendor-invented phrases that can never be
search terms. The frame was unusable and nothing in the pipeline said so.

WHERE THE RIGHT WORDS ALREADY ARE. The client's own award records. A
contracting officer wrote them, so they carry the exact phrasing the next
solicitation will use. Riverbed's 36 award records say "RIVERBED NETWORK
MONITORING HARDWARE AND SOFTWARE MAINTENANCE", "WIDE AREA NETWORK HARDWARE AND
SOFTWARE", "NETPROFILER LICENSING AND SUPPORT". Those are searchable. They also
name the products competing for the same money (DYNATRACE, SOLARWINDS ORION,
NETSCOUT), which are distinctive tier-1 names by construction.

WHAT THIS MODULE DOES NOT DO. It never writes a frame. Capability terms are an
Analyst Layer value and the operator owns them (L11). This module AUDITS the
current frame and PROPOSES candidates with their evidence attached, so the
operator approves or rejects a specific word for a stated reason. Deterministic,
no model call, no network, no quota.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from agents.golden_press.term_tiers import (
    TIER_COMMON, TIER_DISTINCTIVE, TIER_PHRASE, load_guards, tier_of,
)

# Federal contract WRITING noise: real phrases that describe the transaction
# rather than the thing bought. They are not screening guards (those live in
# screen_guards.json and qualify a hit); they are mining noise, so they live
# with the miner.
_ADMIN_NOISE = (
    "base period", "option years", "option year", "base plus", "delivery order",
    "purpose of this requirement", "previous award", "period of performance",
    "firm fixed price", "sole source", "small business", "task order",
    "hardware software", "hardware and software", "software maintenance",
    "hardware maintenance", "maintenance and support", "technical support",
    "maintenance renewal", "licensing and support", "and software maintenance",
    "software licenses", "professional service", "professional services",
    "engineering support", "support services", "annual renewal", "renewal of",
    "the renewal", "this requirement", "shall be", "will be", "in accordance",
)
_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "per", "via",
    "of", "to", "in", "on", "at", "by", "or", "a", "an", "is", "are", "be",
    "as", "it", "its", "not", "all", "any", "new", "no", "hw", "sw", "igf",
    "inc", "llc", "corp", "ot", "cl", "dh", "yr", "ea", "qty", "each",
    # Sentence machinery. An n-gram containing one of these is a fragment of
    # a sentence ("BPA Call will comprised"), never a search term.
    "will", "shall", "may", "must", "provide", "provides", "provided",
    "comprised", "comprise", "include", "includes", "including", "well",
    "also", "other", "such", "these", "those", "their", "there", "which",
    "been", "was", "were", "has", "have", "had", "does", "do", "can",
    "end", "life", "call", "order", "orders", "contract", "contractor",
    "requirement", "requirements", "government", "agency", "award", "awarded",
}
# Tokens that are identifiers rather than words: mixed letters+digits, pure
# digits, and the IGF::OT::IGF service-contract markers.
_ID_TOKEN = re.compile(r"^(?=.*[0-9])[A-Za-z0-9\-]+$")
_IGF = re.compile(r"igf::[a-z]{2}::igf", re.I)


def _normalize(text: str) -> str:
    text = _IGF.sub(" ", str(text or ""))
    text = re.sub(r"[^A-Za-z0-9+&\- ]+", " ", text)
    return " ".join(text.split())


def _tokens(text: str) -> list[str]:
    out = []
    for raw in _normalize(text).split():
        token = raw.strip("-&+")
        if not token or len(token) < 2:
            continue
        low = token.casefold()
        if low in _STOP or _ID_TOKEN.match(token):
            continue
        out.append(token)
    return out


def _ngrams(tokens: list[str], low: int = 2, high: int = 3) -> list[str]:
    grams = []
    for size in range(low, high + 1):
        for i in range(len(tokens) - size + 1):
            grams.append(" ".join(tokens[i:i + size]))
    return grams


def _guard_class(term: str, guards: dict) -> Optional[str]:
    """The shared-guard verdict on a term, when it has one."""
    low = " ".join(str(term or "").split()).casefold()
    if low in {p.casefold() for p in guards.get("vendor_phrases", ())}:
        return "vendor_marketing"
    if low in {p.casefold() for p in guards.get("attributes", ())}:
        return "attribute_not_capability"
    return None


def audit_frame(
    terms: Iterable[str],
    *,
    conn: Optional[Any] = None,
    guards: Optional[dict] = None,
    distinctive_names: Iterable[str] = (),
) -> list[dict]:
    """Per frame term: its tier, any shared-guard verdict, and store presence.

    ``store_hits`` is the number of stored notices whose title or description
    contains the term verbatim. Zero means the term cannot ever produce a
    notice, which is the finding the pipeline never surfaced.
    """
    g = guards or load_guards()
    owned = conn is None
    rows: list[dict] = []
    try:
        if conn is None:
            try:
                from tools.notice_store import connect
                conn = connect()
            except Exception:  # noqa: BLE001 - audit degrades, never blocks
                conn = None
        for term in terms:
            text = " ".join(str(term or "").split())
            if not text:
                continue
            hits = None
            if conn is not None:
                try:
                    like = f"%{text}%"
                    hits = conn.execute(
                        "SELECT COUNT(*) c FROM notices WHERE title LIKE ? "
                        "OR description_prefix LIKE ?", (like, like)
                    ).fetchone()["c"]
                except Exception:  # noqa: BLE001
                    hits = None
            verdict = _guard_class(text, g)
            tier = tier_of(text, distinctive_names=distinctive_names)
            usable = verdict is None and (hits is None or hits > 0)
            rows.append({
                "term": text, "tier": tier, "guard_class": verdict,
                "store_hits": hits, "usable": usable,
                "why": (verdict or ("no stored notice contains this term"
                                    if hits == 0 else "usable")),
            })
    finally:
        if owned and conn is not None:
            conn.close()
    rows.sort(key=lambda r: (r["usable"], -(r["store_hits"] or 0), r["term"]))
    return rows


def mine_terms(
    records: Iterable[Any],
    *,
    existing_terms: Iterable[str] = (),
    entities: Optional[dict] = None,
    guards: Optional[dict] = None,
    conn: Optional[Any] = None,
    min_records: int = 2,
    limit: int = 24,
    max_store_share: float = 0.01,
) -> list[dict]:
    """Candidate search terms drawn from the client's own award records.

    A candidate must appear in at least ``min_records`` DISTINCT records: one
    award's phrasing is an anecdote, two is a pattern. Every candidate carries
    the record ids that support it so the operator judges evidence, not a word.
    """
    g = guards or load_guards()
    have = {" ".join(str(t or "").split()).casefold() for t in existing_terms}
    entity_names = set()
    for values in (entities or {}).values():
        for name in values or ():
            entity_names.add(str(name).casefold())

    support: dict[str, set[str]] = {}
    example: dict[str, str] = {}
    for record in records:
        text = str(getattr(record, "description", None)
                   or (record.get("description") if isinstance(record, dict) else "")
                   or getattr(record, "title", None)
                   or (record.get("title") if isinstance(record, dict) else "") or "")
        rid = str(getattr(record, "record_id", None)
                  or (record.get("record_id") if isinstance(record, dict) else "") or "")
        if not text:
            continue
        for gram in set(_ngrams(_tokens(text))):
            low = gram.casefold()
            if low in have or low in entity_names:
                continue
            if _guard_class(gram, g):
                continue
            if any(noise in low for noise in _ADMIN_NOISE):
                continue
            support.setdefault(low, set()).add(rid)
            example.setdefault(low, gram)

    candidates = []
    for low, rids in support.items():
        if len(rids) < min_records:
            continue
        candidates.append({
            "term": example[low],
            "tier": tier_of(example[low]),
            "record_count": len(rids),
            "supporting_record_ids": sorted(rids)[:6],
        })

    # PROVE IT, THEN PROVE IT DISCRIMINATES. A proposal matching no stored
    # notice repeats the failure being fixed, so store presence is required.
    # But raw frequency is the WRONG ranking: "INDEFINITE DELIVERY" and
    # "PART NUMBER" reach thousands of notices precisely because they are
    # contract boilerplate carrying no capability meaning. A term appearing in
    # more than max_store_share of the whole store is boilerplate by
    # measurement rather than by hand-list, and the useful signal is
    # SPECIFICITY: frequent in this client's own awards, rare in the general
    # population.
    if conn is not None:
        try:
            total = conn.execute(
                "SELECT COUNT(*) c FROM notices").fetchone()["c"] or 0
        except Exception:  # noqa: BLE001
            total = 0
        # A share that rounds to zero must not silently disable the filter, so
        # the ceiling floors at one notice.
        ceiling = max(1, int(total * max_store_share)) if total else 0
        for row in candidates:
            like = f"%{row['term']}%"
            try:
                row["store_hits"] = conn.execute(
                    "SELECT COUNT(*) c FROM notices WHERE title LIKE ? "
                    "OR description_prefix LIKE ?", (like, like)
                ).fetchone()["c"]
            except Exception:  # noqa: BLE001 - degrade, never block
                row["store_hits"] = None
        candidates = [
            r for r in candidates
            if (r.get("store_hits") or 0) > 0
            and (not ceiling or (r["store_hits"] or 0) <= ceiling)]

    # Suppress sub-phrases: keep "RED HAT ENTERPRISE LINUX", drop the
    # "HAT ENTERPRISE" fragment it contains when the longer phrase is at least
    # as well evidenced in the store.
    candidates.sort(key=lambda r: (-len(r["term"]), r["term"].casefold()))
    kept: list[dict] = []
    for row in candidates:
        low = row["term"].casefold()
        # A fragment earns its own row only by being MATERIALLY more
        # searchable than the phrase containing it. Half the reach is the
        # threshold, which resolves both directions of this problem with one
        # rule: "US Coast" (42) dies inside "US Coast Guard" (41, retains
        # nearly all reach) and "HAT ENTERPRISE" dies inside "RED HAT
        # ENTERPRISE", while "network monitoring" (16) SURVIVES "network
        # monitoring hardware" (1, retains almost none) because for a search
        # term the shorter phrase is then the far more useful one.
        covered = any(
            low in k["term"].casefold()
            and (k.get("store_hits") or 0) >= (row.get("store_hits") or 0) * 0.5
            for k in kept)
        if not covered:
            kept.append(row)
    kept.sort(key=lambda r: (-r["record_count"], r.get("store_hits") or 0,
                             r["term"].casefold()))
    return kept[:limit]


def frame_report(
    pack: Any,
    *,
    conn: Optional[Any] = None,
    min_records: int = 2,
    limit: int = 24,
) -> dict:
    """Audit the pack's frame and propose mined replacements. Writes nothing."""
    research = getattr(pack, "research", None) or {}
    if not research and isinstance(pack, dict):
        research = pack.get("research") or {}
    terms = list(research.get("capability_terms") or [])
    entities = research.get("entities") or {}
    distinct = [n for v in (entities.values() if isinstance(entities, dict) else [])
                for n in (v or ())]
    records = list(getattr(pack, "records", None)
                   or (pack.get("records") if isinstance(pack, dict) else []) or [])
    guards = load_guards()
    owned = conn is None
    if conn is None:
        try:
            from tools.notice_store import connect
            conn = connect()
        except Exception:  # noqa: BLE001 - report degrades, never blocks
            conn = None
    try:
        audit = audit_frame(terms, conn=conn, guards=guards,
                            distinctive_names=distinct)
        proposed = mine_terms(records, existing_terms=terms, entities=entities,
                              guards=guards, conn=conn,
                              min_records=min_records, limit=limit)
    finally:
        if owned and conn is not None:
            conn.close()
    unusable = [r for r in audit if not r["usable"]]
    return {
        "client": (getattr(pack, "client_name", None)
                   or (pack.get("client_name") if isinstance(pack, dict) else "")),
        "frame_terms": len(audit),
        "unusable_terms": len(unusable),
        "audit": audit,
        "proposed": proposed,
        "note": ("Proposals only. Capability terms are an Analyst Layer value "
                 "and remain operator-owned; nothing here is written."),
    }


__all__ = ("audit_frame", "mine_terms", "frame_report",
           "TIER_COMMON", "TIER_DISTINCTIVE", "TIER_PHRASE")
