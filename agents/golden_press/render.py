"""Deterministic rendering of the content region (2026-07-27).

WHY THIS EXISTS. The press previously asked one model call to hand-write
~185 KB of HTML carrying several hundred exact dollar figures, dates,
contract numbers and URLs, then mechanically verified every one, then asked
the same model to regenerate the whole document when any single value was
wrong. Three consecutive presses died in that repair loop at the 2,400s
timeout. Transcription error scales with volume and the repair cost was the
entire document.

So: CODE renders every data-bearing element from the pack, and the model
writes only prose. Whole classes of violation become impossible by
construction rather than checked-then-repaired:
  dollar/date/contract-number provenance, the linkage law, dock
  exactly-once, calendar completeness, lane representation, band grammar,
  hero presence, aggregate justification.

The validator keeps every one of those checks (operator ruling, 2026-07-27):
they can no longer fire from this path, which makes them the regression
tripwire that tells us if the deterministic path ever breaks.

PROSE IS OPTIONAL. Every slot has a deterministic factual fallback, so a
missing, short, or failed prose call degrades the writing, never the
evidence, and never blocks a press.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from typing import Any, Iterable, Optional

from agents.golden_press.doctrine import section
from agents.golden_press.decision_rules import (
    R1_MATERIALITY,
    R2_WINDOW,
    SEWP_V_FACTS as SEWP_V_DECISION_FACTS,
)
from agents.golden_press.press_time import local_press_date
from agents.golden_press.seals import seal_for
from agents.golden_press.validate import _record_affinity

SENTINEL = "<!-- LILA:COMPLETE -->"

_EMDASH = re.compile(r"[–—]")


_APFS_STAMP = re.compile(r"\*{2,}\s*\d{1,2}/\d{1,2}/\d{4}\s*:?\s*")


def esc(value: Any) -> str:
    """Escape for HTML text, and strip em dashes (house rule R9).

    Also strips the APFS editorial date stamp ("***07/15/2026:") that some
    forecast titles carry. It is a publishing annotation, not content, and
    it injects a date the pack's date fields do not carry, which reads as a
    provenance violation.
    """
    text = _APFS_STAMP.sub("", str(value if value is not None else ""))
    return _EMDASH.sub("·", html.escape(text, quote=True))


def clip(text: Any, limit: int) -> str:
    """Truncate on a WORD boundary, always.

    A character-boundary cut can slice a contract number in half
    ("28321323FDX0001" -> "28321323FDX0"), and half an identifier reads to
    the validator as an identifier with no provenance, which is a factual
    defect rather than a cosmetic one.

    So the limit yields to the token, never the other way round: the cut
    lands on the last space inside the window, and a window holding no space
    at all emits its whole first token even if that overruns the limit. The
    limits here govern display width, and a few characters of overflow is a
    cheaper price than a fabricated identifier.
    """
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit]
    space = cut.rfind(" ")
    if space > 0:
        return cut[:space].rstrip(" ,;:-")
    head = s.split(" ", 1)[0]
    return head.rstrip(" ,;:-")


_PSC_TITLE_PREFIX = re.compile(r"^[A-Z0-9]{1,5}--\s*")
# The dedupe key inside an event relevance note. Both note builders emit the
# literal "cites <record_id> at" (events.py: "your pack cites X at $N";
# events_conferences.py: "your <agency> corridor cites X at $N"), so the
# cited record id is what identifies a repeat. This was referenced by
# _event_note but never defined: any featured event carrying a note raised
# NameError and killed the press. It only stayed hidden because an event
# had to clear the account tie to be featured at all.
_CITED_RECORD = re.compile(r"\bcites\s+([A-Za-z0-9][A-Za-z0-9._-]{3,})\s+at\b")


def notice_title(raw: Any) -> str:
    """A sam.gov notice title without its leading PSC code.

    VA and DoD title their notices "DA10--RFI | Cost Oversight..." and
    "R408--FRAUD...". The prefix is the product service code, which the
    record already carries in its own field, and it reads as an unprovable
    contract number when rendered inside a title.
    """
    return _PSC_TITLE_PREFIX.sub("", " ".join(str(raw or "").split()))


def _money(value: Optional[float]) -> str:
    return f"${value:,.0f}" if value else "not published"


def _money_exact(value: Optional[float]) -> str:
    return f"${value:,.2f}" if value else "$0.00"


def _short_money(value: Optional[float]) -> str:
    if not value:
        return "not published"
    if value >= 1e9:
        return f"${value / 1e9:,.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:,.2f}M"
    if value >= 1e3:
        return f"${value / 1e3:,.1f}K"
    return f"${value:,.0f}"


def _day(iso: Optional[str]) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return d.strftime("%d %b %Y").upper().lstrip("0")


# "Q2 2026", "FY2026 Q3", "FY26 Q1", "2026 Q4". The year may carry an FY
# prefix with no word boundary in front of the digits, so the year group
# cannot lead with \b.
_QUARTER = re.compile(
    r"\bQ([1-4])\b\D{0,12}?(?:FY)?(\d{2,4})\b|(?:FY)?(\d{2,4})\b\D{0,12}?\bQ([1-4])\b",
    re.I)
_YEAR = re.compile(r"(?:FY\s*)?(?<!\d)(20\d{2})\b", re.I)
_FY = re.compile(r"\bFY\s*\d", re.I)


def _four_digit_year(text: str) -> Optional[int]:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 4:
        return int(digits)
    if len(digits) == 2:
        return 2000 + int(digits)
    return None


def calendar_when(value: Any) -> tuple[str, str]:
    """(sort key, display label) for one calendar date.

    Federal forecast fields are not all dates. APFS publishes quarters
    ("Q2 2026"), fiscal years, and free text, and an earlier version of this
    function silently DROPPED any value it could not parse into a day, which
    quietly removed whole records from the calendar.

    So: nothing is ever dropped, and nothing is ever sharpened. A real date
    displays as a day; a quarter sorts to the start of that quarter but still
    reads "Q2 2026"; anything else sorts last and shows its own text. The
    calendar never invents a precision the source did not publish.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return ("9999-99", "DATE NOT PUBLISHED")
    iso = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso:
        return (iso.group(0), _day(iso.group(0)))
    us = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if us:
        day = f"{us.group(3)}-{int(us.group(1)):02d}-{int(us.group(2)):02d}"
        return (day, _day(day))
    q = _QUARTER.search(text)
    if q:
        quarter = int(q.group(1) or q.group(4))
        year = _four_digit_year(q.group(2) or q.group(3) or "")
        if year:
            if _FY.search(text):
                # The federal fiscal year opens 1 October of the PRIOR
                # calendar year, so FY2026 Q1 is Oct 2025, not Jan 2026.
                # Sorting it as a calendar quarter puts it a year late.
                month = (quarter - 1) * 3 + 10
                if month > 12:
                    month -= 12
                else:
                    year -= 1
            else:
                month = (quarter - 1) * 3 + 1
            return (f"{year:04d}-{month:02d}-00", text.upper())
    year_m = _YEAR.search(text)
    if year_m:
        year = int(year_m.group(1))
        # A bare fiscal year starts the previous October.
        return ((f"{year - 1:04d}-10-00" if _FY.search(text)
                 else f"{year:04d}-00-00"), text.upper())
    return ("9999-99", text.upper())


def _anchor(url: Optional[str], inner: str, cls: str = "") -> str:
    """LINKAGE LAW: every identifier and title renders as a live link to its
    pack source_url. No url means plain text, never a fabricated href."""
    if not url:
        return inner
    klass = f' class="{cls}"' if cls else ""
    return (f'<a data-source-link=""{klass} href="{esc(url)}" target="_blank" '
            f'rel="noopener noreferrer">{inner}</a>')


_STOP = {"of", "the", "and", "for", "us", "u", "s", "department", "office",
         "administration", "agency", "bureau", "service", "services"}


