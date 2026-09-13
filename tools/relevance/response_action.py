"""Bounded response instructions: full expressions and same-action conflicts."""
from __future__ import annotations

import re
from datetime import datetime
from tools.relevance.temporal import instant, source_time

MONTHS = r'January|February|March|April|May|June|July|August|September|October|November|December'
DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}|\b(?:' + MONTHS + r')\s+\d{1,2}(?:,?\s+\d{4})?', re.I)
CLOCK = re.compile(r'(?:T|[ \t]+(?:at[ \t]+)?)(?P<hour>\d{1,2}):(?P<rest>\d{2}(?::\d{2}(?:\.\d+)?)?)'
                   r'(?:[ \t]*(?P<ampm>am|pm)\b)?(?:[ \t]*(?P<zone>UTC|GMT|Z|[+-]\d{2}(?::?\d{2})?|[A-Za-z]+))?', re.I)
ACTION = re.compile(r'\b(?:submit|respond|send|provide)\b[^.!?;\n]{0,70}?\b(?P<object>responses?|questions?|proposals?|capability statements?)\b', re.I)
BOUNDARY = re.compile(r'[!?;\n]|\.(?=\s|$)')
STATES = r'archived|historical|previous|prior|current|new|amended'
HEADING = re.compile(r'\b(?P<colon>' + STATES + r')\s+(?:response\s+)?instructions?\s*:'
                     r'|^[ \t]*(?P<line>' + STATES + r')[ \t]+(?:response[ \t]+)?instructions?[ \t]*\r?$', re.I | re.M)
HISTORICAL = {'archived', 'historical', 'previous', 'prior'}


def _date_claim(match, clause, metadata, at):
    """Never discard an explicit clock because the day alone reconciles."""
    base = match.group()
    normalized = base if base[0].isdigit() else None
    if normalized is None:
        for fmt in ('%B %d, %Y', '%B %d %Y'):
            try:
                normalized = datetime.strptime(base, fmt).date().isoformat()
                break
            except ValueError:
                pass
    tail = clause[match.end():]
    clock = CLOCK.match(tail)
    end = match.end() + (clock.end() if clock else 0)
    reasons = []
    unresolved_clock = False
    if clock:
        hour = int(clock['hour'])
        if clock['ampm']:
            if not 1 <= hour <= 12:
                reasons.append('Invalid twelve-hour response time.')
            hour = hour % 12 + (12 if clock['ampm'].casefold() == 'pm' else 0)
        zone = clock['zone'] or ''
        if zone.upper() in {'UTC', 'GMT', 'Z'}:
            zone = '+00:00'
        elif zone and not zone.startswith(('+', '-')):
            reasons.append('Unresolved response timezone label.')
            zone = ''
        if normalized is not None:
            normalized += f'T{hour:02d}:' + clock['rest'] + zone
        else:
            reasons.append('Explicit response time lacks a complete recorded date.')
    elif re.match(r'T|[ \t]+at\b|[ \t]+\d{1,2}:', tail, re.I):
        unresolved_clock = True
        reasons.append('Unparsed explicit response time; date alone is insufficient.')
        end = len(clause)
    suffix = clause[end:end + 1]
    if suffix and (suffix.isalnum() or suffix in '_:+-/' or (suffix == '.' and clause[end + 1:end + 2].isalnum())):
        reasons.append('Malformed suffix on stated response date.')
        attached = re.match(r'[^\s,;!?)]*', clause[end:])
        end += len(attached.group()) if attached else 0
    expression = clause[match.start():end]
    if normalized is None and not clock:
        try:
            # Leap-safe calendar validation; no source year is invented.
            partial = datetime.strptime(base + ' 2000', '%B %d %Y')
        except ValueError:
            partial = None
        if partial is not None:
            agrees = bool(metadata['date_value'] and metadata['date_value'][5:] == partial.strftime('%m-%d'))
            parsed = dict(raw=expression, precision='month_day', parse_status='partial_date',
                          original_field='response_clause', metadata_month_day_agrees=agrees)
            if not agrees:
                reasons.append('Partial response day does not resolve against metadata.')
            return parsed, reasons
    parsed = source_time(normalized if normalized is not None else expression, field='response_clause')
    parsed['raw'] = expression
    if unresolved_clock:
        parsed.update(parse_status='unknown_time', precision='unknown_time', instant_utc=None)
    if reasons and clock and normalized is None:
        parsed['parse_status'] = 'unknown_date_precision'
    if parsed['parse_status'] == 'aware':
        when = instant(normalized)
        if when <= at:
            reasons.append('Stated response timestamp is closed.')
        if metadata['instant_utc'] and when != instant(metadata['raw']):
            reasons.append('Response prose and metadata timestamps conflict.')
    elif parsed['parse_status'] == 'date_only':
        if parsed['date_value'] < at.date().isoformat():
            reasons.append('Stated response day is past.')
        if metadata['date_value'] and parsed['date_value'] != metadata['date_value']:
            reasons.append('Response prose and metadata dates conflict.')
    else:
        reasons.append('Response date has unresolved precision or timezone.')
    return parsed, reasons


