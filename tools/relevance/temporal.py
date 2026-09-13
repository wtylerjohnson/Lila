"""Source-bound temporal values; display dates never establish a timed action."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

VERSION = 'source-time-v1'
ISO = re.compile(r'(?P<day>\d{4}-\d{2}-\d{2})(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<fraction>\d{1,6}))?)?(?P<offset>Z|[+-]\d{2}(?::?\d{2})?)?)?')
FIELDS = {
    'deadline': ('response_deadline', 'deadline', 'responseDeadLine', 'responseDeadline'),
    'posted': ('posted_date', 'posted', 'postedDate', 'published'),
    'observed': ('retrieved_at', 'fetched_at', 'observed_at'),
}


def source_time(value, *, field='', source_id='') -> dict:
    raw = value.isoformat() if isinstance(value, (date, datetime)) else value
    out = dict(version=VERSION, source_id=str(source_id), original_field=field,
               raw=raw, instant_utc=None, date_value=None, offset_text=None,
               offset_minutes=None, precision=None, parse_status='missing')
    if raw is None or raw == '':
        return out
    if not isinstance(raw, str):
        return out | {'parse_status': 'invalid'}
    text = raw.strip()
    if not text:
        return out
    m = ISO.fullmatch(text)
    if not m:
        return out | {'parse_status': 'invalid'}
    try:
        day = date.fromisoformat(m['day'])
        out.update(date_value=day.isoformat(), precision='date')
        if m['hour'] is None:
            return out | {'parse_status': 'date_only'}
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        out.update(precision='fraction' if m['fraction'] else 'second' if m['second'] else 'minute',
                   offset_text=m['offset'])
        if parsed.tzinfo is None:
            return out | {'parse_status': 'unknown_timezone'}
        out.update(parse_status='aware', instant_utc=parsed.astimezone(timezone.utc).isoformat(),
                   offset_minutes=int(parsed.utcoffset().total_seconds() / 60))
        return out
    except ValueError:
        return out | {'parse_status': 'invalid', 'date_value': None, 'precision': None}


def display_date(value):
    parsed = source_time(value)
    return date.fromisoformat(parsed['date_value']) if parsed['date_value'] else None


def instant(value):
    parsed = source_time(value)
    return datetime.fromisoformat(parsed['instant_utc']) if parsed['instant_utc'] else None


def record_time(row: dict, kind: str) -> dict:
    """Recompute from original fields, ignoring cached temporal-evidence objects.

    SAM's canonical top-level date is a compatibility projection when it exactly
    equals the original raw field's local display date. Competing original fields
    remain independent claims; equal aware instants reconcile across offsets.
    """
    raw = row.get('raw_payload')
    raw = raw if isinstance(raw, dict) else {}
    sid = row.get('source_id') or row.get('notice_id') or row.get('id') or ''
    claims = []
    for prefix, data in (('raw_payload.', raw), ('', row)):
        for key in FIELDS[kind]:
            if data.get(key) is not None and str(data[key]).strip():
                claims.append(source_time(data[key], field=prefix + key, source_id=sid))
    originals = [x for x in claims if x['original_field'].startswith('raw_payload.')]
    projection = {'deadline': 'response_deadline', 'posted': 'posted_date'}.get(kind)
    derived = []
    for claim in claims:
        if (claim['original_field'] == projection and claim['parse_status'] == 'date_only'
                and originals and all(x['parse_status'] in {'aware', 'date_only', 'unknown_timezone'}
                                      and x['date_value'] == claim['date_value'] for x in originals)):
            derived.append(claim['original_field'])
    independent = [x for x in claims if x['original_field'] not in derived]
    keys = {(x['parse_status'], x['instant_utc'] or str(x['raw']).strip()) for x in independent}
    selected = independent[0] if independent else source_time(None, source_id=sid)
    conflict = len(keys) > 1
    return dict(version=VERSION, claims=claims, derived_display_fields=derived,
                selected=selected, conflict=conflict,
                status='conflict' if conflict else selected['parse_status'])


def latest_posting(rows: list[dict]) -> tuple[int, str]:
    """Unique supported chronology; input order is only a display fallback.

    Different date-only days support calendar ordering among date-only records.
    Equal, mixed-precision, offsetless, malformed or conflicting times do not.
    """
    values = [record_time(r, 'posted') for r in rows]
    def later(a, b):
        if a['conflict'] or b['conflict']:
            return False
        x, y = a['selected'], b['selected']
        if x['parse_status'] == y['parse_status'] == 'aware':
            return instant(x['raw']) > instant(y['raw'])
        if x['parse_status'] == y['parse_status'] == 'date_only':
            return x['date_value'] > y['date_value']
        return False
    winners = [i for i, x in enumerate(values) if all(i == j or later(x, y) for j, y in enumerate(values))]
    return (winners[0], 'ordered') if len(winners) == 1 else (0, 'unresolved')


def clock_context(as_of: datetime, *, mode='current', presented_at=None, supplied=None) -> dict:
    """Only caller arguments set reference clocks; saved/model values are ignored."""
    if mode not in {'current', 'historical'}:
        raise ValueError('Research Picture mode must be current or historical')
    assessed = instant(as_of)
    shown = instant(presented_at if presented_at is not None else as_of)
    if assessed is None or shown is None:
        raise ValueError('Assessment and presentation require explicit aware timestamps')
    if assessed > shown:
        raise ValueError('Assessment cannot be after presentation')
    supplied = supplied or {}
    allowed = {'query_started_at', 'capture_completed_at', 'evidence_cutoff_at'}
    if set(supplied) - allowed:
        raise ValueError('Unsupported clock role; observation must come from the source row')
    out = dict(version='research-clocks-v1', mode=mode,
               buying_status_as_of=assessed.isoformat(), presented_at=shown.isoformat(),
               basis='trusted_caller', freshness_policy='same_utc_day',
               deadline_policy='open_iff_assessment_strictly_before_deadline')
    for key in sorted(allowed):
        value = supplied.get(key)
        parsed = instant(value) if value is not None else None
        if value is not None and parsed is None:
            raise ValueError(f'{key} requires an aware timestamp')
        if parsed and parsed > shown:
            raise ValueError(f'{key} cannot be after presentation')
        out[key] = parsed.isoformat() if parsed else None
    query, capture = out['query_started_at'], out['capture_completed_at']
    if query and capture and instant(capture) < instant(query):
        raise ValueError('Capture cannot precede query start')
    out['capture_elapsed_seconds'] = (instant(capture) - instant(query)).total_seconds() if query and capture else None
    return out


def temporal_assessment(deadline: dict, observed: dict, as_of: datetime, *, cutoff=None, posted=None) -> dict:
    at = instant(as_of)
    if at is None:
        raise ValueError('Assessment requires an aware timestamp')
    end = None if deadline['conflict'] else instant(deadline['selected']['raw'])
    fetched = None if observed['conflict'] else instant(observed['selected']['raw'])
    freshness = ('unknown_' + observed['status'] if fetched is None else 'future' if fetched > at
                 else 'fresh' if fetched.date() == at.date() else 'stale')
    window = ('unknown_' + deadline['status'] if end is None else 'open' if at < end else 'closed')
    published = posted['selected'] if posted and not posted['conflict'] else None
    published_at = instant(published['raw']) if published else None
    publication_future = bool(published_at and published_at > at)
    if published and published['parse_status'] == 'date_only':
        publication_future |= published['date_value'] > at.date().isoformat()
    admitted = not publication_future and not (posted and posted['conflict'])
    if cutoff:
        limit = instant(cutoff)
        if not limit:
            raise ValueError('Evidence cutoff requires an aware timestamp')
        admitted &= bool(fetched and fetched <= limit and (published_at is None or published_at <= limit))
        if published and published['parse_status'] == 'date_only':
            admitted &= published['date_value'] <= limit.date().isoformat()
    return dict(response_window=window, source_freshness=freshness,
                evidence_within_cutoff=bool(admitted), publication_after_assessment=publication_future, source_observed_at=observed,
                deadline=deadline, buying_status_as_of=at.isoformat(),
                current_action='not_established')
