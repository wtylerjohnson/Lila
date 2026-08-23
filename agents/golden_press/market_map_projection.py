"""Legacy internal Market Map projection and graph-to-product source adapter.

WHAT THIS CHANGES. Today the renderer derives sections from the pack inline,
which is why the same opportunity could appear in three sections and why
prose and evidence interleave. This projection is assembled FIRST, from the
EvidencePack, the targeting store and verified event evidence, and the
renderer receives it complete. Single appearance stops being renderer
discipline and becomes a property of the data: `opportunity_key` is a set
key, so a duplicate cannot be constructed.

EIGHT INTERNAL ANSWER SETS used to assemble governed source facts:

  company_understanding   what they sell and how we bounded the research
  category_footprint      what the government spent, by whom, through whom
  competitive_position    real product rivals, validated, with awards
  qualified_opportunities one row per canonical opportunity, ever
  teaming_routes          paper holders and the motion each unlocks
  contact_actions         who to call, by motion, with a grade
  events                  where the buyers will be
  research_demand         typed absence, computed before prose

This object does not define the external slot order. The locked eight-slot
projection in ``external_product_projection.py`` consumes it alongside the
certified Federal Pursuit Graph.

DETERMINISTIC END TO END. No model call in this module. Promotion,
deduplication, money and source roles are all rules over the pack. The
language model's only job downstream is one bounded sentence per section
written FROM these typed facts, and it cannot select, qualify, dedupe,
calculate, name, date or link.

SOURCE ROLES ARE THE SPINE. An award proves spend and a paper holder; a
notice or forecast proves an opportunity; an official event page proves an
event; a notice POC or the targeting store proves a contact. The evidence
objects enforce it, so a teaming route built from an award literally cannot
claim to be an open opportunity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from agents.golden_press.evidence_objects import (
    AWARD, CLAIM_AWARD, CLAIM_CONTACT, CLAIM_EVENT, CLAIM_OPPORTUNITY,
    CLAIM_PAPER_HOLDER,
    CLAIM_RECIPIENT, CLAIM_SPEND, EVENT, FORECAST, NOTICE, CoverageState,
    EvidenceReference, ExactMoney, complete, partial, research_next)

MARKET_MAP_PROJECTION_VERSION = "market_map_projection.v1.2026-08-07"

_HEX = re.compile(r"^[0-9a-f]{32}$", re.I)

_LIVE = object()          # sentinel: no capture supplied, read the stores

SECTION_ORDER = ("company", "market", "competition", "opportunities",
                 "teaming", "contacts", "events")

# Typed research demand. A key here is a JOB, not an apology.
DEMAND_COMPETITOR_VALIDATION = "competitor_award_validation"
DEMAND_OPPORTUNITY_VALUE = "opportunity_value_research"
DEMAND_DIRECT_PHONE = "direct_phone_enrichment"
DEMAND_PARTNER_CONTACT = "named_partner_contact"
DEMAND_EVENT_CONFIRMATION = "official_event_confirmation"
DEMAND_PROFILE_CONFIRMATION = "operator_company_profile_confirmation"

# Motions an opportunity or contact can carry. Derived, never model-written.
PRIME = "prime"
SUBCONTRACT = "subcontract"
TEAM = "team"
SHAPE = "shape"
MONITOR = "monitor"
STATUS_CHECK = "status check"

# What each notice type means commercially. The report must say "a response
# is required" for an RFP and "the scope is still being written" for an RFI,
# because those are different jobs on different clocks.
_SHAPING_TYPES = ("sources sought", "request for information", "rfi",
                  "special notice", "presolicitation", "industry day")
_ACTIVE_TYPES = ("solicitation", "combined synopsis", "request for proposal",
                 "rfp", "request for quote", "rfq")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).casefold())


def opportunity_key(source_kind: str, identifier: Any) -> str:
    """THE canonical identity. One opportunity, one key, one appearance."""
    return f"{source_kind}:{_norm(identifier)}"


@dataclass
class CompanyUnderstanding:
    client_name: str
    description: str = ""
    products: tuple = ()
    channels: tuple = ()
    supplied_keywords: tuple = ()
    researched_keywords: tuple = ()
    pending_keywords: tuple = ()
    rejected_keywords: tuple = ()
    naics: tuple = ()
    federal_footprint: str = ""
    posture: str = ""
    coverage: Optional[CoverageState] = None
    confirmations: tuple = ()


@dataclass
class CategoryFootprint:
    total: Optional[ExactMoney] = None
    by_agency: tuple = ()          # (agency, ExactMoney, record_count)
    by_holder: tuple = ()          # (recipient, ExactMoney, record_count)
    by_segment: tuple = ()         # (label, ExactMoney, record_count)
    record_count: int = 0
    period: str = ""
    meaning: str = ""
    coverage: Optional[CoverageState] = None


@dataclass
class Competitor:
    name: str
    product: str = ""
    award: Optional[ExactMoney] = None
    agency: str = ""
    recipient: str = ""
    award_id: str = ""
    evidence: tuple = ()
    implication: str = ""
    validated: bool = False


@dataclass
class CompetitivePosition:
    competitors: tuple = ()
    total: Optional[ExactMoney] = None
    screened: int = 0
    coverage: Optional[CoverageState] = None
    unfunded_note: str = ""        # the shared no-award fact, stated once


@dataclass
class Opportunity:
    key: str
    identifier: str
    source_kind: str
    title: str = ""
    agency: str = ""
    office: str = ""
    opportunity_type: str = ""
    value: Optional[ExactMoney] = None
    value_note: str = ""
    response_date: str = ""
    fit: str = ""
    motion: str = MONITOR
    action: str = ""
    access_route: str = ""
    contacts: tuple = ()
    linked_targets: tuple = ()
    evidence: tuple = ()
    coverage: Optional[CoverageState] = None


@dataclass
class TeamingRoute:
    organisation: str
    role: str = ""                 # prime | reseller | incumbent | holder
    award: Optional[ExactMoney] = None
    agency: str = ""
    why: str = ""
    unlocks: str = ""
    target_role: str = ""
    person: str = ""
    client_role: str = ""          # what the client performs on this route
    buyer_value: str = ""          # why the combination helps the buyer
    evidence: tuple = ()
    action: str = ""
    coverage: Optional[CoverageState] = None


@dataclass
class ContactAction:
    name: str
    title: str = ""
    organisation: str = ""
    email: str = ""
    phone: str = ""
    phone_kind: str = ""
    grade: str = ""
    source_class: str = ""
    motion: str = ""
    bound_to: str = ""
    reason: str = ""
    evidence: tuple = ()
    coverage: Optional[CoverageState] = None


@dataclass
class EventItem:
    name: str
    url: str = ""
    date: str = ""
    location: str = ""
    agencies: tuple = ()
    fit: str = ""
    who_to_meet: str = ""
    action: str = ""
    priority: str = ""
    evidence: tuple = ()


@dataclass
class ResearchOrder:
    kind: str
    section: str
    what: str                      # internal, exact
    client_line: str               # forward language, client-safe
    count: int = 1


@dataclass
class FederalMarketMapDocument:
    client_name: str
    as_of: str = ""
    company_understanding: Optional[CompanyUnderstanding] = None
    category_footprint: Optional[CategoryFootprint] = None
    competitive_position: Optional[CompetitivePosition] = None
    qualified_opportunities: tuple = ()
    teaming_routes: tuple = ()
    contact_actions: tuple = ()
    events: tuple = ()
    research_demand: tuple = ()
    receipts: dict = field(default_factory=dict)

    def opportunity_keys(self) -> list:
        return [o.key for o in self.qualified_opportunities]


# --------------------------------------------------------------------------- #
# Promotion rules. Pure, receipted, one owner each.
# --------------------------------------------------------------------------- #
def _award_reference(record: Any) -> EvidenceReference:
    return EvidenceReference(
        source_id=_clean(getattr(record, "record_id", "")),
        source_kind=AWARD,
        source_url=_clean(getattr(record, "url", "")),
        claim_roles=(CLAIM_AWARD, CLAIM_SPEND, CLAIM_RECIPIENT,
                     CLAIM_PAPER_HOLDER),
        label=_clean(getattr(record, "title", "")))


def _opportunity_reference(record: Any) -> EvidenceReference:
    lane = _clean(getattr(record, "lane", ""))
    kind = FORECAST if lane == "L4_forecast" else NOTICE
    return EvidenceReference(
        source_id=_clean(getattr(record, "record_id", "")),
        source_kind=kind,
        source_url=_clean(getattr(record, "url", "")),
        claim_roles=(CLAIM_OPPORTUNITY,),
        label=_clean(getattr(record, "title", "")))


def _record_money(record: Any, *, basis: str, population: str) -> ExactMoney:
    ref = _award_reference(record)
    return ExactMoney.from_record_float(
        getattr(record, "obligated_dollars", 0) or 0,
        basis=basis, population=population, evidence=(ref,))


def classify_motion(record: Any, posture: dict) -> tuple:
    """(motion, action, access_route). Derived from type and posture only."""
    from agents.golden_press.prime_posture import route_for

    ntype = _clean(getattr(record, "notice_type", "")).casefold()
    lane = _clean(getattr(record, "lane", ""))
    route = route_for(record, posture or {})
    access = {"prime": "Bid directly.",
              "sub": "Reach the likely prime and position as a subcontractor.",
              "either": "Bid directly or position with a partner.",
              "unestablished": "Establish a route before committing effort."
              }.get(route.get("route", ""), "")

    if lane == "L4_forecast":
        return (SHAPE,
                "Reach the requirement owner while the scope is still being "
                "written.", access)
    if any(t in ntype for t in _SHAPING_TYPES):
        return (SHAPE,
                "Respond to shape the requirement before it is finalised.",
                access)
    if any(t in ntype for t in _ACTIVE_TYPES):
        motion = PRIME if route.get("route") == "prime" else (
            SUBCONTRACT if route.get("route") == "sub" else TEAM)
        return (motion, "A response is required by the stated date.", access)
    return (STATUS_CHECK,
            "Confirm whether this has moved into an active procurement.",
            access)


def commercial_meaning(footprint: CategoryFootprint) -> str:
    """One deterministic sentence. Never model prose."""
    if not footprint.total or not footprint.by_agency:
        return ""
    top_agency, top_money, _ = footprint.by_agency[0]
    return (f"{footprint.record_count} cited records carry "
            f"{footprint.total.display} in past obligations, concentrated at "
            f"{top_agency} ({top_money.display}). This is history on the "
            f"records named here, not market size and not pipeline.")


# --------------------------------------------------------------------------- #
# The assembler
# --------------------------------------------------------------------------- #
def _company(pack: Any, profile: dict, posture: dict) -> CompanyUnderstanding:
    from agents.golden_press.client_identity import audit_client_identity
    from agents.golden_press.market_map import _strip_internal
    from agents.golden_press.rollups import resolve_relationship

    client = _clean(getattr(pack, "client_name", ""))
    identity = audit_client_identity(dict(profile, client_name=client))
    products = tuple(e["name"] for e in identity["entities"]
                     if e["kind"] == "product")
    channels = []
    for record in (getattr(pack, "records", []) or []):
        name = _clean(getattr(record, "recipient", ""))
        if name and name not in channels and resolve_relationship(
                name, pack)["relationship"] == "named_reseller":
            channels.append(name)
    core = tuple((profile.get("capability_terms") or {}).get("core") or ())
    approved = tuple(profile.get("discovered_terms_approved") or ())
    rejected = tuple(profile.get("discovered_terms_rejected") or {})
    pending = tuple(_pending_terms(profile))
    confirmations = []
    if pending:
        confirmations.append(
            f"{len(pending)} candidate term(s) await your decision")
    if not products:
        confirmations.append("Confirm the product list")
    coverage = (complete("keywords", "naics", "products")
                if products and core and not pending
                else partial(
                    known=("keywords", "naics"),
                    missing=tuple(x for x in (
                        () if products else ("products",)) + (
                        ("candidate terms",) if pending else ())),
                    action="Confirm these capability terms",
                    demand_kind=DEMAND_PROFILE_CONFIRMATION))
    return CompanyUnderstanding(
        client_name=client,
        description=_strip_internal(profile.get("capability_summary")),
        products=products, channels=tuple(channels),
        supplied_keywords=core, researched_keywords=approved,
        pending_keywords=pending, rejected_keywords=rejected,
        naics=tuple(profile.get("naics_boundary") or ()),
        federal_footprint=_clean(posture.get("why")),
        posture=_clean(posture.get("posture")),
        coverage=coverage, confirmations=tuple(confirmations))


def _pending_terms(profile: dict) -> list:
    import json
    from pathlib import Path
    client = _clean(profile.get("client_name"))
    if not client:
        return []
    try:
        from agents.assessment_chain import canonical_slug
        root = Path(__file__).resolve().parents[2]
        proposal = json.loads(
            (root / "data" / "review"
             / f"{canonical_slug(client)}.term_discovery.json").read_text(
                encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return []
    approved = {_clean(t).casefold()
                for t in (profile.get("discovered_terms_approved") or [])}
    rejected = {_clean(t).casefold()
                for t in (profile.get("discovered_terms_rejected") or {})}
    return [c["term"] for c in (proposal.get("candidates") or [])
            if _clean(c.get("term")).casefold() not in approved
            and _clean(c.get("term")).casefold() not in rejected][:12]


def _award_in_market(record: Any, contract: dict) -> bool:
    """Does this award record belong to the client's market at all?

    THE SAME GATE THE OPPORTUNITIES PASS. Measured on apexanalytix 2026-08-07,
    the ten award records in the pack summed to $1,343,939,436.24 and the
    document led with that number. The records were: a Treasury Account
    Symbol line for an Air Force aircraft program ($683,120,479.78), three
    corporate name-change filings, and a NUCLEAR-ARMED SEA LAUNCHED CRUISE
    MISSILE. Not one of them was supplier-risk or payment-integrity work.

    A category total is only a category total if every record is in the
    category. Without this gate the headline figure is the sum of whatever
    the pull happened to return, which is worse than no figure at all
    because it looks authoritative.
    """
    if not contract:
        return True
    from agents.golden_press.coverage_families import best_match
    title = _clean(getattr(record, "title", ""))
    body = _clean(getattr(record, "description", "")
                  or getattr(record, "description_prefix", ""))
    return bool(best_match(title, contract, description=body))


def _footprint(pack: Any, contract: Any = None) -> CategoryFootprint:
    """Category spend over the CITED records only, split so it reconciles.

    Never mixes category spend, competitor revenue, forecast value and
    client-attributed awards into one total: each is its own segment and the
    segments sum to the stated total exactly.
    """
    from agents.golden_press.rollups import resolve_relationship

    records = [r for r in (getattr(pack, "records", []) or [])
               if _clean(getattr(r, "lane", "")).startswith("L2")]
    screened = len(records)
    records = [r for r in records if _award_in_market(r, contract or {})]
    population = (f"{len(records)} cited award record(s) in market, of "
                  f"{screened} screened")
    basis = "obligated on cited records"

    def _bucket(record) -> str:
        recipient = _clean(getattr(record, "recipient", ""))
        if not recipient:
            return "Other cited holders"
        rel = resolve_relationship(recipient, pack)["relationship"]
        return {"client_entity": "Client and its products",
                "competitor": "Named rivals",
                "named_reseller": "Channel partners"}.get(
            rel, "Other cited holders")

    segments, agencies, holders = {}, {}, {}
    for record in records:
        money = _record_money(record, basis=basis, population=population)
        for key, store in ((_bucket(record), segments),
                           (_clean(getattr(record, "agency", "")) or "Unstated",
                            agencies),
                           (_clean(getattr(record, "recipient", ""))
                            or "Unstated", holders)):
            rows = store.setdefault(key, [])
            rows.append((_clean(getattr(record, "record_id", "")), money))

    def _rollup(store) -> tuple:
        out = []
        for label, rows in store.items():
            money = ExactMoney.summed(rows, basis=basis,
                                      population=f"{len(rows)} record(s)")
            out.append((label, money, len(rows)))
        return tuple(sorted(out, key=lambda r: -r[1].amount))

    by_segment = _rollup(segments)
    total = (ExactMoney.summed(
        [(label, money) for label, money, _ in by_segment],
        basis=basis, population=population)
        if by_segment else None)
    footprint = CategoryFootprint(
        total=total, by_segment=by_segment,
        by_agency=_rollup(agencies)[:10], by_holder=_rollup(holders)[:10],
        record_count=len(records), period="cited records in this pack",
        coverage=complete("obligated", "agency", "recipient") if records
        else research_next(missing=("award evidence",),
                           action="Award research next",
                           demand_kind=DEMAND_COMPETITOR_VALIDATION))
    footprint.meaning = commercial_meaning(footprint)
    return footprint


def _rival_footprint(slug: str,
                     measured: Any = None) -> Optional[CategoryFootprint]:
    """Category spend measured as the federal awards the named rivals hold."""
    from agents.golden_press.rival_footprint import load_cached, summarise

    if measured is None:
        measured = load_cached(slug) if slug else {}
    rows = [r for r in summarise(measured) if r["awards"]]
    if not rows:
        return None

    def _money(amount, basis, population, refs=()) -> ExactMoney:
        return ExactMoney(amount=amount, basis=basis, population=population,
                          evidence=tuple(refs))

    basis = "obligated to named product rivals as prime recipients"
    records = sum(r["awards"] for r in rows)
    population = (f"{records} USAspending award record(s) held by "
                  f"{len(rows)} named rivals, 2019 to date")

    def _refs(awards: Any, limit: int = 3) -> tuple:
        """Award references for a row, largest first, so the rendered row
        can open its own biggest record instead of asserting a bare sum."""
        out = []
        ranked = sorted((awards or []),
                        key=lambda a: -float(a.get("amount") or 0))
        for award in ranked[:limit]:
            rid = _clean(award.get("award_id"))
            if rid:
                out.append(EvidenceReference(
                    source_id=rid, source_kind=AWARD,
                    claim_roles=(CLAIM_SPEND, CLAIM_RECIPIENT),
                    label=_clean(award.get("agency"))))
        return tuple(out)

    holders = tuple(
        (r["name"], _money(r["total"], basis, f"{r['awards']} award record(s)",
                           refs=_refs(measured.get(r["name"]))),
         r["awards"]) for r in rows)

    agencies: dict = {}
    counts: dict = {}
    agency_awards: dict = {}
    for name, awards in measured.items():
        for award in awards:
            key = _clean(award.get("agency")) or "Unnamed buyer"
            agencies[key] = agencies.get(key, Decimal("0")) + Decimal(
                str(award.get("amount") or 0))
            counts[key] = counts.get(key, 0) + 1
            agency_awards.setdefault(key, []).append(award)
    by_agency = tuple(
        (name, _money(total, basis, f"{counts[name]} award record(s)",
                      refs=_refs(agency_awards.get(name))),
         counts[name])
        for name, total in sorted(agencies.items(), key=lambda kv: -kv[1])[:10])

    # THE TOTAL CARRIES ITS OWN RECEIPTS (grading round 1: the headline
    # figure's drawer had an empty source list against the page's own
    # every-amount-opens-its-receipt promise). Largest records first,
    # sampled across every rival, and the drawer's population line already
    # names the full count.
    total_refs: list = []
    for name, awards in measured.items():
        total_refs.extend(_refs(awards, limit=4))
    total_refs.sort(key=lambda r: r.source_id)
    total = _money(sum((r["total"] for r in rows), Decimal("0")), basis,
                   population, refs=tuple(total_refs[:40]))
    footprint = CategoryFootprint(
        total=total, by_agency=by_agency, by_holder=holders,
        record_count=records, period="2019 to date",
        coverage=complete("obligated", "agency", "recipient"))
    footprint.meaning = (
        f"{len(rows)} of the named product rivals hold {total.display} of "
        f"federal work across {records} awards. This is where the money in "
        f"this market actually moves today.")
    return footprint


def _competition(pack: Any, profile: dict, slug: str = "",
                 measured: Any = None) -> CompetitivePosition:
    """The named product rivals, ALWAYS published, enriched where measured.

    THE OPERATOR'S NAMED SET IS THE ANSWER, NOT A CANDIDATE. This function
    used to publish a rival only if it also appeared on an award record in
    the pack. For apexanalytix that discarded ten rivals carrying a cited
    market source (Gartner Magic Quadrant for Supplier Risk Management
    Solutions, 4 May 2026, G00834117) and rendered "Research next" at a
    client, because a DIFFERENT lane happened to be empty. An operator fact
    with a citation is the highest grade of evidence here; corroboration
    enriches it and its absence never deletes it.

    Federal award history is measured per rival from USAspending and
    attached when present. A measured zero is published as a zero, because
    "this rival sells the product and holds no federal paper" is itself a
    finding.
    """
    from agents.golden_press.rival_footprint import load_cached, summarise

    stated = product_competitors(profile)
    if measured is None:
        measured = load_cached(slug) if slug else {}
    by_name = {row["name"]: row for row in summarise(measured)}

    competitors = []
    for entry in stated:
        name = _clean(entry.get("name"))
        if not name:
            continue
        row = by_name.get(name)
        award = None
        evidence: tuple = ()
        agency = ""
        award_id = ""
        if row and row["awards"]:
            agency = row["top_agency"]
            # ONE ORDER FOR ID AND LINK. The displayed id and the link
            # target both come from the FIRST reference below, so they
            # cannot disagree (grading round 1: all five cards showed one
            # award and linked another).
            ranked = sorted((measured.get(name) or []),
                            key=lambda a: -float(a.get("amount") or 0))
            award = ExactMoney(
                amount=row["total"],
                basis="obligated to this rival as prime recipient",
                population=f"{row['awards']} USAspending award record(s), "
                           f"2019 to date",
                evidence=tuple(
                    EvidenceReference(
                        source_id=_clean(a.get("award_id")), source_kind=AWARD,
                        claim_roles=(CLAIM_SPEND, CLAIM_RECIPIENT),
                        label=_clean(a.get("agency")))
                    for a in ranked[:12]
                    if _clean(a.get("award_id"))))
            evidence = award.evidence
            award_id = (_clean(evidence[0].source_id) if evidence else "")
            implication = (
                f"Holds {award.display} of federal work across "
                f"{row['awards']} awards, concentrated at {agency}.")
        else:
            # ONCE, NOT PER CARD. Five rivals each carrying this sentence
            # verbatim tripped the density validator (L-M2: a fact appears
            # once). The per-card copy stays empty and the group note below
            # states the shared fact a single time.
            implication = ""
        position = _clean(entry.get("position"))
        competitors.append(Competitor(
            name=name, product=position or "Product rival", award=award,
            agency=agency, recipient=name, award_id=award_id,
            evidence=evidence, implication=implication,
            validated=bool(row and row["awards"])))

    funded = [c for c in competitors if c.award is not None]
    unfunded = [c.name for c in competitors if c.award is None]
    total = None
    if funded:
        total = ExactMoney.summed(
            [(c.name, c.award) for c in funded],
            basis="obligated to named product rivals as prime recipients",
            population=f"{len(funded)} of {len(competitors)} named rivals "
                       f"hold federal awards")
    unfunded_note = ""
    if unfunded:
        unfunded_note = (
            f"No federal prime award found under "
            f"{len(unfunded)} of the {len(competitors)} names "
            f"({', '.join(unfunded)}); the next pass checks "
            f"reseller-held paper.")
    return CompetitivePosition(
        competitors=tuple(competitors), total=total,
        unfunded_note=unfunded_note,
        screened=len(competitors),
        coverage=complete("named rivals", "federal award history")
        if competitors else research_next(
            missing=("named product rivals",),
            action="Rival research next",
            demand_kind=DEMAND_COMPETITOR_VALIDATION))

def _opportunities(pack: Any, posture: dict, contract: Any = None,
                   rejected_out: Any = None,
                   excluded_terms: Any = ()) -> tuple:
    """One row per canonical key. A duplicate cannot be constructed.

    THE GATE JUDGES THE RECORD, NOT THE ROW. The capability gate ran on the
    finished row's `fit` note, which for pack-lane rows is empty, so a
    record whose DESCRIPTION carries its whole context (SDIB's industrial
    base language, a must-recall receipt) was rejected on its bare title.
    Here the gate sees title plus the record's own description, at the one
    point where the description still exists.
    """
    seen: dict = {}
    for record in (getattr(pack, "records", []) or []):
        lane = _clean(getattr(record, "lane", ""))
        if lane not in ("L1_notice", "L4_forecast"):
            continue
        record_text = (
            f"{_clean(getattr(record, 'title', ''))} "
            f"{_clean(getattr(record, 'description', ''))}").casefold()
        excluded = next(
            (_clean(term) for term in (excluded_terms or ())
             if _clean(term) and _clean(term).casefold() in record_text), "")
        if excluded:
            if rejected_out is not None:
                rejected_out.append({
                    "identifier": _clean(getattr(record, "record_id", "")),
                    "title": _clean(getattr(record, "title", ""))[:100],
                    "kind": (FORECAST if lane == "L4_forecast"
                             else NOTICE),
                    "reason": f"client exclusion matched: {excluded}",
                    "reason_class": "rejected_client_exclusion"})
            continue
        if contract:
            from agents.golden_press.coverage_families import best_match
            gate_description = ("" if contract.get("title_required") else
                                _clean(getattr(record, "description", "")))
            if not best_match(
                    _clean(getattr(record, "title", "")), contract,
                    description=gate_description):
                if rejected_out is not None:
                    rejected_out.append({
                        "identifier": _clean(
                            getattr(record, "record_id", "")),
                        "title": _clean(getattr(record, "title", ""))[:100],
                        "kind": (FORECAST if lane == "L4_forecast"
                                 else NOTICE),
                        "reason": "outside the approved capability frame",
                        "reason_class": "rejected_out_of_frame"})
                continue
        ref = _opportunity_reference(record)
        key = opportunity_key(ref.source_kind,
                              getattr(record, "record_id", ""))
        if not key.split(":")[1] or key in seen:
            continue
        motion, action, access = classify_motion(record, posture)
        value, note, coverage = _opportunity_value(record)
        seen[key] = Opportunity(
            key=key, identifier=_clean(getattr(record, "record_id", "")),
            source_kind=ref.source_kind,
            title=_clean(getattr(record, "title", "")),
            agency=_clean(getattr(record, "agency", "")),
            office=_clean(getattr(record, "office", "")),
            opportunity_type=(_clean(getattr(record, "notice_type", ""))
                              or ("Forecast" if lane == "L4_forecast"
                                  else "Notice")),
            value=value, value_note=note,
            response_date=_clean(getattr(record, "period_end", "")),
            motion=motion, action=action, access_route=access,
            evidence=(ref,), coverage=coverage)
    return tuple(seen.values())


def _opportunity_value(record: Any) -> tuple:
    """(ExactMoney|None, note, CoverageState). Published values only."""
    for attr in ("value_display", "published_value"):
        literal = _clean(getattr(record, attr, ""))
        if literal:
            return (ExactMoney(amount=Decimal("0"), basis="as published",
                               population="one official record",
                               source_literal=literal),
                    "as published", complete("published value"))
    for attr in ("ceiling_dollars", "obligated_dollars"):
        raw = getattr(record, attr, 0) or 0
        if float(raw):
            return (ExactMoney.from_record_float(
                raw, basis="on the cited record",
                population="one official record",
                evidence=(_award_reference(record),)), "on the cited record",
                complete("record value"))
    return (None, "", research_next(
        missing=("official value",), action="Official value not published",
        demand_kind=DEMAND_OPPORTUNITY_VALUE))


# --------------------------------------------------------------------------- #
# Competitor identity: a product rival, not whoever else won an award
# --------------------------------------------------------------------------- #
def product_competitors(profile: dict) -> tuple:
    """The named rival ENTRIES, with their market position, not just names."""
    block = (profile or {}).get("product_competitors") or {}
    return tuple(e for e in (block.get("competitors") or [])
                 if _clean(e.get("name")))


def product_competitor_market(profile: dict) -> dict:
    """The market this rival set was drawn from, and its citation."""
    block = (profile or {}).get("product_competitors") or {}
    return {"market": _clean(block.get("market")),
            "source": _clean(block.get("source")),
            "client_position": _clean(block.get("client_position"))}


def product_competitor_names(profile: dict) -> tuple:
    """The client's REAL product rivals, from an authoritative market source.

    MEASURED 2026-08-07, and this is why the rule exists. apexanalytix's
    pack carried 34 "competitors": Accenture, Booz Allen, CACI, Deloitte,
    Carahsoft, Lockheed, SAIC, IBM. Those are primes, integrators and
    resellers that happen to win federal awards. Its ACTUAL product rivals,
    per the Gartner Magic Quadrant for Supplier Risk Management Solutions
    (4 May 2026), are Everstream Analytics, Exiger, Prewave, Resilinc,
    interos.ai, Z2Data, Altana, Sphera and Moody's.

    OVERLAP BETWEEN THE TWO SETS: ZERO.

    Award co-occurrence answers "who else sells to this buyer". It cannot
    answer "who competes with this product", because the federal market is
    intermediated: the rival's software reaches government through a
    reseller whose name is on the award, and the prime on the award is not
    selling a competing product at all. A competitor set has to come from a
    product-market source, and the profile is where the operator states it.
    """
    block = (profile or {}).get("product_competitors") or {}
    return tuple(_clean(row.get("name"))
                 for row in (block.get("competitors") or [])
                 if _clean(row.get("name")))


def competitor_source(profile: dict) -> str:
    return _clean(((profile or {}).get("product_competitors") or {}).get(
        "source"))


def is_product_competitor(name: Any, profile: dict) -> bool:
    """Word-boundary match against the stated product-rival set only."""
    from tools.entity_lineage import company_matches

    candidate = _clean(name)
    if not candidate:
        return False
    for rival in product_competitor_names(profile):
        try:
            if company_matches(candidate, rival):
                return True
        except Exception:                                 # noqa: BLE001
            if _norm(rival) and _norm(rival) in _norm(candidate):
                return True
    return False


def _rival_on_record(record: Any, profile: dict) -> str:
    """Which named product rival this award is FOR, or empty.

    Reads the entity hits that surfaced the record, then the recipient.
    A record whose hits name the client's own product is client paper, not
    rival paper, and returns empty.
    """
    for hit in (getattr(record, "entity_hits", None) or []):
        if is_product_competitor(hit, profile):
            return next((r for r in product_competitor_names(profile)
                         if _norm(r) in _norm(hit) or _norm(hit) in _norm(r)),
                        _clean(hit))
    recipient = _clean(getattr(record, "recipient", ""))
    if recipient and is_product_competitor(recipient, profile):
        return recipient
    return ""


def _prose_name(value: Any) -> str:
    """A name safe to interpolate into a sentence.

    "BOOZ ALLEN HAMILTON INC." carries a period, so every sentence built
    around such a name splits at the legal suffix and the remainder reads
    identical across routes; Gate E caught four of them colliding. Display
    cells keep the registered name; prose drops the trailing dot.
    """
    return _clean(value).rstrip(".")


def _fit_terms(opp: Any) -> str:
    """The capability terms a qualified record matched, read off its fit."""
    fit = _clean(getattr(opp, "fit", ""))
    if fit.startswith("Matches "):
        return fit[len("Matches "):].split(".")[0]
    return ""


def _teaming(pack: Any, profile: dict, opportunities: tuple) -> tuple:
    """Paper holders and what each unlocks. SEPARATE from competitors.

    An award proves a paper holder. It does NOT prove an open opportunity,
    which is why `unlocks` names a motion rather than a solicitation unless
    an opportunity record independently exists at the same agency.
    """
    from agents.golden_press.rollups import resolve_relationship

    by_agency: dict = {}
    for opp in opportunities:
        by_agency.setdefault(_norm(opp.agency), []).append(opp)

    # A ROUTE MUST BE IN THIS MARKET (grading round 1, fatal: a nuclear
    # cruise missile award and an operating-system license deal stood as
    # teaming routes on term co-occurrence). The same capability gate that
    # judges an opportunity judges the paper a route rides on: when the
    # client has a coverage contract, at least one of the holder's records
    # must clear it, title plus description.
    from agents.golden_press.coverage_families import best_match
    slug_for_gate = _norm(getattr(pack, "client_name", ""))
    contract = _coverage_contract(slug_for_gate)

    def _in_market(record: Any) -> bool:
        if not contract:
            return True
        return bool(best_match(
            _clean(getattr(record, "title", "")), contract,
            description=_clean(getattr(record, "description", ""))))

    holders: dict = {}
    for record in (getattr(pack, "records", []) or []):
        name = _clean(getattr(record, "recipient", ""))
        if not name or is_product_competitor(name, profile):
            continue
        rel = resolve_relationship(name, pack)["relationship"]
        if rel not in ("named_reseller", "independent"):
            continue
        holders.setdefault(name, {"records": [], "rel": rel})["records"].append(
            record)
    if contract:
        dropped = {n: e for n, e in holders.items()
                   if not any(_in_market(r) for r in e["records"])}
        holders = {n: e for n, e in holders.items() if n not in dropped}

    routes = []
    for name, entry in holders.items():
        records = entry["records"]
        best = max(records,
                   key=lambda r: float(getattr(r, "obligated_dollars", 0) or 0))
        money = ExactMoney.summed(
            [(_clean(getattr(r, "record_id", "")),
              _record_money(r, basis="obligated on cited records",
                            population=f"{len(records)} record(s)"))
             for r in records],
            basis="obligated on cited records",
            population=f"{len(records)} cited award record(s)")
        agency = _clean(getattr(best, "agency", ""))
        matched = by_agency.get(_norm(agency)) or []
        unlocks = (f"{matched[0].identifier} at {agency}" if matched
                   else f"access at {agency}" if agency else "an access route")
        client = _prose_name(getattr(pack, "client_name", "")) or "the client"
        pname = _prose_name(name)
        best_id = _clean(getattr(best, "record_id", ""))
        terms = _fit_terms(matched[0]) if matched else ""
        client_role = (
            f"{client} performs the {terms} workload as the specialist "
            f"under {pname}'s paper." if terms else
            f"{client} rides as the specialist under {pname}'s existing "
            f"paper on {best_id or 'the cited record'}; the workload is "
            f"named once a live requirement is bound to this route.")
        buyer_value = (
            f"{_prose_name(agency) or 'The buyer'} keeps its incumbent "
            f"vehicle and gains the {terms} capability it asked for in "
            f"{matched[0].identifier}." if matched and terms else
            f"{_prose_name(agency) or 'The buyer'} adds a capability "
            f"through {pname}'s paper, which it already trusts, with no "
            f"new procurement.")
        routes.append(TeamingRoute(
            organisation=name,
            role=("reseller" if entry["rel"] == "named_reseller"
                  else "paper holder"),
            award=money, agency=agency,
            why=(f"Holds {len(records)} cited award record(s) worth "
                 f"{money.display} at {agency or 'a federal buyer'}."),
            unlocks=unlocks,
            client_role=client_role, buyer_value=buyer_value,
            target_role="Federal alliance or channel lead",
            evidence=(_award_reference(best),),
            action=(f"Ask who owns the route on "
                    f"{best_id or 'the cited record'} and whether "
                    f"{pname} will carry this requirement."),
            coverage=research_next(
                known=("organisation", "award evidence"),
                missing=("named partner contact",),
                action="Named teaming contact in research",
                demand_kind=DEMAND_PARTNER_CONTACT)))
    routes.sort(key=lambda r: -(r.award.amount if r.award else Decimal(0)))
    return tuple(routes)


def _reach_rank(contact: Any) -> tuple:
    """How ACTIONABLE this contact is, most first.

    A name with a direct number is worth more than a name with an inbox, and
    a name with an inbox is worth more than a role with neither. The operator
    works the top of this list, so the list has to be ordered by what can
    actually be done with each row today.

    Sort key, all descending: has a phone, has an email, has a real published
    job title, then name for a stable order.
    """
    phone = 1 if _clean(getattr(contact, "phone", "")) else 0
    email = 1 if _clean(getattr(contact, "email", "")) else 0
    title = _clean(getattr(contact, "title", ""))
    titled = 1 if title and not title.lower().startswith(
        ("point of contact", "secondary point")) else 0
    published = 1 if _clean(
        getattr(contact, "grade", "")) == "government published" else 0
    return (-phone, -email, -titled, -published,
            _clean(getattr(contact, "name", "")).casefold())


def _by_reachability(contacts: Any) -> tuple:
    return tuple(sorted(contacts or (), key=_reach_rank))


def _contacts(pack: Any, slug: str, opportunities: tuple,
              routes: tuple, targets_payload: Any = _LIVE) -> tuple:
    """Every contact the system already holds, by motion, with a grade.

    Government-published notice POCs stay DISTINCT from commercially
    enriched identities, and a missing direct dial produces typed research
    demand rather than an organisation switchboard wearing a person's name.
    """
    from agents.golden_press.person_screen import renderable
    from tools.intelligence_graph.adapter import (
        admissible_target_observations, load_target_observations)

    # THE PUBLISHED POCs ARE ALREADY IN HAND. Every qualified opportunity
    # carries the contact the government printed on the notice, and this
    # function used to return EMPTY whenever the commercial enrichment store
    # was absent, so a report with eleven named contracting officers said
    # "0 contacts". Enrichment adds to this set; it is not a precondition
    # for it.
    out = []
    seen: set = set()
    for opp in (opportunities or ()):
        for person in (getattr(opp, "contacts", ()) or ()):
            email = _clean(person.get("email"))
            name = _clean(person.get("name"))
            if not email or email.casefold() in seen:
                continue
            seen.add(email.casefold())
            out.append(ContactAction(
                name=name or email,
                title=(_clean(person.get("title"))
                       or ("Point of contact on the notice"
                           if person.get("role") != "secondary"
                           else "Secondary point of contact")),
                organisation=_clean(getattr(opp, "agency", "")),
                email=email, phone=_clean(person.get("phone")),
                phone_kind="published" if person.get("phone") else "",
                grade="government published",
                source_class="sam.notice", motion=STATUS_CHECK,
                bound_to=_clean(getattr(opp, "identifier", "")),
                reason=("Published by the government on "
                        f"{_clean(getattr(opp, 'identifier', '')) or 'this record'}."),
                evidence=tuple(getattr(opp, "evidence", ()) or ())[:1],
                coverage=complete("email") if not person.get("phone")
                else complete("email", "phone")))

    payload = (load_target_observations(slug) if targets_payload is _LIVE
               else targets_payload)
    if not payload:
        return _by_reachability(out)
    rows, _dropped = admissible_target_observations(payload)
    for row in rows:
        if not renderable(row.get("screen")):
            continue
        phones = row.get("phones") or []
        direct, kind = "", ""
        for entry in phones:
            ptype = _clean(entry.get("type"))
            if ptype in ("work_direct", "mobile"):
                direct, kind = _clean(entry.get("number")), ptype
                break
        source = _clean((row.get("provenance") or {}).get("source"))
        published = source.startswith("sam.")
        coverage = (complete("email", "phone") if direct
                    else research_next(
                        known=("email",), missing=("direct phone",),
                        action="Direct phone enrichment next",
                        demand_kind=DEMAND_DIRECT_PHONE))
        if _clean(row.get("email")).casefold() in seen:
            continue
        out.append(ContactAction(
            name=_clean(row.get("name")), title=_clean(row.get("title")),
            organisation=_clean(row.get("organization")),
            email=_clean(row.get("email")), phone=direct, phone_kind=kind,
            grade=("government published" if published
                   else "commercial enrichment"),
            source_class=source,
            motion=(STATUS_CHECK if published else TEAM),
            bound_to=" · ".join(row.get("join_record_ids") or []),
            reason=("Published on a record this report cites."
                    if published else
                    "Resolved against a targeting spec this report carries."),
            coverage=coverage))
    return _by_reachability(out)


def _events(pack: Any) -> tuple:
    """Verified events only, from the pack's own event lane.

    SAM amendment/repost records can carry the same event title under more
    than one notice id.  The Market Map promises one canonical row per
    requirement family, so collapse exact title/date repetitions here and
    keep the most recently posted source record.  A recurring event with a
    different published start date remains a distinct row.
    """
    selected: dict[tuple[str, str], Any] = {}
    for event in (getattr(pack, "events", []) or []):
        get = (event.get if isinstance(event, dict)
               else lambda k, d=None: getattr(event, k, d))
        name = _clean(get("name") or get("title"))
        url = _clean(get("url"))
        if not name or not url:
            continue
        event_start = _clean(get("event_start"))
        key = (" ".join(name.casefold().split()), event_start)
        prior = selected.get(key)
        if prior is not None:
            prior_get = (prior.get if isinstance(prior, dict)
                         else lambda k, d=None: getattr(prior, k, d))
            prior_rank = (_clean(prior_get("posted_date")),
                          _clean(prior_get("registration_deadline")),
                          _clean(prior_get("event_id")))
            current_rank = (_clean(get("posted_date")),
                            _clean(get("registration_deadline")),
                            _clean(get("event_id")))
            if current_rank <= prior_rank:
                continue
        selected[key] = event

    out = []
    for event in selected.values():
        get = (event.get if isinstance(event, dict)
               else lambda k, d=None: getattr(event, k, d))
        name = _clean(get("name") or get("title"))
        url = _clean(get("url"))
        out.append(EventItem(
            name=name, url=url,
            date=_clean(get("event_start")) or "Date not published",
            location=_clean(get("location")) or "Location not published",
            agencies=tuple(x for x in [_clean(get("host"))] if x),
            fit=f"Hosted by {_clean(get('host')) or 'a federal buyer'}.",
            who_to_meet="The hosting contracting office.",
            action="Register on the official page.",
            priority="standard",
            evidence=(EvidenceReference(
                source_id=_clean(get("event_id")) or name,
                source_kind=EVENT, source_url=url,
                claim_roles=(CLAIM_EVENT,)),)))
    return tuple(out)


def _research_demand(company, footprint, competition, opportunities,
                     routes, contacts, events) -> tuple:
    """Typed absence, computed BEFORE prose. Every entry is a job."""
    counts: dict = {}

    def _add(state, section):
        if state is None or state.state == "complete":
            return
        key = (state.demand_kind, section, state.next_action)
        entry = counts.setdefault(key, {"detail": state.detail, "n": 0})
        entry["n"] += 1

    for state, section in ((getattr(company, "coverage", None), "company"),
                           (getattr(footprint, "coverage", None), "market"),
                           (getattr(competition, "coverage", None),
                            "competition")):
        _add(state, section)
    for opp in opportunities:
        _add(opp.coverage, "opportunities")
    for route in routes:
        _add(route.coverage, "teaming")
    for contact in contacts:
        _add(contact.coverage, "contacts")
    if not events:
        counts[(DEMAND_EVENT_CONFIRMATION, "events",
                "Official event confirmation next")] = {"detail": "", "n": 1}
    return tuple(
        ResearchOrder(kind=kind, section=section, client_line=action,
                      what=meta["detail"] or action, count=meta["n"])
        for (kind, section, action), meta in sorted(counts.items()))


def _coverage_contract(slug: str) -> dict:
    from agents.golden_press.coverage_families import load
    return load(slug) if slug else {}


def _contract_terms(slug: str) -> tuple:
    """The client's coverage contract, as a search surface plus its receipt.

    When a contract exists it REPLACES the ad-hoc term list: it is the only
    surface with a stated disposition for every family, witnessed government
    vocabulary, and regression receipts that fail the build when recall
    drops.
    """
    from agents.golden_press.coverage_families import (
        ACTIVE, CONTROLLED, coverage_report, load, phrases,
    )
    contract = load(slug)
    if not contract:
        return (), {}
    return (tuple(phrases(contract, statuses=(ACTIVE, CONTROLLED))),
            coverage_report(contract))


def _apply_record_rulings(slug: str, opportunities: tuple,
                          rejected_rows: list) -> tuple:
    """Operator rulings by identifier, applied last and receipted always.

    `clients/<slug>/record_rulings.json` is OPERATOR-OWNED: it encodes the
    engagement decisions data cannot make (a wrong-domain record that
    matched our vocabulary, a bridge not worth contesting, a high-fit
    record whose route demands a partner). A reject moves the record to
    the ledger with the operator's reason class; a reclassify rewrites the
    motion, the action and the access note while LEAVING FIT ALONE. Every
    application lands in the receipt, so the artifact can say which rows
    the operator ruled and why.
    """
    import json as _json
    from dataclasses import replace as _replace
    from pathlib import Path as _Path

    receipt = {"applied": [], "rejected": 0, "reclassified": 0}
    path = (_Path(__file__).resolve().parents[2] / "clients" / _clean(slug)
            / "record_rulings.json")
    if not _clean(slug) or not path.exists():
        return opportunities, receipt
    try:
        rulings = {_clean(r.get("identifier")): r for r in
                   (_json.loads(path.read_text(encoding="utf-8"))
                    .get("rulings") or []) if _clean(r.get("identifier"))}
    except (ValueError, OSError):
        return opportunities, receipt
    kept: list = []
    for opp in opportunities:
        rule = rulings.get(_clean(opp.identifier))
        if not rule:
            kept.append(opp)
            continue
        if _clean(rule.get("ruling")) == "reject":
            rejected_rows.append({
                "identifier": opp.identifier, "title": opp.title[:100],
                "kind": opp.source_kind,
                "reason": _clean(rule.get("reason")),
                "reason_class": _clean(rule.get("reason_class"))
                or "rejected_by_operator"})
            receipt["rejected"] += 1
            receipt["applied"].append(
                {"identifier": opp.identifier, "ruling": "reject"})
            continue
        changes: dict = {}
        if _clean(rule.get("motion")):
            changes["motion"] = _clean(rule.get("motion"))
        if _clean(rule.get("action")):
            changes["action"] = _clean(rule.get("action"))
        if _clean(rule.get("access_note")):
            changes["access_route"] = _clean(rule.get("access_note"))
        kept.append(_replace(opp, **changes) if changes else opp)
        receipt["reclassified"] += 1
        receipt["applied"].append(
            {"identifier": opp.identifier, "ruling": "reclassify",
             "motion": _clean(rule.get("motion"))})
    return tuple(kept), receipt


def _exact_terms(profile: dict) -> list:
    """The title-qualifying phrase set: THE CLIENT'S OWN, exclusively.

    THE LEAK THIS ENDS (Gate E verification, 2026-08-07). This fallback
    carried a hard-coded payment-integrity phrase list plus a shared
    synonyms tier, both of them one client's vocabulary. Every client
    without a coverage contract then pulled the SAME store records: four
    different companies pressed with identical opportunity and contact
    counts, and a network-performance client's map carried VA fraud RFIs.
    A client's store lane searches what THAT client sells: its approved
    discoveries and its own capability terms. A client whose vocabulary is
    thin gets a thin, honest lane and a research order, never another
    company's market.
    """
    terms = list(profile.get("discovered_terms_approved") or [])
    terms.extend((profile.get("capability_terms") or {}).get("core") or [])
    return [_clean(t) for t in terms if _clean(t)]


def _pack_notice_terms(pack: Any) -> list:
    """The exact capability surface used by the pack's notice lane.

    Retrieval already expands approved client vocabulary into government
    buying language. The market-map capture previously discarded that work
    and started again from a much smaller profile list. Keeping the query
    handoff makes the pack and the final report search the same market.
    """
    if pack is None:
        return []
    queries = (pack.get("queries") if isinstance(pack, dict)
               else getattr(pack, "queries", ())) or ()
    out: list = []
    for query in queries:
        lane = (query.get("lane") if isinstance(query, dict)
                else getattr(query, "lane", ""))
        if lane != "L1_notice":
            continue
        body = (query.get("body") if isinstance(query, dict)
                else getattr(query, "body", {})) or {}
        out.extend(body.get("capability_terms") or ())
    return [_clean(term) for term in out if _clean(term)]


def _profile_boundaries(profile: dict) -> tuple[list, list]:
    naics = [str(code) for code in (profile.get("naics_boundary") or [])
             if _clean(code)]
    codes = profile.get("code_universe") or {}
    psc = [str(code) for code in (codes.get("psc") or []) if _clean(code)]
    return naics, psc


def _load_evidence_pack_v2(slug: str) -> dict:
    """Load the optional graph-contract sidecar without changing stores.

    Iterations 1 and 2 deliberately avoid a database migration.  The v2
    evidence pack is therefore a press-time sidecar: when it is absent the
    existing projection is unchanged; when present its qualified requirement
    families and target provenance can be consumed directly.
    """
    if not slug:
        return {}
    path = (Path(__file__).resolve().parents[2] / "data" / "state" /
            "candidate_review_v1" / slug /
            f"{slug}.evidence_pack.v2.json")
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if payload.get("schema_version") == "evidence-pack-v2" else {}


def _v2_opportunity_value(row: dict) -> tuple:
    """Return the source-published value without inventing a spend claim."""
    literal = _clean(row.get("published_value"))
    if not literal:
        return (None, "Value not published", research_next(
            missing=("official value",),
            action="Official value not published",
            demand_kind=DEMAND_OPPORTUNITY_VALUE))
    return (ExactMoney(
        amount=Decimal("0"), basis="as published",
        population="one qualified opportunity record",
        source_literal=literal), "as published", complete("published value"))


def _v2_opportunity(row: dict, targets: tuple) -> Opportunity:
    """Project one qualified V2 record into the renderer contract.

    The graph contract owns membership.  This adapter only translates its
    already-classified facts into the established Opportunity dataclass; it
    does not re-screen or re-rank the record.
    """
    identifier = _clean(row.get("record_id") or row.get("notice_id"))
    evidence_class = _clean(row.get("evidence_class"))
    source_kind = (FORECAST if evidence_class == "forecast" else NOTICE)
    reference = EvidenceReference(
        source_id=identifier, source_kind=source_kind,
        source_url=_clean(row.get("source_url")),
        claim_roles=(CLAIM_OPPORTUNITY,),
        label=_clean(row.get("title")))
    value, value_note, value_coverage = _v2_opportunity_value(row)
    instrument = _clean(row.get("instrument"))
    instrument_norm = instrument.casefold()
    commercial_route = _clean(row.get("commercial_route"))
    if source_kind == FORECAST:
        motion = SHAPE
        action = "Shape the requirement before it becomes a solicitation."
    elif any(term in instrument_norm for term in _SHAPING_TYPES):
        motion = SHAPE
        action = "Respond while the requirement is still being shaped."
    elif any(term in instrument_norm for term in _ACTIVE_TYPES):
        motion = PRIME if commercial_route == "direct" else TEAM
        action = "A response is required by the published date."
    else:
        motion = STATUS_CHECK
        action = "Confirm the current procurement status in the source record."
    access_route = {
        "direct": "Bid directly.",
        "named_partner_teaming": "Use the named eligible teaming route.",
        "restricted_route_needed": "Confirm an eligible route before pursuit.",
    }.get(commercial_route, _clean(row.get("access_rule")))
    contacts = tuple({
        "name": _clean(target.get("name")),
        "email": _clean(target.get("email")),
        "phone": _clean(target.get("phone")),
        "title": _clean(target.get("title") or target.get("role")),
        "source_class": _clean(target.get("source_kind")),
        "source": _clean(target.get("provenance")),
    } for target in targets
        if target.get("source_kind") == "published_contact"
        and (_clean(target.get("name")) or _clean(target.get("email"))
             or _clean(target.get("phone"))))
    response_due = _clean(row.get("response_due"))
    fit = _clean((row.get("technical_fit") or {}).get("basis"))
    return Opportunity(
        key=opportunity_key(source_kind, identifier),
        identifier=identifier, source_kind=source_kind,
        title=_clean(row.get("title")), agency=_clean(row.get("agency")),
        office=_clean(row.get("office")), opportunity_type=instrument,
        value=value, value_note=value_note,
        response_date=response_due[:10], fit=fit, motion=motion,
        action=action, access_route=access_route, contacts=contacts,
        linked_targets=targets, evidence=(reference,),
        coverage=value_coverage)


def attach_v2_targets(opportunities: tuple, payload: dict) -> tuple:
    """Make the V2 qualified set authoritative and attach its targets.

    When the sidecar is present, its ordered qualified records define the
    opportunity population.  Existing projection rows are reused where ids
    match; missing qualified rows are constructed; legacy rows absent from V2
    are removed.  This makes opportunity removal cascade to targets and keeps
    published contacts, approved enrichments, and enrichment candidates
    distinct in the attached provenance.
    """
    if not payload:
        return tuple(opportunities)
    qualified = payload.get("qualified_opportunity_records") or []
    existing = {_clean(row.identifier): row for row in opportunities}
    groups = payload.get("target_groups") or {}
    attached = []
    for row in qualified:
        identifier = _clean(row.get("record_id") or row.get("notice_id"))
        if not identifier:
            continue
        family = _clean(row.get("requirement_family"))
        targets = tuple(dict(target) for target in (groups.get(family) or [])
                        if _clean(target.get("opportunity_record_id")) in {
                            "", identifier})
        opportunity = existing.get(identifier)
        if opportunity is None:
            opportunity = _v2_opportunity(row, targets)
        else:
            published_contacts = tuple({
                "name": _clean(target.get("name")),
                "email": _clean(target.get("email")),
                "phone": _clean(target.get("phone")),
                "title": _clean(target.get("title") or target.get("role")),
                "source_class": _clean(target.get("source_kind")),
                "source": _clean(target.get("provenance")),
            } for target in targets
                if target.get("source_kind") == "published_contact"
                and (_clean(target.get("name")) or
                     _clean(target.get("email")) or
                     _clean(target.get("phone"))))
            opportunity = replace(
                opportunity, linked_targets=targets,
                contacts=(published_contacts or opportunity.contacts))
        attached.append(opportunity)
    return tuple(attached)


def capture_inputs(*, profile: dict, slug: str = "", pack: Any = None) -> dict:
    """Every press-time store read, performed ONCE and returned as data.

    The projection consuming this is pure, so a press that saves this
    mapping beside the pack can be replayed byte for byte after the notice
    store has ingested new days and the targets store has been enriched.
    """
    from tools.intelligence_graph.adapter import load_target_observations

    contract_terms, _coverage = _contract_terms(slug)
    terms = sorted(set(list(contract_terms) + _exact_terms(profile)
                       + _pack_notice_terms(pack)), key=str.casefold)
    naics_boundary, psc_boundary = _profile_boundaries(profile)
    candidates: dict = {}
    try:
        from tools.notice_store import connect as _connect
        conn = _connect()
        try:
            candidates = fetch_store_candidates(
                conn, exact_terms=terms, naics_boundary=naics_boundary,
                psc_boundary=psc_boundary)
        finally:
            conn.close()
    except Exception:                                     # noqa: BLE001
        candidates = {}
    from agents.golden_press.rival_footprint import load_cached
    return {"store_candidates": candidates,
            "store_search_terms": terms,
            "store_naics_boundary": naics_boundary,
            "store_psc_boundary": psc_boundary,
            "store_excluded_terms": list(
                (profile.get("capability_terms") or {}).get("excluded") or []),
            "targets_payload": load_target_observations(slug),
            "rival_footprint": load_cached(slug) if slug else {},
            "evidence_pack_v2": _load_evidence_pack_v2(slug)}


def build_market_map(pack: Any, *, profile: dict, slug: str = "",
                     as_of: str = "", inputs: Any = _LIVE
                     ) -> FederalMarketMapDocument:
    """The whole projection, assembled before narrative and before render."""
    from agents.golden_press.prime_posture import derive_posture

    posture = derive_posture(pack)
    company = _company(pack, profile, posture)
    # The capture happens BEFORE any lane that reads a store, so one press
    # has one view of the world and the sidecar carries all of it.
    captured: dict = {}
    if inputs is _LIVE or not isinstance(inputs, dict):
        captured = capture_inputs(profile=profile, slug=slug, pack=pack)
    else:
        captured = inputs
    measured_rivals = captured.get("rival_footprint")
    footprint = _footprint(pack, _coverage_contract(slug))
    if not footprint.record_count:
        # THE MARKET IS WHAT THE NAMED RIVALS WIN. When the pack's own award
        # lane holds nothing in this capability market, the category is not
        # unknown: it is the federal work the named product rivals hold. That
        # is measured, linked and exactly the question a client is asking.
        footprint = _rival_footprint(
            slug, measured=measured_rivals) or footprint
    competition = _competition(pack, profile, slug,
                           measured=measured_rivals)
    # THE PACK LANE PASSES THE SAME GATE AS THE STORE LANE. The pack's
    # opportunities were selected before the capability contract existed, so
    # it carried an AN/APX-123A(V) IFF transponder replacement into a
    # supplier-risk market map. One gate, both lanes, judging the RECORD
    # (title + its own description), never the finished row's fit note.
    # REJECTED IS A FINDING, NOT A DELETION: every rejected record rides the
    # receipts with its reason, so the artifact says "screened and
    # rejected", never silently thins the set.
    _contract = _coverage_contract(slug)
    rejected_rows: list = []
    profile_exclusions = tuple(
        (profile.get("capability_terms") or {}).get("excluded") or ())
    opportunities = _opportunities(
        pack, posture, contract=_contract, rejected_out=rejected_rows,
        excluded_terms=profile_exclusions)
    pack_kept = list(opportunities)
    # THE STORE LANE. The pack's L1 slice is capped and was selected before
    # the frame was widened, so it carries stale picks. The store holds the
    # live shaping records and costs nothing to read.
    try:
        extra = store_opportunities(
            captured.get("store_candidates") or {}, slug=slug, as_of=as_of,
            rejected_out=rejected_rows,
            excluded_terms=captured.get("store_excluded_terms") or ())
        have = {o.key for o in opportunities}
        opportunities = opportunities + tuple(
            o for o in extra if o.key not in have)
    except Exception:                                     # noqa: BLE001
        pass
    # FAMILY COLLAPSE ACROSS LANES. The store lane dedupes its own rows,
    # but a requirement can arrive once from the pack and once from the
    # store under different posting numbers, and the two PIVOT postings
    # rendered as twins exactly that way. One requirement is one row; the
    # duplicate's identifier survives on the kept row.
    families_raw = len(opportunities)
    by_family: dict = {}
    ordered: list = []
    for opp in opportunities:
        fam = _family(getattr(opp, "title", "")) or opp.key
        held = by_family.get(fam)
        if held is None:
            by_family[fam] = opp
            ordered.append(fam)
            continue
        keep, drop = held, opp
        # Prefer the row that is more actionable: contacts, then value,
        # then a stated response date.
        def _rank(o: Any) -> tuple:
            return (1 if o.contacts else 0, 1 if o.value else 0,
                    1 if _clean(o.response_date) else 0)
        if _rank(opp) > _rank(held):
            keep, drop = opp, held
        extra_id = _clean(getattr(drop, "identifier", ""))
        if extra_id and extra_id != _clean(keep.identifier)                 and not _HEX.match(extra_id):
            suffix = f" Also posted as {extra_id}."
            if suffix not in (keep.fit or ""):
                keep = replace(keep, fit=(keep.fit or "").rstrip() + suffix)
        by_family[fam] = keep
    opportunities = tuple(by_family[f] for f in ordered)
    # THE CLOCK RULES EVERY WORD (grading round 1, fatal). A press dated
    # 2026-08-07 carried "Reply ... before responses close" on nine records
    # whose windows had already closed, because personalisation never
    # consulted the date. Now the clock is derived FIRST and the motion,
    # the action and the ranking all obey it. A closed window is a
    # successor-research order, never an instruction to reply.
    day = _clean(as_of)[:10]

    def _prose_ident(o: Any) -> str:
        """The record's publishable identifier for prose, or empty.

        The same law the renderer keeps: a 32-char hex key is a database
        address, not an identifier a contracting officer would recognise,
        and Gate E caught one walking into a Riverbed action sentence.
        """
        ident = _clean(o.identifier)
        return "" if _HEX.match(ident) else ident

    def _clock(o: Any) -> str:
        if o.source_kind == FORECAST:
            return "forecast"
        d = _clean(o.response_date)[:10]
        if not d:
            return "undated"
        return "future" if d >= day else "past"

    personalised = []
    for opp in opportunities:
        clock = _clock(opp)
        first = (opp.contacts or ({},))[0]
        name = _clean(first.get("name")) or _clean(first.get("email"))
        if clock == "past":
            # The record's own identifier rides the ask, so two records
            # sharing a contact and a date still read as themselves.
            ident = _prose_ident(opp)
            opp = replace(
                opp, motion=STATUS_CHECK,
                # ONE sentence, carrying the identifier: two records that
                # share a contact and a closing date must still read as
                # themselves, and a split sentence hands the density rule a
                # shared half.
                action=((f"Window closed {_clean(opp.response_date)}; ask "
                         f"{name} whether {ident or 'this requirement'} has "
                         f"a successor planned and request its number.")
                        if name else
                        (f"Window closed {_clean(opp.response_date)}; "
                         f"research the successor to "
                         f"{ident or 'this requirement'} at "
                         f"{_clean(opp.agency).title() or 'the buyer'}.")))
        elif clock == "future" and opp.motion == SHAPE:
            ident = _prose_ident(opp)
            opp = replace(opp, action=(
                (f"Reply to {name} before responses close "
                 f"{_clean(opp.response_date)}; shaping input on "
                 f"{ident or 'this requirement'} lands only while the "
                 f"scope is still being written.") if name else
                (f"Respond before {_clean(opp.response_date)}; "
                 f"{ident or 'this requirement'} is still being scoped.")))
        elif clock == "undated":
            ident = _prose_ident(opp)
            subject = (ident or _clean(opp.title)[:40]
                       or "the official record")
            opp = replace(opp, action=(
                (f"Open {subject} and confirm the clock with {name}.")
                if name else
                f"Open {subject} and confirm the clock."))
        elif clock == "forecast":
            ident = _prose_ident(opp)
            opp = replace(opp, action=(
                f"Monitor {ident or 'this forecast'} and shape it before "
                f"it becomes a solicitation."))
        personalised.append(opp)
    opportunities = tuple(personalised)
    # OPERATOR RULINGS ARE FINAL. They apply AFTER the clock pass, because
    # a ruling's motion and action already speak the right clock language
    # ("watch for the successor", "do not contest the bridge") and the
    # personalization pass must never overwrite an operator decision: three
    # ruled records were stomped back to "status check" exactly that way.
    opportunities, ruling_receipt = _apply_record_rulings(
        slug, opportunities, rejected_rows)
    # RANK BY WHAT CAN BE ACTED ON TODAY. An opportunity whose point of
    # contact has a direct number is workable this afternoon; one with only
    # an inbox is workable this week; one with neither is a research order.
    # The operator reads top down, so the order has to reflect that.
    _CLOCK_ORDER = {"future": 0, "undated": 1, "forecast": 2, "past": 3}

    def _opp_rank(opp) -> tuple:
        people = list(opp.contacts or ())
        phone = 1 if any(_clean(p.get("phone")) for p in people) else 0
        email = 1 if any(_clean(p.get("email")) for p in people) else 0
        named = 1 if any(_clean(p.get("name")) for p in people) else 0
        return (_CLOCK_ORDER.get(_clock(opp), 4), -phone, -email, -named,
                _clean(getattr(opp, "title", "")).casefold())

    opportunities = tuple(sorted(opportunities, key=_opp_rank))
    opportunities = attach_v2_targets(
        opportunities, captured.get("evidence_pack_v2") or {})
    routes = _teaming(pack, profile, opportunities)
    contacts = _contacts(pack, slug or _norm(company.client_name),
                         opportunities, routes,
                         targets_payload=captured.get("targets_payload"))
    events = _events(pack)
    demand = _research_demand(company, footprint, competition, opportunities,
                              routes, contacts, events)
    forecasts_kept = sum(1 for o in opportunities
                         if o.source_kind == FORECAST)
    # Kept rows may be family-collapsed after the pack lane, so screened =
    # pack-lane kept forecasts + rejected forecasts, counted at the gate.
    forecasts_screened = sum(
        1 for o in pack_kept if o.source_kind == FORECAST) + sum(
        1 for r in rejected_rows if r.get("kind") == FORECAST)
    if forecasts_screened and not forecasts_kept:
        demand = demand + (ResearchOrder(
            kind="forecast_repull", section="opportunities",
            what=(f"{forecasts_screened} agency forecast records were "
                  "screened and none sits in the approved capability "
                  "frame; the forecast source pull predates the frame"),
            client_line=("Forecast research next: re-pull agency forecasts "
                         "under the approved capability frame."),
            count=forecasts_screened),)
    return FederalMarketMapDocument(
        client_name=company.client_name, as_of=as_of,
        company_understanding=company, category_footprint=footprint,
        competitive_position=competition,
        qualified_opportunities=opportunities, teaming_routes=routes,
        contact_actions=contacts, events=events, research_demand=demand,
        receipts={
            "version": MARKET_MAP_PROJECTION_VERSION,
            "posture": posture.get("posture"),
            "opportunity_keys": len({o.key for o in opportunities}),
            "opportunities": len(opportunities),
            "families_raw": families_raw,
            "families_collapsed": len(opportunities),
            "pack_lane": {
                "screened": len(pack_kept) + len(rejected_rows),
                "kept": len(pack_kept),
                "rejected": len(rejected_rows),
                "rejected_rows": rejected_rows[:12],
                "forecasts_screened": forecasts_screened,
                "forecasts_kept": forecasts_kept,
            },
            "operator_rulings": ruling_receipt,
            "competitor_source": competitor_source(profile),
            "notice_store": dict(getattr(pack, "notice_store", None) or {}),
            "model_calls": 0,
        })


# --------------------------------------------------------------------------- #
# Store-backed shaping opportunities
# --------------------------------------------------------------------------- #
_BAD_TITLE_CHARS = {"\u2014": ":", "\u2013": ":", "\ufffd": ":",
                    "\u2019": "'", "\u201c": '"', "\u201d": '"'}


def _title(value: Any) -> str:
    """A source title, safe to render. No em dash, no mis-encoded byte."""
    text = _clean(value)
    for bad, good in _BAD_TITLE_CHARS.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ",
                  text.replace(" : ", ": ").replace("::", ":"))
    return text.strip(" :")


def _family(title: str) -> str:
    """The requirement behind a posting, so one requirement counts once.

    Strips the prefixes agencies bolt on (`R408--`, `RFI -`, `Request for
    Information (RFI)`) and the office qualifier, leaving the subject.
    """
    low = _title(title).casefold()
    # Parentheticals are office qualifiers and posting numbers
    # ("(VA-26-00015670)", "(PIVOT)"), not the requirement.
    low = re.sub(r"\([^)]*\)", " ", low)
    low = re.sub(r"^[a-z]{1,2}\d{2,3}\s*-{1,2}\s*", "", low)
    low = re.sub(r"^(request for information|rfi|sources sought|"
                 r"special notice|presolicitation)\b[\s:*-]*", "", low)
    low = re.sub(r"\((?:rfi|sources sought|tcrs|fiar|pivot)\)", " ", low)
    low = re.sub(r"[^a-z0-9 ]+", " ", low)
    words = {w for w in low.split()
             if w not in _FAMILY_STOP and not any(c.isdigit() for c in w)}
    return " ".join(sorted(words))


_FAMILY_STOP = {"the", "of", "and", "for", "a", "an", "to", "services",
                "service", "support", "program", "procurement", "sources",
                "sought", "notice", "special", "rfi", "request",
                "information", "presolicitation", "tool", "tools",
                "loopback", "extension", "amendment", "amended", "update",
                "updated", "revision", "revised", "repost", "reposted"}


def fetch_store_candidates(conn, *, exact_terms: Any,
                           naics_boundary: Any = (),
                           psc_boundary: Any = ()) -> dict:
    """The ONE store read, captured as plain rows keyed by matched term.

    Everything downstream of this is pure, which is what lets a press
    capture these rows onto a sidecar and lets replay re-render the same
    document after the store has moved on. Replay from a bound pack must
    not change factual output, and a render-time SQL query is exactly how
    it would.
    """
    shaping = ("Sources Sought", "Request for Information", "RFI",
               "Special Notice", "Presolicitation", "Solicitation",
               "Combined Synopsis/Solicitation", "Request for Proposal",
               "Request for Quote")
    clause = " OR ".join("notice_type LIKE ?" for _ in shaping)
    terms = sorted({_clean(t).casefold() for t in (exact_terms or [])
                    if len(_clean(t)) >= 4})
    out: dict = {}
    naics = [str(code) for code in (naics_boundary or ()) if _clean(code)]
    psc = [str(code) for code in (psc_boundary or ()) if _clean(code)]

    # A client boundary turns the store into a small, relevant universe. In
    # that universe we can match government title wording order-free, so
    # "translation and interpretation" finds "interpretation and
    # translation". Description matches remain in the broader L1 analyst
    # lane. A final client opportunity must still name the capability in its
    # subject line, which prevents OASIS boilerplate from becoming a lead.
    if naics or psc:
        boundary_parts, boundary_params = [], []
        for code in naics:
            boundary_parts.append("naics LIKE ?")
            boundary_params.append(f"{code}%")
        for code in psc:
            boundary_parts.append("psc LIKE ?")
            boundary_params.append(f"{code}%")
        boundary = " OR ".join(boundary_parts)
        rows = conn.execute(
            f"SELECT notice_id, title, notice_type, sol_number, deadline, "
            f"posted, naics, set_aside, "
            f"poc_name, poc_email, poc_phone, poc_title, "
            f"poc_secondary_name, poc_secondary_email, poc_secondary_phone, "
            f"poc_secondary_title, description_prefix, "
            f"COALESCE(subtier, agency) org, office, url "
            f"FROM notices WHERE ({boundary}) AND ({clause}) "
            f"ORDER BY posted DESC",
            (*boundary_params, *(f"%{kind}%" for kind in shaping))).fetchall()
        from agents.golden_press.coverage_families import phrase_matches
        for row in rows:
            text = _clean(row["title"])
            for term in terms:
                if phrase_matches(term, text):
                    out.setdefault(term, []).append(dict(row))
        return out

    for term in terms:
        rows = conn.execute(
            f"SELECT notice_id, title, notice_type, sol_number, deadline, "
            f"posted, naics, set_aside, "
            f"poc_name, poc_email, poc_phone, poc_title, "
            f"poc_secondary_name, poc_secondary_email, poc_secondary_phone, "
            f"poc_secondary_title, description_prefix, "
            f"COALESCE(subtier, agency) org, office, url "
            f"FROM notices WHERE title LIKE ? AND ({clause}) "
            f"ORDER BY posted DESC LIMIT 20",
            (f"%{term}%", *(f"%{s}%" for s in shaping))).fetchall()
        if rows:
            out[term] = [dict(row) for row in rows]
    return out


def store_opportunities(source, *, exact_terms: Any = (), limit: int = 40,
                        slug: str = "", as_of: str = "",
                        rejected_out: Any = None,
                        excluded_terms: Any = ()) -> tuple:
    """Live shaping records from the local notice store. Zero network.

    TWO RULES LEARNED BY MEASURING, both of which the pack lane lacked.

    1 TITLE-EXACT, NOT DESCRIPTION-ANYWHERE. A description-wide match on
      generic words returned "Altitude Chambers Contractor Logistics
      Support" and "VISN H-Wave" as payment-integrity opportunities. The
      subject of a solicitation is in its title.
    2 DEDUPE BY SOLICITATION FAMILY, NOT NOTICE ID. One Air Force FIAR
      requirement was posted ELEVEN times; as notice ids that is eleven
      opportunities and as a solicitation number it is one.

    Every row carries its published POC, because a shaping record whose
    contact the government already published needs no enrichment at all.
    """
    # 3 SANITISE THE SOURCE TITLE. Government titles carry em dashes and
    #   mis-encoded bytes that arrive as a replacement character. Both are
    #   banned in output, and neither is the government's meaning: the dash
    #   is a separator. It becomes a colon, and the artefact stays clean.
    from agents.golden_press.coverage_families import best_match, load
    from agents.golden_press.coverage_families import phrase_matches
    contract = load(slug) if slug else {}
    if hasattr(source, "execute"):
        candidates = fetch_store_candidates(source, exact_terms=exact_terms)
    else:
        candidates = dict(source or {})
    best: dict = {}
    for term in sorted(candidates):
        for row in candidates[term]:
            key = _clean(row["sol_number"]) or _clean(row["title"])
            if not key:
                continue
            title = _title(row["title"])
            description = _clean(row.get("description_prefix")
                                 if isinstance(row, dict)
                                 else row["description_prefix"])
            exclusion = next((phrase for phrase in excluded_terms
                              if phrase_matches(_clean(phrase),
                                                f"{title} {description}")), "")
            if exclusion:
                if rejected_out is not None:
                    rejected_out.append({
                        "identifier": key, "title": title[:100],
                        "kind": NOTICE,
                        "reason": f"excluded client term: {_clean(exclusion)}",
                        "reason_class": "rejected_client_exclusion"})
                continue
            deadline = _clean(row["deadline"])[:10]
            if deadline and _clean(as_of) and deadline < _clean(as_of)[:10]:
                if rejected_out is not None:
                    rejected_out.append({
                        "identifier": key, "title": title[:100],
                        "kind": NOTICE, "reason": "response date passed",
                        "reason_class": "closed_historic"})
                continue
            # THE CONTRACT IS THE GATE. A title hit is a candidate, not an
            # opportunity: the positive context gate, the controlled-family
            # triggers and the boilerplate rejections all still have to pass.
            verdict = {}
            if contract:
                verdict = best_match(
                    title, contract, description=description)
                if not verdict:
                    # REJECTED IS A FINDING (operator ruling 1): the record
                    # rides the ledger with the reason the gate gave the
                    # matched term, never a silent deletion.
                    if rejected_out is not None:
                        from agents.golden_press.coverage_families import (
                            qualify)
                        why = qualify(
                            title=_title(row["title"]),
                            description=_clean(row["description_prefix"]),
                            phrase=term, contract=contract)
                        rejected_out.append({
                            "identifier": key,
                            "title": _title(row["title"])[:100],
                            "kind": NOTICE,
                            "reason": _clean(why.get("rule")),
                            "reason_class": _clean(why.get("reason_class"))
                            or "rejected_out_of_frame"})
                    continue
            entry = best.setdefault(key.casefold(), {"row": row, "terms": set()})
            entry["terms"].add(term)
            entry.setdefault("family", _clean(verdict.get("family")))
    # SECOND PASS: collapse by requirement family. A solicitation number is
    # the better key when it exists, but a posting without one is still the
    # same requirement, and TCRS arrived twice on exactly that gap.
    families: dict = {}
    for entry in best.values():
        fam = _family(entry["row"]["title"]) or _clean(
            entry["row"]["sol_number"]).casefold()
        held = families.get(fam)
        if held is None:
            entry.setdefault("also", set())
            families[fam] = entry
            continue
        held["terms"] |= entry["terms"]
        # The duplicate's identifier survives ON the kept row, so one
        # requirement posted twice renders once and still names both ids.
        other = (_clean(entry["row"]["sol_number"])
                 or _clean(entry["row"]["notice_id"]))
        if other:
            held.setdefault("also", set()).add(other)
        # keep the posting that carries a solicitation number, then the newer
        if (not _clean(held["row"]["sol_number"])
                and _clean(entry["row"]["sol_number"])):
            held["row"] = entry["row"]
    best = families
    out = []
    for entry in sorted(best.values(), key=lambda e: -len(e["terms"])):
        row, terms = entry["row"], entry["terms"]
        also = sorted(entry.get("also") or ())
        ref = EvidenceReference(
            source_id=_clean(row["sol_number"]) or _clean(row["notice_id"]),
            source_kind=NOTICE,
            source_url=(f"https://sam.gov/opp/{_clean(row['notice_id'])}/view"
                        if len(_clean(row["notice_id"])) == 32 else ""),
            claim_roles=(CLAIM_OPPORTUNITY, CLAIM_CONTACT),
            label=_title(row["title"]))
        ntype = _clean(row["notice_type"])
        # THE WHOLE PUBLISHED BLOCK, NOT JUST THE FIRST NAME. A notice
        # carries a title, a phone and often a SECOND named official, and
        # reading only name+email threw away real reachable people. Measured
        # on apexanalytix: two extra named officials and two job titles were
        # sitting unread in columns the store already had.
        contacts = []
        for prefix in ("poc", "poc_secondary"):
            email = _clean(row[f"{prefix}_email"])
            name = _clean(row[f"{prefix}_name"])
            if not email and not name:
                continue
            contacts.append({
                "name": name, "email": email,
                "phone": _clean(row[f"{prefix}_phone"]),
                "title": _clean(row[f"{prefix}_title"]),
                "role": "primary" if prefix == "poc" else "secondary",
                "published": True})
        contacts = tuple(contacts)
        # ROUTE, NOT REJECTION (operator ruling 3): audit-family records
        # keep their fit and change their MOTION when the route demands a
        # partner. Capability fit and access feasibility stay separate.
        from agents.golden_press.coverage_families import route_motion
        deadline = _clean(row["deadline"])[:10]
        routing = route_motion(
            family=_clean(entry.get("family")),
            title=_title(row["title"]),
            description=_clean(row["description_prefix"]),
            naics=_clean(row.get("naics") if isinstance(row, dict)
                         else row["naics"]),
            set_aside=_clean(row.get("set_aside") if isinstance(row, dict)
                             else row["set_aside"]),
            response_past=bool(deadline and _clean(as_of)
                               and deadline < _clean(as_of)[:10]))
        out.append(Opportunity(
            key=opportunity_key(NOTICE, ref.source_id),
            identifier=ref.source_id, source_kind=NOTICE,
            title=_title(row["title"]), agency=_clean(row["org"]),
            office=_clean(row["office"]),
            opportunity_type=ntype,
            value=None,
            response_date=deadline,
            fit=(f"Matches {', '.join(sorted(terms)[:3])}."
                 + (f" Also posted as {', '.join(also)}." if also else "")),
            motion=_clean(routing.get("motion")) or SHAPE,
            action=("Respond to shape the requirement before it is "
                    "finalised."),
            access_route=_clean(routing.get("access_note"))
            or "Reply to the published point of contact.",
            contacts=contacts, evidence=(ref,),
            coverage=(complete("published contact")
                      if contacts else research_next(
                          missing=("published contact",),
                          action="Contact research next",
                          demand_kind=DEMAND_PARTNER_CONTACT))))
        if len(out) >= limit:
            break
    return tuple(out)