def response_instruction(text: str, as_of: datetime, cited_quote=None, *, deadline=None) -> dict:
    at = instant(as_of)
    if at is None:
        raise ValueError('Response assessment requires an aware timestamp')
    out = {'version': 'response-instruction-v3', 'supported': False, 'clauses': [], 'gaps': []}
    if cited_quote is not None and (not cited_quote or text.count(cited_quote) != 1):
        out['gaps'].append('Cited response span is missing or ambiguous.')
        return out
    start = text.index(cited_quote) if cited_quote is not None else 0
    stop = start + len(cited_quote) if cited_quote is not None else len(text)
    metadata = source_time(deadline)
    matches = list(ACTION.finditer(text))
    for index, match in enumerate(matches):
        left = list(BOUNDARY.finditer(text[:match.start()]))
        lo = left[-1].end() if left else 0
        tail = BOUNDARY.search(text, match.end())
        hi = tail.start() if tail else len(text)
        # Two explicit actions in one sentence retain separate deadline scope.
        if index + 1 < len(matches):
            hi = min(hi, matches[index + 1].start())
        if index and matches[index - 1].start() >= lo:
            lo = match.start()
        clause = text[lo:hi]
        reasons = []
        headings = list(HEADING.finditer(text[:match.start()]))
        heading = ((headings[-1]['colon'] or headings[-1]['line']).casefold() if headings else None)
        historical = heading in HISTORICAL or bool(re.search(r'\b(?:archived|historical|previous|prior|expired|closed)\b', clause, re.I))
        prior = text[max(0, lo - 220):lo]
        if (re.search(r'(?:archived|historical|previous|prior)[^.!?]*[.:\n]\s*$', prior, re.I)
                and heading not in {'current', 'new', 'amended'}):
            historical = True
        if historical:
            reasons.append('Archived or historical instruction context.')
        if re.search(r'\b(?:not|no|never)\b', clause, re.I):
            reasons.append('Negative response clause.')
        dates = []
        for date_match in DATE.finditer(clause):
            parsed, gaps = _date_claim(date_match, clause, metadata, at)
            dates.append(parsed)
            reasons.extend(gaps)
        if not dates and re.search(r'\b(?:by|before|until|due|deadline)\b', clause, re.I) and not re.search(r'\bby\s+(?:email|mail|portal|using)\b', clause, re.I):
            reasons.append('Response deadline language is unresolved.')
        if any(int(y) < at.year for y in re.findall(r'\b(?:19\d{2}|20\d{2})\b', clause)):
            reasons.append('Historical year in response instruction.')
        action = 'questions' if match['object'].casefold().startswith('question') else 'submission'
        out['clauses'].append(dict(text=clause, start=lo, end=hi, heading=heading,
                                   action=action, current_context=not historical,
                                   cited=match.start() >= start and match.end() <= stop,
                                   dates=dates, supported=not reasons, gaps=list(dict.fromkeys(reasons))))
    # A narrow quote cannot hide conflicting current instructions for the same
    # action elsewhere in this original passage. Questions remain a distinct
    # action; archived instructions do not compete with explicit current scope.
    for candidate in out['clauses']:
        if not candidate['cited'] or not candidate['supported']:
            continue
        competitors = [c for c in out['clauses'] if c['action'] == candidate['action'] and c['current_context']]
        if any(not c['supported'] for c in competitors):
            out['gaps'].append('Conflicting or unresolved current instructions for the cited response action.')
            continue
        out['supported'] = True
    if not any(c['cited'] for c in out['clauses']):
        out['gaps'].append('No unambiguous cited response instruction.')
    out['gaps'] = list(dict.fromkeys(out['gaps']))
    return out
