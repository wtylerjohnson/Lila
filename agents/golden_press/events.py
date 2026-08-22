"""Events and industry days lane (EVENTS_LANE, 2026-07-27).

Tier A: SAM.gov special notices and pre-solicitations carrying event
language, harvested from the daily Contract Opportunities extract (the whole
active universe with full description text, zero metered quota) and screened
against THIS client's pack: the agencies they are funded in, their capability
vocabulary, their NAICS boundary, and their named competitors.

LAWS (identical standing to the linkage and show-your-work laws):
  - An event with no verifiable URL does not ship.
  - No date, name, host, or location is ever invented, inferred, or
    approximated. Structured fields are used as structured fields; a date
    read out of description prose ships ONLY with the verbatim source span
    that produced it, so a human can check the claim against the notice.
  - Every row carries a relevance rationale bound to a concrete pack fact.
  - Events NEVER count toward the evidence sufficiency gate.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

from pydantic import BaseModel, Field

from agents.reports.links import build_sam_notice_link

SCHEMA_VERSION = 1

# Notice types that host events. Award notices and plain solicitations are
# excluded: they are buys, not gatherings.
EVENT_NOTICE_TYPES = {
    "special notice", "presolicitation", "sources sought",
}

# Event language, ordered most specific first; the matched phrase is carried
# on the record as the reason the notice was treated as an event at all.
EVENT_PHRASES = (
    "pre-proposal conference", "preproposal conference",
    "pre-solicitation conference", "presolicitation conference",
    "pre-bid conference", "prebid conference",
    "industry day", "industry days", "industry engagement day",
    "vendor day", "supplier day", "matchmaking event",
    "sources sought session", "one-on-one session", "one on one session",
    "virtual industry", "industry briefing", "town hall",
    "pre-award conference", "reverse industry day", "capability briefing",
)
# Deliberately NOT event language: "site visit" alone. Measured 2026-07-27 it
# fired on sole-source intents where a visit is a contract mechanic, not a
# gathering a client could attend. Real pre-award site visits carry one of the
# conference phrases above as well.

# Tokens that carry no agency identity; dropped before order-insensitive
# comparison so "VETERANS AFFAIRS, DEPARTMENT OF" reads as the pack's
# "Department of Veterans Affairs".
_AGENCY_STOP = frozenset({
    "department", "dept", "of", "the", "us", "u.s.", "usa", "office",
    "bureau", "national", "federal", "united", "states", "and", "for",
    "administration" if False else "",
}) - {""}

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_NUM = {m: i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_NUM.update({m[:3]: i + 1 for i, m in enumerate(_MONTHS)})
_MONTH_ALT = "|".join(list(_MONTHS) + [m[:3] for m in _MONTHS])

# Explicit calendar dates only. No relative language ("next Tuesday"), no
# bare month-year, no ranges we would have to interpolate.
_DATE_PATTERNS = (
    re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\.?,?\s+(20\d{{2}})\b", re.I),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b"),
    re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),
)

# Multi-day conferences publish RANGES, and the two house styles differ:
# "August 17-20, 2026" (US) and "12-14 October 2026" (military/European, the
# form AUSA uses). Both yield a real start AND end date, so neither is
# inferred. Verified live 2026-07-27 against ausa.org and afcea.org.
_RANGE_PATTERNS = (
    re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})\s*[-–—]\s*"
               rf"(?:({_MONTH_ALT})\.?\s+)?(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+"
               rf"({_MONTH_ALT})\.?,?\s+(20\d{{2}})\b", re.I),
)


def extract_event_range(
    text: str,
    *,
    not_before: Optional[date] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(start_iso, end_iso, verbatim_span) for an explicit multi-day range.

    With ``not_before``, returns the EARLIEST range ending on or after that
    date. Conference pages routinely print last year's recap above next
    year's dates (verified on ausa.org, 2026-07-27: "13-15 October 2025"
    precedes "12-14 October 2026"), so first-match would read the past.

    Returns (None, None, None) when the text carries no explicit range: a
    conference with no published dates is skipped, never guessed at."""
    found: list[tuple[str, str, str]] = []
    for pattern in _RANGE_PATTERNS:
        for m in pattern.finditer(text):
            g = list(m.groups())
            try:
                if pattern is _RANGE_PATTERNS[0]:
                    m1 = _MONTH_NUM[g[0].casefold()[:3]]
                    d1 = int(g[1])
                    m2 = _MONTH_NUM[g[2].casefold()[:3]] if g[2] else m1
                    d2, year = int(g[3]), int(g[4])
                else:
                    d1, d2 = int(g[0]), int(g[1])
                    m1 = m2 = _MONTH_NUM[g[2].casefold()[:3]]
                    year = int(g[3])
                start = date(year, m1, d1).isoformat()
                end = date(year, m2, d2).isoformat()
            except (ValueError, KeyError):
                continue
            if end < start:
                continue
            span = " ".join(text[max(0, m.start() - 70): m.end() + 70].split())
            found.append((start, end, span))
    if not found:
        return None, None, None
    if not_before is not None:
        future = [f for f in found if f[1] >= not_before.isoformat()]
        if not future:
            return None, None, None
        return min(future, key=lambda f: f[0])
    return found[0]