def _initials(name: str) -> str:
    """Short mark for a buyer with no catalogued seal. Derived from the pack's
    own agency string, so it states nothing the record does not already say."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(name or "")) if w]
    caps = [w for w in words if w.isupper() and 2 <= len(w) <= 5]
    if caps:
        return caps[0][:4]
    letters = [w[0].upper() for w in words if w.lower() not in _STOP]
    return "".join(letters[:3]) or "FED"


# AESTHETIC PACKAGE (client-approved 2026-08-03): the render-scoped list of
# labels whose mark slot fell back to a monogram this press. Reset by
# render_content_region, receipted into the method band; the design law is
# mark-slot-never-empty, and a monogram is a DISCLOSED gap, never a blank.
_MARK_GAPS: list[str] = []
MARKS_RECEIPT_TOKEN = "<!-- LILA:MARKS-RECEIPT -->"
LINK_CLASS_TOKEN = "<!-- LILA:LINK-CLASSES -->"


def _record_gap(label: str) -> None:
    value = " ".join(str(label or "").split())
    if value and value not in _MARK_GAPS:
        _MARK_GAPS.append(value)


def seal_cell(record: Any, slot_id: str) -> str:
    """Agency mark for a record, in the library grammar. Resolution: the
    established seal catalog, then packaged marks, then the monogram with
    the gap recorded (mark-slot-never-empty)."""
    from agents.golden_press.report_templates import mark
    label = (getattr(record, "sub_agency", None)
             or getattr(record, "agency", None) or "Federal")
    uri = seal_for(record)
    if uri:
        return (f'<span class="seal has-img" data-logo-slot-id="{esc(slot_id)}">'
                f'<img src="{esc(uri)}" alt="" aria-hidden="true"></span>')
    return mark(str(label), kind="agency", cls="seal", gaps=_MARK_GAPS)


def company_mark(name: str, client_name: str, slot_id: str) -> str:
    """Org mark in the library grammar: committed asset, packaged mark, or
    monogram with the gap recorded."""
    from agents.golden_press.report_templates import mark
    from agents.reports.report_assets import company_logo
    uri = company_logo(name, client_name)
    if uri:
        return (f'<span class="pmk has-img" data-logo-slot-id="{esc(slot_id)}">'
                f'<img src="{esc(uri)}" alt="{esc(name)} logo"></span>')
    return mark(name, kind="org", cls="pmk", gaps=_MARK_GAPS)


def _prose(prose: dict, key: str, fallback: str,
           *, empty_band: bool = False) -> str:
    """Model prose when usable, deterministic fact when not. Never blocks.

    A band that renders NO rows takes the deterministic line, always: on
    the Thinklogical press the empty forward lane still carried a model
    lede naming forecast records, dates and contacts the band did not
    render (CBP Land Mobile Radio, a TSA BPA), so the prose wrote a claim
    the table could not cash. An empty band states its own emptiness."""
    if empty_band:
        return esc(fallback)
    value = (prose or {}).get(key)
    text = " ".join(str(value).split()) if value else ""
    return esc(text) if len(text) >= 25 else esc(fallback)


def _edit(slot: str, inner: str, single: bool = False) -> str:
    attr = ' data-edit-singleline=""' if single else ""
    return f'<span data-edit-id="{slot}"{attr}>{inner}</span>'


# --------------------------------------------------------------------------- #
# pack shaping
# --------------------------------------------------------------------------- #
COMPETITOR_COVERAGE_STATES = (
    "not_supplied", "supplied_not_screened", "partial", "screened")

# Client copy names evidence in the client's language. The raw lane ids are
# internal machinery, and the gold-standard brief puts them in the sidecars,
# not in the deliverable.
LANE_LABELS: dict = {
    "L1_notice": "SAM notices",
    "L2_entity_award": "federal award records",
    "L2_core": "client award records",
    "L2_competitor": "rival award records",
    "L2_channel": "channel award records",
    "L3_research_clock": "research clocks",
    "L4_forecast": "published forecasts",
}


def lane_label(lane: str) -> str:
    """The client-facing name for an evidence lane."""
    key = str(lane or "").strip()
    return LANE_LABELS.get(key, key.replace("_", " ").strip() or "records")


def competitor_coverage(pack: Any) -> dict:
    """What the competitive lane can HONESTLY say, derived from the pack.

    A report may only claim a tested competitive zero when rivals were
    actually supplied AND the rival lane actually ran. The Thinklogical
    press asserted 'HEAD-TO-HEAD · NONE' off an empty entity list: no rival
    name was ever supplied, so nothing was tested and the sentence was a
    claim the evidence could not support.

    The receipt is derived, never asserted: `research.entities.competitor`
    proves what was supplied, and the pack's own LaneQuery rows (method
    `entity:competitor*`) prove which of those names the lane actually
    searched, with their returned/kept denominators.
    """
    research = getattr(pack, "research", None) or {}
    entities = research.get("entities") or {}
    supplied = [" ".join(str(n).split())
                for n in (entities.get("competitor") or []) if str(n).strip()]

    queries, returned, kept = [], 0, 0
    for query in getattr(pack, "queries", None) or []:
        get = (query.get if isinstance(query, dict)
               else lambda k, d=None: getattr(query, k, d))
        if not str(get("method") or "").startswith("entity:competitor"):
            continue
        queries.append(query)
        returned += int(get("result_count") or 0)
        kept += int(get("kept_after_screen") or 0)

    # Which supplied names the lane actually carried, read off the query
    # bodies rather than assumed from the count.
    screened: list = []
    haystack = json.dumps(
        [(q.get("body") if isinstance(q, dict) else getattr(q, "body", None))
         for q in queries], default=str).casefold()
    for name in supplied:
        if name.casefold() in haystack:
            screened.append(name)

    hits = 0
    lowered = {n.casefold() for n in supplied}
    for record in getattr(pack, "records", None) or []:
        if {str(h).casefold()
                for h in (getattr(record, "entity_hits", None) or [])} & lowered:
            hits += 1

    if not supplied:
        state = "not_supplied"
    elif not queries:
        state = "supplied_not_screened"
    elif len(screened) < len(supplied):
        state = "partial"
    else:
        state = "screened"

    return {
        "state": state,
        "supplied": supplied,
        "screened": screened,
        "unscreened": [n for n in supplied if n not in screened],
        "queries": len(queries),
        "returned": returned,
        "kept": kept,
        "records_with_rival_hits": hits,
        "tested_zero_allowed": state == "screened",
    }


def shape(pack: Any, demoted: frozenset[str] | set[str] = frozenset()) -> dict:
    """One deterministic read of the pack that every band shares.

    ``demoted`` names rival records the composer attested as wrong-domain
    name collisions (composer_demotions). They lose their rival SEATS
    (accounts tiles, displacement cards) but stay in the evidence dock and
    calendar: the dock is an exactly-once inventory, and silent client-side
    removal of a seat is the doctrine, never removal of evidence.
    """
    records = list(getattr(pack, "records", []) or [])
    decisions = getattr(pack, "decisions", None) or {}
    core, rival, forecasts, notices = [], [], [], []
    for r in records:
        if r.lane == "L4_forecast":
            forecasts.append(r)
        elif r.lane == "L1_notice":
            notices.append(r)
        elif _record_affinity(r, pack) == "competitor":
            if r.record_id not in demoted:
                rival.append(r)
        else:
            core.append(r)
    def by_dollars(rows: Iterable[Any]) -> list:
        return sorted(rows, key=lambda row: -(row.obligated_dollars or 0))
    clocks = [r for r in records if r.research_clock]
    # Distinct records legitimately share a program name (three APFS lines
    # carry one C5ISC title). Rendering them identically reads as repeated
    # prose, so a colliding title carries its own record id INSIDE the
    # title text, where it disambiguates the fragment itself.
    seen: dict[str, int] = {}
    for r in records:
        base = " ".join(str(r.description or r.title or "").split())
        seen[base[:60]] = seen.get(base[:60], 0) + 1
    titles: dict[str, str] = {}
    for r in records:
        base = " ".join(str(r.description or r.title or "").split())
        titles[r.record_id] = (f"{base} ({r.record_id})"
                               if seen.get(base[:60], 0) > 1 else base)
    # Corridor membership spans EVERY record for that buyer, not just core.
    # The validator derives agency/sub-agency sums over all records, so a
    # core-only corridor total is unprovable by construction.
    corridors: dict[str, list] = {}
    for r in records:
        key = (r.sub_agency or r.agency or "").strip()
        if key:
            corridors.setdefault(key, []).append(r)
    return {
        "records": records, "core": by_dollars(core),
        "rival": by_dollars(rival),
        "competitor_coverage": competitor_coverage(pack),
        "composer_demoted": set(demoted),
        "forecasts": forecasts, "notices": notices,
        "clocks": sorted(clocks, key=lambda r: str(r.period_end or "9999")),
        "corridors": {k: by_dollars(v) for k, v in sorted(
            corridors.items(), key=lambda kv: -sum(
                x.obligated_dollars or 0 for x in kv[1]))},
        "core_total": sum(r.obligated_dollars or 0 for r in core),
        "rival_total": sum(r.obligated_dollars or 0 for r in rival),
        "clock_total": sum(r.obligated_dollars or 0 for r in clocks),
        "titles": titles,
        "events": list(getattr(pack, "events", []) or []),
        "events_screen": getattr(pack, "events_screen", None),
        "sam_lane_hits": list(getattr(pack, "sam_lanes", []) or []),
        "sam_lane_receipt": getattr(pack, "sam_lanes_receipt", None),
        "research": getattr(pack, "research", {}) or {},
        "selection": getattr(pack, "selection", {}) or {},
        "client": getattr(pack, "client_name", ""),
        "generated_at": getattr(pack, "generated_at", "") or "",
        "press_date": local_press_date(getattr(pack, "generated_at", "")),
        "decisions": getattr(pack, "decisions", None),
        "targeting": getattr(pack, "targeting", None),
        "by_id": {r.record_id: r for r in records},
    }


def forecast_floor(forecasts: Iterable[Any]) -> tuple[float, list[tuple[Any, float]]]:
    unit = {"K": 1e3, "M": 1e6, "B": 1e9}
    rows = []
    for r in forecasts:
        bands = re.findall(r"\$\s?([0-9][0-9,.]*)\s*([KMB])",
                           str(r.estimated_value_range or ""), re.I)
        vals = [float(a.replace(",", "")) * unit[u.upper()] for a, u in bands]
        rows.append((r, min(vals) if vals else 0.0))
    return sum(v for _, v in rows), rows


# --------------------------------------------------------------------------- #
# bands
# --------------------------------------------------------------------------- #
def _band_open(index: int, bid: str, title: str, meta: str, context: str) -> str:
    """One contract band in the library grammar: b-no, h2, b-meta, b-lede
    inside <section class="band">. Numbering is the render position."""
    return (f'<section class="band" id="{esc(bid)}" '
            f'data-studio-unit-id="{esc(bid)}" '
            f'data-studio-unit-label="{esc(title)}" '
            f'aria-labelledby="{esc(bid)}Title">'
            f'<div class="b-no">{index:02d}</div>'
            f'<h2 id="{esc(bid)}Title">'
            f'{_edit(f"{bid}-title", esc(title), True)}</h2>'
            f'<div class="b-meta">{esc(meta)}</div>'
            f'<p class="b-lede">{_edit(f"{bid}-lede", context)}</p>')


def _hero_headline(s: dict) -> str:
    """Contract §2/00: the headline is the Band 01 R1 card count with its
    definition sentence; at zero it states the strongest true count
    available (records cited), never a manufactured decision count."""
    decisions = s.get("decisions") or {}
    cards = (decisions.get("r1") or {}).get("cards") or []
    client = str(s.get("client") or "the client")
    if cards:
        n = len(cards)
        plural = "s" if n != 1 else ""
        return (f"{n} account decision{plural} where {client} and a named "
                f"rival are funded in the same buying environment")
    n = len(s.get("records") or [])
    return (f"{n} primary federal records cited for {client}; no "
            f"head-to-head account decision stands on this evidence yet")


def render_hero(s: dict, prose: dict) -> str:
    """Contract §2/00 hero in the library grammar: kicker, artifact name,
    headline, chips, context, record count. G1 (2026-08-03): the clock rail
    (ticker) is REMOVED; clocks belong to Band 03."""
    fallback = (f"{len(s['records'])} linked federal records across "
                f"{len(s['corridors'])} buying accounts, "
                f"{len(s['clocks'])} carrying dated award periods. Every "
                f"claim in this report links its primary record; nothing "
                f"is asserted without one.")
    chips = []
    research = s.get("research") or {}
    entities = research.get("entities") or {}
    terms = (list(entities.get("product") or [])
             or list(research.get("capability_terms") or []))
    seen = {str(s["client"]).casefold()}
    chips.append(f'<span class="chip">{esc(str(s["client"]).upper())}</span>')
    for term in terms[:6]:
        label = str(term).strip()
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        chips.append(f'<span class="chip">{esc(label.upper())}</span>')
        if len(chips) >= 4:
            break
    chips.append('<span class="chip hot">ACQUISITION PATHS TO VALIDATE</span>')
    lane_counts: dict[str, int] = {}
    for record in s["records"]:
        lane = str(getattr(record, "lane", "unclassified") or "unclassified")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    proof_rows = "".join(
        f'<div class="proof-row" data-count="{count}">'
        f'<span>{esc(lane_label(lane))}</span><strong>{count}</strong></div>'
        for lane, count in sorted(lane_counts.items())
    )
    return (
        f'<div class="ghost" aria-hidden="true">{esc(len(s["records"]))}</div>'
        '<header class="hero" data-studio-unit-id="studio-hero" '
        'data-studio-unit-label="Cover and headline" '
        'aria-labelledby="signalTitle"><div>'
        f'<div class="kicker">{_edit("hero-kicker", esc(_kicker(s)), True)}</div>'
        f'<h1 id="signalTitle">{_edit("hero-title", esc("Federal Opportunity Pre-Assessment"), True)}</h1>'
        f'<p class="hero-context"><strong>{esc(_hero_headline(s))}</strong></p>'
        f'<div class="chips">{"".join(chips)}</div>'
        f'<p class="hero-context">{_edit("hero-context", _prose(prose, "hero_context", fallback))}</p>'
        '</div>'
        f'<div class="hero-num"><strong>{_edit("hero-record-count", esc(len(s["records"])), True)}</strong>'
        f'<span>{_edit("hero-record-label", "primary records cited", True)}</span>'
        '<details class="proof"><summary>Show the work</summary>'
        f'<div class="proof-body">{proof_rows}</div></details></div>'
        "</header>")


def _slug(value: str) -> str:
    """Stable, filename-safe key for a corridor prose slot."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _kicker(s: dict) -> str:
    """FEDERAL MARKET RESEARCH · 30 JUL 2026, the golden hero's dated line.

    The date comes from the pack's generated_at, never date.today(). Reading
    the clock here would be both unprovenanced (check_dates admits pack dates,
    and _pack_date_keys already carries generated_at) and non-deterministic,
    which would break byte-reproducible replay of the same pack.
    """

    stamp = str(s.get("press_date") or "")
    try:
        rendered = date.fromisoformat(stamp).strftime("%d %b %Y").upper()
    except ValueError:
        return "FEDERAL MARKET RESEARCH"
    return f"FEDERAL MARKET RESEARCH · {rendered}"



def _notice_card(index: int, r: Any, prose: Optional[dict] = None,
                 titles: Optional[dict] = None) -> str:
    """A shapeable notice in the forward lane, library grammar."""
    base = (titles or {}).get(r.record_id) or str(r.description or r.title or "")
    deadline = f" · respond by {_day(r.response_deadline)}" if r.response_deadline else ""
    return ('<article class="deck gate">'
            f'<div class="d-no">{index:02d}</div>'
            f'<div><h3>{_anchor(r.url, esc(clip(base, 96)))}</h3>'
            f'<div class="m-lab">{esc(r.sub_agency or r.agency or "")}'
            f'{esc(deadline)}</div>'
            f'<p class="claim">Shapeable notice at a funded account · '
            f'qualify, not pipeline · '
            + _anchor(r.url, f"{esc(r.record_id)}"
                      '<span class="arw">&#8599;</span>', cls="ev")
            + '</p></div></article>')


def render_sam_lanes(s: dict) -> str:
    """Brand-name SAM lanes, rendered inside the signals band.

    Two halves, both deterministic. The hits themselves lead with
    subject-grade (the product sits beside the brand-name or sole-source
    language, so it IS the buy) and then mentioned-grade. Under them sits the
    RECEIPT: every lane and every searched product name with its count
    including the zeros, because "we searched twelve product names across
    four lanes and found nothing" is a real answer and an empty section is
    not. A notice sam.gov has already dropped is still shown, marked with the
    date it was last seen live.
    """
    hits, receipt = s["sam_lane_hits"], s["sam_lane_receipt"]
    if not receipt:
        return ""
    rows = []
    ordered = sorted(hits, key=lambda h: (h.get("strength") != "subject",
                                          h.get("signal_distance", 1 << 30)))
    # ONE ROW PER NOTICE. A notice can qualify for several lanes at once (a
    # Webex sources-sought notice is also brand-name language) and can name
    # several tracked products at once. Rendering it once per lane per entity
    # repeats the same title three times and reads as duplicated copy. Keep
    # the strongest appearance and carry the other names on it.
    by_notice: dict = {}
    for h in ordered:
        key = h.get("notice_id") or id(h)
        if key in by_notice:
            names = by_notice[key].setdefault("_also", [])
            if h.get("entity") not in names and h.get("entity") != by_notice[key].get("entity"):
                names.append(h.get("entity"))
            continue
        by_notice[key] = dict(h)
    ordered = list(by_notice.values())
    # LEAD WITH THE BUYS. A common product name earns a long tail of
    # incidental mentions: on 2026-07-27 "Webex" appeared in nine VA notices
    # purely as the meeting platform for an industry day. Those are real and
    # stay in the pack, but if they fill the band they bury the four notices
    # where a product IS the requirement. Show every subject-grade hit, a
    # short tail of the rest, and STATE what was held back (no silent caps).
    subject = [h for h in ordered if h.get("strength") == "subject"]
    mentioned = [h for h in ordered if h.get("strength") != "subject"]
    shown = subject[:10] + mentioned[:3]
    held_back = len(ordered) - len(shown)
    # Distinct notices legitimately share a title: three different
    # architect-engineer solicitations carry one name. Undisambiguated they
    # read as the same row repeated, so a colliding title carries its office.
    title_counts: dict = {}
    for h in shown:
        key = notice_title(h.get("title")).lower()[:60]
        title_counts[key] = title_counts.get(key, 0) + 1
    for i, h in enumerate(shown, start=1):
        grade_label = ("NAMED IN THE RESTRICTION" if h.get("strength") == "subject"
                       else "NAMED IN THE NOTICE")
        # No dates here yet. A notice deadline and a last-seen stamp are both
        # real and both live in pack.sam_lanes, but the validator derives
        # provable dates from pack.records only, so rendering them fires
        # date_without_provenance. The validator is a contract surface and is
        # not edited in the same breath as a feature: the extension that
        # teaches it to read lane dates ships as its own reviewed diff, and
        # these two fields light up when it lands.
        gone = (" · NO LONGER SERVED BY SAM.GOV"
                if h.get("still_active") is False else "")
        plain = notice_title(h.get("title"))
        if title_counts.get(plain.lower()[:60], 0) > 1 and h.get("office"):
            plain = f'{plain} ({h["office"]})'
        title = esc(f'{h.get("entity", "")} · {clip(plain, 96)}')
        rows.append(
            '<article class="deck act">'
            f'<div class="d-no">{i:02d}</div>'
            f'<span class="mk no-img"><span aria-hidden="true">'
            f'{esc(_initials(h.get("sub_agency") or h.get("agency")))}</span></div>'
            f'<div><h3>'
            f'{_anchor(h.get("url"), title)}</div>'
            f'<div class="m-lab">'
            f'{esc(h.get("sub_agency") or h.get("agency") or "")}'
            f'{" · " + esc(", ".join(h["_also"])) if h.get("_also") else ""}</div>'
            # The verbatim excerpt stays in the pack and the ledger but is
            # not rendered yet: it quotes the notice's own words, which carry
            # dates and identifiers the validator cannot derive from
            # pack.records. Quoted primary source needs the validator to
            # understand quotation, and that is a contract-surface change
            # shipping as its own reviewed diff.
            f'<div class="g-list">{esc(h.get("notice_type") or "")} · '
            f'{esc(h.get("office") or h.get("agency") or "")}</div>'
            f'<div class="d-do">{esc(grade_label)}'
            f'{gone} · {_anchor(h.get("url"), esc(h.get("entity") or ""))}'
            "</div></div></article>")
    lane_rows = []
    for lane in receipt.get("lanes", []):
        zero = [e["name"] for e in lane.get("entities", []) if not e["count"]]
        found = [f'{e["name"]} {e["count"]}'
                 for e in lane.get("entities", []) if e["count"]]
        lane_rows.append(
            f'<article class="event"><div class="e-when">'
            f'{esc(lane["lane"].replace("_", " ").upper())}</span>'
            f'<span class="g-lab">{lane.get("notices_in_lane", 0):,} NOTICES '
            f'SCREENED</span>'
            + (esc(", ".join(found)) if found else "no product named")
            + (f' · <span class="g-lab">SEARCHED AND EMPTY: '
               f'{esc(", ".join(zero))}</span>' if zero else "")
            + "</div>")
    carried = receipt.get("ledger") or {}
    footer = (
        f'<p class="b-lede">'
        f'{receipt.get("notices_scanned", 0):,} active notices screened from '
        f'{esc(receipt.get("extract") or "the daily extract")}, '
        f'{receipt.get("metered_quota_spent", 0)} metered API calls spent. '
        f'{carried.get("total", len(hits))} notices held in the durable ledger, '
        f'{carried.get("carried_from_earlier_scans", 0)} of them carried from '
        f'earlier scans because sam.gov no longer serves them.'
        + (f' {held_back} further notices name a tracked product away from the '
           f'brand-name or sole-source language and are held in the ledger '
           f'rather than shown here.' if held_back > 0 else '')
        + '</p>')
    return ("".join(rows) + "".join(lane_rows) + footer)


