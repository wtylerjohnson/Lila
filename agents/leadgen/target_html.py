"""Shared source-linked target and research rendering for both release views."""
from html import escape
from urllib.parse import urlsplit


def esc(value):
    return escape(str(value or ""), quote=True)


def source_link(url, label):
    parsed = urlsplit(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return esc(label)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def render_targets(targets):
    rows = []
    for target in targets:
        row = target.model_dump(mode="json") if hasattr(target, "model_dump") else target
        contacts = " · ".join(esc(row.get(k)) for k in ("email", "phone") if row.get(k))
        fields = [("Role", row.get("role") or row.get("title")),
                  ("Organization", row.get("organization")),
                  ("Contact", contacts), ("Contact status", row.get("contact_status")),
                  ("Route", row.get("route")), ("Why contact", row.get("reason_to_contact")),
                  ("Next ask", row.get("next_ask")), ("Role boundary", row.get("authority_boundary"))]
        sources = [source_link(e.get("source_url"), e.get("source_name") or e.get("evidence_id"))
                   for e in row.get("evidence", [])]
        sources.insert(0, source_link(row.get("source_url"), row.get("source_kind") or "Published source"))
        rows.append('<article class="product-target"><strong>' + esc(row.get("name"))
                    + '</strong><dl class="product-fields">'
                    + ''.join(f'<dt>{esc(label)}</dt><dd>{value if label == "Contact" else esc(value)}</dd>'
                              for label, value in fields if value)
                    + '</dl><p>' + ' · '.join(dict.fromkeys(sources)) + '</p></article>')
    return '<div class="product-targets">' + ''.join(rows) + '</div>' if rows else ''


def render_research(research):
    row = research.model_dump(mode="json") if hasattr(research, "model_dump") else research
    if not row:
        return ''
    fields = (("Buyer requirement", "buyer_requirement"), ("Fit to investigate", "fit_hypothesis"),
              ("Why now", "why_now"), ("Route", "route"), ("Next ask", "next_ask"))
    return ('<div class="research-action"><strong>' + esc(row["status"].replace('_', ' '))
            + '</strong><dl class="product-fields">'
            + ''.join(f'<dt>{esc(label)}</dt><dd>{esc(row.get(key))}</dd>' for label, key in fields)
            + '</dl><p><b>Resolve next:</b> ' + '; '.join(esc(q) for q in row.get("open_questions", []))
            + '</p><p>' + ' · '.join(source_link(e["source_url"], e.get("source_name") or e["evidence_id"])
                                     for e in row.get("evidence", [])) + '</p></div>')
