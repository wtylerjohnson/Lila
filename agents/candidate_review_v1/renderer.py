"""Deterministic, self-contained Candidate Review v1 renderer (Chunk 5).

This module is the ONLY place that turns a validated ``CandidateReviewDocument``
into client-facing HTML.  It renders exclusively from the structured document
and its editor projection; there is no client-specific string substitution and
no external network dependency in the produced artifact.

Design contract:

- Output is one standalone HTML string (inline CSS, inline JS, data-URI
  images).  No external stylesheet, script, font, or image request.
- Output is deterministic: the same document plus the same download name
  produces byte-identical HTML.  No clock reads, no randomness.
- The eight locked sections render in ``SECTION_ORDER``.  The faces /
  account-ownership section never appears; ``assert_candidate_review_language``
  already guarantees the source strings cannot carry the forbidden labels.
- The visual system follows ``docs/reference/candidate-review-v1-house-style.md``
  (white paper, LILA palette, record-frame / fact-matrix / signal-list grammar,
  milestone rails, evidence tabs).  Typography embeds the licensed LILA type
  package (Manrope + Source Sans 3, SIL OFL) as base64 ``@font-face`` data URIs
  from ``fonts/``, with the system stack as fallback.
- Interaction hooks match the locked editor contract: ``#signal-board`` root,
  ``[data-editor-ui]`` toolbar, ``data-edit-id`` editable text,
  ``data-logo-slot-id`` image slots, ``data-source-link`` always-clickable
  citations, ``data-edit-singleline`` headline enforcement.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Optional

from agents.candidate_review_v1.calendar_engine import (
    CalendarItem,
    project_document_calendar,
)
from agents.candidate_review_v1.contracts import (
    SECTION_ORDER,
    CandidateReviewDocument,
    CandidateUnit,
    DateStatus,
    DateValue,
    EvidenceBoundClaim,
    EvidenceRecord,
    SearchConcepts,
    VehicleSignal,
    VehicleWatchRecord,
)
from agents.candidate_review_v1.projection import (
    CandidateReviewProjection,
    NumberedCandidate,
    project_candidate_review,
)


RENDERER_VERSION = "candidate_review_v1.renderer.v1"


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #

def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _attr(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _edit(edit_id: str, text: object, *, tag: str = "span",
          cls: str = "", single_line: bool = False) -> str:
    """Render an editable text node with a stable, unique ``data-edit-id``."""

    classes = f' class="{_attr(cls)}"' if cls else ""
    single = ' data-edit-singleline="1"' if single_line else ""
    return (f'<{tag}{classes} data-edit-id="{_attr(edit_id)}"{single}>'
            f'{_esc(text)}</{tag}>')


def _plain(text: object, *, tag: str = "span", cls: str = "") -> str:
    classes = f' class="{_attr(cls)}"' if cls else ""
    return f"<{tag}{classes}>{_esc(text)}</{tag}>"


def _status_class(status: DateStatus) -> str:
    return {
        DateStatus.CONFIRMED: "is-confirmed",
        DateStatus.ANTICIPATED: "is-anticipated",
        DateStatus.ESTIMATED: "is-estimated",
        DateStatus.RESEARCH_CLOCK: "is-research",
        DateStatus.MONITOR: "is-monitor",
    }[status]


_MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _format_date(date_value: DateValue) -> str:
    """Human date label without inventing precision the evidence lacks."""

    dv = date_value
    if dv.precision.value == "day" and dv.start is not None:
        return f"{_MONTHS[dv.start.month]} {dv.start.day}, {dv.start.year}"
    if dv.precision.value == "range" and dv.start is not None and dv.end is not None:
        if dv.start.year == dv.end.year and dv.start.month == dv.end.month:
            return (f"{_MONTHS[dv.start.month]} {dv.start.day}"
                    f" to {dv.end.day}, {dv.start.year}")
        return (f"{_MONTHS[dv.start.month]} {dv.start.day}, {dv.start.year} "
                f"to {_MONTHS[dv.end.month]} {dv.end.day}, {dv.end.year}")
    # month / quarter / fiscal-year: the source text is authoritative.
    return dv.source_text


def _source_link(evidence: Optional[EvidenceRecord], *, label: Optional[str] = None) -> str:
    if evidence is None:
        return ""
    text = label or evidence.source_name
    return (f'<a class="cr-src" data-source-link href="{_attr(evidence.source_url)}" '
            f'target="_blank" rel="noopener noreferrer">{_esc(text)}</a>')


# --------------------------------------------------------------------------- #
# Section renderers
# --------------------------------------------------------------------------- #

def _evidence_tabs(edit_prefix: str, records_show: str, may_suggest: str,
                   validate_next: str) -> str:
    return (
        '<div class="cr-tabs" role="group" aria-label="Evidence reasoning">'
        '<div class="cr-tab"><span class="cr-tab-label">Records show</span>'
        f'{_edit(edit_prefix + "-show", records_show, tag="p", cls="cr-tab-body")}</div>'
        '<div class="cr-tab"><span class="cr-tab-label">What it may suggest</span>'
        f'{_edit(edit_prefix + "-suggest", may_suggest, tag="p", cls="cr-tab-body")}</div>'
        '<div class="cr-tab"><span class="cr-tab-label">Validate next</span>'
        f'{_edit(edit_prefix + "-validate", validate_next, tag="p", cls="cr-tab-body")}</div>'
        "</div>"
    )


def _milestone_rail(edit_prefix: str, dates: tuple[DateValue, ...]) -> str:
    """Stepped, chevron-separated milestone band (GovTribe rail on white)."""

    if not dates:
        return ""
    steps = []
    for dv in dates[:4]:
        steps.append(
            f'<div class="cr-step {_status_class(dv.status)}">'
            f'<span class="cr-step-label">{_esc(dv.label)}</span>'
            f'<span class="cr-step-value">{_esc(_format_date(dv))}</span>'
            f'<span class="cr-step-tag">{_esc(dv.status.value.replace("_", " "))}</span>'
            "</div>"
        )
    return f'<div class="cr-rail">{"".join(steps)}</div>'


def _fact(label: str, value_html: str) -> str:
    return (f'<div class="cr-fact"><span class="cr-fact-label">{_esc(label)}</span>'
            f'<span class="cr-fact-value">{value_html}</span></div>')


def _candidate_card(item: NumberedCandidate,
                    evidence_by_id: dict[str, EvidenceRecord]) -> str:
    c: CandidateUnit = item.candidate
    pfx = f"cand-{c.candidate_id}"
    kind_label = ("Current official notice"
                  if c.kind.value == "current_notice"
                  else "Research corridor")
    facts = [
        _fact("Agency", _agency_fact_value(pfx, c.agency, pfx + "-agency")),
        _fact("Lifecycle", _plain(c.lifecycle.value.replace("_", " "))),
    ]
    if c.component:
        facts.append(_fact("Component", _edit(pfx + "-component", c.component)))
    for money in c.money:
        facts.append(_fact(money.basis.value.replace("_", " ").title(),
                           _plain(money.display_value, cls="cr-num")))
    # source links from the candidate's member + supporting evidence
    links = []
    seen: set[str] = set()
    for eid in c.all_evidence_ids:
        ev = evidence_by_id.get(eid)
        if ev is not None and eid not in seen:
            seen.add(eid)
            links.append(_source_link(ev, label=ev.source_name))
    links_html = ("<div class=\"cr-links\">" + " ".join(links) + "</div>"
                  if links else "")
    cautions = ""
    if c.cautions:
        items = "".join(f"<li>{_esc(text)}</li>" for text in c.cautions)
        cautions = f'<ul class="cr-cautions">{items}</ul>'
    return (
        f'<article class="cr-frame cr-candidate" data-candidate-id="{_attr(c.candidate_id)}">'
        f'{_milestone_rail(pfx, c.dates)}'
        '<div class="cr-frame-inner">'
        '<header class="cr-frame-head">'
        f'<span class="cr-num" data-candidate-number>{_esc(item.number)}</span>'
        '<div class="cr-frame-titles">'
        f'{_edit(pfx + "-title", c.title, tag="h3", cls="cr-frame-title", single_line=True)}'
        f'<span class="cr-deck"><span class="cr-deck-tag">{_esc(kind_label)}</span>'
        f'<span class="cr-deck-sep">·</span>{_esc(c.lifecycle.value.replace("_", " "))}</span>'
        "</div>"
        '<div class="cr-card-tools" aria-hidden="true">'
        '<button type="button" class="cr-handle" data-reorder-handle title="Drag to reorder">≡</button>'
        '<button type="button" class="cr-remove" data-remove-candidate title="Remove this opportunity">Remove</button>'
        "</div>"
        "</header>"
        f'<div class="cr-matrix">{"".join(facts)}</div>'
        f'{_evidence_tabs(pfx, c.records_show, c.may_suggest, c.validate_next)}'
        f'{cautions}'
        f'{links_html}'
        "</div>"
        "</article>"
    )


def _claim_row(claim: EvidenceBoundClaim,
               evidence_by_id: dict[str, EvidenceRecord]) -> str:
    pfx = f"claim-{claim.block_id}"
    links = " ".join(
        _source_link(evidence_by_id.get(eid))
        for eid in claim.evidence_ids if evidence_by_id.get(eid) is not None
    )
    return (
        '<div class="cr-signal">'
        f'{_edit(pfx + "-head", claim.heading, tag="h4", cls="cr-signal-head", single_line=True)}'
        f'{_evidence_tabs(pfx, claim.records_show, claim.may_suggest, claim.validate_next)}'
        f'<div class="cr-links">{links}</div>'
        "</div>"
    )


_SEAL_URI_MEMO: dict = {}

_SERVICE_SEAL_KEYS = (
    ("AIR FORCE", "usaf"),
    ("SPACE FORCE", "ussf"),
    ("MARINE CORPS", "usmc"),
    ("COAST GUARD", "uscg"),
    ("ARMY", "army"),
    ("NAVY", "navy"),
    ("CUSTOMS AND BORDER", "cbp"),
    ("CITIZENSHIP AND IMMIGRATION", "uscis"),
    ("INTERNAL REVENUE", "irs"),
    ("TRANSPORTATION SECURITY", "tsa"),
    ("DEFENSE HEALTH", "dha"),
    ("NATIONAL TRANSPORTATION SAFETY", "ntsb"),
    ("VETERANS AFFAIRS", "va"),
    ("TREASURY", "treas"),
)


def _agency_seal_uri(agency: str) -> str:
    """Official agency seal as an embedded data URI, or empty.

    Reuses the house monogram resolver and the operator seal cache; the
    downloaded HTML stays self-contained and a missing seal renders nothing
    (no placeholder, no filler).
    """

    key = (agency or "").strip().casefold()
    if not key:
        return ""
    if key in _SEAL_URI_MEMO:
        return _SEAL_URI_MEMO[key]
    uri = ""
    try:
        from agents.reports.views import _agency_monogram, _mark_data_uri
        from tools.brand_marks import find_mark, seals_dir
        upper = agency.upper()
        # This deliverable names the acting organization exactly, so a
        # service branch or component shows ITS OWN seal when one exists;
        # the signal board's component-to-department doctrine is untouched
        # (this supplement is renderer-private and never enters the shared
        # monogram universe).
        supplement = next(
            (seal_key for pattern, seal_key in _SERVICE_SEAL_KEYS
             if pattern in upper), None)
        mark = (find_mark(seals_dir(), supplement)
                if supplement is not None else None)
        if mark is None:
            mark = find_mark(seals_dir(), _agency_monogram(agency).lower())
        uri = _mark_data_uri(mark) or ""
    except Exception:  # noqa: BLE001 - a seal must never break a render
        uri = ""
    _SEAL_URI_MEMO[key] = uri
    return uri


def _agency_fact_value(pfx: str, agency: str, edit_id: str) -> str:
    """The agency value with its seal beside it (house style: the editable
    seal slot sits beside the agency value without changing row height)."""

    seal = _agency_seal_uri(agency)
    seal_html = (
        f'<span class="cr-seal-slot" data-logo-slot-id="{_attr(pfx)}-agency-seal">'
        f'<img class="cr-agency-seal" alt="" src="{seal}"></span>'
        if seal else "")
    return seal_html + _edit(edit_id, agency)


def _vehicle_row(signal: VehicleSignal,
                 evidence_by_id: dict[str, EvidenceRecord]) -> str:
    pfx = f"veh-{signal.signal_id}"
    name = signal.vehicle.name or signal.vehicle.idv_piid or "Unnamed vehicle"
    posture = (signal.relationship.access_posture.value.replace("_", " ")
               if signal.relationship is not None else "unknown")
    links = " ".join(
        _source_link(evidence_by_id.get(eid))
        for eid in signal.all_evidence_ids if evidence_by_id.get(eid) is not None
    )
    return (
        '<div class="cr-signal cr-vehicle">'
        '<div class="cr-signal-top">'
        f'{_edit(pfx + "-title", signal.title, tag="h4", cls="cr-signal-head", single_line=True)}'
        f'<span class="cr-chip">{_esc(signal.status.value)}</span>'
        "</div>"
        '<div class="cr-matrix">'
        f'{_fact("Vehicle", _plain(name))}'
        f'{_fact("Managing agency", _agency_fact_value(pfx, signal.agency, pfx + "-agency"))}'
        f'{_fact("Access posture", _plain(posture))}'
        "</div>"
        f'{_milestone_rail(pfx, signal.dates)}'
        f'{_evidence_tabs(pfx, signal.records_show, signal.may_suggest, signal.validate_next)}'
        f'<div class="cr-links">{links}</div>'
        "</div>"
    )


def _watch_row(record: VehicleWatchRecord,
               evidence_by_id: dict[str, EvidenceRecord]) -> str:
    pfx = f"watch-{record.watch_record_id}"
    identity = (record.canonical_name or record.vehicle_program_id
                or record.parent_idv_piid or "Unnamed vehicle")
    links = " ".join(
        _source_link(evidence_by_id.get(eid))
        for eid in record.all_evidence_ids
        if evidence_by_id.get(eid) is not None
    )
    class_fact = (
        _fact("Vehicle class",
              _plain(record.vehicle_class.value.replace("_", " ")))
        if record.vehicle_class is not None else "")
    scope_fact = (
        _fact("Scope", _edit(pfx + "-scope", record.scope_summary))
        if record.scope_summary else "")
    as_of_fact = (
        _fact("Source data as of", _plain(record.source_data_as_of.isoformat()))
        if record.source_data_as_of is not None else "")
    return (
        '<div class="cr-signal cr-vehicle cr-watch">'
        '<div class="cr-signal-top">'
        f'{_edit(pfx + "-title", identity, tag="h4", cls="cr-signal-head", single_line=True)}'
        f'<span class="cr-chip">{_esc(record.ordering_status.value.replace("_", " "))}</span>'
        "</div>"
        '<div class="cr-matrix">'
        f'{_fact("Managing agency", _agency_fact_value(pfx, record.managing_agency, pfx + "-agency"))}'
        f'{class_fact}'
        f'{_fact("On-ramp", _plain(record.on_ramp_status.value.replace("_", " ")))}'
        f'{_fact("Access posture", _plain(record.access_posture.value.replace("_", " ")))}'
        f'{scope_fact}'
        f'{as_of_fact}'
        "</div>"
        f'{_milestone_rail(pfx, record.dates)}'
        f'{_evidence_tabs(pfx, record.records_show, record.may_suggest, record.validate_next)}'
        f'<div class="cr-links">{links}</div>'
        "</div>"
    )


def _calendar_row(item: CalendarItem,
                  evidence_by_id: dict[str, EvidenceRecord]) -> str:
    dv = item.timing
    links = []
    for url in item.source_urls:
        links.append(f'<a class="cr-src" data-source-link href="{_attr(url)}" '
                     f'target="_blank" rel="noopener noreferrer">source</a>')
    return (
        f'<div class="cr-cal-row {_status_class(dv.status)}">'
        f'<div class="cr-cal-date"><span class="cr-cal-day">{_esc(_format_date(dv))}</span>'
        f'<span class="cr-cal-kind">{_esc(dv.kind.value.replace("_", " "))}</span></div>'
        '<div class="cr-cal-body">'
        f'<span class="cr-cal-title">{_esc(item.title)}</span>'
        f'<span class="cr-cal-status">{_esc(dv.status.value.replace("_", " "))}'
        f'{(" · " + _esc(item.agency)) if item.agency else ""}</span>'
        "</div>"
        f'<div class="cr-cal-src">{" ".join(links)}</div>'
        "</div>"
    )


def _section(section_id: str, heading: str, body_html: str, *,
             empty_note: str = "") -> str:
    pfx = f"sec-{section_id}"
    inner = body_html or (
        f'<p class="cr-empty">{_esc(empty_note)}</p>' if empty_note else "")
    return (
        f'<section class="cr-section" id="section-{_attr(section_id)}" '
        f'aria-labelledby="{_attr(pfx)}-title">'
        f'{_edit(pfx + "-title", heading, tag="h2", cls="cr-section-title", single_line=True)}'
        f'{inner}'
        "</section>"
    )


def _hero(document: CandidateReviewDocument,
          projection: CandidateReviewProjection) -> str:
    as_of = document.as_of
    as_of_label = f"{_MONTHS[as_of.month]} {as_of.day}, {as_of.year}"
    tiles = []
    for kpi in projection.kpi_tiles:
        tiles.append(
            '<div class="cr-kpi">'
            f'<span class="cr-kpi-eyebrow">{_esc(kpi.eyebrow)}</span>'
            f'<span class="cr-kpi-value cr-num" data-kpi-kind="{_attr(kpi.kind.value)}">'
            f'{_esc(kpi.value)}</span>'
            f'<span class="cr-kpi-title">{_esc(kpi.title)}</span>'
            f'<span class="cr-kpi-note">{_esc(kpi.note)}</span>'
            "</div>"
        )
    scoreboard = f'<div class="cr-scoreboard">{"".join(tiles)}</div>' if tiles else ""
    return (
        '<header class="cr-hero">'
        '<div class="cr-hero-main">'
        f'<span class="cr-eyebrow">Federal Opportunity Pre-Assessment · as of {_esc(as_of_label)}</span>'
        f'{_edit("hero-client", document.client_name, tag="h1", cls="cr-title", single_line=True)}'
        f'{_edit("hero-context", "Preview for triage, not a finished qualification decision. Every record links to its primary USAspending or SAM.gov source.", tag="p", cls="cr-lede")}'
        f'<div class="cr-logo-slot" data-logo-slot-id="client-logo" title="Client logo">'
        '<span class="cr-logo-hint">Client logo</span></div>'
        "</div>"
        f'{scoreboard}'
        "</header>"
    )


def _ticker(projection: CandidateReviewProjection) -> str:
    if not projection.ticker_items:
        return ""
    chips = []
    for tick in projection.ticker_items:
        refs = " ".join(tick.candidate_ids)
        chips.append(
            f'<span class="sb-news-item" data-candidate-refs="{_attr(refs)}">'
            f'{_edit("tick-" + tick.ticker_id, tick.label, cls="sb-news-label")}</span>'
        )
    primary = f'<div class="sb-news-set">{"".join(chips)}</div>'
    return (
        '<div class="cr-ticker" aria-label="Report signal ticker">'
        f'<div class="sb-news-tape">{primary}</div>'
        "</div>"
    )


def _nav() -> str:
    links = "".join(
        f'<a href="#section-{_attr(spec.section_id)}">{_esc(spec.heading)}</a>'
        for spec in SECTION_ORDER
    )
    return f'<nav class="cr-nav" aria-label="Report sections">{links}</nav>'


def _toolbar() -> str:
    return (
        '<div class="cr-editor-bar" data-editor-ui>'
        '<div class="cr-editor-actions">'
        '<button type="button" class="cr-btn cr-btn-primary" data-action="toggle-edit" aria-pressed="false">Edit text &amp; logos</button>'
        '<button type="button" class="cr-btn" data-action="save-draft">Save draft</button>'
        '<button type="button" class="cr-btn" data-action="reset">Reset</button>'
        '<button type="button" class="cr-btn" data-action="download-html">Download HTML</button>'
        '<button type="button" class="cr-btn" data-action="print">Print / PDF</button>'
        '<button type="button" class="cr-btn cr-btn-undo" data-action="undo-soft-delete" hidden>Undo remove</button>'
        "</div>"
        '<span class="cr-editor-status" data-editor-status role="status">View mode · ready</span>'
        "</div>"
    )


def _refresh_action() -> str:
    return (
        '<div class="cr-refresh">'
        '<button type="button" class="cr-btn cr-btn-refresh" data-workflow-action="refresh-analyst-layer">'
        'Refresh from Analyst Layer</button>'
        '<p class="cr-refresh-note">Reopens the approved analytical layer. A changed and re-approved '
        'layer triggers one complete refresh.</p>'
        "</div>"
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def render_candidate_review(
    document: CandidateReviewDocument,
    *,
    projection: Optional[CandidateReviewProjection] = None,
    download_name: Optional[str] = None,
) -> str:
    """Render one standalone, deterministic Candidate Review HTML document."""

    # Treat rendering as a publication boundary.  A caller can create a
    # non-validated Pydantic copy, so validate serialized values again even
    # when a precomputed projection is supplied.
    document = CandidateReviewDocument.model_validate(
        document.model_dump(mode="python")
    )

    if projection is None:
        projection = project_candidate_review(document)

    evidence_by_id = {item.evidence_id: item for item in document.evidence}
    visible_candidate_ids = tuple(item.candidate_id for item in projection.candidates)

    # Section 1: 360 assessment (pattern claims)
    s1 = "".join(_claim_row(claim, evidence_by_id)
                 for claim in document.pattern_claims)

    # Section 2: candidate opportunities
    s2 = ('<div class="cr-candidates" data-candidate-list>'
          + "".join(_candidate_card(item, evidence_by_id)
                    for item in projection.candidates)
          + "</div>")

    # Section 3: vehicle, partner, market signals
    s3_parts = [_vehicle_row(sig, evidence_by_id)
                for sig in document.vehicle_signals]
    s3_parts += [_watch_row(record, evidence_by_id)
                 for record in document.vehicle_watch_records]
    s3_parts += [_claim_row(claim, evidence_by_id)
                 for claim in document.market_signals]
    s3 = "".join(s3_parts)

    # Section 4: past awards and competitive analysis
    s4 = "".join(_claim_row(claim, evidence_by_id)
                 for claim in document.past_awards)

    # Section 5: preliminary keywords and capability search concepts
    s5 = _search_concepts(document.search_concepts)

    # Section 6: federal opportunity calendar
    calendar_items = project_document_calendar(
        document, candidate_ids=visible_candidate_ids)
    s6 = ('<div class="cr-calendar">'
          + "".join(_calendar_row(ci, evidence_by_id) for ci in calendar_items)
          + "</div>") if calendar_items else ""

    # Section 7: assess | target | execute
    s7 = _execution_framework(document)

    # Section 8: evidence dock
    s8 = _evidence_dock(projection)

    section_bodies = {
        "forecast": (s1, "Sparse evidence produced a shorter assessment."),
        "candidate-review": (s2, "No current candidate opportunities cleared review this run."),
        "signals": (s3, "No client-relevant vehicle or market signal was evidenced this run."),
        "accounts": (s4, "No competitive award pattern was evidenced this run."),
        "capabilities": (s5, ""),
        "calendar": (s6, "No dated procurement, vehicle, or verified event was in range this run."),
        "method": (s7, ""),
        "evidence": (s8, ""),
    }
    sections_html = "".join(
        _section(spec.section_id, spec.heading, section_bodies[spec.section_id][0],
                 empty_note=section_bodies[spec.section_id][1])
        for spec in SECTION_ORDER
    )

    report_id = f"crv1-{document.binding.client_id}"
    dl_name = download_name or (
        f"{document.binding.client_id}_Federal_Opportunity_Assessment_EDITABLE.html")
    title = f"{document.client_name} Federal Opportunity Pre-Assessment"

    head = (
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f'<meta name="description" content="{_attr(title)}: candidate opportunities, '
        'vehicle and market signals, past awards, preliminary search concepts, and a '
        'federal opportunity calendar. Preview for analyst triage.">'
        f"<style>{_HOUSE_STYLE_CSS}</style>"
        "</head>"
    )
    body = (
        "<body>"
        f"{_toolbar()}"
        f"{_nav()}"
        f'<main id="signal-board" class="cr-report">'
        f"{_hero(document, projection)}"
        f"{_ticker(projection)}"
        f"{sections_html}"
        f"{_refresh_action()}"
        '<footer class="cr-footer"><span>Generated from a single validated evidence '
        'set. Analysts assign final treatment.</span></footer>'
        "</main>"
        f"<script>{_APP_JS}</script>"
        "</body>"
    )
    return (
        "<!doctype html>"
        f'<html lang="en" data-report-id="{_attr(report_id)}" '
        f'data-download-name="{_attr(dl_name)}" '
        f'data-renderer="{_attr(RENDERER_VERSION)}">'
        f"{head}{body}</html>"
    )


def _search_concepts(concepts: Optional[SearchConcepts]) -> str:
    if concepts is None:
        return ""

    def _group(label: str, values: tuple[str, ...]) -> str:
        if not values:
            return ""
        chips = "".join(f'<span class="cr-term">{_esc(v)}</span>' for v in values)
        return (f'<div class="cr-term-group"><span class="cr-fact-label">{_esc(label)}</span>'
                f'<div class="cr-term-row">{chips}</div></div>')

    return (
        '<div class="cr-panel cr-concepts">'
        '<p class="cr-note">Preliminary search frame from the approved Analyst Layer. '
        'Keywords are the primary evidence layer; codes are a coarse boundary.</p>'
        f'{_group("Keywords", concepts.keywords)}'
        f'{_group("NAICS", concepts.naics_codes)}'
        f'{_group("PSC", concepts.psc_codes)}'
        "</div>"
    )


def _execution_framework(document: CandidateReviewDocument) -> str:
    ef = document.execution_framework
    if ef is None:
        return ""
    return (
        '<div class="cr-method">'
        '<div class="cr-panel"><span class="cr-fact-label">Assess</span>'
        f'{_edit("method-assess", ef.assess, tag="p", cls="cr-tab-body")}</div>'
        '<div class="cr-panel"><span class="cr-fact-label">Target</span>'
        f'{_edit("method-target", ef.target, tag="p", cls="cr-tab-body")}</div>'
        '<div class="cr-panel"><span class="cr-fact-label">Execute</span>'
        f'{_edit("method-execute", ef.execute, tag="p", cls="cr-tab-body")}</div>'
        "</div>"
    )


def _evidence_dock(projection: CandidateReviewProjection) -> str:
    rows = []
    for ev in projection.evidence:
        rows.append(
            '<div class="cr-dock-row" data-evidence-id="'
            f'{_attr(ev.evidence_id)}">'
            f'<span class="cr-dock-tier">{_esc(ev.source_tier.value)}</span>'
            f'<span class="cr-dock-title">{_esc(ev.title)}</span>'
            f'{_source_link(ev, label=ev.source_name)}'
            "</div>"
        )
    return f'<div class="cr-dock">{"".join(rows)}</div>'


# --------------------------------------------------------------------------- #
# House style (inline, self-contained)
# --------------------------------------------------------------------------- #

_FONTS_DIR = Path(__file__).parent / "fonts"

# Embedded, license-verified LILA type package (SIL OFL): Manrope for display
# and Source Sans 3 for body.  Licenses ship alongside the WOFF2 files in
# ``fonts/``.  Everything is inlined as data URIs so the artifact makes no
# external font request; if a file is missing the renderer falls back to the
# system stack declared in the CSS variables.
_FONT_FILES = (
    ("Manrope LILA", 700, "Manrope-700.woff2"),
    ("Manrope LILA", 800, "Manrope-800.woff2"),
    ("Source Sans 3 LILA", 400, "SourceSans3-400.woff2"),
    ("Source Sans 3 LILA", 600, "SourceSans3-600.woff2"),
    ("Source Sans 3 LILA", 700, "SourceSans3-700.woff2"),
)


def _load_font_faces() -> str:
    faces = []
    for family, weight, filename in _FONT_FILES:
        try:
            data = (_FONTS_DIR / filename).read_bytes()
        except OSError:
            continue
        b64 = base64.b64encode(data).decode("ascii")
        faces.append(
            f"@font-face{{font-family:'{family}';font-style:normal;"
            f"font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
    return "".join(faces)


_FONT_FACE = _load_font_faces()

_HOUSE_STYLE_CSS = _FONT_FACE + """
:root{
--lila-canvas:#e9edf1;--lila-paper:#ffffff;--lila-panel:#f4f7f9;
--lila-panel-strong:#e9eff4;--lila-ink:#0b1728;--lila-navy:#12233a;
--lila-text:#28394c;--lila-muted:#6a7a8c;--lila-rule:#d5dee6;
--lila-rule-strong:#b9c6d1;--lila-blue:#116da0;--lila-blue-dk:#0d5e8c;
--lila-green:#127258;--lila-orange:#f0451d;
--lila-display:'Manrope LILA','Avenir Next','Segoe UI','Helvetica Neue',Arial,sans-serif;
--lila-body:'Source Sans 3 LILA','Avenir Next','Segoe UI','Helvetica Neue',Arial,sans-serif;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--lila-canvas);color:var(--lila-text);
font-family:var(--lila-body);font-size:15px;line-height:1.55;
font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased;
text-rendering:optimizeLegibility;}
.cr-num,.cr-step-value,.cr-kpi-value,.cr-cal-day{font-variant-numeric:tabular-nums;}
a{color:var(--lila-blue);text-underline-offset:2px;}
h1,h2,h3,h4{font-family:var(--lila-display);color:var(--lila-ink);font-weight:800;margin:0;}
.cr-report{max-width:1000px;margin:0 auto;background:var(--lila-paper);
padding:0 0 64px;box-shadow:0 1px 3px rgba(11,23,40,0.08);}
.cr-editor-bar{position:sticky;top:0;z-index:50;display:flex;
justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;
background:var(--lila-navy);color:#fff;padding:9px 24px;}
.cr-editor-actions{display:flex;gap:8px;flex-wrap:wrap;}
.cr-btn{font-family:var(--lila-body);font-size:12.5px;font-weight:700;
border:1px solid rgba(255,255,255,0.28);background:transparent;color:#fff;
border-radius:5px;padding:6px 12px;cursor:pointer;letter-spacing:0.01em;}
.cr-btn:hover{background:rgba(255,255,255,0.10);}
.cr-btn-primary{background:var(--lila-blue);border-color:var(--lila-blue);}
.cr-btn-undo{background:var(--lila-orange);border-color:var(--lila-orange);}
.cr-editor-status{font-size:12px;color:#b9c8d6;}
.cr-nav{position:sticky;top:40px;z-index:40;background:rgba(255,255,255,0.96);
border-bottom:1px solid var(--lila-rule);max-width:1000px;margin:0 auto;
padding:9px 40px;display:flex;gap:18px;flex-wrap:wrap;font-size:11px;
font-weight:700;text-transform:uppercase;letter-spacing:0.04em;}
.cr-nav a{color:var(--lila-muted);text-decoration:none;}
.cr-nav a:hover{color:var(--lila-blue);}
.cr-hero{display:grid;grid-template-columns:1fr 320px;gap:36px;
padding:36px 40px 26px;border-bottom:3px solid var(--lila-ink);}
.cr-eyebrow{font-size:11px;font-weight:700;text-transform:uppercase;
letter-spacing:0.08em;color:var(--lila-orange);}
.cr-title{font-size:40px;line-height:1.05;font-weight:800;
letter-spacing:-0.025em;margin:8px 0 12px;}
.cr-lede{font-size:15.5px;max-width:62ch;margin:0 0 16px;}
.cr-seal-slot{display:inline-block;vertical-align:-5px;margin-right:7px;line-height:0;}
.cr-agency-seal{height:20px;width:20px;object-fit:contain;border-radius:50%;}
.cr-logo-slot{border:1px dashed var(--lila-rule);border-radius:6px;
min-height:52px;display:flex;align-items:center;justify-content:center;
padding:8px;max-width:230px;}
.cr-logo-slot img{max-width:100%;max-height:64px;display:block;}
.cr-logo-hint{font-size:11px;color:var(--lila-muted);text-transform:uppercase;letter-spacing:0.05em;}
.cr-scoreboard{display:grid;grid-template-columns:1fr 1fr;gap:10px;align-content:start;}
.cr-kpi{background:var(--lila-panel);border:1px solid var(--lila-rule);
border-radius:6px;padding:12px 14px;}
.cr-kpi-eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:0.06em;
color:var(--lila-muted);display:block;}
.cr-kpi-value{font-family:var(--lila-display);font-size:30px;font-weight:800;
letter-spacing:-0.02em;color:var(--lila-ink);display:block;line-height:1.05;}
.cr-kpi-title{font-size:12.5px;font-weight:700;display:block;margin-top:2px;}
.cr-kpi-note{font-size:11.5px;color:var(--lila-muted);display:block;}
.cr-ticker{overflow:hidden;border-bottom:1px solid var(--lila-rule);
background:var(--lila-panel);white-space:nowrap;}
.sb-news-tape{display:inline-flex;animation:cr-marquee 44s linear infinite;}
.sb-news-set{display:inline-flex;}
.sb-news-item{padding:9px 20px;font-size:12.5px;font-weight:700;
border-right:1px solid var(--lila-rule);}
.sb-news-item::before{content:"\\25CF";color:var(--lila-orange);font-size:8px;margin-right:8px;}
.cr-ticker:hover .sb-news-tape{animation-play-state:paused;}
@keyframes cr-marquee{from{transform:translateX(0);}to{transform:translateX(-50%);}}
.cr-section{margin:34px 40px 0;}
.cr-section-title{font-size:21px;font-weight:800;letter-spacing:-0.01em;
padding-bottom:9px;margin-bottom:18px;border-bottom:1px solid var(--lila-rule);position:relative;}
.cr-section-title::after{content:"";position:absolute;left:0;bottom:-1px;
width:44px;height:3px;background:var(--lila-orange);}
.cr-empty{color:var(--lila-muted);font-size:14px;}
.cr-candidates{display:flex;flex-direction:column;gap:20px;}
.cr-frame{background:var(--lila-paper);border:1px solid var(--lila-rule-strong);
border-radius:7px;overflow:hidden;}
.cr-candidate{border-top:3px solid var(--lila-orange);}
.cr-frame-inner{padding:18px 22px 20px;}
.cr-frame-head{display:flex;gap:14px;align-items:flex-start;}
.cr-num{font-family:var(--lila-display);font-weight:800;font-size:20px;
color:var(--lila-orange);min-width:30px;line-height:1.2;}
.cr-frame-titles{flex:1;}
.cr-frame-title{font-size:20px;line-height:1.22;letter-spacing:-0.01em;}
.cr-deck{display:block;margin-top:3px;font-size:12px;color:var(--lila-muted);}
.cr-deck-tag{font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:var(--lila-blue);}
.cr-deck-sep{margin:0 6px;color:var(--lila-rule-strong);}
.cr-card-tools{display:none;gap:6px;}
body.edit-mode .cr-card-tools{display:flex;}
.cr-handle{cursor:grab;background:var(--lila-panel);border:1px solid var(--lila-rule);
border-radius:5px;padding:2px 9px;font-size:14px;}
.cr-remove{cursor:pointer;background:#fff;border:1px solid var(--lila-orange);
color:var(--lila-orange);border-radius:5px;padding:2px 10px;font-size:12px;font-weight:700;}
.cr-rail{display:flex;flex-wrap:wrap;background:var(--lila-panel);
border-bottom:1px solid var(--lila-rule);}
.cr-step{position:relative;flex:1 1 0;min-width:132px;padding:9px 16px 9px 15px;}
.cr-step + .cr-step{border-left:1px solid var(--lila-rule);}
.cr-step + .cr-step::before{content:"\\203A";position:absolute;left:-7px;top:50%;
transform:translateY(-50%);color:var(--lila-rule-strong);font-size:15px;font-weight:700;
background:var(--lila-panel);line-height:1;}
.cr-step-label{display:block;font-size:10px;text-transform:uppercase;
letter-spacing:0.06em;color:var(--lila-muted);}
.cr-step-value{display:block;font-size:14px;font-weight:800;color:var(--lila-ink);letter-spacing:-0.01em;}
.cr-step-tag{display:block;font-size:10px;color:var(--lila-muted);text-transform:capitalize;}
.cr-step.is-confirmed .cr-step-value{color:var(--lila-green);}
.cr-step.is-research .cr-step-value{color:var(--lila-orange);}
.cr-step.is-anticipated .cr-step-value,.cr-step.is-estimated .cr-step-value{color:var(--lila-blue);}
.cr-matrix{display:grid;grid-template-columns:1fr 1fr;
border:1px solid var(--lila-rule);border-radius:6px;overflow:hidden;margin:16px 0;}
.cr-fact{padding:9px 13px;border-top:1px solid var(--lila-rule);}
.cr-fact:nth-child(1),.cr-fact:nth-child(2){border-top:none;}
.cr-fact:nth-child(odd){border-right:1px solid var(--lila-rule);}
.cr-fact-label{display:block;font-size:10.5px;text-transform:uppercase;
letter-spacing:0.05em;color:var(--lila-muted);}
.cr-fact-value{display:block;font-size:14.5px;font-weight:700;color:var(--lila-ink);}
.cr-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;
margin-top:16px;border-top:1px solid var(--lila-rule);padding-top:14px;}
.cr-tab-label{display:inline-block;font-size:10.5px;font-weight:800;text-transform:uppercase;
letter-spacing:0.05em;color:var(--lila-blue);border-bottom:2px solid var(--lila-blue);
padding-bottom:4px;margin-bottom:8px;}
.cr-tab-body{margin:0;font-size:14px;line-height:1.5;}
.cr-cautions{margin:14px 0 0;padding-left:18px;font-size:13px;color:var(--lila-muted);}
.cr-links{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px 16px;
padding-top:10px;border-top:1px dashed var(--lila-rule);}
.cr-src{font-size:12px;font-weight:700;color:var(--lila-blue);text-decoration:none;}
.cr-src::before{content:"\\2197";font-size:11px;margin-right:3px;color:var(--lila-muted);}
.cr-src:hover{text-decoration:underline;}
.cr-signal{border-bottom:1px solid var(--lila-rule);padding:16px 0;}
.cr-signal:first-child{padding-top:2px;}
.cr-signal:last-child{border-bottom:none;}
.cr-signal-top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;}
.cr-signal-head{font-size:16px;color:var(--lila-blue-dk);letter-spacing:-0.01em;}
.cr-chip{font-size:10px;font-weight:800;text-transform:uppercase;
letter-spacing:0.05em;background:var(--lila-panel-strong);color:var(--lila-text);
border-radius:3px;padding:3px 8px;white-space:nowrap;}
.cr-vehicle .cr-matrix{margin:12px 0;}
.cr-panel{background:var(--lila-panel);border:1px solid var(--lila-rule);
border-radius:6px;padding:16px 18px;}
.cr-note{font-size:13px;color:var(--lila-muted);margin:0 0 12px;}
.cr-term-group{margin-top:14px;}
.cr-term-row{display:flex;flex-wrap:wrap;gap:7px;margin-top:7px;}
.cr-term{font-size:12.5px;font-weight:700;background:#fff;border:1px solid var(--lila-rule-strong);
border-radius:4px;padding:4px 11px;color:var(--lila-navy);}
.cr-calendar{display:flex;flex-direction:column;border-top:1px solid var(--lila-rule);}
.cr-cal-row{display:grid;grid-template-columns:160px 1fr auto;gap:16px;
align-items:center;padding:12px 0;border-bottom:1px solid var(--lila-rule);}
.cr-cal-date{border-left:3px solid var(--lila-rule-strong);padding-left:13px;}
.cr-cal-row.is-confirmed .cr-cal-date{border-left-color:var(--lila-green);}
.cr-cal-row.is-research .cr-cal-date{border-left-color:var(--lila-orange);}
.cr-cal-row.is-anticipated .cr-cal-date,.cr-cal-row.is-estimated .cr-cal-date{border-left-color:var(--lila-blue);}
.cr-cal-day{display:block;font-weight:800;color:var(--lila-ink);letter-spacing:-0.01em;}
.cr-cal-kind{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:0.05em;color:var(--lila-muted);}
.cr-cal-title{display:block;font-weight:700;color:var(--lila-ink);}
.cr-cal-status{display:block;font-size:12px;color:var(--lila-muted);}
.cr-method{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.cr-method .cr-fact-label{color:var(--lila-orange);font-size:11px;margin-bottom:6px;}
.cr-dock{display:flex;flex-direction:column;border-top:1px solid var(--lila-rule);}
.cr-dock-row{display:grid;grid-template-columns:96px 1fr auto;gap:14px;
align-items:baseline;padding:8px 0;border-bottom:1px solid var(--lila-rule);font-size:13px;}
.cr-dock-tier{font-size:9.5px;text-transform:uppercase;letter-spacing:0.06em;
color:var(--lila-muted);font-weight:700;}
.cr-dock-title{color:var(--lila-ink);}
.cr-refresh{margin:48px 40px 0;padding-top:26px;border-top:3px solid var(--lila-ink);text-align:center;}
.cr-btn-refresh{background:var(--lila-ink);color:#fff;border:none;font-family:var(--lila-display);
font-size:15px;font-weight:800;letter-spacing:0.01em;padding:13px 26px;border-radius:7px;cursor:pointer;}
.cr-btn-refresh:hover{background:var(--lila-blue);}
.cr-refresh-note{font-size:12px;color:var(--lila-muted);margin-top:10px;}
.cr-footer{margin:32px 40px 0;font-size:11.5px;color:var(--lila-muted);}
body.edit-mode [data-edit-id]{outline:1px dashed rgba(17,109,160,0.4);outline-offset:2px;border-radius:2px;}
body.edit-mode [data-edit-id]:focus{outline:2px solid var(--lila-blue);background:#eef7fb;}
.cr-logo-slot.is-dragover{border-color:var(--lila-blue);background:#eef7fb;}
.cr-candidate.is-removed{display:none;}
@media (max-width:860px){
.cr-hero{grid-template-columns:1fr;padding:24px 20px 20px;}
.cr-nav{padding:8px 20px;gap:12px;}.cr-section{margin:28px 20px 0;}
.cr-refresh,.cr-footer{margin-left:20px;margin-right:20px;}
.cr-tabs{grid-template-columns:1fr;}.cr-matrix{grid-template-columns:1fr;}
.cr-method{grid-template-columns:1fr;}.cr-title{font-size:30px;}
.cr-cal-row{grid-template-columns:1fr;gap:6px;}}
@media print{
body{background:#fff;}.cr-editor-bar,.cr-nav,.cr-refresh,.cr-card-tools{display:none !important;}
.cr-report{max-width:none;box-shadow:none;}.cr-ticker{display:none;}
.cr-frame,.cr-panel{break-inside:avoid;}a{color:#000;}}
"""


# --------------------------------------------------------------------------- #
# Editable app (inline, self-contained). Superset of the reference app:
# adds candidate soft-delete + undo + reorder with count/numbering/ticker/
# evidence reconciliation, on top of edit/save/reset/download/print/drag-drop.
# --------------------------------------------------------------------------- #

_APP_JS = r"""
(() => {
  const root = document.querySelector("#signal-board");
  const ui = document.querySelector("[data-editor-ui]");
  if (!root || !ui) return;
  const reportId = document.documentElement.dataset.reportId || "candidate-review";
  const storageKey = "lila:crv1:" + reportId;
  const action = (name) => ui.querySelector('[data-action="' + name + '"]');
  const status = ui.querySelector("[data-editor-status]");
  const list = root.querySelector("[data-candidate-list]");
  let editing = false, dirty = false;
  const removed = new Set();
  let lastRemoved = null;

  const fields = () => [...root.querySelectorAll("[data-edit-id]")];
  const logos = () => [...root.querySelectorAll("[data-logo-slot-id]")];
  const cards = () => [...root.querySelectorAll(".cr-candidate")];
  const announce = (m) => { status.textContent = m; };
  const isImg = (v) => { const s = (v||"").toLowerCase(); return s.startsWith("data:image/"); };

  const baseline = {
    values: Object.fromEntries(fields().map((el) => [el.dataset.editId, el.innerHTML])),
    logos: Object.fromEntries(logos().map((el) => [el.dataset.logoSlotId, el.innerHTML])),
    order: cards().map((c) => c.dataset.candidateId),
  };

  function snapshot() {
    return {
      version: 1, savedAt: new Date().toISOString(),
      values: Object.fromEntries(fields().map((el) => [el.dataset.editId, el.innerHTML])),
      logos: Object.fromEntries(logos().map((el) => [el.dataset.logoSlotId, el.innerHTML])),
      order: cards().map((c) => c.dataset.candidateId),
      removed: [...removed],
    };
  }

  function reconcile() {
    let n = 0;
    cards().forEach((card) => {
      const gone = removed.has(card.dataset.candidateId);
      card.classList.toggle("is-removed", gone);
      if (!gone) {
        n += 1;
        const num = card.querySelector("[data-candidate-number]");
        if (num) num.textContent = String(n).padStart(2, "0");
      }
    });
    // KPI candidate-count reflects visible candidates
    root.querySelectorAll('[data-kpi-kind="candidate_count"]').forEach((el) => {
      el.textContent = String(n);
    });
    // Ticker items tied only to removed candidates drop out
    root.querySelectorAll("[data-candidate-refs]").forEach((chip) => {
      const refs = chip.dataset.candidateRefs.trim().split(/\s+/).filter(Boolean);
      const orphan = refs.length > 0 && refs.every((id) => removed.has(id));
      chip.style.display = orphan ? "none" : "";
    });
    // Evidence rows owned only by removed candidates dim out
    const alive = new Set();
    cards().forEach((card) => {
      if (removed.has(card.dataset.candidateId)) return;
      card.querySelectorAll("[data-source-link]").forEach((a) => alive.add(a.getAttribute("href")));
    });
    syncTicker();
  }

  function syncTicker() {
    const tape = root.querySelector(".sb-news-tape");
    const primary = tape && tape.querySelector(".sb-news-set:not(.sb-news-dup)");
    if (!tape || !primary) return;
    tape.querySelectorAll(".sb-news-dup").forEach((n) => n.remove());
    const dup = primary.cloneNode(true);
    dup.classList.add("sb-news-dup");
    dup.setAttribute("aria-hidden", "true");
    [dup, ...dup.querySelectorAll("*")].forEach((node) => {
      node.removeAttribute("data-edit-id"); node.removeAttribute("contenteditable");
      node.removeAttribute("spellcheck"); node.removeAttribute("role");
    });
    tape.append(dup);
  }

  function setEdit(on) {
    editing = on;
    document.body.classList.toggle("edit-mode", on);
    fields().forEach((el) => {
      if (on) { el.contentEditable = "true"; el.spellcheck = true; el.setAttribute("role", "textbox"); }
      else { el.removeAttribute("contenteditable"); el.removeAttribute("spellcheck"); el.removeAttribute("role"); }
    });
    cards().forEach((c) => { c.draggable = on; });
    const btn = action("toggle-edit");
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "Finish editing" : "Edit text & logos";
    announce(on ? "Editing enabled" : dirty ? "Unsaved edits" : "View mode · ready");
    if (!on) syncTicker();
  }

  function saveDraft(msg = true) {
    try { localStorage.setItem(storageKey, JSON.stringify(snapshot())); dirty = false;
      if (msg) announce("Draft saved in this browser"); return true; }
    catch { if (msg) announce("Browser storage unavailable · use Download HTML"); return false; }
  }

  function apply(state) {
    if (!state) return;
    if (state.values) fields().forEach((el) => {
      if (Object.hasOwn(state.values, el.dataset.editId)) el.innerHTML = state.values[el.dataset.editId]; });
    if (state.logos) logos().forEach((el) => {
      const v = state.logos[el.dataset.logoSlotId]; if (typeof v === "string" && v) el.innerHTML = v; });
    if (Array.isArray(state.order) && list) {
      state.order.forEach((id) => {
        const card = list.querySelector('.cr-candidate[data-candidate-id="' + CSS.escape(id) + '"]');
        if (card) list.append(card);
      });
    }
    removed.clear();
    (state.removed || []).forEach((id) => removed.add(id));
    reconcile();
  }

  function resetDraft() {
    if (!confirm("Reset all edits to this file's original content?")) return;
    apply({ values: baseline.values, logos: baseline.logos, order: baseline.order, removed: [] });
    try { localStorage.removeItem(storageKey); } catch (e) {}
    dirty = false; announce("Edits reset");
  }

  function removeCandidate(card) {
    const id = card.dataset.candidateId;
    lastRemoved = { id, next: card.nextElementSibling ? card.nextElementSibling.dataset.candidateId : null };
    removed.add(id);
    dirty = true;
    reconcile();
    const undo = action("undo-soft-delete");
    if (undo) { undo.hidden = false; }
    announce("Opportunity removed · Undo available");
  }

  function undoRemove() {
    if (!lastRemoved) return;
    removed.delete(lastRemoved.id);
    lastRemoved = null; dirty = true; reconcile();
    const undo = action("undo-soft-delete");
    if (undo) undo.hidden = true;
    announce("Removal undone");
  }

  function downloadHtml() {
    saveDraft(false); setEdit(false); syncTicker();
    const clone = document.documentElement.cloneNode(true);
    clone.querySelector("body")?.classList.remove("edit-mode");
    clone.querySelectorAll("[contenteditable],[spellcheck]").forEach((el) => {
      el.removeAttribute("contenteditable"); el.removeAttribute("spellcheck"); el.removeAttribute("role"); });
    // Clean export: removed opportunities leave the downloaded file entirely.
    clone.querySelectorAll(".cr-candidate.is-removed").forEach((el) => el.remove());
    clone.querySelectorAll("[data-candidate-refs]").forEach((chip) => {
      if (chip.style.display === "none") chip.remove(); });
    clone.querySelectorAll("[data-editor-status]").forEach((el) => { el.textContent = "View mode · ready"; });
    const blob = new Blob(["<!doctype html>\n" + clone.outerHTML], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = document.documentElement.dataset.downloadName || "Federal_Opportunity_Assessment_EDITABLE.html";
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1200);
    announce("Editable HTML downloaded");
  }

  action("toggle-edit").addEventListener("click", () => setEdit(!editing));
  action("save-draft").addEventListener("click", () => saveDraft());
  action("reset").addEventListener("click", resetDraft);
  action("download-html").addEventListener("click", downloadHtml);
  action("print").addEventListener("click", () => { setEdit(false); requestAnimationFrame(() => window.print()); });
  action("undo-soft-delete")?.addEventListener("click", undoRemove);

  root.addEventListener("input", (e) => {
    if (!e.target.closest("[data-edit-id]")) return;
    dirty = true; announce("Unsaved edits");
  });
  root.addEventListener("click", (e) => {
    const rm = e.target.closest("[data-remove-candidate]");
    if (rm) { e.preventDefault(); removeCandidate(rm.closest(".cr-candidate")); return; }
    if (editing && e.target.closest("a") && !e.target.closest("[data-source-link]")) e.preventDefault();
  });
  // single-line headline enforcement
  root.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.closest("[data-edit-singleline]")) e.preventDefault();
  });
  root.addEventListener("paste", (e) => {
    const f = e.target.closest('[data-edit-id][contenteditable="true"]');
    if (!f) return;
    e.preventDefault();
    let text = e.clipboardData.getData("text/plain");
    if (f.hasAttribute("data-edit-singleline")) text = text.replace(/\s*\n+\s*/g, " ");
    document.execCommand("insertText", false, text);
  });
  // drag-drop image replacement
  root.addEventListener("dragover", (e) => {
    if (!editing) return;
    const t = e.target.closest("[data-logo-slot-id]"); if (!t) return;
    e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    t.classList.add("is-dragover");
  });
  root.addEventListener("dragleave", (e) => {
    const t = e.target.closest("[data-logo-slot-id]");
    if (t && !t.contains(e.relatedTarget)) t.classList.remove("is-dragover");
  });
  root.addEventListener("drop", (e) => {
    if (!editing) return;
    const t = e.target.closest("[data-logo-slot-id]"); if (!t) return;
    e.preventDefault(); t.classList.remove("is-dragover");
    const file = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
    if (!file) { announce("Drop a PNG, JPG, SVG, GIF, or WebP image"); return; }
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const img = document.createElement("img"); img.src = String(reader.result || ""); img.alt = "";
      t.replaceChildren(img); dirty = true; announce("Image replaced · unsaved edits");
    });
    reader.readAsDataURL(file);
  });
  // card reorder via native drag
  let dragCard = null;
  root.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".cr-candidate");
    if (editing && card) { dragCard = card; e.dataTransfer.effectAllowed = "move"; }
  });
  root.addEventListener("dragover", (e) => {
    if (!editing || !dragCard || !list) return;
    const over = e.target.closest(".cr-candidate");
    if (!over || over === dragCard) return;
    e.preventDefault();
    const rect = over.getBoundingClientRect();
    const after = (e.clientY - rect.top) > rect.height / 2;
    list.insertBefore(dragCard, after ? over.nextElementSibling : over);
  });
  root.addEventListener("drop", (e) => { if (dragCard) { dirty = true; reconcile(); dragCard = null; } });

  document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.shiftKey && e.key.toLowerCase() === "e") { e.preventDefault(); setEdit(!editing); }
    else if (mod && editing && e.key.toLowerCase() === "s") { e.preventDefault(); saveDraft(); }
    else if (editing && e.key === "Escape") setEdit(false);
  });
  addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  syncTicker();
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) { apply(JSON.parse(saved)); announce("Saved draft restored"); }
  } catch (e) { announce("View mode · ready"); }
})();
"""