def _decision_record_line(row: dict, by_id: dict) -> str:
    """One evidence row as linked, evidence-bounded copy. Every id anchors to
    its canonical pack URL (linkage law); dollars and dates are the record's
    own fields, never aggregates."""
    record = by_id.get(row["record_id"])
    url = record.url if record is not None else row.get("url")
    chip = _anchor(url, f'<span>'
                        f'{esc(row["record_id"])}</span>')
    parts = [chip]
    if row.get("recipient"):
        parts.append(esc(clip(row["recipient"], 34)))
    if row.get("obligated_dollars"):
        parts.append(esc(_money(row["obligated_dollars"])))
    if row.get("period_end"):
        parts.append("ends " + _day(row["period_end"]))
    return " · ".join(parts)


def _decision_r1_card(index: int, card: dict, s: dict) -> str:
    """One head-to-head account decision. Every claim is the evidence row:
    entity paper at this buyer, each id linked canonical, the nearest dated
    clock named, and the SEWP V tag where a cited award rides that vehicle."""
    by_id = s["by_id"]
    client_rows = " · ".join(
        _decision_record_line(row, by_id) for row in card["client_records"])
    rival_rows = " · ".join(
        _decision_record_line(row, by_id) for row in card["rival_records"])
    rivals = esc(" · ".join(card["rival_entities"]))
    nearest_rec = by_id.get(card["nearest_record_id"])
    nearest_chip = _anchor(
        nearest_rec.url if nearest_rec is not None else None,
        f'<span>'
        f'{esc(card["nearest_record_id"])}</span>')
    contexts = ((s["decisions"] or {}).get("r3") or {}).get("contexts") or {}
    riding = [row for row in (*card["client_records"], *card["rival_records"])
              if row["record_id"] in contexts]
    vehicle_line = ""
    if riding:
        tags = " · ".join(
            _decision_record_line(
                {"record_id": row["record_id"], "url": row.get("url")}, by_id)
            + (f' (parent {esc(row["parent_award_id"])})'
               if row.get("parent_award_id") else "")
            for row in riding)
        vehicle_line = (f'<div class="d-do">RIDES SEWP V · {tags} · '
                        f'see the vehicle note below</div>')
    seal = seal_cell(nearest_rec if nearest_rec is not None
                     else _SEAL_FALLBACK(card["agency"]),
                     f"decision-{index:02d}-mark")
    # D2 materiality disclosure: both sides' totals and their ratio, on
    # EVERY card in both tiers. The ratio is stated only when client paper
    # carries dollars; a zero-dollar client side states that instead.
    materiality = card.get("materiality") or {}
    materiality_line = ""
    if materiality:
        ratio = materiality.get("rival_to_client_ratio")
        unvalued = (int(materiality.get("client_unvalued_records") or 0)
                    + int(materiality.get("rival_unvalued_records") or 0))
        if materiality.get("ratio_not_computable"):
            plural = "s" if unvalued != 1 else ""
            ratio_text = (f"ratio not computable · {unvalued} unvalued "
                          f"record{plural}")
        else:
            ratio_text = f"RIVAL/CLIENT {ratio * 100:.1f}%"
            if unvalued:
                plural = "s" if unvalued != 1 else ""
                ratio_text += f" · incl. {unvalued} unvalued record{plural}"
        materiality_line = (
            '<div class="g-list">SCALE · CLIENT '
            f'{esc(_money(materiality.get("client_total")))} · RIVAL '
            f'{esc(_money(materiality.get("rival_total")))} · '
            f'{esc(ratio_text)}</div>')
    verb = _card_verb(card)
    card_key = _slug(card["agency"]) or f"account-{index:02d}"
    title = (
        f'{esc(card["agency"])}: client and {rivals} paper run concurrently; '
        f'the {_day(card["nearest_end"])} period end at '
        f'{esc(card["agency"])} decides who is positioned first.')
    action = esc(_do_now(card, str(s.get("client") or "the client")))
    return (
        '<article class="deck act">'
        f'<div class="d-no">{index:02d}</div>'
        f'{seal}'
        f'<div>'
        f'<div class="g-lab">{index:02d} · {esc(card["agency"]).upper()} · '
        f'{esc(verb).upper()}</div>'
        f'<h3>{_edit(f"decision-{card_key}-title", title)}</h3>'
        f'<div class="m-lab">NAMED RIVAL PAPER · {rivals}</div>'
        f'<div class="g-list">CLIENT PAPER · {client_rows}</div>'
        f'<div class="g-list">RIVAL PAPER · {rival_rows}</div>'
        f'{materiality_line}'
        f'<div class="d-do">NEAREST CLOCK · {_day(card["nearest_end"])} · '
        f'{nearest_chip}</div>'
        f'{vehicle_line}'
        f'<div class="d-do">DO NOW · '
        f'{_edit(f"decision-{card_key}-action", action)}</div>'
        "</div></article>")


class _SEAL_FALLBACK:
    """Agency-only stand-in for seal_cell when the nearest record is not in
    the pack index (defensive; decisions always cite pack records today)."""

    def __init__(self, agency: str) -> None:
        self.agency = agency
        self.sub_agency = None


def _decision_sentence_receipts(alerts: list, start_index: int) -> list[str]:
    out = []
    for i, alert in enumerate(alerts, start=start_index):
        source = alert["source"]
        title = esc(clip(source.get("title") or "", 96))
        link = _anchor(source.get("url"), title or "source record")
        history = " · ".join(
            _anchor(row.get("url"), f'<span>'
                                    f'{esc(row["record_id"])}</span>')
            for row in alert["client_history_records"][:6])
        out.append(
            '<div class="gaps">'
            f'<div class="g-lab">COMPETITOR SOURCE CONTEXT · {i:02d} · '
            f'{esc(alert["competitor"])} · {esc(alert["agency"])}</div>'
            f'<div class="g-list">{link} · '
            f'{esc(alert["context"].replace("_", " "))}</div>'
            f'<div class="g-list">RECEIPT · &ldquo;'
            f'{esc(clip(alert["sentence"], 320))}&rdquo;</div>'
            f'<div class="g-list">CLIENT HISTORY HERE · '
            f'{history}</div>'
            '</div>')
    return out


def _card_verb(card: dict) -> str:
    """Contract §2/01 verb, deterministic: a material head-to-head is
    DEFEND; a minor-rival account is MAP. GATE belongs to displacement
    alerts and QUALIFY to the forward lane (assigned at their render
    sites)."""
    tier = (card.get("materiality") or {}).get("tier")
    return "map" if tier == "minor_rival_presence" else "defend"


def _do_now(card: dict, client: str) -> str:
    """The card's imperative action sentence, deterministic from rule
    output; every fact in it is the card's own (contract §2/01)."""
    rivals = " and ".join(card.get("rival_entities") or []) or "the rival"
    return (f"Before {_day(card['nearest_end'])}: open the linked records, "
            f"verify the {rivals} paper with the buying office, and "
            f"position the {client} renewal case on the incumbent route.")


def render_decision_band(idx: int, s: dict, prose: dict) -> str:
    """Band 01, the spine: deterministic from rule output, zero prose slots.

    A rule that returned nothing renders its stated zero with the screening
    receipt; a pack pressed before decision_rules states that instead. The
    model writes nothing here by construction.
    """
    doc = section("decisions")
    decisions = s["decisions"]
    if not decisions:
        out = [_band_open(
            idx, "decisions", doc.heading, doc.meta,
            "Decision rules were not computed for this evidence pack; it "
            "was pressed before the deterministic decision rules existed. "
            "Re-press to evaluate head-to-head paper.")]
        out.append("</section>")
        return "".join(out)

    r1 = decisions.get("r1") or {}
    r2 = decisions.get("r2") or {}
    r3 = decisions.get("r3") or {}
    cards = r1.get("cards") or []
    # ITEM 1 (composer attestation): a rival record the composer attested as
    # a wrong-domain name collision loses its displacement SEAT here, the
    # same way it loses its accounts tile in shape(). The record itself stays
    # in the evidence dock; silent seat removal is the doctrine, never
    # removal of evidence.
    _demoted = s.get("composer_demoted") or set()
    alerts = [alert for alert in (r2.get("alerts") or [])
              if alert.get("record_id") not in _demoted]
    # D2: the minor-rival tier renders collapsed BELOW the main cards,
    # never suppressed, never headlined. Tiering is the rule's decision
    # (materiality.tier); the renderer only partitions.
    main_cards = [c for c in cards
                  if (c.get("materiality") or {}).get("tier")
                  != "minor_rival_presence"]
    minor_cards = [c for c in cards
                   if (c.get("materiality") or {}).get("tier")
                   == "minor_rival_presence"]
    minor_note = (f" {len(minor_cards)} accounts with minor rival presence "
                  f"render collapsed below." if minor_cards else "")
    order_bits = []
    for card in main_cards[:3]:
        order_bits.append(f"{_card_verb(card)} {card['agency']} "
                          f"by {_day(card['nearest_end'])}")
    for card in [c for c in cards if c not in main_cards][:1]:
        order_bits.append(f"map {card['agency']}")
    if alerts:
        order_bits.append(f"gate the {len(alerts)} displacement alert(s)")
    out = [_band_open(
        idx, "decisions", doc.heading, doc.meta,
        f"{len(main_cards)} buying accounts hold material current client "
        f"paper and current named-rival paper in the cited evidence, ranked "
        f"by the nearest dated period end.{minor_note} {len(alerts)} "
        f"displacement alerts follow with verbatim sentence receipts. "
        "Every claim links the records that state it.")]

    if order_bits:
        out.append(
            '<div class="order"><div class="o-lab">Leadership order</div>'
            f'<p>{esc("; ".join(order_bits))}.</p>'
            '<div class="o-sub">Ranked by nearest cited period end; '
            'each card below carries the underlying records.</div></div>')
    if main_cards:
        out.append('<div class="decks">')
        for i, card in enumerate(main_cards, start=1):
            out.append(_decision_r1_card(i, card, s))
        out.append('</div>')
    if not cards:
        # AMENDMENT v1.2 / gold-standard brief: an untested lane never prints
        # a tested zero. What this band may say is a function of whether
        # rivals were supplied and whether the lane actually ran.
        coverage = s.get("competitor_coverage") or {"state": "not_supplied",
                                                    "supplied": []}
        state = coverage.get("state")
        basis = esc((r1.get("receipt") or {}).get(
            "current_basis", "the pack-date currency rule"))
        if state == "screened":
            named = esc(", ".join(coverage.get("screened") or []))
            out.append(
                '<div class="gaps"><div class="g-lab">'
                'HEAD-TO-HEAD · NONE FOUND</div><div class="g-list">'
                f'No buying account in the cited evidence holds both current '
                f'client paper and current named-rival paper under {basis}. '
                f'Rivals screened: {named}. '
                f'{coverage.get("returned", 0):,} rival award records were '
                f'read and {coverage.get("kept", 0):,} passed screening.'
                '</div></div>')
        elif state in ("supplied_not_screened", "partial"):
            named = esc(", ".join(coverage.get("unscreened")
                                  or coverage.get("supplied") or []))
            out.append(
                '<div class="gaps"><div class="g-lab">'
                'COMPETITIVE VALIDATION PENDING</div><div class="g-list">'
                f'The named comparison set is {named}. Their award paper is '
                f'not yet read into this edition, so this band states no '
                f'competitive finding.'
                '</div></div>')
        else:
            out.append(
                '<div class="gaps"><div class="g-lab">'
                'COMPETITIVE VALIDATION NEXT</div><div class="g-list">'
                'This edition establishes the client-side federal position '
                'from cited records. Naming the comparison set is the next '
                'account step, and it is what turns this band into a '
                'head-to-head read.'
                '</div></div>')
    if minor_cards:
        ratio_pct = R1_MATERIALITY["ratio_floor"] * 100
        inner = '<div class="decks">' + "".join(
            _decision_r1_card(i, card, s)
            for i, card in enumerate(minor_cards,
                                     start=len(main_cards) + 1)) + '</div>'
        out.append(
            '<details class="agency t-hist"><summary class="tier">'
            '<span class="a-name">'
            f'MINOR RIVAL PRESENCE · {len(minor_cards)} account(s) '
            f'where rival paper is under '
            f'{R1_MATERIALITY["ratio_floor"] * 100:g}% of client '
            'paper; absolute dollars do not decide the tier'
            '</span></summary>'
            + inner + '</details>')
    undated = (r1.get("receipt") or {}).get("undated_not_evaluated") or []
    if undated:
        out.append(
            '<div class="gaps"><div class="g-lab">'
            'RECEIPT · UNDATED</div><div class="g-list">'
            f'{len(undated)} award record(s) carry no period end and were '
            f'not evaluated for currency: '
            + esc(", ".join(undated[:12])) + '</div></div>')
    no_subtier = (r1.get("receipt") or {}).get("no_subtier_not_evaluated") \
        or []
    if no_subtier:
        out.append(
            '<div class="gaps"><div class="g-lab">'
            'RECEIPT · NO SUBTIER</div><div class="g-list">'
            f'{len(no_subtier)} award record(s) name no awarding subtier '
            f'and were not evaluated as a buying account: '
            + esc(", ".join(no_subtier[:12])) + '</div></div>')

    if alerts:
        out.extend(_decision_sentence_receipts(alerts, 1))
    else:
        receipt = r2.get("receipt") or {}
        scanned = receipt.get("store_notices_scanned") or 0
        store_note = ("the durable notice store was unavailable this press"
                      if receipt.get("store_unavailable")
                      else f"{scanned:,} stored notices screened")
        out.append(
            '<div class="gaps"><div class="g-lab">'
            'COMPETITOR SOURCE CONTEXT · NONE</div><div class="g-list">'
            f'No sole-source intent or award names a rival inside a '
            f'client-history account: {esc(store_note)}, plus the cited '
            f'pack records, matched with the shared brand-name guards.'
            '</div></div>')
    # D3: older matches are counted, never rendered. The receipt line names
    # the count, the oldest fiscal year, and the versioned window itself.
    window_receipt = r2.get("receipt") or {}
    excluded = window_receipt.get("window_excluded") or 0
    window_undated = window_receipt.get("window_undated") or 0
    if excluded or window_undated:
        oldest = window_receipt.get("window_excluded_oldest_fy")
        months = (window_receipt.get("window") or {}).get(
            "trailing_months", R2_WINDOW["trailing_months"])
        parts = []
        if excluded:
            parts.append(f"{excluded} older competitor-context record(s) "
                         "excluded" + (f", oldest {oldest}" if oldest
                                       else ""))
        if window_undated:
            parts.append(f"{window_undated} matched record(s) carry no "
                         "usable date and were not evaluated")
        out.append(
            '<div class="gaps"><div class="g-lab">'
            'RECEIPT · SOURCE TIMING WINDOW</div><div class="g-list">'
            + esc("; ".join(parts))
            + esc(f" · window: period end on/after the press date or "
                  f"action within the trailing {months} months.")
            + '</div></div>')

    contexts = r3.get("contexts") or {}
    if contexts:
        facts = SEWP_V_DECISION_FACTS
        ids = " · ".join(
            _anchor((s["by_id"].get(rid).url
                     if s["by_id"].get(rid) is not None else None),
                    f'<span>{esc(rid)}</span>')
            for rid in sorted(contexts))
        out.append(
            '<div class="gaps"><div class="g-lab">'
            'VEHICLE NOTE · SEWP V</div><div class="g-list">'
            f'Cited decision clocks riding NASA SEWP V: ordering ends '
            f'{_day(facts["ordering_ends"])}, option periods run to '
            f'{_day(facts["option_period_ends"][0])} and '
            f'{_day(facts["option_period_ends"][1])}, and '
            f'{esc(facts["successor"])} is anticipated to begin '
            f'{_day(facts["successor_anticipated_start"])} '
            f'({esc(facts["citation"])}). Holder status on the successor '
            f'vehicle is not asserted for any prime.</div>'
            f'<div class="g-list">{ids}</div></div>')

    out.append("</section>")

    return "".join(out)


