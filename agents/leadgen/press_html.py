"""Standalone Press Lead Gen HTML. Pure rendering; no filesystem or network I/O.

Chrome declarations extracted from the operator's Desktop reference:
Arista_Networks_Press_Lead_Gen_SKEPTIC_SCORE_CLIENT_DELIVERABLE_2026-09-07.html
The red tokens, typography and report components retain that Market Map family.
"""

from __future__ import annotations

import re
import json
from enum import Enum
from html import escape, unescape
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from agents.reports.lint import BANNED_PHRASES

if TYPE_CHECKING:
    from .press import PressLeadGenReceipt

# R14 client-copy contract, docs/CONTRACT_SURFACES.md. Raw values stay in JSON.
_CLIENT_COPY_BAN = (
    "unverified", "not verified", "did not verify", "no record",
    "not capability-verified", "pending verification", "zero matched",
    "could not confirm",
)
_COPY_BAN = re.compile(
    "|".join(re.escape(phrase) for phrase in (*_CLIENT_COPY_BAN, *BANNED_PHRASES)),
    re.IGNORECASE,
)

CHROME_CSS = """
:root{--red:#ee0000;--red-dark:#b50000;--ink:#151515;--muted:#5f5f5f;--paper:#fff;--wash:#f5f5f5;--line:#d8d8d8;--soft-red:#fff2f2;--gold:#f4c145;--nav:#202020;--green:#2e7d32;--blue:var(--red-dark);--blue2:var(--red);--pale:var(--soft-red);--shadow:0 16px 45px rgba(0,0,0,.09);--max:1540px;--sans:Arial,Helvetica,sans-serif}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:#ededed;color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.45}
.report{max-width:var(--max);margin:0 auto;background:#fff;box-shadow:var(--shadow);overflow:hidden}
a{color:var(--blue);text-underline-offset:3px}
.topbar{display:grid;grid-template-columns:minmax(290px,1fr) auto auto;align-items:center;gap:26px;padding:28px 62px 24px;border-bottom:5px solid var(--blue2)}
.brand{display:flex;align-items:center;gap:24px}
.brand-copy strong{display:block;font-size:22px}
.brand-copy span{display:block;margin-top:4px;color:var(--muted);font-size:12px;letter-spacing:.12em;text-transform:uppercase}
.refresh{padding-right:22px;border-right:1px solid var(--line);text-align:right}
.refresh b{display:block;font-size:13px}
.refresh span{color:var(--muted);font-size:12px}
.toolbar{display:flex;align-items:center;gap:10px}
.hero{padding:64px 62px 42px;background:linear-gradient(130deg,#fff 0%,#fff 60%,#fff3f3 100%)}
.hero .eyebrow,.eyebrow{color:var(--blue2);font-size:13px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}
.hero h1{max-width:1210px;margin:18px 0 22px;font-size:clamp(50px,5vw,82px);line-height:.94;letter-spacing:-.055em}
.hero .lead{max-width:1020px;margin:0;font-size:25px;line-height:1.35}
.boundary{margin:28px 0 0;color:var(--muted);font-size:14px}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);background:#121212;color:#fff}
.metric{appearance:none;background:#121212;color:#fff;border:0;border-right:1px solid #3c3c3c;padding:26px 26px 28px;text-align:left;cursor:pointer}
.metric:last-child{border-right:0}
.metric small{display:block;color:#b9bcc0;font-size:11px;letter-spacing:.1em;text-transform:uppercase}
.metric strong{display:block;margin:9px 0 3px;font-size:35px;line-height:1}
.metric span{color:#c9cccf;font-size:12px}
.nav{position:sticky;top:0;z-index:40;display:flex;gap:26px;align-items:center;overflow-x:auto;padding:17px 62px;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);box-shadow:0 5px 16px rgba(0,0,0,.05);white-space:nowrap}
.nav a{color:#111;font-size:13px;font-weight:800;text-decoration:none}
.section{padding:68px 62px}
.section.alt{background:var(--wash)}
.section-head{display:grid;grid-template-columns:66px minmax(0,1fr) auto;gap:24px;align-items:start;margin-bottom:36px}
.section-number{display:grid;place-items:center;width:58px;height:58px;border:2px solid var(--blue2);color:var(--blue2);font-size:21px;font-weight:900}
.section-head h2{margin:0;font-size:48px;line-height:1;letter-spacing:-.035em}
.section-head p{max-width:780px;margin:12px 0 0;color:var(--muted);font-size:18px}
.section-identity{padding-top:8px;color:var(--muted);font-size:12px;letter-spacing:.12em;text-transform:uppercase}
.data-table{width:100%;border-collapse:collapse;background:#fff}
.data-table th,.data-table td{padding:15px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
.data-table th{background:#e8eaec;color:#565b60;font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.data-table td small{display:block;color:var(--muted);margin-top:4px}
.table-scroll{overflow:auto}
.receipts{display:grid;gap:10px}
.receipt{background:#fff;border:1px solid var(--line)}
.receipt summary{padding:18px;color:var(--blue);font-weight:900;cursor:pointer}
.receipt>p,.receipt>ul{margin:0;padding:0 18px 18px}
.footer{padding:28px 62px;background:#111;color:#ccc;font-size:12px}
.footer strong{color:#fff}
.source-note{color:var(--muted);font-size:13px}
pre.mono{white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;word-break:break-all}
/* Press layout adaptations only; the palette and report chrome above are reused. */
.metric{text-decoration:none}
.section{padding-top:36px;padding-bottom:36px;border-top:1px solid var(--line)}
.section-head h2{font-size:34px}
.data-table{font-size:14px;table-layout:fixed}
.data-table td,.data-table th,.receipt,.boundary,.toolbar{overflow-wrap:anywhere}
.receipt-body{padding:0 18px 18px}
.receipt-body p{margin:10px 0}
.receipt-body dl{display:grid;grid-template-columns:150px minmax(0,1fr);gap:8px 16px}
.receipt-body dt{font-weight:700}
.receipt-body dd{margin:0}
.data-table .receipt-body dl{display:block}
.data-table .receipt-body dt{margin-top:12px}
.data-table .receipt-body dd{font-size:13px}
.empty{padding:18px;border-left:4px solid var(--red);background:var(--wash)}
:target{scroll-margin-top:64px}
:focus-visible{outline:3px solid var(--red);outline-offset:3px}
@media(max-width:1050px){.topbar{grid-template-columns:1fr auto}.toolbar{display:none}}
@media(max-width:760px){
.topbar,.hero,.section,.nav,.footer{padding-left:22px;padding-right:22px}
.topbar{grid-template-columns:1fr}.refresh{text-align:left;border:0}
.hero{padding-top:36px}.hero h1{font-size:44px}.hero .lead{font-size:20px}
.metrics{grid-template-columns:1fr 1fr}.metric{border-bottom:1px solid #3c3c3c}
.section-head{grid-template-columns:54px minmax(0,1fr);gap:16px}
.section-head h2{font-size:28px}.section-number{width:48px;height:48px}
.data-table{min-width:760px}.receipt-body dl{grid-template-columns:1fr}
}
@media print{
body{background:#fff}.report{max-width:none;box-shadow:none}.nav{display:none}
.hero{padding-top:24px}.hero h1{font-size:42px}.table-scroll{overflow:visible}
.data-table{min-width:0}.receipt{break-inside:avoid}
}
"""


