"""Source-bound research fields; original JSON and acquisition stay unchanged."""
from __future__ import annotations

import base64
from datetime import date
from html import escape
import json
import re

from agents.reports.links import build_usaspending_award_link, federal_link_domain
from tools.contact_graph.grading import grade_for

_URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CHANNEL_FIELD = re.compile(r"(?:^|_)(?:email|phone|fax|telephone)(?:_|$)", re.I)


def source_link(subject, label=None):
    """Resolve an already validated research identity, never a buying verdict."""
    from agents.assess.contracts import ResearchSubject
    subject = ResearchSubject.model_validate(subject.model_dump(mode="json"))
    label = escape(str(label or subject.source_record_id))
    if subject.source_system == "usaspending.gov":
        link = build_usaspending_award_link(subject.source_record_id, reconciled=True)
        return (f'<a href="{escape(link.url, quote=True)}" '
                f'data-link-builder="{link.builder}" '
                f'data-link-record="{escape(link.record_id, quote=True)}" '
                f'data-link-reconciled="1" rel="noreferrer">{label}</a>')
    return (f'<a href="{escape(str(subject.source_url), quote=True)}" '
            f'rel="noreferrer">{label}</a>')


def _field_text(value, subject):
    """Display source text without turning a foreign URL into this record."""
    if not isinstance(value, str):
        return escape(json.dumps(value, ensure_ascii=False))
    parts = []
    previous = 0
    for match in _URL.finditer(value):
        parts.append(escape(value[previous:match.start()]))
        url = match.group(0)
        if federal_link_domain(url):
            allowed = {str(subject.source_url)}
            if subject.source_system == "usaspending.gov":
                allowed.add(build_usaspending_award_link(subject.source_record_id).url)
            if url not in allowed:
                parts.append("Federal URL needs source reconciliation; see original JSON")
            else:
                parts.append(source_link(subject, "Public source record"))
        else:
            parts.append(escape(url))
        previous = match.end()
    parts.append(escape(value[previous:]))
    return "".join(parts)


def _fields(value, pointer=""):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            token = str(key).replace("~", "~0").replace("/", "~1")
            yield from _fields(item, pointer + "/" + token)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _fields(item, pointer + "/" + str(index))
    else:
        yield pointer or "/", value


def render_source_fields(subject, *, as_of: date) -> str:
    """Keep every field inspectable and both serialized inputs downloadable.

    Grade C follows the existing unknown-observation rule, not source freshness.
    These source/context fields never create contact observations or LeadTargets.
    """
    from agents.assess.contracts import ResearchSubject
    subject = ResearchSubject.model_validate(subject.model_dump(mode="json"))
    from agents.reports.value_evidence import value_evidence, render_values
    blocks = [render_values(value_evidence(json.loads(subject.source_payload_json),
                  source_url=str(subject.source_url), source_id=subject.source_record_id),
                  source_link=source_link(subject))]
    for kind, raw in (("source", subject.source_payload_json),
                      ("discovery-context", subject.discovery_context_json)):
        if raw is None:
            continue
        payload = json.loads(raw)
        download = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        rows = []
        for pointer, value in _fields(payload):
            rendered = _field_text(value, subject)
            field = pointer.rsplit("/", 1)[-1]
            if isinstance(value, str) and value and (_EMAIL.search(value) or _CHANNEL_FIELD.search(field)):
                grade = grade_for(None, now=as_of)
                source = escape(f"{subject.subject_id}:{kind}{pointer}", quote=True)
                rendered = (f'<span data-contact="1" data-grade="{grade}" data-source="{source}">'
                            + rendered + '</span><small>Grade C: source observation date unknown. '
                            'Saved field only; current contact role remains unconfirmed.</small>')
            rows.append(f'<tr><th scope="row" class="mono">{escape(pointer)}</th><td>{rendered}</td></tr>')
        blocks.append(
            f'<p><b>{"Original source fields" if kind == "source" else "Saved discovery context"}</b> · '
            f'<a download="{escape(subject.source_sha256)}.{kind}.json" '
            f'data-source-json="{kind}" href="data:application/json;base64,{download}">Download untouched JSON</a></p>'
            '<p>URL fields link to the source record; the download retains the exact original URL and values. '
            'Saved contact text does not establish present responsibility or freshness.</p>'
            '<table class="data-table source-fields"><thead><tr><th>Original field</th><th>Saved value</th></tr></thead>'
            '<tbody>' + ''.join(rows) + '</tbody></table>')
    return ('<details><summary>Show work: original source fields and value basis</summary>'
            f'<p class="mono">Source SHA-256: {escape(subject.source_sha256)}</p>'
            + ''.join(blocks) + '</details>')


def reviewed_context(subject):
    from agents.assess.reviewed_cases import ReviewedSubject
    if not subject.reviewed_overlay_json:
        return None
    return ReviewedSubject.model_validate_json(subject.reviewed_overlay_json)


def _statement_text(statement):
    anchor = (f"Meeting date: {statement.event_date.isoformat()}" if statement.event_date
              else "Meeting date not established; confirm the actual date")
    relative = (f'; original timing: "{statement.relative_date_text}"' if statement.relative_date_text else '')
    return (f'{statement.speaker} ({statement.certainty}, {statement.transcript_timestamp}): '
            f'"{statement.quote}". {anchor}{relative}. '
            'Attributed customer context; reconcile with the original procurement record. '
            'This does not establish budget, tool ownership, authority, or a current buying decision.')


def render_reviewed_context(subject):
    """Keep a known award and distinct expansion questions visible together."""
    from .target_html import render_research, render_targets
    overlay = reviewed_context(subject)
    if overlay is None:
        return ''
    def statements(items):
        return ''.join('<blockquote>' + escape(_statement_text(s))
                       + '<details><summary>Show work: meeting evidence</summary><p>'
                       + escape(s.source_excerpt) + '</p><p>Source document SHA-256: '
                       + escape(s.source_document_sha256) + '</p></details></blockquote>' for s in items)
    blocks = [statements(overlay.customer_statements)]
    for item in overlay.investigations:
        blocks.append('<section data-investigation="'
                      + escape(item.investigation_id, quote=True) + '"><h4>'
                      + escape(item.scope) + '</h4><p>Separate investigation: '
                      + escape(item.purpose.replace('_', ' '))
                      + '. Existing business does not establish demand or purchasing authority for this scope.</p>'
                      + render_research(item.research) + render_targets(item.targets)
                      + statements(item.customer_statements) + '</section>')
    return ''.join(blocks)


def reviewed_context_markdown(subject):
    overlay = reviewed_context(subject)
    if overlay is None:
        return []
    lines = [_statement_text(s) for s in overlay.customer_statements]
    for item in overlay.investigations:
        lines.extend([f'### Separate investigation: {item.scope}', item.research.status,
                      item.research.rationale, item.research.next_ask, item.research.route,
                      *item.research.open_questions,
                      *[_statement_text(s) for s in item.customer_statements],
                      *[f'[{t.name} / {t.role}]({t.source_url}): {t.next_ask}. {t.authority_boundary}' for t in item.targets],
                      'Existing business does not establish demand or purchasing authority for this scope.'])
    return lines