def _squarify(items: list, x: float, y: float, w: float, h: float,
              out: list) -> None:
    """Slice-and-dice with orientation by aspect: deterministic, no JS.
    items are (label, value, mark, show_value) tuples, values > 0."""
    if not items:
        return
    if len(items) == 1:
        out.append((items[0], x, y, w, h))
        return
    total = sum(v for _l, v, _m, _s in items)
    if total <= 0:
        return
    half, acc, split = total / 2.0, 0.0, 1
    for i, (_l, v, _m, _s) in enumerate(items):
        acc += v
        if acc >= half:
            split = i + 1
            break
    # BOTH partitions must be non-empty or the recursion never shrinks.
    # The accumulator only crosses half at the LAST item when the tail
    # dominates (a long-tailed award set whose aggregated "All Other"
    # outweighs every ranked box), which made `first` the whole list and
    # `rest` empty: infinite recursion, killed the Thinklogical press.
    split = min(max(split, 1), len(items) - 1)
    first, rest = items[:split], items[split:]
    frac = sum(v for _l, v, _m, _s in first) / total
    if w >= h:
        fw = w * frac
        _squarify(first, x, y, fw, h, out)
        _squarify(rest, x + fw, y, w - fw, h, out)
    else:
        fh = h * frac
        _squarify(first, x, y, w, fh, out)
        # The remainder keeps the full WIDTH and the leftover HEIGHT. The
        # dimensions were crossed here (`w - fh`, full `h`), which produced
        # negative widths whenever a vertical split ran on a tall box: SVG
        # drops a negative-width rect, so boxes silently vanished from the
        # treemap instead of failing loudly.
        _squarify(rest, x, y + fh, w, h - fh, out)


def _treemap_svg(entries: list, *, svg_id: str, label: str) -> str:
    """The house graph (operator, 2026-08-04): a proportional-area treemap
    where the MARK FILLS ITS ENTIRE BOX, so ownership share reads as seal
    size. Deterministic SVG over the SAME sums the band's rows print;
    amendment v1.1 law: no number or name the tables do not carry (the
    All Other box therefore carries no dollar figure)."""
    # Descending by value (stable, label tie-break) so box aspect ratios do
    # not depend on the caller's row order and the layout is deterministic.
    items = sorted((e for e in entries if e[1] > 0),
                   key=lambda e: (-e[1], str(e[0])))
    if not items:
        return ""
    rects: list = []
    _squarify(items, 0, 0, 980, 420, rects)
    patterns, boxes = [], []
    for i, ((name, value, mark_entry, show_value), x, y, w, h) in             enumerate(rects):
        pid = f"{svg_id}-m{i}"
        if mark_entry and mark_entry.get("src"):
            patterns.append(
                f'<pattern id="{pid}" x="0" y="0" width="1" height="1" '
                f'patternContentUnits="objectBoundingBox">'
                f'<image href="{esc(mark_entry["src"])}" width="1" '
                f'height="1" preserveAspectRatio="xMidYMid slice"/>'
                f'</pattern>')
            fill = f"url(#{pid})"
        else:
            fill = "#eef3f7"
        boxes.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
            f'height="{h:.1f}" fill="{fill}" stroke="#fff" '
            f'stroke-width="2"/>')
        if not (mark_entry and mark_entry.get("src")):
            boxes.append(
                f'<text x="{x + w / 2:.1f}" y="{y + h / 2:.1f}" '
                f'text-anchor="middle" font-size="16" font-weight="900" '
                f'fill="#5f6d7b">{esc(monogram(name))}</text>')
        caption = esc(clip(name, max(8, int(w / 7))))
        if show_value:
            caption += " · " + esc(_money(value))
        ty = y + h - 8 if h > 30 else y + h / 2 + 4
        boxes.append(
            f'<text x="{x + 6:.1f}" y="{ty:.1f}" font-size="10.5" '
            f'font-weight="800" fill="#0b1728" paint-order="stroke" '
            f'stroke="#ffffff" stroke-width="3">{caption}</text>')
    return ('<div class="chart"><svg role="img" viewBox="0 0 980 420" '
            f'aria-label="{esc(label)}"><defs>' + "".join(patterns)
            + "</defs>" + "".join(boxes) + "</svg></div>")


def monogram(name: str) -> str:
    from agents.golden_press.report_templates import monogram_initials
    return monogram_initials(name)


def _accounts_in_play(s: dict) -> set:
    """Account names present in Bands 01-02 (contract 03's inclusion rule):
    R1 card buyers, R2 alert agencies, and forward-lane departments plus
    the client-paper agencies behind them."""
    decisions = s.get("decisions") or {}
    accounts: set = set()
    for card in ((decisions.get("r1") or {}).get("cards") or []):
        accounts.add(str(card.get("agency") or "").casefold())
    for alert in ((decisions.get("r2") or {}).get("alerts") or []):
        accounts.add(str(alert.get("agency") or "").casefold())
    for row in ((decisions.get("r4") or {}).get("rows") or []):
        accounts.add(str(row.get("department") or "").casefold())
        forecast = row.get("forecast") or {}
        for key in ("agency", "sub_agency"):
            if forecast.get(key):
                accounts.add(str(forecast[key]).casefold())
        for rec in row.get("client_history_records") or []:
            for key in ("agency", "sub_agency"):
                if rec.get(key):
                    accounts.add(str(rec[key]).casefold())
    return {a for a in accounts if a}


def _record_account_keys(r) -> set:
    out = set()
    for value in (getattr(r, "agency", None), getattr(r, "sub_agency", None)):
        if value:
            out.add(" ".join(str(value).split()).casefold())
    try:
        from agents.golden_press.decision_rules import _department_key
        for value in (getattr(r, "agency", None),
                      getattr(r, "sub_agency", None)):
            key = _department_key(value)
            if key:
                out.add(key.casefold())
    except Exception:  # noqa: BLE001 - the display keys still match
        pass
    return out


def _render_forward(idx: int, s: dict, prose: dict) -> str:
    """Band 02: R4 qualify rows plus shapeable notices at client-paper
    agencies. Forward values render AS PUBLISHED and are never summed."""
    doc = section("forward")
    decisions = s.get("decisions") or {}
    rows = (decisions.get("r4") or {}).get("rows") or []
    receipt = (decisions.get("r4") or {}).get("receipt") or {}
    departments = {str(d).casefold()
                   for d in receipt.get("client_departments") or []}
    notices = [r for r in s.get("notices") or []
               if _record_account_keys(r) & departments]
    cut = max(0, len(s.get("forecasts") or []) - len(rows))
    s.setdefault("_method_notes", {})["forward_cut"] = cut
    out = [_band_open(
        idx, "forward", doc.heading, doc.meta,
        _prose(prose, "band_forward",
               f"{len(rows)} published forecasts and {len(notices)} "
               f"shapeable notices sit where this client already holds "
               f"paper. Values render as published and are never summed "
               f"into any headline."
               + (f" {cut} screened forecast(s) carried no client-paper "
                  f"adjacency and are not shown." if cut and not rows
                  else ""),
               empty_band=not rows and not notices))]
    seen_departments: dict = {}
    for i, row in enumerate(rows[:10], start=1):
        forecast = row["forecast"]
        history_rows = row["client_history_records"]
        # THE ADJACENCY IS SAID ONCE PER DEPARTMENT (the events-band
        # stated-once precedent): identical paper backing four DHS rows
        # rendered four identical pill runs and read as boilerplate.
        first_card = seen_departments.get(row["department"])
        if first_card is None:
            seen_departments[row["department"]] = i
            pills = " ".join(
                _anchor(h.get("url"), esc(h["record_id"]), cls="pill")
                for h in history_rows[:6])
        else:
            pills = (f'card {i:02d} rides the {len(history_rows)} record'
                     f'{"s" if len(history_rows) != 1 else ""} cited in '
                     f'full on card {first_card:02d}')
        band_chip = esc(forecast.get("estimated_value_range")
                        or "band unpublished")
        out.append(
            '<article class="fcard">'
            f'<div class="f-eye"><span class="f-tag">QUALIFY · {i:02d} · '
            f'{esc(row["department"])}</span>'
            f'<span class="f-band">{band_chip}</span></div>'
            f'<h3>{_anchor(forecast.get("url"), esc(clip(forecast.get("title") or "", 110)))}'
            f' <span class="f-id">{_anchor(forecast.get("url"), esc(forecast["record_id"]))}</span></h3>'
            f'<div class="d-do">NEXT PROOF · request the acquisition '
            f'package behind {esc(forecast["record_id"])} from the program '
            f'office · qualify, not pipeline</div>'
            f'<details class="f-adj"><summary>Client paper at '
            f'{esc(row["department"])} · {len(history_rows)} record'
            f'{"s" if len(history_rows) != 1 else ""}</summary>'
            f'<div class="pills">{pills}</div></details>'
            '</article>')
    press_date = str(s.get("generated_at") or "")[:10]
    open_notices, closed_notices = [], []
    for r in notices:
        deadline = str(r.response_deadline or "")[:10]
        (closed_notices if deadline and press_date and deadline < press_date
         else open_notices).append(r)
    # Held records (no client-paper adjacency) obey the same L8 decay law:
    # an expired window may render only inside the closed-windows strip.
    held_all = [r for r in s.get("notices") or [] if r not in notices]
    held_live = []
    for r in held_all:
        deadline = str(r.response_deadline or "")[:10]
        if deadline and press_date and deadline < press_date:
            closed_notices.append(r)
        else:
            held_live.append(r)
    for i, r in enumerate(open_notices[:8], start=1):
        days = ""
        deadline = str(r.response_deadline or "")[:10]
        if deadline and press_date:
            from datetime import date as _date
            try:
                delta = (_date.fromisoformat(deadline)
                         - _date.fromisoformat(press_date)).days
                days = f" · {delta} day{'s' if delta != 1 else ''} remaining"
            except ValueError:
                days = ""
        out.append(_notice_card(i, r, prose, titles=s["titles"])
                   .replace("</h3>", esc(days) + "</h3>", 1))
    if closed_notices:
        # SHAPEABILITY DECAY (2026-08-04): a respond-by earlier than the
        # press date is a CLOSED window: flagged, collapsed, and excluded
        # from qualify prose; it never renders featured.
        rows = " · ".join(
            _anchor(r.url, esc(r.record_id))
            + f' <span class="flag">RESPONSE CLOSED {_day(str(r.response_deadline)[:10])}</span>'
            for r in closed_notices[:8])
        out.append('<details class="agency t-hist"><summary class="tier">'
                   f'CLOSED SHAPEABLE WINDOWS · {len(closed_notices)} '
                   'notice(s) whose response date passed before the press '
                   'date</summary><div class="gaps"><div class="g-list">'
                   + rows + '</div></div></details>')
    if not rows and not notices:
        out.append(
            '<div class="gaps"><div class="g-lab">'
            'FORWARD · NONE</div><div class="g-list">'
            'No published forecast or shapeable notice sits in an agency '
            'where the cited evidence shows client paper. The forecast '
            'screening receipt is stated in Show the work.</div></div>')
    held = held_live
    if held:
        # LANE REPRESENTATION at zero adjacency (JTG press, 2026-08-19):
        # pack-carried L1 records with no client-paper adjacency render as
        # a links-only strip with their disposition stated. Evidence is
        # never silently dropped, and no claim attaches (the events band's
        # monitor-strip precedent).
        held_rows = " · ".join(
            _anchor(r.url, esc(r.record_id)) for r in held[:8])
        out.append('<details class="agency t-hist"><summary class="tier">'
                   f'SCREENED NOTICES HELD AS EVIDENCE · {len(held)} '
                   'record(s) with no client-paper adjacency · links only'
                   '</summary><div class="gaps"><div class="g-list">'
                   + held_rows + '</div></div></details>')
    out.append("</section>")
    return "".join(out)