def _text(value: Any) -> str:
    """Escape data; keep prohibited prose in the JSON sidecar, not client copy."""
    if value is None or value == "":
        return "Not supplied"
    if isinstance(value, Enum):
        value = value.value
    text = unescape(str(value)).replace(chr(0x2014), " · ")
    if _COPY_BAN.search(text):
        text = "Review source detail in JSON sidecar."
    return escape(text, quote=True)


def _link(url: Any, label: Any) -> str:
    # Model URLs are HTTP(S); keep the guard for model_copy / imported receipts.
    raw = str(url or "")
    if urlsplit(raw).scheme.lower() not in {"http", "https"}:
        return _text(label)
    href = escape(quote(raw, safe=":/?#[]@!$&'()*+,;=%"), quote=True)
    return f'<a href="{href}" rel="noreferrer">{_text(label)}</a>'


def _details(label: Any, fields: list[tuple[str, Any]]) -> str:
    body = "".join(f"<dt>{_text(k)}</dt><dd>{_text(v)}</dd>" for k, v in fields)
    return (f'<details class="receipt"><summary>{_text(label)}</summary>'
            f'<div class="receipt-body"><dl>{body}</dl></div></details>')


def _section(number: int, anchor: str, title: str, subtitle: str, body: str) -> str:
    return (f'<section class="section" id="{anchor}"><div class="section-head">'
            f'<div class="section-number">{number:02d}</div><div>'
            f'<h2>{_text(title)}</h2><p>{_text(subtitle)}</p></div></div>'
            f'{body}</section>')


