"""Deterministic renderer and validator for LILA's eight-slot product."""

from __future__ import annotations

import html
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

from agents.golden_press.external_product_contract import load_external_product_slots
from agents.golden_press.external_product_projection import ExternalProductDocument


EXTERNAL_PRODUCT_RENDER_VERSION = "lila-eight-slot-render.v2.2026-08-23"


_PRODUCT_CSS = r"""
.product-css-sentinel{display:contents}
.product-slot{scroll-margin-top:32px}
.product-slot-intro{min-width:0}
.product-slot-summary{max-width:900px;margin:0 0 22px;color:var(--muted);font-size:15px;line-height:1.65}
.product-slot-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:0 0 22px}
.product-metric{border:1px solid var(--line);background:var(--paper-deep);padding:15px;min-width:0}
.product-metric span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.09em}
.product-metric strong{display:block;margin-top:7px;font-family:var(--mono);font-size:19px;overflow-wrap:anywhere}
.product-metric small{display:block;margin-top:6px;color:var(--muted);line-height:1.45}
.product-records{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.product-slot[data-slot-id="priority-pursuits"] .product-records,.product-slot[data-slot-id="federal-opportunities"] .product-records{grid-template-columns:minmax(0,1fr)}
.product-record{border:1px solid var(--line);background:var(--paper);padding:17px;min-width:0;break-inside:avoid}
.product-record-kind{display:block;color:var(--accent);font-size:10px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:7px}
.product-record h3{font-size:17px;line-height:1.3;margin:0 0 8px;overflow-wrap:anywhere}
.product-record p{color:var(--muted);font-size:13px;line-height:1.55;margin:7px 0}
.product-fields{display:grid;grid-template-columns:minmax(92px,.42fr) minmax(0,1fr);gap:7px 12px;margin:12px 0 0}
.product-fields dt{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em}
.product-fields dd{margin:0;font-size:12px;overflow-wrap:anywhere}
.product-source{display:inline-block;margin-top:12px;color:var(--link);font-family:var(--mono);font-size:11px;overflow-wrap:anywhere}
.product-priority{display:grid;grid-template-columns:42px minmax(0,1fr);gap:13px}
.research-action{margin:16px 0;padding:14px;border-left:3px solid var(--accent);background:var(--paper);overflow-wrap:anywhere}
.product-priority-rank{display:flex;align-items:center;justify-content:center;width:34px;height:34px;border:1px solid var(--accent);color:var(--accent);font-family:var(--mono);font-weight:700}
.product-targets{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.product-targets h4{margin:0 0 9px;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.product-target{border-left:2px solid var(--accent);padding:7px 9px;margin:7px 0;background:var(--paper-deep);font-size:11px;line-height:1.45}
.product-target strong{display:block;font-size:12px}
.product-gap{margin:15px 0 0;border:1px dashed var(--accent);padding:14px;color:var(--muted);font-size:13px;line-height:1.55}
.product-visuals{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:18px}
.product-visual{border:1px solid var(--line);background:var(--paper-deep);padding:14px;min-width:0}
.product-visual h3{font-size:13px;margin:0 0 10px}
.product-visual p{color:var(--muted);font-size:11px;line-height:1.45;margin:0 0 12px}
.product-svg{display:block;width:100%;height:auto;overflow:visible}
.product-svg-axis{stroke:var(--line-strong);stroke-width:1}
.product-svg-path{fill:none;stroke:var(--accent);stroke-width:2}
.product-svg-point{fill:var(--accent);stroke:var(--paper);stroke-width:2}
.product-svg-bar{fill:var(--accent)}
.product-svg-label{fill:var(--ink);font:9px var(--mono)}
.product-svg-edge{stroke:var(--line-strong);stroke-width:1.2;marker-end:url(#productArrow)}
.product-svg-node{fill:var(--paper);stroke:var(--accent);stroke-width:1.2}
.direction-graph{min-height:180px}
.vector-map{min-height:180px}
.product-reference{color:var(--muted);font-size:11px;font-family:var(--mono);margin-top:10px}
.product-status{white-space:nowrap}
@media(max-width:720px){.product-records,.product-visuals{grid-template-columns:1fr}.product-slot-metrics{grid-template-columns:1fr 1fr}.product-fields{grid-template-columns:1fr}.product-fields dd{margin-bottom:5px}}
@page{size:letter;margin:.42in}
@media print{
html,body,.memo{background:#fff!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo{box-shadow:none}
.product-slot{padding-top:28px;padding-bottom:10px}
.product-slot-gap{padding-top:18px;padding-bottom:6px;break-inside:avoid;page-break-inside:avoid}
.product-slot-intro{break-inside:avoid;page-break-inside:avoid;break-after:avoid;page-break-after:avoid}
.product-slot .plain-head{margin-bottom:14px;padding-bottom:11px;break-after:avoid;page-break-after:avoid}
.product-slot-summary{display:none}
.product-slot-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-bottom:14px;break-inside:avoid;page-break-inside:avoid}
.product-metric{padding:11px}
.product-records{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;align-items:start}
.product-slot[data-slot-id="priority-pursuits"] .product-records,.product-slot[data-slot-id="federal-opportunities"] .product-records{grid-template-columns:1fr}
.product-record{margin:0;break-inside:avoid;page-break-inside:avoid;padding:13px;overflow:hidden}
.product-fields{gap:5px 9px;margin-top:9px}
.product-source{color:#000;text-decoration:underline;overflow-wrap:anywhere;word-break:break-word}
.product-gap{margin-top:8px;padding:11px;break-inside:avoid;page-break-inside:avoid}
.product-visuals{display:block;margin-top:12px}
.product-visual{margin:0 0 10px;padding:12px;break-inside:avoid;page-break-inside:avoid;overflow:hidden}
.product-svg{max-height:230px}
.report-foot{break-inside:avoid;page-break-inside:avoid;margin-top:24px}
}
"""