class EventRecord(BaseModel):
    """One event, every field either structured from the source or carried
    with the verbatim span that produced it."""

    event_id: str
    name: str
    event_type: str = Field(
        description="government-hosted | industry conference | client-hosted")
    source_system: str = Field(description="sam_extract | agency_osdbu | curated")
    url: str = Field(description="registration or notice URL, verified live")
    url_verified: bool = False
    url_status: Optional[int] = None
    host: str = ""
    host_office: Optional[str] = None
    location: Optional[str] = None
    is_virtual: bool = False
    event_start: Optional[str] = None
    event_end: Optional[str] = None
    event_date_span: Optional[str] = Field(
        default=None,
        description="verbatim source text that produced event_start; None "
                    "when the date came from a structured field")
    registration_deadline: Optional[str] = None
    registration_deadline_source: Optional[str] = Field(
        default=None, description="structured field name or verbatim span")
    registration_closed: bool = False
    relevance: str = Field(default="", description="one line, bound to a pack fact")
    relevance_basis: dict[str, Any] = Field(default_factory=dict)
    matched_phrase: Optional[str] = None
    notice_type: Optional[str] = None
    naics: Optional[str] = None
    posted_date: Optional[str] = None
    retrieved_at: Optional[str] = None


def _log(msg: str) -> None:
    print(f"     [events] {msg}", file=sys.stderr, flush=True)


