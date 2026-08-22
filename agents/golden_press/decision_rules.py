"""Deterministic account-decision rules over a golden evidence pack.

WHY THIS MODULE (operator, 2026-08-03). A month of measurement says the
product's spine is dated account decisions with a named rival and a record
behind every claim, not solicitation retrieval. The Riverbed rework proved
the structure by hand; these rules make the machine produce it.

DOCTRINE. Pure functions over a pack (plus the durable notice store for R2).
No LLM, no network, no metered call. Every output carries its evidence rows,
and every claim asserts only what those rows show: an entity term matched in
an award description, a recipient identity, a period end, a verbatim
sentence. Rules that find nothing return empty lists that render as stated
zeros; nothing here pads.

THE CLOCK. ``today`` defaults to the PACK's own generated_at date, never the
wall clock, so the same pack always yields the same decisions (replay
determinism, and the frozen-clock doctrine applied at design time instead of
repaired after detonation).

SIDE CLASSIFICATION IS IMPORTED, NEVER REIMPLEMENTED. Term derivation rides
``retrieval.entity_terms`` (which accepts dict-shaped entities), affinity
rides ``retrieval._affinity``, recipient identity rides
``rollups.resolve_relationship``, and R2's entity matching rides the shared
``sam_lanes`` guards (build_matchers / reject_entity_hit / _SOLE), so a
guard fixed there is fixed here.
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from agents.golden_press.rollups import resolve_relationship
from agents.golden_press.sam_lanes import (
    _SOLE,
    _corpus_negatives,
    JA_TYPES,
    _pattern,
    aliases,
    build_matchers,
    reject_entity_hit,
)
from agents.golden_press.retrieval import _affinity, entity_terms
from agents.golden_press.press_time import local_press_date

# v2 (2026-08-03 defect round): R1 buys at the awarding SUBTIER with the
# materiality disclosure and minor-rival tier; R2 enforces the trailing
# window with an excluded-records receipt and the title==description-head
# receipt dedupe.
DECISION_RULES_VERSION = "decision_rules.v2.2026-08-03"

# --------------------------------------------------------------------------- #
# R3 · the one dated vehicle fact, versioned and cited
# --------------------------------------------------------------------------- #
# HARDCODED BY OPERATOR DECISION (2026-08-03): one dated fact the pack cannot
# carry. Never assert any prime's SEWP VI status; the constant speaks only to
# the vehicle's own published dates.
SEWP_V_FACTS = {
    "version": "sewp_v.v1.2026-08-03",
    "vehicle_class": "SEWP V",
    "ordering_ends": "2026-09-30",
    "option_period_ends": ("2027-01-31", "2027-04-30"),
    "successor": "SEWP VI",
    "successor_anticipated_start": "2026-11-01",
    "citation": "sewp.nasa.gov/contract_info.shtml",
}

# Every date the R3 constant can put into rendered copy, for the validator's
# date-provenance gate. One owner here; validate.py imports it.
RULE_CONSTANT_DATES = (
    SEWP_V_FACTS["ordering_ends"],
    *SEWP_V_FACTS["option_period_ends"],
    SEWP_V_FACTS["successor_anticipated_start"],
)

# --------------------------------------------------------------------------- #
# D2/D3 · versioned rule constants (operator defect round, 2026-08-03)
# --------------------------------------------------------------------------- #
# D2: materiality disclosure on R1. Every card states both sides' totals and
# their ratio; a card whose rival paper is under the ratio threshold OR under
# the absolute floor belongs to the collapsed "minor rival presence" tier
# below the main cards. Never suppressed, never headlined.
# D5 (operator refiling, 2026-08-03): tiering is RATIO-ONLY. The absolute
# dollar floor demoted an 82.5% head-to-head (OPO) because both books were
# small; scale is printed on every card instead of deciding the tier. A
# ratio that cannot be computed (zero or fully unvalued client side) tiers
# MAIN with the not-computable note printed — never silently demoted.
R1_MATERIALITY = {
    "version": "r1_materiality.v2.2026-08-03",
    "ratio_floor": 0.05,                     # MAIN iff rival/client >= 5%
}

# D3: displacement alerts are dated intelligence. An alert's source must hold
# current paper (period end on/after the press date) or recent action (inside
# the trailing window); older matches are counted in the receipt, never
# rendered as alerts.
R2_WINDOW = {
    "version": "r2_window.v1.2026-08-03",
    "trailing_months": 24,
}

# Every dollar figure the rule constants can put into rendered copy (the
# materiality floor renders in the minor-tier summary), for the validator's
# dollar-provenance gate. Same one-owner pattern as RULE_CONSTANT_DATES.
RULE_CONSTANT_DOLLARS: tuple = ()


# --------------------------------------------------------------------------- #
# TASK 1 · vehicle derivation from the USAspending generated id
# --------------------------------------------------------------------------- #
_CONT_AWD_RE = re.compile(r"CONT_AWD_(?P<rest>[^/?#]+)", re.I)
_NONEISH = {"", "-none-", "none", "-NONE-"}


def parse_award_url(url: Optional[str]) -> Optional[dict]:
    """(piid, agency, parent, parent_agency) parsed from a USAspending award
    URL's CONT_AWD generated id, or None when the URL carries no such id.

    The generated id joins exactly four segments with underscores; a missing
    parent arrives as ``-NONE-``. rsplit keeps a piid that itself contains an
    underscore intact.
    """
    match = _CONT_AWD_RE.search(str(url or ""))
    if not match:
        return None
    parts = match.group("rest").rsplit("_", 3)
    if len(parts) != 4:
        return None
    piid, agency, parent, parent_agency = parts

    def _clean(value: str) -> Optional[str]:
        return None if value.strip() in _NONEISH else value.strip()

    return {
        "piid": _clean(piid),
        "agency": _clean(agency),
        "parent": _clean(parent),
        "parent_agency": _clean(parent_agency),
    }


def classify_vehicle(parent: Optional[str],
                     parent_agency: Optional[str]) -> str:
    """Prefix-only classification. No lookups, no web calls (operator spec).

    NNG15S* at agency 8000 is SEWP V; GS*/47Q* is GSA; HHSN* is HHS;
    HSHQDC* is DHS; no parent is a definitive contract; anything else is
    IDV (unclassified) and the raw parent rides on the record for the reader.
    """
    if not parent:
        return "definitive"
    p = str(parent).upper()
    if p.startswith("NNG15S") and str(parent_agency or "") == "8000":
        return "SEWP V"
    if p.startswith(("GS", "47Q")):
        return "GSA"
    if p.startswith("HHSN"):
        return "HHS"
    if p.startswith("HSHQDC"):
        return "DHS"
    return "IDV (unclassified)"


def annotate_vehicles(records: Iterable[Any]) -> int:
    """Parse and classify every record with a CONT_AWD URL, in place.

    Fills parent_award_id when the search row lacked it, always fills
    parent_award_agency and vehicle_class. Returns how many records were
    annotated. Idempotent: re-annotating produces the same values.
    """
    annotated = 0
    for record in records:
        parsed = parse_award_url(getattr(record, "url", None))
        if parsed is None:
            continue
        if parsed["parent"] and not getattr(record, "parent_award_id", None):
            record.parent_award_id = parsed["parent"]
        record.parent_award_agency = parsed["parent_agency"]
        record.vehicle_class = classify_vehicle(
            parsed["parent"] or getattr(record, "parent_award_id", None),
            parsed["parent_agency"])
        annotated += 1
    return annotated


# --------------------------------------------------------------------------- #
# shared plumbing
# --------------------------------------------------------------------------- #
def _pack_kind_by_name(pack: Any) -> dict[str, str]:
    """term.casefold() -> kind, derived with retrieval's OWN term expansion.

    entity_terms accepts dict-shaped entities, so the pack's research dict is
    handed to the exact function that derived the search terms; the bare
    product-suffix rule and the client-name term therefore classify here
    exactly as they classified at retrieval time.
    """
    entities = (getattr(pack, "research", None) or {}).get("entities") or {}
    shim = SimpleNamespace(
        client_name=str(getattr(pack, "client_name", "") or ""),
        research_entities=[{"kind": kind, "name": name}
                           for kind, names in entities.items()
                           for name in (names or [])],
    )
    return {name.casefold(): kind
            for kind, name in entity_terms(shim, include_resellers=True)}


def _record_side(record: Any, pack: Any, kind_by_name: dict[str, str]) -> str:
    """core | competitor | channel for one record.

    Affinity from the record's own entity hits first (the terms that
    surfaced it), then recipient identity: an award WON by the client (or an
    operator-listed alias) is client paper regardless of what the
    description says, and one won by a named rival is rival paper.
    """
    affinity = _affinity(record, kind_by_name)
    relationship = resolve_relationship(
        getattr(record, "recipient", "") or "", pack)["relationship"]
    if relationship == "client_entity":
        return "core"
    if relationship == "competitor" and affinity != "core":
        return "competitor"
    return affinity


def _iso_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _decision_today(pack: Any, today: Optional[date]) -> date:
    """The pack's own date, never the wall clock (replay determinism)."""
    if today is not None:
        return today
    stamped = _iso_date(local_press_date(getattr(pack, "generated_at", None)))
    return stamped or date.today()