def esc(value: Any) -> str:
    return html.escape(" ".join(str(value or "").split()), quote=True)


def _http(value: Any) -> str:
    from agents.golden_press.market_map_render import _resolving_source_url
    url = _resolving_source_url(value)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "Not stated"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _source_link(record: dict) -> str:
    url = _http(record.get("source_url"))
    if not url:
        return ""
    label = record.get("source_id") or "Open source record"
    if _http(label):
        label = "Open source record"
    return (f'<a class="product-source" href="{esc(url)}" target="_blank" '
            f'rel="noopener noreferrer">{esc(label)} ↗</a>')


def _fields(record: dict) -> str:
    labels = (
        ("agency", "Agency"), ("office", "Office"),
        ("recipient", "Recipient"), ("response_date", "Timing"),
        ("selected_response_date", "Selected notice response (historical)"),
        ("value", "Published value"), ("commercial_route", "Route"),
        ("window_state", "Window"), ("service_fit", "Fit"),
        ("role", "Relationship"), ("target_role", "Target role"),
        ("person", "Named person"), ("date", "Date"),
        ("location", "Location"), ("status", "Status"),
        ("method", "Method"), ("result_count", "Returned"),
        ("kept_after_screen", "Kept"),
        ("assessment_status", "Assessment"), ("lead_status", "Lead readiness"),
        ("source_as_of", "Source acquisition" if record.get("source_clock_status") else "Evidence date"),
        ("source_clock_coverage", "Acquisition coverage"),
    )
    rows = []
    for key, label in labels:
        if key == "value" and record.get("value_evidence"):
            continue
        value = record.get(key)
        if value in (None, "", [], {}):
            continue
        rows.append(f"<dt>{esc(label)}</dt><dd>{esc(_display_value(value))}</dd>")
    from agents.reports.value_evidence import render_values
    return (('<dl class="product-fields">' + "".join(rows) + "</dl>"
             if rows else "") + render_values(record.get("value_evidence", []), source_link=_source_link(record)))


def _targets(record: dict) -> str:
    from agents.leadgen.target_html import render_targets, render_research
    if record.get("research") or any(t.get("next_ask") for t in record.get("targets") or []):
        return render_research(record.get("research")) + render_targets(record.get("targets") or [])
    rows = []
    for target in record.get("targets") or []:
        name = target.get("name") or target.get("role_needed") or target.get("role")
        organization = target.get("organization") or ""
        source_kind = target.get("source_kind") or "target"
        contact = target.get("email") or target.get("phone") or ""
        rows.append(
            '<div class="product-target">'
            f'<span>{esc(source_kind)}</span><strong>{esc(name)}</strong>'
            f'<span>{esc(organization)}</span>'
            + (f'<span> · {esc(contact)}</span>' if contact else "")
            + "</div>")
    if not rows:
        return '<div class="product-gap">Target research next: resolve the named roles for this opportunity without substituting a generic inbox or switchboard.</div>'
    return ('<div class="product-targets"><h4>Targets specific to this opportunity</h4>'
            + "".join(rows) + "</div>")


