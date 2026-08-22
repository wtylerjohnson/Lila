"""Entity lineage crosswalk (L14) — one canonical door per corporate entity.

USAspending award and subaward records name the entity AS OF THE CONTRACT, so
a trailing window routinely lists a legacy name and its acquirer as two primes
(the 2026-07-09 Osprey brief listed Perspecta at $938.7M and Peraton at $559.6M
as separate teaming targets; Perspecta has been Peraton since May 2021). The
crosswalk at data/entities/crosswalk.json resolves name variants, M&A lineages,
and DBAs to a single canonical door BEFORE any report renders; tables merge
same-door rows, sum the dollars, and carry the lineage note once. Evidence
records are never silently rewritten: award rows keep their as-of-contract
names, and every merge renders its note.

Curation rules: every alias row carries a source_url (no sourceless lineage
claims); pure legal-suffix/punctuation variants need no alias row, the
normalizer collapses them. Unresolved names pass through unchanged and are
logged to data/entities/unresolved.log (count + first-seen) so the crosswalk
grows from real traffic.

ONE matcher for every path that touches a company name (the product tagger,
the buyer-map incumbent resolver, competitive-table merging, the lint):
`normalize_company` + `company_matches` live here so the paths cannot drift.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _entities_dir() -> str:
    return os.environ.get("LILA_ENTITIES_DIR") or os.path.join(
        ROOT, "data", "entities")


def crosswalk_path() -> str:
    return os.path.join(_entities_dir(), "crosswalk.json")


def unresolved_path() -> str:
    return os.path.join(_entities_dir(), "unresolved.log")


# ── normalization + word-boundary matching (shared, L13/L14) ─────────────────

_LEGAL_SUFFIX_TOKENS = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp",
    "corporation", "co", "company", "technology", "technologies",
}


def normalize_company(name: str) -> str:
    """Lowercase, strip punctuation, drop trailing legal suffixes:
    'Gigamon Inc.' and 'GIGAMON' normalize identically."""
    toks = re.sub(r"[^\w\s]", " ", (name or "").lower()).split()
    while toks and toks[-1] in _LEGAL_SUFFIX_TOKENS:
        toks.pop()
    return " ".join(toks)


def company_matches(name: str, needle: str) -> bool:
    """Word-boundary containment on normalized names: 'Gigamon' matches
    'Gigamon Inc.' but 'Riverbed' never matches 'River Edge Systems'."""
    hay = normalize_company(name).split()
    ned = normalize_company(needle).split()
    if not hay or not ned:
        return False
    return any(hay[i:i + len(ned)] == ned
               for i in range(len(hay) - len(ned) + 1))


# ── crosswalk loading (mtime-cached) ─────────────────────────────────────────

_cache: dict = {"key": None, "index": None, "entities": None}


def _load() -> tuple[list, dict]:
    """(alias index, entities by id). Index rows: (normalized alias tokens,
    canonical_id); each canonical_name is its own alias. Sorted longest alias
    first so 'General Dynamics Information Technology' wins over 'General
    Dynamics' for GDIT rows."""
    path = crosswalk_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return [], {}
    key = (path, mtime)
    if _cache["key"] == key:
        return _cache["index"], _cache["entities"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], {}
    index: list = []
    entities: dict = {}
    for e in data.get("entities") or []:
        cid = e.get("canonical_id")
        if not cid:
            continue
        entities[cid] = e
        names = [e.get("canonical_name") or ""] + [
            a.get("name") or "" for a in e.get("aliases") or []]
        for nm in names:
            toks = tuple(normalize_company(nm).split())
            if toks:
                index.append((toks, cid))
    index.sort(key=lambda row: -len(row[0]))
    _cache.update(key=key, index=index, entities=entities)
    return index, entities


# ── the resolver ─────────────────────────────────────────────────────────────

def resolve_entity_result(raw_name: str) -> tuple[Optional[str], Optional[str]]:
    """Pure ``(canonical_id, unresolved_reason)`` entity resolution."""
    toks = tuple(normalize_company(raw_name).split())
    if not toks:
        return None, "empty"
    index, _ = _load()
    if not index:
        return None, "no crosswalk"
    best: Optional[tuple] = None
    ambiguous = False
    for alias, cid in index:
        if alias == toks:
            return cid, None
        if 2 <= len(alias) < len(toks):
            hit = any(toks[i:i + len(alias)] == alias
                      for i in range(len(toks) - len(alias) + 1))
            if hit:
                if best is None:
                    best = (alias, cid)
                elif len(alias) == len(best[0]) and cid != best[1]:
                    ambiguous = True
    if best and not ambiguous:
        return best[1], None
    return None, "ambiguous" if ambiguous else "no match"


def resolve_entity(raw_name: str, *, log_unresolved: bool = True) -> Optional[str]:
    """canonical_id for a raw company name, or None (optionally logged).

    Exact normalized match always resolves. Word-boundary containment resolves
    only for aliases of 2+ tokens (single-token aliases match exact-only, so
    'Arbor' never claims 'Arbor Day Foundation'). Among containment matches
    the longest alias wins; a cross-door tie is ambiguity, logged, unresolved.
    """
    canonical_id, reason = resolve_entity_result(raw_name)
    if canonical_id is None and log_unresolved and reason not in {None, "empty"}:
        _log_unresolved(raw_name, reason)
    return canonical_id


def canonical_name(canonical_id: str) -> Optional[str]:
    _, entities = _load()
    e = entities.get(canonical_id)
    return e.get("canonical_name") if e else None


def entity_note(canonical_id: str) -> str:
    """The one-line lineage summary, suitable for a report footnote."""
    _, entities = _load()
    e = entities.get(canonical_id)
    return (e.get("notes") or "") if e else ""


def door_key(raw_name: str, *, log_unresolved: bool = True) -> str:
    """The merge key every table groups by: canonical_id when resolved, the
    normalized name otherwise (so pure suffix variants still merge)."""
    return (resolve_entity(raw_name, log_unresolved=log_unresolved)
            or normalize_company(raw_name))


# ── unresolved traffic log (the crosswalk grows from real traffic) ───────────

def _log_unresolved(raw_name: str, reason: str) -> None:
    nm = normalize_company(raw_name)
    if len(nm) < 3:
        return
    path = unresolved_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                log = json.load(f)
        except (OSError, ValueError):
            log = {}
        row = log.get(nm) or {"raw": raw_name, "count": 0,
                              "first_seen": date.today().isoformat(),
                              "reason": reason}
        row["count"] += 1
        log[nm] = row
        with open(path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1, sort_keys=True)
    except OSError:
        pass  # the log is telemetry; it never breaks a build


# ── back-compat lineage-note API (facts.py) ──────────────────────────────────

def lineage_of(name: str) -> Optional[tuple]:
    """(current parent, note) when `name` is a known LEGACY entity of a door,
    else None. A name that already IS the door returns None."""
    cid = resolve_entity(name)
    if cid is None:
        return None
    canon = canonical_name(cid) or ""
    if normalize_company(name) == normalize_company(canon):
        return None
    return canon, entity_note(cid)


def shared_door(name_a: str, name_b: str) -> Optional[str]:
    """The lineage note when two names resolve to the same corporate door,
    else None."""
    ca, cb = resolve_entity(name_a), resolve_entity(name_b)
    if ca and cb and ca == cb:
        return entity_note(ca)
    return None