def _evidence_row(record: Any) -> dict:
    return {
        "record_id": record.record_id,
        "url": record.url,
        "recipient": record.recipient,
        "agency": record.agency,
        "sub_agency": record.sub_agency,
        "obligated_dollars": record.obligated_dollars,
        "period_end": record.period_end,
        "entity_hits": list(record.entity_hits or []),
        "vehicle_class": getattr(record, "vehicle_class", None),
        "parent_award_id": getattr(record, "parent_award_id", None),
    }


def _department_key(text: Optional[str]) -> Optional[str]:
    """Canonical department key for the adjacency join, via the shipped
    agency catalog: full names resolve through _department_abbrs, and a
    string that already IS a catalog department abbreviation (the APFS
    lane's 'DHS') resolves to itself. None means unresolvable, and an
    unresolvable agency never joins (ignorance never manufactures
    adjacency)."""
    value = " ".join(str(text or "").split())
    if not value:
        return None
    from tools.agency_scope import _department_abbrs
    resolved = _department_abbrs(value)
    if resolved:
        return sorted(resolved)[0]
    from tools.agencies import AGENCIES
    departments = {a["abbr"] for a in AGENCIES if a.get("parent") is None}
    if value in departments:
        return value
    return None


# --------------------------------------------------------------------------- #
# R1 · head-to-head account decisions
# --------------------------------------------------------------------------- #
def r1_decisions(pack: Any, *, today: Optional[date] = None,
                 kind_by_name: Optional[dict[str, str]] = None) -> dict:
    """Buying subtiers where client paper AND named-rival paper are both in
    the current period.

    D1 (defect round, 2026-08-03): the buying agency is the SUBTIER, never
    the toptier. Toptier grouping manufactured a Department of Defense
    head-to-head out of client paper at one component and rival paper at
    another; a card must live or die on its own subtier evidence. A record
    with no subtier cannot prove a buying account and is EXCLUDED and
    receipted, exactly like an undated record (ignorance never manufactures
    a head-to-head).

    Current means period_end >= today, judged on the enriched period field
    alone. A record with no period_end is EXCLUDED and counted in the
    receipt as undated, not evaluated; potential_end_date never substitutes
    (an option is not a period). Cards rank by the nearest dated period end
    across both sides.

    D2 (defect round, 2026-08-03): every card carries the materiality
    disclosure: both sides' totals and the rival/client ratio. The versioned
    R1_MATERIALITY constants decide the collapsed minor-rival tier; the rule
    never suppresses a card, it only tiers it.
    """
    today = _decision_today(pack, today)
    kind_by_name = kind_by_name or _pack_kind_by_name(pack)
    undated: list[str] = []
    no_subtier: list[str] = []
    by_buyer: dict[str, dict[str, list]] = {}
    for record in getattr(pack, "records", []) or []:
        if record.lane != "L2_entity_award":
            continue
        side = _record_side(record, pack, kind_by_name)
        if side not in ("core", "competitor"):
            continue
        end = _iso_date(record.period_end)
        if end is None:
            undated.append(record.record_id)
            continue
        if end < today:
            continue
        buyer = " ".join(str(record.sub_agency or "").split())
        if not buyer:
            no_subtier.append(record.record_id)
            continue
        slot = by_buyer.setdefault(buyer, {"core": [], "competitor": []})
        slot[side].append(record)

    cards = []
    for buyer, sides in by_buyer.items():
        if not sides["core"] or not sides["competitor"]:
            continue
        both = [*sides["core"], *sides["competitor"]]
        nearest = min(both, key=lambda r: str(r.period_end))
        rival_names = sorted({
            hit for r in sides["competitor"]
            for hit in (r.entity_hits or [])
            if kind_by_name.get(hit.casefold()) == "competitor"
        } | {
            str(r.recipient) for r in sides["competitor"]
            if resolve_relationship(r.recipient or "", pack)["relationship"]
            == "competitor"
        })
        client_total = sum(
            float(r.obligated_dollars or 0.0) for r in sides["core"])
        rival_total = sum(
            float(r.obligated_dollars or 0.0) for r in sides["competitor"])
        client_unvalued = sum(1 for r in sides["core"]
                              if not r.obligated_dollars)
        rival_unvalued = sum(1 for r in sides["competitor"]
                             if not r.obligated_dollars)
        rival_all_unvalued = rival_unvalued == len(sides["competitor"])
        ratio = (rival_total / client_total) if client_total > 0 else None
        not_computable = ratio is None or rival_all_unvalued
        minor = (not not_computable
                 and ratio < R1_MATERIALITY["ratio_floor"])
        cards.append({
            "agency": buyer,
            "client_records": [_evidence_row(r) for r in sorted(
                sides["core"], key=lambda r: str(r.period_end))],
            "rival_records": [_evidence_row(r) for r in sorted(
                sides["competitor"], key=lambda r: str(r.period_end))],
            "rival_entities": rival_names,
            "nearest_end": str(nearest.period_end)[:10],
            "nearest_record_id": nearest.record_id,
            "materiality": {
                "client_total": client_total,
                "rival_total": rival_total,
                "rival_to_client_ratio": ratio,
                "ratio_not_computable": not_computable,
                "client_unvalued_records": client_unvalued,
                "rival_unvalued_records": rival_unvalued,
                "tier": ("minor_rival_presence" if minor else "main"),
                "basis": R1_MATERIALITY["version"],
            },
        })
    cards.sort(key=lambda c: (c["nearest_end"], c["agency"]))
    return {
        "cards": cards,
        "receipt": {
            "current_basis": f"period_end >= {today.isoformat()} "
                             "(pack date, enriched period fields; buying "
                             "agency = awarding subtier)",
            "undated_not_evaluated": sorted(set(undated)),
            "no_subtier_not_evaluated": sorted(set(no_subtier)),
            "materiality_basis": dict(R1_MATERIALITY),
        },
    }