def _priority(record: dict) -> str:
    fields = []
    for label, key in (("Route", "route"), ("Timing", "timing"),
                       ("Next action", "next_action"),
                       ("Targets", "target_count")):
        value = record.get(key)
        if value not in (None, ""):
            fields.append(f"<dt>{esc(label)}</dt><dd>{esc(_display_value(value))}</dd>")
    return (
        '<article class="product-record product-priority">'
        f'<div class="product-priority-rank">{int(record.get("priority") or 0):02d}</div>'
        f'<div><span class="product-record-kind">{esc(record.get("priority_kind") or "Ranked pursuit")}</span>'
        f'<h3>{esc(record.get("title"))}</h3>'
        f'<p>{esc(record.get("why"))}</p>'
        + ('<dl class="product-fields">' + "".join(fields) + "</dl>" if fields else "")
        + f'<div class="product-reference"><a href="#{esc(record.get("reference_key"))}">Open assessment, contacts and evidence</a></div>'
        + (_source_link(record) if record.get("source_url") else "")
        + "</div></article>")


def _forecast_pocs(record: dict) -> str:
    from agents.leadgen.research_html import render_forecast_contact_record
    raw = record.get("forecast_contact_record")
    return render_forecast_contact_record(raw) if raw else ""


def _record(record: dict, *, include_targets: bool = False) -> str:
    if "reference_key" in record:
        return _priority(record)
    summary = record.get("summary") or record.get("next_action") or ""
    return (
        '<article class="product-record" '
        f'id="{esc(record.get("record_key"))}" data-record-key="{esc(record.get("record_key"))}">'
        f'<span class="product-record-kind">{esc(record.get("kind"))}</span>'
        f'<h3>{esc(record.get("title"))}</h3>'
        + (f'<p>{esc(summary)}</p>' if summary else "")
        + _fields(record)
        + _source_link(record)
        + _forecast_pocs(record)
        + _family_history(record)
        + _lead_rows(record)
        + _saved_lead_history(record)
        + (_targets(record) if include_targets else "")
        + "</article>")


def _family_history(record: dict) -> str:
    representative = record.get("current_representative")
    members = record.get("family_members") or []
    if not representative or not members:
        return ""
    current = dict(representative)
    current["source_id"] = representative.get("source_id")
    title = "Current family representative" if record.get("family_order_status") == "ordered" else "Family chronology unresolved"
    rows = []
    for member in members:
        status = str(member.get("lineage_status") or "unknown").replace("_", " ")
        if member.get("superseded_by"):
            status += f" by {member['superseded_by']}"
        rows.append("<li>" + esc(member.get("source_id")) + " · " + esc(member.get("title"))
                    + " · " + esc(member.get("source_status")) + " · " + esc(status)
                    + _source_link(member) + "</li>")
    return ('<div class="product-family"><h4>' + esc(title) + '</h4><p>'
            + esc(representative.get("source_id")) + ' · ' + esc(representative.get("title"))
            + ' · ' + esc(representative.get("source_status")) + '</p>' + _source_link(current)
            + '<details><summary>Notice family history (' + str(len(members)) + ')</summary><ul>'
            + ''.join(rows) + '</ul><p>' + esc(record.get("family_history_scope")) + '</p></details></div>')


def _saved_lead_history(record: dict) -> str:
    saved = record.get("saved_lead_rows") or []
    if not saved:
        return ""
    rows = []
    for lead in saved:
        fields = {"Saved readiness": lead.get("lead_tier"),
                  "Saved next action": (lead.get("next_action") or {}).get("verb"),
                  "Saved research": (lead.get("research") or {}).get("status")}
        rows.append('<dl class="product-fields">' + ''.join(
            f'<dt>{esc(label)}</dt><dd>{esc(_display_value(value))}</dd>'
            for label, value in fields.items() if value) + '</dl>')
    return ('<details class="product-saved-history"><summary>Saved lead history — current authority withheld</summary><p>'
            + esc('; '.join(record.get("lead_projection_gaps") or [])) + '</p>'
            + ''.join(rows) + '</details>')


