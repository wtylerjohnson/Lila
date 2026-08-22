"""Industry days: a filter and a label over notices already in the store.

NOT NEW RETRIEVAL. An industry day posts as a Special Notice, which the
leverage ladder already ranks 2. This module decides which rank-2 notices are
an EVENT and renders them as one, using rows the store already holds.

WHAT COUNTS, and what emphatically does not. The obvious filter is any notice
mentioning a gathering, and it is wrong. Measured on the live corridor:

    12,084 in-corridor notices
     1,083 mention any event phrase, or are a Special Notice
       334 of those are still in the future
       100 match an EVENT phrase (site visit and pre-proposal excluded)
        20 of those are still in the future
         5 survive deduplication by (title, agency)

"site visit" alone matched 145 notices and "pre-proposal conference" 63.
Neither is an industry day: they are ROUTINE PROCUREMENT MECHANICS that
appear inside construction and audio-visual solicitations ("a site visit will
be held prior to the due date"). Including them returned Galley Grab-N-Go,
CCTV - Community Commons, and Fort Yuma Automatic Door Operators. They are
excluded, and that exclusion is most of the drop from 334 to 20.

THE EVENT DATE IS NOT PARSED. The date an industry day is actually HELD lives
in prose, in the description, in whatever format the contracting officer
chose. This module never extracts it and never asserts it. What it renders is
the RESPONSE DEADLINE, which the store holds as a structured field and which
is the actionable date anyway: it is the date by which a vendor must
register. A candidate date lifted from prose may be surfaced ALONGSIDE THE
SENTENCE IT CAME FROM and marked unverified, never on its own. A wrong date
on an event is worse than no date, because a client who misses a session
because we published the wrong day does not get another one.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any, Iterable, Optional

from agents.golden_press.events import EventRecord, pack_frame, score_relevance
from agents.golden_press.events import _iso as _iso_date
from agents.reports.links import build_sam_notice_link

# Phrases that name an EVENT. A notice is an industry day because it IS one.
EVENT_PHRASES = (
    "industry day",
    "vendor day",
    "reverse industry day",
    "capability briefing",
    "market research event",
    "one-on-one session",
)

# Phrases that describe a STEP INSIDE a procurement, not a standalone event.
# Kept here explicitly so nobody re-adds them believing they were forgotten.
PROCUREMENT_MECHANICS = (
    "site visit",                    # 145 hits, almost all construction/AV
    "pre-proposal conference",       # 63 hits, a step in a solicitation
    "pre-solicitation conference",   # 6 hits, same
)

# Mirrors events.py tier A: a just-missed gathering is flagged, not hidden.
RECENTLY_CLOSED_DAYS = 21
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}$")

_EVENT = re.compile("|".join(re.escape(p) for p in EVENT_PHRASES), re.I)

# A date in prose, with enough shape to be worth showing a human. NEVER used
# as the event date on its own; always carried with its sentence.
_PROSE_DATE = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{4})\b", re.I)

# The event HAPPENS then, rather than the notice was posted then.
_EVENT_VERB = re.compile(
    r"\b(will be held|is scheduled|scheduled for|will take place|takes place|"
    r"will host|will convene|to be held|convenes?|session on|day on)\b", re.I)
# Words that mark a date as administrative rather than the event itself.
_POSTING_WORD = re.compile(
    r"\b(posted|amendment|updated|revised|issued|published)\b", re.I)


def is_industry_day(notice_type: Optional[str], title: Optional[str],
                    description: Optional[str] = None) -> bool:
    """True when this notice IS an event, not merely mentions one.

    The title is the strong signal. A Special Notice whose DESCRIPTION names
    an event also counts, because agencies routinely title those by program
    ("Next Generation Command and Control") and say "industry day" inside.
    A Solicitation mentioning an event in its description does NOT count:
    that is the procurement describing its own steps.
    """
    if _EVENT.search(str(title or "")):
        return True
    return bool(str(notice_type or "") == "Special Notice"
                and _EVENT.search(str(description or "")))


def candidate_event_date(description: Optional[str]) -> Optional[dict]:
    """A date found in prose, WITH the sentence it came from, marked unverified.

    Returns None when nothing date-shaped is present. The caller must render
    the sentence beside the date or not render the date at all: the whole
    point is that a reader can check it in one glance.
    """
    text = " ".join(str(description or "").split())
    if not text:
        return None
    # A DATE IS NOT AN EVENT DATE just because it is near event language.
    # Measured on the five live rows: every date my first pass surfaced was a
    # POSTING or AMENDMENT date ("AMENDMENT 01 (Posted July 21, 2026)",
    # "This notification, posted 24 June 2026"). Rendering those beside an
    # industry day would read as "the event is on the 21st" and be wrong
    # every time. A candidate is only offered when the surrounding sentence
    # says the event HAPPENS then.
    match = None
    for cand in _PROSE_DATE.finditer(text):
        lo = max(0, cand.start() - 120)
        window = text[lo:cand.end() + 120]
        if _EVENT_VERB.search(window) and not _POSTING_WORD.search(window):
            match = cand
            break
    if not match:
        return None
    start = text.rfind(".", 0, match.start()) + 1
    end = text.find(".", match.end())
    sentence = text[start:end if end > 0 else min(len(text), match.end() + 120)]
    return {
        "value": match.group(0),
        "verified": False,
        "source_sentence": sentence.strip()[:240],
        "caution": ("lifted from prose, NOT a structured field. Check the "
                    "sentence before relying on it."),
    }


def collect(rows: Any, *, today: Optional[str] = None) -> dict:
    """(rows, receipt) for the industry-day band, deduped and future-only.

    Every number in the receipt is a real count from this pass, so an empty
    band can state what it screened rather than reading as a hole.
    """
    cutoff = today or date.today().isoformat()
    rows = list(rows)
    # "Still in the extract" is the freshness signal for undated notices:
    # the newest last_seen across this row set is the store's now.
    latest_seen = max((str(_cell(r, "last_seen") or "")[:10] for r in rows),
                      default="")
    screened = future = matched = 0
    undated_kept = undated_stale = recently_closed = 0
    seen: dict[tuple, dict] = {}
    for r in rows:
        screened += 1
        title = r["title"] if "title" in r.keys() else r.get("title")
        ntype = r["notice_type"] if "notice_type" in r.keys() else r.get("notice_type")
        desc = (r["description_prefix"] if "description_prefix" in r.keys()
                else r.get("description_prefix"))
        deadline = (r["deadline"] if "deadline" in r.keys() else r.get("deadline")) or ""
        if not is_industry_day(ntype, title, desc):
            continue
        matched += 1
        # THREE DEADLINE STATES, NOT ONE COMPARISON (audit 2026-07-30). The
        # old `str(deadline)[:10] < cutoff` treated an EMPTY deadline as
        # past - "" sorts before every date - and silently dropped 103 of
        # 696 matched gatherings (14.8%, measured live). An industry day
        # posted without a structured deadline is normal contracting-officer
        # behavior, not a finished event.
        day = str(deadline)[:10]
        if not day or not _ISO_DAY.match(day):
            # Undated: kept while sam.gov still lists the notice (its
            # last_seen equals the store's newest), stale otherwise. A
            # dropped notice with no date is an unknowable room; an ACTIVE
            # one is a live room whose date lives in prose or attachments.
            row_seen = str(_cell(r, "last_seen") or "")[:10]
            if not latest_seen or row_seen != latest_seen:
                undated_stale += 1
                continue
            undated_kept += 1
            day = ""
        elif day < cutoff:
            # Mirrors tier A's recently-closed tail (events.py): an event
            # whose registration closed days ago is FLAGGED, not hidden -
            # a just-missed industry day is intelligence about the program.
            age = (date.fromisoformat(cutoff)
                   - date.fromisoformat(day)).days
            if age > RECENTLY_CLOSED_DAYS:
                continue
            recently_closed += 1
        else:
            future += 1
        key = (str(title or "").strip().lower(),
               str((r["agency"] if "agency" in r.keys() else r.get("agency")) or "").strip().lower())
        if key in seen:
            continue
        seen[key] = {
            "notice_id": r["notice_id"] if "notice_id" in r.keys() else r.get("notice_id"),
            "title": title,
            "notice_type": ntype,
            "agency": r["agency"] if "agency" in r.keys() else r.get("agency"),
            "sub_agency": r["subtier"] if "subtier" in r.keys() else r.get("subtier"),
            "office": r["office"] if "office" in r.keys() else r.get("office"),
            "registration_deadline": day or None,
            "candidate_event_date": candidate_event_date(desc),
        }
    return {
        "rows": sorted(seen.values(),
                       key=lambda x: x["registration_deadline"] or "9999"),
        "receipt": {
            "screened": screened,
            "matched_event_phrase": matched,
            "still_future": future,
            "distinct_after_dedupe": len(seen),
            "undated_kept_active": undated_kept,
            "undated_dropped_stale": undated_stale,
            "recently_closed_kept": recently_closed,
            "deadline_policy": ("an empty deadline is UNKNOWN, never past: "
                                "kept while sam.gov still lists the notice, "
                                "dropped as stale otherwise; a deadline "
                                f"closed within {RECENTLY_CLOSED_DAYS} days "
                                "is kept and flagged, mirroring tier A"),
            "event_phrases": list(EVENT_PHRASES),
            "excluded_as_procurement_mechanics": list(PROCUREMENT_MECHANICS),
            "event_date_policy": ("registration deadline is the structured, "
                                  "actionable date; the event date itself is "
                                  "never parsed from prose and asserted"),
            "as_of": cutoff,
        },
    }


# --------------------------------------------------------------------------- #
# Wiring into the events lane (2026-07-30)
#
# `collect` decides which stored notices ARE events. It does not decide whether
# a given client should see one, and it does not speak the shape the events
# band consumes. This section is the seam between the two, and it holds three
# facts that cost a wrong press to learn:
#
#   1. THE SOURCE IS THE STORE, NOT THE DAILY EXTRACT. `collect` reads the
#      notice-store column vocabulary (notice_id/title/notice_type/agency/
#      subtier/office/deadline/description_prefix). The extract's CSV headers
#      are different words entirely (NoticeId/Title/Type/ResponseDeadLine/
#      Department-Ind.Agency). Handing extract rows to `collect` matches
#      nothing and reports a confident zero. The store is also the RIGHT
#      source: 6.4% of notices vanish from the extract every three days, and
#      the store is the thing that accumulates them.
#   2. RELEVANCE IS NOT `collect`'S JOB. Measured 2026-07-30 the live store
#      held 67 distinct future industry days, essentially none of them
#      connected to any one client's book. `score_relevance` is the events
#      lane's single owner of "one line bound to a pack fact"; industry days
#      go through it verbatim rather than growing a second dialect of
#      relevance.
#   3. THE EVENT DATE STAYS UNASSERTED. `event_start` is ALWAYS None here.
#      This module never parses the day an industry day is held, so a
#      candidate lifted from prose rides inertly in `relevance_basis`, where
#      the operator can see it and no renderer can print it as fact.
# --------------------------------------------------------------------------- #

# Notice-store column -> the extract header `score_relevance` already reads.
# One translation, in one place: the relevance screen keeps its single
# vocabulary and never learns that a second source exists.
_STORE_TO_EXTRACT = {
    "title": "Title",
    "description_prefix": "Description",
    "naics": "NaicsCode",
    "agency": "Department/Ind.Agency",
    "subtier": "Sub-Tier",
    "office": "Office",
}


def _cell(row: Any, key: str) -> Any:
    """One read that works for a sqlite3.Row and a plain dict alike.

    `collect` probes `.keys()` for the same reason: a sqlite3.Row raises
    IndexError on a missing column rather than returning None.
    """
    try:
        keys = row.keys()
    except AttributeError:
        return None
    return row[key] if key in keys else None


def _clean(value: Any) -> str:
    """Collapsed text with decode wreckage removed.

    The store ingests the extract with errors="replace", so seven of the 67
    live industry-day titles carry U+FFFD where a cp1252 dash used to be
    ("Reverse Industry Day <?> Capabilities"). lint_emdash does not fire on it
    because it is not an em dash, which is exactly why it would have shipped.
    """
    return " ".join(str(value or "").replace("�", " ").split())


def matched_event_phrase(*texts: Any) -> Optional[str]:
    """The phrase that made this notice an event, carried on the record."""
    for text in texts:
        found = _EVENT.search(str(text or ""))
        if found:
            return found.group(0).casefold()
    return None


def _in_engagement_scope(source: Any, scope: Any) -> bool:
    """The operator's agency universe, asked through its one owner."""
    from tools.relevance.scope import in_scope

    allowed, _reason = in_scope(
        {"agency": _cell(source, "agency"),
         "subtier": _cell(source, "subtier"),
         "office": _cell(source, "office")}, scope)
    return bool(allowed)


