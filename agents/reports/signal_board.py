"""Deterministic Federal Opportunity Pre-Assessment renderer.

Standardizes LILA's client deliverable on the operator-LOCKED "Signal Board"
format (docs/reference/federal_opportunity_signals.reference.html, spec at
docs/opportunity_signals_format.md). NO LLM composes this artifact: the locked
template is a fixed shell (structure, labels, CSS, doctrine wording) and this
module fills only per-report data slots from pipeline data, so every report is
byte-identical in format, carries no LLM voice, and cannot drift. Conformance is
enforced by lint_signal_board and a golden test.

Review remediation (Codex, 2026-07-13): every per-report field is tokenized
(no cross-client leakage); links pass a scheme allowlist and content is escaped;
the marquee scale is built from structured award rows (never a mislabeled stat);
agency badges fall back to text when no seal resolves; the lint enforces
section-local records, not just the static shell. Branding assets are resolved
UPSTREAM (adapter) and passed in as data URIs; the renderer never fetches.
See [[lila-opportunity-assessment-doctrine]].
"""
from __future__ import annotations

import html as _html
import os
import re
from typing import Any, Optional

from agents.reports.links import (
    CanonicalFederalLink,
    SAM_NOTICE_BUILDER,
    USASPENDING_AWARD_BUILDER,
    build_sam_notice_link,
    build_usaspending_award_link,
)

_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "templates", "signal_board.template.html")

#: Every data token the locked template exposes. render() fills all of them;
#: an unfilled {{TOKEN}} is a hard lint failure.
TOKENS = (
    "CLIENT_NAME", "CLIENT_LOGO", "CLIENT_BRAND_ATTRS",
    "CLIENT_BRAND_SUB", "REPORT_DATE",
    "HERO_CONTEXT", "CHIPS",
    "SCALE_TOTAL", "SCALE_COUNTS", "SCALE_WORK", "NEWS", "SIGNAL_CARDS",
    "COVERAGE", "BEST_FIT", "COMPETITORS", "SECTION_03_BODY", "HORIZON",
    "POCS", "SECTION_03_NAV", "SECTION_03_TITLE", "SECTION_03_META",
    "SECTION_03_CONTEXT", "SECTION_03_GRID_CLASS", "HORIZON_META",
    "HORIZON_CONTEXT", "HORIZON_TEASER",
    "SOURCE_COVERAGE_SUMMARY", "SOURCE_COVERAGE",
    "CONNECTED_CAPABILITIES", "EVIDENCE", "SEQUENCE", "DATA_CURRENT",
)

_SAFE_SCHEMES = ("https://", "http://", "mailto:")


def _e(x: Any) -> str:
    return _html.escape("" if x is None else str(x), quote=True)


def _safe_url(url: Any) -> str:
    """Allowlist URL schemes (P2-6): only http(s)/mailto survive; else '#'."""
    if isinstance(url, CanonicalFederalLink):
        url = url.url
    u = ("" if url is None else str(url)).strip()
    return u if u.lower().startswith(_SAFE_SCHEMES) else "#"


def _anchor_attrs(url: Any) -> str:
    """Safe href plus canonical-builder lineage for federal links."""
    attrs = [f'href="{_e(_safe_url(url))}"', 'target="_blank"',
             'rel="noopener noreferrer"']
    if isinstance(url, CanonicalFederalLink):
        attrs.extend([
            f'data-link-builder="{_e(url.builder)}"',
            f'data-link-record="{_e(url.record_id)}"',
            f'data-link-reconciled="{1 if url.reconciled else 0}"',
        ])
    return " ".join(attrs)


def _link(url: Any, text: Any, *, thirdparty_identity: bool = False) -> str:
    """Render one escaped link.

    ``thirdparty_identity`` is deliberately narrower than a card-level
    exemption: it protects only the cited external identity inside the
    anchor.  Neighboring analyst-authored copy remains visible to the
    white-label and client-bleed lints.
    """
    thirdparty = ' data-thirdparty="1"' if thirdparty_identity else ""
    return f'<a{thirdparty} {_anchor_attrs(url)}>{_e(text)}</a>'


def _materialize_federal_links(
    value: Any,
    reconciled: Optional[dict[tuple[str, str], bool]],
) -> Any:
    """Turn structured identities into canonical links, recursively.

    Client content stores primary-record identities, never SPA URL strings.
    Re-running this transform is byte/data stable: the structured keys remain
    the source and any prior materialized value is deterministically replaced.
    """
    if isinstance(value, list):
        return [_materialize_federal_links(item, reconciled) for item in value]
    if not isinstance(value, dict):
        return value
    out = {
        # Machine evidence is an inert validation sidecar: it is never
        # rendered.  Materializing identities inside it would add phantom
        # occurrences to the renderer's canonical-link manifest.
        key: (item if key == "machine_evidence"
              else _materialize_federal_links(item, reconciled))
        for key, item in value.items()
    }
    refs = [key for key in ("award_generated_id", "sam_notice_guid",
                            "opp_notice_guid") if out.get(key)]
    if len(refs) > 1:
        raise ValueError(
            "a link-bearing row must name exactly one federal record identity")
    if out.get("award_generated_id"):
        record_id = str(out["award_generated_id"])
        ok = bool((reconciled or {}).get(
            (USASPENDING_AWARD_BUILDER, record_id), False))
        out["url"] = build_usaspending_award_link(
            record_id, reconciled=ok)
    elif out.get("sam_notice_guid"):
        record_id = str(out["sam_notice_guid"])
        ok = bool((reconciled or {}).get(
            (SAM_NOTICE_BUILDER, record_id), False))
        out["url"] = build_sam_notice_link(record_id, reconciled=ok)
    elif out.get("opp_notice_guid"):
        record_id = str(out["opp_notice_guid"])
        ok = bool((reconciled or {}).get(
            (SAM_NOTICE_BUILDER, record_id), False))
        out["opp_url"] = build_sam_notice_link(record_id, reconciled=ok)
    return out


def canonical_federal_link_manifest(value: Any) -> tuple[CanonicalFederalLink, ...]:
    """Occurrence-level builder manifest retained outside rendered HTML."""
    links: list[CanonicalFederalLink] = []

    def walk(item: Any) -> None:
        if isinstance(item, CanonicalFederalLink):
            links.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    return tuple(links)


# ── band builders: reproduce the locked sb-* inner markup exactly ────────────

def _chip(c: dict) -> str:
    tone = c.get("tone")
    cls = "sb-chip" + (f" {tone}" if tone in ("hot", "live") else "")
    return f'<span class="{cls}">{_e(c.get("label"))}</span>'