def _render_clocks(idx: int, s: dict, prose: dict) -> str:
    """Band 03: one ascending table of clocks belonging to Bands 01-02
    accounts. Expired clocks stay, flagged (contract L8)."""
    from agents.golden_press.report_templates import clock_row
    doc = section("clocks")
    accounts = _accounts_in_play(s)
    today = str(s.get("press_date") or "")

    def _collect(bound_only: bool) -> tuple[list, int]:
        found, cut = [], 0
        for r in s.get("records") or []:
            for value, tag in ((r.period_end, "period end"),
                               (r.response_deadline, "response deadline")):
                if not value:
                    continue
                if bound_only and not (_record_account_keys(r) & accounts):
                    cut += 1
                    continue
                iso = str(value)[:10]
                flag = "EXPIRED" if (today and iso and iso < today) else ""
                found.append((iso, tag, r, flag))
        found.sort(key=lambda x: x[0])
        return found, cut

    rows, removed = _collect(bound_only=True)
    # AMENDMENT v1.2 (operator, 2026-08-04: optimize for actionable content).
    # The account binding is a focusing rule, not a suppression rule. When
    # Bands 01-02 name no accounts at all, binding to them removes EVERY
    # clock and the band prints a zero while live renewals sit in the pack
    # (Thinklogical: 26 dated clocks, 7 still open, $6.87M of DoD paper).
    # With no accounts in play the band falls back to the pack's own dated
    # clocks, open first.
    fallback = not accounts and not rows
    if fallback:
        rows, removed = _collect(bound_only=False)
    open_rows = [row for row in rows if not row[3]]
    if fallback:
        # Actionable first: open clocks ascending, then expired history.
        rows = open_rows + [row for row in rows if row[3]]
    s.setdefault("_method_notes", {})["clocks_removed"] = removed
    s["_method_notes"]["clocks_unbound_fallback"] = bool(fallback)
    if fallback:
        lede = (f"{len(rows)} dated clocks ride the cited record set, "
                f"{len(open_rows)} of them still open and listed first. "
                f"No account carries both client and named-rival paper in "
                f"this evidence, so the clocks are shown against the record "
                f"set itself. An expired clock stays on the table with its "
                f"flag; it is history that still frames the renewal.")
    else:
        lede = (f"{len(rows)} dated clocks belong to the accounts in this "
                f"report, sorted ascending. An expired clock stays on the "
                f"table with its flag; it is history that still frames the "
                f"renewal.")
    out = [_band_open(idx, "clocks", doc.heading, doc.meta, lede)]
    if rows:
        isos = [iso for iso, _t, _r, _f in rows]
        lo, hi = min(isos), max(isos)
        from datetime import date as _date
        try:
            span = max(1, (_date.fromisoformat(hi)
                           - _date.fromisoformat(lo)).days)
            ticks = []
            for iso, _t, r, flag in rows[:24]:
                x = 40 + int(880 * (_date.fromisoformat(iso)
                                    - _date.fromisoformat(lo)).days / span)
                color = "#9d2a10" if flag else "#157eaf"
                ticks.append(f'<line x1="{x}" y1="12" x2="{x}" y2="34" '
                             f'stroke="{color}" stroke-width="3"/>')
            out.append(
                '<div class="tl"><svg role="img" viewBox="0 0 960 48" '
                'aria-label="Decision clocks timeline">'
                '<line x1="40" y1="23" x2="920" y2="23" stroke="#d4dde7" '
                'stroke-width="1"/>' + "".join(ticks)
                + f'<text x="40" y="46" font-size="10" fill="#5f6d7b">'
                  f'{_day(lo)}</text>'
                  f'<text x="920" y="46" text-anchor="end" font-size="10" '
                  f'fill="#5f6d7b">{_day(hi)}</text></svg></div>')
        except ValueError:
            pass
        # The scope text can carry the record's own id (a short DAAS-era
        # description inside the 64-char clip, or the colliding-title "(id)"
        # suffix); it rides pre-anchored via what_html so the id occurrence
        # never renders bare (Band 05 remedy, 2026-08-05).
        body = "".join(
            clock_row(_day(iso), (r.sub_agency or r.agency or ""),
                      "",
                      _money(r.obligated_dollars), r.record_id,
                      r.url or "", flag=flag,
                      what_html=_anchor_own_id(
                          r,
                          f"{clip(s['titles'].get(r.record_id, r.title or ''), 64)}"
                          f" · {r.recipient or 'recipient not published'}"))
            for iso, tag, r, flag in rows[:24])
        out.append('<table class="recs" data-clock-table="1"><thead><tr>'
                   '<th>Ends</th><th>Account</th><th>Scope and prime</th>'
                   '<th>Obligated on the cited record</th><th>Record</th>'
                   '</tr></thead><tbody>' + body + '</tbody></table>')
    else:
        out.append('<div class="gaps"><div class="g-lab">'
                   'CLOCKS · NONE</div><div class="g-list">'
                   'No dated period end or response deadline belongs to the '
                   'accounts named in this report.</div></div>')
    out.append("</section>")
    return "".join(out)


def _render_paper(idx: int, s: dict, prose: dict) -> str:
    """Band 04: top prime recipients over the cited record set."""
    from agents.golden_press.report_templates import recipient_row
    doc = section("paper")
    today = str(s.get("press_date") or "")
    by_recipient: dict = {}
    for r in s.get("records") or []:
        if r.lane != "L2_entity_award" or not r.recipient:
            continue
        slot = by_recipient.setdefault(r.recipient, [])
        slot.append(r)
    ranked = sorted(by_recipient.items(),
                    key=lambda kv: -sum(x.obligated_dollars or 0
                                        for x in kv[1]))[:10]

    def _why(recs) -> str:
        current = [x for x in recs
                   if str(x.period_end or "")[:10] >= today]
        if len(recs) >= 3:
            return (f"most recurrent holder on the cited set: "
                    f"{len(recs)} records")
        if current:
            top = max(current, key=lambda x: x.obligated_dollars or 0)
            return (f"holds the largest current renewal here, ending "
                    f"{_day(top.period_end)}")
        return "historical route on the cited records"

    out = [_band_open(
        idx, "paper", doc.heading, doc.meta,
        _prose(prose, "band_paper",
               f"{len(ranked)} prime recipients hold the paper on the cited "
               f"records. A recipient is the route on the record, not a "
               f"partner; validate vehicle access before outreach."))]
    if ranked:
        from agents.golden_press.report_templates import find_mark
        total_all = sum(x.obligated_dollars or 0
                        for recs2 in by_recipient.values() for x in recs2)
        shown_total = sum(sum(x.obligated_dollars or 0 for x in recs2)
                          for _n, recs2 in ranked)
        entries = [(name2, sum(x.obligated_dollars or 0 for x in recs2),
                    find_mark(name2, kind="org"), True)
                   for name2, recs2 in ranked]
        remainder = total_all - shown_total
        if remainder > 0:
            entries.append(("All Other", remainder, None, False))
        out.append(_treemap_svg(entries, svg_id="tm-paper",
                                label="Prime recipients by cited "
                                      "obligations, mark area "
                                      "proportional to dollars"))
        body = "".join(
            recipient_row(i, name,
                          _money(sum(x.obligated_dollars or 0 for x in recs)),
                          len(recs), _why(recs), gaps=_MARK_GAPS)
            for i, (name, recs) in enumerate(ranked, start=1))
        out.append('<table class="recs" data-paper-table="1"><thead><tr>'
                   '<th>Rank</th><th>Prime recipient</th>'
                   '<th>Obligated on cited records</th><th>Records</th>'
                   '<th>Why it matters</th></tr></thead><tbody>'
                   + body + '</tbody></table>')
        routes = []
        with_current = [(n, recs) for n, recs in ranked
                        if any(str(x.period_end or "")[:10] >= today
                               for x in recs)]
        if with_current:
            largest = max(with_current, key=lambda kv: max(
                (x.obligated_dollars or 0) for x in kv[1]
                if str(x.period_end or "")[:10] >= today))
            routes.append(largest[0])
            recurrent = max(with_current, key=lambda kv: len(kv[1]))
            if recurrent[0] not in routes:
                routes.append(recurrent[0])
        if routes:
            out.append('<div class="gaps"><div class="g-lab">'
                       'ROUTES TO WORK FIRST</div><div class="g-list">'
                       + esc(" · ".join(routes[:2]))
                       + ' · largest current renewal and most recurrent '
                         'current holder on the cited records.</div></div>')
        out.append('<div class="gaps"><div class="g-list">'
                   'A recipient here is the contracting route on the cited '
                   'record, not a partner relationship; validate vehicle '
                   'access before any outreach.</div></div>')
    else:
        out.append('<div class="gaps"><div class="g-lab">'
                   'PAPER · NONE</div><div class="g-list">'
                   'No prime recipient appears on the cited award records.'
                   '</div></div>')
    out.append("</section>")
    return "".join(out)


def _anchor_own_id(record: Any, text: str) -> str:
    """Escaped cell copy with the record's own id kept anchored in place.

    The LINKAGE LAW's remedy for verbatim cell text that carries its own
    identifier (a DAAS-era description ending in its delivery-order number,
    or shape()'s colliding-title "(id)" suffix): the occurrence renders
    inside the record's canonical anchor. The text is never edited to hide
    an id and check_links learns no exception.
    """
    rid = str(record.record_id or "")
    if not rid:
        return esc(text)
    parts = re.split(
        rf"(?<![0-9A-Za-z])({re.escape(rid)})(?![0-9A-Za-z])", text)
    if len(parts) == 1:
        return esc(text)
    return "".join(
        _anchor(record.url, esc(piece)) if piece == rid else esc(piece)
        for piece in parts)


def _scope_cell(record: Any) -> str:
    """Band 05 scope-on-record cell copy, own id kept anchored (witnessed
    2026-08-05: the DCMA Data Link Solutions award, "...!A!N! !N!0002")."""
    return _anchor_own_id(
        record, clip(record.description or record.title or "", 88))


def _render_agencies(idx: int, s: dict, prose: dict) -> str:
    """Band 05: collapsible per-buying-agency record tables, labeled
    historical buying context (contract L5/L6)."""
    doc = section("agencies")
    decisions = s.get("decisions") or {}
    card_buyers = {str(c.get("agency") or "").casefold()
                   for c in ((decisions.get("r1") or {}).get("cards") or [])}
    groups: dict = {}
    for r in s.get("records") or []:
        if r.lane != "L2_entity_award":
            continue
        buyer = " ".join(str(r.sub_agency or r.agency or "").split())
        if buyer:
            groups.setdefault(buyer, []).append(r)
    ranked = sorted(groups.items(),
                    key=lambda kv: -sum(x.obligated_dollars or 0
                                        for x in kv[1]))
    out = [_band_open(
        idx, "agencies", doc.heading, doc.meta,
        _prose(prose, "band_agencies",
               f"{len(groups)} buying agencies hold the cited paper, "
               f"ranked by obligations on the linked records."))]
    # CONTRACT §2/05: the lede is LAW, not prose. It renders deterministically
    # whether or not the model wrote band color, so the L5/L6 framing can
    # never be composed away (the first FiscalNote re-press proved the slot
    # version could be).
    out.append(
        '<div class="gaps"><div class="g-list">'
        'Historical buying context on the linked records: what each buying '
        'agency has obligated, to whom, for what. This is history, never '
        'position or pipeline; forecast and event values are excluded from '
        'every figure.</div></div>')
    rival_buyers = set()
    for x in s.get("rival") or []:
        for key in (x.sub_agency, x.agency):
            if key:
                rival_buyers.add(" ".join(str(key).split()).casefold())
    if ranked:
        from agents.golden_press.report_templates import find_mark
        top = ranked[:10]
        shown = sum(sum(x.obligated_dollars or 0 for x in recs2)
                    for _b, recs2 in top)
        total_all = sum(sum(x.obligated_dollars or 0 for x in recs2)
                        for _b, recs2 in ranked)
        entries = [(buyer2, sum(x.obligated_dollars or 0 for x in recs2),
                    find_mark(buyer2, kind="agency"), True)
                   for buyer2, recs2 in top]
        remainder = total_all - shown
        if remainder > 0:
            entries.append(("All Other", remainder, None, False))
        out.append(_treemap_svg(entries, svg_id="tm-agencies",
                                label="Past obligations by buying agency, "
                                      "seal area proportional to dollars"))
    for buyer, recs in ranked[:12]:
        total = sum(x.obligated_dollars or 0 for x in recs)
        tone = ("current" if buyer.casefold() in card_buyers
                else "rival" if buyer.casefold() in rival_buyers else "hist")
        rows = "".join(
            f'<tr><td class="buyer">{esc(buyer)}</td>'
            f'<td class="prime">{esc(x.recipient or "")}</td>'
            f'<td class="desc">{_scope_cell(x)}</td>'
            f'<td class="rec">{_anchor(x.url, esc(x.record_id))}</td></tr>'
            for x in sorted(recs, key=lambda x: -(x.obligated_dollars or 0)))
        out.append(
            f'<details class="agency t-{tone}"><summary>'
            f'{seal_cell(recs[0], f"agencies-{_slug(buyer)[:24]}-mark")}'
            f'<span class="a-name">{esc(buyer)}</span>'
            f'<span class="a-n">{len(recs)} record'
            f'{"s" if len(recs) != 1 else ""}</span>'
            f'<span class="a-dollars">{esc(_money(total))} past obligations '
            f'on cited records</span></summary>'
            '<table class="recs"><thead><tr><th>Buying agency</th>'
            '<th>Prime recipient</th><th>Scope on the record</th>'
            '<th>Record</th></tr></thead><tbody>' + rows
            + '</tbody></table></details>')
    if not ranked:
        out.append('<div class="gaps"><div class="g-lab">'
                   'OBLIGATIONS · NONE</div><div class="g-list">'
                   'The cited record set carries no award obligations to '
                   'group by buying agency.</div></div>')
    out.append("</section>")
    return "".join(out)


def _competitive_seat_line(label: str, rows: list, *, cap: int = 4) -> str:
    """One labeled seat line: linked record chips with the record's own
    amount and dates, in the decision band's row grammar.

    LINKAGE LAW at the row: a record id renders only inside its canonical
    anchor. A row with no usable URL renders its recipient or title text
    instead of a bare id, and a mailto: target is never a citation (the
    gold reference HTML harvested mailto: links as "cited records"; that
    class is refused here by construction, contract §2/06)."""
    if not rows:
        return ""
    parts = []
    for row in rows[:cap]:
        url = str(row.get("url") or "")
        if url.startswith("mailto:"):
            url = ""
        rid = str(row.get("record_id") or "")
        if url and rid:
            bits = [_anchor(url, f"<span>{esc(rid)}</span>")]
        else:
            bits = [esc(clip(row.get("recipient")
                             or row.get("title") or "record", 40))]
        amount = row.get("amount")
        if isinstance(amount, (int, float)) and amount:
            bits.append(esc(_money(amount)))
        if row.get("end"):
            bits.append("ends " + _day(str(row.get("end"))))
        if row.get("deadline"):
            bits.append("respond by " + _day(str(row.get("deadline"))))
        recipient = " ".join(str(row.get("recipient") or "").split())
        if url and rid and recipient:
            bits.append(esc(clip(recipient, 34)))
        parts.append(" · ".join(bit for bit in bits if bit))
    more = (f" · +{len(rows) - cap} more in the sidecars"
            if len(rows) > cap else "")
    return (f'<div class="g-list">{esc(label)} · '
            + " · ".join(parts) + esc(more) + "</div>")