# --------------------------------------------------------------------------- #
# R2 · displacement alerts with verbatim sentence receipts
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def _matched_sentence(text: str, match_span: tuple[int, int]) -> str:
    """The VERBATIM sentence containing the match: the receipt. Sentence
    boundaries are mechanical ([.!?;] + space); the slice is never
    paraphrased, only whitespace-flattened for HTML."""
    start = 0
    for boundary in _SENTENCE_SPLIT.finditer(text):
        if boundary.end() > match_span[0]:
            break
        start = boundary.end()
    tail = _SENTENCE_SPLIT.search(text, match_span[1])
    end = tail.start() + 1 if tail else len(text)
    return " ".join(text[start:end].split())


def _is_displacement_context(notice_type: str, text: str) -> Optional[str]:
    """sole_source_intent | award | None. Sole-source phrasing rides the
    shared sam_lanes._SOLE pattern; a Justification or Award notice type is
    itself the context."""
    if notice_type in JA_TYPES or _SOLE.search(text):
        return "sole_source_intent" if _SOLE.search(text) else "posted_ja"
    if notice_type == "Award Notice":
        return "award"
    return None


def _receipt_text(title: Any, description: Any) -> str:
    """D4 (defect round, 2026-08-03): title==description-head dedupe BEFORE
    the sentence slice. Store notices routinely repeat the title verbatim as
    the description's opening; concatenating both handed the extractor a
    doubled head, so the verbatim receipt rendered the same sentence twice.
    When the normalized description already opens with the normalized title,
    the description alone is the receipt surface."""
    title_norm = " ".join(str(title or "").split())
    desc_norm = " ".join(str(description or "").split())
    if title_norm and desc_norm.casefold().startswith(title_norm.casefold()):
        return desc_norm
    return (title_norm + " " + desc_norm).strip()


