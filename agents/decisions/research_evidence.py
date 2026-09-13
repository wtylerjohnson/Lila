"""Bounded evidence for the internal Research Picture, never a qualification gate."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from tools.relevance.temporal import record_time, instant, temporal_assessment


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
    temporal_evidence: dict = Field(default_factory=dict)
    passage_authority: dict[str, dict] = Field(default_factory=dict)
    attachment_provenance: dict = Field(default_factory=dict)
    content_sha256: str
    truncated: bool = False
    binding_issues: list[str] = Field(default_factory=list)


# Only recorded scalar fields: no attachment research or model-generated triage.
_PASSAGE_FIELDS = ('description', 'description_snippet', 'full_text', 'text',
                   'summary', 'title', 'notice_type', 'type', 'active', 'status',
                   'response_deadline', 'deadline', 'responseDeadLine', 'responseDeadline',
                   'posted_date', 'posted', 'postedDate',
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
    authority = {}
    truncated = False
    for prefix, data in (('', row), ('raw_payload.', raw)):
        for key in _PASSAGE_FIELDS:
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                value = str(value)
                field = prefix + key
                passages[field] = value[:2000]
                # Native attachment enrichment owns raw text. Missing or forged
                # cached authority cannot upgrade this field to notice evidence.
                level = ('discovery_only' if key == 'text' else
                         'original_notice' if lane == 'sam.gov' else 'recorded_context')
                authority[field] = {'authority': level, 'original_field': field,
                                    'source_id': sid, 'truncated': len(value) > 2000}
                truncated |= len(value) > 2000 and level != 'discovery_only'
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
    temporal = {kind: record_time(row, kind) for kind in ('deadline', 'posted', 'observed')}
    deadline = temporal['deadline']['selected']['raw']
    if temporal['deadline']['conflict']:
        binding_issues.append('Conflicting response deadlines; verify the original notice.')
        deadline = None
    if temporal['observed']['conflict']:
        binding_issues.append('Conflicting source observation timestamps.')
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
        retrieved_at=(str(temporal['observed']['selected']['raw']) if temporal['observed']['selected']['raw'] is not None else None),
        posted_at=(str(temporal['posted']['selected']['raw']) if temporal['posted']['selected']['raw'] is not None else None),
        deadline=str(deadline) if deadline is not None else None,
        retrieval_status=status, primary_notice=primary, passages=passages,
        supplied_retrieval_statuses=supplied, temporal_evidence=temporal,
        passage_authority=authority,
        attachment_provenance={k: raw[k] for k in (
            'attachment_evidence', 'attachment_inventory_hash', 'attachment_evidence_sha256',
            'attachment_text_sha256', 'attachment_text_authority', 'attachment_research') if k in raw},
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
    # Recompute amendment chronology from the complete original SAM census.
    # Saved triage choices and an attractive old source cannot authenticate it.
    from agents.decisions.triage import _latest_notice_threads
    sam_rows = [r for r in results.get('sam.gov', []) if isinstance(r, dict)] if isinstance(results.get('sam.gov'), list) else []
    latest, superseded, _ = _latest_notice_threads(sam_rows)
    uncertain = {sid for row in latest if row.get('raw_payload', {}).get('triage_thread_order_status') == 'unresolved'
                 for sid in row['raw_payload']['triage_thread_members']}
    for sid, source in registry.items():
        if source.lane != 'sam.gov':
            continue
        issue = ('Amendment chronology unresolved; current action withheld.' if sid in uncertain else
                 f"Superseded by source {superseded[sid]['superseded_by']}; retained as context." if sid in superseded else None)
        if issue:
            source.binding_issues.append(issue)
            gaps.append(f'{sid}: {issue}')
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


def _timestamp(value):
    return instant(value)


def _current_response_instruction(quote: str, as_of: datetime, cited_quote: str | None = None, *, deadline=None) -> bool:
    from tools.relevance.response_action import response_instruction
    return response_instruction(quote, as_of, cited_quote, deadline=deadline)['supported']


def trusted_passage(source: EvidenceSource, field: str) -> bool:
    return source.passage_authority.get(field, {}).get('authority') == 'original_notice'


def source_temporal_assessment(source: EvidenceSource, as_of: datetime, clocks=None):
    temporal = source.temporal_evidence
    return temporal_assessment(temporal.get('deadline') or record_time({'response_deadline': source.deadline}, 'deadline'),
                               temporal.get('observed') or record_time({'retrieved_at': source.retrieved_at}, 'observed'),
                               as_of, cutoff=(clocks or {}).get('evidence_cutoff_at'),
                               posted=temporal.get('posted'))


def source_action_evidence(source: EvidenceSource, claims: list[ResearchClaim], as_of: datetime):
    from tools.relevance.response_action import response_instruction
    return [dict(source_id=source.source_id, passage_id=ref.passage_id,
                 authority=source.passage_authority.get(ref.passage_id, {}),
                 **response_instruction(source.passages.get(ref.passage_id, ''), as_of,
                                        ref.quote, deadline=source.deadline))
            for claim in claims if claim.basis == 'source_fact' and claim.kind == 'next_action'
            for ref in claim.evidence if ref.source_id == source.source_id]


def current_notice(source: EvidenceSource, claims: list[ResearchClaim], as_of: datetime, taxonomy=None, clocks=None) -> bool:
    """A documented notice is still subject to existing qualification/requirement review."""
    if (not source.primary_notice or source.binding_issues or source.truncated
            or source.retrieval_status != 'stored_source_text'):
        return False
    timing = source_temporal_assessment(source, as_of, clocks)
    if timing['source_freshness'] != 'fresh' or timing['response_window'] != 'open' or not timing['evidence_within_cutoff']:
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
               ref.passage_id.split('.')[-1] in _TEXT_FIELDS and trusted_passage(source, ref.passage_id) for ref in claim.evidence):
            kinds.add(claim.kind)
    if not {'procurement_state', 'offering_fit', 'next_action'} <= kinds or taxonomy is None:
        return False
    from tools.relevance.requirement_support import requirement_support
    # Rebuild from source passages, never from a supplied role or a saved
    # screening receipt. Passage IDs retain their exact original source field.
    row = {'source_id': source.source_id, 'raw_payload': {}}
    for field, text in source.passages.items():
        if field.split('.')[-1] in _TEXT_FIELDS and not trusted_passage(source, field):
            continue
        if field.startswith('raw_payload.'):
            row['raw_payload'][field.removeprefix('raw_payload.')] = text
        else:
            row[field] = text
    support = requirement_support(row, taxonomy)
    if support['requested_support'] is not True:
        return False
    offering = action = False
    for claim in claims:
        if claim.basis != 'source_fact':
            continue
        for ref in claim.evidence:
            if ref.source_id != source.source_id or not trusted_passage(source, ref.passage_id):
                continue
            if claim.kind == 'offering_fit':
                offering |= any(s['field'] == ref.passage_id and s['tier'] == 'core'
                                and s['role'] == 'requested_deliverable' and s['quote'] in ref.quote
                                for s in support['spans'])
            if claim.kind == 'next_action':
                # A deadline is not an instruction to respond, and a CORE
                # quote cannot fill this typed claim solely by being relabeled.
                action |= _current_response_instruction(source.passages.get(ref.passage_id, ''), as_of, ref.quote, deadline=source.deadline)
    return offering and action


def classification(source: EvidenceSource, claims: list[ResearchClaim], as_of: datetime, taxonomy=None, clocks=None) -> str:
    if current_notice(source, claims, as_of, taxonomy, clocks):
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
              'Discovery excerpt (unverified)' if any(s.retrieval_status == 'discovery_only' for s in sources) else
              'Discovery excerpt (not requirement approval)' if any(
                  registry[r.source_id].passage_authority.get(r.passage_id, {}).get('authority') == 'discovery_only'
                  or registry[r.source_id].retrieval_status == 'discovery_only' for r in claim.evidence)
              else 'Recorded source excerpt (retrieval state unverified)' if any(
                  s.retrieval_status not in {'stored_source_text', 'metadata_only'} for s in sources)
              else 'Recorded source excerpt')
    citations = []
    for ref in claim.evidence:
        source = registry[ref.source_id]
        label = clean_text(f'{source.source_id}/{ref.passage_id}')
        citations.append(f'[{label}](<{source.url}>)' if source.url else label)
    return f'{prefix}: {clean_text(claim.statement)} ({"; ".join(citations)})'
