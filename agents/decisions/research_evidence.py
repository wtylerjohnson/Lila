"""Bounded evidence for the internal Research Picture, never a qualification gate."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    source_id: str
    passage_id: str
    quote: str = Field(min_length=1, max_length=2000)


class ResearchClaim(BaseModel):
    kind: Literal['procurement_state', 'offering_fit', 'value', 'incumbent',
                  'vehicle_route', 'next_action', 'context']
    statement: str = Field(max_length=2000)
    basis: Literal['source_fact', 'inference'] = 'source_fact'
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=6)
    section: Literal['headline', 'demand_signals', 'market_structure',
                     'watchlist', 'next_action'] = 'headline'


class EvidenceSource(BaseModel):
    source_id: str
    original_source_id: str | None = None
    lane: str
    locator: str
    title: str
    url: str | None = None
    notice_id: str | None = None
    solicitation_id: str | None = None
    retrieved_at: str | None = None
    posted_at: str | None = None
    deadline: str | None = None
    retrieval_status: str
    primary_notice: bool = False
    supplied_retrieval_statuses: list[str] = Field(default_factory=list)
    passages: dict[str, str] = Field(default_factory=dict)
    content_sha256: str
    truncated: bool = False
    binding_issues: list[str] = Field(default_factory=list)


# Only recorded scalar fields: no attachment research or model-generated triage.
_PASSAGE_FIELDS = ('description', 'description_snippet', 'full_text', 'text',
                   'summary', 'title', 'notice_type', 'type', 'active', 'status',
                   'response_deadline', 'deadline', 'posted_date', 'posted',
                   'estimated_value', 'amount', 'value', 'awardee', 'recipient_name',
                   'completion', 'piid', 'signal_type', 'vehicle', 'contract_number')
_TEXT_FIELDS = {'description', 'description_snippet', 'full_text', 'text'}
_NON_SOURCE_KEYS = {'triage', 'triage_prefilter', 'research_picture', '_attempts',
                    'source_coverage_verdict', 'decision_coverage_verdict',
                    'sam_census', 'term_yield'}


def safe_url(value) -> str | None:
    if not isinstance(value, str) or any(c.isspace() or c in '<>\"' for c in value):
        return None
    try:
        p = urlsplit(value)
        return value if p.scheme in {'http', 'https'} and p.hostname and not p.username else None
    except ValueError:
        return None


def _scalar(row, raw, *keys):
    for key in keys:
        for data in (row, raw):
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                return str(value)
    return None


def source_record(lane: str, locator: str, row: dict) -> EvidenceSource:
    raw = row.get('raw_payload')
    raw = raw if isinstance(raw, dict) else {}
    encoded = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    original = _scalar(row, raw, 'source_id', 'notice_id', 'id')
    sid = original or f'rp:{lane}:{digest[:16]}'
    passages = {}
    truncated = False
    for prefix, data in (('', row), ('raw_payload.', raw)):
        for key in _PASSAGE_FIELDS:
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                value = str(value)
                passages[prefix + key] = value[:2000]
                truncated |= len(value) > 2000
    url = safe_url(_scalar(row, raw, 'url', 'api_url', 'canonical_url', 'html_url'))
    host = urlsplit(url).hostname if url else ''
    notice_id = _scalar(row, raw, 'notice_id') or (original if lane == 'sam.gov' else None)
    url_identity = re.search(r'/opp/([^/]+)/', urlsplit(url).path + '/') if url else None
    primary = (lane == 'sam.gov' and host in {'sam.gov', 'www.sam.gov'}
               and url_identity is not None and url_identity.group(1) == notice_id)
    binding_issues = []
    if lane == 'sam.gov':
        identities = {str(d[k]).strip() for d in (row, raw)
                      for k in ('source_id', 'notice_id', 'id') if d.get(k)}
        if len(identities) > 1:
            binding_issues.append('Conflicting primary notice identities.')
            primary = False
    deadlines = set()
    for data in (row, raw):
        for key in ('response_deadline', 'deadline'):
            value = data.get(key)
            if value is None or not str(value).strip():
                continue
            value = str(value).strip()
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo:
                    value = parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
            deadlines.add(value)
    deadline = _scalar(row, raw, 'response_deadline', 'deadline')
    if len(deadlines) > 1:
        binding_issues.append('Conflicting response deadlines; verify the original notice.')
        deadline = None
    has_text = any(k.split('.')[-1] in _TEXT_FIELDS for k in passages)
    supplied = [str(d[k]).casefold().strip() for d in (row, raw)
                for k in ('retrieval_status', 'evidence_status') if d.get(k)]
    failed = bool(row.get('error') or raw.get('error')) or any(
        state in {'failed', 'error', 'unavailable', 'unreadable', 'not_fetched', 'not-fetched'}
        for state in supplied)
    discovery = lane == 'web' or 'discovery_only' in supplied
    restrictive = [v for v in supplied if v not in
                   {'ok', 'success', 'retrieved', 'stored_source_text', 'complete'}]
    status = ('failed' if failed else 'discovery_only' if discovery else
              restrictive[0] if restrictive else
              'stored_source_text' if has_text else 'metadata_only')
    return EvidenceSource(
        source_id=sid, original_source_id=original, lane=lane, locator=locator,
        title=_scalar(row, raw, 'title', 'name') or sid, url=url,
        notice_id=notice_id,
        solicitation_id=_scalar(row, raw, 'solicitation', 'solicitation_number'),
        retrieved_at=_scalar(row, raw, 'retrieved_at', 'fetched_at', 'observed_at'),
        posted_at=_scalar(row, raw, 'posted_date', 'posted', 'published'),
        deadline=deadline,
        retrieval_status=status, primary_notice=primary, passages=passages,
        supplied_retrieval_statuses=supplied,
        content_sha256=digest, truncated=truncated, binding_issues=binding_issues)


def coverage_gaps(results: dict) -> list[str]:
    gaps = []
    coverage = results.get('source_coverage_verdict') or {}
    if isinstance(coverage, dict):
        if coverage.get('comprehensive') is False:
            gaps.append('Source coverage is not comprehensive; this is not an empty-market finding.')
        for lane in coverage.get('lanes', []):
            if isinstance(lane, dict) and lane.get('required') and lane.get('state') not in {
                    'complete', 'complete-within-boundary', 'screened-zero', 'scope-excluded'}:
                gaps.append(f"Required source {lane.get('source')}: {lane.get('state', 'unknown')}.")
        for name in coverage.get('blocking_sources', []):
            if not any(f'Required source {name}:' in gap for gap in gaps):
                gaps.append(f'Required source {name}: coverage incomplete.')
    for attempt in results.get('_attempts', []):
        if isinstance(attempt, dict) and attempt.get('ok') is False:
            gaps.append(f"Source {attempt.get('source', 'unknown')}: retrieval failed.")
    for name, value in results.items():
        if name not in _NON_SOURCE_KEYS and isinstance(value, dict) and value.get('error'):
            gaps.append(f'Source {name}: retrieval failed.')
    return list(dict.fromkeys(gaps))


def build_registry(results: dict, sweep: dict) -> tuple[dict[str, EvidenceSource], list[str]]:
    """Keep native selection/caps; disclose missing or conflicting evidence."""
    registry = {}
    collisions = set()
    gaps = coverage_gaps(results)
    remaining_chars = 120000
    selected = {r['id'] for k in ('pursue_notices', 'monitor_notices')
                for r in sweep.get(k, [])}

    def walk(value, path, depth=0):
        if depth > 5:
            return
        if isinstance(value, list):
            for i, row in enumerate(value):
                yield from walk(row, f'{path}[{i}]', depth + 1)
        elif isinstance(value, dict):
            if value.get('error'):
                return
            if any(value.get(k) for k in ('source_id', 'notice_id', 'title', 'piid', 'name')):
                yield path, value
            else:
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        yield from walk(child, f'{path}.{key}', depth + 1)

    # Web and SAM first, then a bounded sample of each contextual lane.
    lanes = ['sam.gov', 'web'] + sorted(set(results) - _NON_SOURCE_KEYS - {'sam.gov', 'web'})
    for lane in lanes:
        rows = list(walk(results.get(lane), lane))
        if lane == 'sam.gov':
            rows = [(path, row) for path, row in rows
                    if (row.get('source_id') or row.get('notice_id')) in selected]
        cap = 30 if lane == 'sam.gov' else 10
        if len(rows) > cap:
            gaps.append(f'{lane}: evidence context capped at {cap} of {len(rows)} records in source order.')
        for path, row in rows[:cap]:
            source = source_record(lane, path, row)
            sid = source.source_id
            if sid in collisions:
                continue
            if sid in registry:
                if registry[sid].content_sha256 != source.content_sha256:
                    registry.pop(sid)
                    collisions.add(sid)
                    gaps.append(f'Ambiguous source ID {sid}: conflicting records excluded.')
                continue
            size = len(source.model_dump_json())
            if len(registry) >= 80 or size > remaining_chars:
                gaps.append(f'{lane}: evidence registry budget reached; further source evidence omitted.')
                break
            registry[sid] = source
            gaps.extend(f'{sid}: {issue}' for issue in source.binding_issues)
            remaining_chars -= size
            if source.retrieval_status not in {'stored_source_text', 'metadata_only', 'discovery_only'}:
                gaps.append(f'{sid}: retrieval state {source.retrieval_status}.')
            if source.truncated:
                gaps.append(f'{sid}: source passages truncated to 2000 characters per field.')
    return registry, gaps


def checked_claim(claim: ResearchClaim, registry: dict[str, EvidenceSource]):
    """Quote existence proves traceability, never entailment of a paraphrase."""
    if not claim.evidence:
        return None, 'missing evidence references'
    excerpts = []
    for ref in claim.evidence:
        if not any(character.isalnum() for character in ref.quote):
            return None, 'empty or non-substantive evidence quote'
        source = registry.get(ref.source_id)
        if source is None:
            return None, f'unknown source ID {ref.source_id}'
        passage = source.passages.get(ref.passage_id)
        if source.retrieval_status == 'failed' or passage is None or ref.quote not in passage:
            return None, f'unsupported passage or quote {ref.source_id}/{ref.passage_id}'
        excerpts.append(ref.quote)
    if claim.basis == 'inference' and claim.kind in {
            'procurement_state', 'value', 'incumbent', 'vehicle_route'}:
        return None, f'{claim.kind} requires source facts, not inference'
    # No generated factual wording survives just because a citation exists.
    statement = claim.statement if claim.basis == 'inference' else ' | '.join(dict.fromkeys(excerpts))
    return claim.model_copy(update={'statement': statement}), None


def _timestamp(value: str | None):
    try:
        parsed = datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def current_notice(source: EvidenceSource, claims: list[ResearchClaim], as_of: datetime) -> bool:
    """A documented notice is still subject to existing qualification/requirement review."""
    if (not source.primary_notice or source.binding_issues
            or source.retrieval_status != 'stored_source_text'):
        return False
    fetched, deadline = _timestamp(source.retrieved_at), _timestamp(source.deadline)
    if not fetched or fetched > as_of or fetched.date() != as_of.date() or not deadline or deadline <= as_of:
        return False
    active_values = [v.casefold().strip() for k, v in source.passages.items()
                     if k.split('.')[-1] == 'active']
    types = [v.casefold().strip() for k, v in source.passages.items()
             if k.split('.')[-1] in {'notice_type', 'type'}]
    if not active_values or any(v not in {'yes', 'true', '1'} for v in active_values):
        return False
    if not types or any(v not in {'solicitation', 'combined synopsis/solicitation', 'sources sought'}
                        for v in types):
        return False
    kinds = set()
    for claim in claims:
        if claim.basis != 'source_fact':
            continue
        if any(ref.source_id == source.source_id and
               ref.passage_id.split('.')[-1] in _TEXT_FIELDS for ref in claim.evidence):
            kinds.add(claim.kind)
    return {'procurement_state', 'offering_fit', 'next_action'} <= kinds


def classification(source: EvidenceSource, claims: list[ResearchClaim], as_of: datetime) -> str:
    if current_notice(source, claims, as_of):
        return 'confirmed_opportunity'
    if source.lane in {'contract_awards', 'usaspending.gov', 'dod_contracts'}:
        return 'historical_market_evidence'
    if source.lane != 'web' and source.retrieval_status == 'stored_source_text' and any(
            c.kind == 'vehicle_route' and c.basis == 'source_fact' and
            any(r.source_id == source.source_id for r in c.evidence) for c in claims):
        return 'channel_fact'
    return 'research_signal'


def clean_text(value: str) -> str:
    """Plain Markdown text: source/model content cannot create headings or links."""
    value = value.replace('\u2014', '; ').replace('\n', ' ')
    return re.sub(r'([\\`*_{}\[\]<>#!|])', r'\\\1', value)


def claim_text(claim: ResearchClaim, registry: dict[str, EvidenceSource]) -> str:
    sources = [registry[ref.source_id] for ref in claim.evidence]
    prefix = ('Inference (requires verification)' if claim.basis == 'inference' else
              'Discovery excerpt (unverified)' if any(s.retrieval_status == 'discovery_only' for s in sources)
              else 'Recorded source excerpt (retrieval state unverified)' if any(
                  s.retrieval_status not in {'stored_source_text', 'metadata_only'} for s in sources)
              else 'Recorded source excerpt')
    citations = []
    for ref in claim.evidence:
        source = registry[ref.source_id]
        label = clean_text(f'{source.source_id}/{ref.passage_id}')
        citations.append(f'[{label}](<{source.url}>)' if source.url else label)
    return f'{prefix}: {clean_text(claim.statement)} ({"; ".join(citations)})'
