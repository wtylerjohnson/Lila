"""Deterministic capability matcher and relevance scoring. No LLM here.

Scoring doctrine (the evidence inversion, R11, made mechanical):
- Relevance is earned ONLY through CORE evidence, optionally reinforced by
  ADJACENT evidence. Adjacent alone is never sufficient. A code match
  contributes exactly zero.
- Every match captures its span verbatim with bounded context, so "why is
  this here" is always answerable with quoted record text.
- EXCLUDE kill-rules remove matches in their span window (or the whole
  record), so known false-positive shapes die where they stand.
- The code universe is a pre-filter that may exclude. A record outside the
  universe with strong capability evidence passes through flagged
  OFF-CODE SIGNAL (internal vocabulary; client copy never mentions codes).
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

#: a record is relevant iff score >= this (one distinct CORE term)
RELEVANCE_THRESHOLD = 3
#: an out-of-universe record survives the code pre-filter iff score >= this
OFF_CODE_STRONG_THRESHOLD = 6
CORE_WEIGHT = 3
ADJACENT_WEIGHT = 1
#: chars of verbatim context captured around a match
SPAN_CONTEXT = 60
#: chars around a span-scoped kill within which capability matches die
KILL_WINDOW = 120

#: record keys the matcher reads as text, in order.  The final three are
#: official PROGRAM-source narrative fields (DSIP, RegInfo, DARPA); keeping
#: them in the sanctioned scorer preserves exact field/span provenance.
TEXT_KEYS = (
    "title", "description", "description_snippet", "text", "label",
    "objective", "abstract", "summary",
)

_SUFFIXES = ("ing", "ed", "es", "s")


def _stem(token: str) -> str:
    low = token.lower()
    for suf in _SUFFIXES:
        if low.endswith(suf) and len(low) - len(suf) >= 3:
            low = low[: len(low) - len(suf)]
            break
    # analysis/analyses both stem to 'analys'
    if len(low) > 4 and low.endswith("i"):
        low = low[:-1]
    return low


class MatchedSpan(BaseModel):
    term: str
    tier: str                       # 'core' | 'adjacent' | 'exclude'
    mode: str
    field: str
    matched_text: str = Field(description="the exact matched record text")
    context: str = Field(description="verbatim record text around the match")
    start: int


class RelevanceVerdict(BaseModel):
    score: int
    relevant: bool
    core_terms: list[str] = Field(default_factory=list)
    adjacent_terms: list[str] = Field(default_factory=list)
    spans: list[MatchedSpan] = Field(default_factory=list)
    killed: list[str] = Field(
        default_factory=list,
        description="matches removed by kill-rules, with the rule's reason")
    off_code: bool = Field(
        default=False,
        description="outside the client's code universe yet strongly "
                    "capability-relevant: OFF-CODE SIGNAL, internal only")
    off_scope: bool = Field(
        default=False,
        description="outside the engagement's agency universe; scores "
                    "normally, never counts as in-scope")
    off_scope_signal: bool = Field(
        default=False,
        description="off-scope AND strong: OFF-SCOPE SIGNAL, internal "
                    "vocabulary, surfaced as expansion signal only")
    scope_basis: str = Field(
        default="UNSCOPED",
        description="how scope was decided: UNSCOPED, unresolved, "
                    "in-scope:<dept>, off-scope:<dept>")
    excluded_by_code: bool = Field(
        default=False,
        description="outside the universe without strong evidence: the "
                    "pre-filter excluded it")
    taxonomy_client: str = ""
    taxonomy_version: int = 0


def _tokenize(text: str) -> list[tuple[str, int]]:
    return [(m.group(0), m.start())
            for m in re.finditer(r"[A-Za-z0-9']+", text)]


def _phrase_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    pattern = r"(?<![\w])" + r"[\s\-]+".join(
        re.escape(w) for w in phrase.split()) + r"(?![\w])"
    return [(m.start(), m.end())
            for m in re.finditer(pattern, text, re.I)]


def _stemmed_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    want = [_stem(w) for w in re.findall(r"[A-Za-z0-9']+", phrase)]
    if not want:
        return []
    tokens = _tokenize(text)
    stems = [_stem(t) for t, _ in tokens]
    out = []
    for i in range(len(stems) - len(want) + 1):
        if stems[i:i + len(want)] == want:
            start = tokens[i][1]
            last_tok, last_off = tokens[i + len(want) - 1]
            out.append((start, last_off + len(last_tok)))
    return out


def _acronym_spans(text: str, term: TaxonomyTerm) -> list[tuple[int, int]]:
    """The acronym counts only when its expansion context co-occurs in the
    same text: standalone SCA proves nothing."""
    hits = _phrase_spans(text, term.term)
    if not hits:
        return []
    low = text.lower()
    if any(ctx.lower() in low for ctx in term.expansion_context):
        return hits
    return []


def _spans_for(text: str, term: TaxonomyTerm) -> list[tuple[int, int]]:
    if term.mode == "exact_phrase":
        return _phrase_spans(text, term.term)
    if term.mode == "acronym":
        return _acronym_spans(text, term)
    return _stemmed_spans(text, term.term)


def _mk_span(text: str, field: str, term: TaxonomyTerm, tier: str,
             start: int, end: int) -> MatchedSpan:
    lo = max(0, start - SPAN_CONTEXT)
    hi = min(len(text), end + SPAN_CONTEXT)
    return MatchedSpan(term=term.term, tier=tier, mode=term.mode, field=field,
                       matched_text=text[start:end], context=text[lo:hi],
                       start=start)


def score_text(text: str, taxonomy: CapabilityTaxonomy,
               field: str = "text") -> tuple[list[MatchedSpan], list[str]]:
    """All capability spans in one text after kill-rules. Returns
    (surviving spans, killed descriptions)."""
    if not text:
        return [], []
    kills: list[tuple[int, int, str, str]] = []
    for rule in taxonomy.exclude:
        for start, end in _phrase_spans(text, rule.term):
            kills.append((start, end, rule.scope, rule.reason or rule.term))
    if any(scope == "record" for _, _, scope, _ in kills):
        rule = next((r, s) for _, _, s, r in kills if s == "record")
        reason = rule[0]
        term = next(k.term for k in taxonomy.exclude
                    if (k.reason or k.term) == reason and k.scope == "record")
        return [], [f"record killed by '{term}': {reason}"]

    spans: list[MatchedSpan] = []
    killed: list[str] = []
    for tier, terms in (("core", taxonomy.core),
                        ("adjacent", taxonomy.adjacent)):
        for term in terms:
            for start, end in _spans_for(text, term):
                near = next(
                    (r for ks, ke, scope, r in kills
                     if scope == "span"
                     and start < ke + KILL_WINDOW
                     and end > ks - KILL_WINDOW), None)
                if near is not None:
                    killed.append(f"'{text[start:end]}' killed: {near}")
                    continue
                # a kill-rule phrase that CONTAINS the match is the same
                # false-positive shape even without window overlap
                spans.append(_mk_span(text, field, term, tier, start, end))
    return spans, killed


def record_text_fields(record: dict) -> list[tuple[str, str]]:
    """The record's readable text surfaces, including one level of
    raw_payload nesting."""
    out = []
    for key in TEXT_KEYS:
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            out.append((key, val))
    raw = record.get("raw_payload")
    if isinstance(raw, dict):
        for key in TEXT_KEYS:
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                out.append((f"raw_payload.{key}", val))
        # Full-description retrieval evidence is stored as independent,
        # bounded contexts.  Score each context separately: concatenating
        # contexts would allow a phrase to bridge two unrelated source spans.
        evidence = raw.get("screen_evidence_matches")
        for index, row in enumerate(
                evidence if isinstance(evidence, list) else []):
            if not isinstance(row, dict):
                continue
            context = row.get("context")
            if isinstance(context, str) and context.strip():
                out.append((
                    f"raw_payload.screen_evidence_matches[{index}].context",
                    context,
                ))
    return out


def _source_code(value) -> Optional[str]:
    """Normalize the code shapes emitted by federal source adapters.

    USAspending can return NAICS and PSC as either a scalar code or a
    ``{"code": ..., "description": ...}`` object.  Boundary evaluation is
    always against the code itself; stringifying the object would turn a
    valid in-boundary record into a false off-code exclusion.
    """
    if isinstance(value, dict):
        value = value.get("code")
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _code_of(record: dict) -> tuple[Optional[str], Optional[str]]:
    naics = record.get("naics_code") or record.get("naics")
    psc = record.get("psc_code") or record.get("psc")
    raw = record.get("raw_payload")
    if isinstance(raw, dict):
        naics = naics or raw.get("naics_code") or raw.get("naics")
        psc = psc or raw.get("psc_code") or raw.get("psc")
    return _source_code(naics), _source_code(psc)


def score_record(record: dict,
                 taxonomy: CapabilityTaxonomy,
                 engagement_scope=None) -> RelevanceVerdict:
    """The engine verdict for one notice/award/forecast record.

    `engagement_scope` (EngagementScope | None) is the second exclude-only
    boundary: out-of-scope records score normally but never count as
    in-scope; a strong hit becomes OFF-SCOPE SIGNAL (internal only)."""
    spans: list[MatchedSpan] = []
    killed: list[str] = []
    for field, text in record_text_fields(record):
        s, k = score_text(text, taxonomy, field=field)
        spans.extend(s)
        killed.extend(k)

    core_terms = sorted({s.term for s in spans if s.tier == "core"})
    adjacent_terms = sorted({s.term for s in spans if s.tier == "adjacent"})
    score = CORE_WEIGHT * len(core_terms) + ADJACENT_WEIGHT * len(adjacent_terms)
    relevant = bool(core_terms) and score >= RELEVANCE_THRESHOLD

    off_code = False
    excluded_by_code = False
    universe = taxonomy.code_universe
    if universe is not None:
        naics, psc = _code_of(record)
        in_universe = True
        if naics is not None and universe.naics:
            in_universe = naics in universe.naics
        if in_universe and psc is not None and universe.psc:
            in_universe = psc in universe.psc
        if not in_universe:
            if relevant and score >= OFF_CODE_STRONG_THRESHOLD:
                off_code = True          # OFF-CODE SIGNAL, internal only
            else:
                excluded_by_code = True
                relevant = False

    from tools.relevance.scope import in_scope
    inside, scope_basis = in_scope(record, engagement_scope)
    off_scope = not inside
    off_scope_signal = False
    if off_scope:
        if relevant and score >= OFF_CODE_STRONG_THRESHOLD:
            off_scope_signal = True
        relevant = False          # a boundary never includes; never counts

    return RelevanceVerdict(
        score=score, relevant=relevant,
        core_terms=core_terms, adjacent_terms=adjacent_terms,
        spans=spans, killed=killed,
        off_code=off_code, excluded_by_code=excluded_by_code,
        off_scope=off_scope, off_scope_signal=off_scope_signal,
        scope_basis=scope_basis,
        taxonomy_client=taxonomy.client_name,
        taxonomy_version=taxonomy.version)


def _rows_citing(node, ids: set[str], out: list[dict]) -> None:
    if isinstance(node, dict):
        blob = " ".join(str(v) for v in node.values()
                        if isinstance(v, (str, int, float)))
        if any(i in blob for i in ids):
            out.append(node)
        for v in node.values():
            _rows_citing(v, ids, out)
    elif isinstance(node, list):
        for v in node:
            _rows_citing(v, ids, out)


def score_opportunity_evidence(sweep: dict, record_ids: list[str],
                               taxonomy: CapabilityTaxonomy) -> RelevanceVerdict:
    """The verdict for a RENDERED opportunity: every stored-sweep row citing
    any of its record ids contributes its text surfaces, so the justification
    quotes the same evidence the pipeline used (award descriptions, subaward
    descriptions, buyer-map products/terms), never just one row's fields."""
    ids = {i for i in (record_ids or []) if i}
    if not ids:
        return score_record({}, taxonomy)
    rows: list[dict] = []
    _rows_citing(sweep, ids, rows)
    spans: list[MatchedSpan] = []
    killed: list[str] = []
    seen_texts: set[str] = set()
    for row in rows:
        for field, text in record_text_fields(row):
            if text in seen_texts:
                continue
            seen_texts.add(text)
            s, k = score_text(text, taxonomy, field=field)
            spans.extend(s)
            killed.extend(k)
        for listy in ("products", "terms", "matched_products", "matched_terms"):
            for item in (row.get(listy) or []):
                if isinstance(item, str) and item not in seen_texts:
                    seen_texts.add(item)
                    s, k = score_text(item, taxonomy, field=listy)
                    spans.extend(s)
                    killed.extend(k)
    core_terms = sorted({s.term for s in spans if s.tier == "core"})
    adjacent_terms = sorted({s.term for s in spans if s.tier == "adjacent"})
    score = CORE_WEIGHT * len(core_terms) + ADJACENT_WEIGHT * len(adjacent_terms)
    return RelevanceVerdict(
        score=score, relevant=bool(core_terms) and score >= RELEVANCE_THRESHOLD,
        core_terms=core_terms, adjacent_terms=adjacent_terms,
        spans=spans, killed=killed,
        taxonomy_client=taxonomy.client_name,
        taxonomy_version=taxonomy.version)


def relevance_basis_violations(cards: list, *, machine_screened: bool) -> list[str]:
    """Nothing renders on code match alone, and nothing machine-screened
    renders out of engagement scope: both are lint-level impossibilities of
    the same family. Operator-included content rows are the operator's
    judgment: their scores render in the INTERNAL trail (zero-evidence and
    off-scope rows visibly so) without blocking, and the calibration
    harness surfaces them for adjudication."""
    out = []
    for label, verdict in cards:
        if not machine_screened:
            continue
        if not verdict.core_terms:
            out.append(
                f"RELEVANCE_BASIS: '{label}' rendered with no capability "
                f"evidence (score {verdict.score}); code membership alone "
                f"never renders (taxonomy v{verdict.taxonomy_version})")
        if verdict.off_scope:
            out.append(
                f"RELEVANCE_BASIS: '{label}' rendered out of engagement "
                f"scope ({verdict.scope_basis}); a boundary never includes "
                f"and an out-of-scope record never renders machine-screened")
    return out