def _months_back(anchor: date, months: int) -> date:
    """Calendar arithmetic for the trailing window; day clamps to the
    target month's length so every anchor date has a defined floor."""
    import calendar
    month = anchor.month - months
    year = anchor.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _fy_label(value: date) -> str:
    """Federal fiscal-year label, two-digit ('FY19'): FY starts 01 Oct."""
    fiscal = value.year + (1 if value.month >= 10 else 0)
    return f"FY{fiscal % 100:02d}"


def r2_displacement(pack: Any, store_rows: Optional[Iterable[Any]] = None,
                    *, today: Optional[date] = None,
                    kind_by_name: Optional[dict[str, str]] = None) -> dict:
    """Sole-source intents and awards naming a rival inside an agency where
    the client holds any award history (current or past).

    Matching imports the shared sam_lanes guards: an ambiguous single-word
    rival name still needs the vendor present, exactly as the brand lanes
    require, and every guard rejection is counted in the receipt. The
    empirical basis for this rule is one observed case; it ships without
    confidence language and asserts only what each verbatim sentence shows.

    D3 (defect round, 2026-08-03): an alert is dated intelligence. A match
    is admitted only when its source holds a period end on/after the press
    date OR shows action inside the trailing R2_WINDOW months. Older matches
    are counted in the receipt with the oldest fiscal year named; a matched
    source with no usable date at all is receipted separately. Neither
    renders as an alert.
    """
    today = _decision_today(pack, today)
    window_floor = _months_back(today, R2_WINDOW["trailing_months"])
    kind_by_name = kind_by_name or _pack_kind_by_name(pack)
    entities = (getattr(pack, "research", None) or {}).get("entities") or {}
    vendor = str(getattr(pack, "client_name", "") or "")
    matchers = {name: m for name, m in
                build_matchers(entities, vendor).items()
                if m["kind"] == "competitor"}
    all_matchers = build_matchers(entities, vendor)
    vendor_pattern = _pattern(aliases(vendor)) if vendor else None

    corridor: dict[str, list] = {}
    for record in getattr(pack, "records", []) or []:
        if record.lane != "L2_entity_award":
            continue
        if _record_side(record, pack, kind_by_name) != "core":
            continue
        agency = " ".join(str(record.agency or "").split())
        if agency:
            corridor.setdefault(agency, []).append(record)

    receipt = {"guard_rejections": {}, "store_notices_scanned": 0,
               "store_unavailable": store_rows is None,
               "corridor_agencies": sorted(corridor),
               "window": dict(R2_WINDOW),
               "window_floor": window_floor.isoformat(),
               "window_excluded": 0,
               "window_excluded_oldest_fy": None,
               "window_undated": 0}
    alerts: list[dict] = []
    oldest_excluded: list[date] = []

    def _scan(text: str, *, notice_type: str, agency: str, source: dict,
              period_end: Any = None, action_dates: tuple = ()) -> None:
        context = _is_displacement_context(notice_type, text)
        if context is None:
            return
        corridor_key = " ".join(str(agency or "").split())
        history = corridor.get(corridor_key)
        if not history:
            return
        vendor_present = bool(vendor_pattern.search(text)) \
            if vendor_pattern else False
        product_hits = {n for n, m in all_matchers.items()
                        if m["kind"] in ("product", "competitor")
                        and m["pattern"].search(text)}
        for name, matcher in matchers.items():
            found = matcher["pattern"].search(text)
            if not found:
                continue
            why = reject_entity_hit(matcher, vendor_present=vendor_present,
                                    product_hits=product_hits, text=text,
                                    negative_phrases=_corpus_negatives())
            if why:
                receipt["guard_rejections"][why] = (
                    receipt["guard_rejections"].get(why, 0) + 1)
                continue
            # D3 window: applied to the surviving MATCH, after the shared
            # guards, so guard receipts stay honest and only real matches
            # can be excluded as older.
            end = _iso_date(period_end)
            actions = [d for d in (_iso_date(a) for a in action_dates) if d]
            current = end is not None and end >= today
            recent = any(a >= window_floor for a in actions)
            if not (current or recent):
                governing = end or (max(actions) if actions else None)
                if governing is None:
                    receipt["window_undated"] += 1
                else:
                    receipt["window_excluded"] += 1
                    oldest_excluded.append(governing)
                continue
            alerts.append({
                "competitor": name,
                "matched_text": found.group(0),
                "sentence": _matched_sentence(text, found.span()),
                "context": context,
                "agency": corridor_key,
                "source": source,
                "client_history_records": [
                    _evidence_row(r) for r in history],
            })

    for record in getattr(pack, "records", []) or []:
        text = _receipt_text(record.title, record.description)
        # An L2 record IS an award by lane; its notice_type is None because
        # awards are not notices. The lane satisfies the award context
        # directly; notices keep the type/text derivation.
        notice_type = ("Award Notice" if record.lane == "L2_entity_award"
                       else str(getattr(record, "notice_type", "") or ""))
        _scan(text,
              notice_type=notice_type,
              agency=record.agency or "",
              source={"kind": "pack_record", "record_id": record.record_id,
                      "lane": record.lane, "url": record.url,
                      "title": record.title},
              period_end=record.period_end,
              action_dates=(record.posted_date, record.period_start))

    for row in store_rows or ():
        get = row.__getitem__ if hasattr(row, "keys") else \
            (row.get if isinstance(row, dict) else None)
        if get is None:
            continue
        receipt["store_notices_scanned"] += 1
        title = str(get("title") or "")
        from agents.golden_press.sam_lanes import canonical_notice_url
        _scan(_receipt_text(title, get("description_prefix")),
              notice_type=str(get("notice_type") or ""),
              agency=str(get("agency") or ""),
              source={"kind": "store_notice",
                      "notice_id": str(get("notice_id") or ""),
                      "url": canonical_notice_url(
                          {"notice_id": get("notice_id"),
                           "url": get("url")}),
                      "title": title,
                      "notice_type": str(get("notice_type") or ""),
                      "posted": str(get("posted") or "")},
              period_end=None,
              action_dates=(get("posted"),))

    if oldest_excluded:
        receipt["window_excluded_oldest_fy"] = _fy_label(min(oldest_excluded))

    # One alert per (source, competitor): a sentence matched in both title
    # and description is one receipt, not two.
    seen: set = set()
    unique: list[dict] = []
    for alert in alerts:
        key = (alert["source"].get("record_id")
               or alert["source"].get("notice_id"), alert["competitor"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(alert)
    return {"alerts": unique, "receipt": receipt}


# --------------------------------------------------------------------------- #
# R3 · vehicle expiry context on qualifying clocks
# --------------------------------------------------------------------------- #
def r3_vehicle_context(record: Any) -> Optional[dict]:
    """The vehicle-expiry context for ONE record, or None.

    Fires only for a classified vehicle with a known expiry (today that is
    SEWP V, the one versioned constant). The line speaks to the vehicle's
    published dates and NEVER asserts any prime's SEWP VI status.
    """
    if getattr(record, "vehicle_class", None) != \
            SEWP_V_FACTS["vehicle_class"]:
        return None
    return {
        "record_id": record.record_id,
        "parent_award_id": getattr(record, "parent_award_id", None),
        "facts": dict(SEWP_V_FACTS),
    }


# --------------------------------------------------------------------------- #
# R4 · forecast adjacency, qualify only
# --------------------------------------------------------------------------- #
def r4_adjacency(pack: Any, *,
                 kind_by_name: Optional[dict[str, str]] = None) -> dict:
    """L4 forecasts at a department where the client holds any award,
    current or historical. Labeled qualify, never pipeline; forecast values
    are never summed into any dollar figure here."""
    kind_by_name = kind_by_name or _pack_kind_by_name(pack)
    client_departments: dict[str, list] = {}
    client_subtiers: dict[str, list] = {}
    for record in getattr(pack, "records", []) or []:
        if record.lane != "L2_entity_award":
            continue
        if _record_side(record, pack, kind_by_name) != "core":
            continue
        subtier = " ".join(str(record.sub_agency or "").split())
        if subtier:
            client_subtiers.setdefault(subtier.casefold(), []).append(record)
        key = _department_key(record.agency) or _department_key(
            record.sub_agency)
        if key:
            client_departments.setdefault(key, []).append(record)

    rows = []
    unresolved = []
    for record in getattr(pack, "records", []) or []:
        if record.lane != "L4_forecast":
            continue
        key = _department_key(record.agency) or _department_key(
            record.sub_agency)
        if key is None:
            unresolved.append(record.record_id)
            continue
        history = client_departments.get(key)
        if not history:
            continue
        # G4 (2026-08-03): adjacency evaluates at R1's subtier grain first.
        # A forecast whose subtier matches client paper is full-grain; a
        # toptier-only join renders COLLAPSED, and its receipt names the
        # client subtiers that were matched against and why it collapsed.
        forecast_subtier = " ".join(
            str(record.sub_agency or "").split()).casefold()
        subtier_history = (client_subtiers.get(forecast_subtier)
                          if forecast_subtier else None)
        if subtier_history:
            grain = "subtier"
            history_rows = subtier_history
            collapse = None
        else:
            grain = "toptier_only"
            history_rows = history
            matched = sorted({
                " ".join(str(r.sub_agency or "").split())
                for r in history if r.sub_agency})
            collapse = {
                "reason": ("forecast names no subtier matching client "
                           "paper; joined at department grain only"),
                "client_subtiers_matched_against": matched,
                "forecast_subtier": (record.sub_agency or None),
            }
        rows.append({
            "label": "qualify",
            "grain": grain,
            "collapse": collapse,
            "forecast": _evidence_row(record) | {
                "title": record.title,
                "estimated_value_range": record.estimated_value_range,
                "fiscal_year": record.fiscal_year,
                "anticipated_solicitation": record.anticipated_solicitation,
            },
            "department": key,
            "client_history_records": [_evidence_row(r)
                                       for r in history_rows],
        })
    return {
        "rows": rows,
        "receipt": {
            "client_departments": sorted(client_departments),
            "forecast_agency_unresolved": unresolved,
        },
    }


# --------------------------------------------------------------------------- #
# the one assembly the press stores on the pack
# --------------------------------------------------------------------------- #
def build_decisions(pack: Any, store_rows: Optional[Iterable[Any]] = None,
                    *, today: Optional[date] = None) -> dict:
    """Annotate vehicles, run R1-R4, and return the decisions object the
    press stores on pack.decisions. Deterministic for a given (pack,
    store_rows, today); today defaults to the pack's own date."""
    annotate_vehicles(getattr(pack, "records", []) or [])
    kind_by_name = _pack_kind_by_name(pack)
    effective_today = _decision_today(pack, today)
    r1 = r1_decisions(pack, today=effective_today, kind_by_name=kind_by_name)
    r2 = r2_displacement(pack, store_rows, today=effective_today,
                         kind_by_name=kind_by_name)
    r4 = r4_adjacency(pack, kind_by_name=kind_by_name)

    # R3 context binds to R1/R2 clocks: every rule-cited award riding a
    # known-expiry vehicle gets its context row, keyed by record id.
    r3_rows: dict[str, dict] = {}
    cited_ids = {row["record_id"]
                 for card in r1["cards"]
                 for row in (*card["client_records"], *card["rival_records"])}
    cited_ids |= {row["record_id"]
                  for alert in r2["alerts"]
                  for row in alert["client_history_records"]}
    cited_ids |= {alert["source"].get("record_id")
                  for alert in r2["alerts"]
                  if alert["source"].get("kind") == "pack_record"}
    for record in getattr(pack, "records", []) or []:
        if record.record_id in cited_ids:
            context = r3_vehicle_context(record)
            if context is not None:
                r3_rows[record.record_id] = context

    return {
        "version": DECISION_RULES_VERSION,
        "today": effective_today.isoformat(),
        "r1": r1,
        "r2": r2,
        "r3": {"contexts": r3_rows,
               "facts_version": SEWP_V_FACTS["version"],
               "citation": SEWP_V_FACTS["citation"]},
        "r4": r4,
    }