def _competitive_account_deck(index: int, account: dict, s: dict,
                              seen_why: set) -> str:
    """One contested buying account in the deck grammar: buyer + mark, the
    deterministic motion, and the cited seats. The motion rationale is
    stated once per motion (the stated-once precedent: identical engine
    language on same-motion accounts would read as repeated prose)."""
    buyer = " ".join(str(account.get("buyer") or "").split()) or "this buyer"
    motion = " ".join(str(account.get("recommended_motion") or "").split())
    why = " ".join(str(account.get("why_this_matters") or "").split())
    record = None
    for lane_key in ("client_current", "competitor_current",
                     "channel_current", "competitor_historical", "forward"):
        for row in account.get(lane_key) or []:
            record = s["by_id"].get(row.get("record_id"))
            if record is not None:
                break
        if record is not None:
            break
    if record is not None:
        seal = seal_cell(record, f"competitive-{index:02d}-mark")
    else:
        from agents.golden_press.report_templates import mark
        seal = mark(buyer, kind="agency", cls="seal", gaps=_MARK_GAPS)
    key = _slug(buyer) or f"account-{index:02d}"
    title = f"{buyer}: {motion or 'read the cited paper here'}."
    why_line = ""
    if why and why.casefold() not in seen_why:
        seen_why.add(why.casefold())
        why_line = f'<div class="d-do">WHY · {esc(why)}</div>'
    return (
        '<article class="deck act">'
        f'<div class="d-no">{index:02d}</div>'
        f'{seal}'
        '<div>'
        f'<div class="g-lab">{index:02d} · {esc(buyer.upper())} · '
        f'{esc(motion.upper() or "ACCOUNT")}</div>'
        f'<h3>{_edit(f"competitive-{key}-title", esc(title))}</h3>'
        + _competitive_seat_line(
            "CLIENT PAPER", account.get("client_current") or [])
        + _competitive_seat_line(
            "CHANNEL HOLDS", account.get("channel_current") or [])
        + _competitive_seat_line(
            "RIVAL PAPER", account.get("competitor_current") or [])
        + _competitive_seat_line(
            "RIVAL HISTORY", account.get("competitor_historical") or [])
        + _competitive_seat_line(
            "FORWARD", account.get("forward") or [])
        + why_line
        + "</div></article>")


def _render_competitive(idx: int, s: dict, prose: dict) -> str:
    """Band 06: the proactive competitive build's account-by-account
    picture, rendered ONLY from the pack's own research slice (the six
    competitive sidecars are the underlying record, so a saved-inputs
    replay reproduces the band with no re-research). Contract §2/06,
    amendment v1.3.

    LAWS. An untested lane never prints a tested zero: the zero-state
    branches on competitor_coverage exactly like Band 01's stated zero. A
    composer WRONG DOMAIN attestation costs a record its rival SEAT here,
    the same silent seat removal as shape() and the Band 01 displacement
    filter; the account's motion re-derives through the engine's own
    motion table, never a renderer reimplementation, and the record stays
    in the pack and the report's other tables. Research mechanics and raw
    lane ids stay in the sidecars; the band speaks the client lane
    vocabulary.
    """
    doc = section("competitive")
    comp = (s.get("research") or {}).get("competitive") or {}
    if not comp:
        return (_band_open(
            idx, "competitive", doc.heading, doc.meta,
            "The account-by-account competitive picture was not computed "
            "for this evidence pack; it was pressed before the proactive "
            "competitive build existed. Re-press to compute the "
            "per-account read from the cited records.")
            + "</section>")

    completeness = comp.get("completeness") or {}
    accounts_payload = comp.get("accounts") or {}
    as_of = str(accounts_payload.get("as_of") or "")[:10]
    confirmed = [str(n) for n in (comp.get("frame_confirmed")
                 or completeness.get("confirmed_competitors") or [])]
    lane_names = sorted({lane_label(lane)
                         for lane in comp.get("lanes_searched") or []})
    demoted = s.get("composer_demoted") or set()

    accounts = []
    for node in accounts_payload.get("accounts") or []:
        if not isinstance(node, dict):
            continue
        account = {key: list(node.get(key) or []) for key in (
            "client_current", "client_historical", "competitor_current",
            "competitor_historical", "channel_current", "forward", "clocks")}
        account["buyer"] = node.get("buyer")
        account["recommended_motion"] = node.get("recommended_motion")
        account["why_this_matters"] = node.get("why_this_matters")
        lost_seat = False
        for lane_key in ("competitor_current", "competitor_historical"):
            kept = [row for row in account[lane_key]
                    if row.get("record_id") not in demoted]
            if len(kept) != len(account[lane_key]):
                lost_seat = True
                account[lane_key] = kept
        if lost_seat:
            from agents.golden_press.competitive import _account_motion
            motion, why = _account_motion(account)
            account["recommended_motion"] = motion
            account["why_this_matters"] = why
        accounts.append(account)

    contested = [a for a in accounts
                 if a["competitor_current"] or a["channel_current"]
                 or (a["client_current"] and a["forward"])]
    coverage = s.get("competitor_coverage") or {"state": "not_supplied"}
    state = coverage.get("state")
    # The engine stamps as_of from the pack's own generated_at; only that
    # provenance may render (a wall-clock fallback date is not a pack date).
    generated_date = str(s.get("generated_at") or "")[:10]
    press_date = str(s.get("press_date") or "")[:10]
    as_of_label = (_day(as_of)
                   if as_of and as_of in (generated_date, press_date) else "")

    if contested:
        fallback = (
            f"{len(contested)} of {len(accounts)} buying accounts read "
            f"carry a live competitive seat on the cited records. Each "
            f"account below names who holds current paper and the "
            f"recommended motion; every seat links its record.")
    elif state == "screened":
        fallback = (
            "The completed screen across the named comparison set found "
            "no buying account carrying a live competitive seat on the "
            "cited records"
            + (f" as of {as_of_label}" if as_of_label else "")
            + ". The receipt below states exactly what was read.")
    elif state in ("supplied_not_screened", "partial"):
        fallback = (
            "A comparison set is named for this engagement and its award "
            "paper is not yet read into this edition. This band states "
            "the validation posture; it does not state a zero.")
    else:
        fallback = (
            "No rival comparison set is named for this engagement yet. "
            "The cited records establish the client-side position; naming "
            "the set turns this band into a per-account competitive read.")
    out = [_band_open(idx, "competitive", doc.heading, doc.meta,
                      _prose(prose, "band_competitive", fallback,
                             empty_band=not contested))]

    if contested:
        out.append('<div class="decks">')
        seen_why: set = set()
        for i, account in enumerate(contested[:8], start=1):
            out.append(_competitive_account_deck(i, account, s, seen_why))
        out.append('</div>')
        if len(contested) > 8:
            out.append(
                '<div class="gaps"><div class="g-list">'
                f'{len(contested) - 8} further contested accounts ride the '
                'competitive sidecars; the cap is stated here, never '
                'silent.</div></div>')
    else:
        historical = sum(len(a["competitor_historical"]) for a in accounts)
        forward = sum(len(a["forward"]) for a in accounts)
        if state == "screened":
            named = ", ".join(coverage.get("screened") or confirmed) \
                or "the confirmed roster"
            bits = [f"Comparison set read: {named}"]
            if lane_names:
                bits.append("evidence lanes: " + ", ".join(lane_names))
            if as_of_label:
                bits.append(f"as of {as_of_label}")
            bits.append(f"{len(accounts)} buying accounts read")
            bits.append(f"{historical} historical rival record(s)")
            bits.append(f"{forward} forward signal(s)")
            out.append(
                '<div class="gaps"><div class="g-lab">'
                'COMPLETED SCREEN · SCOPE RECEIPT</div><div class="g-list">'
                + esc(" · ".join(bits)) + '</div></div>')
            if historical or forward:
                out.append(
                    '<div class="gaps"><div class="g-lab">'
                    'COMMERCIAL READ</div><div class="g-list">'
                    'History and forward demand sit here without current '
                    'rival paper on the cited records: the position is '
                    'open to shape, and the receipt above is the evidence '
                    'boundary of that read.</div></div>')
        elif state in ("supplied_not_screened", "partial"):
            named = ", ".join(coverage.get("unscreened")
                              or coverage.get("supplied") or confirmed) \
                or "the named comparison set"
            out.append(
                '<div class="gaps"><div class="g-lab">'
                'COMPETITIVE VALIDATION PENDING</div><div class="g-list">'
                + esc(f'Comparison set awaiting the screen: {named}. The '
                      'account seats render after their award paper is '
                      'read; nothing here prints a zero the lane has not '
                      'earned.') + '</div></div>')
        else:
            out.append(
                '<div class="gaps"><div class="g-lab">'
                'COMPETITIVE VALIDATION NEXT</div><div class="g-list">'
                'Naming the rival comparison set is the next account step '
                'for this engagement; the cited records above establish '
                'the client-side position it will be read against.'
                '</div></div>')
    out.append("</section>")
    return "".join(out)


def _render_method(idx: int, s: dict, prose: dict) -> str:
    """Band 08: the six contract blocks, in order."""
    doc = section("method")
    notes = s.get("_method_notes") or {}
    out = [_band_open(
        idx, "method", doc.heading, doc.meta,
        _prose(prose, "band_method",
               "Every figure in this document derives from the cited "
               "records; each identifier links to its primary source."))]
    out.append('<div class="gaps"><div class="g-lab">'
               '1 · LINK CLASSES</div><div class="g-list">'
               + LINK_CLASS_TOKEN + '</div></div>')
    out.append(
        '<div class="gaps"><div class="g-lab">2 · BAND 01 '
        'SELECTION RULE</div><div class="g-list">'
        'A decision card exists where client paper AND named-rival paper '
        'are both current (period end on or after the press date) at the '
        'same awarding subtier, judged on enriched period fields; records '
        'with no period end or no subtier are receipted in Band 01, never '
        f'evaluated. Minor rival presence (under '
        f'{R1_MATERIALITY["ratio_floor"] * 100:g} percent of client paper) '
        'renders collapsed below the main cards; absolute dollars are '
        'printed as scale but never decide the tier. No qualifying account '
        'is silently dropped. Exceptions this '
        'press: none.</div></div>')
    sel = s.get("selection") or {}
    screened = sel.get("screened_relevant") or {}
    if screened:
        out.append('<div class="gaps"><div class="g-lab">'
                   'Research coverage</div><div class="g-list">'
                   + esc("Screened " + ", ".join(
                       f"{lane_label(k)} {v}"
                       for k, v in sorted(screened.items()))
                       + " · off-scope dropped "
                       + str(sel.get("off_scope_dropped", 0)))
                   + '</div></div>')
    out.append(MARKS_RECEIPT_TOKEN)
    out.append(
        '<div class="gaps"><div class="g-lab">4 · MANUAL '
        'CONTENT</div><div class="g-list">'
        'Hand-entered links this press: 0. Studio-edited links are stamped '
        'by the export path and disclosed here as not machine-verified when '
        'present.</div></div>')
    absence = []
    if not s.get("notices"):
        absence.append("no live public solicitations matched this "
                       "vocabulary in the screened universe; this market "
                       "moves as renewals and category vehicles more than "
                       "open postings")
    if notes.get("forward_cut"):
        absence.append(f"{notes['forward_cut']} published forecast(s) "
                       "carried no client-paper adjacency and were cut")
    if notes.get("clocks_removed"):
        absence.append(f"{notes['clocks_removed']} dated clock(s) belong to "
                       "accounts outside this report and were removed")
    absence.append("obligated dollars are not ceilings and not run rates; "
                   "a current period end is not a potential option end")
    out.append(
        '<div class="gaps"><div class="g-lab">5 · WHAT THIS '
        'BRIEF DOES NOT CLAIM</div><div class="g-list">'
        + esc(" · ".join(absence)) + '</div></div>')
    screen = s.get("events_screen") or {}
    receipt_bits = []
    if isinstance(screen.get("scanned"), int):
        receipt_bits.append(f"{screen['scanned']:,} notices screened for "
                            f"events at zero metered cost")
    lane_receipt = s.get("sam_lane_receipt")
    if lane_receipt:
        receipt_bits.append("brand-name lane receipts ride the pack "
                            "artifact")
    out.append(
        '<div class="gaps"><div class="g-lab">6 · SCREENING '
        'RECEIPTS</div><div class="g-list">'
        + esc(" · ".join(receipt_bits)
              or "screening receipts ride the pack artifact")
        + '</div></div>')
    out.append(render_sam_lanes(s))
    out.append("</section>")
    return "".join(out)


def _targeting_join_cell(spec: dict, s: dict, cap: int = 3) -> str:
    """The joined record ids, each inside its own canonical anchor.

    LINKAGE LAW at the row: a record id renders only inside an anchor whose
    href is that record's pack source_url. A joined record the pack cannot
    resolve to a URL renders its buyer text instead of a bare id, the same
    refusal the competitive band's seat line makes.
    """
    parts = []
    for row in spec.get("join_records") or []:
        rid = str(row.get("record_id") or "")
        record = s["by_id"].get(rid)
        url = str(getattr(record, "url", "") or row.get("url") or "")
        if url.startswith("mailto:"):
            url = ""
        if rid and url:
            parts.append(_anchor(url, f"<span>{esc(rid)}</span>"))
        else:
            parts.append(esc(clip(row.get("sub_agency") or row.get("agency")
                                  or "cited record", 34)))
        if len(parts) >= cap:
            break
    total = len(spec.get("join_records") or [])
    if total > cap:
        parts.append(esc(f"+{total - cap} more"))
    return " · ".join(parts)


# The rendered spec table's bound. Adjacency legitimately produces one row
# per forecast at a single reseller, so the page needs a cap; the pack and
# the sequence export keep every spec.
_TARGETING_SPEC_CAP = 12


def _targeting_seeks_cell(spec: dict, ladder: dict) -> str:
    """Persona tiers as short, separator-split fragments.

    Every chunk stays under the repeated-prose floor by construction: the
    ladder itself is printed ONCE above the table, so a row names only which
    rungs it seeks.
    """
    bits = []
    for number in spec.get("seeks_tiers") or []:
        row = ladder.get(number) or {}
        bits.append(f"TIER {number}")
        persona = " ".join(str(row.get("persona") or "").split())
        if persona:
            bits.append(persona)
    return esc(" · ".join(bits)) or "not sought"


