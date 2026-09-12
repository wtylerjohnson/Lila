"""Conservative clause evidence for discovery, never entailment or Assess approval.

Recomputed from stored text at each consuming boundary. Unknown language stays
unresolved; a role label supplied by a caller is never an input to this decision.
"""
from __future__ import annotations

import hashlib
import json
import re

from tools.relevance.engine import record_text_fields, score_record, score_text
from tools.relevance.notice_type import notice_type_evidence

VERSION = "requirement-support-v1"
NEGATIVE_ROLES = {"negated_requirement", "evaluation_boilerplate", "reference_guidance",
                  "historical_mention", "quoted_prior_work", "company_name_collision"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def clause_role(text: str, start: int, end: int, *, field: str = "description") -> dict:
    """Resolve a bounded sentence containing this exact span, with its offsets.

    These explicit language rules establish only a reviewable requested-work
    signal. They do not prove that an arbitrary paraphrase follows from a quote.
    """
    lo = max([0, *[m.end() for m in re.finditer(r'[.!?;\n]\s*', text[:start])]])
    tail = re.search(r'[.!?;\n](?:\s|$)', text[end:])
    hi = end + tail.start() + 1 if tail else len(text)
    # An adversative clause can change the object/scope of a requirement.
    for split in re.finditer(r',?\s+\b(?:but(?! also)|however|whereas)\b\s*', text[lo:hi], re.I):
        boundary = lo + split.start()
        if boundary < start:
            lo = lo + split.end()
            break
        if boundary >= end:
            hi = boundary
            break
    cut = start - lo > 350 or hi - end > 350
    lo, hi = max(lo, start - 350), min(hi, end + 350)
    quote = text[lo:hi]
    low = quote.casefold()
    before = text[lo:start].casefold()
    after = text[end:hi].casefold()
    request = re.search(r'\b(?:shall|must)\s+(?:provide|deliver|implement|configure|perform|supply|support|develop|install|maintain|integrate|replace)\b|\b(?:requires?|seeks?|seeking|purchases?)\b|\brequirement (?:is|includes)\b|(?:^|:\s*)(?:provide|deliver|implement|supply|perform|replace)\b', before)
    prior = text[max(0, lo - 220):lo].casefold()
    inherited = re.search(r'(?:quoted prior|prior work|historical background|previous requirement|not (?:current|a requirement)|excluded from (?:this|the) (?:purchase|scope))[^.!?]*[.:\n]\s*$', prior)
    role = "context_missing"
    # A title, URL, or cut clause cannot establish a purchased deliverable.
    if field.split('.')[-1] in {'title', 'label'}:
        role = 'title_only'
    elif re.fullmatch(r'\s*https?://\S+\s*', text):
        role = 'material_source_missing'
    elif cut:
        role = 'truncated_context'
    elif re.search(r'\b(?:unavailable|missing|unreadable)\b.{0,50}\b(?:sow|attachment|scope)\b', low):
        role = 'material_source_missing'
    elif inherited or re.search(r'\bquoted (?:prior|previous)|excluded from this purchase', low):
        role = 'quoted_prior_work'
    elif re.match(r'\s*(?:llc|inc\b|corporation\b)', after) or re.search(r'awardee name', before):
        role = 'company_name_collision'
    elif re.search(r'\b(?:not required|shall not|must not|no longer|out of scope|excludes?|excluded|not included|not seeking|not buying|do not|does not)\b', low) or re.search(r'\bno\s+', before):
        role = 'negated_requirement'
    elif re.search(r'\b(?:previously|historical|prior work|past work|purchased|was awarded|were awarded)\b', low) and not request:
        role = 'historical_mention'
    elif re.search(r'\b(?:evaluation|sprs|supplier performance risk system|past performance)\b', before) and not request:
        role = 'evaluation_boilerplate'
    elif re.search(r'\b(?:refers?|references?|discusses?|mentions?)\b.{0,90}\b(?:guidance|handbook|manual)\b|\bguidance (?:on|about)\b', before):
        role = 'reference_guidance'
    else:
        # Require the request verb in the same clause and before the matched
        # concept (or an explicit passive predicate immediately after it).
        passive = re.match(r'.{0,60}\b(?:is required|are required|must be provided|shall be provided)\b', after)
        if request or passive:
            role = 'requested_deliverable'
        elif re.search(r'\b(?:guidance|handbook|reference|references)\b', low):
            role = 'reference_guidance'
    return {"role": role, "quote": quote, "context_start": lo, "context_end": hi,
            "context_truncated": cut, "method": VERSION}


def _origin(record: dict, field: str, text: str, start: int, matched: str) -> dict:
    """Keep retained-context offsets distinct from original-field offsets."""
    result = {"original_field": field, "original_start": start,
              "field_sha256": digest(text), "provenance_state": "stored_field"}
    m = re.fullmatch(r'raw_payload.screen_evidence_matches\[(\d+)\].context', field)
    if not m:
        raw = record.get('raw_payload') or {}
        if field == 'raw_payload.description_snippet' and raw.get('via') == 'daily-extract':
            result.update(original_field='description', field_sha256=raw.get('description_sha256') or digest(text))
        return result
    row = record['raw_payload']['screen_evidence_matches'][int(m[1])]
    result.update(original_field=row.get('field'), original_start=None,
                  provenance_state='legacy_context_origin_unresolved')
    if row.get('version') != 'extract-context-v2':
        return result
    offset, length = row.get('context_start'), row.get('field_characters')
    valid = (isinstance(offset, int) and isinstance(length, int) and offset >= 0
             and offset + len(text) <= length and row.get('context_end') == offset + len(text)
             and row.get('context_sha256') == digest(text)
             and re.fullmatch('[0-9a-f]{64}', str(row.get('field_sha256') or '')) is not None
             and isinstance(row.get('start'), int) and isinstance(row.get('end'), int)
             and row['start'] >= offset and row['end'] <= offset + len(text)
             and text[row['start']-offset:row['end']-offset] == row.get('matched_text'))
    if row.get('source_id') and row['source_id'] != str(record.get('source_id') or record.get('id') or ''):
        valid = False
    result.update(provenance_state='retained_extract_context' if valid else 'invalid_context_receipt')
    if valid:
        raw = record['raw_payload']
        parent = record.get(row.get('field')) or raw.get(row.get('field'))
        if isinstance(parent, str):
            valid = digest(parent) == row['field_sha256'] and parent[offset:offset + len(text)] == text
        elif row.get('field') == 'description' and raw.get('description_sha256'):
            valid = (row['field_sha256'] == raw['description_sha256']
                     and length == raw.get('description_characters'))
            snippet = raw.get('description_snippet') or ''
            if offset < len(snippet):
                valid = valid and snippet[offset:min(len(snippet), offset + len(text))] == text[:max(0, len(snippet)-offset)]
        result['provenance_state'] = 'retained_extract_context' if valid else 'invalid_context_receipt'
    if valid:
        result.update(original_start=offset + start, field_sha256=row['field_sha256'],
                      field_characters=length, context_start=offset,
                      source_field_truncated=row.get('context_truncated', False))
    return result


def requirement_support(record: dict, taxonomy, *, verdict=None) -> dict:
    """Return attributable roles and a tri-state requested-work determination."""
    verdict = verdict or score_record(record, taxonomy)
    fields = dict(record_text_fields(record))
    raw = record.get('raw_payload') if isinstance(record.get('raw_payload'), dict) else {}
    spans = []
    for span in verdict.spans[:64]:
        if span.tier not in {'core', 'adjacent'}:
            continue
        text = fields.get(span.field, '')
        if text[span.start:span.start + len(span.matched_text)] != span.matched_text:
            continue
        origin = _origin(record, span.field, text, span.start, span.matched_text)
        role = clause_role(text, span.start, span.start + len(span.matched_text), field=origin['original_field'] or span.field)
        if origin['provenance_state'] in {'legacy_context_origin_unresolved', 'invalid_context_receipt'}:
            role['role'] = 'source_origin_unresolved'
        if origin.get('source_field_truncated'):
            role['role'] = 'truncated_context'
        spans.append({**role, **origin, 'field': span.field, 'start': span.start,
                      'matched_text': span.matched_text, 'term': span.term, 'tier': span.tier,
                      'source_id': str(record.get('source_id') or record.get('id') or ''),
                      'stored_field_sha256': digest(text)})
    positive = [s for s in spans if s['role'] == 'requested_deliverable']
    negative = [s for s in spans if s['role'] == 'negated_requirement']
    contradictory = bool({s['term'] for s in positive} & {s['term'] for s in negative})
    lifecycle = notice_type_evidence(record)
    coverage = raw.get('screen_evidence_coverage') or {}
    overflow = bool(coverage.get('overflow')) or len(verdict.spans) > 64
    # Incomplete retained text cannot prove that no later clause reverses it.
    complete_capture = (coverage.get('version') == 'extract-context-v2'
                        and coverage.get('all_keyword_occurrences_scanned') is True
                        and coverage.get('occurrences') == coverage.get('retained')
                        and isinstance(raw.get('description_characters'), int)
                        and bool(raw.get('description_sha256')))
    unscanned = bool(record.get('description_truncated')) or (bool(raw.get('description_truncated')) and not complete_capture)
    if lifecycle['historical']:
        support, state = False, 'historical_award'
    elif lifecycle['conflict'] or contradictory or overflow or unscanned or any(s['role'] == 'source_origin_unresolved' for s in spans):
        support, state = None, 'contradictory_context' if contradictory else 'evidence_incomplete'
    elif positive:
        support, state = True, 'requested_deliverable'
    elif spans and all(s['role'] in NEGATIVE_ROLES or s['role'] == 'title_only' for s in spans) and any(s['role'] in NEGATIVE_ROLES for s in spans):
        support, state = False, 'incidental_or_excluded'
    elif spans:
        support, state = None, 'context_unresolved'
    else:
        support, state = False, 'no_capability_span'
        # A phrase assembled across unrelated fields is a gap, never support.
        combined, _ = score_text(' '.join(fields.values()), taxonomy)
        if any(s.tier in {'core', 'adjacent'} for s in combined):
            support, state = None, 'incomplete_cross_field_phrase'
    return {'version': VERSION, 'source_id': str(record.get('source_id') or record.get('id') or ''),
            'record_sha256': digest(json.dumps(record, sort_keys=True, separators=(',', ':'), default=str)),
            'taxonomy_client': taxonomy.client_name, 'taxonomy_version': taxonomy.version,
            'requested_support': support, 'state': state, 'spans': spans,
            'overflow': overflow, 'qualification': 'NOT_ESTABLISHED',
            'limitations': ['Conservative clause rules, not semantic entailment.',
                            'Stored-source attribution is not independent source authentication.']}


def family_diagnostic(notices: list[dict], screenings: dict) -> dict:
    """Unique evidence-bearing families by term/field, separate from term_yield."""
    from agents.decisions.triage import _notice_thread_key
    groups = {}
    for index, notice in enumerate(notices):
        sid = str(notice.get('source_id') or notice.get('id') or f'idx-{index}')
        evidence = screenings[sid]
        support = evidence.get('requirement_support') or {}
        family = (str(notice.get('source') or 'sam.gov'), *_notice_thread_key(notice, index))
        for span in support.get('spans', []):
            key = (span['term'], span['original_field'], family)
            row = groups.setdefault(key, {'term': span['term'], 'field': span['original_field'],
                'family': list(family), 'grouping': 'structured' if family[1] == 'solicitation' else 'notice_fallback',
                'notice_ids': [], 'evidence': [], 'buckets': set()})
            if sid not in row['notice_ids']:
                row['notice_ids'].append(sid)
            bucket = ('historical' if evidence['notice_type_evidence']['historical'] else
                      'unresolved' if support.get('requested_support') is None else
                      'requested' if span['role'] == 'requested_deliverable' else
                      'incidental' if span['role'] in NEGATIVE_ROLES else 'unresolved')
            if evidence.get('screen_state') == 'CONSOLIDATED_POSTING':
                bucket = 'superseded'
            row['buckets'].add(bucket)
            row['evidence'].append({'source_id': sid, 'field': span['field'], 'start': span['start'],
                                    'currentness': 'superseded' if evidence.get('screen_state') == 'CONSOLIDATED_POSTING' else 'selected_or_unresolved',
                                    'original_start': span['original_start'], 'role': span['role']})
    rows = []
    for row in groups.values():
        row['buckets'] = sorted(row['buckets'])
        rows.append(row)
    return {'version': VERSION, 'population': 'supplied_notice_census', 'families_by_term_field': rows,
            'unique_lexical_families': len({tuple(r['family']) for r in rows}),
            'limitation': 'Overlapping field buckets are not additive totals; historical spans are retained.'}