def render_html(receipt: PressLeadGenReceipt) -> str:
    """Render the receipt without discovery, enrichment, or I/O."""
    from .press import PressLeadGenReceipt
    receipt = PressLeadGenReceipt.model_validate(receipt.model_dump(mode="json"))
    parents = {p.assessment_id: p for p in receipt.parents}
    parent_anchors = {p.assessment_id: f"parent-{i}"
                      for i, p in enumerate(receipt.parents)}
    traces = {t.trace_id: t for t in receipt.traces}
    metrics = "".join(
        f'<a class="metric" href="#tier-{bucket.lead_tier.value}">'
        f'<small>{_text(bucket.label)}</small><strong>{len(bucket.lead_ids)}</strong>'
        f'<span>{"Active leads" if bucket.active else "Retained receipts"}</span></a>'
        for bucket in receipt.by_lead_tier
    )
    parent_rows = []
    for parent in receipt.parents:
        detail = _details("Assessment receipt", [
            ("Assessment ID", parent.assessment_id),
            ("Subject ID", parent.subject_id),
            ("Notice ID", parent.notice_id),
            ("Solicitation", parent.solicitation_number),
            ("Classification", parent.live_classification),
            ("Recommendation", parent.live_recommendation),
            ("Requirement", parent.requirement_span),
        ])
        if parent.research_subject is not None:
            from .target_html import render_research, render_targets
            subject = parent.research_subject
            context = json.loads(subject.discovery_context_json) if subject.discovery_context_json else {}
            boundary = "Research only; direct qualification unchanged. Code boundary: " + str(context.get("direct_code_boundary", "unknown"))
            detail += render_research(parent.research) + render_targets(parent.targets)
            detail += ('<div class="receipt-body"><p><b>Research needed</b> · ' + _text(subject.source_posture.replace('_',' ')) + '</p>'
                       + _link(subject.source_url, subject.source_record_id)
                       + '<p>' + _text(subject.next_ask) + '</p>'
                       + '<p>' + _text(subject.route_hypothesis) + '</p><ul>'
                       + ''.join('<li>' + _text(q) + '</li>' for q in subject.open_questions)
                       + '</ul><p>' + _text(boundary) + '</p><details><summary>Show work: original source fields and value basis</summary><pre class="mono">'
                       + escape(subject.source_payload_json) + '</pre><pre class="mono">' + escape(subject.discovery_context_json or '') + '</pre></details></div>')
        parent_rows.append(
            f'<tr id="{parent_anchors[parent.assessment_id]}">'
            f'<td><b>{_text(parent.title)}</b>{detail}</td>'
            f'<td>{_text(parent.agency)}</td>'
            f'<td>{_text(parent.subject_kind)}</td>'
            f'<td>{len(parent.lead_ids)}</td></tr>')
    parent_body = (
        '<div class="table-scroll"><table class="data-table"><thead><tr>'
        '<th>Opportunity / receipt</th><th>Agency</th><th>Subject kind</th>'
        '<th>Child leads</th></tr></thead><tbody>' + "".join(parent_rows)
        + '</tbody></table></div>' if parent_rows else
        '<p class="empty">No parent assessments supplied.</p>'
    )
    sections = [_section(1, "parents", "Opportunity assessments",
                        f"{len(receipt.parents)} parents · {len(receipt.leads)} draft leads. "
                        "An opportunity assessment remains valid with zero children.",
                        parent_body)]
    from agents.leadgen.target_html import render_research, render_targets
    priority_rows = []
    for lead in receipt.leads:
        if lead.research and lead.research.priority:
            parent = parents[lead.parent_assessment_id]
            priority_rows.append('<article class="product-record"><h3>' + _text(parent.title)
                                 + '</h3>' + render_research(lead.research)
                                 + render_targets(lead.targets) + '</article>')
    if priority_rows:
        sections.insert(0, _section(0, "priority", f"Priority opportunities · {receipt.as_of.date()}",
                                   "", ''.join(priority_rows[:3])))
    for index, bucket in enumerate(receipt.by_lead_tier, 2):
        rows = []
        for lead in receipt.leads:
            if lead.lead_tier != bucket.lead_tier:
                continue
            parent = parents.get(lead.parent_assessment_id)
            title = parent.title if parent else lead.parent_assessment_id
            anchor = parent_anchors.get(lead.parent_assessment_id)
            parent_link = (f'<a href="#{anchor}">{_text(title)}</a>'
                           if anchor else _text(title))
            motion, pathway = lead.buying_motion, lead.external_pathway
            seller, action = lead.seller_path, lead.next_action
            trace = traces.get(lead.decision_trace_id)
            detail = _details("Show the lead receipt", [
                ("Lead ID", lead.lead_id), ("Readiness", lead.readiness),
                ("Buying motion", motion.kind), ("Buying clock", motion.clock),
                ("Projected window", motion.window.model_dump_json() if motion.window else None),
                ("Seller path", seller.kind), ("Holder", seller.holder),
                ("Vehicle", seller.vehicle), ("Next action", action.verb),
                ("Action detail", action.object), ("Due", action.due),
                ("Resolve before action", action.blocked_by), ("Owner", action.owner),
                ("Contactability", lead.contactability),
                ("Communication permission", lead.communication_permission),
                ("Decision trace ID", lead.decision_trace_id),
                ("Decision note", trace.notes if trace else None),
            ])
            from agents.assess.source_clock import acquired_at
            evidence = "".join(
                f'<li>{_link(item.source_url, item.evidence_id)} · '
                f'{_text(item.excerpt)}<br>Source acquisition: '
                f'{_text(acquired_at(item).isoformat()) if acquired_at(item) else "Collection time not established"}'
                f'{" · Original timestamp: " + _text(item.source_acquisition.raw_values[0]) if item.source_acquisition and acquired_at(item) else ""}</li>' for item in pathway.evidence
            )
            contacts = "; ".join(" · ".join(filter(None, (c.name, c.title, c.email, c.phone)))
                                 for c in pathway.published_contacts)
            rows.append(
                '<tr><td>' + parent_link + detail + '</td>'
                f'<td>{_text(motion.buyer_agency)}<small>{_text(contacts)}</small></td>'
                f'<td>{_text(action.object)}<small>{_text(action.blocked_by)}</small>'
                + render_research(lead.research) + render_targets(lead.targets) + '</td>'
                f'<td>{_link(pathway.source_url, "Open published pathway")}'
                f'<details class="receipt"><summary>Source evidence</summary>'
                f'<div class="receipt-body"><ul>{evidence}</ul></div></details></td></tr>')
        body = (
            '<div class="table-scroll"><table class="data-table"><thead><tr>'
            '<th>Opportunity / lead receipt</th><th>Buyer / published contact</th>'
            '<th>Next action / dependency</th><th>Evidence</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>' if rows else
            '<p class="empty">' + (
                f'{_text(bucket.label)}: 0 active leads. Empty is expected '
                'when four-leg receipts are missing.' if bucket.active else
                f'{_text(bucket.label)}: 0 receipts.'
            ) + '</p>'
        )
        sections.append(_section(index, f"tier-{bucket.lead_tier.value}",
                                 bucket.label, f"{len(bucket.lead_ids)} "
                                 + ("active leads" if bucket.active else "retained receipts"), body))
    steps = "".join(
        f'<tr><td>{step.step}. {_text(step.name)}</td>'
        f'<td>{_text(step.status)}</td><td>{_text(step.notes)}'
        f'<small>{_text(step.artifact)}</small></td></tr>' for step in receipt.steps
    )
    sections.append(_section(7, "steps", "Steps receipt", "Build Plan steps 1 through 9.",
        '<div class="table-scroll"><table class="data-table"><thead><tr>'
        '<th>Step</th><th>Status</th><th>Receipt</th></tr></thead>'
        f'<tbody>{steps}</tbody></table></div>'))
    trace_details = "".join(_details(trace.trace_id, [
        ("Parent", trace.parent_assessment_id), ("Lead", trace.lead_id),
        ("Assess gate", trace.assess_gate), ("Steps", "; ".join(trace.steps)),
        ("Evidence IDs", "; ".join(trace.evidence_ids)), ("Notes", trace.notes),
    ]) for trace in receipt.traces)
    sections.append(_section(8, "coverage", "Coverage and decision trace",
        "Coverage placeholder · Decision-trace placeholder",
        f'<p>{_text(receipt.coverage_placeholder)}</p>'
        f'<p>{_text(receipt.decision_trace_placeholder)}</p>'
        f'<div class="receipts">{trace_details}</div>'))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(receipt.client_name)} · Press Lead Gen</title>
