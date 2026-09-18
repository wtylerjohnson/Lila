"""Shared source-linked target and research rendering for both release views."""
from html import escape
import hashlib
import re
from urllib.parse import urlsplit


def esc(value):
    return escape(str(value or ""), quote=True)


def source_link(url, label):
    parsed = urlsplit(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return esc(label)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def source_text(value, source):
    """Keep contact-bearing source prose traceable without claiming freshness."""
    text = str(value or '')
    if re.search(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|tel:\+?[\d().\s-]{7,}', text):
        return contact_channel(text, source)
    return esc(text)


def editable_text(value, key=None, source=None):
    """Prose-only hooks for the existing Studio editor; never edit source facts."""
    if key is None:
        return source_text(value, source)
    token = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f'<span data-edit-id="action-{token}">{source_text(value, source)}</span>'


def contact_channel(value, source):
    """A retained channel has no independently established observation date."""
    return ('<span data-contact="1" data-grade="C" data-source="'
            + esc(source or 'source-not-supplied') + '">' + esc(value) + '</span>')


def render_targets(targets):
    rows = []
    for target in targets:
        row = target.model_dump(mode="json") if hasattr(target, "model_dump") else target
        fields = [("Role", row.get("role") or row.get("title")),
                  ("Organization", row.get("organization")),
                  ("Email", row.get("email")), ("Phone", row.get("phone")),
                  ("Contact status", row.get("contact_status")),
                  ("Route", row.get("route")), ("Why contact", row.get("reason_to_contact")),
                  ("Next ask", row.get("next_ask")), ("Role boundary", row.get("authority_boundary"))]
        verification = row.get('channel_verification') or {}
        if verification:
            fields.extend([('Phone type', verification.get('phone_type')),
                           ('DNC status', verification.get('dnc_status')),
                           ('Contact verified at', verification.get('verified_at')),
                           ('Contact verified on', verification.get('verified_on'))])
        sources = [source_link(e.get("source_url"), e.get("source_name") or e.get("evidence_id"))
                   for e in row.get("evidence", [])]
        sources.insert(0, source_link(row.get("source_url"), row.get("source_kind") or "Published source"))
        rows.append('<article class="product-target"><strong>' + esc(row.get("name"))
                    + '</strong><dl class="product-fields">'
                    + ''.join(f'<dt>{esc(label)}</dt><dd>{contact_channel(value, row.get("source_url")) if label in {"Email", "Phone"} else source_text(value, row.get("source_url"))}</dd>'
                              for label, value in fields if value)
                    + ('</dl><p>Source-reported contact verification retained. Program responsibility and outreach permission remain separate.</p><p>'
                       if verification else '</dl><p>Grade C: contact observation date not established. Confirm current role and contact details.</p><p>')
                    + ' · '.join(dict.fromkeys(sources)) + '</p></article>')
    return '<div class="product-targets">' + ''.join(rows) + '</div>' if rows else ''


def render_research(research, *, edit_key=None, source_subject=None):
    row = research.model_dump(mode="json") if hasattr(research, "model_dump") else research
    if not row:
        return ''
    evidence_source = next((e.get('source_url') for e in row.get('evidence', []) if e.get('source_url')), None)
    def prose(key):
        return editable_text(row.get(key), f'{edit_key}:{key}' if edit_key else None, source=evidence_source)
    statuses = {'technical_qualification': 'Check technical fit', 'known_opportunity': 'Known opportunity',
                'investigation': 'Investigate next', 'deprioritized': 'Keep on the watchlist',
                'rejected': 'Removed from priority'}
    def evidence_link(e):
        if source_subject is not None and str(source_subject.source_url) == str(e['source_url']):
            from .research_html import source_link as bound_link
            return bound_link(source_subject, e.get('source_name') or e['evidence_id'])
        return source_link(e['source_url'], e.get('source_name') or e['evidence_id'])
    sources = ' · '.join(evidence_link(e)
                         for e in row.get('evidence', []))
    return ('<div class="research-action"><strong>' + esc(statuses.get(row['status'], row['status']))
            + '</strong><p><b>Why this is here:</b> ' + prose('rationale')
            + '</p><div class="action-grid"><section><h4>Buyer requirement</h4><p>'
            + source_text(row.get('buyer_requirement'), evidence_source) + '</p><p>' + sources
            + '</p><h4>Why now / research view</h4><p>' + prose('why_now')
            + '</p></section><section><h4>Where the company could fit / proposed</h4><p>'
            + prose('fit_hypothesis') + '</p><h4>Route to investigate / proposed</h4><p>'
            + prose('route') + '</p><p>Confirm product scope and buying authority with the source owner.</p>'
            + '</section></div><div class="first-question"><h4>First question</h4><p>'
            + prose('next_ask') + '</p></div><h4>What must be confirmed</h4><ul>'
            + ''.join('<li>' + source_text(q, evidence_source) + '</li>' for q in row.get('open_questions', []))
            + '</ul><p>These are conditions for reassessment, not permission to contact or bid.</p>'
            + '<details><summary>Research evidence and review</summary><p>' + sources
            + '</p><p>Reviewed by ' + esc(row.get('reviewed_by')) + ' · ' + esc(row.get('reviewed_at'))
            + '</p>' + ''.join('<blockquote data-thirdparty="1">' + source_text(e.get('excerpt'), e.get('source_url')) + '</blockquote>'
                              for e in row.get('evidence', [])) + '</details></div>')
