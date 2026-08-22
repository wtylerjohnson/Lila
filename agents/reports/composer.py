"""The auto-composer: deterministic selection from the relevance shortlist
and stored sweep into Signal Board content (V4.2, C1).

The composer produces content; the existing machine judges it. No gate,
lint, schema, or press-runner change belongs here.

SELECTION RULES (deterministic; the composer's whole judgment)

BEST-FIT (default 3): candidates are first the stored sweep's actionable
sam.gov solicitation family minus triage discards. Open solicitations and
combined notices rank before presolicitations/sources sought, explicit RFI or
draft-RFP special notices, and unknown legacy types. Award notices,
justifications, and generic special notices never enter this live band. Each
candidate is scored by tools.relevance.engine.score_record with the client
taxonomy and engagement scope; only verdict.relevant rows survive. When that
pool is empty, active USAspending award corridors may fill the band, but only
when the sanctioned evidence scorer finds real CORE text, the award is in
scope, its PoP end is current/future, and both PIID and generated identity are
present. Award cards say ACTIVE AWARD CORRIDOR and PERIOD OF PERFORMANCE END;
they never masquerade as solicitations.

COMPETITOR LANES (default 5): direct-award vendors in the client's
corridors, from the sweep's incumbent evidence. Pool: every award-kind
row under results.incumbent_buyer_map.buyers[].records with a
capability match (matched_products or matched_terms nonempty) AND an
end_date on or after the compose date (missing dates do not prove a
forward-looking corridor), excluding
the client itself, one
lane per (recipient, buyer) pair keyed to its winning record: amount
within evidence tier, then end_date desc, then the record's canonical JSON
asc, so equal-amount records cannot trade places under input permutation.
Named-product rows are the direct tier and are the only pool whenever any
exist; broad adjacent-term rows are a fallback, never allowed to inflate or
outrank the product view. Order across a tier: record amount desc, then
recipient asc, then buyer asc.
Other stored awards for a selected recipient surface as footprint context
only when their explicit NAICS lies inside the supplied client boundary;
they never create or rank a competitor lane.

TEAMING (default 4): primes on the SELECTED best-fit plays. For each
selected notice, candidate primes are results.subawards.primes whose
edges (results.subawards.edges[naics] rows) share the notice's
naics_code and pass the shared CORE, agency, scope, and exact-record gates.
Order: matched CORE strength, selected-play strength, cited subaward amount,
then stable source identity. A company already shown as a competitor never
reappears as a teaming recommendation. When no distinct publication-grade
partner survives, Section 03 becomes FEDERAL ACQUISITION PATHWAYS: a
source-bound projection of how the cited accounts bought, what access facts
the record states, and what must be verified next. It never invents a
partner merely to fill the layout.

HORIZON (CLI default 12): events inside an exact 36-calendar-month window
from the compose date (month-end clamped). Pool, in precedence order:
recompete calendar ATTACK and DEFEND rows (pop_end in window),
forecast_signals.matched rows (source-stated solicitation or award date,
fiscal quarter, or fiscal year), contract-award and USAspending expiry rows,
and active buyer-map award PoP ends (PIID + generated identity required).
Every row must independently clear
the same sanctioned CORE relevance and engagement-scope bar as best fit;
a NAICS match alone never enters the client horizon. Order: event date asc,
then pool precedence, then ident asc, then the row's canonical JSON.
Current, exact-URL PROGRAM records from sanctioned official adapters may
occupy one configured slot when acquisition events already fill the band, or
at most two otherwise. They remain program-shaping signals, never live
solicitations, and carry their stated program/funding timing verbatim.

Ties beyond the stated keys cannot survive: every order ends on the
row's canonical JSON, which is unique.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from tools.api.sam_notice_family import notice_family_rank
from tools.capability import client_display_name, load_profile
from tools.relevance.engine import (
    RelevanceVerdict, record_text_fields, score_opportunity_evidence,
    score_record, score_text,
)
from tools.relevance.scope import load_engagement_scope
from tools.relevance.taxonomy import derived_taxonomy, load_taxonomy

HORIZON_MONTHS = 36
DEFAULT_FORWARD_EVENTS = 12
MATERIAL_AWARD_FLOOR = 50_000.0


class ComposerSelectionError(RuntimeError):
    """A named selection failure with its repair surface."""

    def __init__(self, reason: str, fix_surface: str) -> None:
        super().__init__(reason)
        self.fix_surface = fix_surface


@dataclass
class BestFitPick:
    source_id: str
    title: str
    agency: str
    naics: str
    deadline: str
    verdict: RelevanceVerdict
    record: dict
    why: str = ""
    source_kind: str = "sam.gov"
    award_id: str = ""
    gid: str = ""
    buyer: str = ""
    recipient: str = ""
    amount: float = 0.0
    window_end: str = ""
    scope_ok: bool = False
    scope_basis: str = ""
    triage_verdict: str = ""
    triage_reason: str = ""
    procurement_facts: list = field(default_factory=list)
    missing_procurement_facts: list = field(default_factory=list)


@dataclass
class CompetitorPick:
    recipient: str
    buyer: str
    agency: str
    amount: float
    record: dict
    award_id: str = ""
    gid: str = ""
    lane_records: list = field(default_factory=list)
    why: str = ""
    scope_ok: bool = False
    scope_basis: str = ""
    direct_product: bool = False
    client_relevance_basis: dict = field(default_factory=dict)
    boundary_records: list = field(default_factory=list)
    procurement_facts: list = field(default_factory=list)
    missing_procurement_facts: list = field(default_factory=list)


@dataclass
class TeamingPick:
    partner: str
    basis: str            # "subaward-evidence" only in machine composition
    edge_count: int
    total: float
    play: dict            # the selected notice or lane this prime attaches to
    agency: str = ""
    buyer: str = ""
    edges: list = field(default_factory=list)
    why: str = ""
    scope_ok: bool = False
    scope_basis: str = ""
    client_relevance_basis: dict = field(default_factory=dict)
    procurement_facts: list = field(default_factory=list)
    missing_procurement_facts: list = field(default_factory=list)


@dataclass
class AcquisitionPathPick:
    """Evidence-bound acquisition research, never a partner assertion."""

    agency: str
    buyer: str
    capability: str
    record: dict
    source_kind: str
    source_identity: str
    award_id: str = ""
    gid: str = ""
    notice_guid: str = ""
    why: str = ""
    scope_ok: bool = False
    scope_basis: str = ""
    client_footprint: bool = False
    client_relevance_basis: dict = field(default_factory=dict)
    procurement_facts: list = field(default_factory=list)
    missing_procurement_facts: list = field(default_factory=list)
    verdict: Optional[RelevanceVerdict] = None


@dataclass
class HorizonPick:
    kind: str             # recompete | forecast | expiring | award-window
    event_date: str
    ident: str
    row: dict
    why: str = ""
    agency: str = ""
    scope_ok: bool = False
    scope_basis: str = ""
    award_id: str = ""
    gid: str = ""
    quote: str = ""
    client_relevance_basis: dict = field(default_factory=dict)
    posture: str = "QUALIFY"
    timing_source_value: str = ""
    timing_precision: str = "exact-date"
    timing_sort_date: str = ""
    source_citation: str = ""
    procurement_facts: list = field(default_factory=list)
    missing_procurement_facts: list = field(default_factory=list)


def _num(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.]", "", value)
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0
    return 0.0


def _event_day(value) -> str:
    """ISO or APFS MM/DD/YYYY date to YYYY-MM-DD; '' when unusable."""
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()
    iso = _iso_day(text)
    if iso:
        return iso
    try:
        return datetime.strptime(text[:10], "%m/%d/%Y").date().isoformat()
    except ValueError:
        return ""


_FORECAST_QUARTER_RE = re.compile(
    r"^(?:FY\s*)?(?:Q([1-4])\s*(?:FY\s*)?(\d{4})|"
    r"(\d{4})\s*Q([1-4]))$",
    re.I,
)
_FORECAST_YEAR_RE = re.compile(r"^(?:FY\s*)?(\d{4})$", re.I)


def _forecast_timing(
    value,
    *,
    floor: date,
    ceiling: date,
) -> Optional[tuple[str, str, str]]:
    """Sortable timing plus source-stated display, without inventing a day.

    Exact dates sort on that date. Government forecast quarter and FY values
    are tested as periods against the 36-month window, but the public value
    remains the source's quarter or year. The internal first day is only a
    stable ordering key and never renders as an asserted event date.
    """

    stated = " ".join(str(value or "").strip().split())
    if not stated:
        return None
    exact = _event_day(stated)
    if exact:
        day = date.fromisoformat(exact)
        if floor <= day <= ceiling:
            return exact, stated, "exact-date"
        return None
    match = _FORECAST_QUARTER_RE.fullmatch(stated)
    if match:
        quarter = int(match.group(1) or match.group(4))
        fiscal_year = int(match.group(2) or match.group(3))
        starts = {
            1: date(fiscal_year - 1, 10, 1),
            2: date(fiscal_year, 1, 1),
            3: date(fiscal_year, 4, 1),
            4: date(fiscal_year, 7, 1),
        }
        ends = {
            1: date(fiscal_year - 1, 12, 31),
            2: date(fiscal_year, 3, 31),
            3: date(fiscal_year, 6, 30),
            4: date(fiscal_year, 9, 30),
        }
        start, end = starts[quarter], ends[quarter]
        if end >= floor and start <= ceiling:
            return max(start, floor).isoformat(), stated.upper(), "fiscal-quarter"
        return None
    match = _FORECAST_YEAR_RE.fullmatch(stated)
    if match:
        fiscal_year = int(match.group(1))
        start = date(fiscal_year - 1, 10, 1)
        end = date(fiscal_year, 9, 30)
        if end >= floor and start <= ceiling:
            return max(start, floor).isoformat(), stated.upper(), "fiscal-year"
    return None


def add_months(day: date, months: int) -> date:
    """Exact calendar-month arithmetic, day clamped to the target month's
    end (Jan 31 + 1mo = Feb 28/29; leap days survive to leap years)."""
    import calendar
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _canonical(row: dict) -> str:
    import json as _json
    return _json.dumps(row, sort_keys=True, separators=(",", ":"),
                       default=str)


def _source_code(value) -> str:
    """Code-only projection for scalar or USAspending object values."""
    if isinstance(value, dict):
        value = value.get("code")
    return " ".join(str(value or "").strip().split())


def _first_source_code(*values) -> str:
    for value in values:
        code = _source_code(value)
        if code:
            return code
    return ""


def _aware_iso(stamp: str) -> str:
    """Normalize a sweep timestamp to timezone-aware UTC ISO. Bare dates
    become midnight UTC; naive datetimes are stamped UTC. The press's
    freshness gate subtracts these from an aware render moment."""
    from datetime import timezone
    text = stamp.strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _iso_day(value) -> str:
    """Best-effort YYYY-MM-DD from sweep date strings; '' when unusable."""
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        return ""


def load_client_inputs(client_name: str) -> dict:
    """Taxonomy (real else derived) + engagement scope, named failures."""
    taxonomy = load_taxonomy(client_name) or derived_taxonomy(client_name)
    if taxonomy is None:
        raise RuntimeError(
            f"no capability taxonomy or profile for {client_name}; "
            "onboard the client before composing")
    scope = load_engagement_scope(client_name)
    return {"taxonomy": taxonomy, "scope": scope}


def select_best_fit(sweep: dict, taxonomy, scope, *, n: int = 3) -> list[BestFitPick]:
    results = sweep.get("results") or {}
    triage = results.get("triage") or {}
    picks: list[BestFitPick] = []
    for record in results.get("sam.gov") or []:
        sid = str(record.get("source_id") or "")
        if not sid:
            continue
        call = triage.get(sid) or {}
        raw = record.get("raw_payload") or {}
        attachment_fingerprint = str(
            raw.get("attachment_evidence_sha256") or "")
        reviewed_fingerprint = str(
            call.get("attachment_evidence_sha256") or "")
        if attachment_fingerprint \
                and reviewed_fingerprint != attachment_fingerprint:
            # Refresh intentionally preserves the prior metadata-only triage
            # ledger. Official requirement-file text is new evidence that
            # screen never saw, so only a verdict bound to this exact public
            # attachment inventory remains effective.
            call = {}
        if call.get("verdict") == "discard":
            continue
        family_rank = notice_family_rank(record)
        if family_rank is None:
            continue
        verdict = score_record(record, taxonomy, engagement_scope=scope)
        if not verdict.relevant:
            continue
        if scope is not None and "unresolved" in verdict.scope_basis:
            # machine bar: an unresolvable agency is not scope proof
            continue
        picks.append(BestFitPick(
            source_id=sid,
            title=str(record.get("title") or ""),
            agency=str(record.get("agency") or ""),
            naics=str(record.get("naics_code") or ""),
            deadline=_iso_day(record.get("response_deadline")),
            verdict=verdict,
            record=record,
            triage_verdict=str(call.get("verdict") or ""),
            triage_reason=str(call.get("reason") or ""),
        ))
    picks.sort(key=lambda p: (
        notice_family_rank(p.record),
        -p.verdict.score,
        -len(p.verdict.core_terms),
        p.deadline or "9999-12-31",
        p.source_id,
    ))
    chosen = picks[:n]
    for p in chosen:
        p.why = (f"score {p.verdict.score} · core {', '.join(p.verdict.core_terms)}"
                 f" · {len(p.verdict.spans)} evidence spans"
                 + (f" · due {p.deadline}" if p.deadline else ""))
    return chosen


def _scope_verdict(record: dict, scope) -> tuple[bool, str]:
    """The scope law over one FULL source record, at the MACHINE bar:
    (inside, basis). The whole record is judged so department resolution
    can use every field the catalog knows (agency strings, URLs,
    generated-id toptiers), never a display string alone. A resolved
    out-of-scope department is excluded, never included. With no
    engagement scope, UNSCOPED passes; when a scope EXISTS, a record
    whose department cannot resolve is not machine evidence (the generic
    engine's ignorance-never-excludes doctrine does not lower the
    machine-screened bar)."""
    from tools.relevance.scope import in_scope
    inside, basis = in_scope(record, scope)
    if scope is not None and "unresolved" in basis:
        return False, basis
    return inside, basis


_COMPANY_SUFFIXES = {
    "co", "company", "corp", "corporation", "inc", "incorporated",
    "llc", "llp", "lp", "ltd", "limited", "pc", "pllc",
}


def _company_key(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
    for punctuated, collapsed in (
        (("l", "l", "c"), "llc"),
        (("l", "l", "p"), "llp"),
        (("p", "l", "l", "c"), "pllc"),
        (("l", "p"), "lp"),
        (("p", "c"), "pc"),
    ):
        if tuple(words[-len(punctuated):]) == punctuated:
            words[-len(punctuated):] = [collapsed]
            break
    words = [word for word in words if word not in _COMPANY_SUFFIXES]
    return " ".join(words)


def _is_client_company(recipient: str, client_name: str) -> bool:
    """Conservative legal-suffix-aware self-recipient match.

    The buyer map is an incumbent map, so the client's own active award is a
    useful protect/expand corridor, not a competitor. A longer legal name such
    as ``RIVERBED TECHNOLOGY LLC`` may begin with the exact client brand; a
    mid-string or fuzzy match never counts.
    """
    recipient_key = _company_key(recipient)
    client_key = _company_key(client_name)
    return bool(
        client_key
        and (recipient_key == client_key
             or recipient_key.startswith(client_key + " "))
    )


def _is_client_footprint(recipient: str, record: dict,
                         client_name: str, *, taxonomy=None) -> bool:
    """Exact client installed-base evidence, including reseller awards.

    Federal software awards commonly name the channel partner as recipient.
    An exact structured ``matched_products`` hit on the client brand is still
    the client's footprint; a substring, adjacent term, or free-text guess is
    not. Those rows may support protect/expand strategy but must never appear
    as competitors or speculative teaming partners.
    """
    if _is_client_company(recipient, client_name):
        return True
    client_key = _company_key(client_name)
    exact_product = bool(client_key and any(
        _company_key(str(product)) == client_key
        for product in (record.get("matched_products") or [])
    ))
    if not exact_product:
        return False
    # A channel-recipient product tag is retrieval metadata, not proof on its
    # own. C1 also requires the source award text to clear the curated CORE
    # matcher, including its collision kill rules. The procurement-code
    # universe is intentionally not applied to an exact installed-product
    # fact: agencies sometimes code a reseller order outside the client's
    # opportunity boundary, but that cannot turn the client's named product
    # into an incumbent takeout target. Exact legal self-recipient remains
    # objective footprint evidence even when the terse description omits the
    # client brand.
    if taxonomy is None:
        return True
    return any(
        any(span.tier == "core" for span in spans)
        for field, text in record_text_fields(record)
        for spans, _killed in [score_text(text, taxonomy, field=field)]
    )


def _award_evidence_record(row: dict) -> dict:
    """Expose stored buyer-map list evidence to the sanctioned text scorer.

    ``score_opportunity_evidence`` performs this projection for identified
    rows. This compact fallback exists only so a core-evidenced row missing an
    identity can fail NAMED instead of disappearing before the identity gate.
    """
    record = dict(row)
    evidence_terms = [
        str(item) for key in ("matched_products", "matched_terms")
        for item in (row.get(key) or []) if str(item).strip()
    ]
    if evidence_terms:
        record["title"] = " · ".join(evidence_terms)
    return record


def select_client_footprints(
    sweep: dict,
    taxonomy,
    scope,
    *,
    client_name: str,
    today: date,
) -> list[BestFitPick]:
    """All active, structured, in-scope client installed-base awards.

    This collection is independent of best-fit capacity: a live notice must
    not erase installed-base authority from the ticker, signal cards, or
    scale show-work. Exact legal self-recipient is objective. A reseller is
    footprint only when ``matched_products`` names the exact client brand AND
    the source award text clears the sanctioned taxonomy. GIDs de-duplicate.
    """
    floor = today.isoformat()
    buyer_map = (sweep.get("results") or {}).get("incumbent_buyer_map") or {}
    picks: dict[str, BestFitPick] = {}
    for buyer in buyer_map.get("buyers") or []:
        buyer_name = str(buyer.get("buyer") or "")
        buyer_agency = str(buyer.get("agency") or "")
        for row in buyer.get("records") or []:
            if row.get("kind") != "award":
                continue
            end_day = _iso_day(row.get("end_date"))
            if not end_day or end_day < floor:
                continue
            recipient = str(row.get("recipient") or "")
            if not recipient or not _is_client_footprint(
                    recipient, row, client_name, taxonomy=taxonomy):
                continue
            award_id = str(row.get("award_id") or "")
            gid = str(row.get("generated_internal_id") or "")
            if not award_id or not gid:
                continue
            own_record = dict(row)
            own_record.setdefault("generated_id", gid)
            inside, scope_basis = _scope_verdict(own_record, scope)
            if scope is not None and "unresolved" in scope_basis:
                own_record.setdefault("agency", buyer_agency)
                inside, scope_basis = _scope_verdict(own_record, scope)
            if not inside or (scope is not None
                              and "unresolved" in scope_basis):
                continue
            verdict = score_opportunity_evidence(sweep, [gid], taxonomy)
            verdict = verdict.model_copy(update={
                "scope_basis": scope_basis,
                "off_scope": False,
                "off_scope_signal": False,
            })
            pick = BestFitPick(
                source_id=gid,
                title=f"{buyer_name} × {recipient}",
                agency=buyer_agency,
                naics=_source_code(
                    row.get("naics_code") or row.get("naics")),
                deadline="",
                verdict=verdict,
                record=own_record,
                source_kind="usaspending-award",
                award_id=award_id,
                gid=gid,
                buyer=buyer_name,
                recipient=recipient,
                amount=_num(row.get("amount")),
                window_end=end_day,
                scope_ok=True,
                scope_basis=scope_basis,
                why=(f"active client footprint · PoP end {end_day} · "
                     f"award {award_id}"),
            )
            prior = picks.get(gid)
            rank = (_canonical(pick.record), pick.buyer, pick.agency)
            prior_rank = ((_canonical(prior.record), prior.buyer, prior.agency)
                          if prior is not None else None)
            if prior_rank is None or rank < prior_rank:
                picks[gid] = pick
    return sorted(picks.values(), key=lambda pick: (
        pick.window_end, pick.gid, _canonical(pick.record)))


def select_award_best_fit(
    sweep: dict,
    taxonomy,
    scope,
    *,
    client_name: str,
    today: date,
    n: int = 3,
) -> list[BestFitPick]:
    """Core-evidenced active award corridors when SAM yields no play.

    This is deliberately not a generic award ranking. Every selected row has
    a current/future PoP end, a positive scope verdict, exact structured award
    identity, and a relevant verdict from the same evidence scorer the Signal
    Board press uses. It therefore supports installed-base renewal/expansion
    and evidenced displacement corridors without calling either a live bid.
    """
    results = sweep.get("results") or {}
    buyer_map = results.get("incumbent_buyer_map") or {}
    floor = today.isoformat()
    picks: dict[str, BestFitPick] = {}
    for buyer in buyer_map.get("buyers") or []:
        buyer_name = str(buyer.get("buyer") or "")
        buyer_agency = str(buyer.get("agency") or "")
        for row in buyer.get("records") or []:
            if row.get("kind") != "award":
                continue
            end_day = _iso_day(row.get("end_date"))
            if not end_day or end_day < floor:
                continue
            recipient = str(row.get("recipient") or "")
            if not recipient:
                continue
            award_id = str(row.get("award_id") or "")
            gid = str(row.get("generated_internal_id") or "")

            own_record = dict(row)
            if gid:
                own_record.setdefault("generated_id", gid)
            inside, scope_basis = _scope_verdict(own_record, scope)
            if scope is not None and "unresolved" in scope_basis:
                contextual = dict(own_record)
                contextual.setdefault("agency", buyer_agency)
                inside, scope_basis = _scope_verdict(contextual, scope)
                own_record = contextual
            if not inside or (scope is not None
                              and "unresolved" in scope_basis):
                continue

            verdict = (
                score_opportunity_evidence(sweep, [gid], taxonomy)
                if gid else
                score_record(_award_evidence_record(row), taxonomy)
            )
            verdict = verdict.model_copy(update={
                "scope_basis": scope_basis,
                "off_scope": False,
                "off_scope_signal": False,
            })
            if not verdict.relevant:
                continue
            if not award_id or not gid:
                raise ComposerSelectionError(
                    f"core-evidenced active award corridor {recipient} · "
                    f"{buyer_name} lacks structured award identity "
                    "(award_id and generated_internal_id)",
                    "re-run the sweep: the buyer map now preserves Award ID "
                    "and generated_internal_id on award records",
                )
            pick = BestFitPick(
                source_id=gid,
                title=f"{buyer_name} × {recipient}",
                agency=buyer_agency,
                naics=_source_code(
                    row.get("naics_code") or row.get("naics")),
                deadline="",
                verdict=verdict,
                record=own_record,
                source_kind="usaspending-award",
                award_id=award_id,
                gid=gid,
                buyer=buyer_name,
                recipient=recipient,
                amount=_num(row.get("amount")),
                window_end=end_day,
                scope_ok=True,
                scope_basis=scope_basis,
            )
            prior = picks.get(gid)
            if prior is None or _canonical(pick.record) < _canonical(prior.record):
                picks[gid] = pick
    ranked = sorted(picks.values(), key=lambda p: (
        -p.verdict.score,
        -len(p.verdict.core_terms),
        p.window_end,
        -p.amount,
        p.gid,
        _canonical(p.record),
    ))
    # A token subscription should not displace a materially larger,
    # equally publishable corridor merely because its PoP end is earlier.
    # Stable-partition material routes ahead of sub-floor rows. Sparse
    # clients still retain cited routes, but increasing `n` can never let a
    # token row jump ahead of a material route.
    material = [
        pick for pick in ranked if pick.amount >= MATERIAL_AWARD_FLOOR]
    below_floor = [
        pick for pick in ranked if pick.amount < MATERIAL_AWARD_FLOOR]
    chosen = (material + below_floor)[:n]
    for pick in chosen:
        kind = ("client installed-base" if _is_client_footprint(
            pick.recipient, pick.record, client_name, taxonomy=taxonomy)
            else "displacement")
        pick.why = (
            f"active {kind} award corridor · score {pick.verdict.score} · "
            f"core {', '.join(pick.verdict.core_terms)} · "
            f"{len(pick.verdict.spans)} evidence spans · PoP end "
            f"{pick.window_end} · award {pick.award_id}"
        )
    return chosen


def select_competitors(sweep: dict, *, n: int = 5,
                       today: Optional[date] = None,
                       scope=None,
                       client_name: str = "",
                       taxonomy=None,
                       naics_boundary: Optional[list[str]] = None,
                       ) -> list[CompetitorPick]:
    results = sweep.get("results") or {}
    buyer_map = results.get("incumbent_buyer_map") or {}
    floor = today.isoformat() if today else ""
    lanes: dict[tuple, CompetitorPick] = {}
    code_universe = getattr(taxonomy, "code_universe", None)
    boundary_naics = {
        str(value).strip()
        for value in (
            naics_boundary
            if naics_boundary is not None
            else (getattr(code_universe, "naics", None) or []))
        if str(value).strip()
    }
    boundary_by_recipient: dict[str, list[dict]] = {}
    for buyer in buyer_map.get("buyers") or []:
        buyer_name = str(buyer.get("buyer") or "")
        agency = str(buyer.get("agency") or "")
        for row in buyer.get("records") or []:
            if row.get("kind") != "award":
                continue
            recipient = str(row.get("recipient") or "")
            if not recipient:
                continue
            # the scope law judges EVERY candidate award on its own
            # complete record first: explicit agency text, generated-id
            # toptiers, and URL evidence can each exclude a child record
            # under an otherwise in-scope buyer. Buyer fields supply
            # ONLY missing context, and only when the record alone is
            # silent; the buyer's verdict is never stamped on children.
            own_record = dict(row)
            gid_value = str(row.get("generated_internal_id") or "")
            if gid_value and not own_record.get("generated_id"):
                own_record["generated_id"] = gid_value
            inside, scope_basis = _scope_verdict(own_record, scope)
            if scope is not None and "unresolved" in scope_basis:
                context = dict(own_record)
                context.setdefault("agency", agency)
                inside, scope_basis = _scope_verdict(context, scope)
            if not inside:
                continue
            # The footprint context may use every stored award with an
            # explicit code inside the client's boundary. Selection of the
            # lead competitor lane remains capability-evidence gated below;
            # an untagged award may expand a selected incumbent's evidenced
            # federal footprint but can never create that incumbent card.
            row_naics = _source_code(
                row.get("naics") or row.get("naics_code"))
            if boundary_naics and row_naics in boundary_naics:
                footprint_row = dict(row)
                footprint_row.setdefault("buyer", buyer_name)
                footprint_row.setdefault("agency", agency)
                boundary_by_recipient.setdefault(
                    _company_key(recipient), []).append(footprint_row)
            if not (row.get("matched_products") or row.get("matched_terms")):
                continue
            # The upstream product tag is candidate retrieval, not evidence.
            # When C1 supplies its taxonomy, the source award text itself must
            # earn CORE relevance (and survives the taxonomy's kill rules).
            # This blocks namesake collisions such as ecological SteelHead
            # work while retaining actual Riverbed/SteelHead maintenance.
            if taxonomy is not None and not score_record(
                    row, taxonomy).relevant:
                continue
            end_day = _iso_day(row.get("end_date"))
            if not end_day or (floor and end_day < floor):
                continue
            if client_name and _is_client_footprint(
                    recipient, row, client_name, taxonomy=taxonomy):
                continue
            key = (recipient, buyer_name)
            amount = _num(row.get("amount"))
            end_day = _iso_day(row.get("end_date"))
            canonical = _canonical(row)
            direct_product = bool(row.get("matched_products"))
            prior = lanes.get(key)
            prior_rank = prior.record.get("_rank") if prior else None
            rank = (int(direct_product), amount, end_day)
            # evidence tier first, then amount desc, end_date desc, canonical
            # JSON ASCENDING - equal records resolve to the smaller JSON
            better = (
                prior_rank is None
                or rank > prior_rank[:3]
                or (rank == prior_rank[:3] and canonical < prior_rank[3]))
            if better:
                lane_records = prior.lane_records if prior else []
                winner = dict(row)
                winner["_rank"] = (*rank, canonical)
                lanes[key] = CompetitorPick(
                    recipient=recipient, buyer=buyer_name, agency=agency,
                    amount=amount, record=winner,
                    award_id=str(row.get("award_id") or ""),
                    gid=str(row.get("generated_internal_id") or ""),
                    lane_records=lane_records,
                    scope_ok=True, scope_basis=scope_basis,
                    direct_product=direct_product)
            lanes[key].lane_records.append(row)
    direct = [lane for lane in lanes.values() if lane.direct_product]
    pool = direct if direct else list(lanes.values())
    ranked = sorted(pool, key=lambda c: (
        not c.direct_product, -c.amount, c.recipient, c.buyer))
    material = [
        lane for lane in ranked if lane.amount >= MATERIAL_AWARD_FLOOR]
    # Competitor cards are a priority band, not a complete ledger.  When at
    # least one material route exists, sub-$50K records stay in the evidence
    # store rather than receiving equal visual weight.  A genuinely sparse
    # client with no material route still retains its best cited lane.
    chosen = (material if material else ranked)[:n]
    for c in chosen:
        if not (c.award_id and c.gid):
            raise ComposerSelectionError(
                f"selected USAspending lane {c.recipient} · {c.buyer} lacks "
                "structured award identity (award_id and "
                "generated_internal_id); identity is never reconstructed "
                "from a URL",
                "re-run the sweep: the buyer map now preserves Award ID "
                "and generated_internal_id on award records")
        matched = (c.record.get("matched_products") or
                   c.record.get("matched_terms") or [])
        tier = ("named-product" if c.direct_product
                else "adjacent capability")
        seen_boundary: set[str] = set()
        c.boundary_records = []
        for row in sorted(
                boundary_by_recipient.get(_company_key(c.recipient), []),
                key=_canonical):
            identity = str(
                row.get("generated_internal_id")
                or row.get("award_id")
                or _canonical(row)
            )
            if identity in seen_boundary:
                continue
            seen_boundary.add(identity)
            c.boundary_records.append(row)
        c.why = (f"largest {tier} award ${c.amount:,.0f} at "
                 f"{c.buyer} · matched {', '.join(map(str, matched[:3]))}"
                 f" · {len(c.lane_records)} lane records"
                 f" · {len(c.boundary_records)} cited boundary awards"
                 f" · award {c.award_id}")
    return chosen


def _prime_award_gid_index(results: dict) -> dict[str, tuple[str, dict]]:
    """Unambiguous PIID -> (canonical GID, stored award record)."""
    candidates: dict[str, dict[str, dict]] = {}
    buyer_map = results.get("incumbent_buyer_map") or {}
    for buyer in buyer_map.get("buyers") or []:
        for row in buyer.get("records") or []:
            if row.get("kind") != "award":
                continue
            piid = str(row.get("award_id") or "").strip()
            gid = str(row.get("generated_internal_id") or "").strip()
            if piid and gid:
                candidates.setdefault(piid, {}).setdefault(gid, row)
    return {
        piid: next(iter(by_gid.items()))
        for piid, by_gid in candidates.items() if len(by_gid) == 1
    }


def _award_record_gid_index(results: dict) -> dict[str, dict]:
    """Canonical GID -> one deterministic stored buyer-map award record."""
    candidates: dict[str, list[dict]] = {}
    buyer_map = results.get("incumbent_buyer_map") or {}
    for buyer in buyer_map.get("buyers") or []:
        for row in buyer.get("records") or []:
            if row.get("kind") != "award":
                continue
            gid = str(row.get("generated_internal_id") or "").strip()
            if gid:
                candidates.setdefault(gid, []).append(row)
    return {
        gid: sorted(rows, key=_canonical)[0]
        for gid, rows in candidates.items()
        if len({str(row.get("award_id") or "") for row in rows}) == 1
    }


def _notice_component(record: dict) -> str:
    """Best stored component identity for the shared Assess predicate."""
    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    for value in (
        record.get("component"), record.get("subagency"),
        raw.get("component"), raw.get("subtier"), raw.get("subtier_name"),
        raw.get("subtierAgencyName"), raw.get("organization_name"),
    ):
        if str(value or "").strip():
            return str(value).strip()
    hierarchy = (raw.get("organizationHierarchy")
                 or raw.get("organization_hierarchy") or [])
    if isinstance(hierarchy, list):
        names = []
        for node in hierarchy:
            if isinstance(node, dict):
                value = (node.get("name") or node.get("organization_name")
                         or node.get("title"))
            else:
                value = node
            if str(value or "").strip():
                names.append(str(value).strip())
        if names:
            return names[-1]
    return ""


def select_teaming(sweep: dict, best_fit: list[BestFitPick],
                   competitors: list[CompetitorPick], *,
                   n: int = 4, scope=None, profile=None,
                   diagnostics: Optional[list[str]] = None,
                   ) -> list[TeamingPick]:
    results = sweep.get("results") or {}
    subs = results.get("subawards") or {}
    edges_by_naics = subs.get("edges") or {}
    prime_totals = {str(p.get("name") or ""): _num(p.get("total"))
                    for p in subs.get("primes") or []}
    if profile is None:
        profile = load_profile(str(sweep.get("client") or ""))
    prime_gids = _prime_award_gid_index(results)
    award_records_by_gid = _award_record_gid_index(results)
    evidence: dict[tuple[str, str], TeamingPick] = {}
    diagnostic_counts: dict[str, int] = {}
    diagnostic_examples: dict[str, set[tuple[str, str]]] = {}

    def note(play_id: str, prime: str, reason: str) -> None:
        if diagnostics is not None:
            diagnostic_counts[reason] = diagnostic_counts.get(reason, 0) + 1
            diagnostic_examples.setdefault(reason, set()).add(
                (play_id, prime or "?"))

    from agents.assess.ledger import (
        partner_opportunity_core_terms, qualify_partner_subaward_edge,
    )
    from agents.reports.links import build_usaspending_award_link

    # Bind every subaward candidate to ONE selected play.  A prime's edges
    # across different notices are never aggregated into a stronger-looking
    # public route.
    for play in best_fit:
        naics = play.naics
        if not naics:
            continue
        opportunity_terms = (
            partner_opportunity_core_terms(profile, play.record)
            if profile is not None else frozenset()
        )
        for edge in edges_by_naics.get(naics) or []:
            prime = str(edge.get("prime") or "")
            if not prime:
                continue
            common, reason = qualify_partner_subaward_edge(
                edge, profile=profile,
                opportunity_terms=opportunity_terms,
                opportunity_agency=play.agency,
                opportunity_component=_notice_component(play.record),
            ) if profile is not None else (
                frozenset(), "client capability profile is unavailable")
            if not common:
                note(play.source_id, prime, reason)
                continue
            prime_award_id = str(edge.get("prime_award_id") or "").strip()
            if not prime_award_id:
                note(play.source_id, prime,
                     "prime award identity is absent")
                continue
            explicit_gid = str(
                edge.get("prime_award_generated_id")
                or edge.get("prime_generated_internal_id") or ""
            ).strip()
            indexed = prime_gids.get(prime_award_id)
            if explicit_gid:
                prime_gid = explicit_gid
                prime_record = award_records_by_gid.get(explicit_gid)
                if (prime_record is not None
                        and str(prime_record.get("award_id") or "")
                        != prime_award_id):
                    prime_record = None
            else:
                prime_gid, prime_record = indexed or ("", None)
            try:
                build_usaspending_award_link(prime_gid)
            except ValueError:
                note(play.source_id, prime,
                     "prime PIID has no unambiguous canonical award GID")
                continue
            if prime_record is None:
                note(play.source_id, prime,
                     "canonical prime award has no unique stored record for "
                     "figure and freshness reconciliation")
                continue
            # the scope law is judged PER EDGE: a resolved off-scope
            # route never becomes teaming evidence, never rides another
            # edge's verdict, and never inflates the edge count
            inside, _basis = _scope_verdict(edge, scope)
            if not inside:
                note(play.source_id, prime,
                     "subaward route is outside the engagement scope")
                continue
            cited_edge = dict(edge)
            cited_edge["_matched_naics"] = naics
            cited_edge["_prime_award_generated_id"] = prime_gid
            cited_edge["_prime_award_record"] = dict(prime_record)
            cited_edge["_shared_core_terms"] = sorted(common)
            cited_edge["_selected_play_identity"] = notice_guid(play.record)
            cited_edge["_selected_play_label"] = notice_identity_label(
                play.record)
            cited_edge["_selected_play_score"] = play.verdict.score
            key = (play.source_id, prime)
            pick = evidence.get(key)
            if pick is None:
                pick = TeamingPick(
                    partner=prime, basis="subaward-evidence", edge_count=0,
                    total=prime_totals.get(prime, 0.0),
                    play=play.record, agency=play.agency)
                evidence[key] = pick
            pick.edge_count += 1
            pick.edges.append(cited_edge)
    for pick in evidence.values():
        pick.edges.sort(key=lambda e: (
            -len(e.get("_shared_core_terms") or []),
            -_num(e.get("amount")),
            str(e.get("subaward_id") or ""),
            str(e.get("prime_award_id") or ""),
            _canonical(e),
        ))
        # One card cites one exact subaward record. Multiple edges on the
        # same play do not inflate the route or create a multi-record claim.
        pick.edges = [pick.edges[0]]
        pick.edge_count = 1
        # every surviving edge passed the scope law; the pick's recorded
        # basis is its leading route's verdict
        _inside, basis = _scope_verdict(pick.edges[0], scope)
        pick.scope_ok, pick.scope_basis = True, basis
    def route_rank(pick: TeamingPick) -> tuple:
        edge = pick.edges[0]
        return (
            -len(edge.get("_shared_core_terms") or []),
            -int(edge.get("_selected_play_score") or 0),
            -_num(edge.get("amount")),
            str(edge.get("_selected_play_identity") or ""),
            str(edge.get("subaward_id") or ""),
            _canonical(edge),
        )

    # The same prime/subaward may match multiple selected notices. Publish
    # exactly one stable best-play rationale per partner; otherwise repeated
    # cards would share a source identity and imply independent evidence.
    displayed_company_keys = {
        _company_key(lane.recipient)
        for lane in competitors if _company_key(lane.recipient)
    }
    displayed_company_keys.update(
        _company_key(play.recipient)
        for play in best_fit if _company_key(play.recipient)
    )
    best_by_partner: dict[str, TeamingPick] = {}
    for pick in sorted(evidence.values(), key=route_rank):
        partner_key = _company_key(pick.partner)
        if not partner_key or partner_key in displayed_company_keys:
            note(
                str(pick.play.get("source_id") or "?"),
                pick.partner,
                "partner is already published in an upper route band",
            )
            continue
        best_by_partner.setdefault(partner_key, pick)
    ranked = sorted(
        best_by_partner.values(),
        key=lambda pick: (*route_rank(pick), pick.partner),
    )
    chosen = ranked[:n]
    for t in chosen:
        if t.basis == "subaward-evidence":
            edge = t.edges[0]
            t.why = (
                f"{t.edge_count} publication-grade subaward "
                f"{'edge' if t.edge_count == 1 else 'edges'} bound to "
                f"selected play {t.play.get('source_id') or '?'} · agency "
                f"and shared CORE {', '.join(edge.get('_shared_core_terms') or [])} "
                f"· subaward {edge.get('subaward_id')} · prime award "
                f"{edge.get('prime_award_id')}"
            )
    if diagnostics is not None:
        for reason in sorted(diagnostic_counts):
            examples = sorted(diagnostic_examples[reason])[:3]
            example_text = ", ".join(
                f"{play_id} × {prime}" for play_id, prime in examples)
            diagnostics.append(
                f"suppressed {diagnostic_counts[reason]} subaward candidate "
                f"edge(s): {reason}; examples {example_text}")
    return chosen


def _sam_acquisition_route_key(record: dict) -> str:
    """Stable notice-family key so amendments cannot masquerade as new work."""

    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    solicitation = re.sub(
        r"\s+", " ", str(raw.get("solicitation") or "").strip()).casefold()
    if solicitation:
        return f"sam-solicitation:{solicitation}"
    identity = notice_guid(record).strip()
    return f"sam-notice:{identity}" if identity else ""


def _acquisition_route_key_for_best_fit(play: BestFitPick) -> str:
    if play.source_kind == "sam.gov":
        return _sam_acquisition_route_key(play.record)
    identity = str(play.gid or "").strip()
    return f"award:{identity}" if identity else ""


def _is_acquisition_route_notice(record: dict) -> bool:
    """Permit live notice families plus explicit official access vehicles."""

    if notice_family_rank(record) is not None:
        return True
    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    text = " ".join(str(value or "") for value in (
        record.get("title"), record.get("description"),
        raw.get("description_snippet"), raw.get("type")))
    return bool(re.search(
        r"\b(?:commercial solutions opening|cso|broad agency "
        r"announcement|baa|contract vehicle|multiple award schedule|"
        r"governmentwide acquisition contract|gwac|idiq|bpa)\b",
        text, flags=re.IGNORECASE))


def _acquisition_pathway_pick_is_publishable(
        sweep: dict, pick: AcquisitionPathPick) -> bool:
    """A route needs a requirement plus at least one substantive buy fact."""

    if pick.source_kind == "usaspending-award":
        record = _enriched_award_record(
            sweep, pick.record, gid=pick.gid, award_id=pick.award_id)
        family = "award"
        identity_label = "Award"
        identity_value = pick.award_id
    else:
        record = pick.record
        family = "notice"
        identity_label = "Solicitation"
        identity_value = notice_identity_label(pick.record)
    facts, missing = _procurement_facts(
        record, family=family, identity_label=identity_label,
        identity_value=identity_value)
    facts = [fact for fact in facts if fact.get("label") != "Co-holders"]
    try:
        _acquisition_pathway_public_fields(
            _acquisition_pathway_basis(pick, facts, missing))
    except ValueError:
        return False
    return True


def select_acquisition_pathways(
        sweep: dict, best_fit: list[BestFitPick],
        competitors: list[CompetitorPick], *, n: int = 4, scope=None,
        profile=None, taxonomy=None, client_name: str = "",
        today: Optional[date] = None) -> list[AcquisitionPathPick]:
    """Build the honest Section-03 fallback from already sanctioned rows.

    The pathway band answers a different question from the competitor band:
    it exposes the agency's observed buying route and the exact access facts
    still requiring validation. A recipient is deliberately not carried on
    the public pick. The selector first searches relevant, in-scope SAM
    records not already shown as best-fit opportunities, then active award
    corridors not already shown as best-fit or competitor records. It never
    relabels a displayed record merely to fill the layout.
    """

    if n <= 0:
        return []
    resolved_client = (
        client_name
        or str(getattr(profile, "client_name", "") or "")
        or str(sweep.get("client") or "")
    )
    if taxonomy is None and resolved_client:
        taxonomy = (
            load_taxonomy(resolved_client)
            or derived_taxonomy(resolved_client)
        )
    if taxonomy is None:
        raise ValueError(
            "acquisition pathway selection requires the current client "
            "taxonomy")
    if today is None:
        sweep_day = _iso_day(sweep.get("generated_at"))
        if not sweep_day:
            raise ValueError(
                "acquisition pathway selection requires a deterministic "
                "compose date or sweep generation date")
        today = date.fromisoformat(sweep_day)

    visible_route_keys = {
        _acquisition_route_key_for_best_fit(play)
        for play in best_fit
        if _acquisition_route_key_for_best_fit(play)
    }
    visible_route_keys.update(
        f"award:{str(lane.gid or '').strip()}"
        for lane in competitors if str(lane.gid or "").strip()
    )
    disallowed_company_keys = {
        _company_key(lane.recipient)
        for lane in competitors if _company_key(lane.recipient)
    }
    disallowed_company_keys.update(
        _company_key(play.recipient)
        for play in best_fit if _company_key(play.recipient)
    )

    def mentions_displayed_company(pick: AcquisitionPathPick) -> bool:
        public_text = _company_key(
            _acquisition_requirement_excerpt(
                pick.record, pick.capability))
        return any(re.search(
            rf"(?:^| ){re.escape(key)}(?: |$)", public_text)
            for key in disallowed_company_keys)

    results = sweep.get("results") or {}
    triage = results.get("triage") or {}
    sam_rows = results.get("sam.gov") or []
    guid_counts: dict[str, int] = {}
    for row in sam_rows:
        guid = notice_guid(row)
        if guid:
            guid_counts[guid] = guid_counts.get(guid, 0) + 1
    sam_by_route: dict[str, tuple[tuple, AcquisitionPathPick]] = {}
    for record in sam_rows:
        identity = notice_guid(record)
        route_key = _sam_acquisition_route_key(record)
        if (not identity or not route_key or route_key in visible_route_keys
                or guid_counts.get(identity) != 1
                or not _is_acquisition_route_notice(record)
                or (triage.get(str(record.get("source_id") or "")) or {}).get(
                    "verdict") == "discard"):
            continue
        verdict = score_record(
            record, taxonomy, engagement_scope=scope)
        if (not verdict.relevant or verdict.off_scope
                or "unresolved" in str(verdict.scope_basis or "")):
            continue
        core_terms = [
            str(term).strip() for term in verdict.core_terms
            if str(term).strip()
        ]
        if not core_terms:
            continue
        agency = str(record.get("agency") or "").strip()
        pick = AcquisitionPathPick(
            agency=agency,
            buyer=agency,
            capability=core_terms[0],
            record=dict(record),
            source_kind="sam.gov",
            source_identity=identity,
            notice_guid=identity,
            why=(f"distinct source-stated SAM acquisition path for "
                 f"{core_terms[0]} · {identity}; access conditions require "
                 "record-by-record validation"),
            scope_ok=True,
            scope_basis=str(verdict.scope_basis or ""),
            verdict=verdict,
        )
        if (mentions_displayed_company(pick)
                or not _acquisition_pathway_pick_is_publishable(sweep, pick)):
            continue
        family_rank = notice_family_rank(record)
        posted = _iso_day(
            record.get("modified_date") or record.get("updated_at")
            or record.get("posted_date"))
        posted_ordinal = (
            date.fromisoformat(posted).toordinal() if posted else 0)
        rank = (
            family_rank is None,
            family_rank if family_rank is not None else 99,
            -verdict.score,
            -len(core_terms),
            -posted_ordinal,
            _iso_day(record.get("response_deadline")) or "9999-12-31",
            identity,
            _canonical(record),
        )
        prior = sam_by_route.get(route_key)
        if prior is None or rank < prior[0]:
            sam_by_route[route_key] = (rank, pick)

    sam_candidates = [
        pick for _rank, pick in sorted(sam_by_route.values(), key=lambda x: x[0])
    ]

    award_candidates: list[AcquisitionPathPick] = []
    if today is not None and resolved_client:
        award_pool = select_award_best_fit(
            sweep, taxonomy, scope, client_name=resolved_client,
            today=today, n=10_000)
        for play in award_pool:
            route_key = _acquisition_route_key_for_best_fit(play)
            identity = str(play.gid or "").strip()
            if (not route_key or route_key in visible_route_keys
                    or not identity
                    or _company_key(play.recipient)
                    in disallowed_company_keys
                    or len(_client_relevance_buyer_records(
                        sweep, identity)) != 1):
                continue
            core_terms = [
                str(term).strip() for term in play.verdict.core_terms
                if str(term).strip()
            ]
            if not core_terms:
                continue
            pick = AcquisitionPathPick(
                agency=play.agency,
                buyer=play.buyer or play.agency,
                capability=core_terms[0],
                record=dict(play.record),
                source_kind="usaspending-award",
                source_identity=identity,
                award_id=play.award_id,
                gid=identity,
                why=(f"distinct executed acquisition path for "
                     f"{core_terms[0]} · award {play.award_id}; access "
                     "conditions require record-by-record validation"),
                scope_ok=True,
                scope_basis=str(
                    play.verdict.scope_basis or play.scope_basis or ""),
                client_footprint=_is_client_footprint(
                    play.recipient, play.record, resolved_client,
                    taxonomy=taxonomy),
                verdict=play.verdict,
            )
            if (not mentions_displayed_company(pick)
                    and _acquisition_pathway_pick_is_publishable(sweep, pick)):
                award_candidates.append(pick)

    selected: list[AcquisitionPathPick] = []
    seen_identities: set[str] = set()
    for pick in [*sam_candidates, *award_candidates]:
        if pick.source_identity in seen_identities:
            continue
        seen_identities.add(pick.source_identity)
        selected.append(pick)
        if len(selected) >= n:
            break
    return selected


def reserve_distinct_forward_timing(
        pathways: list[AcquisitionPathPick],
        horizon: list[HorizonPick]) -> list[AcquisitionPathPick]:
    """Keep one nearest forward record when every Horizon row is a path.

    Section 03 and Forward Timing answer different questions and therefore
    cannot publish the same award identity. If Horizon already has a record
    outside the pathway pool, no reservation is needed. Otherwise the nearest
    cited forward event wins its chronological role and is removed from the
    pathway candidates. A genuinely one-record universe remains an honest
    named insufficiency rather than a duplicated card.
    """

    if not pathways or not horizon:
        return list(pathways)

    def path_keys(pick: AcquisitionPathPick) -> set[str]:
        return {
            str(value).strip().casefold()
            for value in (
                pick.source_identity, pick.gid, pick.award_id,
            ) if str(value).strip()
        }

    def horizon_keys(pick: HorizonPick) -> set[str]:
        return {
            str(value).strip().casefold()
            for value in (
                pick.ident, pick.gid, pick.award_id,
            ) if str(value).strip()
        }

    pathway_keys = set().union(*(path_keys(pick) for pick in pathways))
    if any(horizon_keys(pick).isdisjoint(pathway_keys) for pick in horizon):
        return list(pathways)
    reserved = horizon_keys(horizon[0])
    return [
        pick for pick in pathways if path_keys(pick).isdisjoint(reserved)
    ]


_PROGRAM_HORIZON_SOURCES = (
    "dsip_topics",
    "sbir_gov",
    "grants_gov",
    "reginfo_unified_agenda",
    "darpa_opportunities",
    "dod_budget_exhibits",
    "foreign_assistance",
)


def _program_identity(source: str, record_id: str) -> str:
    """Canonical cross-adapter identity for one stored PROGRAM record."""
    if source not in _PROGRAM_HORIZON_SOURCES or not record_id.strip():
        raise ValueError("program source and record identity are required")
    return f"{source}::{record_id.strip()}"


def _program_identity_parts(identity: str) -> tuple[str, str] | None:
    source, marker, record_id = str(identity or "").partition("::")
    if marker and source in _PROGRAM_HORIZON_SOURCES and record_id.strip():
        return source, record_id.strip()
    return None


def _partial_program_day(value: object) -> str:
    """Normalize an official program date without inventing its month/year."""
    day = _event_day(value)
    if day:
        return day
    match = re.search(
        r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})",
        str(value or ""),
    )
    if not match:
        return ""
    month = int(match.group("month"))
    stated_day = int(match.group("day")) or 1
    try:
        return date(
            int(match.group("year")), month, stated_day
        ).isoformat()
    except ValueError:
        return ""


def _program_horizon_timing(
    source: str,
    row: dict,
    *,
    today: date,
    ceiling: str,
) -> tuple[str, str, str] | None:
    """Return ranking day, exact display basis, and public timing label.

    Actionable stated dates win. Publication and budget signals must be
    current enough to be prospective; an old fiscal year never consumes a
    visible Horizon slot merely because its API responded today.
    """
    floor = today.isoformat()
    if source == "dsip_topics":
        for field, label in (
            ("close_date", "ACTIVE TOPIC · CLOSES"),
            ("open_date", "ACTIVE TOPIC · OPENS"),
            ("pre_release_end_date", "PRE-RELEASE · ENDS"),
        ):
            day = _event_day(row.get(field))
            if day and floor <= day <= ceiling:
                return day, day, label
        return None
    if source == "sbir_gov":
        for field, label in (
            ("close_date", "SBIR/STTR TOPIC · CLOSES"),
            ("open_date", "SBIR/STTR TOPIC · OPENS"),
        ):
            day = _event_day(row.get(field))
            if day and floor <= day <= ceiling:
                return day, day, label
        return None
    if source == "grants_gov":
        status = str(row.get("status") or "").strip().lower()
        open_day = _event_day(row.get("open_date"))
        close_day = _event_day(row.get("close_date"))
        if status == "forecasted" and open_day and floor <= open_day <= ceiling:
            return open_day, open_day, "FEDERAL ASSISTANCE · FORECASTED OPEN"
        if close_day and floor <= close_day <= ceiling:
            return close_day, close_day, "FEDERAL ASSISTANCE · CLOSES"
        if open_day and floor <= open_day <= ceiling:
            return open_day, open_day, "FEDERAL ASSISTANCE · OPENS"
        return None
    if source == "reginfo_unified_agenda":
        days = []
        for event in (row.get("timetable") or []):
            if not isinstance(event, dict):
                continue
            raw_date = str(event.get("date") or "").strip()
            day = _partial_program_day(raw_date)
            if not day or not floor <= day <= ceiling:
                continue
            month_only = re.fullmatch(
                r"(?P<month>\d{1,2})/00/(?P<year>\d{4})", raw_date)
            display = (
                f"{int(month_only.group('month')):02d}/"
                f"{month_only.group('year')}"
                if month_only else day
            )
            days.append((day, display))
        if days:
            rank_day, display = min(days)
            return rank_day, display, "UNIFIED AGENDA · PLANNED ACTION"
        return None
    if source == "darpa_opportunities":
        published = _event_day(str(row.get("published_at") or "")[:10])
        if not published:
            published = _event_day(row.get("data_as_of"))
        freshness_floor = (today - timedelta(days=180)).isoformat()
        if published and freshness_floor <= published <= floor:
            return published, published, "DARPA · OFFICIAL PROGRAM SIGNAL"
        return None
    if source in {"dod_budget_exhibits", "foreign_assistance"}:
        raw_year = (
            row.get("budget_year")
            if source == "dod_budget_exhibits"
            else row.get("fiscal_year")
        )
        match = re.search(r"\b(20\d{2})\b", str(raw_year or ""))
        if not match:
            return None
        year = int(match.group(1))
        if not today.year <= year <= int(ceiling[:4]):
            return None
        label = (
            "DOD BUDGET EXHIBIT · REQUEST"
            if source == "dod_budget_exhibits"
            else "FOREIGN ASSISTANCE · BUDGET REQUEST"
        )
        # Fiscal-year rows state no day. Sort them behind exact dated actions
        # without turning the sorting sentinel into client-facing evidence.
        return ceiling, f"FY{year}", label
    return None


def _program_public_timing_value(event_basis: str) -> str:
    """Render an exact PROGRAM date basis without inventing a day."""
    month_only = re.fullmatch(
        r"(?P<month>\d{2})/(?P<year>\d{4})", str(event_basis or ""))
    if month_only:
        return date(
            int(month_only.group("year")),
            int(month_only.group("month")),
            1,
        ).strftime("%b %Y").upper()
    return fmt_day(event_basis) or str(event_basis or "")


def _program_duplicate_key(pick: HorizonPick) -> tuple[str, ...]:
    """Collapse only the same defense topic mirrored by DSIP and SBIR.gov."""
    source = str(pick.row.get("_horizon_program_source") or "")
    if source not in {"dsip_topics", "sbir_gov"}:
        return ("identity", pick.ident)
    title = re.sub(
        r"[^a-z0-9]+", " ", str(pick.row.get("title") or "").lower()
    ).strip()
    close_day = _event_day(pick.row.get("close_date"))
    raw_agency = str(pick.row.get("agency") or "").lower()
    agency = (
        "department of defense"
        if raw_agency.strip() in {"dod", "department of war"}
        or "defense" in raw_agency
        else re.sub(r"[^a-z0-9]+", " ", raw_agency).strip()
    )
    if not title or not close_day or not agency:
        return ("identity", pick.ident)
    return ("defense-sbir-topic", agency, title, close_day)


def select_horizon(sweep: dict, calendar: Optional[dict], *,
                   today: date, n: int = 4,
                   scope=None, taxonomy=None,
                   award_corridors: Optional[list] = None,
                   featured_award_identities: Optional[set[str]] = None,
                   client_name: str = "") -> list[HorizonPick]:
    floor = today.isoformat()
    ceiling_date = add_months(today, HORIZON_MONTHS)
    ceiling = ceiling_date.isoformat()
    featured_awards = {
        str(identity).strip().casefold()
        for identity in (featured_award_identities or set())
        if str(identity).strip()
    }

    def in_window(day: str) -> bool:
        return bool(day) and floor <= day <= ceiling

    def machine_relevant(row: dict) -> bool:
        if taxonomy is None:
            return True
        verdict = score_record(
            row, taxonomy, engagement_scope=scope)
        return bool(
            verdict.relevant
            and not verdict.off_scope
            and (scope is None or "unresolved" not in verdict.scope_basis)
        )

    pool: list[tuple] = []
    for lane_name, posture in (("attack", "ATTACK"), ("defend", "DEFEND")):
        for row in (calendar or {}).get(lane_name) or []:
            if not machine_relevant(row):
                continue
            day = _event_day(row.get("pop_end"))
            if in_window(day):
                agency = str(row.get("awarding_agency") or row.get("agency")
                             or "")
                inside, basis = _scope_verdict(row, scope)
                if not inside:
                    continue
                ident = str(row.get("award_id") or "")
                if not ident:
                    continue
                pool.append((day, 0, ident, _canonical(row), HorizonPick(
                    kind="calendar-expiry", event_date=day, ident=ident,
                    row=row, why=f"award period ends {day}",
                    agency=agency, scope_ok=True, scope_basis=basis,
                    award_id=ident,
                    posture=posture, timing_source_value=str(
                        row.get("pop_end") or ""))))
    results = sweep.get("results") or {}
    for row in (results.get("forecast_signals") or {}).get("matched") or []:
        if not machine_relevant(row):
            continue
        milestones = []
        for milestone, source_value in (
            ("solicitation", row.get("anticipated_solicitation")),
            ("award", row.get("anticipated_award")),
        ):
            timing = _forecast_timing(
                source_value, floor=today, ceiling=ceiling_date)
            if timing is not None:
                milestones.append((*timing, milestone))
        if not milestones:
            continue
        agency = str(row.get("agency") or row.get("awarding_agency") or "")
        inside, basis = _scope_verdict(row, scope)
        if not inside:
            continue
        day, source_value, precision, milestone = min(milestones)
        ident = str(row.get("source_id") or "")
        if not ident:
            continue
        pool.append((day, 1, ident, _canonical(row), HorizonPick(
            kind="forecast", event_date=day, ident=ident, row=row,
            why=f"forecast {milestone} {day}",
            agency=agency, scope_ok=True, scope_basis=basis,
            posture="QUALIFY", timing_source_value=source_value,
            timing_precision=precision)))
    for row in (results.get("contract_awards") or {}).get("recompetes") or []:
        if not machine_relevant(row):
            continue
        day = _event_day(row.get("completion"))
        if in_window(day):
            agency = str(row.get("agency") or row.get("awarding_agency")
                         or "")
            inside, basis = _scope_verdict(row, scope)
            if not inside:
                continue
            ident = (str(row.get("piid") or "")
                     or f"{row.get('awardee') or 'unknown'}·{day}")
            incumbent = str(row.get("awardee") or "")
            posture = ("DEFEND" if client_name and _is_client_company(
                incumbent, client_name) else "ATTACK")
            gid = str(row.get("generated_internal_id") or "")
            pool.append((day, 2, ident, _canonical(row), HorizonPick(
                kind="expiring", event_date=day, ident=ident, row=row,
                why=f"incumbent contract completes {day}",
                agency=agency, scope_ok=True, scope_basis=basis,
                award_id=str(row.get("piid") or ""), gid=gid,
                posture=posture,
                timing_source_value=str(row.get("completion") or ""))))

    for row in (results.get("expiring_awards") or {}).get("rows") or []:
        if not machine_relevant(row):
            continue
        day = _event_day(row.get("end_date"))
        if not in_window(day):
            continue
        agency = str(
            row.get("awarding_sub_agency")
            or row.get("awarding_agency") or "")
        inside, basis = _scope_verdict(row, scope)
        if not inside:
            continue
        award_id = str(row.get("award_id") or "")
        gid = str(row.get("generated_internal_id") or "")
        if not award_id:
            continue
        incumbent = str(row.get("recipient") or "")
        posture = ("DEFEND" if client_name and _is_client_company(
            incumbent, client_name) else "ATTACK")
        ident = gid or award_id
        pool.append((day, 3, ident, _canonical(row), HorizonPick(
            kind="usaspending-expiring", event_date=day, ident=ident,
            row=row, why=f"USAspending award period ends {day}",
            agency=agency, scope_ok=True, scope_basis=basis,
            award_id=award_id, gid=gid, posture=posture,
            timing_source_value=str(row.get("end_date") or ""))))

    # A client may truthfully have no capability-evidenced APFS or recompete
    # row. Active award PoP ends are still forward acquisition events, but
    # only at the same machine bar as award best fit: sanctioned CORE
    # evidence, positive scope, a future/in-window end, and both PIID + GID.
    # One GID yields one horizon event even if the buyer map repeats it.
    award_windows: dict[str, tuple] = {}
    if taxonomy is not None:
        candidates = []
        if award_corridors is None:
            buyer_map = results.get("incumbent_buyer_map") or {}
            for buyer in buyer_map.get("buyers") or []:
                buyer_name = str(buyer.get("buyer") or "")
                buyer_agency = str(buyer.get("agency") or "")
                for source_row in buyer.get("records") or []:
                    candidates.append(
                        (buyer_name, buyer_agency, source_row))
        else:
            for corridor in award_corridors:
                source_row = getattr(corridor, "record", {})
                if getattr(corridor, "source_kind", "usaspending-award") \
                        != "usaspending-award":
                    continue
                candidates.append((
                    str(getattr(corridor, "buyer", "") or ""),
                    str(getattr(corridor, "agency", "") or ""),
                    source_row,
                ))
        for buyer_name, buyer_agency, source_row in candidates:
            if source_row.get("kind") != "award":
                continue
            day = _iso_day(source_row.get("end_date"))
            if not in_window(day):
                continue
            award_id = str(source_row.get("award_id") or "")
            gid = str(source_row.get("generated_internal_id") or "")
            if not award_id or not gid:
                continue
            row = dict(source_row)
            row.setdefault("generated_id", gid)
            row.setdefault("buyer", buyer_name)
            row.setdefault("agency", buyer_agency)
            inside, basis = _scope_verdict(row, scope)
            if not inside:
                continue
            verdict = score_opportunity_evidence(
                sweep, [gid], taxonomy)
            if not verdict.relevant:
                continue
            quote = (verdict.spans[0].context if verdict.spans else
                     str(row.get("description") or ""))
            pick = HorizonPick(
                kind="award-window", event_date=day, ident=gid, row=row,
                why=f"active award PoP ends {day}",
                agency=buyer_agency, scope_ok=True, scope_basis=basis,
                award_id=award_id, gid=gid, quote=quote)
            footprint = bool(client_name and _is_client_footprint(
                str(row.get("recipient") or ""), row, client_name,
                taxonomy=taxonomy))
            pick.posture = "DEFEND" if footprint else "ATTACK"
            pick.timing_source_value = str(source_row.get("end_date") or "")
            candidate = (day, 4, gid, _canonical(row), pick)
            prior = award_windows.get(gid)
            if prior is None or candidate[:4] < prior[:4]:
                award_windows[gid] = candidate
        window_items = list(award_windows.values())
        material_windows = [
            item for item in window_items
            if _num(item[4].row.get("amount")) >= MATERIAL_AWARD_FLOOR
        ]
        # A full-size forward card is also a priority surface.  When material
        # award windows exist, keep sub-floor records in the source/evidence
        # store rather than granting them equal visual weight. Sparse clients
        # with no material window retain their best cited clocks.
        pool.extend(material_windows if material_windows else window_items)
    pool.sort(key=lambda item: item[:4])

    # PROGRAM rows were already capability-matched and scope-gated by the
    # sanctioned adapters. Re-run the same C1 relevance/scope bar here, bind
    # their exact official record ids/URLs, and reserve at most one slot when
    # stronger dated acquisition events already fill the band. PROGRAM never
    # enters the live-solicitation ledger and never displaces more than one
    # dated acquisition event.
    program_pool: list[tuple] = []
    from agents.reports.horizon_discovery._stored_program import (
        stored_program_records,
    )

    for priority, source in enumerate(_PROGRAM_HORIZON_SOURCES):
        payload = results.get(source) or {}
        for source_row in stored_program_records(payload, source=source):
            if not machine_relevant(source_row):
                continue
            record_id = str(source_row["record_id"])
            ident = _program_identity(source, record_id)
            timing = _program_horizon_timing(
                source, source_row, today=today, ceiling=ceiling)
            if timing is None:
                continue
            rank_day, event_basis, timing_label = timing
            timing_precision = (
                "calendar-month"
                if re.fullmatch(r"\d{2}/\d{4}", event_basis)
                else "fiscal-year"
                if re.fullmatch(r"FY\d{4}", event_basis, re.I)
                else "exact-date"
            )
            inside, basis = _scope_verdict(source_row, scope)
            if not inside:
                continue
            row = dict(source_row)
            row["_horizon_program_source"] = source
            row["_horizon_timing_label"] = timing_label
            quote = str(
                row.get("objective") or row.get("description")
                or row.get("abstract") or row.get("summary")
                or row.get("title") or ""
            ).strip()
            if not quote:
                continue
            source_rank = (
                -date.fromisoformat(rank_day).toordinal()
                if source == "darpa_opportunities" else rank_day
            )
            program_pool.append((
                priority,
                source_rank,
                ident,
                _canonical(row),
                HorizonPick(
                    kind="program",
                    event_date=event_basis,
                    ident=ident,
                    row=row,
                    why=f"official {source} PROGRAM signal · {event_basis}",
                    agency=str(row.get("agency") or ""),
                    scope_ok=True,
                    scope_basis=basis,
                    quote=quote,
                    timing_source_value=event_basis,
                    timing_precision=timing_precision,
                    timing_sort_date=rank_day,
                ),
            ))
    program_pool.sort(key=lambda item: item[:4])
    deduped_program_pool = []
    seen_programs: set[tuple[str, ...]] = set()
    for item in program_pool:
        duplicate_key = _program_duplicate_key(item[4])
        if duplicate_key in seen_programs:
            continue
        seen_programs.add(duplicate_key)
        deduped_program_pool.append(item)

    if n <= 0:
        return []
    standard_picks = []
    seen_awards: set[str] = set()
    for item in pool:
        pick = item[4]
        award_identity = _first_stated(
            pick.award_id,
            pick.row.get("award_id"), pick.row.get("piid"),
        ).casefold()
        generated_identity = _first_stated(
            pick.gid,
            pick.row.get("generated_internal_id"),
            pick.row.get("generated_id"),
        ).casefold()
        if award_identity and pick.kind in {
                "calendar-expiry", "expiring", "usaspending-expiring",
                "award-window"}:
            if featured_awards.intersection({
                    award_identity, generated_identity,
                    str(pick.ident or "").casefold()}):
                continue
            if award_identity in seen_awards:
                continue
            seen_awards.add(award_identity)
        standard_picks.append(pick)
    def select_standard(limit: int) -> list[HorizonPick]:
        """Keep chronology while reserving one explicit forecast seat."""
        selected = standard_picks[:limit]
        forecasts = [
            pick for pick in standard_picks if pick.kind == "forecast"]
        if limit and forecasts and not any(
                pick.kind == "forecast" for pick in selected):
            selected[-1] = forecasts[0]
            order = {
                (pick.kind, pick.ident): index
                for index, pick in enumerate(standard_picks)
            }
            selected.sort(key=lambda pick: order[(pick.kind, pick.ident)])
        return selected

    program_picks = [item[4] for item in deduped_program_pool]
    if not program_picks:
        return select_standard(min(len(standard_picks), n))
    standard_limit = min(len(standard_picks), max(0, n - 1))
    chosen = select_standard(standard_limit)
    chosen.extend(program_picks[:min(2, n - len(chosen))])
    if len(chosen) < n:
        selected_keys = {(pick.kind, pick.ident) for pick in chosen}
        for pick in standard_picks:
            key = (pick.kind, pick.ident)
            if key in selected_keys:
                continue
            chosen.append(pick)
            selected_keys.add(key)
            if len(chosen) == n:
                break
    return chosen[:n]


# ── composition: bands from picks; prose only through the seam ──────────────

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def fmt_money(raw: float) -> str:
    """Deterministic house money text: $71.23M / $677.55K / $2B."""
    def trim(x: float) -> str:
        s = f"{x:.2f}".rstrip("0").rstrip(".")
        return s
    n = abs(raw)
    if n >= 1e9:
        return f"${trim(raw / 1e9)}B"
    if n >= 1e6:
        return f"${trim(raw / 1e6)}M"
    if n >= 1e3:
        return f"${trim(raw / 1e3)}K"
    return f"${raw:,.0f}"


def fmt_day(iso: str) -> str:
    """'2026-07-30' -> '30 JUL 2026'; '' passes through."""
    day = _iso_day(iso)
    if not day:
        return ""
    y, m, d = day.split("-")
    return f"{int(d):02d} {_MONTHS[int(m) - 1]} {y}"


def _first_stated(*values) -> str:
    for value in values:
        text = " ".join(str(value or "").strip().split())
        if text:
            return text
    return ""


def _place_text(record: dict) -> str:
    """One compact place statement, using only stored source fields."""

    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    place = record.get("place_of_performance") \
        if isinstance(record.get("place_of_performance"), dict) else {}
    street = _first_stated(
        place.get("address_line1"), raw.get("pop_street"),
        record.get("pop_street"))
    city = _first_stated(
        place.get("city_name"), raw.get("pop_city"), record.get("pop_city"))
    state = _first_stated(
        place.get("state_code"), place.get("state_name"),
        raw.get("pop_state"), record.get("pop_state"))
    postal = _first_stated(
        place.get("zip5"), place.get("foreign_postal_code"),
        raw.get("pop_zip"), record.get("pop_zip"))
    country = _first_stated(
        place.get("country_name"), raw.get("pop_country"),
        record.get("pop_country"))
    locality = ", ".join(value for value in (city, state) if value)
    parts = [value for value in (street, locality, postal, country) if value]
    return " · ".join(parts)


def _award_repull_record(
    sweep: dict, *, gid: str = "", award_id: str = "",
) -> dict:
    """Exact current award-detail row, preferring GID over unique PIID."""

    rows = (sweep.get("results") or {}).get("award_repulls") or []
    if gid:
        exact = [row for row in rows
                 if str(row.get("generated_id") or "") == gid]
        if len(exact) == 1:
            return exact[0]
    if award_id:
        exact = [row for row in rows
                 if str(row.get("award_id") or "") == award_id]
        if len(exact) == 1:
            return exact[0]
    return {}


def _enriched_award_record(
    sweep: dict, record: dict, *, gid: str = "", award_id: str = "",
) -> dict:
    """Stored corridor plus its exact primary-record re-pull, if present."""

    merged = dict(record)
    for key, value in _award_repull_record(
            sweep, gid=gid, award_id=award_id).items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _notice_acquisition_method(record: dict) -> str:
    """Return only an acquisition method explicitly named by the notice."""

    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    text = " ".join(str(value or "") for value in (
        record.get("title"), record.get("description"),
        raw.get("description_snippet")))
    methods = (
        (r"\bcommercial solutions opening\b", "Commercial Solutions Opening"),
        (r"\bbroad agency announcement\b", "Broad Agency Announcement"),
        (r"\bgovernmentwide acquisition contract\b",
         "Governmentwide Acquisition Contract"),
        (r"\bmultiple award schedule\b", "Multiple Award Schedule"),
        (r"\bindefinite[- ]delivery(?:/| and )indefinite[- ]quantity\b",
         "Indefinite-Delivery/Indefinite-Quantity"),
        (r"\bblanket purchase agreement\b", "Blanket Purchase Agreement"),
    )
    for pattern, label in methods:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return ""


def _procurement_facts(
    record: dict,
    *,
    family: str,
    identity_label: str = "",
    identity_value: str = "",
) -> tuple[list[dict], list[str]]:
    """Evidence-only procurement projection shared by every public card.

    Values are copied from the stored record. Missing fields are summarized
    separately so absence is visible without filling cards with guessed
    defaults such as ``full and open`` or a vehicle parsed from an ID.
    """

    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    candidates: list[tuple[str, object, str]] = []
    if identity_label:
        candidates.append((identity_label, identity_value, "record identity"))
    if family == "notice":
        candidates.extend([
            ("Acquisition method", _notice_acquisition_method(record),
             "source-stated notice language"),
            ("Notice type", _first_stated(
                record.get("notice_type"), raw.get("type")),
             "SAM notice type"),
        ])
    candidates.extend([
        ("NAICS", _first_source_code(
            record.get("naics"), record.get("naics_code"),
            raw.get("naics"), raw.get("naics_code")), "NAICS"),
        ("PSC", _first_source_code(
            record.get("psc"), record.get("psc_code"),
            raw.get("psc"), raw.get("classification_code")), "PSC"),
        ("Set-aside", _first_stated(
            record.get("set_aside"), raw.get("set_aside")),
         "set-aside"),
        ("Competition", _first_stated(record.get("competition")),
         "extent competed"),
        ("Place", _place_text(record), "place of performance"),
    ])
    if family in {"award", "expiring"}:
        amount = record.get("amount")
        if isinstance(amount, (int, float)):
            amount_basis = _first_stated(
                record.get("amount_basis"), "award record")
            amount_label = {
                "base + all options": "Base + all options",
                "base + exercised options": "Base + exercised options",
                "action obligation": "Action obligation",
                "usaspending award amount": "USAspending award amount",
            }.get(amount_basis.casefold(),
                  "Obligated" if family == "award"
                  else "Cited award value")
            candidates.append((
                amount_label,
                fmt_money(float(amount)),
                amount_basis,
            ))
        ceiling = record.get("potential_ceiling")
        if isinstance(ceiling, (int, float)):
            candidates.append((
                "Base + all options", fmt_money(float(ceiling)),
                "USAspending base_and_all_options",
            ))
        parent_idv = _first_stated(record.get("parent_award_id"))
        parent_type = _first_stated(
            record.get("parent_vehicle_type"),
            record.get("parent_award_structure"))
        award_structure = _first_stated(
            record.get("award_structure"), record.get("award_type"))
        holders = record.get("vehicle_coholders")
        if isinstance(holders, list):
            holder_names = [
                _first_stated(row.get("name") if isinstance(row, dict)
                              else row)
                for row in holders
            ]
            coholders = " · ".join(
                value for value in holder_names if value)
        else:
            coholders = _first_stated(holders)
        candidates.extend([
            ("Award structure", award_structure, "award type/structure"),
            ("Parent IDV", parent_idv,
             "USAspending parent_award.piid"),
            ("Parent IDV type", parent_type,
             "USAspending parent_award type"),
            ("Co-holders", coholders,
             "cited vehicle-holder records"),
        ])
    elif family == "forecast":
        stated_band = _first_stated(record.get("estimated_value_range"))
        lower = record.get("estimated_value_lower")
        upper = record.get("estimated_value_upper")
        numeric_lower = (float(lower) if isinstance(lower, (int, float))
                         and not isinstance(lower, bool) else None)
        numeric_upper = (float(upper) if isinstance(upper, (int, float))
                         and not isinstance(upper, bool) else None)
        if stated_band and numeric_lower is not None \
                and numeric_upper is not None:
            if numeric_lower == numeric_upper:
                band_label = "Published value"
                band_value = fmt_money(numeric_lower)
            else:
                band_label = "Published value band"
                band_value = (f"{fmt_money(numeric_lower)} to "
                              f"{fmt_money(numeric_upper)}")
            candidates.append((
                band_label, band_value,
                "forecast estimated_value_range: " + stated_band,
            ))
        elif stated_band and numeric_lower is not None:
            candidates.append((
                "Published lower boundary", fmt_money(numeric_lower),
                "forecast estimated_value_range: " + stated_band,
            ))
        elif stated_band and numeric_upper is not None:
            candidates.append((
                "Published upper boundary", fmt_money(numeric_upper),
                "forecast estimated_value_range: " + stated_band,
            ))
        candidates.extend([
            ("Award type", _first_stated(record.get("award_type")),
             "forecast award type"),
            ("Incumbent", _first_stated(record.get("incumbent_stated")),
             "forecast incumbent"),
        ])
    elif family == "calendar-expiry":
        candidates.extend([
            ("Award type", _first_stated(record.get("award_type")),
             "calendar award type"),
            ("Incumbent", _first_stated(
                record.get("recipient"), record.get("recipient_raw")),
             "calendar recipient"),
        ])

    facts = [
        {"label": label, "value": str(value), "source_field": source}
        for label, value, source in candidates if str(value or "").strip()
    ]
    stated = {fact["label"] for fact in facts}
    expected = {
        "notice": [
            "Acquisition method", "Notice type", "NAICS", "PSC",
            "Set-aside", "Place",
        ],
        "award": [
            "NAICS", "PSC", "Set-aside", "Competition", "Place",
            "Base + all options", "Award structure",
        ],
        "forecast": ["NAICS", "PSC", "Set-aside", "Award type", "Incumbent"],
        "calendar-expiry": [
            "NAICS", "Set-aside", "Award type", "Incumbent"],
        "expiring": ["NAICS", "PSC", "Competition", "Award structure"],
        "subaward": ["NAICS", "PSC", "Set-aside"],
    }.get(family, [])
    if family in {"award", "expiring"}:
        structure_text = _first_stated(
            record.get("award_type"), record.get("award_structure"),
            record.get("parent_vehicle_type"),
            record.get("parent_award_structure")).casefold()
        order_like = any(token in structure_text for token in (
            "delivery order", "task order", "bpa call", "indefinite",
        ))
        if parent_idv or order_like:
            expected.append("Parent IDV")
        if parent_idv:
            expected.extend(["Parent IDV type", "Co-holders"])
    missing = [label for label in expected if label not in stated]
    return facts, missing


def _competitor_boundary_facts(pick: CompetitorPick) -> list[dict]:
    """Other cited awards for this recipient inside the explicit NAICS set."""

    others = [row for row in pick.boundary_records
              if str(row.get("generated_internal_id") or "") != pick.gid]
    if not others:
        return []
    buyers = []
    for row in others:
        buyer = _compact_org(str(row.get("buyer") or row.get("agency") or ""))
        if buyer and buyer not in buyers:
            buyers.append(buyer)
    facts = [{
        "label": "Other boundary awards",
        "value": str(len(others)),
        "source_field": "buyer-map awards with explicit boundary NAICS",
    }]
    if buyers:
        facts.append({
            "label": "Other federal buyers",
            "value": " · ".join(buyers[:3]),
            "source_field": "buyer-map buying offices",
        })
    return facts


def notice_guid(record: dict) -> str:
    raw = (record.get("raw_payload") or {}).get("notice_id")
    cand = str(raw or record.get("source_id") or "")
    return cand


def notice_identity_label(record: dict) -> str:
    """Press-recognizable identity for evidence labels: the solicitation
    number when the record carries one, else the full notice id. Never a
    truncated GUID."""
    raw = record.get("raw_payload") or {}
    sol = str(raw.get("solicitation") or "").strip()
    return sol or notice_guid(record)


def _short(name: str, limit: int = 26) -> str:
    text = re.sub(r"\s+", " ", str(name)).strip()
    for suffix in (", INC. - FEDERAL", ", INC.", " INC.", " INC", " LLC",
                   " L.L.C.", " CORP.", " CORPORATION", " & PERMITS"):
        if text.upper().endswith(suffix):
            text = text[: -len(suffix)]
    if len(text) <= limit:
        return text.strip(" ·,&")
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip(" ·,&")


_HORIZON_JOB_CONTEXT_VERSION = 1
_HORIZON_CONTEXT_LIMIT = 112
_HORIZON_SOURCE_KIND_BY_EVENT = {
    "calendar-expiry": "award-period-calendar",
    "forecast": "agency-forecast",
    "expiring": "contract-award",
    "usaspending-expiring": "usaspending-expiring-award",
    "award-window": "usaspending-award",
    "program": "program",
}
_HORIZON_CONTEXT_FIELDS_BY_EVENT = {
    "calendar-expiry": frozenset({"description", "none"}),
    "forecast": frozenset({"description", "title", "none"}),
    "expiring": frozenset({"description", "none"}),
    "usaspending-expiring": frozenset({"description", "none"}),
    "award-window": frozenset({"description", "none"}),
    "program": frozenset({
        "objective", "description", "abstract", "summary", "title", "none",
    }),
}
_HORIZON_TRAIL_CONTEXT_PREFIX = "  - horizon job context "
_HORIZON_PROGRAM_TRAIL_PREFIX = "  - horizon program source "
_HORIZON_DECISION_TRAIL_PREFIX = "  - horizon decision evidence "


def _program_card_binding(card: dict) -> dict:
    """Exact source adapter, record, and official URL for one PROGRAM card."""
    from agents.reports.horizon_discovery._stored_program import (
        is_official_program_url,
    )

    machine = card.get("machine_evidence")
    if not isinstance(machine, dict):
        raise ValueError("program horizon card lacks machine evidence")
    adapter = str(machine.get("source_adapter") or "")
    record_id = str(card.get("program_record_id") or "")
    canonical_url = str(machine.get("canonical_url") or "")
    timing_value = str(machine.get("timing_value") or "")
    timing_label = str(machine.get("timing_label") or "")
    expected_identity = (
        _program_identity(adapter, record_id)
        if adapter in _PROGRAM_HORIZON_SOURCES and record_id else ""
    )
    if (
        machine.get("source_kind") != "program"
        or not expected_identity
        or machine.get("source_identity") != expected_identity
        or card.get("program_route_identity") != expected_identity
        or str(card.get("url") or "") != canonical_url
        or str(card.get("value") or "") != timing_value
        or str(card.get("small") or "") != timing_label
        or not is_official_program_url(canonical_url)
    ):
        raise ValueError(
            "program horizon card adapter, record identity, or official "
            "canonical URL is inconsistent"
        )
    return {
        "source_adapter": adapter,
        "record_id": record_id,
        "canonical_url": canonical_url,
        "timing_value": timing_value,
        "timing_label": timing_label,
    }


def _horizon_job_context_basis(pick: HorizonPick) -> dict:
    """Source field and exact text supporting one public work description."""
    row = pick.row
    source_field = "none"
    source_text = ""
    if pick.kind in {
            "award-window", "calendar-expiry", "expiring",
            "usaspending-expiring"}:
        source_text = str(row.get("description") or "").strip()
        if source_text:
            source_field = "description"
    elif pick.kind == "forecast":
        source_text = str(row.get("description") or "").strip()
        if source_text:
            source_field = "description"
        else:
            source_text = str(row.get("title") or "").strip()
            if source_text:
                source_field = "title"
    elif pick.kind == "program":
        for candidate in (
            "objective", "description", "abstract", "summary", "title",
        ):
            source_text = str(row.get(candidate) or "").strip()
            if source_text:
                source_field = candidate
                break
    return {
        "version": _HORIZON_JOB_CONTEXT_VERSION,
        "event_kind": pick.kind,
        "source_field": source_field,
        "source_text": source_text,
    }


def _horizon_job_context_public(basis: dict) -> dict:
    """Deterministic public projection of a bound Horizon source field."""
    if (not isinstance(basis, dict)
            or basis.get("version") != _HORIZON_JOB_CONTEXT_VERSION
            or basis.get("source_field") not in set().union(
                *_HORIZON_CONTEXT_FIELDS_BY_EVENT.values())):
        raise ValueError("invalid horizon job-context basis")
    event_kind = str(basis.get("event_kind") or "")
    source_field = str(basis.get("source_field") or "none")
    if event_kind not in _HORIZON_CONTEXT_FIELDS_BY_EVENT:
        raise ValueError("invalid horizon job-context event kind")
    if source_field not in _HORIZON_CONTEXT_FIELDS_BY_EVENT[event_kind]:
        raise ValueError(
            f"horizon job-context source field {source_field!r} is not "
            f"allowed for {event_kind!r}")
    raw_text = str(basis.get("source_text") or "").strip()
    if (source_field == "none") != (not raw_text):
        raise ValueError(
            "horizon job-context source field and text are inconsistent")
    text = re.sub(r"\s+", " ", raw_text) \
        .strip(" .")
    if source_field == "none" or not text:
        return {
            "kind": "not-stated",
            "text": "Work detail is not stated in the cited record.",
        }

    # Remove contact/link and monetary fragments from rendered copy. The full
    # untouched source text remains in the inert basis and compose trail.
    text = re.sub(r"https?://\S+|www\.\S+", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "", text)
    text = re.sub(r"\$[0-9][0-9,.]*(?:\.[0-9]+)?\s?[MBK]?\b", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,;:-.")

    prefixes = (
        "THIS IS A DEFINITIVE CONTRACT TO ACQUIRE ",
        "DEFINITIVE CONTRACT TO ACQUIRE ",
        "THIS IS A CONTRACT TO ACQUIRE ",
        "CONTRACT TO ACQUIRE ",
        "THE PURPOSE OF THIS REQUIREMENT IS FOR THE ",
        "THE PURPOSE OF THIS REQUIREMENT IS ",
    )
    upper = text.upper()
    for prefix in prefixes:
        if upper.startswith(prefix):
            text = text[len(prefix):].strip(" ,;:-.")
            break
    if not text:
        return {
            "kind": "not-stated",
            "text": "Work detail is not stated in the cited record.",
        }

    words = re.findall(r"[A-Za-z0-9]+", text)
    if len(words) == 1:
        return {
            "kind": "limited-description",
            "text": (f"{text} · fuller work detail is not stated in the "
                     "cited record."),
        }
    if len(text) > _HORIZON_CONTEXT_LIMIT:
        cut = text[:_HORIZON_CONTEXT_LIMIT - 1].rsplit(" ", 1)[0] \
            .rstrip(" ,;:-")
        text = f"{cut or text[:_HORIZON_CONTEXT_LIMIT - 1]}…"
    return {
        "kind": ("published-title" if source_field == "title"
                 else "published-description"),
        "text": text,
    }


def validate_machine_horizon_card(card: dict) -> dict:
    """Reproject and verify one machine-composed Horizon card."""
    machine = card.get("machine_evidence")
    if not isinstance(machine, dict):
        raise ValueError("horizon card lacks machine evidence")
    basis = machine.get("job_context_basis")
    expected = _horizon_job_context_public(basis)
    event_kind = str(basis.get("event_kind") or "")
    expected_source_kind = _HORIZON_SOURCE_KIND_BY_EVENT[event_kind]
    if machine.get("source_kind") != expected_source_kind:
        raise ValueError(
            f"horizon card {card.get('title')!r} source kind does not "
            "match its job-context event kind")
    timing = machine.get("timing_basis")
    if (machine.get("decision") not in {"ATTACK", "DEFEND", "QUALIFY"}
            or not str(machine.get("source_citation") or "").strip()
            or not isinstance(timing, dict)
            or set(timing) != {
                "source_value", "precision", "sort_date"}
            or not all(str(timing.get(key) or "").strip()
                       for key in timing)):
        raise ValueError(
            f"horizon card {card.get('title')!r} lacks exact cited timing "
            "or decision evidence")
    if event_kind == "program":
        _program_card_binding(card)
    if card.get("job_context") != expected:
        raise ValueError(
            f"horizon card {card.get('title')!r} public job context does "
            "not match its structured source basis; re-run compose")
    return expected


def _horizon_trail_contexts(trail_md: str) -> dict[str, dict]:
    """Parse the canonical, independently stored Horizon basis envelopes."""
    contexts: dict[str, dict] = {}
    for line in trail_md.splitlines():
        if not line.startswith(_HORIZON_TRAIL_CONTEXT_PREFIX):
            continue
        raw = line[len(_HORIZON_TRAIL_CONTEXT_PREFIX):]
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("compose trail has malformed horizon job context") \
                from exc
        if (not isinstance(envelope, dict)
                or set(envelope) != {"basis", "card_identity"}
                or not isinstance(envelope.get("basis"), dict)):
            raise ValueError("compose trail horizon job context is not exact")
        identity = str(envelope.get("card_identity") or "")
        if not identity or identity in contexts:
            raise ValueError(
                "compose trail horizon job-context identities are invalid")
        _horizon_job_context_public(envelope["basis"])
        contexts[identity] = envelope["basis"]
    return contexts


def _horizon_program_trail_bindings(trail_md: str) -> dict[str, dict]:
    """Parse independently stored PROGRAM adapter/record/URL bindings."""
    from agents.reports.horizon_discovery._stored_program import (
        is_official_program_url,
    )

    bindings: dict[str, dict] = {}
    for line in trail_md.splitlines():
        if not line.startswith(_HORIZON_PROGRAM_TRAIL_PREFIX):
            continue
        raw = line[len(_HORIZON_PROGRAM_TRAIL_PREFIX):]
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "compose trail has malformed horizon program source") from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"binding", "card_identity"}
            or not isinstance(envelope.get("binding"), dict)
            or set(envelope["binding"]) != {
                "source_adapter", "record_id", "canonical_url",
                "timing_value", "timing_label"}
        ):
            raise ValueError(
                "compose trail horizon program source is not exact")
        identity = str(envelope.get("card_identity") or "")
        binding = envelope["binding"]
        adapter = str(binding.get("source_adapter") or "")
        record_id = str(binding.get("record_id") or "")
        if (
            not identity
            or identity in bindings
            or adapter not in _PROGRAM_HORIZON_SOURCES
            or not record_id
            or _program_identity(adapter, record_id) != identity
            or not is_official_program_url(binding.get("canonical_url"))
            or not str(binding.get("timing_value") or "")
            or not str(binding.get("timing_label") or "")
        ):
            raise ValueError(
                "compose trail horizon program identities are invalid")
        bindings[identity] = binding
    return bindings


def _horizon_decision_trail_bindings(trail_md: str) -> dict[str, dict]:
    """Parse independent timing, posture, citation, and fact projections."""

    bindings: dict[str, dict] = {}
    required = {
        "decision", "source_citation", "timing_basis",
        "procurement_facts", "missing_procurement_facts",
    }
    for line in trail_md.splitlines():
        if not line.startswith(_HORIZON_DECISION_TRAIL_PREFIX):
            continue
        raw = line[len(_HORIZON_DECISION_TRAIL_PREFIX):]
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "compose trail has malformed horizon decision evidence"
            ) from exc
        if (not isinstance(envelope, dict)
                or set(envelope) != {"binding", "card_identity"}
                or not isinstance(envelope.get("binding"), dict)
                or set(envelope["binding"]) != required):
            raise ValueError(
                "compose trail horizon decision evidence is not exact")
        identity = str(envelope.get("card_identity") or "")
        binding = envelope["binding"]
        timing = binding.get("timing_basis")
        if (not identity or identity in bindings
                or binding.get("decision") not in {
                    "ATTACK", "DEFEND", "QUALIFY"}
                or not str(binding.get("source_citation") or "")
                or not isinstance(timing, dict)
                or set(timing) != {
                    "source_value", "precision", "sort_date"}
                or not all(str(timing.get(key) or "") for key in timing)
                or not isinstance(binding.get("procurement_facts"), list)
                or not isinstance(
                    binding.get("missing_procurement_facts"), list)):
            raise ValueError(
                "compose trail horizon decision identities are invalid")
        bindings[identity] = binding
    return bindings


def validate_machine_horizon_contract(
        content, *, trail_md: Optional[str] = None) -> None:
    """Press-time semantic gate for every machine Horizon card."""
    payload = (content.model_dump(mode="python")
               if hasattr(content, "model_dump") else content)
    cards = payload.get("horizon") or []
    trail_contexts = (_horizon_trail_contexts(trail_md)
                      if trail_md is not None else None)
    trail_programs = (_horizon_program_trail_bindings(trail_md)
                      if trail_md is not None else None)
    trail_decisions = (_horizon_decision_trail_bindings(trail_md)
                       if trail_md is not None else None)
    seen: set[str] = set()
    seen_programs: set[str] = set()
    for card in cards:
        validate_machine_horizon_card(card)
        machine = card.get("machine_evidence") or {}
        identity = str(machine.get("source_identity") or "")
        if not identity or identity in seen:
            raise ValueError(
                "horizon card source identities are missing or duplicated")
        seen.add(identity)
        decision_binding = {
            "decision": machine.get("decision"),
            "source_citation": machine.get("source_citation"),
            "timing_basis": machine.get("timing_basis"),
            "procurement_facts": machine.get("procurement_facts"),
            "missing_procurement_facts": machine.get(
                "missing_procurement_facts"),
        }
        if (card.get("decision") != decision_binding["decision"]
                or card.get("source_citation")
                != decision_binding["source_citation"]
                or card.get("timing_basis")
                != decision_binding["timing_basis"]
                or card.get("procurement_facts")
                != decision_binding["procurement_facts"]
                or card.get("missing_procurement_facts")
                != decision_binding["missing_procurement_facts"]
                or (trail_decisions is not None
                    and trail_decisions.get(identity) != decision_binding)):
            raise ValueError(
                f"horizon card {card.get('title')!r} decision evidence "
                "does not match its trail-bound source")
        if machine.get("source_kind") == "program":
            seen_programs.add(identity)
            if (
                trail_programs is not None
                and trail_programs.get(identity) != _program_card_binding(card)
            ):
                raise ValueError(
                    f"horizon card {card.get('title')!r} program source "
                    "does not match its trail-bound adapter, record, and URL")
        if trail_contexts is not None and (
                trail_contexts.get(identity)
                != machine.get("job_context_basis")):
            raise ValueError(
                f"horizon card {card.get('title')!r} job-context basis does "
                "not match its trail-bound source")
    if trail_contexts is not None and set(trail_contexts) != seen:
        raise ValueError(
            "compose trail horizon job-context set does not match the cards")
    if trail_programs is not None and set(trail_programs) != seen_programs:
        raise ValueError(
            "compose trail horizon program-source set does not match the cards")
    if trail_decisions is not None and set(trail_decisions) != seen:
        raise ValueError(
            "compose trail horizon decision set does not match the cards")


_ORG_ABBREVIATIONS = (
    ("NATIONAL TRANSPORTATION SAFETY BOARD", "NTSB"),
    ("BUREAU OF LAND MANAGEMENT", "BLM"),
    ("FEDERAL HIGHWAY ADMINISTRATION", "FHWA"),
    ("OFFICE OF THE COMPTROLLER OF THE CURRENCY", "OCC"),
    ("SOCIAL SECURITY ADMINISTRATION", "SSA"),
    ("SOCIAL SECURITY", "SSA"),
)


def _compact_org(name: str, limit: int = 34) -> str:
    """Authority-preserving organization label for tight public bands.

    Known federal organizations use the shared agency catalog abbreviation;
    the few independent/component names absent from that catalog are pinned
    above. The fallback keeps enough room for a complete name instead of
    cutting at a generic prefix such as ``FEDERAL BUREAU OF``.
    """
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    upper = text.upper()
    for source, abbreviation in _ORG_ABBREVIATIONS:
        if upper == source:
            return abbreviation
    try:
        from tools.agencies import AGENCIES, matches_record
        for agency in AGENCIES:
            if matches_record(text, agency, include_components=False):
                return str(agency["abbr"])
    except (ImportError, KeyError, TypeError):
        pass
    return _short(text, limit)


_COMPANY_CASE = {
    "CENTRALSQUARE": "CentralSquare",
    "OPENFOX": "OpenFox",
    "MARK43": "Mark43",
    "REDSKY": "RedSky",
    "RIVERBED": "Riverbed",
}
_COMPANY_ACRONYMS = {
    "CACI", "CGI", "CTG", "FCN", "GDIT", "IBM", "LLC", "LP",
    "NETSCOUT", "SAIC", "US", "USA",
}


def _display_company(name: str, limit: int = 34) -> str:
    """Readable legal-name casing without downcasing known brands/acronyms."""
    text = _short(name, limit)
    if not text or not text.isupper():
        return text
    words = []
    for index, token in enumerate(text.split()):
        clean = token.strip(".,")
        shown = (
            clean.lower() if index and clean in {"AND", "FOR", "OF", "THE"}
            else _COMPANY_CASE.get(clean)
            or (clean if clean in _COMPANY_ACRONYMS else clean.title())
        )
        words.append(token.replace(clean, shown, 1))
    return " ".join(words)


_CLIENT_RELEVANCE_VERSION = 1
_CLIENT_RELEVANCE_TRAIL_PREFIX = "  - client relevance "
_CLIENT_RELEVANCE_KINDS = frozenset({
    "record-core-match",
    "corridor-core-match",
    "shared-core-route",
})
_CLIENT_RELEVANCE_CONTEXT_KEYS = {
    ("record-core-match", "competitors"):
        frozenset({"band", "buyer", "client"}),
    ("record-core-match", "acquisition_pathways"):
        frozenset({
            "band", "buyer", "client", "evidence_state",
            "client_footprint",
        }),
    ("record-core-match", "horizon"):
        frozenset({"band", "client", "client_footprint", "event_kind"}),
    ("corridor-core-match", "teaming"):
        frozenset({"band", "buyer", "client", "partner"}),
    ("shared-core-route", "teaming"):
        frozenset({"band", "buyer", "client", "partner"}),
}


def _client_relevance_tokens(value: str) -> list[str]:
    """Token stems used only to prove that the bound match names ``term``."""
    from tools.relevance.engine import _stem
    return [_stem(token) for token in re.findall(r"[A-Za-z0-9']+", value)]


def _validate_client_relevance_basis(basis: dict) -> None:
    """Validate the closed, source-bound input to the public projection."""
    if not isinstance(basis, dict):
        raise ValueError("client relevance basis is absent or malformed")
    allowed = {
        "version", "kind", "profile_client", "profile_surface",
        "profile_version", "term", "supports", "public_context",
    }
    required = allowed
    if not required <= set(basis) or set(basis) - allowed:
        raise ValueError("client relevance basis fields are not exact")
    kind = str(basis.get("kind") or "")
    if basis.get("version") != _CLIENT_RELEVANCE_VERSION \
            or kind not in _CLIENT_RELEVANCE_KINDS:
        raise ValueError("client relevance basis version or kind is invalid")
    surface = str(basis.get("profile_surface") or "")
    expected_surface = (
        "profile.capability_terms.core"
        if kind == "shared-core-route"
        else "capability_taxonomy.core"
    )
    version = basis.get("profile_version")
    if (not str(basis.get("profile_client") or "").strip()
            or surface != expected_surface
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1):
        raise ValueError(
            "client relevance profile surface or version is invalid")
    term = str(basis.get("term") or "").strip()
    supports = basis.get("supports")
    if not term or not isinstance(supports, list) or not supports:
        raise ValueError("client relevance CORE term or supports are absent")
    if kind == "shared-core-route" and len(supports) != 2:
        raise ValueError(
            "client relevance shared route requires two exact supports")
    if kind != "shared-core-route" and len(supports) != 1:
        raise ValueError(
            "client relevance record match requires one exact support")
    roles = []
    for support in supports:
        if not isinstance(support, dict) or set(support) != {
                "role", "source_identity", "field", "matched_text",
                "quote"}:
            raise ValueError("client relevance support fields are not exact")
        role = str(support.get("role") or "").strip()
        identity = str(support.get("source_identity") or "").strip()
        field_name = str(support.get("field") or "").strip()
        matched = str(support.get("matched_text") or "").strip()
        quote = str(support.get("quote") or "").strip()
        if not all((role, identity, field_name, matched, quote)):
            raise ValueError("client relevance support is incomplete")
        if matched.casefold() not in quote.casefold():
            raise ValueError(
                "client relevance matched text is absent from its quote")
        if _client_relevance_tokens(term) != \
                _client_relevance_tokens(matched):
            raise ValueError(
                "client relevance term does not match its bound evidence")
        roles.append(role)
    if kind == "shared-core-route" and set(roles) != {
            "selected-play", "subaward-route"}:
        raise ValueError(
            "client relevance shared route support roles are invalid")

    context = basis.get("public_context")
    if not isinstance(context, dict):
        raise ValueError("client relevance public context is malformed")
    band = str(context.get("band") or "")
    expected_keys = _CLIENT_RELEVANCE_CONTEXT_KEYS.get((kind, band))
    if expected_keys is None or set(context) != expected_keys:
        raise ValueError("client relevance public context is not exact")
    for key, value in context.items():
        if key == "client_footprint":
            if not isinstance(value, bool):
                raise ValueError(
                    "client relevance footprint verdict is invalid")
            continue
        text = str(value or "").strip()
        if not text or re.search(
                r"https?://|www\.|\b[\w.+-]+@[\w.-]+\.", text,
                flags=re.IGNORECASE):
            raise ValueError(
                "client relevance public context is unsafe or empty")
    if band == "horizon" and context.get("event_kind") not in \
            _HORIZON_SOURCE_KIND_BY_EVENT:
        raise ValueError(
            "client relevance Horizon event kind is invalid")
    if band == "acquisition_pathways" and context.get(
            "evidence_state") not in {"executed-award", "published-notice"}:
        raise ValueError(
            "client relevance acquisition evidence state is invalid")
    if (band == "acquisition_pathways"
            and context.get("evidence_state") == "published-notice"
            and context.get("client_footprint")):
        raise ValueError(
            "client relevance published notice cannot assert a client "
            "installed-base footprint")


def client_relevance_public(basis: dict) -> dict:
    """Pure public projection of one source-bound CORE relevance basis."""
    _validate_client_relevance_basis(basis)
    kind = str(basis["kind"])
    term = re.sub(r"\s+", " ", str(basis["term"])).strip().upper()
    context = basis["public_context"]
    band = context["band"]
    client = str(context["client"])
    if band == "competitors":
        text = (
            f"DISPLACEMENT TARGET · {context['buyer']} currently funds "
            f"{term}; prioritize this incumbent account for {client} "
            "replacement and follow-on capture."
        )
    elif band == "acquisition_pathways":
        if context["evidence_state"] == "executed-award":
            if context["client_footprint"]:
                footprint = (
                    "federal footprint"
                    if _company_key(term) == _company_key(client)
                    else f"{term} footprint"
                )
                text = (
                    f"DEFEND / EXPAND PATH · {context['buyer']} funds a "
                    f"cited {client} {footprint}; use the award to "
                    "confirm the renewal owner, channel route, and expansion "
                    "scope before the next procurement action."
                )
            else:
                text = (
                    f"ACCOUNT-ENTRY PATH · {context['buyer']} funded "
                    f"{term}; use the cited award to position {client} and "
                    "resolve vehicle, incumbent, and follow-on access before "
                    "the next procurement action."
                )
        else:
            text = (
                f"PUBLISHED DEMAND · {context['buyer']} published a "
                f"requirement matching {term}; use the cited notice to "
                f"position {client} before the stated acquisition action."
            )
    elif band == "teaming":
        if kind == "shared-core-route":
            text = (
                f"PRIME-CHANNEL SIGNAL · {context['partner']} has matched "
                f"opportunity and subaward evidence for {term}; prioritize "
                f"this prime for {client} teaming outreach."
            )
        else:
            text = (
                f"PARTNER DECISION · {context['partner']} holds the cited "
                f"{context['buyer']} {term} award; target that incumbent "
                f"channel for a {client} partner-or-displace decision."
            )
    else:
        event_kind = str(context["event_kind"])
        if event_kind in {"award-window", "calendar-expiry"}:
            if context["client_footprint"]:
                footprint = (
                    "federal footprint"
                    if _company_key(term) == _company_key(client)
                    else f"{term} footprint"
                )
                text = (
                    f"CONTINUITY CHECK · {client}'s cited {footprint} "
                    "reaches period end. That date is not a confirmed "
                    "recompete; confirm option, extension, follow-on, "
                    "replacement, or sunset before assigning capture "
                    "action."
                )
            else:
                text = (
                    f"FOLLOW-ON CHECK · Incumbent {term} work reaches "
                    "period end. That date is not a confirmed recompete; "
                    "confirm option, extension, follow-on, replacement, or "
                    f"sunset before assigning {client} takeout action."
                )
        elif event_kind == "forecast":
            text = (
                f"PRE-SOLICITATION MOVE · The cited forecast matches "
                f"{term}; engage the buyer and place {client} ahead of the "
                "stated acquisition milestone."
            )
        elif event_kind == "program":
            text = (
                f"PROGRAM MOVE · The cited official program signal matches "
                f"{term}; position {client} with the sponsoring organization "
                "before the stated program or funding action."
            )
        else:
            text = (
                f"COMPLETION SIGNAL · Cited {term} work approaches contract "
                "completion; validate options, extension, and the follow-on "
                f"acquisition path before assigning {client} capture action."
            )
    return {"kind": kind, "text": text}


def _verdict_client_relevance_basis(
        verdict: RelevanceVerdict, *, kind: str, source_identity: str,
        public_context: dict) -> dict:
    """Capture the exact first sanctioned CORE span used by one card."""
    span = next((item for item in verdict.spans if item.tier == "core"), None)
    if not verdict.relevant or span is None:
        raise ValueError(
            "client relevance cannot be composed without a CORE span")
    basis = {
        "version": _CLIENT_RELEVANCE_VERSION,
        "kind": kind,
        "profile_client": verdict.taxonomy_client,
        "profile_surface": "capability_taxonomy.core",
        "profile_version": verdict.taxonomy_version,
        "term": span.term,
        "supports": [{
            "role": "record",
            "source_identity": source_identity,
            "field": span.field,
            "matched_text": span.matched_text,
            "quote": span.context,
        }],
        "public_context": public_context,
    }
    _validate_client_relevance_basis(basis)
    return basis


def _profile_term_support(
        term: str, *, role: str, source_identity: str,
        fields: list[tuple[str, str]]) -> dict:
    """Exact support for the Assess/C1 shared profile-core predicate."""
    from tools.capability import term_regex
    for field_name, source_text in fields:
        match = term_regex(term).search(source_text)
        if match is None:
            continue
        lo = max(0, match.start() - 60)
        hi = min(len(source_text), match.end() + 60)
        return {
            "role": role,
            "source_identity": source_identity,
            "field": field_name,
            "matched_text": match.group(0),
            "quote": source_text[lo:hi],
        }
    raise ValueError(
        f"client relevance {role} has no exact shared CORE support")


def _shared_route_client_relevance_basis(
        pick: TeamingPick, *, profile, taxonomy, client_label: str) -> dict:
    edge = pick.edges[0] if pick.edges else {}
    shared = {str(term).casefold() for term in
              (edge.get("_shared_core_terms") or [])}
    terms = [term for term in profile.capability_terms.core
             if str(term).casefold() in shared]
    if not terms:
        raise ValueError(
            "client relevance subaward route lacks a shared CORE term")
    term = sorted(map(str, terms), key=str.casefold)[0]
    raw = (pick.play.get("raw_payload")
           if isinstance(pick.play.get("raw_payload"), dict) else {})
    play_identity = str(edge.get("_selected_play_identity") or "")
    subaward_identity = str(edge.get("subaward_id") or "")
    basis = {
        "version": _CLIENT_RELEVANCE_VERSION,
        "kind": "shared-core-route",
        "profile_client": str(taxonomy.client_name or ""),
        "profile_surface": "profile.capability_terms.core",
        "profile_version": int(taxonomy.version),
        "term": term,
        "supports": [
            _profile_term_support(
                term, role="selected-play", source_identity=play_identity,
                fields=[
                    ("title", str(pick.play.get("title") or "")),
                    ("raw_payload.description_snippet",
                     str(raw.get("description_snippet") or "")),
                ]),
            _profile_term_support(
                term, role="subaward-route",
                source_identity=subaward_identity,
                fields=[("description", str(edge.get("description") or ""))]),
        ],
        "public_context": {
            "band": "teaming",
            "buyer": _compact_org(str(edge.get("awarding_agency") or "")),
            "client": client_label,
            "partner": _display_company(pick.partner, 34),
        },
    }
    _validate_client_relevance_basis(basis)
    return basis


_CLIENT_RELEVANCE_ID_KEYS = frozenset({
    "award_id", "generated_id", "generated_internal_id", "id", "notice_id",
    "notice_guid", "piid", "prime_award_id", "record_id", "source_id",
    "subaward_id",
})


def _client_relevance_source_rows(
        sweep: dict, source_identity: str) -> list[dict]:
    """Stored rows carrying one exact structured identity, de-duplicated."""
    program_parts = _program_identity_parts(source_identity)
    if program_parts is not None:
        from agents.reports.horizon_discovery._stored_program import (
            stored_program_records,
        )

        source, record_id = program_parts
        program_rows: dict[str, dict] = {}

        def collect_program(node) -> None:
            if isinstance(node, dict):
                payload = node.get(source)
                if isinstance(payload, dict):
                    for row in stored_program_records(payload, source=source):
                        if str(row.get("record_id") or "") == record_id:
                            canonical = json.dumps(
                                row, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"))
                            program_rows[canonical] = row
                for value in node.values():
                    collect_program(value)
            elif isinstance(node, list):
                for value in node:
                    collect_program(value)

        collect_program(sweep)
        return list(program_rows.values())

    rows: dict[str, dict] = {}

    def collect(node) -> None:
        if isinstance(node, dict):
            identities = {
                str(value) for key, value in node.items()
                if key in _CLIENT_RELEVANCE_ID_KEYS
                and isinstance(value, (str, int)) and str(value)}
            completion_identity = ""
            if node.get("awardee") and node.get("completion"):
                completion_identity = (
                    f"{node['awardee']}·{_iso_day(node['completion'])}")
            if (source_identity in identities
                    or source_identity == completion_identity):
                canonical = json.dumps(
                    node, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"))
                rows[canonical] = node
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(sweep)
    return list(rows.values())


def _client_relevance_row_texts(row: dict) -> list[tuple[str, str]]:
    """The exact text surfaces sanctioned by opportunity relevance."""
    from tools.relevance.engine import record_text_fields

    fields = record_text_fields(row)
    for list_field in (
            "products", "terms", "matched_products", "matched_terms"):
        fields.extend(
            (list_field, str(item))
            for item in (row.get(list_field) or [])
            if isinstance(item, str) and item.strip())
    return fields


def _client_relevance_support_is_stored(
        support: dict, *, term: str, kind: str, taxonomy,
        sweep: dict) -> bool:
    """Reproduce one support from at least one current stored projection.

    C3 composes before the sanctioned award re-pull.  The re-pull can append
    a second, richer projection of the same award and exact support.  That is
    corroboration, not an ambiguous claim: the original field, matched text,
    quote, and structured identity must still match exactly, while any number
    of identical current projections may carry it.
    """
    from tools.capability import term_regex
    from tools.relevance.engine import score_text

    field = str(support["field"])
    matched = str(support["matched_text"])
    quote = str(support["quote"])
    matching_rows = 0
    for row in _client_relevance_source_rows(
            sweep, str(support["source_identity"])):
        row_matches = False
        for source_field, source_text in _client_relevance_row_texts(row):
            if source_field != field:
                continue
            if kind == "shared-core-route":
                spans = [match.span() for match in term_regex(term).finditer(
                    source_text)]
            else:
                scored, _killed = score_text(
                    source_text, taxonomy, field=source_field)
                spans = [
                    (span.start, span.start + len(span.matched_text))
                    for span in scored
                    if span.tier == "core"
                    and span.term.casefold() == term.casefold()
                ]
            for start, end in spans:
                if source_text[start:end] != matched:
                    continue
                expected_quote = source_text[
                    max(0, start - 60):min(len(source_text), end + 60)]
                if expected_quote == quote:
                    row_matches = True
                    break
            if row_matches:
                break
        matching_rows += int(row_matches)
    return matching_rows >= 1


def _validate_client_relevance_current_sources(
        basis: dict, *, taxonomy, profile, sweep: dict,
        calendar: Optional[dict] = None) -> None:
    """Bind a basis to today's exact client vocabulary and stored rows."""
    from tools.capability import _slug as canonical_client

    taxonomy_client = str(getattr(taxonomy, "client_name", "") or "")
    profile_client = str(getattr(profile, "client_name", "") or "")
    basis_client = str(basis.get("profile_client") or "")
    if not taxonomy_client or not profile_client or not basis_client or {
            canonical_client(taxonomy_client),
            canonical_client(profile_client),
            canonical_client(basis_client),
    } != {canonical_client(taxonomy_client)}:
        raise ValueError(
            "client relevance basis is not bound to the current client")
    if basis.get("profile_version") != getattr(taxonomy, "version", None):
        raise ValueError(
            "client relevance basis uses a stale taxonomy version")

    kind = str(basis["kind"])
    term = str(basis["term"])
    if kind == "shared-core-route":
        capability_terms = getattr(profile, "capability_terms", None)
        current_terms = list(getattr(capability_terms, "core", []) or [])
    else:
        current_terms = [
            str(getattr(row, "term", "") or "")
            for row in (getattr(taxonomy, "core", []) or [])]
    if term.casefold() not in {
            str(current).casefold() for current in current_terms}:
        raise ValueError(
            "client relevance term is not in the current client CORE set")
    for support in basis["supports"]:
        if not _client_relevance_support_is_stored(
            support, term=term, kind=kind, taxonomy=taxonomy,
                sweep={"sweep": sweep, "calendar": calendar or {}}):
            raise ValueError(
                "client relevance support does not match its current "
                "stored source record")