def naics_boundary(pack: Any) -> set[str]:
    """The client's APPROVED NAICS codes, not the codes its awards happen to
    carry. `pack_frame` merges both for the corridor screen; widening the
    events band on an incidental award code would let the boundary drift."""
    research = getattr(pack, "research", None) or {}
    return {str(code).strip() for code in
            (research.get("inferred_naics") or []) if str(code).strip()}


def room_to_be_in(source: Any, *, boundary: set[str],
                  scope: Any = None) -> Optional[tuple[str, dict]]:
    """(rationale, basis) for an event inside the client's declared universe.

    THE SECOND ROUTE IN, and deliberately weaker than a bid's (2026-07-30).
    `score_relevance` asks whether an event sits in a corridor where the pack
    already holds a cited award. That is the right question for a live
    solicitation and the wrong one for a gathering: it requires the client to
    already be winning at an agency before we will let them walk into its
    room, and an industry day missed is not re-run.

    So an event also qualifies on the two boundaries the OPERATOR set: the
    engagement scope (which agencies this engagement is about at all) and the
    approved NAICS boundary (what the client actually sells). Both legs are
    required. Either alone was measured on 2026-07-30 and both failed:
    NAICS alone returned two DoD events into a civilian-scoped engagement,
    and scope alone returned FEMA Emergency Meal and Star of Life Ambulances.
    """
    if not boundary:
        return None
    naics = re.sub(r"[^0-9]", "", str(_cell(source, "naics") or ""))[:6]
    if not naics or naics not in boundary:
        return None
    scoped = scope is not None
    if scoped and not _in_engagement_scope(source, scope):
        return None
    agency = _clean(_cell(source, "agency")) or "this buyer"
    # NOT a corridor claim, and never a scope claim the run cannot make: an
    # unscoped run says only what it checked. The line states exactly what the
    # evidence supports and names what is missing.
    where = ("and inside this engagement's agency scope" if scoped
             else "with no agency scope set on this engagement")
    # BANNED VOCABULARY (L7, operator 2026-08-06: never say it, anywhere).
    # This one string shipped three consecutive apexanalytix presses
    # uncertified: the validator's scan is mechanical and full-artifact, so a
    # single occurrence fails the whole document. Say what is meant instead.
    return (f"inside the approved NAICS boundary ({naics}) {where}; {agency} "
            f"carries no cited award in this pack, so this is a room to be "
            f"in, not a position already held",
            {"strength": "boundary", "naics": naics, "agency": agency,
             "scope_checked": scoped,
             "route": "engagement_scope_and_naics_boundary" if scoped
                      else "naics_boundary_only"})