def _lead_rows(record: dict) -> str:
    rows = []
    for lead in record.get("lead_rows") or []:
        action = lead.get("next_action") or {}
        route = lead.get("seller_path") or {}
        pathway = lead.get("external_pathway") or {}
        fields = {
            "Lead readiness": lead.get("lead_tier"),
            "Route": route.get("kind"),
            "Pathway": pathway.get("kind"),
            "Next action": action.get("verb"),
            "Due": action.get("due_at") or action.get("due_date"),
            "Evidence to complete": action.get("blocked_by"),
        }
        detail = "".join(f"<dt>{esc(k)}</dt><dd>{esc(_display_value(v))}</dd>"
                         for k, v in fields.items() if v)
        rows.append('<div class="product-target"><dl class="product-fields">'
                    + detail + '</dl></div>')
    return ('<div class="product-targets"><h4>Lead generation</h4>'
            + "".join(rows) + '</div>') if rows else ""


def _metrics(metrics: Any) -> str:
    cards = []
    for metric in metrics or ():
        cards.append(
            '<article class="product-metric">'
            f'<span>{esc(metric.get("label"))}</span>'
            f'<strong>{esc(_display_value(metric.get("value")))}</strong>'
            f'<small>{esc(metric.get("note"))}</small></article>')
    return ('<div class="product-slot-metrics">' + "".join(cards) + "</div>"
            if cards else "")


def _directional_graph(visual: dict) -> str:
    points = list(visual.get("points") or [])[:12]
    if not points:
        return ""
    width, height = 620, 230
    left, top, right, bottom = 40, 30, 25, 45
    usable_w, usable_h = width - left - right, height - top - bottom
    values = []
    for point in points:
        value = point.get("magnitude")
        values.append(float(value) if isinstance(value, (int, float)) else 0.0)
    high = max(values) or 1.0
    bars = []
    bar_space = usable_w / max(1, len(points))
    bar_width = max(8.0, min(34.0, bar_space * .58))
    for index, (point, value) in enumerate(zip(points, values)):
        x = left + bar_space * index + bar_space / 2
        bar_height = max(2.0, (value / high) * usable_h)
        y = top + usable_h - bar_height
        label = f"F{index + 1}"
        bars.append(
            f'<rect class="product-svg-bar" x="{x - bar_width / 2:.1f}" '
            f'y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" />'
            f'<text class="product-svg-label" x="{x:.1f}" y="{height - 18}" '
            f'text-anchor="middle">{label}</text>')
    return (
        f'<svg class="product-svg direction-graph" viewBox="0 0 {width} {height}" role="img" aria-label="Published forecast value floor chart">'
        f'<line class="product-svg-axis" x1="{left}" y1="{top + usable_h}" x2="{width-right}" y2="{top + usable_h}" />'
        + "".join(bars) + "</svg>")


def _vector_map(visual: dict) -> str:
    edges = list(visual.get("edges") or [])[:24]
    if not edges:
        return ""
    root = str(edges[0].get("from") or "Client")
    agencies = []
    for edge in edges:
        if edge.get("from") == root and edge.get("to") not in agencies:
            agencies.append(edge.get("to"))
    agencies = agencies[:8]
    width, height = 620, max(220, 70 + len(agencies) * 34)
    root_x, root_y = 90, height / 2
    items = [
        '<defs><marker id="productArrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="currentColor" /></marker></defs>',
        f'<circle class="product-svg-node" cx="{root_x}" cy="{root_y:.1f}" r="28" />',
        f'<text class="product-svg-label" x="{root_x}" y="{root_y + 3:.1f}" text-anchor="middle">{esc(root[:14])}</text>',
    ]
    for index, agency in enumerate(agencies):
        y = 40 + index * ((height - 80) / max(1, len(agencies) - 1))
        x = 430
        items.append(
            f'<line class="product-svg-edge" x1="{root_x+30}" y1="{root_y:.1f}" x2="{x-52}" y2="{y:.1f}" />'
            f'<rect class="product-svg-node" x="{x-50}" y="{y-15:.1f}" width="150" height="30" rx="4" />'
            f'<text class="product-svg-label" x="{x+25}" y="{y+3:.1f}" text-anchor="middle">{esc(str(agency)[:24])}</text>')
    return (f'<svg class="product-svg vector-map" viewBox="0 0 {width} {height}" role="img" aria-label="Forecast relationship vector map">'
            + "".join(items) + "</svg>")