def _iso(value: str) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(text[:len(fmt) + 4].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(20\d{2}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def extract_event_date(text: str, phrase_pos: int) -> tuple[Optional[str], Optional[str]]:
    """(iso_date, verbatim_span) for the explicit calendar date nearest the
    event phrase. Returns (None, None) when no explicit date is present:
    we never infer one."""
    window = text[max(0, phrase_pos - 240): phrase_pos + 600]
    best: Optional[tuple[int, str, str]] = None
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(window):
            try:
                groups = [g for g in m.groups()]
                if pattern is _DATE_PATTERNS[0]:
                    month = _MONTH_NUM[groups[0].casefold()[:3]]
                    day, year = int(groups[1]), int(groups[2])
                elif pattern is _DATE_PATTERNS[1]:
                    day = int(groups[0])
                    month = _MONTH_NUM[groups[1].casefold()[:3]]
                    year = int(groups[2])
                elif pattern is _DATE_PATTERNS[2]:
                    month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
                else:
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                iso = date(year, month, day).isoformat()
            except (ValueError, KeyError):
                continue
            distance = abs(m.start() - min(phrase_pos, 240))
            span = window[max(0, m.start() - 60): m.end() + 60].strip()
            span = " ".join(span.split())
            if best is None or distance < best[0]:
                best = (distance, iso, span)
    return (best[1], best[2]) if best else (None, None)


def _client_paper(record: Any, pack: Any, kind_by_name: dict,
                  words: Any, vendor_pattern: Any) -> bool:
    """Is this record the CLIENT's own funded paper, under the shared guards?

    TRUTH PURGE (operator filing, 2026-08-03). A corridor exists only where
    the client's own paper puts it. Three admissions, every one an imported
    owner, never a reimplementation:

      1. recipient identity (rollups.resolve_relationship): the client won
         the award; that is its own proof (the Osprey rule).
      2. a guarded core entity hit: the record was surfaced by a client/
         product term (retrieval._affinity says core) AND that hit is either
         a distinctive name (sam_lanes.is_distinctive) or the vendor is
         named in the record's own text. apexanalytix's pack proved the
         unguarded version: awards to Accenture/IBM/Palantir carrying the
         bare dictionary-word hit 'Platform' minted DoD/VA/ED corridors and
         shipped seven DoD filler events into an AP-audit client's report.
      3. the vendor named in the record's own text.
    """
    from agents.golden_press.rollups import resolve_relationship
    from agents.golden_press.sam_lanes import is_distinctive

    recipient = str(getattr(record, "recipient", "") or "")
    if recipient and resolve_relationship(
            recipient, pack)["relationship"] == "client_entity":
        return True
    text = " ".join(
        f"{getattr(record, 'title', '') or ''} "
        f"{getattr(record, 'description', '') or ''}".split())
    vendor_present = bool(vendor_pattern and text
                          and vendor_pattern.search(text))
    for hit in (getattr(record, "entity_hits", None) or []):
        if kind_by_name.get(str(hit).casefold()) in ("client", "product") \
                and (is_distinctive(str(hit), words) or vendor_present):
            return True
    return vendor_present


def pack_frame(pack: Any) -> dict:
    """The client's own facts that an event must connect to.

    `agency_evidence` maps each buying corridor in the pack to its STRONGEST
    cited record. A corridor-based relevance line must name that record, so
    "an agency you sell to is holding an event" can never ship as a reason;
    only "this corridor, where THIS award of THIS size runs out on THIS
    date" can.

    TRUTH PURGE (operator filing, 2026-08-03), two legs:
      - A corridor is minted ONLY by the client's own funded paper
        (_client_paper over L2 awards, shared-guard classified). A stranger's
        award in the pack is evidence about that award, never about where
        this client sells.
      - The NAICS boundary is the operator-approved inferred list ONLY,
        matching industry_days.naics_boundary. A record's incidental code
        widened the boundary without anyone deciding to (the same drift the
        industry-day lane already refuses).
    """
    from agents.golden_press.decision_rules import _pack_kind_by_name
    from agents.golden_press.retrieval import _affinity
    from agents.golden_press.sam_lanes import (
        _pattern,
        aliases,
        load_dictionary,
    )

    kind_by_name = _pack_kind_by_name(pack)
    words, _complete = load_dictionary()
    vendor = str(getattr(pack, "client_name", "") or "")
    vendor_pattern = _pattern(aliases(vendor)) if vendor else None

    agencies: dict[str, str] = {}
    agency_evidence: dict[str, Any] = {}
    for r in getattr(pack, "records", []) or []:
        if getattr(r, "lane", None) != "L2_entity_award":
            continue                      # corridors are funded paper only
        if _affinity(r, kind_by_name) == "competitor":
            continue
        if not _client_paper(r, pack, kind_by_name, words, vendor_pattern):
            continue
        for value in (r.agency, r.sub_agency):
            if not value:
                continue
            key = " ".join(str(value).split()).casefold()
            agencies.setdefault(key, str(value))
            held = agency_evidence.get(key)
            if held is None or (r.obligated_dollars or 0) > (
                    held.obligated_dollars or 0):
                agency_evidence[key] = r
    research = getattr(pack, "research", {}) or {}
    entities = research.get("entities", {}) or {}
    return {
        "agencies": agencies,
        "agency_evidence": agency_evidence,
        "naics": {str(c) for c in (research.get("inferred_naics") or [])},
        "terms": [str(t) for t in (research.get("screen_terms")
                                   or research.get("capability_terms") or [])],
        "competitors": [str(n) for n in (entities.get("competitor") or [])],
        "products": [str(n) for n in (entities.get("product") or [])],
        "client": getattr(pack, "client_name", ""),
    }


def _word_hit(text: str, term: str) -> bool:
    if len(term) < 3:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(term.casefold())
                     + r"(?![a-z0-9])", text) is not None


def _agency_tokens(name: str) -> frozenset:
    words = re.findall(r"[a-z0-9]+", str(name or "").casefold())
    return frozenset(w for w in words if w not in _AGENCY_STOP and len(w) > 2)


def agency_match(pack_agency: str, notice_agency_text: str) -> bool:
    """Cross-format corridor match.

    Pack agencies come from USAspending ("Department of Veterans Affairs");
    the SAM extract writes them its own way ("VETERANS AFFAIRS, DEPARTMENT
    OF"). Layer 1 is the repo's curated agency catalog; layer 2 is an
    order-insensitive significant-token subset with a two-token floor, so
    "defense" alone can never match everything.
    """
    from tools.agencies import find, matches_record

    entry = find(pack_agency)
    if entry is not None:
        try:
            if matches_record(notice_agency_text, entry):
                return True
        except Exception:  # noqa: BLE001 - catalog is best effort
            pass
    pack_tokens = _agency_tokens(pack_agency)
    notice_tokens = _agency_tokens(notice_agency_text)
    if len(pack_tokens) < 2 or not notice_tokens:
        return False
    return pack_tokens <= notice_tokens


def score_relevance(row: dict, frame: dict) -> tuple[Optional[str], dict]:
    """(rationale, basis) bound to a concrete pack fact, or (None, {}) when
    the notice has no connection to this client's book."""
    hay = " ".join(str(row.get(k) or "") for k in
                   ("Title", "Description", "NaicsCode")).casefold()
    agency_text = " ".join(str(row.get(k) or "") for k in
                           ("Department/Ind.Agency", "Sub-Tier", "Office")).casefold()
    naics = re.sub(r"[^0-9]", "", str(row.get("NaicsCode") or ""))[:6]

    agency_hit = next(
        (display for key, display in frame["agencies"].items()
         if key and agency_match(display, agency_text)), None)
    term_hits = [t for t in frame["terms"] if _word_hit(hay, t)]
    product_hits = [p for p in frame["products"] if _word_hit(hay, p)]
    competitor_hits = [c for c in frame["competitors"] if _word_hit(hay, c)]
    naics_hit = naics if naics and naics in frame["naics"] else None

    basis = {k: v for k, v in {
        "agency": agency_hit, "naics": naics_hit,
        "capability_terms": term_hits[:4], "products": product_hits[:3],
        "competitors": competitor_hits[:3]}.items() if v}
    if not basis:
        return None, {}

    # CAPABILITY tier: the notice itself names the client's product, a
    # competitor, or a capability phrase. Strongest, and rare for a software
    # vendor: measured 2026-07-27, ZERO of 2,107 event-language notices named
    # Red Hat, OpenShift, Ansible, RHEL, or any named rival. Government
    # industry days are about programs, not COTS brands.
    if term_hits or product_hits or competitor_hits:
        parts = []
        if product_hits:
            parts.append(f"names {product_hits[0]}")
        elif term_hits:
            parts.append(f"matches {term_hits[0]}")
        if competitor_hits:
            parts.append(f"{competitor_hits[0]} is positioned here")
        if agency_hit:
            parts.append(f"{agency_hit} carries cited awards in this pack")
        basis["strength"] = "capability"
        return " · ".join(parts[:3]), basis

    # CORRIDOR tier: the event sits in a buying corridor this client is
    # actually funded in, inside the approved NAICS boundary. This is NOT
    # "an agency you sell to exists" padding: the line must NAME the pack
    # record that makes the corridor real, with its dollars and its clock.
    # Missing agency evidence or boundary code means it does not ship.
    if not (agency_hit and naics_hit):
        return None, {}
    evidence = (frame.get("agency_evidence") or {}).get(agency_hit.casefold())
    if evidence is None or not getattr(evidence, "record_id", None):
        return None, {}
    dollars = evidence.obligated_dollars or 0
    clock = evidence.period_end or evidence.potential_end_date
    basis["strength"] = "corridor"  # rule name, never rendered copy
    basis["cited_record"] = {
        "record_id": evidence.record_id, "url": evidence.url,
        "obligated_dollars": dollars, "period_end": clock}
    money = f"${dollars:,.0f}" if dollars else "a cited award"
    tail = f", clock {clock}" if clock else ""
    # THE EVENT ID RIDES THE NOTE. Several events legitimately sit in one
    # corridor and cite the SAME pack record, and without something
    # event-specific all of them render the identical sentence: six were
    # caught as repeated_prose on a live press. The corridor is why the event
    # is relevant; the id is which event this is.
    return (f"{agency_hit} buying account · your pack cites {evidence.record_id} at "
            f"{money}{tail} · event NAICS {naics_hit} is inside the boundary"),\
        basis


def harvest_tier_a(
    rows: Iterable[dict],
    pack: Any,
    *,
    today: Optional[date] = None,
    horizon_days: int = 400,
    recently_closed_days: int = 21,
) -> tuple[list[EventRecord], dict]:
    """Screen the extract's notice universe for client-relevant events.

    Returns (candidates, stats). URLs are NOT yet verified here; the caller
    verifies live and drops anything that fails.
    """
    today = today or date.today()
    frame = pack_frame(pack)
    stats = {"scanned": 0, "event_type": 0, "event_language": 0,
             "client_relevant": 0, "dated": 0, "kept": 0}
    out: list[EventRecord] = []
    for row in rows:
        stats["scanned"] += 1
        ntype = str(row.get("Type") or "").strip().casefold()
        if ntype not in EVENT_NOTICE_TYPES:
            continue
        stats["event_type"] += 1
        title = " ".join(str(row.get("Title") or "").split())
        desc = " ".join(str(row.get("Description") or "").split())
        blob = f"{title} {desc}".casefold()
        phrase = next((p for p in EVENT_PHRASES if p in blob), None)
        if phrase is None:
            continue
        stats["event_language"] += 1
        rationale, basis = score_relevance(row, frame)
        if rationale is None:
            continue
        stats["client_relevant"] += 1

        pos = blob.find(phrase)
        event_start, span = extract_event_date(f"{title} {desc}", pos)
        deadline = _iso(str(row.get("ResponseDeadLine") or ""))
        if not event_start and not deadline:
            continue                      # nothing verifiable to stand on
        stats["dated"] += 1
        anchor = event_start or deadline
        try:
            anchor_date = date.fromisoformat(anchor)
        except ValueError:
            continue
        # SPEC: "An event whose registration closes before the client would
        # see it is flagged, not hidden." Keep a short recently-closed tail
        # so a just-missed industry day still surfaces (flagged), while
        # genuinely stale notices stay out.
        if (today - anchor_date).days > recently_closed_days:
            continue
        if (anchor_date - today).days > horizon_days:
            continue                      # beyond the horizon

        # LINKAGE LAW: the extract's own Link column is the login-walled
        # sam.gov/workspace form, which client HTML bans outright
        # (lint_sam_workspace_links). Rebuild the PUBLIC notice URL from the
        # canonical builder; a notice whose id will not build does not ship.
        notice_id = str(row.get("NoticeId") or "").strip()
        try:
            link = build_sam_notice_link(notice_id).url
        except ValueError:
            continue                      # no verifiable public URL
        place = " ".join(x for x in (str(row.get("PopCity") or "").strip(),
                                     str(row.get("PopState") or "").strip())
                         if x) or None
        out.append(EventRecord(
            event_id=str(row.get("NoticeId") or "").strip(),
            name=title or "(untitled notice)",
            event_type="government-hosted",
            source_system="sam_extract",
            url=link,
            host=" ".join(str(row.get("Department/Ind.Agency") or "").split()),
            host_office=" ".join(str(row.get("Sub-Tier") or "").split()) or None,
            location=place,
            is_virtual=("virtual" in blob or "microsoft teams" in blob
                        or "webex" in blob or "zoom" in blob),
            event_start=event_start,
            event_date_span=span,
            registration_deadline=deadline,
            registration_deadline_source=(
                "SAM extract ResponseDeadLine" if deadline else None),
            registration_closed=bool(
                deadline and date.fromisoformat(deadline) < today),
            relevance=rationale,
            relevance_basis=basis,
            matched_phrase=phrase,
            notice_type=str(row.get("Type") or "").strip(),
            naics=re.sub(r"[^0-9]", "", str(row.get("NaicsCode") or ""))[:6] or None,
            posted_date=_iso(str(row.get("PostedDate") or "")),
        ))
        stats["kept"] += 1
    # SAM reposts the same gathering under multiple notice ids (a BRAND NAME_
    # variant, an amendment, a second office). One event, one row: keep the
    # richest record per (normalized title, anchor date) and count the merge.
    def _fold(name: str) -> str:
        text = re.sub(r"[^a-z0-9 ]", " ", name.casefold())
        text = re.sub(r"\b(brand name|sources sought|special notice|"
                      r"presolicitation|amendment|update)\b", " ", text)
        return " ".join(text.split())[:70]

    best: dict[tuple, EventRecord] = {}
    for event in out:
        key = (_fold(event.name),
               event.event_start or event.registration_deadline or "")
        held = best.get(key)
        richness = (bool(event.event_start), bool(event.registration_deadline),
                    len(event.relevance))
        if held is None or richness > (bool(held.event_start),
                                       bool(held.registration_deadline),
                                       len(held.relevance)):
            best[key] = event
    stats["deduped"] = len(out) - len(best)
    stats["kept"] = len(best)
    merged = sorted(best.values(),
                    key=lambda e: (e.event_start or e.registration_deadline
                                   or "9999", e.name))
    return merged, stats


def verify_urls(
    events: list[EventRecord],
    *,
    fetch: Optional[Callable[[str], tuple[int, str]]] = None,
    limit: Optional[int] = None,
) -> tuple[list[EventRecord], list[dict]]:
    """LAW: an event with no verifiable URL does not ship. Returns
    (verified, skipped_report)."""
    if fetch is None:
        import httpx
        from tools.api.sam_gov import SAM_HEADERS

        client = httpx.Client(timeout=30.0, headers=SAM_HEADERS,
                              follow_redirects=True,
                              transport=httpx.HTTPTransport(local_address="0.0.0.0"))

        def fetch(url: str) -> tuple[int, str]:  # noqa: F811
            r = client.get(url)
            return r.status_code, r.text[:4000]

    verified, skipped = [], []
    for event in events[:limit] if limit else events:
        try:
            status, body = fetch(event.url)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"event_id": event.event_id, "name": event.name,
                            "url": event.url, "reason": f"{type(exc).__name__}"})
            continue
        event.url_status = status
        if status != 200:
            skipped.append({"event_id": event.event_id, "name": event.name,
                            "url": event.url, "reason": f"HTTP {status}"})
            continue
        event.url_verified = True
        verified.append(event)
    _log(f"URL verification: {len(verified)} verified, {len(skipped)} skipped")
    return verified, skipped


def load_extract_rows(path: str) -> Iterable[dict]:
    csv.field_size_limit(sys.maxsize)
    with open(path, encoding="utf-8", errors="replace") as f:
        yield from csv.DictReader(f)