def to_event_record(row: dict, source: Any, *, frame: dict, today: date,
                    boundary: Optional[set[str]] = None,
                    scope: Any = None) -> Optional[EventRecord]:
    """One `collect` row as an EventRecord, or None when it cannot ship.

    Returns None for the two honest refusals: a notice with no relevance to
    this pack by EITHER route, and a notice whose id will not build a public
    URL.
    """
    # THE OPERATOR BOUNDARY GATES THE WHOLE BAND, BOTH ROUTES (2026-07-30).
    # The corridor screen matches on capability terms and never consulted the
    # engagement scope, so a DoD industry day whose title happened to say
    # "network visibility" shipped into a civilian-scoped engagement. Scope is
    # an operator decision about which agencies this engagement is about; a
    # term hit is not an argument against it.
    if scope is not None and not _in_engagement_scope(source, scope):
        return None
    adapted = {header: _cell(source, col)
               for col, header in _STORE_TO_EXTRACT.items()}
    rationale, basis = score_relevance(adapted, frame)
    if rationale is None:
        # Additive only: anything the corridor screen already admits is
        # untouched, and this can only widen the band, never narrow it.
        fallback = room_to_be_in(source, boundary=boundary or set(),
                                 scope=scope)
        if fallback is None:
            return None
        rationale, basis = fallback
    try:
        url = build_sam_notice_link(str(row.get("notice_id") or "")).url
    except ValueError:
        return None

    # The prose candidate is preserved, never promoted. `relevance_basis` is
    # inert: the validator does not read dates out of it and no renderer
    # prints it, so the operator keeps the lead without the report making a
    # claim this module has always refused to make.
    candidate = row.get("candidate_event_date")
    if candidate:
        basis = {**basis, "industry_day_candidate_date": candidate}

    deadline = row.get("registration_deadline") or None
    return EventRecord(
        event_id=str(row.get("notice_id") or ""),
        name=_clean(row.get("title")) or "(untitled notice)",
        event_type="government-hosted",
        source_system="sam_extract",
        url=url,
        host=_clean(row.get("agency")),
        host_office=_clean(row.get("sub_agency")) or None,
        event_start=None,                      # never parsed, never asserted
        registration_deadline=deadline,
        registration_deadline_source=(
            "SAM notice store deadline column" if deadline else None),
        registration_closed=bool(
            deadline and str(deadline)[:10] < today.isoformat()),
        relevance=rationale,
        relevance_basis=basis,
        matched_phrase=matched_event_phrase(
            row.get("title"), _cell(source, "description_prefix")),
        notice_type=_clean(row.get("notice_type")) or None,
        naics=re.sub(r"[^0-9]", "", str(_cell(source, "naics") or ""))[:6] or None,
        posted_date=_iso_date(str(_cell(source, "posted") or "")),
        retrieved_at=str(_cell(source, "last_seen") or "") or None,
    )