def _visuals(visuals: Any) -> str:
    cards = []
    for visual in visuals or ():
        body = (_directional_graph(visual)
                if visual.get("type") == "directional_graph"
                else _vector_map(visual)
                if visual.get("type") == "relationship_vector_map" else "")
        if body:
            cards.append('<article class="product-visual">'
                         f'<h3>{esc(visual.get("title"))}</h3>'
                         + (f'<p>{esc(visual.get("note"))}</p>'
                            if visual.get("note") else "")
                         + body + '</article>')
    return ('<div class="product-visuals">' + "".join(cards) + "</div>"
            if cards else "")


def render_slot(slot: Any) -> str:
    include_targets = slot.slot_id == "federal-opportunities"
    records = "".join(_record(row, include_targets=include_targets)
                      for row in slot.records)
    gaps = "".join(f'<div class="product-gap">{esc(gap)}</div>'
                   for gap in slot.gaps)
    status_class = " product-slot-gap" if slot.status == "gap" else ""
    return (
        f'<section class="plain-section product-slot{status_class}" id="{esc(slot.slot_id)}" '
        f'data-slot-id="{esc(slot.slot_id)}" data-slot-number="{slot.number}">'
        '<div class="product-slot-intro">'
        '<div class="plain-head">'
        f'<span class="plain-number">{slot.number}</span><div>'
        f'<h2>{esc(slot.heading)}</h2><p>{esc(slot.summary)}</p></div>'
        f'<span class="plain-state product-status">{esc(slot.status)}</span></div>'
        + _metrics(slot.metrics)
        + '</div>'
        + ('<div class="product-records">' + records + "</div>" if records else "")
        + _visuals(slot.visuals) + gaps + "</section>")


def _work_details(doc: ExternalProductDocument) -> dict:
    details = {}
    for slot in doc.slots:
        sources = []
        for record in slot.records:
            url = _http(record.get("source_url"))
            if url:
                label = record.get("source_id") or record.get("title")
                if _http(label):
                    label = "Open source record"
                sources.append([label, url])
        details[f"slot-{slot.number}"] = {
            "title": slot.heading,
            "summary": slot.summary,
            "formula": (f"{len(slot.records)} owned record(s); "
                        f"{len(slot.gaps)} named gap(s)."),
            "sources": sources[:60],
        }
    return details


def _coverage(doc: ExternalProductDocument) -> list[dict]:
    by_id = {slot.slot_id: slot for slot in doc.slots}
    opportunities = by_id["federal-opportunities"]
    targets = sum(len(row.get("targets") or []) for row in opportunities.records)
    return [
        {"label": "Product slots", "value": "8 / 8",
         "note": "Operator-locked order", "work": "slot-1"},
        {"label": "Priority pursuits",
         "value": len(by_id["priority-pursuits"].records),
         "note": "Ranked references", "work": "slot-1"},
        {"label": "Opportunity assessments", "value": len(opportunities.records),
         "note": f"{opportunities.coverage.get('qualified', 0)} qualified; remaining candidates retained for review", "work": "slot-5"},
        {"label": "Opportunity targets", "value": targets,
         "note": "Bound beneath their opportunity", "work": "slot-5"},
    ]


def _ticker(doc: ExternalProductDocument) -> list[dict]:
    slot = next(item for item in doc.slots if item.slot_id == "priority-pursuits")
    return [{"label": f"{row.get('priority'):02d} · {row.get('title')}", "url": ""}
            for row in slot.records]


def render_external_product(doc: ExternalProductDocument) -> tuple[str, str]:
    """Return editable studio HTML and the scriptless client artifact."""
    from agents.golden_press.market_map_press import _client_export
    from agents.golden_press.market_map_skeleton import EditIds, build_document

    sections = [(slot.slot_id, slot.heading) for slot in doc.slots]
    ids = EditIds(prefix="lila")
    content = "".join(render_slot(slot) for slot in doc.slots)
    details = _work_details(doc)
    studio = build_document(
        client_name=doc.client_name, slug=doc.slug, stamp=doc.as_of,
        title="LILA Federal Market Map",
        standfirst="Federal opportunities and the evidence behind your next action.",
        edition=f"{doc.client_name} · Complete LILA edition",
        edition_note=(f"Research pressed {doc.as_of}. Every record remains "
                      "bound to its owning slot and source evidence."),
        coverage=_coverage(doc), sections=sections, content=content,
        ticker_items=_ticker(doc), work_details=details,
        footer_note=("LILA Federal Market Map. Eight fixed slots, one owning "
                     "slot per record, and named gaps instead of silent blanks."),
        ids=ids,
    )
    studio = studio.replace("</style>", _PRODUCT_CSS + "\n</style>", 1)
    client = _client_export(studio, receipts=details)
    return studio, client


