"""Bounded, cited response instructions with original heading and deadline scope."""
from __future__ import annotations

import re
from datetime import datetime
from tools.relevance.temporal import instant, source_time

MONTHS = r'January|February|March|April|May|June|July|August|September|October|November|December'
DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?'
                  r'|\b(?:' + MONTHS + r')\s+\d{1,2}(?:,?\s+\d{4})?', re.I)
ACTION = re.compile(r'\b(?:submit|respond|send|provide)\b[^.!?;\n]{0,70}\b(?:response|responses|questions|proposal|proposals|capability statement)\b', re.I)
BOUNDARY = re.compile(r'[!?;\n]|\.(?=\s|$)')
HEADING = re.compile(r'\b(archived|historical|previous|prior|current|new|amended)\s+(?:response\s+)?instructions?\s*:', re.I)


def response_instruction(text: str, as_of: datetime, cited_quote=None, *, deadline=None) -> dict:
    at = instant(as_of)
    if at is None:
        raise ValueError('Response assessment requires an aware timestamp')
    out = {'version': 'response-instruction-v2', 'supported': False, 'clauses': [], 'gaps': []}
    if cited_quote is not None and (not cited_quote or text.count(cited_quote) != 1):
        out['gaps'].append('Cited response span is missing or ambiguous.')
        return out
    start = text.index(cited_quote) if cited_quote is not None else 0
    stop = start + len(cited_quote) if cited_quote is not None else len(text)
    metadata = source_time(deadline)
    for match in ACTION.finditer(text):
        if match.start() < start or match.end() > stop:
            continue
        left = list(BOUNDARY.finditer(text[:match.start()]))
        lo = left[-1].end() if left else 0
        tail = BOUNDARY.search(text, match.end())
        hi = tail.start() if tail else len(text)
        clause = text[lo:hi]
        reasons = []
        headings = list(HEADING.finditer(text[:match.start()]))
        heading = headings[-1].group(1).casefold() if headings else None
        if heading in {'archived', 'historical', 'previous', 'prior'}:
            reasons.append('Archived or historical instruction context.')
        prior = text[max(0, lo - 220):lo]
        if (re.search(r'(?:archived|historical|previous|prior)[^.!?]*[.:\n]\s*$', prior, re.I)
                and heading not in {'current', 'new', 'amended'}):
            reasons.append('Historical preceding context.')
        if re.search(r'\b(?:not|no|never|archived|historical|previous|prior|expired|closed)\b', clause, re.I):
            reasons.append('Negative or historical response clause.')
        dates = []
        for date_match in DATE.finditer(clause):
            value = date_match.group()
            normalized = value
            if not value[0].isdigit():
                normalized = None
                for fmt in ('%B %d, %Y', '%B %d %Y'):
                    try:
                        normalized = datetime.strptime(value, fmt).date().isoformat()
                        break
                    except ValueError:
                        pass
            if normalized is None:
                # Month/day text is partial evidence. Compare only the stated
                # components with independently recorded metadata, never invent
                # a source year from runtime.
                try:
                    partial = datetime.strptime(value + ' 2000', '%B %d %Y')  # leap-safe calendar validation only
                except ValueError:
                    partial = None
                if partial is not None:
                    agrees = bool(metadata['date_value'] and metadata['date_value'][5:] == partial.strftime('%m-%d'))
                    dates.append(dict(raw=value, precision='month_day', parse_status='partial_date',
                                      original_field='response_clause', metadata_month_day_agrees=agrees))
                    if not agrees:
                        reasons.append('Partial response day does not resolve against metadata.')
                    continue
            parsed = source_time(normalized if normalized is not None else value,
                                 field='response_clause')
            parsed['raw'] = value
            dates.append(parsed)
            if parsed['parse_status'] == 'aware':
                when = instant(normalized)
                if when <= at:
                    reasons.append('Stated response timestamp is closed.')
                if metadata['instant_utc'] and when != instant(deadline):
                    reasons.append('Response prose and metadata timestamps conflict.')
            elif parsed['parse_status'] == 'date_only':
                if parsed['date_value'] < at.date().isoformat():
                    reasons.append('Stated response day is past.')
                if metadata['date_value'] and parsed['date_value'] != metadata['date_value']:
                    reasons.append('Response prose and metadata dates conflict.')
            else:
                reasons.append('Response date has unresolved precision or timezone.')
        if any(int(y) < at.year for y in re.findall(r'\b(?:19\d{2}|20\d{2})\b', clause)):
            reasons.append('Historical year in response instruction.')
        out['clauses'].append(dict(text=clause, start=lo, end=hi, heading=heading,
                                   dates=dates, supported=not reasons, gaps=reasons))
        out['supported'] |= not reasons
    if not out['clauses']:
        out['gaps'].append('No unambiguous cited response instruction.')
    return out
