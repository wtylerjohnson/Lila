"""Structured notice families and complete supplied history, never title similarity."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy

from tools.relevance.temporal import latest_posting, record_time

VERSION = 'notice-family-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def original(row):
    """Transport is explanatory; a positive decision still requires a source rebind."""
    fields = row.get('source_fields') or {}
    transport = fields.get('notice_source_v1') or {}
    saved = transport.get('original_record')
    if isinstance(saved, dict):
        return deepcopy(saved)
    if row.get('source_id'):
        return deepcopy(row)
    # Legacy display/store records support identity diagnostics only.
    out = deepcopy(row)
    out.setdefault('source_id', row.get('notice_id') or row.get('record_id'))
    out.setdefault('solicitation', row.get('sol_number'))
    out.setdefault('posted_date', row.get('posted'))
    return out


def _norm(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def identity(row, index=0):
    raw = row.get('raw_payload') or {}
    raw = raw if isinstance(raw, dict) else {}
    values = []
    conflict = False
    for keys in (('solicitation', 'solicitation_number', 'solicitationNumber', 'sol_number'),
                 ('agency',), ('office',)):
        # Canonical agency strings may include display component paths. The
        # original structured namespace owns identity, as in native triage.
        claims = ([str(raw[k]).strip() for k in keys if raw.get(k)] or
                  [str(row[k]).strip() for k in keys if row.get(k)])
        # Identifier punctuation is significant. No suffix/call-number stripping.
        normalized = [(' '.join(v.split()).upper() if len(keys) > 1 else _norm(v)) for v in claims]
        conflict |= len(set(normalized)) > 1
        values.append(normalized[0] if normalized else '')
    sid = str(row.get('source_id') or row.get('notice_id') or row.get('record_id') or f'idx-{index}')
    if not conflict and all(values) and values[0] not in {'N/A', 'NONE', 'NA', 'UNKNOWN', '-'}:
        return ('solicitation', *values, str(row.get('source') or 'sam.gov'))
    return ('notice', sid) if not conflict else ('conflicted_notice', sid, str(index))


def resolve(rows):
    """Representatives follow original selected ordinals; every member is retained."""
    groups = {}
    for index, row in enumerate(rows):
        source = original(row)
        key = identity(source, index)
        groups.setdefault(key, []).append((index, row, source))
    out = []
    for key, members in groups.items():
        selected, status = latest_posting([source for _, _, source in members])
        ordinal, row, source = members[selected]
        sid = str(source.get('source_id') or source.get('notice_id') or row.get('record_id') or '')
        history = []
        for index, item, member in members:
            member_id = str(member.get('source_id') or member.get('notice_id') or item.get('record_id') or '')
            history.append(dict(source_id=member_id, native_ordinal=index,
                original_record=deepcopy(member), record_sha256=digest(member),
                posted=record_time(member, 'posted'), deadline=record_time(member, 'deadline'),
                status=('representative' if index == ordinal else 'superseded' if status == 'ordered' else 'order_unresolved'),
                superseded_by=sid if index != ordinal and status == 'ordered' else None))
        out.append(dict(version=VERSION, family_key=digest(key)[:20], family_basis=list(key),
            representative_id=sid, representative_ordinal=ordinal, order_status=status,
            member_ids=[m['source_id'] for m in history], members=history,
            history_scope='all supplied snapshots; unavailable prior store revisions are not reconstructed',
            representative=deepcopy(row)))
    return sorted(out, key=lambda f: f['representative_ordinal'])


def store_siblings(conn, rows):
    """Read all retained exact-family siblings before positive display selection."""
    rows = [dict(row) for row in rows]
    keys = {identity(original(row), i) for i, row in enumerate(rows)}
    numbers = list(dict.fromkeys(str(r.get('sol_number')) for r in rows if r.get('sol_number')))
    known = {str(r.get('notice_id')) for r in rows}
    out = list(rows)
    for start in range(0, len(numbers), 400):
        batch = numbers[start:start + 400]
        values = conn.execute('SELECT *, COALESCE(subtier, agency) org FROM notices WHERE sol_number IN (' +
                              ','.join('?' for _ in batch) + ')', batch).fetchall()
        for value in values:
            row = dict(value)
            sid = str(row.get('notice_id'))
            if sid not in known and identity(original(row)) in keys:
                out.append(row)
                known.add(sid)
    return out