def _client_relevance_buyer_records(
        sweep: dict, source_identity: str) -> list[tuple[str, str, dict]]:
    """Buyer-map record plus its parent account for one canonical GID."""
    buyer_map = (sweep.get("results") or {}).get(
        "incumbent_buyer_map") or {}
    matches: dict[str, tuple[str, str, dict]] = {}
    for buyer in buyer_map.get("buyers") or []:
        for record in buyer.get("records") or []:
            identities = {
                str(record.get("generated_internal_id") or ""),
                str(record.get("generated_id") or ""),
            }
            if source_identity in identities:
                buyer_name = str(buyer.get("buyer") or "")
                agency = str(buyer.get("agency") or "")
                key = json.dumps(
                    [buyer_name, agency, record], ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"))
                matches[key] = (buyer_name, agency, record)
    return list(matches.values())


def _validate_client_relevance_card_binding(card: dict, basis: dict) -> None:
    """Bind public sentence inputs to the card's other structured fields."""
    from tools.capability import _slug as canonical_client

    machine = card["machine_evidence"]
    context = basis["public_context"]
    band = str(context["band"])
    if band == "competitors":
        label_buyer = str(card.get("label") or "").split("·", 1)[0].strip()
        if (machine.get("source_kind") != "usaspending-award"
                or label_buyer.casefold()
                != str(context["buyer"]).casefold()):
            raise ValueError(
                "client relevance account does not match its card")
        return

    if band == "acquisition_pathways":
        label_buyer = str(card.get("label") or "").split("·", 1)[0].strip()
        expected_state = {
            "usaspending-award": "executed-award",
            "sam.gov": "published-notice",
        }.get(str(machine.get("source_kind") or ""))
        if (expected_state is None
                or context.get("evidence_state") != expected_state
                or not isinstance(context.get("client_footprint"), bool)
                or label_buyer.casefold()
                != str(context["buyer"]).casefold()):
            raise ValueError(
                "client relevance acquisition account does not match its "
                "card")
        return

    if band == "teaming":
        route_basis = machine.get("route_basis")
        if not isinstance(route_basis, dict):
            raise ValueError(
                "client relevance teaming route basis is absent")
        route_public = _teaming_public_fields(route_basis)
        if machine.get("source_identity") != route_public["source_identity"]:
            raise ValueError(
                "client relevance teaming identity does not match its route")
        partner = str(route_basis.get("partner") or "").strip()
        public_partner = _display_company(partner, 34)
        expected_buyer = _compact_org(str(
            route_basis.get("agency")
            if route_basis.get("kind") == "subaward-evidence"
            else route_basis.get("buyer") or ""))
        if (not partner
                or _company_key(str(context["partner"]))
                != _company_key(public_partner)
                or _company_key(str(card.get("partner") or ""))
                != _company_key(public_partner)
                or str(context["buyer"]).casefold()
                != expected_buyer.casefold()
                or canonical_client(str(card.get("client") or ""))
                != canonical_client(str(context["client"]))):
            raise ValueError(
                "client relevance teaming context does not match its route")
        supports = {row["role"]: row for row in basis["supports"]}
        if basis["kind"] == "shared-core-route":
            shared = {
                str(term).casefold()
                for term in (route_basis.get("shared_core_terms") or [])}
            if (route_basis.get("kind") != "subaward-evidence"
                    or supports["selected-play"]["source_identity"]
                    != route_basis.get("selected_play_identity")
                    or supports["subaward-route"]["source_identity"]
                    != route_basis.get("subaward_id")
                    or str(basis["term"]).casefold() not in shared):
                raise ValueError(
                    "client relevance shared route does not match its card")
        elif route_basis.get("kind") != "entry-thesis":
            raise ValueError(
                "client relevance entry route does not match its card")
        return

    job_basis = machine.get("job_context_basis")
    event_kind = str(context["event_kind"])
    if (not isinstance(job_basis, dict)
            or job_basis.get("event_kind") != event_kind
            or machine.get("source_kind")
            != _HORIZON_SOURCE_KIND_BY_EVENT[event_kind]):
        raise ValueError(
            "client relevance Horizon event does not match its card")
    client_mark = any(
        isinstance(mark, dict) and mark.get("kind") == "client"
        for mark in (card.get("organization_marks") or []))
    expected_footprint = (
        client_mark if event_kind in {
            "award-window", "calendar-expiry", "expiring",
            "usaspending-expiring",
        } else False
    )
    if context["client_footprint"] is not expected_footprint:
        raise ValueError(
            "client relevance Horizon footprint does not match its card")


def _client_relevance_horizon_rows(
        sweep: dict, calendar: Optional[dict], *, event_kind: str,
        source_identity: str, source_adapter: str = "") -> list[dict]:
    """Resolve one Horizon record from the collection its kind sanctions."""
    results = sweep.get("results") or {}
    if event_kind == "forecast":
        candidates = (results.get("forecast_signals") or {}).get(
            "matched") or []
        matches = [row for row in candidates
                   if str(row.get("source_id") or "") == source_identity]
    elif event_kind == "calendar-expiry":
        candidates = (
            list((calendar or {}).get("attack") or [])
            + list((calendar or {}).get("defend") or [])
        )
        matches = [row for row in candidates
                   if str(row.get("award_id") or "") == source_identity]
    elif event_kind == "program":
        from agents.reports.horizon_discovery._stored_program import (
            stored_program_records,
        )

        parts = _program_identity_parts(source_identity)
        if parts is None or parts[0] != source_adapter:
            matches = []
        else:
            source, record_id = parts
            matches = [
                row for row in stored_program_records(
                    results.get(source) or {}, source=source)
                if str(row.get("record_id") or "") == record_id
            ]
    elif event_kind == "expiring":
        candidates = (results.get("contract_awards") or {}).get(
            "recompetes") or []
        matches = [row for row in candidates if (
            str(row.get("piid") or "")
            or f"{row.get('awardee') or 'unknown'}·"
               f"{_event_day(row.get('completion'))}") == source_identity]
    elif event_kind == "usaspending-expiring":
        candidates = (results.get("expiring_awards") or {}).get("rows") or []
        matches = [row for row in candidates if (
            str(row.get("generated_internal_id") or "")
            or str(row.get("award_id") or "")) == source_identity]
    else:
        matches = [record for _buyer, _agency, record in
                   _client_relevance_buyer_records(sweep, source_identity)]
    unique = {
        json.dumps(row, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")): row
        for row in matches}
    return list(unique.values())


def _validate_client_relevance_sweep_context(
        card: dict, basis: dict, *, content_client: str,
        taxonomy, sweep: dict, calendar: Optional[dict] = None) -> None:
    """Bind account and footprint semantics to current buyer-map records."""
    context = basis["public_context"]
    band = str(context["band"])
    machine = card["machine_evidence"]
    if band == "competitors":
        candidates = _client_relevance_buyer_records(
            sweep, str(machine["source_identity"]))
        valid = [record for buyer, agency, record in candidates if (
                str(context["buyer"]).casefold()
                == _compact_org(buyer).casefold()
                and _company_key(str(card.get("title") or ""))
                == _company_key(_display_company(
                    str(record.get("recipient") or ""), 34))
                and str(machine.get("agency") or "").casefold()
                == agency.casefold()
                and str(machine.get("quote") or "")
                == str(record.get("description") or ""))]
        if len(candidates) != 1 or len(valid) != 1:
            raise ValueError(
                "client relevance competitor context is not in the "
                "current stored buyer map")
    elif band == "acquisition_pathways":
        pathway_basis = machine.get("pathway_basis") or {}
        if machine.get("source_kind") == "sam.gov":
            candidates = [
                record for record in (sweep.get("results") or {}).get(
                    "sam.gov") or []
                if notice_guid(record)
                == str(machine.get("source_identity") or "")
            ]
            valid = [record for record in candidates if (
                str(context["buyer"]).casefold()
                == _compact_org(str(record.get("agency") or "")).casefold()
                and str(machine.get("quote") or "")
                == _acquisition_requirement_excerpt(
                    record, str(pathway_basis.get("capability") or ""))
                and pathway_basis.get("requirement_excerpt")
                == _acquisition_requirement_excerpt(
                    record, str(pathway_basis.get("capability") or ""))
                and pathway_basis.get("route_key")
                == _sam_acquisition_route_key(record)
                and not pathway_basis.get("source_company_key"))]
        else:
            candidates = _client_relevance_buyer_records(
                sweep, str(machine["source_identity"]))
            valid = [record for buyer, agency, record in candidates if (
                    str(context["buyer"]).casefold()
                    == _compact_org(buyer).casefold()
                    and str(machine.get("agency") or "").casefold()
                    == agency.casefold()
                    and str(machine.get("quote") or "")
                    == _acquisition_requirement_excerpt(
                        record, str(pathway_basis.get("capability") or ""))
                    and pathway_basis.get("requirement_excerpt")
                    == _acquisition_requirement_excerpt(
                        record, str(pathway_basis.get("capability") or ""))
                    and pathway_basis.get("route_key")
                    == f"award:{machine.get('source_identity')}"
                    and pathway_basis.get("source_company_key")
                    == _company_key(str(record.get("recipient") or ""))
                    and pathway_basis.get("client_footprint") is
                    _is_client_footprint(
                        str(record.get("recipient") or ""), record,
                        content_client, taxonomy=taxonomy)
                    and context.get("client_footprint") is
                    pathway_basis.get("client_footprint"))]
        if len(candidates) != 1 or len(valid) != 1:
            raise ValueError(
                "client relevance acquisition pathway is not in the "
                "current stored buyer map")
    elif band == "teaming" and basis["kind"] == "corridor-core-match":
        route_basis = machine["route_basis"]
        candidates = _client_relevance_buyer_records(
            sweep, str(route_basis.get("award_generated_id") or ""))
        valid = [record for buyer, _agency, record in candidates if (
                str(context["buyer"]).casefold()
                == _compact_org(buyer).casefold()
                and _company_key(str(context["partner"]))
                == _company_key(_display_company(
                    str(record.get("recipient") or ""), 34))
                and _company_key(str(route_basis.get("partner") or ""))
                == _company_key(str(record.get("recipient") or ""))
                and str(machine.get("quote") or "")
                == str(record.get("description") or ""))]
        if len(candidates) != 1 or len(valid) != 1:
            raise ValueError(
                "client relevance teaming context is not in the current "
                "stored buyer map")
    elif band == "teaming" and basis["kind"] == "shared-core-route":
        route_basis = machine["route_basis"]
        subaward_id = str(route_basis.get("subaward_id") or "")
        subaward_rows = [
            row for row in _client_relevance_source_rows(sweep, subaward_id)
            if str(row.get("subaward_id") or "") == subaward_id]
        if len(subaward_rows) != 1:
            raise ValueError(
                "client relevance shared route has no unique current "
                "subaward record")
        edge = subaward_rows[0]
        prime_gid = str(route_basis.get("award_generated_id") or "")
        prime_rows = _client_relevance_buyer_records(sweep, prime_gid)
        explicit_gid = str(
            edge.get("prime_award_generated_id")
            or edge.get("prime_generated_internal_id") or "")
        if (len(prime_rows) != 1
                or _company_key(str(route_basis.get("partner") or ""))
                != _company_key(str(edge.get("prime") or ""))
                or str(route_basis.get("agency") or "").casefold()
                != str(edge.get("awarding_agency") or "").casefold()
                or str(route_basis.get("prime_award_id") or "")
                != str(edge.get("prime_award_id") or "")
                or (explicit_gid and explicit_gid != prime_gid)
                or str(machine.get("quote") or "")
                != str(edge.get("description") or "")):
            raise ValueError(
                "client relevance shared route does not match its current "
                "subaward source")
        _buyer, _agency, prime_record = prime_rows[0]
        if (str(prime_record.get("award_id") or "")
                != str(route_basis.get("prime_award_id") or "")
                or _company_key(str(prime_record.get("recipient") or ""))
                != _company_key(str(edge.get("prime") or ""))):
            raise ValueError(
                "client relevance shared route prime does not match its "
                "current canonical award")
    elif band == "horizon":
        event_kind = str(context["event_kind"])
        rows = _client_relevance_horizon_rows(
            sweep, calendar, event_kind=event_kind,
            source_identity=str(machine["source_identity"]),
            source_adapter=str(machine.get("source_adapter") or ""))
        if len(rows) != 1:
            raise ValueError(
                "client relevance Horizon identity is absent or ambiguous "
                "in its current source collection")
        current_row = dict(rows[0])
        if event_kind == "program":
            current_row["_horizon_program_source"] = str(
                machine.get("source_adapter") or "")
            generated_day = _event_day(
                str(sweep.get("generated_at") or "")[:10])
            if not generated_day:
                raise ValueError(
                    "client relevance PROGRAM sweep date is absent")
            compose_day = date.fromisoformat(generated_day)
            timing = _program_horizon_timing(
                str(machine.get("source_adapter") or ""),
                current_row,
                today=compose_day,
                ceiling=add_months(
                    compose_day, HORIZON_MONTHS).isoformat(),
            )
            if (
                timing is None
                or card.get("program_record_id")
                != current_row.get("record_id")
                or card.get("url") != current_row.get("canonical_url")
                or machine.get("canonical_url")
                != current_row.get("canonical_url")
                or machine.get("timing_value")
                != _program_public_timing_value(timing[1])
                or machine.get("timing_label") != timing[2]
            ):
                raise ValueError(
                    "client relevance PROGRAM adapter, record, or canonical "
                    "URL does not match its current stored source")
        expected_job = _horizon_job_context_basis(HorizonPick(
            kind=event_kind, event_date="",
            ident=str(machine["source_identity"]), row=current_row))
        if machine.get("job_context_basis") != expected_job:
            raise ValueError(
                "client relevance Horizon event does not match its current "
                "stored source record")
        expected = False
        expected_decision = "QUALIFY"
        if event_kind == "award-window":
            expected = _is_client_footprint(
                str(rows[0].get("recipient") or ""), rows[0],
                content_client, taxonomy=taxonomy)
            expected_decision = "DEFEND" if expected else "ATTACK"
        elif event_kind == "calendar-expiry":
            identity = str(machine["source_identity"])
            expected = any(
                str(row.get("award_id") or "") == identity
                for row in (calendar or {}).get("defend") or [])
            expected_decision = "DEFEND" if expected else "ATTACK"
        elif event_kind == "expiring":
            expected = _is_client_company(
                str(rows[0].get("awardee") or ""), content_client)
            expected_decision = "DEFEND" if expected else "ATTACK"
        elif event_kind == "usaspending-expiring":
            expected = _is_client_company(
                str(rows[0].get("recipient") or ""), content_client)
            expected_decision = "DEFEND" if expected else "ATTACK"
        if (context["client_footprint"] is not expected
                or machine.get("decision") != expected_decision):
            raise ValueError(
                "client relevance Horizon posture or footprint is not "
                "supported by the current stored source")


def validate_machine_client_relevance_card(card: dict) -> dict:
    """Reproject and verify one machine card's public relevance line."""
    machine = card.get("machine_evidence")
    if not isinstance(machine, dict):
        raise ValueError("client relevance card lacks machine evidence")
    basis = machine.get("client_relevance_basis")
    expected = client_relevance_public(basis)
    if card.get("client_relevance") != expected:
        raise ValueError(
            "client relevance public copy does not match its source basis")
    if basis["kind"] != "shared-core-route":
        support_identity = str(basis["supports"][0]["source_identity"])
        if support_identity != str(machine.get("source_identity") or ""):
            raise ValueError(
                "client relevance support identity does not match its card")
    _validate_client_relevance_card_binding(card, basis)
    return expected


def _client_relevance_trail_rows(trail_md: str) -> dict[tuple[str, str], dict]:
    """Parse canonical relevance envelopes from the independent trail."""
    rows: dict[tuple[str, str], dict] = {}
    for line in trail_md.splitlines():
        if not line.startswith(_CLIENT_RELEVANCE_TRAIL_PREFIX):
            continue
        raw = line[len(_CLIENT_RELEVANCE_TRAIL_PREFIX):]
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "client relevance compose-trail envelope is malformed") \
                from exc
        if (not isinstance(envelope, dict)
                or set(envelope) != {"band", "basis", "card_identity"}
                or envelope.get("band") not in {
                    "competitors", "acquisition_pathways", "teaming",
                    "horizon"}
                or not isinstance(envelope.get("basis"), dict)):
            raise ValueError(
                "client relevance compose-trail envelope is not exact")
        key = (str(envelope["band"]),
               str(envelope.get("card_identity") or ""))
        if not key[1] or key in rows:
            raise ValueError(
                "client relevance compose-trail identities are invalid")
        client_relevance_public(envelope["basis"])
        rows[key] = envelope["basis"]
    return rows


def validate_machine_client_relevance_contract(
        content, *, trail_md: Optional[str] = None, taxonomy=None,
        profile=None, sweep: Optional[dict] = None,
        calendar: Optional[dict] = None) -> None:
    """Require relevance context on all three machine-produced card bands."""
    payload = (content.model_dump(mode="python")
               if hasattr(content, "model_dump") else content)
    if str(payload.get("composition_mode") or "operator") != "machine":
        return
    expected: dict[tuple[str, str], dict] = {}
    allowed_kinds = {
        "competitors": {"record-core-match"},
        "acquisition_pathways": {"record-core-match"},
        "teaming": {"corridor-core-match", "shared-core-route"},
        "horizon": {"record-core-match"},
    }
    from tools.capability import _slug as canonical_client
    content_client = str(payload.get("client_name") or "")
    current_inputs = (taxonomy, profile, sweep)
    if any(value is not None for value in current_inputs) and not all(
            value is not None for value in current_inputs):
        raise ValueError(
            "client relevance current-source validation inputs are partial")
    for band in (
            "competitors", "acquisition_pathways", "teaming", "horizon"):
        for card in payload.get(band) or []:
            validate_machine_client_relevance_card(card)
            machine = card.get("machine_evidence") or {}
            identity = str(machine.get("source_identity") or "")
            key = (band, identity)
            basis = machine.get("client_relevance_basis") or {}
            if (not identity or key in expected
                    or basis.get("kind") not in allowed_kinds[band]):
                raise ValueError(
                    "client relevance card identity or band kind is invalid")
            if canonical_client(str(basis.get("profile_client") or "")) != \
                    canonical_client(content_client):
                raise ValueError(
                    "client relevance profile belongs to another client")
            context = basis["public_context"]
            if context.get("band") != band:
                raise ValueError(
                    "client relevance public context names the wrong band")
            if canonical_client(
                    str(context.get("client") or "")) != canonical_client(
                        content_client):
                raise ValueError(
                    "client relevance public context names another client")
            if taxonomy is not None:
                _validate_client_relevance_current_sources(
                    basis, taxonomy=taxonomy, profile=profile, sweep=sweep,
                    calendar=calendar)
                _validate_client_relevance_sweep_context(
                    card, basis, content_client=content_client,
                    taxonomy=taxonomy, sweep=sweep, calendar=calendar)
            expected[key] = basis
    if trail_md is None:
        return
    actual = _client_relevance_trail_rows(trail_md)
    if set(actual) != set(expected):
        raise ValueError(
            "client relevance compose-trail set does not match the cards")
    for key, basis in expected.items():
        if actual[key] != basis:
            raise ValueError(
                "client relevance basis does not match its trail-bound source")


def _source_excerpt_count(count: int) -> str:
    return f"{count} source excerpt{'s' if count != 1 else ''}"


def compose_line(kind: str, ident: str, context: dict, template: str, *,
                 client: str, offline: bool, trail: list,
                 max_len: int = 90) -> str:
    """One prose line through the cache + lint battery: LLM when the Max
    CLI is present and not offline, one regenerate on lint failure, then
    the deterministic template. max_len is the STORED field's limit: the
    line is validated at its final length and never sliced afterward.
    The template is itself linted; a template that fails is a composer
    defect surfaced loudly. Context must carry quoted evidence."""
    from agents.reports.capture_brief import normalize_house_style
    from agents.reports.lint import lint_text

    stop_words = {"the", "and", "for", "with", "from", "that", "this",
                  "into", "over", "under", "offers", "provides", "route",
                  "lane", "entry", "thesis"}
    evidence_blob = " ".join(
        str(v) for v in (context.get("quoted_evidence") or [])
        + [str(x) for x in context.values() if isinstance(x, str)]
        + [str(t) for v in context.values() if isinstance(v, list)
           for t in v]).lower()

    def grounded(line: str) -> bool:
        """Deterministic evidence grounding: every substantive token of
        the line must appear in the quoted evidence context. Prompt-only
        grounding is insufficient; this check is the validator."""
        tokens = [t for t in re.findall(r"[a-z]{4,}", line.lower())
                  if t not in stop_words]
        return all(t in evidence_blob for t in tokens)

    def viable(line: str) -> bool:
        line = line.strip()
        return (bool(line) and len(line) <= max_len and lint_text(line).ok
                and grounded(line))

    template = normalize_house_style(template.strip())
    if not (template and len(template) <= max_len
            and lint_text(template).ok):
        raise RuntimeError(
            f"composer template line fails lint for {kind}:{ident}: {template!r}")

    source = "template"
    line = template
    if not offline:
        from agents.decisions import maxplan_cli
        if maxplan_cli.cli_available():
            from agents.reports.compose_cache import (
                cached_call, canonical_digest, section_key)
            key = section_key(client=client, scope="board",
                              section=f"line.{kind}.{ident}",
                              inputs_digest=canonical_digest(context))

            def producer() -> dict:
                prompt = (
                    "Write ONE line for a federal sales board card. "
                    f"Kind: {kind}. HARD MAX {max_len} characters; shorter "
                    "is better. Plain declarative phrase, no em dashes, no "
                    "hype words, no first person. Ground the line ONLY in "
                    f"this quoted record evidence: {context}. "
                    "Return only the line.")
                first = normalize_house_style(
                    maxplan_cli.run_claude(prompt, model="opus").strip())
                if viable(first):
                    return {"line": first, "source": "llm"}
                retry = normalize_house_style(maxplan_cli.run_claude(
                    prompt + " The previous attempt failed the style lint;"
                    " produce a plainer line.", model="opus").strip())
                if viable(retry):
                    return {"line": retry, "source": "llm-retry"}
                return {"line": template, "source": "template-fallback"}

            def validator(entry: dict) -> dict:
                if not isinstance(entry, dict) or "line" not in entry:
                    raise ValueError("malformed cached compose line")
                if not viable(str(entry["line"])):
                    raise ValueError("cached compose line fails current lint")
                return entry

            try:
                got, _hit = cached_call(key=key, validator=validator,
                                        producer=producer)
                line, source = str(got["line"]), str(got["source"])
            except Exception as exc:  # noqa: BLE001 - seam failure = template
                line, source = template, f"template-fallback:{exc}"[:120]
    trail.append({"kind": kind, "ident": ident, "source": source,
                  "line": line})
    return line


_ACQUISITION_REQUIREMENT_LIMIT = 300


def _acquisition_requirement_excerpt(
        record: dict, capability: str = "") -> str:
    """Bounded source text centered on the matched client capability."""

    raw = record.get("raw_payload") \
        if isinstance(record.get("raw_payload"), dict) else {}
    title = _first_stated(record.get("title"))
    description = _first_stated(
        record.get("description"), raw.get("description_snippet"))

    def bounded(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        cut = text[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut.rstrip(" ,.;:") + "…"

    term = _first_stated(capability)
    context = ""
    if description and term:
        index = description.casefold().find(term.casefold())
        if index >= 0:
            start = max(0, index - 60)
            end = min(len(description), index + len(term) + 100)
            if start:
                next_space = description.find(" ", start)
                if next_space >= 0 and next_space < index:
                    start = next_space + 1
            context = description[start:end].strip(" ,.;:")
            if start:
                context = "…" + context
            if end < len(description):
                context += "…"
    if title and context:
        clean_title = title.rstrip(" .;:-")
        text = f"{bounded(clean_title, 150)}: {bounded(context, 140)}"
    else:
        text = title or context or description
    return bounded(text, _ACQUISITION_REQUIREMENT_LIMIT)


def _acquisition_pathway_public_fields(basis: dict) -> dict:
    """Pure public projection of one acquisition-research record."""

    required = {
        "version", "agency", "buyer", "capability", "source_kind",
        "source_identity", "award_id", "notice_guid",
        "route_key", "source_company_key", "requirement_excerpt",
        "client_footprint", "procurement_facts",
        "missing_procurement_facts",
    }
    if not isinstance(basis, dict) or set(basis) != required \
            or basis.get("version") != 1:
        raise ValueError("acquisition pathway basis fields are not exact")
    source_kind = str(basis.get("source_kind") or "")
    if source_kind not in {"usaspending-award", "sam.gov"}:
        raise ValueError("acquisition pathway source kind is not sanctioned")
    identity = str(basis.get("source_identity") or "").strip()
    buyer = str(basis.get("buyer") or "").strip()
    capability = str(basis.get("capability") or "").strip()
    route_key = str(basis.get("route_key") or "").strip()
    source_company_key = str(
        basis.get("source_company_key") or "").strip()
    client_footprint = basis.get("client_footprint")
    requirement_excerpt = str(
        basis.get("requirement_excerpt") or "").strip()
    facts = basis.get("procurement_facts")
    missing = basis.get("missing_procurement_facts")
    if (not identity or not buyer or not capability or not route_key
            or not requirement_excerpt
            or not isinstance(client_footprint, bool)
            or not isinstance(facts, list) or not isinstance(missing, list)):
        raise ValueError("acquisition pathway basis is incomplete")
    award_id = str(basis.get("award_id") or "").strip()
    notice_id = str(basis.get("notice_guid") or "").strip()
    if ((source_kind == "usaspending-award"
         and (not award_id or notice_id
              or route_key != f"award:{identity}"
              or not source_company_key))
            or (source_kind == "sam.gov"
                and (not notice_id or award_id or notice_id != identity
                     or not route_key.startswith("sam-")
                     or source_company_key or client_footprint))):
        raise ValueError("acquisition pathway identity family is inconsistent")
    facts_by_label = {
        str(fact.get("label") or ""): str(fact.get("value") or "")
        for fact in facts if isinstance(fact, dict)
    }
    identity_label = (
        "Award" if source_kind == "usaspending-award" else "Solicitation")
    pathway_labels = (
        "Acquisition method", "Notice type", identity_label,
        "Award structure", "Parent IDV type", "Parent IDV", "Set-aside",
        "Competition", "NAICS", "PSC",
    )
    # NAICS and PSC classify the requirement; they do not explain how an
    # account bought or plans to buy.  Section 03 must carry at least one
    # method, vehicle, structure, access, or competition fact before it may
    # call the record an acquisition pathway.
    substantive_labels = {
        "Acquisition method", "Award structure", "Parent IDV type",
        "Parent IDV", "Set-aside", "Competition",
    }
    if source_kind == "sam.gov":
        substantive_labels.add("Notice type")
    if not any(facts_by_label.get(label) for label in substantive_labels):
        raise ValueError(
            "acquisition pathway record states no substantive buying facts")
    pathway = " · ".join(
        f"{label.upper()} {facts_by_label[label]}"
        for label in pathway_labels if facts_by_label.get(label)
    )
    if not pathway:
        raise ValueError("acquisition pathway record states no buying facts")
    access_labels = (
        "Set-aside", "Competition", "Parent IDV type",
    )
    access = " · ".join(
        f"{label.upper()} {facts_by_label[label]}"
        for label in access_labels if facts_by_label.get(label)
    ) or "ACCESS CONDITIONS NOT STATED IN CITED RECORD"
    validation_names = {
        "Set-aside": "set-aside eligibility",
        "Competition": "competition method",
        "Place": "place-of-performance conditions",
        "Base + all options": "option ceiling",
        "Award structure": "vehicle or award structure",
        "Parent IDV": "vehicle access",
        "Parent IDV type": "vehicle type",
        "Co-holders": "eligible holders",
        "NAICS": "NAICS route",
        "PSC": "PSC route",
        "Acquisition method": "acquisition method",
        "Notice type": "notice type",
    }
    unresolved = []
    for label in missing:
        item = validation_names.get(str(label), str(label).casefold())
        if item and item not in unresolved:
            unresolved.append(item)
    if client_footprint:
        capture_questions = [
            "renewal or follow-on owner", "reseller/channel route",
            "expansion requirement",
        ]
        action_prefix = "DEFEND / EXPAND NEXT"
        action_suffix = " before assigning the renewal capture action."
    else:
        capture_questions = [
            "follow-on owner", "next acquisition milestone",
        ]
        action_prefix = "ENTER ACCOUNT NEXT"
        action_suffix = (
            " before assigning partner, displacement, or direct-pursuit "
            "posture."
        )
    ordered_questions = []
    for item in [*capture_questions, *unresolved]:
        if item not in ordered_questions:
            ordered_questions.append(item)
    action = (
        f"{action_prefix} · " + ", ".join(ordered_questions[:4])
        + action_suffix
    )
    compact_buyer = _compact_org(buyer)
    return {
        "label": (f"{compact_buyer.upper()} · "
                  + ("CLIENT FOOTPRINT"
                     if source_kind == "usaspending-award"
                     and client_footprint else
                     "EXECUTED BUY" if source_kind == "usaspending-award"
                     else "PUBLISHED NOTICE")),
        "title": (
            f"{compact_buyer} renewal route" if client_footprint
            else f"{compact_buyer} acquisition route"),
        "requirement": requirement_excerpt,
        "pathway": pathway,
        "access": access,
        "action": action,
        "citation_label": (
            f"award {award_id}" if source_kind == "usaspending-award"
            else f"SAM notice {facts_by_label.get('Solicitation') or notice_id}"),
    }


def _acquisition_pathway_basis(
        pick: AcquisitionPathPick, facts: list[dict],
        missing_facts: list[str]) -> dict:
    return {
        "version": 1,
        "agency": pick.agency,
        "buyer": pick.buyer,
        "capability": pick.capability,
        "source_kind": pick.source_kind,
        "source_identity": pick.source_identity,
        "award_id": pick.award_id,
        "notice_guid": pick.notice_guid,
        "route_key": (
            f"award:{pick.source_identity}"
            if pick.source_kind == "usaspending-award"
            else _sam_acquisition_route_key(pick.record)
        ),
        "source_company_key": (
            _company_key(str(pick.record.get("recipient") or ""))
            if pick.source_kind == "usaspending-award" else ""
        ),
        "client_footprint": bool(pick.client_footprint),
        "requirement_excerpt": _acquisition_requirement_excerpt(
            pick.record, pick.capability),
        "procurement_facts": facts,
        "missing_procurement_facts": missing_facts,
    }


def _teaming_route_basis(pick: TeamingPick) -> dict:
    """Structured source fields that solely determine public teaming copy."""
    if pick.basis == "subaward-evidence" and pick.edges:
        edge = pick.edges[0]
        return {
            "kind": "subaward-evidence",
            "partner": str(pick.partner or ""),
            "agency": str(edge.get("awarding_agency") or ""),
            "matched_naics": str(edge.get("_matched_naics") or ""),
            "shared_core_terms": list(
                edge.get("_shared_core_terms") or []),
            "selected_play_identity": str(
                edge.get("_selected_play_identity") or ""),
            "selected_play_label": str(
                edge.get("_selected_play_label") or ""),
            "subaward_id": str(edge.get("subaward_id") or ""),
            "prime_award_id": str(edge.get("prime_award_id") or ""),
            "award_generated_id": str(
                edge.get("_prime_award_generated_id") or ""),
            "edge_count": pick.edge_count,
        }
    matched = (pick.play.get("matched_products")
               or pick.play.get("matched_terms") or [])
    return {
        "kind": "entry-thesis",
        "partner": str(pick.partner or ""),
        "route": str(matched[0]) if matched else "",
        "buyer": str(pick.buyer or ""),
        "pop_end": _iso_day(pick.play.get("end_date")),
        "award_id": str(pick.play.get("award_id") or ""),
        "award_generated_id": str(
            pick.play.get("generated_internal_id") or ""),
    }


def _teaming_public_fields(route_basis: dict) -> dict:
    """Pure, fail-closed rendering of one cited candidate route.

    The validator calls this same builder against the trail-bound basis, so a
    post-compose edit to angle, proof, target, citation label, or GID refuses.
    """
    from agents.reports.links import build_usaspending_award_link

    kind = route_basis.get("kind")
    gid = str(route_basis.get("award_generated_id") or "")
    build_usaspending_award_link(gid)  # exact canonical identity or fail
    if kind == "subaward-evidence":
        agency = str(route_basis.get("agency") or "").strip()
        naics = str(route_basis.get("matched_naics") or "").strip()
        shared_terms = [
            str(term).strip() for term in
            (route_basis.get("shared_core_terms") or [])
            if str(term).strip()
        ]
        selected_play_identity = str(
            route_basis.get("selected_play_identity") or "").strip()
        selected_play_label = str(
            route_basis.get("selected_play_label") or "").strip()
        subaward_id = str(route_basis.get("subaward_id") or "").strip()
        prime_award_id = str(
            route_basis.get("prime_award_id") or "").strip()
        edge_count = int(route_basis.get("edge_count") or 0)
        if not (agency and naics and shared_terms and selected_play_identity
                and selected_play_label and subaward_id and prime_award_id
                and edge_count == 1):
            raise ValueError(
                "subaward teaming route lacks agency, NAICS, shared CORE "
                "terms, exact selected-play identity, native subaward "
                "identity, prime award identity, or one-record binding")
        rationale = " / ".join(shared_terms[:2]).upper()
        return {
            "target": (f"{_compact_org(agency)} · "
                       f"{selected_play_label}"),
            "angle": (f"SHARED CORE: {rationale} · "
                      f"SUBAWARD {subaward_id}"),
            "proof": (f"PRIME AWARD {prime_award_id} · "
                      "MATCHED PRIME-SUBAWARD RECORD"),
            "citation_label": f"prime award {prime_award_id}",
            "award_generated_id": gid,
            "source_identity": f"{gid}::subaward::{subaward_id}",
        }
    if kind != "entry-thesis":
        raise ValueError(f"unknown teaming route basis {kind!r}")
    route = str(route_basis.get("route") or "").strip()
    buyer = str(route_basis.get("buyer") or "").strip()
    award_id = str(route_basis.get("award_id") or "").strip()
    if not (route and buyer and award_id):
        raise ValueError(
            "entry thesis lacks matched route, buyer, or award identity")
    angle = f"{route} award at {_compact_org(buyer)}"
    pop_end = _iso_day(route_basis.get("pop_end"))
    if pop_end:
        angle += f" · through {fmt_day(pop_end)}"
    else:
        angle = "Cited " + angle
    return {
        "target": f"{route} account",
        "angle": angle.upper(),
        "proof": f"AWARD {award_id} · CITED AWARD-HOLDER ROUTE",
        "citation_label": f"award {award_id}",
        "award_generated_id": gid,
        "source_identity": gid,
    }


def validate_machine_teaming_card(card: dict) -> dict:
    """Enforce the current public candidate-route contract on one card."""
    machine = card.get("machine_evidence")
    if not isinstance(machine, dict):
        raise ValueError("candidate teaming card lacks machine evidence")
    route_basis = machine.get("route_basis")
    if not isinstance(route_basis, dict):
        raise ValueError(
            f"candidate teaming card {card.get('partner')!r} lacks the "
            "current structured route basis; re-run compose")
    expected = _teaming_public_fields(route_basis)
    actual = {
        key: card.get(key)
        for key in ("target", "angle", "proof", "citation_label",
                    "award_generated_id")
    }
    expected_public = {key: expected[key] for key in actual}
    if actual != expected_public or card.get("program") != expected["target"]:
        raise ValueError(
            f"candidate teaming card {card.get('partner')!r} public route "
            "copy or canonical citation does not match its structured "
            "evidence basis")
    expected_kind = (
        "usaspending-subaward"
        if route_basis.get("kind") == "subaward-evidence"
        else "corridor-entry-thesis"
    )
    if (machine.get("source_identity") != expected["source_identity"]
            or machine.get("source_kind") != expected_kind
            or not machine.get("quote")
            or machine.get("in_scope") is not True
            or not machine.get("scope_basis")):
        raise ValueError(
            f"candidate teaming card {card.get('partner')!r} source "
            "identity, kind, quote, or positive scope proof does not match "
            "the current contract")
    expected_award_id = (
        route_basis.get("prime_award_id")
        if route_basis.get("kind") == "subaward-evidence"
        else route_basis.get("award_id")
    )
    if str(card.get("award_id") or "") != str(expected_award_id or ""):
        raise ValueError(
            f"candidate teaming card {card.get('partner')!r} award identity "
            "does not match its structured route basis")
    return expected


def _validate_machine_teaming_only_contract(content) -> None:
    """Semantic gate for the genuine candidate-teaming variant only."""
    payload = (content.model_dump(mode="python")
               if hasattr(content, "model_dump") else content)
    rows = payload.get("teaming") or []
    if not rows:
        raise ValueError(
            "MACHINE_EVIDENCE: candidate teaming opportunities has no "
            "publication-grade route; generic placeholder refused")
    for card in rows:
        validate_machine_teaming_card(card)
        route_basis = (card.get("machine_evidence") or {}).get(
            "route_basis") or {}
        if route_basis.get("kind") != "subaward-evidence":
            raise ValueError(
                "MACHINE_EVIDENCE: candidate teaming opportunities may "
                "publish only matched prime-subaward routes; incumbent "
                "entry theses are acquisition research, not partners")


def validate_machine_acquisition_pathway_card(card: dict) -> dict:
    """Reproject an acquisition card from its exact source-bound basis."""

    machine = card.get("machine_evidence")
    if not isinstance(machine, dict):
        raise ValueError("acquisition pathway card lacks machine evidence")
    basis = machine.get("pathway_basis")
    expected = _acquisition_pathway_public_fields(basis)
    actual = {key: card.get(key) for key in expected}
    if actual != expected:
        raise ValueError(
            "acquisition pathway public copy does not match its structured "
            "source basis")
    source_kind = str(basis.get("source_kind") or "")
    identity = str(basis.get("source_identity") or "")
    identity_field = (
        "award_generated_id" if source_kind == "usaspending-award"
        else "sam_notice_guid")
    if (str(card.get(identity_field) or "") != identity
            or str(machine.get("source_identity") or "") != identity
            or str(machine.get("source_kind") or "") != source_kind
            or str(machine.get("quote") or "")
            != str(basis.get("requirement_excerpt") or "")
            or machine.get("in_scope") is not True
            or not str(machine.get("scope_basis") or "").strip()
            or card.get("procurement_facts")
            != basis.get("procurement_facts")
            or card.get("missing_procurement_facts")
            != basis.get("missing_procurement_facts")):
        raise ValueError(
            "acquisition pathway identity, quote, scope, or procurement "
            "projection does not match its source basis")
    if source_kind == "usaspending-award" and str(
            card.get("award_id") or "") != str(basis.get("award_id") or ""):
        raise ValueError(
            "acquisition pathway award identity does not match its basis")
    return expected


def validate_machine_route_band_contract(content) -> None:
    """Require one honest Section 03 and prevent cross-band role collapse."""

    payload = (content.model_dump(mode="python")
               if hasattr(content, "model_dump") else content)
    owners: dict[str, str] = {}
    for band in (
            "best_fit", "competitors", "acquisition_pathways", "horizon"):
        for card in payload.get(band) or []:
            identity = str((card.get("machine_evidence") or {}).get(
                "source_identity") or "").strip()
            if not identity:
                continue
            prior = owners.get(identity)
            if prior is not None and prior != band:
                raise ValueError(
                    "MACHINE_EVIDENCE: one source identity cannot publish "
                    f"in both {prior} and {band}: {identity}")
            owners[identity] = band
    teaming = payload.get("teaming") or []
    pathways = payload.get("acquisition_pathways") or []
    if bool(teaming) == bool(pathways):
        state = "both" if teaming else "neither"
        if state == "neither":
            raise ValueError(
                "MACHINE_EVIDENCE: candidate teaming opportunities has no "
                "publication-grade route and federal acquisition pathways "
                "has no source-bound fallback; generic placeholder refused")
        raise ValueError(
            "MACHINE_EVIDENCE: Section 03 must publish exactly one of "
            "distinct candidate teaming opportunities or federal "
            f"acquisition pathways; found {state}")
    competitor_keys = {
        _company_key(str(card.get("title") or ""))
        for card in payload.get("competitors") or []
        if _company_key(str(card.get("title") or ""))
    }
    displayed_company_keys = set(competitor_keys)
    for card in payload.get("best_fit") or []:
        machine = card.get("machine_evidence") or {}
        if machine.get("source_kind") != "usaspending-award":
            continue
        title = str(card.get("title") or "")
        recipient = title.split("×", 1)[1].strip() if "×" in title else ""
        key = _company_key(recipient)
        if key:
            displayed_company_keys.add(key)
    if teaming:
        _validate_machine_teaming_only_contract(payload)
        partner_keys = [
            _company_key(str(card.get("partner") or ""))
            for card in teaming
        ]
        if (any(not key for key in partner_keys)
                or len(partner_keys) != len(set(partner_keys))):
            raise ValueError(
                "MACHINE_EVIDENCE: candidate teaming partners are not "
                "distinct normalized companies")
        overlap = sorted(set(partner_keys) & displayed_company_keys)
        if overlap:
            raise ValueError(
                "MACHINE_EVIDENCE: a company cannot appear in best-fit or "
                "competitor routes and again as a candidate teaming "
                "partner: " + ", ".join(overlap))
        return
    featured_route_keys: set[str] = set()
    for card in payload.get("best_fit") or []:
        machine = card.get("machine_evidence") or {}
        identity = str(machine.get("source_identity") or "").strip()
        if machine.get("source_kind") == "sam.gov":
            refs = card.get("evidence") or []
            label = str(refs[0].get("label") or "").strip() if refs else ""
            route_value = label if label and label != identity else identity
            prefix = "sam-solicitation" if route_value != identity else "sam-notice"
            if route_value:
                normalized_route = re.sub(
                    r"\s+", " ", route_value).casefold()
                featured_route_keys.add(
                    f"{prefix}:{normalized_route}")
        elif identity:
            featured_route_keys.add(f"award:{identity}")
    for card in payload.get("competitors") or []:
        identity = str((card.get("machine_evidence") or {}).get(
            "source_identity") or "").strip()
        if identity:
            featured_route_keys.add(f"award:{identity}")
    for card in pathways:
        validate_machine_acquisition_pathway_card(card)
        basis = (card.get("machine_evidence") or {}).get(
            "pathway_basis") or {}
        if str(basis.get("route_key") or "") in featured_route_keys:
            raise ValueError(
                "MACHINE_EVIDENCE: federal acquisition pathways must use "
                "research records distinct from best-fit and competitor "
                "cards")
        if str(basis.get("source_company_key") or "") in \
                displayed_company_keys:
            raise ValueError(
                "MACHINE_EVIDENCE: federal acquisition pathways must not "
                "reuse a company already displayed in best-fit or "
                "competitor cards")
        public_blob = _company_key(" ".join(
            str(card.get(key) or "") for key in (
                "label", "title", "requirement", "pathway", "access",
                "action")) + " " + json.dumps(
                    card.get("procurement_facts") or [], sort_keys=True))
        leaked = [
            key for key in displayed_company_keys
            if key and re.search(
                rf"(?:^| ){re.escape(key)}(?: |$)", public_blob)
        ]
        if leaked:
            raise ValueError(
                "MACHINE_EVIDENCE: acquisition pathways must not recast "
                "competitor companies as public route recommendations")


def validate_machine_teaming_contract(content) -> None:
    """Stable press seam for the now-discriminated Section 03 contract."""

    validate_machine_route_band_contract(content)


def _summary_cell(card: dict, label: str) -> str:
    for cell in card.get("cells") or []:
        if str(cell.get("label") or "").casefold() == label.casefold():
            return str(cell.get("value") or "").strip()
    return ""


def _summary_day(value: str) -> tuple[Optional[date], str]:
    """Parse the board's displayed date without reaching behind the card."""
    try:
        parsed = datetime.strptime(value.strip().title(), "%d %b %Y").date()
    except (TypeError, ValueError):
        return None, ""
    return parsed, f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _summary_list(values: list[str]) -> str:
    values = [value for value in values if value]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def _summary_agency(value: str) -> str:
    """Readable component label, still derived from the displayed card."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    component = text.rsplit("/", 1)[-1].strip()
    if component.upper() == "US MARSHALS SERVICE":
        return "U.S. Marshals Service"
    return _compact_org(component or text)


def _summary_corridor(card: dict, client_name: str) -> Optional[dict]:
    machine = card.get("machine_evidence") or {}
    if machine.get("source_kind") != "usaspending-award":
        return None
    buyer, marker, recipient = str(card.get("title") or "").partition(" × ")
    obligated = _summary_cell(card, "Obligated to date")
    award_scale = _summary_cell(card, "Award scale")
    amount = obligated or award_scale
    raw_day, shown_day = _summary_day(_summary_cell(card, "Window"))
    if not (marker and buyer and recipient and amount and raw_day and shown_day):
        return None
    return {
        "card": card,
        "day": raw_day,
        "shown_day": shown_day,
        "buyer": _compact_org(buyer),
        "recipient": _display_company(recipient, 40),
        "amount": amount,
        "amount_basis": (
            "obligated to date" if obligated else "award scale"
        ),
        "footprint": "CLIENT FOOTPRINT" in str(card.get("account") or ""),
        "civilian": "preset:civilian" in str(machine.get("scope_basis") or ""),
        "client_recipient": _is_client_company(recipient, client_name),
        "identity": str(machine.get("source_identity") or ""),
    }


def _summary_notice(card: dict) -> Optional[dict]:
    machine = card.get("machine_evidence") or {}
    if machine.get("source_kind") != "sam.gov":
        return None
    raw_day, shown_day = _summary_day(_summary_cell(card, "Window"))
    title = re.sub(r"\s+", " ", str(card.get("title") or "")).strip()
    agency = _summary_agency(
        str(card.get("agency_name") or machine.get("agency") or ""))
    if not (raw_day and shown_day and title and agency):
        return None
    return {
        "card": card,
        "day": raw_day,
        "shown_day": shown_day,
        "title": title,
        "agency": agency,
        "triage_verdict": str(machine.get("triage_verdict") or "").lower(),
        "identity": str(machine.get("source_identity") or ""),
    }


def _summary_teaming(card: dict) -> Optional[dict]:
    machine = card.get("machine_evidence") or {}
    basis = machine.get("route_basis")
    if not isinstance(basis, dict):
        return None
    kind = str(basis.get("kind") or "")
    partner = _display_company(str(card.get("partner") or ""), 45)
    identity = str(machine.get("source_identity") or "")
    if kind == "entry-thesis":
        route = str(basis.get("route") or "").strip()
        buyer = _compact_org(str(basis.get("buyer") or ""))
        pop_end = _iso_day(basis.get("pop_end"))
        if not (route and buyer and partner and identity):
            return None
        return {
            "kind": kind,
            "fact": f"{route} at {buyer}",
            "partner": partner,
            "sort": (pop_end or "9999-12-31", identity),
        }
    if kind == "subaward-evidence":
        shared = [
            str(term).strip() for term in basis.get("shared_core_terms") or []
            if str(term).strip()
        ]
        agency = _compact_org(str(basis.get("agency") or ""))
        subaward_id = str(basis.get("subaward_id") or "").strip()
        if not (shared and agency and subaward_id and partner and identity):
            return None
        return {
            "kind": kind,
            "fact": f"{shared[0]} at {agency}",
            "partner": partner,
            "sort": ("9999-12-31", identity),
        }
    return None


def _summary_forward_event(card: dict) -> Optional[dict]:
    """Project one distinct forward record from its rendered card only.

    Active-award period ends are deliberately excluded: those dates already
    appear in the current-corridor narrative and are not separate acquisition
    records. Forecast, completion, and program records may earn one hero
    sentence, but remain explicitly unqualified pipeline.
    """
    machine = card.get("machine_evidence") or {}
    source_kind = str(machine.get("source_kind") or "")
    if source_kind not in {
            "agency-forecast", "contract-award",
            "usaspending-expiring-award", "program"}:
        return None
    identity = str(machine.get("source_identity") or "").strip()
    timing = str(card.get("value") or "").strip()
    sort_day = _iso_day(
        (card.get("timing_basis") or {}).get("sort_date"))
    label = str(card.get("label") or "")
    agency = _compact_org(label.split("·", 1)[0].strip())
    title = _display_company(str(card.get("title") or ""), 48)
    if not (identity and timing and agency and title):
        return None
    parsed_day, shown_day = _summary_day(timing)
    if parsed_day is not None:
        timing = shown_day
    # Only an agency-published forecast value band is prospective value.
    # Award obligations and ceilings remain visible on their exact cards but
    # never become unlabeled future-opportunity dollars in the hero readout.
    fact_priority = {"Published value band": 0}
    stated_value = ""
    facts = sorted(
        (row for row in (card.get("procurement_facts") or [])
         if isinstance(row, dict)),
        key=lambda row: fact_priority.get(str(row.get("label") or ""), 99),
    )
    for fact in facts if source_kind == "agency-forecast" else []:
        label_text = str(fact.get("label") or "")
        value = str(fact.get("value") or "").strip()
        if label_text in fact_priority and "$" in value:
            stated_value = value
            break
    job = card.get("job_context") or {}
    work = re.sub(r"\s+", " ", str(job.get("text") or "")).strip()
    if job.get("kind") != "published-description":
        work = ""
    # Prefer records that describe an acquisition or program decision over
    # generic contract-completion clocks.  A nearer completion date remains
    # available as a fallback, but must not bury a published forecast that
    # actually says what the government expects to buy.
    source_priority = {
        "agency-forecast": 0,
        "program": 1,
        "contract-award": 2,
        "usaspending-expiring-award": 2,
    }
    return {
        "identity": identity,
        "source_kind": source_kind,
        "agency": agency,
        "title": title,
        "timing": timing,
        "sort": (
            source_priority[source_kind],
            sort_day or "9999-12-31",
            identity,
        ),
        "stated_value": stated_value,
        "work": _short(work, 88) if work else "",
    }


def _executive_summary_sentences(
        client_name: str, best_fit: list[dict], teaming: list[dict],
        horizon: Optional[list[dict]] = None) -> list[str]:
    """Two-to-five evidence-derived sentences for the existing hero slot.

    Only fields already displayed on the selected cards, plus their bound
    machine evidence, may contribute. Missing structure is omitted rather
    than guessed. The empty input therefore yields no narrative at all.
    """
    client_label = client_display_name(client_name)
    corridors = [
        fact for card in best_fit
        if (fact := _summary_corridor(card, client_label)) is not None
    ]
    notices = [
        fact for card in best_fit
        if (fact := _summary_notice(card)) is not None
    ]
    routes = [
        fact for card in teaming
        if (fact := _summary_teaming(card)) is not None
    ]
    forward = [
        fact for card in (horizon or [])
        if (fact := _summary_forward_event(card)) is not None
    ]
    corridors.sort(key=lambda row: (row["day"], row["identity"]))
    notices.sort(key=lambda row: (row["day"], row["identity"]))
    routes.sort(key=lambda row: row["sort"])
    forward.sort(key=lambda row: row["sort"])

    lead_kind = ""
    lead = None
    dated = [
        (row["day"], "corridor", row) for row in corridors
    ] + [
        (row["day"], "notice", row) for row in notices
    ]
    if dated:
        _, lead_kind, lead = min(
            dated, key=lambda item: (item[0], item[1], item[2]["identity"]))

    sentences: list[str] = []
    if lead_kind == "corridor":
        via = ("" if lead["client_recipient"] else
               f" via {lead['recipient']}")
        if lead["footprint"]:
            sentences.append(
                f"The nearest dated signal is {client_label}\u2019s "
                f"{lead['amount']} {lead['amount_basis']} "
                f"{lead['buyer']} footprint{via}, whose "
                f"cited award period ends {lead['shown_day']}.")
        else:
            sentences.append(
                f"The nearest dated signal is the cited "
                f"{lead['amount']} {lead['amount_basis']} "
                f"{lead['buyer']} award corridor through "
                f"{lead['recipient']}, whose award period ends "
                f"{lead['shown_day']}.")
    elif lead_kind == "notice":
        if lead["triage_verdict"] == "monitor":
            sentence = (
                "The nearest dated notice-level signal is the "
                f"{lead['agency']}\u2019s {lead['title']}, with responses due "
                f"{lead['shown_day']}; the assessment classifies it as "
                f"a monitor signal, outside {client_label}'s current "
                "pursuit set")
        else:
            sentence = (
                f"The nearest dated notice-level signal is "
                f"{client_label}\u2019s {lead['title']} at the {lead['agency']}, "
                f"with responses due {lead['shown_day']}")
        sentences.append(sentence + ".")

    if lead_kind == "corridor":
        remaining = [row for row in corridors if row is not lead][:2]
        if remaining:
            facts = [
                f"{row['buyer']} ({row['amount']} {row['amount_basis']}"
                + ("" if row["client_recipient"]
                   else f" via {row['recipient']}")
                + ")"
                for row in remaining
            ]
            clustered = (
                len(corridors) == 3
                and len(remaining) == 2
                and lead["footprint"]
                and all(row["footprint"] for row in remaining)
                and all(row["civilian"] for row in corridors)
                and remaining[0]["day"].month == remaining[1]["day"].month
                and remaining[0]["day"].day >= 20
                and remaining[1]["day"].day >= 20
            )
            if clustered:
                month = remaining[0]["day"].strftime("%B")
                sentences.append(
                    f"{_summary_list(facts)} have current award periods "
                    f"ending in late {month}, creating a concentrated "
                    "period-end action "
                    "sequence across three evidenced civilian accounts.")
            else:
                dates = [row["shown_day"] for row in remaining]
                sentences.append(
                    f"{_summary_list(facts)} follow with cited award periods "
                    f"ending {_summary_list(dates)}.")

    if forward:
        event = forward[0]
        kind = {
            "agency-forecast": "published acquisition forecast",
            "contract-award": "contract-completion signal",
            "usaspending-expiring-award": "contract-completion signal",
            "program": "official program signal",
        }[event["source_kind"]]
        work = f" for {event['work']}" if event["work"] else ""
        value = (
            f" The cited source records {event['stated_value']} and "
            f"timing on {event['timing']}."
            if event["stated_value"] else
            f" The cited source states timing on {event['timing']}."
        )
        sentences.append(
            f"A separate cited {kind} is {event['agency']} × "
            f"{event['title']}{work}.{value}")
        sentences.append(
            "Validate its acquisition path before assigning pipeline value "
            "or a capture deadline.")

    if routes:
        kinds = {row["kind"] for row in routes}
        route_facts = _summary_list([row["fact"] for row in routes])
        partners = _summary_list([row["partner"] for row in routes])
        count = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"} \
            .get(len(routes), str(len(routes)))
        prefix = (
            "Beyond the installed base" if lead_kind == "corridor"
            and lead and lead["footprint"] else
            "Beyond that notice" if lead_kind == "notice" else
            "Beyond the priority routes" if lead is not None else
            "The current evidence set"
        )
        if kinds == {"entry-thesis"}:
            record_phrase = f"cited {route_facts} awards identify"
            route_label = "award-holder decision"
            action = (
                f"make the partner-or-displace call for {client_label} at "
                "those accounts"
            )
        elif kinds == {"subaward-evidence"}:
            record_phrase = (
                f"cited opportunity and subaward records for {route_facts} "
                "establish"
            )
            route_label = "prime-channel signal"
            action = f"prioritize those primes for {client_label} teaming"
        else:
            record_phrase = "cited award and subaward records establish"
            route_label = "source-backed account-entry"
            action = (
                f"assign each route a {client_label} partner or "
                "displacement posture"
            )
        noun = "route" if len(routes) == 1 else "routes"
        if kinds == {"subaward-evidence"}:
            route_phrase = route_label + ("" if len(routes) == 1 else "s")
        else:
            route_phrase = f"{route_label} {noun}"
        sentences.append(
            f"{prefix}, {record_phrase} {count} {route_phrase} through "
            f"{partners}; {action}.")

    if not sentences:
        return []
    if len(sentences) == 1:
        if lead is not None:
            sentences.append(
                "It is the only priority route in the current screened "
                "evidence set.")
        else:
            sentences.append(
                "No priority federal route cleared the current "
                "machine-screened publication bar.")
    return sentences[:5]


def derive_executive_summary(
        client_name: str, best_fit: list[dict], teaming: list[dict],
        horizon: Optional[list[dict]] = None) -> str:
    """Public pure seam used identically at compose and press time."""
    return " ".join(
        _executive_summary_sentences(
            client_name, best_fit, teaming, horizon))


def validate_machine_executive_summary_contract(
        content, *, sweep: Optional[dict] = None) -> None:
    """Refuse any machine hero narrative not rederived from final cards."""
    payload = (content.model_dump(mode="python")
               if hasattr(content, "model_dump") else content)
    if sweep is not None:
        triage = ((sweep.get("results") or {}).get("triage") or {})
        for card in payload.get("best_fit") or []:
            machine = card.get("machine_evidence") or {}
            if machine.get("source_kind") != "sam.gov":
                continue
            identity = str(machine.get("source_identity") or "")
            stored = triage.get(identity) or {}
            expected_verdict = str(stored.get("verdict") or "")
            expected_reason = str(stored.get("reason") or "")
            if (str(machine.get("triage_verdict") or "") != expected_verdict
                    or str(machine.get("triage_reason") or "")
                    != expected_reason):
                raise ValueError(
                    f"MACHINE_EVIDENCE: best-fit notice {identity!r} triage "
                    "verdict or reason does not match the stored sweep")
            if expected_verdict and (
                    f" · {expected_verdict.upper()} · "
                    not in str(card.get("account") or "")):
                raise ValueError(
                    f"MACHINE_EVIDENCE: best-fit notice {identity!r} does "
                    "not visibly surface its stored triage verdict")
    sentences = _executive_summary_sentences(
        str(payload.get("client_name") or ""),
        list(payload.get("best_fit") or []),
        list(payload.get("teaming") or []),
        list(payload.get("horizon") or []),
    )
    expected = " ".join(sentences)
    actual = str(payload.get("hero_context") or "")
    if actual != expected:
        raise ValueError(
            "MACHINE_EVIDENCE: executive summary does not match the "
            "selected best-fit and candidate-teaming records; re-run compose")
    if expected:
        if not 2 <= len(sentences) <= 5:
            raise ValueError(
                "MACHINE_EVIDENCE: executive summary must contain two to "
                "five evidence-derived sentences")


def compose_content(client_name: str, sweep: dict, *,
                    best_fit: list, competitors: list, teaming: list,
                    acquisition_pathways: Optional[list] = None,
                    horizon: list, today: date, offline: bool,
                    prose_trail: list,
                    footprints: Optional[list] = None) -> dict:
    """Assemble the Signal Board content dict in the golden shape. The
    hero band (scale/figures) is attached by build_hero afterward."""
    from tools.relevance.scope import record_department
    acquisition_pathways = list(acquisition_pathways or [])
    client_label = client_display_name(client_name)
    client_inputs = load_client_inputs(client_name)
    core_terms = []
    if best_fit:
        for pick in best_fit:
            for term in pick.verdict.core_terms:
                if term not in core_terms:
                    core_terms.append(term)
    if not core_terms:
        core_terms = [t.term for t in client_inputs["taxonomy"].core][:4]

    chips = [{"label": term.upper(), **({"tone": "hot"} if i < 2 else {})}
             for i, term in enumerate(core_terms[:4])]
    if any(t.basis == "subaward-evidence" for t in teaming):
        chips.append({"label": "EVIDENCED PARTNER ROUTES", "tone": "live"})
    elif acquisition_pathways:
        chips.append({"label": "ACQUISITION PATHS TO VALIDATE"})
    else:
        chips.append({"label": "ROUTE EVIDENCE GAP"})

    award_picks = [pick for pick in best_fit
                   if pick.source_kind == "usaspending-award"]
    footprint_picks = (
        list(footprints) if footprints is not None else [
            pick for pick in award_picks
            if _is_client_footprint(
                pick.recipient, pick.record, client_name,
                taxonomy=client_inputs["taxonomy"])
        ]
    )
    footprint_gids = {pick.gid for pick in footprint_picks}
    news_award_picks = footprint_picks + [
        pick for pick in award_picks if pick.gid not in footprint_gids
    ]

    news = []
    for pick in news_award_picks:
        footprint = pick.gid in footprint_gids
        kind = "active footprint" if footprint else "displacement corridor"
        party = (client_label if _is_client_company(
            pick.recipient, client_name) else
            f"{client_label} via {_display_company(pick.recipient, 28)}"
            if footprint else _display_company(pick.recipient, 30))
        news.append({
            "head": f"{_compact_org(pick.buyer)} · {party} · {kind}",
            "body": (f"{fmt_money(pick.amount)} obligated · active award "
                     f"corridor · PoP end {fmt_day(pick.window_end)}"),
            "award_generated_id": pick.gid,
        })
    for lane in competitors:
        row = {
            "head": (f"{_compact_org(lane.buyer)} · "
                     f"{_display_company(lane.recipient, 30)} · "
                     "competitor corridor"),
            "body": (f"{fmt_money(lane.amount)} obligated · active award"
                     + (f" · through {fmt_day(lane.record.get('end_date'))}"
                        if _iso_day(lane.record.get("end_date")) else "")),
        }
        if lane.gid:
            row["award_generated_id"] = lane.gid
        news.append(row)

    dept_components: dict[str, list] = {}
    coverage_picks = footprint_picks + [
        pick for pick in best_fit
        if not pick.gid or pick.gid not in footprint_gids
    ]
    for pick in coverage_picks:
        dept = (record_department({"agency": pick.agency})
                or _compact_org(pick.agency))
        component = _compact_org(pick.buyer or pick.agency).upper()
        dept_components.setdefault(dept, [])
        if component not in dept_components[dept]:
            dept_components[dept].append(component)
    for lane in competitors:
        dept = (record_department({"agency": lane.agency})
                or _compact_org(lane.agency))
        comp = _compact_org(lane.buyer).upper()
        dept_components.setdefault(dept, [])
        if comp not in dept_components[dept]:
            dept_components[dept].append(comp)
    for pick in acquisition_pathways:
        dept = (record_department({"agency": pick.agency})
                or _compact_org(pick.agency))
        comp = _compact_org(pick.buyer or pick.agency).upper()
        dept_components.setdefault(dept, [])
        if comp and comp not in dept_components[dept]:
            dept_components[dept].append(comp)
    # The authority rail describes the complete published assessment, not
    # only the current-award bands above it. A forward-only forecast,
    # recompete, expiry, or PROGRAM organization therefore belongs in the
    # same logo-capable coverage inventory.
    for pick in horizon:
        row = pick.row
        agency = str(
            pick.agency or row.get("agency")
            or row.get("awarding_agency") or "")
        if not agency:
            continue
        dept = (record_department(row)
                or record_department({"agency": agency})
                or _compact_org(agency))
        component = str(
            row.get("component") or row.get("buyer")
            or row.get("awarding_sub_agency") or agency)
        comp = _compact_org(component).upper()
        dept_components.setdefault(dept, [])
        if comp and comp not in dept_components[dept]:
            dept_components[dept].append(comp)
    coverage = [{"dept": dept, "components": " · ".join(comps[:3])}
                for dept, comps in sorted(dept_components.items())]

    best_fit_cards = []
    for i, pick in enumerate(best_fit):
        quotes = [s.context for s in pick.verdict.spans[:2]]
        fit_line = compose_line(
            "fit", pick.source_id,
            {"title": pick.title, "core": pick.verdict.core_terms[:3],
             "quoted_evidence": quotes},
            template=(pick.verdict.core_terms[0].title()
                      if pick.verdict.core_terms else "Capability match"),
            client=client_name, offline=offline, trail=prose_trail,
            max_len=40)
        code = (record_department({"agency": pick.agency})
                or _compact_org(pick.agency))
        if pick.source_kind == "usaspending-award":
            fact_record = _enriched_award_record(
                sweep, pick.record, gid=pick.gid, award_id=pick.award_id)
            facts, missing_facts = _procurement_facts(
                fact_record, family="award", identity_label="Award",
                identity_value=pick.award_id)
            pick.procurement_facts = facts
            pick.missing_procurement_facts = missing_facts
            lane_kind = ("CLIENT FOOTPRINT" if _is_client_footprint(
                pick.recipient, pick.record, client_name,
                taxonomy=client_inputs["taxonomy"])
                else "DISPLACEMENT")
            excerpts = len(pick.verdict.spans)
            card = {
                "rank": f"{i + 1:02d}",
                "code": code,
                "agency_name": pick.agency,
                "title": pick.title[:70],
                "account": (f"{str(code).upper()} · ACTIVE AWARD CORRIDOR · "
                            f"{lane_kind}"),
                "award_generated_id": pick.gid,
                "evidence": [{"award_generated_id": pick.gid,
                              "label": pick.award_id}],
                "cells": [
                    {"label": "Obligated to date",
                     "value": fmt_money(pick.amount),
                     "small": "HISTORICAL SPEND · NOT OPPORTUNITY SIZE"},
                    {"label": "Window", "value": fmt_day(pick.window_end),
                     "small": "PERIOD OF PERFORMANCE END"},
                    {"label": "Fit", "value": fit_line,
                     "small": ", ".join(
                         pick.verdict.core_terms[:2]).upper()[:40]},
                    {"label": "Evidence",
                     "value": _source_excerpt_count(excerpts),
                     "small": "VERIFIED AWARD RECORD"},
                ],
                "procurement_facts": facts,
                "missing_procurement_facts": missing_facts,
                "machine_evidence": {
                    "source_identity": pick.gid,
                    "source_kind": "usaspending-award",
                    "quote": quotes[0] if quotes else "",
                    "agency": pick.agency,
                    "in_scope": pick.scope_ok,
                    "scope_basis": pick.scope_basis,
                    "procurement_facts": facts,
                    "missing_procurement_facts": missing_facts,
                },
            }
        else:
            guid = notice_guid(pick.record)
            facts, missing_facts = _procurement_facts(
                pick.record, family="notice", identity_label="Solicitation",
                identity_value=notice_identity_label(pick.record))
            pick.procurement_facts = facts
            pick.missing_procurement_facts = missing_facts
            excerpts = len(pick.verdict.spans)
            card = {
                "rank": f"{i + 1:02d}",
                "code": code,
                "agency_name": pick.agency,
                "title": pick.title[:70],
                "account": (
                    f"{str(code).upper()} · "
                    + (f"{pick.triage_verdict.upper()} · "
                       if pick.triage_verdict else "")
                    + f"SCORE {pick.verdict.score}"
                ),
                "evidence": [{"sam_notice_guid": guid,
                              "label": notice_identity_label(pick.record)}],
                "cells": [
                    {"label": "Relevance",
                     "value": f"Score {pick.verdict.score}",
                     "small": f"{len(pick.verdict.core_terms)} CORE TERMS"},
                    {"label": "Window",
                     "value": fmt_day(pick.deadline) or "OPEN",
                     "small": ("RESPONSE DEADLINE" if pick.deadline
                               else "NO DATE")},
                    {"label": "Fit", "value": fit_line,
                     "small": ", ".join(
                         pick.verdict.core_terms[:2]).upper()[:40]},
                    {"label": "Evidence",
                     "value": _source_excerpt_count(excerpts),
                     "small": "VERIFIED NOTICE RECORD"},
                ],
                "procurement_facts": facts,
                "missing_procurement_facts": missing_facts,
                "machine_evidence": {
                    "source_identity": guid,
                    "source_kind": "sam.gov",
                    "quote": quotes[0] if quotes else "",
                    "agency": pick.agency,
                    "in_scope": True,
                    "scope_basis": pick.verdict.scope_basis,
                    "triage_verdict": pick.triage_verdict,
                    "triage_reason": pick.triage_reason,
                    "procurement_facts": facts,
                    "missing_procurement_facts": missing_facts,
                },
            }
            if guid:
                card["sam_notice_guid"] = guid
        best_fit_cards.append(card)

    competitor_cards = []
    for lane in competitors:
        fact_record = _enriched_award_record(
            sweep, lane.record, gid=lane.gid, award_id=lane.award_id)
        facts, missing_facts = _procurement_facts(
            fact_record, family="award", identity_label="Award",
            identity_value=lane.award_id)
        facts.extend(_competitor_boundary_facts(lane))
        lane.procurement_facts = facts
        lane.missing_procurement_facts = missing_facts
        matched = (lane.record.get("matched_products") or
                   lane.record.get("matched_terms") or [])
        # The prior wedge could be model-authored and said only what the
        # product was.  The replacement is a deterministic projection of the
        # exact sanctioned CORE span and says why the account matters.
        angle = ("Incumbent product · "
                 + str((matched or ["capability"])[0]))
        prose_trail.append({
            "kind": "angle", "ident": f"{lane.recipient}·{lane.buyer}",
            "source": "evidence-template:competitor",
            "line": angle,
        })
        relevance_verdict = score_record(
            lane.record, client_inputs["taxonomy"])
        relevance_basis = _verdict_client_relevance_basis(
            relevance_verdict,
            kind="record-core-match",
            source_identity=lane.gid,
            public_context={
                "band": "competitors",
                "buyer": _compact_org(lane.buyer),
                "client": client_label,
            },
        )
        lane.client_relevance_basis = relevance_basis
        competitor_cards.append({
            "label": f"{_compact_org(lane.buyer).upper()} · ACTIVE",
            "title": _display_company(lane.recipient, 34),
            "money": fmt_money(lane.amount),
            "small": ("OBLIGATED"
                      + (f" · THROUGH {fmt_day(lane.record.get('end_date'))}"
                         if _iso_day(lane.record.get("end_date")) else "")),
            "wedge": angle,
            "client_relevance": client_relevance_public(relevance_basis),
            "agency_name": lane.agency,
            "award_generated_id": lane.gid,
            "procurement_facts": facts,
            "missing_procurement_facts": missing_facts,
            "machine_evidence": {
                "source_identity": lane.gid,
                "source_kind": "usaspending-award",
                "quote": str(lane.record.get("description") or ""),
                "agency": lane.agency,
                "in_scope": lane.scope_ok,
                "scope_basis": lane.scope_basis,
                "client_relevance_basis": relevance_basis,
                "procurement_facts": facts,
                "missing_procurement_facts": missing_facts,
            },
        })

    acquisition_pathway_cards = []
    for pick in acquisition_pathways:
        if pick.source_kind == "usaspending-award":
            fact_record = _enriched_award_record(
                sweep, pick.record, gid=pick.gid,
                award_id=pick.award_id)
            identity_label = "Award"
            identity_value = pick.award_id
            family = "award"
        else:
            fact_record = dict(pick.record)
            identity_label = "Solicitation"
            identity_value = notice_identity_label(pick.record)
            family = "notice"
        facts, missing_facts = _procurement_facts(
            fact_record, family=family, identity_label=identity_label,
            identity_value=identity_value)
        facts = [
            fact for fact in facts if fact.get("label") != "Co-holders"]
        pick.procurement_facts = facts
        pick.missing_procurement_facts = missing_facts
        pathway_basis = _acquisition_pathway_basis(
            pick, facts, missing_facts)
        public = _acquisition_pathway_public_fields(pathway_basis)
        relevance_verdict = pick.verdict or score_record(
            pick.record, client_inputs["taxonomy"])
        relevance_basis = _verdict_client_relevance_basis(
            relevance_verdict,
            kind="record-core-match",
            source_identity=pick.source_identity,
            public_context={
                "band": "acquisition_pathways",
                "buyer": _compact_org(pick.buyer),
                "client": client_label,
                "client_footprint": pick.client_footprint,
                "evidence_state": (
                    "executed-award"
                    if pick.source_kind == "usaspending-award"
                    else "published-notice"
                ),
            },
        )
        pick.client_relevance_basis = relevance_basis
        evidence_quote = _acquisition_requirement_excerpt(
            pick.record, pick.capability)
        card = {
            **public,
            "agency_name": pick.agency,
            "client_relevance": client_relevance_public(relevance_basis),
            "procurement_facts": facts,
            "missing_procurement_facts": missing_facts,
            "machine_evidence": {
                "source_identity": pick.source_identity,
                "source_kind": pick.source_kind,
                "quote": evidence_quote,
                "agency": pick.agency,
                "in_scope": pick.scope_ok,
                "scope_basis": pick.scope_basis,
                "pathway_basis": pathway_basis,
                "client_relevance_basis": relevance_basis,
                "procurement_facts": facts,
                "missing_procurement_facts": missing_facts,
            },
        }
        if pick.source_kind == "usaspending-award":
            card["award_generated_id"] = pick.gid
            card["award_id"] = pick.award_id
        else:
            card["sam_notice_guid"] = pick.notice_guid
        acquisition_pathway_cards.append(card)
        prose_trail.append({
            "kind": "acquisition-pathway",
            "ident": pick.source_identity,
            "source": "evidence-template:acquisition-pathway",
            "line": public["action"],
        })

    teaming_cards = []
    for pick in teaming:
        route_basis = _teaming_route_basis(pick)
        public = _teaming_public_fields(route_basis)
        if pick.basis == "subaward-evidence" and pick.edges:
            edge = pick.edges[0]
            award_id = str(edge.get("prime_award_id") or "")
            agency = str(edge.get("awarding_agency") or "")
            evidence_quote = str(edge.get("description") or "")
            prime_record = dict(edge.get("_prime_award_record") or {})
            prime_gid = str(edge.get("_prime_award_generated_id") or "")
            fact_record = _enriched_award_record(
                sweep, {**pick.play, **prime_record}, gid=prime_gid,
                award_id=award_id)
            facts, missing_facts = _procurement_facts(
                fact_record, family="award", identity_label="Prime award",
                identity_value=award_id)
            if edge.get("subaward_id"):
                facts.append({
                    "label": "Subaward",
                    "value": str(edge["subaward_id"]),
                    "source_field": "USAspending subaward identity",
                })
        else:
            agency = str(pick.play.get("awarding_agency")
                         or pick.play.get("agency") or pick.agency or "")
            evidence_quote = str(pick.play.get("description") or "")
            award_id = str(pick.play.get("award_id") or "")
            corridor_gid = str(pick.play.get("generated_internal_id") or "")
            fact_record = _enriched_award_record(
                sweep, pick.play, gid=corridor_gid, award_id=award_id)
            facts, missing_facts = _procurement_facts(
                fact_record, family="award", identity_label="Award",
                identity_value=award_id)
        pick.procurement_facts = facts
        pick.missing_procurement_facts = missing_facts
        if pick.basis == "subaward-evidence":
            relevance_basis = _shared_route_client_relevance_basis(
                pick, profile=load_profile(client_name),
                taxonomy=client_inputs["taxonomy"],
                client_label=client_label)
        else:
            relevance_verdict = score_record(
                pick.play, client_inputs["taxonomy"])
            relevance_basis = _verdict_client_relevance_basis(
                relevance_verdict,
                kind="corridor-core-match",
                source_identity=public["source_identity"],
                public_context={
                    "band": "teaming",
                    "buyer": _compact_org(str(pick.buyer or "")),
                    "client": client_label,
                    "partner": _display_company(pick.partner, 34),
                },
            )
        pick.client_relevance_basis = relevance_basis
        # Unlike other prose slots, the partnership angle is intentionally
        # never delegated to compose_line: it is a deterministic rendering
        # of the cited structured record, and cannot fall back to generic
        # copy.  Retain the line in the INTERNAL compose trail.
        prose_trail.append({
            "kind": "wedge", "ident": pick.partner,
            "source": f"evidence-template:{pick.basis}",
            "line": public["angle"],
        })
        teaming_cards.append({
            "client": client_label.upper(),
            "partner": _short(pick.partner, 34).upper(),
            "target": public["target"],
            "angle": public["angle"],
            "proof": public["proof"],
            "award_id": award_id,
            "award_generated_id": public["award_generated_id"],
            "citation_label": public["citation_label"],
            "idv_piid": "",
            "program": public["target"],
            "agency": agency,
            "client_relevance": client_relevance_public(relevance_basis),
            "procurement_facts": facts,
            "missing_procurement_facts": missing_facts,
            "machine_evidence": {
                "source_identity": public["source_identity"],
                "source_kind": ("usaspending-subaward"
                                if pick.basis == "subaward-evidence"
                                else "corridor-entry-thesis"),
                "quote": evidence_quote,
                "agency": agency,
                "in_scope": pick.scope_ok,
                "scope_basis": pick.scope_basis,
                "route_basis": route_basis,
                "client_relevance_basis": relevance_basis,
                "procurement_facts": facts,
                "missing_procurement_facts": missing_facts,
            },
        })

    horizon_cards = []
    for i, pick in enumerate(horizon):
        row = pick.row
        job_context_basis = _horizon_job_context_basis(pick)
        horizon_footprint = pick.posture == "DEFEND"
        subject_marks = []
        fact_record = row
        fact_family = "calendar-expiry"
        identity_label = "Award"
        identity_value = pick.award_id or pick.ident
        source_citation = ""
        if pick.kind == "calendar-expiry":
            # A calendar row with only a PoP end is an expiry watch, never
            # evidence that a successor competition exists.
            value = fmt_day(pick.event_date)
            small = "AWARD PERIOD END · NOT CONFIRMED RECOMPETE"
            label = (
                f"{_compact_org(str(row.get('awarding_agency') or '')).upper()}"
                " · PERIOD END")
            recipient = str(row.get("recipient") or "")
            title = _short(
                recipient or str(row.get("description") or ""), 34)
            source_citation = f"Award period calendar · {pick.ident}"
            if recipient:
                if pick.posture == "DEFEND":
                    subject_marks.append({
                        "kind": "client", "label": client_name,
                        "display": client_label,
                    })
                else:
                    subject_marks.append({
                        "kind": "company", "label": recipient,
                        "display": _display_company(recipient, 22),
                    })
        elif pick.kind == "forecast":
            value = (fmt_day(pick.event_date)
                     if pick.timing_precision == "exact-date"
                     else pick.timing_source_value)
            small = (f"FORECAST · {str(row.get('forecast_status') or 'PLANNED').upper()}"
                     + f" · {pick.why.split()[1].upper()}")
            label = f"{_short(str(row.get('component') or row.get('agency') or ''), 18).upper()} · FORECAST"
            title = _short(str(row.get("title") or ""), 34)
            fact_family = "forecast"
            identity_label = "Forecast"
            source = str(row.get("source") or "agency forecast")
            source_citation = f"{source.replace('_', ' ').upper()} · {pick.ident}"
        elif pick.kind == "award-window":
            value = fmt_day(pick.event_date)
            small = "ACTIVE AWARD · PERIOD OF PERFORMANCE END"
            buyer = str(row.get("buyer") or row.get("agency") or "")
            label = f"{_compact_org(buyer).upper()} · AWARD WINDOW"
            recipient = str(row.get("recipient") or "")
            footprint = _is_client_footprint(
                recipient, row, client_name,
                taxonomy=client_inputs["taxonomy"])
            horizon_footprint = footprint
            fact_record = _enriched_award_record(
                sweep, row, gid=pick.gid, award_id=pick.award_id)
            fact_family = "award"
            identity_value = pick.award_id
            source_citation = f"USAspending award · {pick.award_id}"
            if _is_client_company(recipient, client_name):
                title = client_label
                subject_marks.append({
                    "kind": "client", "label": client_name,
                    "display": client_label,
                })
            elif footprint:
                recipient_display = _display_company(recipient, 22)
                title = f"{client_label} via {recipient_display}"
                subject_marks.extend([{
                    "kind": "client", "label": client_name,
                    "display": client_label,
                }, {
                    "kind": "company", "label": recipient,
                    "display": recipient_display,
                }])
            else:
                title = _display_company(recipient, 34)
                if recipient:
                    subject_marks.append({
                        "kind": "company", "label": recipient,
                        "display": _display_company(recipient, 22),
                    })
        elif pick.kind == "program":
            value = _program_public_timing_value(pick.event_date)
            source = str(row.get("_horizon_program_source") or "")
            small = str(
                row.get("_horizon_timing_label")
                or "OFFICIAL PROGRAM SIGNAL"
            )
            source_labels = {
                "dsip_topics": "DSIP",
                "sbir_gov": "SBIR/STTR",
                "grants_gov": "GRANTS.GOV",
                "reginfo_unified_agenda": "UNIFIED AGENDA",
                "darpa_opportunities": "DARPA",
                "dod_budget_exhibits": "DOD BUDGET",
                "foreign_assistance": "FOREIGN ASSISTANCE",
            }
            agency_label = _compact_org(pick.agency).upper() or "FEDERAL"
            label = f"{agency_label} · {source_labels.get(source, 'PROGRAM')}"
            title = _short(str(row.get("title") or ""), 34)
            fact_family = "forecast"
            identity_label = "Program record"
            identity_value = str(row.get("record_id") or pick.ident)
            source_citation = (
                f"{source_labels.get(source, 'OFFICIAL PROGRAM')} · "
                f"{identity_value}"
            )
        else:
            value = fmt_day(pick.event_date)
            small = "INCUMBENT CONTRACT COMPLETES"
            agency_label = _compact_org(str(
                row.get("agency") or row.get("awarding_sub_agency")
                or row.get("awarding_agency") or "")).upper() \
                or "INCUMBENT"
            label = f"{agency_label} · EXPIRING"
            awardee = str(row.get("awardee") or row.get("recipient") or "")
            title = _short(awardee, 34)
            fact_record = _enriched_award_record(
                sweep, row, gid=pick.gid, award_id=pick.award_id)
            fact_family = "expiring"
            identity_value = pick.award_id or pick.ident
            source_citation = (
                f"USAspending award · {identity_value}"
                if pick.kind == "usaspending-expiring"
                or row.get("record_source") == "usaspending.gov"
                else f"SAM Contract Awards · {identity_value}"
            )
            if awardee:
                if pick.posture == "DEFEND":
                    subject_marks.append({
                        "kind": "client", "label": client_name,
                        "display": client_label,
                    })
                else:
                    subject_marks.append({
                        "kind": "company", "label": awardee,
                        "display": _display_company(awardee, 22),
                    })
        facts, missing_facts = _procurement_facts(
            fact_record, family=fact_family, identity_label=identity_label,
            identity_value=identity_value)
        pick.source_citation = source_citation
        pick.procurement_facts = facts
        pick.missing_procurement_facts = missing_facts
        organization_marks = []
        if pick.agency:
            organization_marks.append({
                "kind": "agency", "label": pick.agency,
                "display": label.split("·", 1)[0].strip(),
            })
        organization_marks.extend(subject_marks)
        relevance_verdict = (
            score_opportunity_evidence(
                sweep, [pick.gid], client_inputs["taxonomy"])
            if pick.kind == "award-window"
            else score_record(
                row, client_inputs["taxonomy"],
                engagement_scope=client_inputs["scope"])
        )
        relevance_basis = _verdict_client_relevance_basis(
            relevance_verdict,
            kind="record-core-match",
            source_identity=pick.ident,
            public_context={
                "band": "horizon",
                "client": client_label,
                "client_footprint": horizon_footprint,
                "event_kind": pick.kind,
            },
        )
        pick.client_relevance_basis = relevance_basis
        card = {"hot": i == 0, "label": label, "title": title,
                "value": value, "small": small,
                "decision": pick.posture,
                "source_citation": source_citation,
                "timing_basis": {
                    "source_value": (pick.timing_source_value
                                     or pick.event_date),
                    "precision": pick.timing_precision,
                    "sort_date": pick.timing_sort_date or pick.event_date,
                },
                "procurement_facts": facts,
                "missing_procurement_facts": missing_facts,
                "job_context": _horizon_job_context_public(
                    job_context_basis),
                "client_relevance": client_relevance_public(
                    relevance_basis),
                "agency_name": pick.agency,
                "organization_marks": organization_marks,
                # Forecast source id, calendar award id, or contract PIID
                "machine_evidence": {
                    "source_identity": pick.ident,
                    "source_kind": _HORIZON_SOURCE_KIND_BY_EVENT[pick.kind],
                    "quote": (pick.quote or str(
                        row.get("title") or row.get("description")
                        or row.get("awardee") or "")),
                    "agency": pick.agency,
                    "in_scope": pick.scope_ok,
                    "scope_basis": pick.scope_basis,
                    "job_context_basis": job_context_basis,
                    "client_relevance_basis": relevance_basis,
                    "decision": pick.posture,
                    "source_citation": source_citation,
                    "timing_basis": {
                        "source_value": (pick.timing_source_value
                                         or pick.event_date),
                        "precision": pick.timing_precision,
                        "sort_date": (
                            pick.timing_sort_date or pick.event_date),
                    },
                    "procurement_facts": facts,
                    "missing_procurement_facts": missing_facts,
                }}
        if pick.kind == "forecast" and row.get("source_id"):
            card["forecast_id"] = str(row.get("source_id"))
            card["machine_evidence"]["source_adapter"] = str(
                row.get("source") or "agency_forecast")
            if row.get("source") == "dhs_apfs":
                card["apfs_id"] = str(row.get("source_id"))
        elif pick.kind in {
                "award-window", "expiring", "usaspending-expiring"} \
                and pick.gid:
            card["award_generated_id"] = pick.gid
        elif pick.kind == "program":
            card["program_record_id"] = str(row.get("record_id") or "")
            card["program_route_identity"] = pick.ident
            card["url"] = str(row.get("canonical_url") or "")
            card["machine_evidence"]["source_adapter"] = str(
                row.get("_horizon_program_source") or row.get("source") or ""
            )
            card["machine_evidence"]["canonical_url"] = card["url"]
            card["machine_evidence"]["timing_value"] = card["value"]
            card["machine_evidence"]["timing_label"] = card["small"]
        horizon_cards.append(card)

    # The hero readout is the final pass over every publication card. It is
    # deterministic, uses no compose-line/LLM seam, and is rederived again by
    # staged validation and direct press before any bytes render. Distinct
    # forecast/completion records can therefore be surfaced without treating
    # an active-award period end as a separate opportunity.
    hero_sentences = _executive_summary_sentences(
        client_label, best_fit_cards, teaming_cards, horizon_cards)
    hero_context = " ".join(hero_sentences)
    prose_trail.extend({
        "kind": "executive-summary",
        "ident": f"sentence-{index:02d}",
        "source": "evidence-template:executive-summary",
        "line": sentence,
    } for index, sentence in enumerate(hero_sentences, 1))

    pocs = []
    for pick in best_fit:
        if pick.source_kind != "sam.gov":
            continue
        for contact in (pick.record.get("contacts") or [])[:1]:
            guid = notice_guid(pick.record)
            pocs.append({
                "opp": f"{pick.title[:44]} · {pick.agency[:24]}",
                "opp_notice_guid": guid,
                "name": str(contact.get("name") or ""),
                "role": str(contact.get("title") or "Primary POC"),
                "agency": pick.agency,
                "source_system": "SAM.gov",
                "source_record_id": guid,
                "deadline": fmt_day(pick.deadline) or "NO CURRENT DATE",
                "signal": "ACTIVE",
            })

    # Reviewed official leadership records live outside generated content so
    # a sanctioned re-compose cannot erase them. When notice contacts also
    # exist, the renderer keeps them in a separate, explicitly non-POC group.
    from agents.reports.board_content import (
        load_decision_makers,
        validate_decision_maker_routes,
    )

    reviewed_people = load_decision_makers(client_name)
    decision_makers = [
        row.model_dump(mode="json") for row in reviewed_people
    ]
    validate_decision_maker_routes(
        {
            "best_fit": best_fit_cards,
            "competitors": competitor_cards,
            "acquisition_pathways": acquisition_pathway_cards,
            "teaming": teaming_cards,
            "horizon": horizon_cards,
            "decision_makers": decision_makers,
        },
        expected_people=reviewed_people,
    )

    evidence = []
    seen_keys = set()
    for card in best_fit_cards:
        for ref in card.get("evidence") or []:
            key = ref.get("sam_notice_guid") or ref.get("award_generated_id")
            if key and key not in seen_keys:
                seen_keys.add(key)
                evidence.append(ref)
    for card in news + competitor_cards:
        gid = card.get("award_generated_id")
        if gid and gid not in seen_keys:
            seen_keys.add(gid)
            evidence.append({"award_generated_id": gid,
                             "label": card.get("head", card.get("title", ""))[:34].upper()})

    award_lead = award_picks[0] if award_picks else None
    footprint_lead = (min(footprint_picks, key=lambda pick: (
        pick.window_end, pick.gid)) if footprint_picks else None)
    lead = competitors[0] if competitors else None
    distinct_forward_candidates = [
        (event["sort"], card)
        for card in horizon_cards
        if (event := _summary_forward_event(card)) is not None
    ]
    distinct_forward = (
        min(distinct_forward_candidates, key=lambda row: row[0])[1]
        if distinct_forward_candidates else None)
    if distinct_forward:
        forward_agency = _compact_org(
            str(distinct_forward.get("label") or "").split("·", 1)[0])
        forward_timing = str(distinct_forward.get("value") or "").strip()
        forward_action = (
            f"validate the {forward_agency} cited acquisition path tied to "
            f"{forward_timing}; confirm vehicle, contracting office, "
            "competition path, and incumbent continuity before assigning "
            "pipeline value."
        )
    else:
        forward_action = (
            "treat cited award period ends as research triggers, not "
            "recompetes; confirm option status, vehicle, contracting office, "
            "and follow-on path."
        )
    sequence = (
        (f"Protect and expand the active footprint at "
         f"{_compact_org(footprint_lead.buyer)}; " if footprint_lead else
         f"Pursue the {_compact_org(award_lead.buyer)} displacement "
         f"corridor; " if award_lead else
         f"Lead with {_display_company(lead.recipient, 30)} at "
         f"{_compact_org(lead.buyer)}; " if lead else "")
        + ("prioritize the matched primes for teaming outreach; "
           if any(t.basis == "subaward-evidence" for t in teaming)
           else "resolve the cited acquisition-path access conditions "
                "before assigning partner, displacement, or direct-pursuit "
                "posture; " if acquisition_pathways
           else "close the named route-evidence gap before assigning "
                "partner or displacement posture; ")
        + forward_action)

    signal_cards = [{
        "label": "Priority routes", "value": str(len(best_fit)),
        "sub": "Verified federal records",
        "detail": ("Active award corridors" if award_lead
                   else "Current federal notices"),
    }]
    if footprint_lead:
        total_footprint = sum(pick.amount for pick in footprint_picks)
        award_word = "award" if len(footprint_picks) == 1 else "awards"
        signal_cards.append({
            "label": "Active footprint",
            "value": fmt_money(total_footprint),
            "sub": (f"Obligated across {len(footprint_picks)} active "
                    f"{award_word}"),
            "detail": (f"{_compact_org(footprint_lead.buyer)} · nearest PoP "
                       f"{fmt_day(footprint_lead.window_end)}"),
        })
    elif lead:
        signal_cards.append({
            "label": "Top corridor", "value": fmt_money(lead.amount),
            "sub": "Largest verified competitor award",
            "detail": (f"{_compact_org(lead.buyer)} · "
                       f"{_display_company(lead.recipient, 24)}")})
    nearest = horizon[0] if horizon else None
    nearest_value = "NONE"
    if nearest is not None:
        nearest_value = (fmt_day(nearest.event_date)
                         if nearest.timing_precision == "exact-date"
                         else nearest.timing_source_value)
    signal_cards.append({
        "label": "Forward timing signals", "value": str(len(horizon)),
        "sub": f"Cited events within up to {HORIZON_MONTHS} months",
        "detail": (
            f"Nearest {nearest_value} · {nearest.posture}"
            if nearest is not None else "No cited timing event selected"),
    })
    signal_cards.append({
        "label": "Coverage", "value": str(len(coverage)),
        "sub": "Federal organizations represented",
        "detail": " · ".join(c["dept"] for c in coverage[:4])})

    return {
        "client_name": client_name,
        "composition_mode": "machine",
        "hero_context": hero_context,
        "chips": chips,
        "news": news,
        "signal_cards": signal_cards,
        "coverage": coverage,
        "best_fit": best_fit_cards,
        "competitors": competitor_cards,
        "acquisition_pathways": acquisition_pathway_cards,
        "teaming": teaming_cards,
        "horizon": horizon_cards,
        "pocs": pocs,
        "decision_makers": decision_makers,
        "evidence": evidence,
        "sequence": sequence,
    }


# ── hero: component-summed scale + provenance-complete figures ──────────────

def build_hero(content: dict, competitors: list, teaming: list,
               sweep: dict, *, client_name: str,
               best_fit: Optional[list] = None,
               footprints: Optional[list] = None,
               acquisition_pathways: Optional[list] = None,
               horizon: Optional[list] = None) -> dict:
    """Attach the hero band and the figures registry.

    The marquee scale is a derived sum over competitor-held corridors when
    any exist. It falls back to client-footprint obligations only when no
    competitor corridor cleared publication. Client and competitor dollars
    are never blended into one headline. The shown rows are exactly the
    selected components, so the display derives its own total. Every rendered
    dollar on the board (corridor money, news bodies, teaming proofs, totals)
    registers a figure with raw taken verbatim from the sweep. Identity
    is structured-only: no record id is ever reconstructed from a URL;
    rows without one carry none and back by stored value. The composer
    never emits analyst rows."""
    client_label = client_display_name(client_name)
    results = sweep.get("results") or {}
    buyer_map = results.get("incumbent_buyer_map") or {}
    retrieved = _aware_iso(str(buyer_map.get("retrieved_at")
                               or sweep.get("generated_at") or ""))

    figures: list[dict] = []
    seen: set[tuple] = set()

    def register(raw: float, text: str, *, record_id: str = "",
                 gid: str = "", source_system: str = "usaspending",
                 source_url: str = "",
                 component_raws: Optional[list] = None,
                 retrieved_at_override: str = "") -> None:
        key = (round(raw, 2), record_id or gid or text)
        if key in seen:
            return
        seen.add(key)
        row: dict = {"text": text, "raw": raw,
                     "source_system": source_system,
                     "retrieved_at": (
                         _aware_iso(retrieved_at_override) or retrieved)}
        if record_id:
            row["source_record_id"] = record_id
        if gid:
            row["generated_internal_id"] = gid
        if source_url:
            row["source_url"] = source_url
        if component_raws:
            row["component_raws"] = component_raws
        figures.append(row)

    attack_rows = []
    attack_component_raws = []
    footprint_rows = []
    footprint_component_raws = []
    corridor_ids: set[str] = set()

    def add_corridor(label: str, amount: float, award_id: str, gid: str,
                     *, footprint: bool, amount_basis: str) -> None:
        if not gid or gid in corridor_ids:
            return
        if amount_basis != "obligated_to_date":
            raise ValueError(
                f"award corridor {gid} does not bind amount to "
                "obligated_to_date")
        corridor_ids.add(gid)
        # non-derived USAspending figures carry the full structured
        # provenance: PIID, generated id, system, retrieval stamp
        register(amount, fmt_money(amount), record_id=award_id, gid=gid)
        row = {
            "label": label,
            "amount": fmt_money(amount),
            "amount_basis": amount_basis,
            "award_generated_id": gid,
        }
        if footprint:
            footprint_component_raws.append(amount)
            footprint_rows.append(row)
        else:
            attack_component_raws.append(amount)
            attack_rows.append(row)

    for pick in footprints or []:
        party = (client_label if _is_client_company(
            pick.recipient, client_name) else
            f"{client_label} via {_display_company(pick.recipient, 26)}")
        add_corridor(
            f"{_compact_org(pick.buyer)} × {party}",
            pick.amount, pick.award_id, pick.gid, footprint=True,
            amount_basis=str(pick.record.get("amount_basis") or ""))
    for pick in best_fit or []:
        if pick.source_kind == "usaspending-award":
            footprint = _is_client_footprint(
                pick.recipient, pick.record, client_name)
            party = (client_label if _is_client_company(
                pick.recipient, client_name) else
                f"{client_label} via {_display_company(pick.recipient, 26)}"
                if footprint else _display_company(pick.recipient, 30))
            add_corridor(
                f"{_compact_org(pick.buyer)} × {party}",
                pick.amount, pick.award_id, pick.gid,
                footprint=footprint,
                amount_basis=str(pick.record.get("amount_basis") or ""))
    for lane in competitors:
        add_corridor(
            f"{_compact_org(lane.buyer)} × "
            f"{_display_company(lane.recipient, 30)}",
            lane.amount, lane.award_id, lane.gid, footprint=False,
            amount_basis=str(lane.record.get("amount_basis") or ""))

    # A publication-grade subaward route links its exact prime award.  Seed
    # that record in the same verified figure registry used by the press link
    # reconciliation gate, without adding its value to the marquee scale.
    for pick in teaming:
        if pick.basis != "subaward-evidence" or not pick.edges:
            continue
        edge = pick.edges[0]
        record = edge.get("_prime_award_record") or {}
        gid = str(edge.get("_prime_award_generated_id") or "")
        award_id = str(edge.get("prime_award_id") or "")
        if gid and gid not in corridor_ids:
            register(_num(record.get("amount")),
                     fmt_money(_num(record.get("amount"))),
                     record_id=award_id, gid=gid)

    def register_award_facts(pick, *, record: Optional[dict] = None,
                             gid: str, award_id: str) -> None:
        """Register only dollar facts that the composed card actually shows.

        The selected buyer-map record remains the authority for its stated
        obligated amount when no sanctioned re-pull exists.  When a re-pull
        does exist, ``_award_repull_record`` wins and supplies the current
        obligation/base-and-options values.  This mirrors the reconciliation
        gate's primary-record precedence instead of leaving richer card facts
        outside the figure registry.
        """
        facts = {
            str(row.get("value") or "")
            for row in getattr(pick, "procurement_facts", [])
            if isinstance(row, dict)
        }
        repull = _award_repull_record(
            sweep, gid=gid, award_id=award_id)
        authority = repull or record or {}
        for field in ("amount", "potential_ceiling"):
            raw = authority.get(field)
            if not isinstance(raw, (int, float)):
                continue
            display = fmt_money(float(raw))
            if display not in facts:
                continue
            register(
                float(raw), display, record_id=award_id, gid=gid,
                retrieved_at_override=str(
                    authority.get("retrieved_at") or ""),
            )

    def register_forecast_facts(record: dict, facts: list[dict],
                                *, identity: str = "") -> None:
        """Register agency-stated value boundaries without aggregating them."""

        facts_text = " ".join(
            str(row.get("value") or "")
            for row in facts
            if isinstance(row, dict)
        )
        identity = str(record.get("source_id") or identity)
        if not identity:
            return
        provenance = (results.get("forecast_signals") or {}).get(
            "_provenance") or {}
        source_adapter = str(record.get("source") or "")
        source_system = ("apfs" if source_adapter == "dhs_apfs"
                         else "agency_doc")
        for field in ("estimated_value_lower", "estimated_value_upper"):
            raw = record.get(field)
            if (not isinstance(raw, (int, float))
                    or isinstance(raw, bool)):
                continue
            display = fmt_money(float(raw))
            if display not in facts_text:
                continue
            register(
                float(raw), display, record_id=identity,
                source_system=source_system,
                source_url=str(record.get("url") or ""),
                retrieved_at_override=str(
                    record.get("retrieved_at")
                    or provenance.get("retrieved_at") or ""),
            )

    for pick in footprints or []:
        register_award_facts(
            pick, record=pick.record, gid=str(pick.gid or ""),
            award_id=str(pick.award_id or ""))
    for pick in best_fit or []:
        if pick.source_kind == "usaspending-award":
            register_award_facts(
                pick, record=pick.record, gid=str(pick.gid or ""),
                award_id=str(pick.award_id or ""))
    for pick in competitors:
        register_award_facts(
            pick, record=pick.record, gid=str(pick.gid or ""),
            award_id=str(pick.award_id or ""))
    for pick in acquisition_pathways or []:
        if pick.source_kind == "usaspending-award":
            register_award_facts(
                pick, record=pick.record, gid=str(pick.gid or ""),
                award_id=str(pick.award_id or ""))
    for pick in teaming:
        if pick.basis == "subaward-evidence" and pick.edges:
            edge = pick.edges[0]
            gid = str(edge.get("_prime_award_generated_id") or "")
            award_id = str(edge.get("prime_award_id") or "")
        else:
            gid = str(pick.play.get("generated_internal_id") or "")
            award_id = str(pick.play.get("award_id") or "")
        register_award_facts(
            pick, record=(edge.get("_prime_award_record") or {})
            if pick.basis == "subaward-evidence" and pick.edges
            else pick.play,
            gid=gid, award_id=award_id)
    contract_retrieved = str(
        ((results.get("contract_awards") or {}).get("_provenance") or {}).get(
            "retrieved_at") or "")
    for pick in horizon or []:
        if pick.kind == "forecast":
            register_forecast_facts(
                pick.row, pick.procurement_facts, identity=pick.ident)
        if pick.gid:
            register_award_facts(
                pick, record=pick.row, gid=str(pick.gid),
                award_id=str(pick.award_id or ""))
        if pick.kind != "expiring":
            continue
        raw = pick.row.get("amount")
        display = fmt_money(float(raw)) if isinstance(raw, (int, float)) else ""
        if display and display in {
                str(row.get("value") or "")
                for row in pick.procurement_facts
                if isinstance(row, dict)}:
            register(
                float(raw), display, record_id=str(pick.award_id or pick.ident),
                source_system="sam",
                retrieved_at_override=contract_retrieved,
            )
    if horizon is None:
        # Backward-compatible callers may supply composed content without the
        # selection objects. Recover only exact record identities from the
        # machine evidence; never infer a source from display copy.
        contract_rows = (results.get("contract_awards") or {}).get(
            "recompetes") or []
        for card in content.get("horizon") or []:
            machine = card.get("machine_evidence") or {}
            identity = str(machine.get("source_identity") or "")
            source_kind = str(machine.get("source_kind") or "")
            facts = {
                str(row.get("value") or "")
                for row in (card.get("procurement_facts") or [])
                if isinstance(row, dict)
            }
            if source_kind in {
                    "usaspending-award", "usaspending-expiring-award"}:
                repull = _award_repull_record(sweep, gid=identity)
                corridor = _award_record_gid_index(results).get(identity, {})
                authority = repull or corridor
                award_id = str(authority.get("award_id") or "")
                for field in ("amount", "potential_ceiling"):
                    raw = authority.get(field)
                    if not isinstance(raw, (int, float)):
                        continue
                    display = fmt_money(float(raw))
                    if display in facts:
                        register(
                            float(raw), display, record_id=award_id,
                            gid=identity,
                            retrieved_at_override=str(
                                authority.get("retrieved_at") or ""),
                        )
            elif source_kind == "contract-award":
                matches = [row for row in contract_rows
                           if str(row.get("piid") or "") == identity]
                if len(matches) != 1:
                    continue
                raw = matches[0].get("amount")
                display = (fmt_money(float(raw))
                           if isinstance(raw, (int, float)) else "")
                if display and display in facts:
                    register(
                        float(raw), display, record_id=identity,
                        source_system="sam",
                        retrieved_at_override=contract_retrieved,
                    )
            elif source_kind == "agency-forecast":
                matches = [
                    row for row in (
                        (results.get("forecast_signals") or {}).get(
                            "matched") or [])
                    if str(row.get("source_id") or "") == identity
                ]
                if len(matches) == 1:
                    register_forecast_facts(
                        matches[0], card.get("procurement_facts") or [],
                        identity=identity)

    if footprint_component_raws:
        footprint_total = round(sum(footprint_component_raws), 2)
        register(
            footprint_total,
            fmt_money(footprint_total),
            source_system="usaspending",
            source_url="https://www.usaspending.gov/",
            component_raws=footprint_component_raws,
        )

    rows = attack_rows or footprint_rows
    component_raws = (
        attack_component_raws if attack_rows else footprint_component_raws)
    scale_label = (
        "Competitor-held obligated history"
        if attack_rows else "Client-footprint obligated history")
    total_raw = round(sum(component_raws), 2)
    total_text = fmt_money(total_raw)
    if component_raws:
        # derived sum: component_raws + the established aggregate-source
        # convention (usaspending root url); never one record's identity
        register(total_raw, total_text, source_system="usaspending",
                 source_url="https://www.usaspending.gov/",
                 component_raws=component_raws)

    content["scale"] = {
        "rows": rows,
        "total": total_text,
        "basis": ("competitor_obligated_to_date"
                  if attack_rows else "client_obligated_to_date"),
    }
    content["scale_label"] = scale_label
    content["scale_total"] = total_text
    content["scale_counts"] = (
        f"Across {len(rows)} cited active award "
        f"{'corridor' if len(rows) == 1 else 'corridors'}")
    content["figures"] = figures
    return content


# ── INTERNAL compose trail: what was selected, why, who wrote each line ─────

def _teaming_trail_row(pick: TeamingPick) -> dict:
    route_basis = _teaming_route_basis(pick)
    public = _teaming_public_fields(route_basis)
    edge = pick.edges[0] if pick.edges else None
    return {
        "ident": pick.partner,
        "card_identity": public["source_identity"],
        "basis": pick.basis,
        "why": pick.why,
        "quote": (str(edge.get("description") or "") if edge else
                  str(pick.play.get("description") or "")),
        "play_award_id": (str(edge.get("prime_award_id") or "")
                          if edge else
                          str(pick.play.get("award_id") or "")),
        "corridor_award_id": ("" if edge else
                              str(pick.play.get("award_id") or "")),
        "corridor_gid": ("" if edge else
                         str(pick.play.get("generated_internal_id") or "")),
        "subaward_id": (str(edge.get("subaward_id") or "")
                        if edge else ""),
        "prime_award_gid": (str(
            edge.get("_prime_award_generated_id") or "") if edge else ""),
        "route_basis": route_basis,
        "client_relevance_basis": pick.client_relevance_basis,
        "procurement_facts": pick.procurement_facts,
        "missing_procurement_facts": pick.missing_procurement_facts,
        "in_scope": pick.scope_ok,
        "scope_basis": pick.scope_basis,
    }


def build_trail(client_name: str, *, best_fit: list, competitors: list,
                teaming: list, acquisition_pathways: Optional[list] = None,
                horizon: list, prose_trail: list,
                today: date, scope=None,
                selection_findings: Optional[list[str]] = None) -> dict:
    """The INTERNAL sidecar: selection whys with quoted evidence spans,
    every prose line with its source, and named findings for anything the
    data refused to support. Never leaves the shop."""
    findings = []
    acquisition_pathways = list(acquisition_pathways or [])
    if not best_fit:
        findings.append(
            "best_fit is empty: no non-discarded sam.gov notice or active "
            "award corridor carries core-term evidence under the "
            "machine-screened bar")
    if not competitors:
        findings.append("no forward-looking capability-matched corridor "
                        "lanes in incumbent_buyer_map")
    if not any(t.basis == "subaward-evidence" for t in teaming):
        suffix = (
            "; Section 03 publishes acquisition-pathway research instead"
            if acquisition_pathways else
            "; Section 03 has no publishable fallback route")
        findings.append(
            "no distinct prime carries a publication-grade same-play "
            "subaward route with matching agency, shared client-core "
            "evidence, an official subaward identity, and a canonical "
            "prime-award GID" + suffix)
    findings.extend(selection_findings or [])
    counts: dict[str, int] = {}
    for line in prose_trail:
        bucket = line["source"].split(":", 1)[0]
        counts[bucket] = counts.get(bucket, 0) + 1
    return {
        "internal": "COMPOSE TRAIL · INTERNAL · never leaves the shop",
        "client_name": client_name,
        "composed_on": today.isoformat(),
        "selections": {
            "best_fit": [{
                "ident": p.source_id,
                "card_identity": (p.gid if p.source_kind ==
                                  "usaspending-award"
                                  else notice_guid(p.record)),
                "source_kind": p.source_kind,
                "why": p.why,
                "relevant": p.verdict.relevant,
                "in_scope": not p.verdict.off_scope,
                "scope_basis": p.verdict.scope_basis,
                "award_id": (p.award_id if p.source_kind ==
                             "usaspending-award" else ""),
                "generated_internal_id": (
                    p.gid if p.source_kind == "usaspending-award" else ""),
                "date_basis": (p.window_end if p.source_kind ==
                               "usaspending-award" else p.deadline),
                "triage_verdict": (p.triage_verdict if p.source_kind ==
                                   "sam.gov" else ""),
                "triage_reason": (p.triage_reason if p.source_kind ==
                                   "sam.gov" else ""),
                "procurement_facts": p.procurement_facts,
                "missing_procurement_facts": p.missing_procurement_facts,
                "quoted_spans": [
                    {"term": s.term, "tier": s.tier, "field": s.field,
                     "quote": s.context}
                    for s in p.verdict.spans[:6]],
            } for p in best_fit],
            "competitors": [{
                "ident": f"{c.recipient} · {c.buyer}",
                "card_identity": c.gid,
                "why": c.why,
                "award_id": c.award_id,
                "generated_internal_id": c.gid,
                "quote": str(c.record.get("description") or ""),
                "client_relevance_basis": c.client_relevance_basis,
                "procurement_facts": c.procurement_facts,
                "missing_procurement_facts": c.missing_procurement_facts,
                "in_scope": c.scope_ok,
                "scope_basis": c.scope_basis,
            } for c in competitors],
            "acquisition_pathways": [{
                "ident": p.source_identity,
                "card_identity": p.source_identity,
                "why": p.why,
                "source_kind": p.source_kind,
                "award_id": p.award_id,
                "generated_internal_id": p.gid,
                "notice_guid": p.notice_guid,
                "pathway_basis": _acquisition_pathway_basis(
                    p, p.procurement_facts,
                    p.missing_procurement_facts),
                "quote": _acquisition_requirement_excerpt(
                    p.record, p.capability),
                "client_relevance_basis": p.client_relevance_basis,
                "procurement_facts": p.procurement_facts,
                "missing_procurement_facts":
                    p.missing_procurement_facts,
                "in_scope": p.scope_ok,
                "scope_basis": p.scope_basis,
            } for p in acquisition_pathways],
            "teaming": [_teaming_trail_row(t) for t in teaming],
            "horizon": [{
                "ident": h.ident, "card_identity": h.ident,
                "kind": h.kind, "why": h.why,
                "date_basis": h.event_date,
                "program_source_adapter": (
                    str(h.row.get("_horizon_program_source") or "")
                    if h.kind == "program" else ""),
                "program_record_id": (
                    str(h.row.get("record_id") or "")
                    if h.kind == "program" else ""),
                "canonical_url": (
                    str(h.row.get("canonical_url") or "")
                    if h.kind == "program" else ""),
                "program_timing_value": (
                    _program_public_timing_value(h.event_date)
                    if h.kind == "program" else ""),
                "program_timing_label": (
                    str(h.row.get("_horizon_timing_label") or "")
                    if h.kind == "program" else ""),
                "job_context_basis": _horizon_job_context_basis(h),
                "client_relevance_basis": h.client_relevance_basis,
                "decision": h.posture,
                "source_citation": h.source_citation,
                "timing_basis": {
                    "source_value": h.timing_source_value or h.event_date,
                    "precision": h.timing_precision,
                    "sort_date": h.timing_sort_date or h.event_date,
                },
                "procurement_facts": h.procurement_facts,
                "missing_procurement_facts": h.missing_procurement_facts,
                "award_id": h.award_id,
                "generated_internal_id": h.gid,
                "quote": (h.quote or str(h.row.get("title")
                                          or h.row.get("description")
                                          or h.row.get("awardee") or "")),
                "in_scope": h.scope_ok,
                "scope_basis": h.scope_basis,
            } for h in horizon],
        },
        "prose": {"lines": prose_trail, "counts": counts},
        "named_findings": findings,
    }


def trail_path(client_name: str) -> str:
    """The INTERNAL compose trail: markdown, beside the content, with
    'compose' in its path and .md so the C3 adapter tokens and validates
    it (first nonempty line '# INTERNAL' + exact display client name)."""
    from agents.reports.board_content import content_path
    return content_path(client_name).replace(
        "signal_board_content.json", "compose_trail.md")


def compose_trail_header(display_name: str) -> str:
    """The ONE header grammar shared by producer and the C3 adapter
    consumer (assessment_chain.COMPOSE_TRAIL_HEADER_RE)."""
    return f"# INTERNAL compose trail · {display_name} · never leaves the shop"


def pair_binding_line(content_sha256: str) -> str:
    """The trail's FINAL line binds the exact content generation it was
    published beside; assessment_chain.COMPOSE_PAIR_BINDING_RE is the
    consumer grammar. A crash between the trail publish and the content
    commit leaves a DETECTABLE mixed pair, never an accepted one."""
    return f"pair content sha256 {content_sha256}"


def assert_trail_header(trail_md: str, display_name: str) -> None:
    """Exact-grammar check; substring identity is insufficient."""
    from agents.assessment_chain import COMPOSE_TRAIL_HEADER_RE
    first = next((ln for ln in trail_md.splitlines() if ln.strip()), "")
    match = COMPOSE_TRAIL_HEADER_RE.fullmatch(first.strip())
    if not match or match.group("client") != display_name:
        raise ValueError(
            f"trail header {first!r} does not satisfy the shared grammar "
            f"for client {display_name!r}")


def validate_machine_evidence(
        content: dict, trail: dict, *, taxonomy=None, profile=None,
        sweep: Optional[dict] = None,
        calendar: Optional[dict] = None) -> None:
    """Prepublication evidence law for machine-composed content: every
    auto-selected item carries exact source evidence or the compose
    refuses. Raises ValueError naming the first defect."""
    def _require_positive_scope(band: str, row: dict) -> None:
        # a nonempty string called scope_basis is not proof of being in
        # scope: the POSITIVE verdict must ride beside its basis
        if (row.get("in_scope") is not True
                or not row.get("scope_basis")
                or str(row["scope_basis"]).startswith("off-scope")):
            raise ValueError(
                f"{band} selection {row.get('ident')!r} lacks a positive "
                "in-scope verdict and its basis")

    for card in content.get("best_fit") or []:
        refs = card.get("evidence") or []
        identities = [
            (key, ref[key]) for ref in refs
            for key in ("sam_notice_guid", "award_generated_id")
            if ref.get(key)
        ]
        if len(identities) != 1:
            raise ValueError(
                f"best_fit card {card.get('title')!r} must carry exactly "
                "one structured SAM notice or USAspending award identity")
    for row in trail["selections"]["best_fit"]:
        if not row.get("quoted_spans"):
            raise ValueError(
                f"best_fit selection {row.get('ident')!r} carries no "
                "quoted spans")
        if not row.get("relevant"):
            raise ValueError(
                f"best_fit selection {row.get('ident')!r} is not "
                "engine-relevant")
        _require_positive_scope("best_fit", row)
        if row.get("source_kind") == "usaspending-award" and not (
                row.get("award_id") and row.get("generated_internal_id")
                and row.get("date_basis")):
            raise ValueError(
                f"best_fit award corridor {row.get('ident')!r} lacks its "
                "PIID, generated identity, or PoP end")

    for card in content.get("competitors") or []:
        if not card.get("award_generated_id"):
            raise ValueError(
                f"competitor lane {card.get('title')!r} lacks structured "
                "award identity")
    for row in trail["selections"]["competitors"]:
        if not (row.get("quote") and row.get("award_id")
                and row.get("generated_internal_id")):
            raise ValueError(
                f"competitor selection {row.get('ident')!r} lacks its "
                "buyer-record quote or identity")
        _require_positive_scope("competitor", row)
    for row in trail["selections"].get("acquisition_pathways", []):
        if not (row.get("quote") and row.get("card_identity")
                and row.get("source_kind") in {
                    "usaspending-award", "sam.gov"}):
            raise ValueError(
                f"acquisition pathway {row.get('ident')!r} lacks its "
                "source quote, identity, or sanctioned source kind")
        if row.get("source_kind") == "usaspending-award" and not (
                row.get("award_id") and row.get("generated_internal_id")):
            raise ValueError(
                f"acquisition pathway {row.get('ident')!r} lacks its "
                "award PIID or canonical GID")
        if row.get("source_kind") == "sam.gov" and not row.get(
                "notice_guid"):
            raise ValueError(
                f"acquisition pathway {row.get('ident')!r} lacks its "
                "SAM notice GUID")
        _require_positive_scope("acquisition pathway", row)
    for row in trail["selections"]["teaming"]:
        if not row.get("quote"):
            raise ValueError(
                f"teaming selection {row.get('ident')!r} carries no "
                "evidence quote")
        _require_positive_scope("teaming", row)
        if row.get("basis") == "subaward-evidence" \
                and not (row.get("play_award_id")
                         and row.get("subaward_id")
                         and row.get("prime_award_gid")):
            raise ValueError(
                f"teaming selection {row.get('ident')!r} lacks its native "
                "subaward identity, prime award PIID, or canonical prime "
                "award GID")
        if row.get("basis") == "entry-thesis" and not (
                row.get("corridor_award_id") and row.get("corridor_gid")):
            raise ValueError(
                f"teaming selection {row.get('ident')!r} is an entry "
                "thesis without its corridor record citation; entry "
                "theses are never an evidence-free exception")
    for row in trail["selections"]["horizon"]:
        if not (row.get("quote") and row.get("job_context_basis")
                and row.get("date_basis")
                and row.get("scope_basis") and row.get("source_citation")
                and row.get("decision") in {"ATTACK", "DEFEND", "QUALIFY"}
                and isinstance(row.get("timing_basis"), dict)
                and row["timing_basis"].get("source_value")
                and row["timing_basis"].get("sort_date")
                and row["timing_basis"].get("precision")):
            raise ValueError(
                f"horizon selection {row.get('ident')!r} lacks its record "
                "quote, job-context basis, cited timing, decision, source, "
                "or scope basis")
        if row.get("kind") == "award-window" and not (
                row.get("award_id") and row.get("generated_internal_id")):
            raise ValueError(
                f"horizon award window {row.get('ident')!r} lacks its "
                "PIID or generated identity")
        _require_positive_scope("horizon", row)

    # every machine card carries its own bound evidence, and the trail
    # binds ONE-TO-ONE to the content cards by stable source identity:
    # equal cardinality, no duplicates, exact matches. Extra content
    # cards and extra trail selections both fail.
    for band in (
            "best_fit", "competitors", "acquisition_pathways", "teaming",
            "horizon"):
        card_ids = []
        for card in content.get(band) or []:
            evidence = card.get("machine_evidence")
            if not (isinstance(evidence, dict)
                    and evidence.get("source_identity")
                    and evidence.get("source_kind")
                    and evidence.get("quote")
                    and evidence.get("in_scope") is True
                    and evidence.get("scope_basis")):
                raise ValueError(
                    f"{band} card "
                    f"{card.get('title') or card.get('partner') or card.get('label')!r} "
                    "lacks its bound machine evidence (identity, kind, "
                    "quote, or positive scope proof)")
            card_ids.append(str(evidence["source_identity"]))
        trail_rows = trail["selections"].get(band, [])
        trail_ids = [str(row.get("card_identity") or "")
                     for row in trail_rows]
        if len(set(card_ids)) != len(card_ids):
            raise ValueError(f"{band} cards carry duplicate source identities")
        if len(set(trail_ids)) != len(trail_ids):
            raise ValueError(
                f"{band} trail selections carry duplicate source identities")
        if sorted(card_ids) != sorted(trail_ids):
            raise ValueError(
                f"{band} trail does not bind one-to-one to the content "
                f"cards: cards {sorted(card_ids)!r} vs trail "
                f"{sorted(trail_ids)!r}")
        trail_by_id = {
            str(row["card_identity"]): row
            for row in trail_rows
        }
        for card in content.get(band) or []:
            machine = card["machine_evidence"]
            trail_row = trail_by_id[str(machine["source_identity"])]
            if (card.get("procurement_facts")
                    != machine.get("procurement_facts")
                    or card.get("missing_procurement_facts")
                    != machine.get("missing_procurement_facts")
                    or machine.get("procurement_facts")
                    != trail_row.get("procurement_facts")
                    or machine.get("missing_procurement_facts")
                    != trail_row.get("missing_procurement_facts")):
                raise ValueError(
                    f"{band} card procurement facts do not match its "
                    "trail-bound source projection")
        if band == "best_fit":
            for card in content.get("best_fit") or []:
                machine = card["machine_evidence"]
                if machine.get("source_kind") != "sam.gov":
                    continue
                trail_row = trail_by_id[str(machine["source_identity"])]
                verdict = str(machine.get("triage_verdict") or "")
                reason = str(machine.get("triage_reason") or "")
                if (verdict != str(trail_row.get("triage_verdict") or "")
                        or reason != str(trail_row.get("triage_reason") or "")):
                    raise ValueError(
                        f"best_fit card {card.get('title')!r} triage verdict "
                        "or reason does not match its trail-bound selection")
                if verdict and f" · {verdict.upper()} · " not in str(
                        card.get("account") or ""):
                    raise ValueError(
                        f"best_fit card {card.get('title')!r} does not "
                        "surface its bound triage verdict")
        elif band == "acquisition_pathways":
            for card in content.get("acquisition_pathways") or []:
                machine = card["machine_evidence"]
                identity = str(machine["source_identity"])
                trail_row = trail_by_id[identity]
                pathway_basis = trail_row.get("pathway_basis")
                if (not isinstance(pathway_basis, dict)
                        or machine.get("pathway_basis") != pathway_basis):
                    raise ValueError(
                        "acquisition pathway structured basis does not "
                        "match its trail-bound source fields")
                validate_machine_acquisition_pathway_card(card)
                if machine.get("quote") != trail_row.get("quote"):
                    raise ValueError(
                        "acquisition pathway quote does not match its "
                        "trail-bound source quote")
        elif band == "teaming":
            for card in content.get("teaming") or []:
                machine = card["machine_evidence"]
                identity = str(machine["source_identity"])
                trail_row = trail_by_id[identity]
                route_basis = trail_row.get("route_basis")
                if (not isinstance(route_basis, dict)
                        or machine.get("route_basis") != route_basis):
                    raise ValueError(
                        f"teaming card {card.get('partner')!r} route basis "
                        "does not match its trail-bound source fields")
                try:
                    validate_machine_teaming_card(card)
                except ValueError as exc:
                    raise ValueError(
                        f"teaming card {card.get('partner')!r} public route "
                        "copy or canonical citation was changed after "
                        f"deterministic composition: {exc}") from exc
                if machine.get("quote") != trail_row.get("quote"):
                    raise ValueError(
                        f"teaming card {card.get('partner')!r} quote does "
                        "not match its trail-bound source quote")
        elif band == "horizon":
            for card in content.get("horizon") or []:
                machine = card["machine_evidence"]
                identity = str(machine["source_identity"])
                trail_row = trail_by_id[identity]
                if (machine.get("quote") != trail_row.get("quote")
                        or machine.get("job_context_basis")
                        != trail_row.get("job_context_basis")
                        or machine.get("decision")
                        != trail_row.get("decision")
                        or machine.get("source_citation")
                        != trail_row.get("source_citation")
                        or machine.get("timing_basis")
                        != trail_row.get("timing_basis")
                        or card.get("decision") != machine.get("decision")
                        or card.get("source_citation")
                        != machine.get("source_citation")
                        or card.get("timing_basis")
                        != machine.get("timing_basis")):
                    raise ValueError(
                        f"horizon card {card.get('title')!r} quote, timing, "
                        "decision, source, or work description does not "
                        "match its trail-bound source")
                if machine.get("source_kind") == "program":
                    trail_binding = {
                        "source_adapter":
                            trail_row.get("program_source_adapter"),
                        "record_id": trail_row.get("program_record_id"),
                        "canonical_url": trail_row.get("canonical_url"),
                        "timing_value":
                            trail_row.get("program_timing_value"),
                        "timing_label":
                            trail_row.get("program_timing_label"),
                    }
                    if _program_card_binding(card) != trail_binding:
                        raise ValueError(
                            f"horizon card {card.get('title')!r} PROGRAM "
                            "adapter, record, URL, or timing does not match "
                            "its trail-bound source")
                try:
                    validate_machine_horizon_card(card)
                except ValueError as exc:
                    raise ValueError(
                        f"horizon card {card.get('title')!r} public work "
                        "description changed after deterministic composition: "
                        f"{exc}") from exc

    validate_machine_route_band_contract(content)
    validate_machine_client_relevance_contract(
        content,
        trail_md=render_trail_md(
            trail, str(content.get("client_name") or "")),
        taxonomy=taxonomy,
        profile=profile,
        sweep=sweep,
        calendar=calendar,
    )
    validate_machine_executive_summary_contract(content)
    expected_summary_lines = _executive_summary_sentences(
        str(content.get("client_name") or ""),
        list(content.get("best_fit") or []),
        list(content.get("teaming") or []),
        list(content.get("horizon") or []),
    )
    actual_summary_lines = [
        str(row.get("line") or "")
        for row in (trail.get("prose") or {}).get("lines") or []
        if row.get("kind") == "executive-summary"
    ]
    if actual_summary_lines != expected_summary_lines:
        raise ValueError(
            "executive summary prose trail does not match the exact "
            "evidence-derived sentences")


def render_trail_md(trail: dict, display_name: str) -> str:
    """Adapter-facing rendering of the trail dict. The header line is the
    C1/C3 handshake: the shared exact grammar."""
    lines = [
        compose_trail_header(display_name),
        f"composed {trail['composed_on']} · rules in "
        "agents/reports/composer.py · selections below carry their why",
        "",
    ]
    for section, rows in trail["selections"].items():
        lines.append(f"## {section}")
        if not rows:
            lines.append("- none selected")
        for row in rows:
            lines.append(f"- {row['ident']} · {row.get('why', '')}"
                         + (f" · basis {row['basis']}" if row.get("basis")
                            else ""))
            for span in row.get("quoted_spans") or []:
                lines.append(f"  - span [{span['tier']}:{span['term']}] "
                             f"{span['field']}: \"{span['quote']}\"")
            # every selected record's evidence renders: identity, quote,
            # dates, and the scope verdict with its basis
            identity = []
            if row.get("award_id"):
                identity.append(f"award {row['award_id']}")
            if row.get("generated_internal_id"):
                identity.append(f"gid {row['generated_internal_id']}")
            if row.get("notice_guid"):
                identity.append(f"notice {row['notice_guid']}")
            if row.get("play_award_id"):
                identity.append(f"play award {row['play_award_id']}")
            if row.get("corridor_award_id"):
                identity.append(
                    f"corridor award {row['corridor_award_id']}")
            if row.get("corridor_gid"):
                identity.append(f"corridor gid {row['corridor_gid']}")
            if identity:
                lines.append("  - identity " + " · ".join(identity))
            if row.get("quote"):
                lines.append(f"  - quote: \"{row['quote']}\"")
            if row.get("job_context_basis"):
                envelope = {
                    "basis": row["job_context_basis"],
                    "card_identity": row["card_identity"],
                }
                lines.append(
                    _HORIZON_TRAIL_CONTEXT_PREFIX
                    + json.dumps(envelope, ensure_ascii=False,
                                 separators=(",", ":"), sort_keys=True))
            if row.get("timing_basis"):
                decision_envelope = {
                    "binding": {
                        "decision": row["decision"],
                        "source_citation": row["source_citation"],
                        "timing_basis": row["timing_basis"],
                        "procurement_facts": row["procurement_facts"],
                        "missing_procurement_facts":
                            row["missing_procurement_facts"],
                    },
                    "card_identity": row["card_identity"],
                }
                lines.append(
                    _HORIZON_DECISION_TRAIL_PREFIX
                    + json.dumps(
                        decision_envelope, ensure_ascii=False,
                        separators=(",", ":"), sort_keys=True))
            if row.get("program_source_adapter"):
                program_envelope = {
                    "binding": {
                        "source_adapter": row["program_source_adapter"],
                        "record_id": row["program_record_id"],
                        "canonical_url": row["canonical_url"],
                        "timing_value": row["program_timing_value"],
                        "timing_label": row["program_timing_label"],
                    },
                    "card_identity": row["card_identity"],
                }
                lines.append(
                    _HORIZON_PROGRAM_TRAIL_PREFIX
                    + json.dumps(
                        program_envelope, ensure_ascii=False,
                        separators=(",", ":"), sort_keys=True))
            if row.get("client_relevance_basis"):
                relevance_envelope = {
                    "band": section,
                    "basis": row["client_relevance_basis"],
                    "card_identity": row["card_identity"],
                }
                lines.append(
                    _CLIENT_RELEVANCE_TRAIL_PREFIX
                    + json.dumps(relevance_envelope, ensure_ascii=False,
                                 separators=(",", ":"), sort_keys=True))
            if row.get("kind"):
                lines.append(f"  - kind {row['kind']}")
            if row.get("date_basis"):
                lines.append(f"  - date basis {row['date_basis']}")
            if row.get("scope_basis"):
                verdict = ("in-scope"
                           if row.get("in_scope", row.get("relevant"))
                           else "OFF-SCOPE")
                lines.append(
                    f"  - scope {verdict} · basis {row['scope_basis']}")
        lines.append("")
    counts = trail["prose"]["counts"]
    lines.append("## prose")
    lines.append(f"- counts: {counts}")
    for entry in trail["prose"]["lines"]:
        lines.append(f"- [{entry['source']}] {entry['kind']}.{entry['ident']}"
                     f": {entry['line']}")
    lines.append("")
    lines.append("## named findings")
    findings = trail["named_findings"] or ["none"]
    for finding in findings:
        lines.append(f"- {finding}")
    return "\n".join(lines) + "\n"