def validate_external_product_document(doc: ExternalProductDocument) -> list[dict]:
    violations: list[dict] = []
    contract = load_external_product_slots()
    expected = [(slot.number, slot.slot_id, slot.heading) for slot in contract]
    actual = [(slot.number, slot.slot_id, slot.heading) for slot in doc.slots]
    if actual != expected:
        violations.append({"rule": "slot_contract", "detail":
                           f"slot sequence {actual!r} does not match the operator lock"})
    owned: dict[str, str] = {}
    for slot in doc.slots:
        if slot.status not in {"populated", "gap"}:
            violations.append({"rule": "slot_status", "detail":
                               f"{slot.slot_id} has invalid status {slot.status!r}"})
        if slot.status == "gap" and not slot.gaps:
            violations.append({"rule": "silent_gap", "detail":
                               f"{slot.slot_id} is empty without a named next action"})
        for row in slot.records:
            key = row.get("record_key")
            if not key:
                continue  # Slot 1 references, it does not own evidence rows.
            if key in owned:
                violations.append({"rule": "duplicate_record_owner", "detail":
                                   f"{key} appears in {owned[key]} and {slot.slot_id}"})
            owned[key] = slot.slot_id
            url = row.get("source_url")
            if url and not _http(url):
                violations.append({"rule": "invalid_source_url", "detail":
                                   f"{key} has a non-HTTP(S) source URL"})
        if slot.slot_id == "federal-opportunities":
            for row in slot.records:
                family = row.get("requirement_family")
                for target in row.get("targets") or []:
                    if target.get("requirement_family") not in {None, "", family}:
                        violations.append({"rule": "target_lineage", "detail":
                                           f"target for {target.get('requirement_family')} is nested under {family}"})
    if not doc.graph_receipt.get("certified"):
        violations.append({"rule": "graph_not_certified", "detail":
                           "the Federal Pursuit Graph contract is not certified"})
    workflow = doc.graph_receipt.get("workflow_contract") or {}
    if workflow and workflow.get("certified") is False:
        violations.append({"rule": "workflow_not_certified", "detail":
                           "the graph workflow contract is not certified"})
    text = json.dumps(doc.to_dict(), ensure_ascii=False)
    if "—" in text:
        violations.append({"rule": "em_dash", "detail":
                           "an em dash appears in output-reachable product data"})
    return violations


def validate_external_product_html(client_html: str, doc: ExternalProductDocument) -> dict:
    from agents.golden_press.client_visual_contract import validate_client_visual_contract
    from agents.golden_press.style_contract import unstyled_in_context

    violations = validate_external_product_document(doc)
    order = re.findall(r'<section[^>]*data-slot-id="([^"]+)"', client_html)
    expected = [slot.slot_id for slot in doc.slots]
    if order != expected:
        violations.append({"rule": "rendered_slot_order", "detail":
                           f"rendered slots are {order}; expected {expected}"})
    for slot in doc.slots:
        if slot.heading not in client_html:
            violations.append({"rule": "rendered_heading", "detail":
                               f"locked heading is absent: {slot.heading}"})
    if "<script" in client_html.casefold():
        violations.append({"rule": "script_in_client_artifact", "detail":
                           "the client artifact contains script"})
    if "—" in re.sub(r"<[^>]+>", " ", client_html):
        violations.append({"rule": "em_dash", "detail":
                           "an em dash appears in rendered client text"})
    unstyled = unstyled_in_context(client_html)
    if unstyled:
        violations.append({"rule": "unstyled_classes", "detail":
                           f"unstyled classes: {[name for name, _ in unstyled][:12]}"})
    visual = validate_client_visual_contract(client_html, client_name=doc.client_name)
    violations.extend(visual.get("violations") or [])
    return {
        "schema_version": EXTERNAL_PRODUCT_RENDER_VERSION,
        "ok": not violations,
        "violations": violations,
        "slot_order": order,
        "client_visual_contract": visual,
    }


__all__ = (
    "EXTERNAL_PRODUCT_RENDER_VERSION",
    "render_external_product",
    "render_slot",
    "validate_external_product_document",
    "validate_external_product_html",
)