def drop_already_shipped(candidates: list[EventRecord],
                         held: Iterable[Any]) -> list[EventRecord]:
    """Industry days the events band is not already carrying.

    ONE GATHERING, ONE ROW. The same notice legitimately reaches both tiers:
    tier A sees it in today's extract and this lane sees it in the store that
    ingested that same extract. Shipping both doubles the record and reads as
    repeated prose in the band.
    """
    def _id(item: Any) -> str:
        # Tier A hands over EventRecords, but a resumed pack carries the same
        # rows as plain dicts. Matching one shape only would silently double
        # every row on exactly the presses that reuse a pack.
        if isinstance(item, dict):
            return str(item.get("event_id") or "")
        return str(getattr(item, "event_id", "") or "")

    seen = {_id(e) for e in held}
    return [c for c in candidates if str(c.event_id or "") not in seen]


def harvest_from_store(rows: Iterable[Any], pack: Any, *,
                       today: Optional[date] = None,
                       scope: Any = None,
                       ) -> tuple[list[EventRecord], dict]:
    """(candidates, receipt) for the industry days this pack should see.

    Mirrors `events.harvest_tier_a`: URLs are NOT verified here, because the
    caller verifies live and drops whatever fails. Every count in the receipt
    is real, so an empty band states what it screened rather than reading as
    a hole in the report.
    """
    today = today or date.today()
    rows = list(rows)                  # a generator would screen exactly once
    gathered = collect(rows, today=today.isoformat())
    by_id = {str(_cell(r, "notice_id") or ""): r for r in rows}

    frame = pack_frame(pack)
    boundary = naics_boundary(pack)
    out: list[EventRecord] = []
    unbuildable = 0
    for entry in gathered["rows"]:
        source = by_id.get(str(entry.get("notice_id") or ""))
        if source is None:
            continue
        # Buildability is checked FIRST so the receipt cannot blame the wrong
        # cause: an unbuildable id is a data problem worth seeing, while an
        # irrelevant notice is the screen doing its job. Classifying after the
        # relevance check would file every irrelevant bad id under the former.
        try:
            build_sam_notice_link(str(entry.get("notice_id") or ""))
        except ValueError:
            unbuildable += 1
            continue
        record = to_event_record(entry, source, frame=frame, today=today,
                                 boundary=boundary, scope=scope)
        if record is None:
            continue
        out.append(record)

    by_route = Counter(str((r.relevance_basis or {}).get("strength") or "?")
                       for r in out)
    receipt = {
        **gathered["receipt"],
        "source": "sam_notice_store",
        "metered_quota_spent": 0,
        "client_relevant": len(out),
        "dropped_unbuildable_url": unbuildable,
        "relevance_screen": ("events.score_relevance (cited corridor), then "
                             "engagement scope + approved NAICS boundary for "
                             "a room worth being in"),
        "engagement_scope": getattr(scope, "resolved_basis", None) or "UNSCOPED",
        "naics_boundary": sorted(boundary),
        "kept_by_route": dict(by_route),
    }
    return out, receipt