def _brand_attrs(
    kind: str,
    label: Any,
    logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    """Inert identity metadata consumed only by the Command Center editor."""
    from agents.reports.signal_board_presentation import (
        DEFAULT_LOGO_SIZE_PERCENT,
        logo_identity,
        validate_logo_size_percent,
    )

    text = "" if label is None else str(label)
    if not text.strip():
        return ""
    key = logo_identity(text, kind, text)
    if logo_sizes is not None and not isinstance(logo_sizes, dict):
        raise ValueError("logo_sizes must be an identity-to-percentage object")
    percent = validate_logo_size_percent(
        (logo_sizes or {}).get(key, DEFAULT_LOGO_SIZE_PERCENT))
    scale = f"{percent / 100:.2f}".rstrip("0").rstrip(".")
    return (f'data-brand-kind="{_e(kind)}" data-brand-key="{_e(key)}" '
            f'data-brand-label="{_e(text)}" data-brand-size="{percent}" '
            f'style="--sb-brand-scale:{scale}"')


def _signal(s: dict) -> str:
    return (f'<div class="sb-signal"><div class="sb-label">{_e(s.get("label"))}</div>'
            f'<strong>{_e(s.get("value"))}</strong><b>{_e(s.get("sub"))}</b>'
            f'<small>{_e(s.get("detail"))}</small></div>')


def _coverage(
    m: dict, logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    seal = (f'<img src="{_e(m["seal"])}" alt="" aria-hidden="true">'
            if m.get("seal") else '<img alt="" aria-hidden="true">')
    label = m.get("components") or m.get("dept")
    return (f'<div class="sb-agency-mark" '
            f'{_brand_attrs("agency", label, logo_sizes)}>'
            f'{seal}<span><b>{_e(m.get("dept"))}</b>'
            f'<small>{_e(m.get("components"))}</small></span></div>')


def _agency_badge(
    o: dict, logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    """P2-7: has-image ONLY when a seal actually resolves; else a text code."""
    label = o.get("agency_name") or o.get("code")
    attrs = _brand_attrs("agency", label, logo_sizes)
    if o.get("seal"):
        return (f'<div class="sb-agency-code has-image" {attrs}>'
                f'<img class="sb-agency-upload" alt="{_e(o.get("agency_name"))} seal" '
                f'src="{_e(o["seal"])}"><span>{_e(o.get("code"))}</span></div>')
    return (f'<div class="sb-agency-code" {attrs}>'
            f'<span>{_e(o.get("code"))}</span></div>')


def _opp(o: dict, logo_sizes: Optional[dict[str, int]] = None) -> str:
    links = "".join(_link(ev.get("url"), ev.get("label"))
                    for ev in (o.get("evidence") or []))
    cells = "".join(
        f'<div class="sb-cell"><div class="sb-label">{_e(c.get("label"))}</div>'
        f'<strong>{_e(c.get("value"))}</strong><small>{_e(c.get("small"))}</small></div>'
        for c in (o.get("cells") or []))
    procurement = _procurement_facts(o)
    return (f'<article class="sb-opp"><div class="sb-rank">{_e(o.get("rank"))}</div>'
            f'<div class="sb-seal-wrap">{_agency_badge(o, logo_sizes)}</div>'
            f'<div class="sb-opp-title-wrap">'
            f'<a class="sb-opp-title" {_anchor_attrs(o.get("url"))}>'
            f'{_e(o.get("title"))}</a>'
            f'<div class="sb-opp-account">{_e(o.get("account"))}</div>'
            f'<div class="sb-evidence-links">{links}</div></div>{cells}'
            f'{procurement}</article>')


def _client_relevance(row: dict) -> str:
    """Render the shared public client-relevance projection, when present."""
    relevance = row.get("client_relevance")
    if isinstance(relevance, dict):
        text = str(relevance.get("text") or "").strip()
    elif isinstance(relevance, str):
        # Operator/legacy tolerance; C1 emits the strict {kind, text} shape.
        text = relevance.strip()
    else:
        text = ""
    if not text:
        return ""
    return (f'<div class="sb-client-relevance">'
            f'<b>Client relevance</b><span>{_e(text)}</span></div>')


def _procurement_facts(row: dict) -> str:
    """Render only the procurement fields carried by the cited record.

    C1 owns the projection and its evidence binding.  The renderer neither
    fills missing fields nor derives acquisition facts from identifiers; it
    makes the source-backed values and the known gaps visible to the reader.
    """
    facts = row.get("procurement_facts")
    missing = row.get("missing_procurement_facts")
    if not isinstance(facts, list):
        facts = []
    if not isinstance(missing, list):
        missing = []

    fact_rows = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        label = str(fact.get("label") or "").strip()
        value = str(fact.get("value") or "").strip()
        source = str(fact.get("source_field") or "").strip()
        if not label or not value:
            continue
        source_html = f'<small>{_e(source)}</small>' if source else ""
        fact_rows.append(
            f'<div class="sb-procurement-fact"><dt>{_e(label)}</dt>'
            f'<dd><span>{_e(value)}</span>{source_html}</dd></div>'
        )

    missing_fields = []
    for field in missing:
        text = str(field or "").strip()
        if text and text not in missing_fields:
            missing_fields.append(text)
    if not fact_rows and not missing_fields:
        return ""

    facts_html = (
        f'<dl class="sb-procurement-facts">{"".join(fact_rows)}</dl>'
        if fact_rows else ""
    )
    missing_html = (
        '<div class="sb-procurement-missing"><b>Not stated in cited record'
        f'</b><span>{_e(" · ".join(missing_fields))}</span></div>'
        if missing_fields else ""
    )
    return (f'<div class="sb-procurement-panel">'
            f'<div class="sb-procurement-heading">Procurement record</div>'
            f'{facts_html}{missing_html}</div>')


def _competitor(
    c: dict, logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    # A structured award corridor may legitimately name another portfolio
    # client. Exempt only its cited identity link; small/wedge copy is
    # analyst-authored and must remain inside the white-label/bleed scan.
    cited_identity = _safe_url(c.get("url")) != "#"
    title = c.get("title")
    logo = (f'<img class="sb-competitor-logo" alt="{_e(title)} logo" '
            f'src="{_e(c.get("logo"))}">' if c.get("logo") else "")
    logo_class = " has-logo" if logo else ""
    relevance = _client_relevance(c)
    procurement = _procurement_facts(c)
    legacy_wedge = ("" if relevance else
                    f'<div class="sb-wedge">{_e(c.get("wedge"))}</div>')
    return (f'<article class="sb-competitor{logo_class}" '
            f'{_brand_attrs("company", title, logo_sizes)}>'
            f'<div class="sb-label">{_e(c.get("label"))}</div>'
            f'<div class="sb-competitor-identity">{logo}'
            f'{_link(c.get("url"), title, thirdparty_identity=cited_identity)}'
            f'</div>'
            f'<div class="sb-money">{_e(c.get("money"))}</div>'
            f'<small>{_e(c.get("small"))}</small>'
            f'{procurement}{relevance}{legacy_wedge}</article>')


def _team_recompete_line(t: dict) -> str:
    """The play's recompete context line (recompete-aware theses,
    2026-07-17). KNOWN and NOT FOUND render for the client; CALENDAR GAP
    renders nothing here and rides the INTERNAL sidecar only. Copy is
    informative noun phrases, softened, no internal tier vocabulary: a
    record-backed event states the event and its record; an analyst-tier
    match reads as a signal, never as a record."""
    rc = t.get("recompete")
    if not rc:
        return ""
    state = rc.get("state")
    if state == "not_found":
        return ('<div class="sb-team-recompete">No recompete identified in '
                'the current screening window</div>')
    if state != "known":
        return ""
    ev = rc.get("event") or {}
    kind = str(ev.get("kind") or "")
    date_bit = f' · {_e(ev.get("date"))}' if ev.get("date") else ""
    rec_bit = (f' · {_e(ev.get("source_record_id"))}'
               if ev.get("source_record_id") else "")
    if ev.get("tier") == "record":
        head = {
            "bridge": "Bridge activity on the calendar",
            "follow_on": "Follow-on on the calendar",
            "forecast": "Forecast successor signal",
            "recompete": "Recompete on the calendar",
            "expiry": "Award period end on the calendar",
        }.get(kind)
        if head is None:
            return ""
        body = f'{head} · {_e(ev.get("label"))}{rec_bit}{date_bit}'
    elif ev.get("tier") == "analyst":
        qualifier = {
            "bridge": "bridge",
            "follow_on": "follow-on",
            "forecast": "forecast successor",
            "recompete": "recompete",
            "expiry": "award-period",
        }.get(kind)
        if qualifier is None:
            return ""
        body = (f'Possible {qualifier} signal · {_e(ev.get("label"))}'
                f'{date_bit}')
    else:
        return ""
    source = ev.get("url") or ev.get("source_url")
    if source:
        body += f' · {_link(source, "source")}'
    return f'<div class="sb-team-recompete">{body}</div>'


def _team(
    t: dict,
    client_name: Any = None,
    logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    def _co(name, logo, *, kind="company", identity_label=None,
            thirdparty=False):
        evidence_attr = ' data-thirdparty="1"' if thirdparty else ""
        brand_attrs = _brand_attrs(
            kind, identity_label or name, logo_sizes)
        if logo:
            img = f'<img class="sb-company-logo" alt="{_e(name)} logo" src="{_e(logo)}">'
            return f'<div class="sb-company has-logo" {brand_attrs}{evidence_attr}>{img}<span class="sb-company-name">{_e(name)}</span></div>'
        return f'<div class="sb-company" {brand_attrs}{evidence_attr}><span class="sb-company-name">{_e(name)}</span></div>'
    citation = (f' · {_link(t.get("url"), t.get("citation_label") or "award record")}'
                if t.get("url") else "")
    relevance = _client_relevance(t)
    procurement = _procurement_facts(t)
    return (f'<article class="sb-team-card"><div class="sb-team-line">'
            f'{_co(t.get("client"), t.get("client_logo"), kind="client", identity_label=client_name)}<div class="sb-x">×</div>'
            f'{_co(t.get("partner"), t.get("partner_logo"), thirdparty=True)}</div>'
            f'<div class="sb-for">For</div>'
            f'<div class="sb-team-target" data-thirdparty="1">{_e(t.get("target"))}</div>'
            f'{relevance}'
            f'<div class="sb-angle"><b>ANGLE</b><span>{_e(t.get("angle"))}</span></div>'
            f'<div class="sb-team-proof">{_e(t.get("proof"))}{citation}</div>'
            f'{_team_recompete_line(t)}{procurement}</article>')


def _acquisition_pathway(
    row: dict,
    logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    """Render agency buying-path research without a partner assertion."""

    citation = (
        _link(row.get("url"), row.get("citation_label") or "official record")
        if row.get("url") else _e(row.get("citation_label")))
    return (
        '<article class="sb-acquisition-card">'
        '<div class="sb-acquisition-head">'
        f'<div class="sb-seal-wrap">{_agency_badge(row, logo_sizes)}</div>'
        '<div class="sb-acquisition-title">'
        f'<div class="sb-label">{_e(row.get("label"))}</div>'
        f'<strong>{_e(row.get("title"))}</strong></div></div>'
        '<div class="sb-acquisition-field is-requirement">'
        '<b>Published requirement</b>'
        f'<span>{_e(row.get("requirement"))}</span></div>'
        f'{_client_relevance(row)}'
        '<div class="sb-acquisition-field">'
        '<b>Source-stated buying path</b>'
        f'<span>{_e(row.get("pathway"))}</span></div>'
        '<div class="sb-acquisition-field">'
        '<b>Access condition</b>'
        f'<span>{_e(row.get("access"))}</span></div>'
        '<div class="sb-acquisition-action">'
        '<b>Capture research action</b>'
        f'<span>{_e(row.get("action"))}</span></div>'
        f'<div class="sb-acquisition-citation">{citation}</div>'
        f'{_procurement_facts(row)}</article>'
    )


def _section_03_copy(
    teaming: list[dict], acquisition_pathways: list[dict],
) -> tuple[str, str, str, str, str]:
    """One physical section, one honest semantic role."""

    if teaming and acquisition_pathways:
        raise ValueError(
            "Section 03 cannot mix teaming and acquisition pathways")
    if acquisition_pathways:
        return (
            "Acquisition paths",
            "Federal acquisition pathways",
            "SOURCE-STATED ROUTES · ACCESS CONDITIONS · VALIDATION QUEUE",
            "These distinct records show how agencies state or execute "
            "relevant buys and what must be verified before pursuit. They "
            "are acquisition-path evidence, not teaming recommendations.",
            "sb-acquisition-grid",
        )
    return (
        "Teaming",
        "Candidate teaming opportunities",
        _teaming_meta(teaming),
        "Candidate pairings are hypotheses grounded in cited prime-subaward "
        "records; each card separates the evidenced route from what still "
        "requires validation.",
        "sb-team-grid",
    )


def _teaming_meta(rows: list[dict]) -> str:
    """Derive the band summary from the rendered citations, never a fixture.

    Matched prime-subaward records, cited award-holder routes, and legacy
    operator candidates are counted separately. The section title already
    names all rows as candidates, so the meta can state the exact evidence
    class without implying that a historical subaward proves access to the
    selected opportunity.
    """
    prime_subaward_records = sum(
        1 for row in rows
        if (row.get("machine_evidence") or {}).get("source_kind")
        == "usaspending-subaward"
        or "MATCHED PRIME-SUBAWARD RECORD"
        in str(row.get("proof") or "").upper()
        or "EVIDENCED SUBAWARD ROUTE"
        in str(row.get("proof") or "").upper()
        or "ROUTE EVIDENCED" in str(row.get("proof") or "").upper()
    )
    award_routes = sum(
        1 for row in rows
        if (row.get("machine_evidence") or {}).get("source_kind")
        == "corridor-entry-thesis" and row.get("award_generated_id")
    )
    operator_candidates = len(rows) - prime_subaward_records - award_routes
    parts = []
    if prime_subaward_records:
        record_word = "RECORD" if prime_subaward_records == 1 else "RECORDS"
        parts.append(
            f"{prime_subaward_records} MATCHED PRIME-SUBAWARD {record_word}")
    if award_routes:
        route_word = "ROUTE" if award_routes == 1 else "ROUTES"
        parts.append(f"{award_routes} CITED AWARD-HOLDER {route_word}")
    if operator_candidates:
        candidate_word = (
            "CANDIDATE" if operator_candidates == 1 else "CANDIDATES")
        parts.append(f"{operator_candidates} OPERATOR {candidate_word}")
    if not parts:
        parts.append("0 SOURCE-BACKED CANDIDATES")
    return " · ".join(parts)


def _horizon_agency(h: dict) -> str:
    """Exact structured agency identity for a horizon card."""
    machine = h.get("machine_evidence")
    agency = h.get("agency_name")
    if not agency and isinstance(machine, dict):
        agency = machine.get("agency")
    return str(agency or "").strip()


def _horizon_marks(h: dict) -> list[dict]:
    """Structured identities rendered as independent horizon mark slots."""
    raw = h.get("organization_marks")
    if isinstance(raw, list):
        marks = []
        for mark in raw:
            if not isinstance(mark, dict):
                continue
            kind = str(mark.get("kind") or "").strip()
            label = str(mark.get("label") or "").strip()
            if kind not in {"agency", "client", "company"} or not label:
                continue
            marks.append({
                "kind": kind,
                "label": label,
                "display": str(mark.get("display") or label).strip(),
                "logo": str(mark.get("logo") or ""),
            })
        if marks:
            return marks
    agency = _horizon_agency(h)
    if not agency:
        return []
    display = str(h.get("label") or "").split("·", 1)[0].strip() or agency
    return [{
        "kind": "agency", "label": agency, "display": display,
        "logo": str(h.get("seal") or ""),
    }]


def _horizon_mark(
    mark: dict, logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    kind = mark["kind"]
    label = mark["label"]
    display = mark["display"]
    logo = mark.get("logo")
    cls = "sb-horizon-mark has-logo" if logo else "sb-horizon-mark"
    if logo:
        image = (f'<img class="sb-horizon-logo" alt="{_e(display)} logo" '
                 f'src="{_e(logo)}">')
    else:
        image = '<img class="sb-horizon-logo" alt="" aria-hidden="true">'
    thirdparty = ' data-thirdparty="1"' if kind == "company" else ""
    return (f'<div class="{cls}" '
            f'{_brand_attrs(kind, label, logo_sizes)}{thirdparty}>'
            f'{image}'
            f'<span>{_e(display)}</span></div>')


def _horizon_work_context(h: dict) -> tuple[str, str]:
    """Render only C1's public projection; machine evidence stays inert."""
    job = h.get("job_context")
    if isinstance(job, dict) and str(job.get("text") or "").strip():
        kind = str(job.get("kind") or "")
        label = "Work detail" if kind == "not-stated" else "Cited work"
        return label, str(job["text"]).strip()
    # Legacy/operator cards predate the C1 projection. They remain readable
    # without treating an internal relevance excerpt as public copy.
    return "Work detail", "Work detail is not stated in the cited record."


def _horizon_decision(h: dict) -> str:
    decision = str(h.get("decision") or "").strip().upper()
    return decision if decision in {"ATTACK", "DEFEND", "QUALIFY"} else ""


def _horizon_source_timing(h: dict) -> str:
    """Visible citation for the source identity and its stated time value."""
    citation = str(h.get("source_citation") or "").strip()
    timing = h.get("timing_basis")
    source_value = precision = ""
    if isinstance(timing, dict):
        source_value = str(timing.get("source_value") or "").strip()
        precision = str(timing.get("precision") or "").strip().upper()
    if not citation and not source_value:
        return ""

    citation_html = (
        f'<span class="sb-horizon-source">{_e(citation)}</span>'
        if citation else ""
    )
    timing_text = source_value
    if source_value and precision:
        timing_text = f"{source_value} · {precision}"
    timing_html = (
        f'<small>Source timing · {_e(timing_text)}</small>'
        if timing_text else ""
    )
    return (f'<div class="sb-horizon-citation"><b>Source record</b>'
            f'{citation_html}{timing_html}</div>')


def _horizon(
    h: dict, logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    cls = "sb-horizon-card hot" if h.get("hot") else "sb-horizon-card"
    mark_rows = _horizon_marks(h)
    marks = "".join(
        _horizon_mark(mark, logo_sizes) for mark in mark_rows)
    cited_company_identity = (
        _safe_url(h.get("url")) != "#"
        and any(mark["kind"] == "company" for mark in mark_rows)
    )
    title = _link(
        h.get("url"), h.get("title"),
        thirdparty_identity=cited_company_identity,
    )
    label = str(h.get("label") or "")
    if _is_award_period_horizon(h):
        label = label.replace("AWARD WINDOW", "AWARD PERIOD END")
    work_label, work_context = _horizon_work_context(h)
    relevance = _client_relevance(h)
    decision = _horizon_decision(h)
    decision_html = (
        f'<div class="sb-horizon-decision is-{decision.lower()}">'
        f'{_e(decision)}</div>' if decision else ""
    )
    source_timing = _horizon_source_timing(h)
    procurement = _procurement_facts(h)
    return (f'<article class="{cls}">'
            f'{decision_html}'
            f'<div class="sb-horizon-marks">{marks}</div>'
            f'<div class="sb-label">{_e(label)}</div>'
            f'{title}'
            f'<div class="sb-horizon-work"><b>{_e(work_label)}</b>'
            f'<span>{_e(work_context)}</span></div>'
            f'{relevance}'
            f'<strong>{_e(h.get("value"))}</strong>'
            f'<small>{_e(h.get("small"))}</small>'
            f'{source_timing}{procurement}</article>')


def _is_award_period_horizon(h: dict) -> bool:
    """Whether C1 explicitly emitted a dated active-award period end."""
    machine = h.get("machine_evidence")
    source_kind = (machine.get("source_kind")
                   if isinstance(machine, dict) else "")
    label = " ".join(str(h.get("label") or "").upper().split())
    small = " ".join(str(h.get("small") or "").upper().split())
    return (
        source_kind == "usaspending-award"
        and label.endswith("· AWARD WINDOW")
        and small == "ACTIVE AWARD · PERIOD OF PERFORMANCE END"
        and _parse_board_date(h.get("value")) is not None
    )


def _horizon_agency_display(h: dict) -> str:
    """Short, already-rendered agency identity for the Horizon guide."""
    label = str(h.get("label") or "").split("·", 1)[0].strip()
    return label or _horizon_agency(h)


def _horizon_copy(rows: list[dict]) -> tuple[str, str, str]:
    """Derive the forward-decision guide from the rendered cited records."""
    count = len(rows)
    award_period_count = sum(
        1 for row in rows if _is_award_period_horizon(row))
    award_periods = bool(rows) and award_period_count == count
    decisions = [_horizon_decision(row) for row in rows]
    decision_counts = {
        decision: decisions.count(decision)
        for decision in ("ATTACK", "DEFEND", "QUALIFY")
        if decisions.count(decision)
    }
    event_word = "EVENT" if count == 1 else "EVENTS"
    meta_parts = [f"{count} CITED FORWARD {event_word}"]
    meta_parts.extend(
        f"{decision_counts[decision]} {decision}"
        for decision in ("ATTACK", "DEFEND", "QUALIFY")
        if decision in decision_counts
    )
    meta_parts.append("NOT QUALIFIED PIPELINE")
    meta = " · ".join(meta_parts)
    context = (
        "Each card pairs a cited federal timing fact with the procurement "
        "fields stated by its source. A date is not automatically a "
        "solicitation deadline, and an event is not qualified pipeline."
    )
    posture_guide = (
        "ATTACK marks an entry route; DEFEND marks an evidenced client "
        "footprint; QUALIFY marks a signal that needs fit or status "
        "confirmation."
    )

    if not rows:
        return meta, context, posture_guide

    verb = "forms" if count == 1 else "form"
    lead = f"{count} cited forward {event_word.lower()} {verb} this decision queue."
    if award_period_count == count:
        composition = (
            "This event uses a cited active-award period end as its decision "
            "clock, not a separate solicitation record."
            if count == 1 else
            f"All {count} events use cited active-award period ends as "
            "decision clocks, not separate solicitation records."
        )
    elif award_period_count:
        other_count = count - award_period_count
        other_verb = "comes" if other_count == 1 else "come"
        composition = (
            f"{award_period_count} of {count} cited events use active-award "
            f"period ends; the remaining {other_count} {other_verb} from separate "
            "cited forecast, recompete, expiry, or program records."
        )
    else:
        composition = (
            "The queue is drawn from cited forecast, recompete, expiry, or "
            "program records rather than active-award period ends."
        )
    if not award_periods:
        return meta, context, f"{lead} {composition} {posture_guide}"

    dated = [(_parse_board_date(row.get("value")), row) for row in rows]
    dated.sort(key=lambda item: item[0])

    first_date, first_row = dated[0]
    last_date, _ = dated[-1]
    first_text = _fmt_board_date(first_date.date())
    agency = _horizon_agency_display(first_row)
    agency_clause = f", beginning with {agency}" if agency else ""
    if count == 1 or first_date.date() == last_date.date():
        sequence = f"The first source-stated event is {first_text}{agency_clause}."
    else:
        last_text = _fmt_board_date(last_date.date())
        sequence = (f"The source-stated sequence runs from {first_text} "
                    f"through {last_text}{agency_clause}.")
    return meta, context, f"{lead} {composition} {sequence} {posture_guide}"


def _person_initials(name: Any) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    return "".join(word[0].upper() for word in words[:2]) or "?"


def _portrait_attrs(
    person: dict,
    logo_sizes: Optional[dict[str, int]] = None,
) -> str:
    """Editable portrait identity already resolved against exact evidence."""
    from agents.reports.signal_board_presentation import (
        DEFAULT_LOGO_SIZE_PERCENT,
        validate_logo_size_percent,
    )

    key = str(person.get("portrait_identity") or "")
    claim = str(person.get("portrait_identity_label") or "")
    name = str(person.get("name") or "")
    if not key or not claim or not name:
        return ""
    if logo_sizes is not None and not isinstance(logo_sizes, dict):
        raise ValueError("logo_sizes must be an identity-to-percentage object")
    percent = validate_logo_size_percent(
        (logo_sizes or {}).get(key, DEFAULT_LOGO_SIZE_PERCENT))
    scale = f"{percent / 100:.2f}".rstrip("0").rstrip(".")
    return (
        f'data-brand-kind="person" data-brand-key="{_e(key)}" '
        f'data-brand-label="{_e(claim)}" data-brand-display="{_e(name)}" '
        f'data-brand-size="{percent}" style="--sb-brand-scale:{scale}"'
    )


def _portrait(person: dict, logo_sizes: Optional[dict[str, int]]) -> str:
    portrait = str(person.get("portrait") or "")
    has_portrait = " has-portrait" if portrait else ""
    image = (
        f'<img class="sb-person-portrait" src="{_e(portrait)}" '
        f'alt="Portrait of {_e(person.get("name"))}">'
        if portrait else
        '<img class="sb-person-portrait" alt="" aria-hidden="true">'
    )
    return (
        f'<div class="sb-person-portrait-target{has_portrait}" '
        f'{_portrait_attrs(person, logo_sizes)}>{image}'
        f'<span class="sb-person-initials" aria-hidden="true">'
        f'{_e(_person_initials(person.get("name")))}</span></div>'
    )


def _portrait_provenance(person: dict) -> str:
    label = str(person.get("portrait_provenance") or "PORTRAIT NOT SOURCED")
    source = person.get("portrait_source_url")
    if source:
        return (f'<span class="sb-person-portrait-source">'
                f'{_link(source, label)}</span>')
    return f'<span class="sb-person-portrait-source">{_e(label)}</span>'


def _poc_card(
    person: dict,
    logo_sizes: Optional[dict[str, int]],
) -> str:
    source_label = str(person.get("source_system") or "SAM.gov").upper()
    return (
        '<article class="sb-person-card sb-poc-card">'
        f'{_portrait(person, logo_sizes)}'
        '<div class="sb-person-copy">'
        '<div class="sb-label">SOLICITATION CONTACT</div>'
        f'<strong class="sb-poc-name">{_e(person.get("name"))}</strong>'
        f'<div class="sb-poc-role">{_e(person.get("role"))}</div>'
        f'<div class="sb-person-org">{_e(person.get("agency"))}</div>'
        f'<div class="sb-person-route">{_link(person.get("opp_url"), person.get("opp"))}</div>'
        f'<div class="sb-person-source">{source_label} · PUBLISHED CONTACT · '
        f'{_e(person.get("source_record_id"))}</div>'
        f'{_portrait_provenance(person)}'
        '<div class="sb-person-meta">'
        f'<span>{_e(person.get("deadline"))}</span>'
        f'<span>{_e(person.get("signal"))}</span></div>'
        '</div></article>'
    )


def _decision_maker_card(
    person: dict,
    logo_sizes: Optional[dict[str, int]],
) -> str:
    route_kind = str(person.get("route_kind") or "")
    route_identity = str(person.get("route_identity") or "")
    return (
        '<article class="sb-person-card sb-decision-maker-card" '
        f'data-route-kind="{_e(route_kind)}" '
        f'data-route-identity="{_e(route_identity)}">'
        f'{_portrait(person, logo_sizes)}'
        '<div class="sb-person-copy">'
        '<div class="sb-label">DECISION MAKER TO VALIDATE</div>'
        f'<strong class="sb-poc-name">{_e(person.get("name"))}</strong>'
        f'<div class="sb-poc-role">{_e(person.get("title"))}</div>'
        f'<div class="sb-person-org">{_e(person.get("organization"))}</div>'
        f'<div class="sb-person-route"><b>Displayed route</b> '
        f'{_e(person.get("route_label"))}</div>'
        f'<p class="sb-person-rationale">'
        f'{_e(person.get("relevance_rationale"))}</p>'
        f'<div class="sb-person-source">'
        f'{_link(person.get("bio_url"), "OFFICIAL LEADERSHIP SOURCE")} · RETRIEVED '
        f'{_e(str(person.get("retrieved_at") or "")[:10])}</div>'
        f'{_portrait_provenance(person)}'
        '</div></article>'
    )


def _people_band(
    pocs: list[dict],
    decision_makers: list[dict],
    logo_sizes: Optional[dict[str, int]],
) -> str:
    """Render sourced people while keeping the two roles visibly separate."""

    def group(title: str, meta: str, context: str, cards: str) -> str:
        return (
            '<section class="sb-person-group">'
            '<div class="sb-person-group-head">'
            f'<strong>{_e(title)}</strong><span>{_e(meta)}</span></div>'
            f'<p class="sb-person-group-context">{_e(context)}</p>'
            f'<div class="sb-person-grid">{cards}</div>'
            '</section>'
        )

    if pocs and decision_makers:
        title = "RELEVANT PEOPLE"
        meta = "TWO VERIFIED ROLE CLASSES · KEPT SEPARATE"
        context = (
            "Published notice contacts and route-bound public leadership "
            "records are separated below. A leadership card is a research "
            "lead; it is not proof that the person owns the procurement."
        )
        groups = (
            group(
                "PUBLISHED SOLICITATION CONTACTS",
                "CONTACTS AS LISTED IN SAM.GOV",
                "Names and roles are reproduced from the linked notice. "
                "Listing does not imply prior engagement or outreach priority.",
                "".join(_poc_card(row, logo_sizes) for row in pocs),
            )
            + group(
                "STRATEGIC LEADERSHIP OFFICES TO VALIDATE",
                "OFFICIAL PUBLIC LEADERSHIP RECORDS · NOT SOLICITATION POCS",
                "These records help map mission ownership. For acquisition "
                "outreach, validate the responsible program office and use "
                "a published SAM contact, contracting officer, or COR.",
                "".join(
                    _decision_maker_card(row, logo_sizes)
                    for row in decision_makers[:4]
                ),
            )
        )
    elif pocs:
        title = "PUBLISHED SOLICITATION CONTACTS"
        meta = "CONTACTS AS LISTED IN SAM.GOV"
        context = (
            "Names and roles are reproduced from the linked notice. Listing "
            "does not imply prior engagement or outreach priority."
        )
        groups = (
            '<div class="sb-person-grid">'
            + "".join(_poc_card(row, logo_sizes) for row in pocs)
            + '</div>'
        )
    elif decision_makers:
        title = "STRATEGIC LEADERSHIP OFFICES TO VALIDATE"
        meta = "OFFICIAL PUBLIC LEADERSHIP RECORDS · NOT SOLICITATION POCS"
        context = (
            "These official public leadership records help map mission "
            "ownership; they are not solicitation contacts. For acquisition "
            "outreach, validate the responsible program office and use a "
            "published SAM contact, contracting officer, or COR."
        )
        groups = (
            '<div class="sb-person-grid">'
            + "".join(
                _decision_maker_card(row, logo_sizes)
                for row in decision_makers[:4]
            )
            + '</div>'
        )
    else:
        title = "RELEVANT PEOPLE"
        meta = "NO VERIFIED PERSON RECORDS"
        context = (
            "No notice-level contact or official route-bound leadership "
            "record cleared the evidence bar for this refresh."
        )
        groups = (
            '<div class="sb-person-grid"><div class="sb-person-empty">'
            'No person was inferred from an organization name, title, or '
            'generic web result.</div></div>'
        )
    return (
        '<div class="sb-pocs-head">'
        f'<strong id="pocTitle">{_e(title)}</strong><span>{_e(meta)}</span>'
        '</div>'
        f'<div class="sb-pocs-context" id="pocContext">{_e(context)}</div>'
        f'{groups}'
    )


def _evidence(rec: dict) -> str:
    return _link(rec.get("url"), rec.get("label"))


def _source_coverage_summary(coverage: dict) -> str:
    counts = coverage.get("counts") or {}
    connected = sum(
        int(counts.get(key) or 0)
        for key in ("standard", "additional", "connected_not_used")
    )
    additional = int(counts.get("additional") or 0)
    extra = (f" · {additional} additional attempted "
             f"lane{'s' if additional != 1 else ''}"
             if additional else "")
    fallback = int(counts.get("fallback") or 0)
    fallback_text = (f" · {fallback} via official fallback"
                     if fallback else "")
    stale = int(counts.get("stale") or 0)
    stale_text = (f" · {stale} stale official snapshot"
                  f"{'s' if stale != 1 else ''}" if stale else "")
    partial = int(counts.get("partial") or 0)
    partial_text = (f" · {partial} partial return"
                    f"{'s' if partial != 1 else ''}" if partial else "")
    current_snapshot = int(counts.get("current_snapshot") or 0)
    snapshot_text = (f" · {current_snapshot} current-day official snapshot"
                     f"{'s' if current_snapshot != 1 else ''}"
                     if current_snapshot else "")
    scope_excluded = int(counts.get("scope_excluded") or 0)
    scope_text = (
        f" · {scope_excluded} excluded by engagement scope"
        if scope_excluded else ""
    )
    count_keys = (
        "standard", "additional", "attempted", "returned", "fallback",
        "partial", "current_snapshot", "stale", "failed", "not_run",
        "scope_excluded", "connected_not_used",
    )
    attrs = " ".join(
        f'data-{key.replace("_", "-")}="{_e(counts.get(key, 0))}"'
        for key in count_keys)
    return (
        '<span class="sb-source-client-only sb-source-client-summary">'
        f'<strong>{_e(connected)} connected research sources</strong>'
        '<small>Toggle the integration map</small></span>'
        '<span class="sb-source-operator-only">'
        f'<span class="sb-source-counts" {attrs}>'
        f'<strong>{_e(counts.get("standard", 0))} standard lanes</strong>'
        f'{_e(extra)} · {_e(counts.get("attempted", 0))} attempted · '
        f'{_e(counts.get("returned", 0))} returned{_e(fallback_text)}'
        f'{_e(partial_text)}{_e(snapshot_text)}{_e(stale_text)} · '
        f'{_e(counts.get("failed", 0))} rate-limited/failed · '
        f'{_e(counts.get("not_run", 0))} not run this refresh'
        f'{_e(scope_text)}'
        '</span></span>'
    )


def _source_coverage_rows(coverage: dict) -> str:
    rows = []
    for row in coverage.get("lanes") or []:
        status = str(row.get("status") or "")
        status_class = {
            "RETURNED": "returned",
            "RETURNED VIA OFFICIAL FALLBACK": "fallback",
            "PARTIAL RETURN": "partial",
            "PARTIAL VIA OFFICIAL FALLBACK": "partial",
            "CURRENT-DAY OFFICIAL SNAPSHOT": "snapshot",
            "CURRENT-DAY OFFICIAL FALLBACK SNAPSHOT": "snapshot",
            "STALE OFFICIAL SNAPSHOT": "stale",
            "RATE-LIMITED/FAILED": "failed",
            "NOT RUN THIS REFRESH": "not-run",
            "EXCLUDED BY ENGAGEMENT SCOPE": "scope-excluded",
        }.get(status, "unknown")
        scope = str(row.get("scope") or "additional")
        dimensions = " ".join(
            f'data-source-{name.replace("_", "-")}="{int(row.get(name) is True)}"'
            for name in ("fallback", "partial", "current_snapshot", "stale")
        )
        rows.append(
            f'<div class="sb-source-lane is-{_e(status_class)}" '
            f'role="listitem" data-source-scope="{_e(scope)}" '
            f'data-source-key="{_e(row.get("source"))}" '
            f'data-source-status="{_e(status)}" {dimensions}>'
            f'<span><b>{_e(row.get("label"))}</b>'
            f'<small class="sb-source-client-only">'
            f'{_e(row.get("source"))} · integrated research source</small>'
            f'<small class="sb-source-operator-only">{_e(row.get("source"))}'
            f'{" · " + _e(row.get("detail")) if row.get("detail") else ""}'
            f'</small></span>'
            '<strong><span class="sb-source-client-only">CONNECTED</span>'
            f'<span class="sb-source-operator-only">{_e(status)}</span>'
            '</strong></div>'
        )
    return "".join(rows)


def _connected_capabilities(coverage: dict) -> str:
    rows = []
    for row in coverage.get("connected_not_used") or []:
        rows.append(
            f'<div class="sb-connected-capability" role="listitem" '
            f'data-source-key="{_e(row.get("source"))}" '
            f'data-source-status="{_e(row.get("status"))}">'
            f'<div><b>{_e(row.get("label"))}</b>'
            f'<small class="sb-source-client-only">{_e(row.get("source"))}'
            ' · integrated research capability</small>'
            '<small class="sb-source-operator-only">'
            f'{_e(row.get("detail"))}</small></div>'
            '<strong><span class="sb-source-client-only">CONNECTED</span>'
            '<span class="sb-source-operator-only">'
            f'{_e(row.get("status"))}</span></strong></div>'
        )
    return "".join(rows)


def _news_set(news: list, dup: bool) -> str:
    cls = "sb-news-set sb-news-dup" if dup else "sb-news-set"
    extra = ' aria-hidden="true"' if dup else ""
    items = []
    for n in news:
        head = f'<b>{_e(n.get("head"))}</b>{_e(n.get("body"))}'
        if dup:
            items.append(f'<span class="sb-news-item">{head}</span>')
        else:
            items.append(f'<a class="sb-news-item" {_anchor_attrs(n.get("url"))}>'
                         f'{head}</a>')
    return f'<div class="{cls}"{extra}>{"".join(items)}</div>'


def _scale_work(scale: Optional[dict]) -> str:
    """P1-4/P2-6: deterministic structured builder for the expandable proof.

    `scale` (when present) is {'rows': [{label,url,naics,amount}], 'total',
    'exact'}. Never accepts raw HTML. When absent, the panel is omitted (the
    <details> still renders, empty), never faked.
    """
    if not scale or not scale.get("rows"):
        return ""
    def line(row: dict) -> str:
        naics = str(row.get("naics") or "").strip()
        detail = f'<small>NAICS {_e(naics)}</small>' if naics else ""
        return (
            f'<div class="sb-scale-line">'
            f'<span class="sb-scale-op"><span>+</span></span>'
            f'<div>{_link(row.get("url"), row.get("label"))}{detail}</div>'
            f'<span class="sb-scale-amount">{_e(row.get("amount"))}</span>'
            f'</div>'
        )

    lines = "".join(line(row) for row in scale["rows"])
    total = (f'<div class="sb-scale-line total"><span class="sb-scale-op">'
             f'<span>=</span></span><span>Exact total</span>'
             f'<span class="sb-scale-amount">{_e(scale.get("total"))}</span></div>')
    return (f'<div class="sb-scale-section"><h3>How the total was built</h3>'
            f'{lines}{total}</div>')


def _load_template() -> str:
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _fmt_board_date(d) -> str:
    return f"{d.day:02d} {d.strftime('%b').upper()} {d.year}"


def _parse_board_date(raw: Any):
    """The board's display date format ('15 JUL 2026') back to a datetime."""
    from datetime import datetime
    try:
        return datetime.strptime(str(raw).strip().title(), "%d %b %Y")
    except (ValueError, TypeError):
        return None


def _figure_retrieval_dates(figures: Optional[list]) -> Optional[list]:
    """Per-figure retrieval times from provenance rows (Fact objects or
    dicts). None when any figure lacks one: no honest minimum exists."""
    from agents.reports.facts import _parse_retrieved
    if not figures:
        return None
    times = []
    for f in figures:
        raw = (f.get("retrieved_at") if isinstance(f, dict)
               else getattr(f, "retrieved_at", None))
        parsed = _parse_retrieved(raw)
        if parsed is None:
            return None
        times.append(parsed)
    return times


def _data_current_clause(figures: Optional[list]) -> str:
    """The evidence dock's data-current line, COMPUTED from min(retrieved_at)
    across the rendered figures' provenance rows; never hand-set. With no
    provenance, or any unknown-freshness figure, the clause is absent and
    lint_signal_board fails the render (fail closed, no invented currency)."""
    times = _figure_retrieval_dates(figures)
    if times is None:
        return ""
    return f" · data current {_fmt_board_date(min(times).date())}"


def render_signal_board(model: dict, *, template: Optional[str] = None) -> str:
    """Fill the locked template from a model dict. Deterministic; no LLM."""
    from agents.reports.signal_board_presentation import (
        DEFAULT_HEADER_COMPANION_TEXT,
        validate_header_companion_text,
    )
    from agents.reports.source_coverage import coverage_from_attempts

    tpl = template if template is not None else _load_template()
    news = model.get("news", [])
    teaming = model.get("teaming", [])
    acquisition_pathways = model.get("acquisition_pathways", [])
    horizon = model.get("horizon", [])
    logo_sizes = model.get("logo_sizes", {})
    source_coverage = model.get("source_coverage")
    if not isinstance(source_coverage, dict):
        source_coverage = coverage_from_attempts([])
    horizon_meta, horizon_context, horizon_teaser = _horizon_copy(horizon)
    (section_03_nav, section_03_title, section_03_meta,
     section_03_context, section_03_grid_class) = _section_03_copy(
        teaming, acquisition_pathways)
    section_03_body = (
        "".join(_team(t, model.get("client_name"), logo_sizes)
                for t in teaming)
        if teaming else
        "".join(_acquisition_pathway(row, logo_sizes)
                for row in acquisition_pathways)
    )
    header_companion = validate_header_companion_text(
        model.get(
            "header_companion_text", DEFAULT_HEADER_COMPANION_TEXT))
    slots = {
        "CLIENT_NAME": _e(model.get("client_name")),
        "CLIENT_LOGO": _e(model.get("client_logo", "")),
        "CLIENT_BRAND_ATTRS": _brand_attrs(
            "client", model.get("client_name"), logo_sizes),
        "CLIENT_BRAND_SUB": _e(header_companion),
        "REPORT_DATE": _e(model.get("report_date")),
        "HERO_CONTEXT": _e(model.get("hero_context")),
        "SCALE_LABEL": _e(
            model.get("scale_label") or "Cited obligated history"),
        "SCALE_TOTAL": _e(model.get("scale_total")),
        "SCALE_COUNTS": _e(model.get("scale_counts")),
        "SCALE_WORK": _scale_work(model.get("scale")),
        "CHIPS": "".join(_chip(c) for c in model.get("chips", [])),
        "NEWS": _news_set(news, dup=False) + _news_set(news, dup=True),
        "SIGNAL_CARDS": "".join(_signal(s) for s in model.get("signal_cards", [])),
        "COVERAGE": "".join(
            _coverage(m, logo_sizes) for m in model.get("coverage", [])),
        "BEST_FIT": "".join(
            _opp(o, logo_sizes) for o in model.get("best_fit", [])),
        "COMPETITORS": "".join(
            _competitor(c, logo_sizes)
            for c in model.get("competitors", [])),
        "SECTION_03_NAV": _e(section_03_nav),
        "SECTION_03_TITLE": _e(section_03_title),
        "SECTION_03_META": _e(section_03_meta),
        "SECTION_03_CONTEXT": _e(section_03_context),
        "SECTION_03_GRID_CLASS": _e(section_03_grid_class),
        "SECTION_03_BODY": section_03_body,
        "HORIZON_META": _e(horizon_meta),
        "HORIZON_CONTEXT": _e(horizon_context),
        "HORIZON_TEASER": _e(horizon_teaser),
        "HORIZON": "".join(_horizon(h, logo_sizes) for h in horizon),
        "POCS": _people_band(
            list(model.get("pocs", [])),
            list(model.get("decision_makers", [])),
            logo_sizes,
        ),
        "SOURCE_COVERAGE_SUMMARY": _source_coverage_summary(source_coverage),
        "SOURCE_COVERAGE": _source_coverage_rows(source_coverage),
        "CONNECTED_CAPABILITIES": _connected_capabilities(source_coverage),
        "EVIDENCE": "".join(_evidence(r) for r in model.get("evidence", [])),
        "SEQUENCE": _e(model.get("sequence")),
        # computed ONLY from figure provenance; a hand-set model string has
        # no slot to land in (per-figure freshness, 2026-07-16)
        "DATA_CURRENT": _data_current_clause(model.get("figures")),
    }
    for tok, val in slots.items():
        tpl = tpl.replace("{{" + tok + "}}", val)
    return tpl


# ── conformance lint: the format cannot drift, and content cannot be empty ──

_REQUIRED_BANDS = (
    "sb-hero", "sb-agency-rail", "sb-signal-strip", "sb-opps",
    "sb-competitor-grid", "sb-route-grid", "sb-horizon",
    'id="research-sources"', "sb-source-dock", "sb-footer",
)


def lint_signal_board(html_text: str) -> tuple[bool, list[str]]:
    """Enforce the locked format AND that data actually filled it (P1-5).

    Beyond structure, this rejects an empty render: the record-bearing bands
    must contain their article records, there must be enough signal cards and
    opportunity cells, and reinforcing links must be present.
    """
    v: list[str] = []

    leftover = sorted(set(re.findall(r"\{\{(\w+)\}\}", html_text)))
    if leftover:
        v.append(f"unfilled template tokens: {leftover}")

    body_start = html_text.find("</style>")
    body = html_text[body_start:] if body_start != -1 else html_text
    last = -1
    for band in _REQUIRED_BANDS:
        idx = body.find(band)
        if idx == -1:
            v.append(f"required band missing: {band}")
        elif idx < last:
            v.append(f"band out of locked order: {band}")
        else:
            last = idx

    # section-local record floors: an empty model must NOT pass
    counts = {
        "opportunities": body.count('class="sb-opp"'),
        "competitors": len(re.findall(
            r'class="sb-competitor(?:\s[^\"]*)?"', body)),
        "candidate teaming opportunities": body.count(
            'class="sb-team-card"'),
        "federal acquisition pathways": body.count(
            'class="sb-acquisition-card"'),
        "horizon records": body.count('class="sb-horizon-card'),
        "signal cards": body.count('class="sb-signal"'),
    }
    section_03_count = (
        counts["candidate teaming opportunities"]
        + counts["federal acquisition pathways"])
    for name, n in counts.items():
        if name in {
                "candidate teaming opportunities",
                "federal acquisition pathways"}:
            continue
        if n == 0:
            v.append(f"no {name} rendered (empty band)")
    if section_03_count == 0:
        v.append("no Section 03 route research rendered (empty band)")
    if (counts["candidate teaming opportunities"]
            and counts["federal acquisition pathways"]):
        v.append("Section 03 mixes teaming and acquisition pathways")
    if 0 < counts["signal cards"] < 4:
        v.append(f"signal strip needs 4 cards, has {counts['signal cards']}")
    if counts["opportunities"] and body.count('class="sb-cell"') < 4 * counts["opportunities"]:
        v.append("opportunity cards are missing their four evidence cells")

    from agents.reports.source_coverage import (
        CONNECTED_NOT_USED, NOT_USED, PUBLIC_LANE_STATUSES,
        STANDARD_SWEEP_LANES, counts_from_lanes,
    )
    raw_source_matches = re.findall(
        r'<div class="sb-source-lane[^"]*" role="listitem" '
        r'data-source-scope="([^"]+)" data-source-key="([^"]+)" '
        r'data-source-status="([^"]+)" data-source-fallback="([01])" '
        r'data-source-partial="([01])" '
        r'data-source-current-snapshot="([01])" '
        r'data-source-stale="([01])">',
        body,
    )
    source_matches = [{
        "scope": scope,
        "source": key,
        "status": status,
        "fallback": fallback == "1",
        "partial": partial == "1",
        "current_snapshot": current_snapshot == "1",
        "stale": stale == "1",
    } for (
        scope, key, status, fallback, partial, current_snapshot, stale
    ) in raw_source_matches]
    standard_rows = [
        row for row in source_matches if row["scope"] == "standard"
    ]
    standard_source_rows = len(standard_rows)
    expected_source_keys = [source for source, _label in STANDARD_SWEEP_LANES]
    actual_source_keys = [row["source"] for row in standard_rows]
    if standard_source_rows != len(expected_source_keys):
        v.append(
            "research source coverage must render all "
            f"{len(expected_source_keys)} standard lanes, has "
            f"{standard_source_rows}"
        )
    elif actual_source_keys != expected_source_keys:
        v.append(
            "research source coverage standard lane keys/order differ from "
            "the sanctioned sweep catalog")
    bad_scopes = sorted({
        row["scope"] for row in source_matches
        if row["scope"] not in {"standard", "additional"}
    })
    if bad_scopes:
        v.append(f"research source coverage has invalid scopes: {bad_scopes}")
    bad_statuses = sorted({
        row["status"] for row in source_matches
        if row["status"] not in PUBLIC_LANE_STATUSES
    })
    if bad_statuses:
        v.append(
            f"research source coverage has invalid statuses: {bad_statuses}")

    connected_matches = re.findall(
        r'<div class="sb-connected-capability" role="listitem" '
        r'data-source-key="([^"]+)" data-source-status="([^"]+)">',
        body,
    )
    expected_connected = [source for source, _label, _detail
                          in CONNECTED_NOT_USED]
    actual_connected = [source for source, _status in connected_matches]
    if actual_connected != expected_connected:
        v.append(
            "research source coverage must disclose connected capabilities "
            "not used this refresh in sanctioned order"
        )
    if any(status != NOT_USED for _source, status in connected_matches):
        v.append("connected capability coverage has an invalid status")

    count_names = (
        "standard", "additional", "attempted", "returned", "fallback",
        "partial", "current-snapshot", "stale", "failed", "not-run",
        "scope-excluded", "connected-not-used",
    )
    count_pattern = (
        r'<span class="sb-source-counts" '
        + " ".join(fr'data-{name}="(\d+)"' for name in count_names)
        + r'>'
    )
    count_match = re.search(count_pattern, body)
    if count_match is None:
        v.append("research source coverage count manifest is missing")
    else:
        reported = dict(zip(
            (name.replace("-", "_") for name in count_names),
            (int(value) for value in count_match.groups()),
        ))
        reconciled = counts_from_lanes(source_matches)
        if reported != reconciled:
            v.append(
                "research source coverage counts do not reconcile with "
                "rendered lane statuses")

    low = html_text.lower()
    if "no affiliation or endorsement" not in low:
        v.append("agency-marks identification-only disclaimer missing")
    if "not pipeline" not in low:
        v.append("marquee scale is missing its honest boundary qualifier")
    if "not opportunity value" not in low:
        v.append(
            "marquee obligation is missing its opportunity-value boundary")
    if body.count("<a ") < counts["opportunities"] + 1:
        v.append("too few linked records: reinforcing source links are absent")
    # per-figure freshness (2026-07-16): the evidence dock's data-current
    # line exists ONLY when computed from figure provenance; its absence
    # means the render could not prove currency and must not ship
    if "data current" not in low:
        v.append("data-current line is not computed from figure provenance")

    # Authority + motion are part of the locked client-ready format, not
    # optional decoration.  The header must carry a self-contained client
    # mark, and the award ticker must remain duplicated, animated, pausable,
    # and respectful of reduced-motion preferences.
    client_mark = re.search(
        r'<div class="sb-brand has-image"[^>]*><img\b[^>]*\bsrc="([^"]*)"',
        body,
    )
    mark_src = _html.unescape(client_mark.group(1)) if client_mark else ""
    if not re.fullmatch(
            r"data:image/(?:png|svg\+xml|webp|jpe?g);base64,[A-Za-z0-9+/=]+",
            mark_src):
        v.append("client header logo is missing a self-contained image")

    ticker_contract = {
        'class="sb-news-ticker"': "award ticker container missing",
        'aria-label="Active federal award signals"':
            "award ticker accessible label missing",
        "Active award signals · hover to pause":
            "award ticker visible label missing",
        "animation: sb-news-scroll": "award ticker animation missing",
        "@keyframes sb-news-scroll": "award ticker keyframes missing",
        "animation-play-state: paused": "award ticker pause control missing",
        "@media (prefers-reduced-motion: reduce)":
            "award ticker reduced-motion fallback missing",
        ".sb-news-dup { display: none; }":
            "award ticker reduced-motion duplicate rule missing",
    }
    for needle, detail in ticker_contract.items():
        if needle not in html_text:
            v.append(detail)
    if body.count('class="sb-news-set') < 2 or "sb-news-dup" not in body:
        v.append("award ticker duplicated tape missing")
    if body.count('class="sb-news-item"') < 2:
        v.append("award ticker has no duplicated award signal")

    return (not v, v)


# ── adapter: one scoped AssessmentDocument -> model (Codex canonical mapping) ──

def _agency_code(agency: Optional[str]) -> str:
    from tools import brand_marks as _bm
    if not agency:
        return "FED"
    return (_bm.recognize_agency_key(str(agency)) or str(agency).split()[0])[:6].upper()


def _check_data_currency(figures: Optional[list], report_date: str) -> None:
    """Pre-render hard fail: the computed data-current date may not diverge
    from the document date by more than the freshness threshold."""
    from agents.reports.verification import FRESHNESS_MAX_AGE_DAYS

    times = _figure_retrieval_dates(figures)
    if times is None:
        return
    from agents.reports.facts import _parse_retrieved
    doc_dt = _parse_retrieved(report_date) or _parse_board_date(report_date)
    if doc_dt is None:
        raise ValueError(
            f"cannot verify data currency: document date "
            f"{report_date!r} is unparseable while figure provenance "
            f"is supplied")
    divergence = (doc_dt.date() - min(times).date()).days
    if divergence > FRESHNESS_MAX_AGE_DAYS:
        raise ValueError(
            f"document is dated {doc_dt.date().isoformat()} but its "
            f"oldest figure was retrieved {min(times).date().isoformat()}"
            f", {divergence} days earlier (limit {FRESHNESS_MAX_AGE_DAYS}"
            f"); re-pull the stale figures before render")


def build_model(doc: Any, *, report_date: str,
                figures: Optional[list] = None,
                content: Any = None,
                recompete: Any = None,
                federal_link_status: Optional[
                    dict[tuple[str, str], bool]
                ] = None) -> dict:
    """Map ONE scoped AssessmentDocument into the render model.

    Consumes only Assess-stage data (no reverse dependency on the Target-stage
    contact plan). Bands whose source fields are not yet exposed by the pipeline
    (verified SAM POCs, structured award scale) come through empty; the lint
    flags the gap rather than the adapter inventing signal.

    `figures` (additive, 2026-07-16) is the provenance row set for every
    figure this board renders: the FactPack facts backing the document. The
    evidence dock's data-current line computes from their min(retrieved_at);
    a computed date diverging from the document date by more than
    FRESHNESS_MAX_AGE_DAYS fails HERE, before any render happens.

    `content` (additive, press-run reproducibility) is the operator-approved
    SignalBoardContent artifact: its bands OVERRIDE the derived ones so the
    same stored inputs reproduce the same board, and its figure registry
    supplies provenance when `figures` is not passed. Content is copy, never
    evidence; reconciliation against the stored sweep is the caller's gate
    (agents/reports/board_content.py)."""
    from agents.reports import report_assets as _ra
    from agents.reports.signal_board_presentation import (
        resolve_header_companion_text,
        resolve_logo_sizes,
    )
    from agents.reports.source_coverage import coverage_from_attempts

    source_coverage = getattr(doc, "research_source_coverage", None)
    if not isinstance(source_coverage, dict):
        source_coverage = coverage_from_attempts([])

    if content is not None and figures is None:
        from agents.reports.board_content import figure_provenance_rows
        figures = figure_provenance_rows(content)
    # data currency is checked after every enrichment (recompete events join
    # the figure rows too), just before the model returns

    board = getattr(doc, "board", None)
    pursuits = list(getattr(board, "pursuits", []) or [])
    competitive = getattr(doc, "competitive", None)
    competitors = list(getattr(competitive, "competitors", []) or [])
    partnering = getattr(doc, "partnering", None)
    plays = list(getattr(partnering, "plays", []) or [])
    watchlist = getattr(doc, "watchlist", None)
    entries = list(getattr(watchlist, "entries", []) or [])
    market = getattr(doc, "market", None)
    news_src = list(getattr(market, "news", []) or [])
    client_identity = getattr(doc, "client_name", "")
    from tools.capability import client_display_name
    display_name = client_display_name(client_identity)

    def _scoped_recompete_inputs(calendar, forecasts):
        """Apply the client's engagement boundary before successor joins."""
        from tools.relevance.scope import in_scope, load_engagement_scope

        scope = load_engagement_scope(client_identity)
        if scope is None:
            return calendar, forecasts, 0

        def allowed(row) -> bool:
            if not isinstance(row, dict):
                return False
            inside, basis = in_scope(row, scope)
            return bool(inside and "unresolved" not in basis)

        scoped_calendar = None
        excluded = 0
        if isinstance(calendar, dict):
            scoped_calendar = dict(calendar)
            for lane in ("attack", "defend"):
                source_rows = list(calendar.get(lane) or [])
                scoped_rows = [row for row in source_rows if allowed(row)]
                excluded += len(source_rows) - len(scoped_rows)
                scoped_calendar[lane] = scoped_rows
        source_forecasts = list(forecasts or [])
        scoped_forecasts = [row for row in source_forecasts if allowed(row)]
        excluded += len(source_forecasts) - len(scoped_forecasts)
        return scoped_calendar, scoped_forecasts, excluded

    def _resolve_horizon_row(source: dict) -> dict:
        row = dict(source)
        resolved = []
        for mark in _horizon_marks(row):
            kind, label = mark["kind"], mark["label"]
            if kind == "agency":
                logo = _ra.agency_seal(label, client_identity)
            elif kind == "company":
                logo = _ra.company_logo(label, client_identity)
            else:
                logo = _ra.client_logo(client_identity)
            resolved.append(dict(mark, logo=logo))
        row["organization_marks"] = resolved
        row.pop("seal", None)
        return row

    best_fit, agencies = [], []
    for i, p in enumerate(pursuits, 1):
        agency = getattr(p, "agency", None)
        agencies.append(agency)
        best_fit.append({
            "rank": f"{i:02d}", "code": _agency_code(agency), "agency_name": agency,
            "seal": _ra.agency_seal(agency, client_identity),
            "title": getattr(p, "title", ""), "url": getattr(p, "url", ""),
            "account": (getattr(p, "notice_type", "") or "").upper(),
            "evidence": [{"url": getattr(p, "url", ""),
                          "label": getattr(p, "solicitation", None) or getattr(p, "source_id", "")}],
            "cells": [
                {"label": "Window", "value": getattr(p, "response_deadline", None) or "See notice",
                 "small": getattr(p, "notice_type", "") or ""},
                {"label": "Fit", "value": getattr(getattr(p, "grade", None), "label", "") or "Graded",
                 "small": getattr(p, "naics", "") or ""},
                {"label": "Set-aside", "value": getattr(p, "set_aside", None) or "Open", "small": ""},
                {"label": "Route", "value": "Pursue", "small": getattr(p, "solicitation", "") or ""},
            ],
        })

    comp = [{"label": (getattr(c, "name", "") or "").upper()[:22],
             "title": getattr(c, "name", ""),
             "logo": _ra.company_logo(
                 getattr(c, "name", ""), client_identity),
             "url": "#", "money": getattr(c, "dollars_label", "") or "",
             "small": "DIRECT AWARD RECORD",
             "wedge": "Product" if getattr(c, "product", False) else "Lane"}
            for c in competitors]

    teaming = []
    acquisition_pathways = []
    for play in plays:
        if getattr(play, "kind", "pursue") != "pursue":
            continue
        for cand in list(getattr(play, "candidates", []) or []):
            teaming.append({
                "client": (display_name or "").upper(),
                "partner": (getattr(cand, "company", "") or "").upper(),
                "client_logo": _ra.client_logo(client_identity),
                # partner marks resolve through the client's curated assets
                # first (clients/<slug>/assets/partners/), text fallback
                "partner_logo": _ra.partner_logo(client_identity,
                                                 getattr(cand, "company", "")),
                "target": getattr(play, "title", "") or "",
                "angle": ", ".join(getattr(cand, "agencies", []) or [])[:80],
                "proof": "Evidence-graded partnering candidate",
            })

    horizon = []
    for entry in entries:
        label = (getattr(entry, "source", "") or "").upper()
        horizon.append({
            "hot": getattr(entry, "kind", "") in ("new", "changed"),
            "label": label,
            "title": getattr(entry, "title", "") or "",
            "url": getattr(entry, "url", "") or "",
            "value": "",
            "small": (getattr(entry, "detail", "") or "")[:80],
            "organization_marks": [],
        })

    news = [{"head": (n.get("recency") or "").upper(), "body": n.get("title") or "",
             "url": n.get("url") or ""} for n in news_src]

    evidence = [{"url": o["url"], "label": o["evidence"][0]["label"]}
                for o in best_fit if o.get("url")]

    coverage_seen, coverage = set(), []
    for a in agencies:
        code = _agency_code(a)
        if a and code not in coverage_seen:
            coverage_seen.add(code)
            coverage.append({
                "dept": code,
                "components": a,
                "seal": _ra.agency_seal(a, client_identity),
            })

    try:
        counts = doc.counts()
    except Exception:  # noqa: BLE001 — counts is best-effort for the signal strip
        counts = {}
    signal_cards = [
        {"label": "Priority routes", "value": str(len(best_fit)),
         "sub": "Graded pursuits", "detail": "Current federal records"},
        {"label": "Coverage", "value": str(counts.get("agencies", len(coverage))),
         "sub": "Agencies represented", "detail": "Source agencies"},
        {"label": "Competitor lanes", "value": str(len(comp)),
         "sub": "Direct award records", "detail": "Not market share"},
        {"label": "Forward timing signals", "value": str(len(horizon)),
         "sub": "Cited events", "detail": "Capture postures"},
    ]

    # operator-approved content overlay (press-run reproducibility): content
    # bands replace derived ones verbatim; seals and logos still resolve at
    # render from committed caches, never from the content file
    hero_context = f"Screened primary federal records for {display_name}."
    scale_label = "Cited obligated history"
    scale_total, scale_counts, scale, chips = "", "", None, []
    pocs, decision_makers, sequence = [], [], ""
    machine_content = False
    if content is not None:
        # C1 machine content is the complete, screened publication surface.
        # An empty machine band is an intentional rejection result, never a
        # request to resurrect the assessment document's derived rows.
        machine_content = (
            getattr(content, "composition_mode", "operator") == "machine"
        )
        hero_context = content.hero_context or hero_context
        if client_identity and display_name != client_identity:
            hero_context = re.sub(
                rf"(?<!\w){re.escape(client_identity)}(?!\w)",
                display_name,
                hero_context,
                flags=re.IGNORECASE,
            )
        chips = list(content.chips)
        scale_label = (
            getattr(content, "scale_label", "")
            or "Cited obligated history")
        scale_total, scale_counts = content.scale_total, content.scale_counts
        scale = content.scale
        pocs = list(content.pocs)
        decision_makers = [
            (row.model_dump(mode="json")
             if hasattr(row, "model_dump") else dict(row))
            for row in getattr(content, "decision_makers", [])
        ]
        sequence = content.sequence
        if machine_content or content.news:
            news = list(content.news)
        if machine_content or content.signal_cards:
            signal_cards = list(content.signal_cards)
        if machine_content or content.coverage:
            coverage = [dict(m, seal=_ra.agency_seal(
                m.get("components") or m.get("dept"), client_identity))
                for m in content.coverage]
        if machine_content or content.best_fit:
            best_fit = [dict(o, seal=_ra.agency_seal(
                o.get("agency_name") or o.get("code"), client_identity))
                for o in content.best_fit]
        if machine_content or content.competitors:
            comp = [dict(c, logo=_ra.company_logo(
                c.get("title") or c.get("label"), client_identity))
                for c in content.competitors]
        if machine_content or content.teaming:
            teaming = [dict(t,
                            client_logo=_ra.client_logo(client_identity),
                            partner_logo=_ra.partner_logo(client_identity,
                                                          t.get("partner")))
                       for t in content.teaming]
        if machine_content or getattr(content, "acquisition_pathways", []):
            acquisition_pathways = [dict(
                row,
                code=_agency_code(row.get("agency_name") or ""),
                seal=_ra.agency_seal(
                    row.get("agency_name") or "", client_identity),
            ) for row in getattr(content, "acquisition_pathways", [])]
        if machine_content or content.horizon:
            horizon = [_resolve_horizon_row(h) for h in content.horizon]
        if machine_content or content.evidence:
            evidence = list(content.evidence)

    # missing-mark notes ride the model for the INTERNAL sidecar only; the
    # client render already falls back to the text code, never a broken image
    internal_notes = []
    noted = set()
    for o in best_fit + coverage + acquisition_pathways:
        label = o.get("agency_name") or o.get("components") or o.get("dept")
        if label and not o.get("seal") and label not in noted:
            noted.add(label)
            internal_notes.append(
                f"no seal asset for agency '{label}'; rendered the text code "
                f"(drop a mark in data/reference/seals/ to embed it)")
    for row in horizon:
        for mark in _horizon_marks(row):
            identity = (mark["kind"], mark["label"])
            if mark.get("logo") or identity in noted:
                continue
            noted.add(identity)
            internal_notes.append(
                f"no mark asset for horizon {mark['kind']} "
                f"'{mark['label']}'; rendered the identity text")

    # Faces are a presentation enrichment over exact person/source claims.
    # Resolution is offline; invalid/missing assets become editable initials
    # placeholders and ride the internal notes rather than guessing.
    from agents.reports.person_portraits import resolve_portrait

    def _resolve_person_rows(rows: list[dict]) -> list[dict]:
        resolved_rows = []
        for source in rows:
            row = dict(source)
            resolution = resolve_portrait(client_identity, row)
            row.update({
                "portrait": resolution.data_uri,
                "portrait_identity_label": resolution.identity_label,
                "portrait_identity": resolution.identity,
                "portrait_source_url": resolution.portrait_source_url,
                "portrait_provenance": resolution.provenance,
            })
            if resolution.issue:
                internal_notes.append(
                    f"portrait placeholder for '{row.get('name')}': "
                    f"{resolution.issue}")
            resolved_rows.append(row)
        return resolved_rows

    pocs = _resolve_person_rows(pocs)
    decision_makers = _resolve_person_rows(decision_makers)

    # recompete-aware theses (2026-07-17): every teaming play joins the
    # recompete calendar and forecast lanes for its successor event. KNOWN
    # and NOT FOUND render on the card; CALENDAR GAP is an INTERNAL note
    # only. A KNOWN event's provenance joins the figure rows so the
    # data-current line and freshness gate account for it, no special cases.
    if recompete is not None and teaming:
        from dataclasses import asdict

        from agents.reports.recompete_context import (
            PlayKey, successor_events, within_horizon,
        )
        from agents.reports.facts import _parse_retrieved
        calendar = (recompete or {}).get("calendar")
        forecasts = (recompete or {}).get("forecasts") or []
        calendar, forecasts, excluded = _scoped_recompete_inputs(
            calendar, forecasts)
        if excluded:
            internal_notes.append(
                f"engagement scope excluded {excluded} recompete or "
                "forecast row(s) before teaming successor matching")
        render_dt = (_parse_retrieved(report_date)
                     or _parse_board_date(report_date))
        enriched, joined_events = [], set()
        for row in teaming:
            key = PlayKey(
                award_id=str(row.get("award_id") or ""),
                idv_piid=str(row.get("idv_piid") or ""),
                program_name=str(row.get("program")
                                 or row.get("target") or ""),
                agency=str(row.get("agency") or row.get("angle") or ""))
            ctx = successor_events(key, calendar, forecasts)
            row = dict(row)
            if ctx.state == "gap":
                internal_notes.append(
                    f"recompete calendar gap for teaming play "
                    f"'{row.get('target')}': {ctx.reason} (the card renders "
                    f"no recompete line; run the recompete step to close)")
            else:
                event_payload = asdict(ctx.event) if ctx.event else None
                if event_payload is not None:
                    generated_id = event_payload.pop(
                        "source_generated_id", None)
                    if (event_payload.get("source_system") == "usaspending"
                            and generated_id):
                        event_payload["award_generated_id"] = generated_id
                row["recompete"] = {
                    "state": ctx.state, "matched_by": ctx.matched_by,
                    "event": event_payload,
                }
                # The separately rendered event line carries any sourced
                # recompete label/date/identity.  It never mutates the
                # evidence-derived candidate angle with a generic strategy
                # clause.
                if ctx.event is not None:
                    if figures is None:
                        figures = []
                    figures = list(figures) + [
                        {"retrieved_at": ctx.event.retrieved_at}]
                    # horizon join: record-backed events inside the window,
                    # with a linkable primary record or APFS forecast id,
                    # join the horizon band under existing evidence
                    # discipline; analyst-tier and linkless events never do
                    ev = ctx.event
                    ev_id = ev.source_record_id or ""
                    link_identity = (ev.source_generated_id
                                     if ev.source_system == "usaspending"
                                     else ev.source_url)
                    horizon_kind = {
                        "bridge": "BRIDGE",
                        "follow_on": "FOLLOW-ON",
                        "forecast": "FORECAST",
                        "recompete": "RECOMPETE",
                        "expiry": "EXPIRY",
                    }.get(ev.kind)
                    if (not machine_content
                            and ev.tier == "record" and link_identity and ev_id
                            and horizon_kind is not None
                            and render_dt is not None
                            and within_horizon(ev.date, render_dt.date())
                            and ev_id not in joined_events):
                        joined_events.add(ev_id)
                        if ev.kind == "expiry":
                            event_detail = (
                                f"{ev_id} · AWARD PERIOD END · "
                                "NOT CONFIRMED RECOMPETE")
                        elif ev.kind == "forecast":
                            event_detail = (
                                f"{ev_id} · FORECAST SUCCESSOR SIGNAL")
                        else:
                            event_detail = (
                                f"{ev_id} · SUCCEEDS "
                                f"{row.get('target') or ''}")
                        horizon_row = {
                            "hot": True,
                            "label": (f"{_agency_code(row.get('agency'))} · "
                                      f"{horizon_kind}"),
                            "title": ev.label,
                            "value": ev.date or "FORECAST",
                            "small": event_detail[:80],
                            "agency_name": row.get("agency") or "",
                            "organization_marks": [{
                                "kind": "agency",
                                "label": row.get("agency") or "",
                                "display": _agency_code(row.get("agency")),
                            }],
                        }
                        if ev.source_system == "usaspending":
                            horizon_row["award_generated_id"] = (
                                ev.source_generated_id)
                        else:
                            horizon_row["url"] = ev.source_url
                        horizon = list(horizon) + [
                            _resolve_horizon_row(horizon_row)]
            enriched.append(row)
        teaming = enriched

    # Gate order after the main-first merge: enrich the model from recompete
    # records, then materialize every structured federal identity, then run
    # the existing data-currency gate over the complete figure set.
    scale = _materialize_federal_links(scale, federal_link_status)
    news = _materialize_federal_links(news, federal_link_status)
    best_fit = _materialize_federal_links(best_fit, federal_link_status)
    comp = _materialize_federal_links(comp, federal_link_status)
    acquisition_pathways = _materialize_federal_links(
        acquisition_pathways, federal_link_status)
    teaming = _materialize_federal_links(teaming, federal_link_status)
    horizon = _materialize_federal_links(horizon, federal_link_status)
    pocs = _materialize_federal_links(pocs, federal_link_status)
    evidence = _materialize_federal_links(evidence, federal_link_status)

    _check_data_currency(figures, report_date)

    return {
        "client_name": display_name,
        "client_logo": _ra.client_logo(client_identity),
        "header_companion_text": resolve_header_companion_text(
            client_identity),
        "logo_sizes": resolve_logo_sizes(client_identity),
        "report_date": report_date,
        "hero_context": hero_context,
        "scale_label": scale_label,
        "scale_total": scale_total, "scale_counts": scale_counts,
        "scale": scale, "chips": chips,
        "news": news, "signal_cards": signal_cards, "coverage": coverage,
        "best_fit": best_fit, "competitors": comp,
        "acquisition_pathways": acquisition_pathways, "teaming": teaming,
        "horizon": horizon, "pocs": pocs,
        "decision_makers": decision_makers, "evidence": evidence,
        "source_coverage": source_coverage,
        "sequence": sequence,
        # figure provenance rides the model so the render computes the
        # data-current line from it; there is no hand-set date slot
        "figures": list(figures) if figures else None,
        # INTERNAL sidecar notes; the renderer never reads this key
        "internal_notes": internal_notes,
    }