<style>{CHROME_CSS}</style></head><body><main class="report">
<header class="topbar"><div class="brand"><div class="brand-copy">
<strong>LILA</strong><span>Federal Market Map · Press Lead Gen</span></div></div>
<div class="refresh"><b>Assessment as of</b><span>{_text(receipt.as_of.isoformat())}</span></div>
<div class="toolbar"><b>{_text(receipt.client_name)}</b></div></header>
<section class="hero"><div class="eyebrow">Primary deliverable · HTML</div>
<h1>{_text(receipt.client_name)}<br>Press Lead Gen</h1>
<p class="lead">Opportunity assessments, lead tiers, and the evidence behind the next action.</p>
<p class="boundary">{
    "Orchestration stub. Active lead T1 / lead T2 remain empty."
    if receipt.stub else
    "Real qualifier ran. HOLD remains the fail-closed default when evidence is thin."
} Assessment run: {_text(receipt.assess_run_id)}.</p></section>
<div class="metrics">{metrics}</div>
<nav class="nav" aria-label="Report sections"><a href="#parents">Parents</a>
<a href="#tier-LEAD_T1">Lead T1 / T2</a><a href="#tier-WATCH">Watch</a>
<a href="#tier-HOLD">Hold</a><a href="#tier-REJECT">Reject</a>
<a href="#steps">Steps</a><a href="#coverage">Coverage / trace</a></nav>
{"".join(sections)}
<footer class="footer"><strong>LILA · {_text(receipt.client_name)}</strong>
<br>Primary press artifact: branded HTML with Market Map chrome. MD/JSON are sidecars.
<br>The complete release includes the assessment, lead report, dossier and source receipts.</footer>
</main></body></html>
"""