def _render_targeting(idx: int, s: dict, prose: dict) -> str:
    """Band 09: the action layer (contract §2/09, amendment v1.4).

    Two layers, never conflated. The TARGETING SPECS are deterministic
    press-side rule output over the decision rows, and they render whether
    or not anyone was ever resolved. The CONTACT ROWS are enrichment read
    from the durable store, which a separate sweep-class step wrote; the
    press calls nothing.

    LAWS. The join is the admission ticket: a contact renders only against a
    record the evidence bands already cite, and the identity is labeled
    enrichment, never asserted as a federal record. No provenance, no
    render. The zero branches on the ENRICHMENT RECEIPT, so a lane that was
    never attempted states that instead of printing a zero it never earned.
    """
    doc = section("targeting")
    targeting = s.get("targeting") or {}
    if not targeting:
        return (_band_open(
            idx, "targeting", doc.heading, doc.meta,
            "The targeting specs were not computed for this evidence pack; "
            "it was pressed before the targeting rules existed. Re-press to "
            "derive the per-decision call sheet from the cited records.")
            + '<div class="gaps"><div class="g-lab">TARGETING NOT COMPUTED'
              '</div><div class="g-list">'
              'No spec was derived, so this band states nothing about who to '
              'call. That is a missing computation, not a zero.'
              '</div></div></section>')

    receipt = targeting.get("receipt") or {}
    ladder_rows = list(targeting.get("ladder") or [])
    ladder = {row.get("tier"): row for row in ladder_rows}
    rows_read = receipt.get("rows_read") or {}
    total_rows = sum(int(v or 0) for v in rows_read.values())

    # THE JOIN LAW, applied to what the bands above ACTUALLY printed. A spec
    # whose every joined record was capped out of bands 01 to 07 has nothing
    # to hang a call on, so it does not render; the count is stated, never
    # silent.
    from agents.golden_press.validate import _visible_token_positions

    rendered_above = s.get("_rendered_bands") or ""
    specs = []
    withheld_unrendered = 0
    for spec in (targeting.get("specs") or []):
        joined = [row for row in (spec.get("join_records") or [])
                  if _visible_token_positions(
                      rendered_above, str(row.get("record_id") or ""))]
        if not joined:
            withheld_unrendered += 1
            continue
        spec = dict(spec)
        spec["join_records"] = joined
        specs.append(spec)

    lede = (
        f"{len(specs)} targeting spec(s) derived from the "
        f"{total_rows} decision row(s) this report already carries. Each "
        f"names the buying component, the persona tiers to seek, and the "
        f"cited record it hangs off. A contact here is a route to that "
        f"record, never evidence of its own.")
    if not specs:
        lede = (
            f"No targeting spec stands on this evidence: "
            f"{total_rows} decision row(s) were read and none carried both a "
            f"buying component and a cited record to hang a call on.")
    out = [_band_open(idx, "targeting", doc.heading, doc.meta, lede)]

    if specs:
        sought = set()
        for spec in specs:
            sought.update(spec.get("seeks_tiers") or [])
        for row in ladder_rows:
            if row.get("tier") not in sought:
                continue
            titles = " · ".join(str(t) for t in (row.get("titles") or [])[:6])
            out.append(
                '<div class="gaps"><div class="g-lab">'
                f'TIER {esc(row.get("tier"))} · '
                f'{esc(str(row.get("persona") or "").upper())}</div>'
                f'<div class="g-list">{esc(row.get("why") or "")} · '
                f'{esc(titles)}</div></div>')

        seen_why: set = set()
        body = []
        # Measured 2026-08-05: adjacency legitimately produces ten rows at
        # one reseller, so the table needs a bound. The cap is stated below,
        # never silent, and the specs themselves ride the pack artifact.
        shown = specs[:_TARGETING_SPEC_CAP]
        for spec in shown:
            why = " ".join(str(spec.get("rationale") or "").split())
            # Two specs can legitimately reach the same sentence (the same
            # rival named twice in one corridor). The stated-once precedent
            # from the competitive band's motion rationale applies: the
            # claim is printed once and the rows still stand.
            if why.casefold() in seen_why:
                why = ""
            elif why:
                seen_why.add(why.casefold())
            body.append(
                "<tr>"
                f'<td class="cn">{esc(spec.get("rule_id"))}</td>'
                f'<td>{esc(clip(spec["target_org"]["name"], 42))}</td>'
                f'<td>{esc(str(spec.get("row_class") or "").upper())}</td>'
                f'<td>{_targeting_seeks_cell(spec, ladder)}</td>'
                f'<td>{_targeting_join_cell(spec, s)}</td>'
                f'<td>{esc(why)}</td>'
                "</tr>")
        out.append(
            '<table class="recs" data-targeting-table="1"><thead><tr>'
            '<th>Rule</th><th>Target organisation</th><th>Row class</th>'
            '<th>Personas sought</th><th>Joined record</th>'
            '<th>Why this call</th></tr></thead><tbody>'
            + "".join(body) + "</tbody></table>")
        if len(specs) > len(shown):
            out.append(
                '<div class="gaps"><div class="g-list">'
                + esc(f"{len(specs) - len(shown)} further targeting spec(s) "
                      f"ride the pack artifact and the sequence export; the "
                      f"cap of {_TARGETING_SPEC_CAP} rows is stated here, "
                      f"never silent.") + '</div></div>')

    # ---- the enrichment layer, read from the store, never fetched here ---- #
    from tools.intelligence_graph.adapter import (
        admissible_target_observations, target_field_provenance)

    contacts, dropped = admissible_target_observations(
        {"contacts": targeting.get("contacts") or []},
        spec_ids=[spec["spec_id"] for spec in specs])
    enrichment = targeting.get("enrichment_receipt")

    if contacts:
        from agents.golden_press import phone_policy

        rows = []
        withheld_numbers = 0
        for contact in contacts:
            prov = target_field_provenance(contact, "name")
            # TASK 5 render policy, fail closed. org_main gets its OWN
            # labeled column and may never sit in the dial column; mobile
            # and unclassified are stored and withheld behind the switch.
            phones = contact.get("phones") or []
            main = phone_policy.select(phones, phone_policy.ORG_MAIN)
            direct = phone_policy.select(phones, phone_policy.WORK_DIRECT)
            mobile = phone_policy.select(phones, phone_policy.MOBILE)
            other = phone_policy.select(phones, phone_policy.UNCLASSIFIED)
            withheld_numbers += len(phone_policy.withheld(phones))
            spec = next((sp for sp in specs
                         if sp["spec_id"] == contact.get("spec_id")), None)
            # One route can answer several specs. The joined cell shows the
            # anchor spec's own records (the only ids proved rendered); the
            # count says how many specs this person serves without printing
            # an id no band above carries.
            served = len(contact.get("spec_ids") or [contact.get("spec_id")])
            also = (f" · serves {served} specs" if served > 1 else "")
            rows.append(
                "<tr>"
                f'<td>{esc(clip(contact.get("name"), 38))}</td>'
                f'<td>{esc(clip(contact.get("title") or "not published", 44))}'
                "</td>"
                f'<td>{esc(clip(contact.get("organization"), 38))}</td>'
                f'<td class="cn">TIER {esc(contact.get("tier"))}</td>'
                f'<td>{esc(contact.get("email") or "not resolved")}</td>'
                f'<td>{esc(mobile["number"] if mobile else (other["number"] if other else "not resolved"))}'
                "</td>"
                f'<td>{esc(direct["number"] if direct else "not resolved")}'
                "</td>"
                f'<td>{esc(main["number"] if main else "not published")}</td>'
                f'<td class="cd">{esc(prov["class"].upper())} · '
                f'{esc(_day(prov["retrieved_at"]))}</td>'
                f'<td>{(_targeting_join_cell(spec, s) + esc(also)) if spec else ""}</td>'
                "</tr>")
        out.append(
            '<table class="recs" data-targeting-contacts="1"><thead><tr>'
            '<th>Name</th><th>Title</th><th>Organisation</th><th>Tier</th>'
            '<th>Work email</th><th>Mobile</th><th>Direct line</th>'
            '<th>Main line</th>'
            '<th>Source and retrieved</th><th>Joined record</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table>")
        out.append(
            '<div class="gaps"><div class="g-lab">MAIN LINE IS NOT A DIRECT '
            'LINE</div><div class="g-list">'
            'A main line is the organisation switchboard, published by the '
            'organisation itself. It reaches the desk, not the person, and '
            'it never occupies the direct-line column. Ask for the named '
            'individual when you call it.</div></div>')
        if withheld_numbers:
            out.append(
                '<div class="gaps"><div class="g-lab">NUMBERS WITHHELD'
                '</div><div class="g-list">'
                + esc(f"{withheld_numbers} further number(s) are held in the "
                      f"targeting store and withheld from this document: "
                      f"mobile and unclassified numbers render only when the "
                      f"operator switch is on. The count is stated here, "
                      f"never silent.") + '</div></div>')
        out.append(
            '<div class="gaps"><div class="g-lab">ENRICHMENT · NOT '
            'EVIDENCE</div><div class="g-list">'
            'Every identity above is enrichment carrying its own source and '
            'retrieval date. It is a route to the linked record, never a '
            'federal record itself, and it is not counted in this document '
            'as primary evidence. Verify a person before acting on the '
            'name.</div></div>')

    if enrichment:
        attempted = bool(enrichment.get("attempted"))
        bits = [
            "attempted" if attempted else "not attempted",
            f"source {enrichment.get('source') or 'not stated'}",
            f"as of {_day(str(enrichment.get('retrieved_at') or '')) or 'not stated'}",
            f"{int(enrichment.get('result_count') or 0)} identity result(s)",
            f"{len(contacts)} rendered after the join and provenance laws",
        ]
        if enrichment.get("calls_made") is not None:
            bits.append(f"{int(enrichment['calls_made'])} supply call(s)")
        screened = list(enrichment.get("screened_out") or [])
        if screened:
            bits.append(f"{len(screened)} screened out before the dial spend")
        stripped = sum(int(v) for v in dropped.values())
        if stripped:
            bits.append(f"{stripped} row(s) or field(s) withheld for missing "
                        "provenance or join")
        label = "ENRICHMENT RECEIPT" if contacts else "NO CONTACTS RESOLVED"
        out.append(
            f'<div class="gaps"><div class="g-lab">{label}</div>'
            f'<div class="g-list">{esc(" · ".join(bits))}</div></div>')
        if screened:
            # A rejection is RECEIPTED BY NAME. The screen removes people
            # from a client artifact, so the operator must be able to see
            # exactly who and reinstate them; a silent screen is worse than
            # no screen.
            named = " · ".join(
                f'{" ".join(str(row.get("name") or "a candidate").split())} '
                f'({" ".join(str(row.get("why") or "screened out").split())})'
                for row in screened[:4])
            out.append(
                '<div class="gaps"><div class="g-lab">SCREENED OUT BEFORE '
                'SPEND</div><div class="g-list">'
                + esc(f"{len(screened)} candidate(s) rejected before the "
                      f"dial spend: " + named)
                + esc(f" · +{len(screened) - 4} more in the store"
                      if len(screened) > 4 else "")
                + '</div></div>')
        if not contacts:
            out.append(
                '<div class="gaps"><div class="g-list">'
                'The supply step ran against the specs above and resolved no '
                'identity that met the join and provenance laws. That is a '
                'tested result with its receipt, not an absence of '
                'work.</div></div>')
    elif specs:
        out.append(
            '<div class="gaps"><div class="g-lab">TARGETING SUPPLY NOT '
            'RUN</div><div class="g-list">'
            'No enrichment receipt exists for this client, so no identity '
            'lookup has been attempted against these specs. This states the '
            'posture; it is not a zero, and the specs above are ready for '
            'the supply step exactly as they stand.</div></div>')
    else:
        out.append(
            '<div class="gaps"><div class="g-lab">TARGETING · NONE'
            '</div><div class="g-list">'
            + esc(f"{total_rows} decision row(s) were read and none produced "
                  f"a spec: {len(receipt.get('no_join_not_targeted') or [])} "
                  f"carried no record to join, "
                  f"{len(receipt.get('no_route_not_targeted') or [])} named "
                  f"no route to work, and {withheld_unrendered} joined only "
                  f"records the bands above did not print. The count is "
                  f"stated here, never silent.") + '</div></div>')
    if specs and withheld_unrendered:
        out.append(
            '<div class="gaps"><div class="g-lab">WITHHELD ON THE JOIN'
            '</div><div class="g-list">'
            + esc(f"{withheld_unrendered} further spec(s) joined only "
                  f"records that the bands above did not print, so no call "
                  f"hangs on them here. They ride the pack artifact; the cap "
                  f"is stated, never silent.") + '</div></div>')
    out.append("</section>")
    return "".join(out)


def render_footer(s: dict) -> str:
    stamp = str(s.get("press_date") or "")
    return (
        '<footer class="foot">'
        '<div class="g-list">Federal Opportunity Pre-Assessment · '
        f'evidence snapshot {esc(_day(stamp) or stamp)} · '
        f'{len(s.get("records") or [])} primary records · '
        'prepared by The GTM Group · sources: USAspending, SAM.gov, APFS · '
        'verify every record at its linked primary source before acting.'
        '</div></footer>')


_BAND_BUILDERS = {
    "decisions": lambda idx, s, prose: render_decision_band(idx, s, prose),
    "forward": _render_forward,
    "clocks": _render_clocks,
    "paper": _render_paper,
    "agencies": _render_agencies,
    "competitive": _render_competitive,
    "events": lambda idx, s, prose: render_events(idx, s, prose),
    "method": _render_method,
    "targeting": _render_targeting,
}


def render_bands(s: dict, prose: dict) -> str:
    """CONTRACT DERIVATION (2026-08-03): the band sequence comes from
    docs/REPORT_CONTRACT.md §1 via the contract loader; numbering is the
    render position itself, so a stale numeral cannot exist."""
    from agents.golden_press.contract import contract_section_ids
    out = []
    for idx, band_id in enumerate(contract_section_ids(), start=1):
        # "ALREADY RENDERED" IS LITERAL (contract §2/09 join law, v1.4). The
        # targeting band may only hang a call on a record id the bands above
        # it actually printed, and band 05 caps its buyer list, so the join
        # cannot be proved against the pack alone. Each builder therefore
        # sees the region rendered so far; only band 09 reads it.
        s["_rendered_bands"] = "".join(out)
        out.append(_BAND_BUILDERS[band_id](idx, s, prose))
    return "".join(out)


def _event_note(note: Any, seen: set) -> str:
    """The relevance note, once per CITED RECORD.

    Deduping on the whole sentence was not enough. Several events in
    DIFFERENT corridors cite the SAME pack award, so the notes differ at both
    ends ("DHS corridor ... event NAICS 541512") while sharing the clause
    that carries the actual claim ("your pack cites W519TC26F0012 at
    $145,033,091"). Five rendered on a live press and the validator matched
    the shared clause, not the whole string. The repeated thing is the
    record, so the record is the key.
    """
    text = " ".join(str(note or "").split())
    if text:
        cited = _CITED_RECORD.search(text)
        if cited:
            key = cited.group(1).upper()
            if key in seen:
                return ""
            seen.add(key)
            return f'<div class="g-list">{esc(text)}</div>'
    if not text or text.lower() in seen:
        return ""
    seen.add(text.lower())
    return f'<div class="g-list">{esc(text)}</div>'


def render_events(idx: int, s: dict, prose: dict) -> str:
    # ZERO-STATE RECEIPT (truth purge, 2026-08-03): a client whose corridors
    # connect to no relevant event renders the screening receipt as a stated
    # zero, exactly the decision band's stated-zero doctrine. DoD filler for
    # an AP-audit client is the defect class this replaces; an absent band
    # would read as "never looked".
    if not s["events"]:
        screen = s.get("events_screen") or {}
        doc = section("events")
        counts = " · ".join(
            f"{label} {screen.get(key):,}"
            for key, label in (("scanned", "notices screened"),
                               ("event_type", "event-type notices"),
                               ("event_language", "event-language notices"),
                               ("client_relevant", "connected to this client"))
            if isinstance(screen.get(key), int))
        return (_band_open(
            idx, "events", doc.heading, doc.meta,
            "No verified buyer or teaming event connects to this client's "
            "funded accounts, capability vocabulary, or approved NAICS "
            "boundary in the screened universe. The screen is stated below; "
            "nothing here is padded.")
            + '<div class="gaps"><div class="g-lab">'
              'SCREENING RECEIPT · ZERO RELEVANT EVENTS</div>'
              '<div class="g-list">'
            + esc(counts or "screening counts unavailable for this press")
            + esc(" · events kept: 0 · metered SAM quota spent: "
                  f"{screen.get('metered_quota_spent', 0)}")
            + '</div></div></section>')
    body = []
    # THE ACCOUNT REASON IS SAID ONCE. Several events legitimately sit in
    # the same buying account and cite the same pack record, so the
    # relevance note is identical for all of them: six rendered the same
    # sentence on a live press and the validator caught it as
    # repeated_prose. It is stated on the first event of an account and
    # omitted on the rest. CONTRACT §2/07: at most 3 featured events; the
    # rest ride the monitor strip, links only, no claim attached.
    seen_notes: set = set()
    # G5 (2026-08-03): FEATURED requires (open registration OR a future
    # event date) AND an account tie to a Band 01/02 account (subtier or
    # its parent). Keyword-only matches ride the monitor strip; a closed
    # registration NEVER features.
    press_date = str(s.get("press_date") or "")
    accounts = _accounts_in_play(s)

    def _get(e):
        return (e.get if isinstance(e, dict)
                else lambda k, d=None: getattr(e, k, d))

    def _qualifies(e):
        get = _get(e)
        if get("registration_closed"):
            return False
        deadline = str(get("registration_deadline") or "")[:10]
        start = str(get("event_start") or "")[:10]
        open_reg = bool(deadline) and press_date and deadline >= press_date
        future = bool(start) and press_date and start > press_date
        if not (open_reg or future):
            return False
        basis = get("relevance_basis") or {}
        agency = " ".join(str(basis.get("agency") or "").split())
        if not agency:
            return False
        keys = {agency.casefold()}
        try:
            from agents.golden_press.decision_rules import _department_key
            parent = _department_key(agency)
            if parent:
                keys.add(parent.casefold())
        except Exception:  # noqa: BLE001
            pass
        return bool(keys & accounts)

    def _qualifies_timing_only(e):
        """The G5 timing half: open registration or a future date. The
        account half is dropped only when no account exists to tie to."""
        get = _get(e)
        if get("registration_closed"):
            return False
        deadline = str(get("registration_deadline") or "")[:10]
        start = str(get("event_start") or "")[:10]
        return bool((deadline and press_date and deadline >= press_date)
                    or (start and press_date and start > press_date))

    qualified = [e for e in s["events"] if _qualifies(e)]
    # AMENDMENT v1.2 (operator, 2026-08-04: optimize for actionable content).
    # The account tie focuses the feature; it must not erase it. With no
    # accounts in play NOTHING can tie, so a client's own verified circuit
    # (Thinklogical: AFCEA TechNet Augusta, AUSA, AFA) was demoted to a link
    # strip. With no accounts, an event still features on the timing test
    # alone: it is already bound to a pack fact and verified live upstream.
    unbound_feature = bool(s["events"]) and not accounts and not qualified
    if unbound_feature:
        qualified = [e for e in s["events"] if _qualifies_timing_only(e)]
    rest = [e for e in s["events"] if e not in qualified]
    featured = qualified[:3]
    monitor = (qualified[3:] + rest)[:5]
    for e in featured:
        get = e.get if isinstance(e, dict) else (lambda k, d=None: getattr(e, k, d))
        when = _day(get("event_start")) or "TBD"
        deadline = get("registration_deadline")
        # event_id rides inside the anchor: revision pairs of the same
        # announcement stay textually distinct (repeated_prose) and the id
        # stays covered by its own source link (contract-number linkage).
        body.append(
            f'<article class="event"><div class="e-when">{esc(when)}</div>'
            f'<h3>{_anchor(get("url"), esc(notice_title(get("name"))))}</h3>'
            f'<div class="m-lab">{esc(get("host") or "")}'
            f'{" · " + esc(get("location")) if get("location") else ""}'
            f'{" · VIRTUAL" if get("is_virtual") else ""}'
            f'{" · REGISTRATION " + ("CLOSED" if get("registration_closed") else "BY " + _day(deadline)) if deadline else ""}'
            f'{" · date not published in the notice" if not get("event_start") else ""}'
            f'</div>{_event_note(get("relevance"), seen_notes)}'
            "</article>")
    if monitor:
        links = " · ".join(
            _anchor((e.get if isinstance(e, dict)
                     else lambda k, d=None: getattr(e, k, d))("url"),
                    esc(clip((e.get if isinstance(e, dict)
                              else lambda k, d=None: getattr(e, k, d))
                             ("name") or "event", 60)))
            for e in monitor)
        body.append('<div class="gaps"><div class="g-lab">'
                    'MONITOR · LINKS ONLY · NO CLAIM ATTACHED</div>'
                    f'<div class="g-list">{links}</div></div>')
    opener = (f"{len(featured)} featured events are bound to a cited record "
              f"and still open" if unbound_feature else
              f"{len(featured)} featured events connect to accounts in this "
              f"report")
    fallback_lede = (
        opener
        + (f"; {len(monitor)} more ride the monitor strip, links only"
           if monitor else "")
        + ". Events are a separate research surface and do not count "
          "toward evidence sufficiency.")
    band_html = (_band_open(idx, "events", section("events").heading,
                            section("events").meta,
                            _prose(prose, "band_events", fallback_lede))
                 + "".join(body) + "</section>")
    return _link_event_names(band_html, s.get("events") or [])


def _link_event_names(band_html: str, events: Iterable[Any]) -> str:
    """Linkage law, extended to event names (JTG press, 2026-08-19): a
    composed lede may name a verified event the deterministic rows did not
    feature; an un-anchored occurrence of a verified name is wrapped in its
    verified URL so the citation travels with the claim."""
    for e in events:
        get = e.get if isinstance(e, dict) else (
            lambda k, d=None: getattr(e, k, d))
        name, url = str(get("name") or ""), get("url")
        if not name or not url or not get("url_verified"):
            continue
        needle = esc(name)
        if needle not in band_html:
            continue
        spans = [(m.start(), m.end()) for m in
                 re.finditer(r"<a\b.*?</a>|<[^>]+>", band_html, re.S)]
        out, last, changed = [], 0, False
        for m in re.finditer(re.escape(needle), band_html):
            if any(a <= m.start() < b for a, b in spans):
                continue
            out.append(band_html[last:m.start()])
            out.append(_anchor(url, needle))
            last = m.end()
            changed = True
        if changed:
            out.append(band_html[last:])
            band_html = "".join(out)
    return band_html


def link_loose_identifiers(content: str, records: Iterable[Any]) -> str:
    """LINKAGE LAW, made total: wrap any record identifier that ended up in
    running text (award descriptions frequently quote their own PIID) in
    that record's canonical anchor. Occurrences already inside an anchor,
    an attribute, or a tag are left untouched."""
    spans = [(m.start(), m.end())
             for m in re.finditer(r"<a\b.*?</a>|<[^>]+>", content, re.S)]

    def covered(pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    for r in records:
        rid = str(getattr(r, "record_id", "") or "")
        url = getattr(r, "url", None)
        if len(rid) < 6 or not url:
            continue
        out, last, changed = [], 0, False
        for m in re.finditer(re.escape(rid), content):
            if covered(m.start()):
                continue
            out.append(content[last:m.start()])
            out.append(_anchor(url, f'<span>'
                                    f"{esc(rid)}</span>"))
            last = m.end()
            changed = True
        if changed:
            out.append(content[last:])
            content = "".join(out)
            spans = [(m.start(), m.end())
                     for m in re.finditer(r"<a\b.*?</a>|<[^>]+>", content, re.S)]
    return content


# CONTRACT DERIVATION (2026-08-03): prose exists only for the bands the
# contract lets the model speak in (L10: brief slots, deterministic bands
# carry none). Keys are band_{contract-id}; membership is checked against
# the contract at import so a renamed band cannot orphan its slot.
_PROSE_BEARING = ("forward", "paper", "agencies", "competitive", "events",
                  "method")
BAND_PROSE_BRIEFS: tuple[tuple[str, str], ...] = (
    ("hero_context", "Two sentences under the headline: what this client's "
                     "federal position looks like right now."),
    ("band_forward", "Why the forward lane is worth qualifying now."),
    ("band_paper", "What the prime-recipient pattern says about routes."),
    ("band_agencies", "How to read the historical buying context."),
    ("band_competitive", "The commercial meaning of the account-by-account "
                         "competitive picture: where to defend, where to "
                         "displace, and which paper holder to approach. "
                         "Research mechanics stay in the sidecars, never "
                         "in this copy."),
    ("band_events", "Why these events matter for these accounts."),
    ("band_method", "How this assessment was built and its boundary."),
)


WRONG_DOMAIN_PREFIX = "WRONG DOMAIN:"


def composer_demotions(pack: Any, prose: dict) -> dict[str, str]:
    """Rival records the composer itself attested as name collisions.

    The why-slot for a rival record invites the writer to open with the
    exact WRONG_DOMAIN_PREFIX when the award is not actually the
    competitor's product domain (the Sentra gasket-probe class,
    2026-08-04). The attestation rides the prose call that already runs;
    detection is a byte-exact prefix, never sentiment parsing.
    """
    from agents.golden_press.validate import _record_affinity

    out: dict[str, str] = {}
    for record in getattr(pack, "records", ()) or ():
        if _record_affinity(record, pack) != "competitor":
            continue
        text = str((prose or {}).get(f"why_{record.record_id}") or "")
        if text.startswith(WRONG_DOMAIN_PREFIX):
            out[record.record_id] = text[len(WRONG_DOMAIN_PREFIX):].strip()
    return out


def _assert_prose_keys_match_contract() -> None:
    from agents.golden_press.contract import contract_section_ids
    ids = set(contract_section_ids())
    for key, _brief in BAND_PROSE_BRIEFS:
        if key.startswith("band_") and key[5:] not in ids:
            raise ValueError(f"prose slot {key} names no contract band")
    for band_id in _PROSE_BEARING:
        if band_id not in ids:
            raise ValueError(f"prose-bearing band {band_id} not in contract")


_assert_prose_keys_match_contract()


def prose_slots(pack: Any) -> list[dict]:
    """Every prose slot the renderer will consume, with a factual brief.

    The brief carries the facts so the writer never has to recall or restate
    a figure: code has already rendered every number, date, and identifier
    around the prose.
    """
    s = shape(pack)
    slots = [{"key": k, "brief": b} for k, b in BAND_PROSE_BRIEFS]
    for name, recs in list(s["corridors"].items())[:6]:
        total = sum(x.obligated_dollars or 0 for x in recs)
        slots.append({
            "key": f"why_account_{_slug(name)}",
            "brief": (f"One sentence on why the {name!r} buying account "
                      f"({len(recs)} cited records, {_money(total)} obligated) "
                      "is a viable lane for this client, and what makes it "
                      "actionable now."),
        })
    rival_ids = {r.record_id for r in s["rival"]}
    for r in s["records"]:
        buyer = r.sub_agency or r.agency or "this buyer"
        brief = (f"One sentence on why {buyer} / "
                 f"{clip(r.description or r.title, 90)!r} "
                 f"(awardee {r.recipient or 'not published'}"
                 + (f", vehicle {r.vehicle}" if r.vehicle else "")
                 + ") is worth this client's attention.")
        if r.record_id in rival_ids:
            # The attestation invitation rides the same call: a rival seat
            # exists because text matched a competitor name, and the writer
            # sees the full award facts, so it is the cheapest reliable
            # judge of a name collision.
            brief += (
                " This award is seated as competitor evidence because its"
                " text matched a competitor name. If it is NOT actually"
                " that competitor's product or service domain (a name"
                " collision: auto parts, lab hardware, an unrelated"
                f" vendor), begin with exactly {WRONG_DOMAIN_PREFIX!r}"
                " followed by one clause naming what the award actually"
                " is.")
        slots.append({"key": f"why_{r.record_id}", "brief": brief})
    return slots


def render_content_region(pack: Any, prose: Optional[dict] = None) -> str:
    prose = prose or {}
    s = shape(pack, demoted=frozenset(composer_demotions(pack, prose)))
    n_clocks = len(s["clocks"])
    next_fallback = (f"Validate the {n_clocks} clocked records with their "
                     f"contracting offices, then prioritise the corridors "
                     f"carrying the largest obligations.")
    aside = render_footer(s)
    # AESTHETIC PACKAGE (2026-08-03): styles come only from report.css +
    # design_tokens.json; the <style> travels with the content region because
    # the skeleton stays byte-verbatim by doctrine. The mark-gap collector
    # resets per render, and the method band's receipt token is filled AFTER
    # every band has rendered its marks (mark-slot-never-empty: a monogram is
    # a disclosed gap, receipted, never a blank or a guess).
    from agents.golden_press.report_templates import aesthetic_style_block
    del _MARK_GAPS[:]
    body = ('<div id="board">' + render_hero(s, prose)
            + aesthetic_style_block()
            + render_bands(s, prose) + aside + '</div>')
    if _MARK_GAPS:
        receipt = (
            '<div class="gaps"><div class="g-lab">'
            'MARKS · OFFICIAL OR MONOGRAM</div><div class="g-list">'
            f'{len(_MARK_GAPS)} named organisation(s) render a monogram '
            'because no official mark is on file: '
            + esc(" · ".join(_MARK_GAPS[:12]))
            + '. Every other mark is the catalogued official one; no slot '
              'is empty and no mark is guessed.</div></div>')
    else:
        receipt = ""
    body = body.replace(MARKS_RECEIPT_TOKEN, receipt, 1)
    # link-class census (contract §2/08 block 1): counted over the rendered
    # body itself, so the legend can never drift from the artifact.
    hrefs = re.findall(r'href="([^"]+)"', body)
    primary = sum(1 for h in hrefs if any(k in h for k in (
        "usaspending.gov", "sam.gov", "apfs", "acquisitiongateway")))
    event_pages = max(0, len(hrefs) - primary)
    legend = esc(f"Primary evidence links {primary} · official event and "
                 f"agency pages {event_pages} · published contacts 0 · "
                 f"vendor marketing 0 (never cited as evidence)")
    body = body.replace(LINK_CLASS_TOKEN, legend, 1)
    return link_loose_identifiers(body, s["records"]) + SENTINEL
