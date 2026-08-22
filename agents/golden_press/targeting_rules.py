"""Deterministic targeting rules over the decision-rule output (Band 09).

WHY THIS MODULE (operator, 2026-08-05). The assessment ends by establishing
what is true. Band 09 states who to call about it. That is the action layer
the whole document feeds, and it has to be as deterministic as the bands
above it: a contact is only ever a route to a record the report already
cites, never a claim of its own.

DOCTRINE. Pure functions over a pack. No LLM, no network, no metered call.
One targeting spec per R1 decision card, R2 displacement alert, and R4
qualify row, computed from pack data only. Every spec carries the evidence
rows it was built from, exactly as R1 to R4 do, and every rule prints its
own basis so a reader can see why a persona set was sought.

THE PERSONA LADDER LIVES IN DATA, NOT HERE. data/reference/
targeting_personas.json carries tiers 1 to 4 (persona, component, titles,
seniorities) and the row-class rules that select among them. A title added
there changes what the supply step searches for with no code change, the
same tuning surface the polysemy corpus has. This module is that file's one
owner.

THE JOIN IS THE ADMISSION TICKET (contract amendment v1.4). A spec exists
only when it joins at least one pack record id, because those ids are what
the evidence bands render and the contact row has to hang off one. A row
that joins nothing is receipted as untargeted, never quietly dropped.

SIDE CLASSIFICATION IS IMPORTED, NEVER REIMPLEMENTED. Recipient identity
rides ``rollups.resolve_relationship``, exactly as decision_rules does, so
a client alias fixed there is fixed here.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

TARGETING_RULES_VERSION = "targeting_rules.v1.2026-08-05"

_ROOT = Path(__file__).resolve().parents[2]
PERSONAS_PATH_ENV = "LILA_TARGETING_PERSONAS"
_PERSONAS_DEFAULT = _ROOT / "data" / "reference" / "targeting_personas.json"

# The row classes this layer knows. A rule names exactly one.
ROW_CLASSES = ("renewal", "displacement", "adjacency")

_cache: dict = {}


class TargetingError(RuntimeError):
    """The persona reference file is missing or malformed."""


def _personas_path() -> Path:
    return Path(os.environ.get(PERSONAS_PATH_ENV, "") or _PERSONAS_DEFAULT)


def load_personas() -> dict:
    """The persona ladder and row-class rules, from the reference file.

    Loud on a malformed file, exactly like the contract loader: a tier with
    no titles would silently search for nobody, and a rule naming an unknown
    row class would silently target nothing.
    """
    path = _personas_path()
    key = str(path)
    if key in _cache:
        return _cache[key]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TargetingError(f"targeting personas unreadable at {path}: {exc}")
    except ValueError as exc:
        raise TargetingError(f"targeting personas malformed at {path}: {exc}")
    tiers = payload.get("tiers")
    rules = payload.get("rules")
    if not isinstance(tiers, list) or not tiers:
        raise TargetingError("targeting personas carry no tier ladder")
    if not isinstance(rules, list) or not rules:
        raise TargetingError("targeting personas carry no rules")
    seen_tiers = set()
    for tier in tiers:
        number = tier.get("tier")
        if not isinstance(number, int) or number in seen_tiers:
            raise TargetingError(
                f"tier {number!r} is missing or repeated in the ladder")
        seen_tiers.add(number)
        if not tier.get("titles"):
            raise TargetingError(f"tier {number} carries no titles")
        if tier.get("target") not in ("buying_component", "paper_holder"):
            raise TargetingError(
                f"tier {number} names an unknown target {tier.get('target')!r}")
    seen_rules = set()
    for rule in rules:
        rid = rule.get("rule_id")
        if not rid or rid in seen_rules:
            raise TargetingError(f"rule id {rid!r} is missing or repeated")
        seen_rules.add(rid)
        if rule.get("row_class") not in ROW_CLASSES:
            raise TargetingError(
                f"rule {rid} names an unknown row class "
                f"{rule.get('row_class')!r}")
        wanted = rule.get("seeks_tiers") or []
        unknown = [t for t in wanted if t not in seen_tiers]
        if not wanted or unknown:
            raise TargetingError(
                f"rule {rid} seeks tier(s) the ladder does not define: "
                f"{unknown or 'none named'}")
    _cache[key] = payload
    return payload


def _rule(rule_id: str) -> dict:
    for row in load_personas()["rules"]:
        if row["rule_id"] == rule_id:
            return row
    raise TargetingError(f"unknown targeting rule: {rule_id}")


def tier(number: int) -> dict:
    for row in load_personas()["tiers"]:
        if row["tier"] == number:
            return row
    raise TargetingError(f"unknown persona tier: {number}")


def _slug(value: str) -> str:
    """Lowercase slug. Deliberately lowercase: a spec id must never be
    contract-number shaped, or the validator's id-provenance scan would read
    it as an unprovenanced identifier."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _titles_for(tiers_sought: list) -> list:
    """Every searchable title across the sought tiers, ladder order kept."""
    out: list = []
    for number in tiers_sought:
        for title in tier(number)["titles"]:
            if title not in out:
                out.append(title)
    return out


def _seniorities_for(tiers_sought: list) -> list:
    out: list = []
    for number in tiers_sought:
        for value in tier(number).get("seniorities") or []:
            if value not in out:
                out.append(value)
    return out


def tier_for_title(spec: dict, title: Any) -> Optional[int]:
    """Which persona rung a resolved title actually answers.

    The supply step searches a spec's whole title set at once, so the tier
    has to be attributed from the title that came back, not assumed to be
    the first rung sought.

    NO SILENT DEFAULT (operator ruling 2026-08-06). An unrecognised title
    returns None, and the person carries persona_tier null with an explicit
    unattributed state downstream. The previous behaviour returned the FIRST
    sought tier, so "Supervisory IT Specialist" was presented as a confident
    tier 1 when nothing had matched at all. A person may never appear at a
    rung merely because their title resembles one.
    """
    sought = list(spec.get("seeks_tiers") or [])
    text = " ".join(str(title or "").split()).casefold()
    if not text:
        return None
    for number in sought:
        known = (spec.get("persona_titles") or {}).get(str(number)) or []
        for candidate in known:
            if str(candidate).casefold() in text:
                return number
    return None


def _join_row(row: dict) -> dict:
    """One joined evidence row, in the decision band's own row grammar."""
    return {
        "record_id": row.get("record_id"),
        "url": row.get("url"),
        "recipient": row.get("recipient"),
        "agency": row.get("agency"),
        "sub_agency": row.get("sub_agency"),
        "period_end": row.get("period_end"),
    }


def _spec(*, rule_id: str, buying_component: str, office: Optional[str],
          target_kind: str, target_name: str, rationale: str,
          joins: list, evidence: dict, ordinal: int) -> dict:
    rule = _rule(rule_id)
    tiers_sought = list(rule["seeks_tiers"])
    return {
        "spec_id": f"{rule_id.casefold()}-{ordinal:02d}-"
                   f"{_slug(target_name)[:40] or 'target'}",
        "rule_id": rule_id,
        "row_class": rule["row_class"],
        "source_rule": rule["source_rule"],
        "buying_component": buying_component,
        "office": office or None,
        "target_org": {"kind": target_kind, "name": target_name},
        "seeks_tiers": tiers_sought,
        "person_titles": _titles_for(tiers_sought),
        "person_seniorities": _seniorities_for(tiers_sought),
        # The zero-credit query bound, carried onto the spec so the supply
        # step sends it and a replay can see what the search was bounded to.
        # Operator-owned: it lives in the persona reference file.
        "person_locations": list(
            (load_personas().get("query_bounds") or {}).get(
                "person_locations") or []),
        # tier -> its own titles, so the supply step can attribute a
        # resolved person to the rung their title actually answers rather
        # than to whichever rung happened to be first.
        "persona_titles": {str(n): list(tier(n)["titles"])
                           for n in tiers_sought},
        "rationale": rationale,
        "join_record_ids": [str(j["record_id"]) for j in joins
                            if j.get("record_id")],
        "join_records": joins,
        "evidence": evidence,
        "rule_basis": rule["basis"],
    }


# --------------------------------------------------------------------------- #
# T1 renewal / T2 displacement · one spec per R1 card
# --------------------------------------------------------------------------- #
def t1_t2_from_r1(pack: Any, decisions: dict, receipt: dict) -> list:
    """R1 cards, classed by WHOSE clock closes first.

    A card whose nearest dated period end belongs to a CLIENT record is a
    renewal (defend what is held); one whose nearest end belongs to a RIVAL
    record is a displacement window. The card already computed that nearest
    record, so the class is read off the evidence rather than re-derived,
    and the receipt names which side supplied it.
    """
    out = []
    cards = ((decisions.get("r1") or {}).get("cards") or [])
    for index, card in enumerate(cards, start=1):
        client_rows = list(card.get("client_records") or [])
        rival_rows = list(card.get("rival_records") or [])
        nearest = str(card.get("nearest_record_id") or "")
        client_ids = {str(r.get("record_id")) for r in client_rows}
        renewal = nearest in client_ids
        rule_id = "T1" if renewal else "T2"
        buyer = _clean(card.get("agency")) or "this buying component"
        joins = [_join_row(r) for r in (client_rows + rival_rows)
                 if r.get("record_id")]
        if not joins:
            receipt["no_join_not_targeted"].append(
                {"source": "R1", "buyer": buyer,
                 "why": "the card carries no record id to join"})
            continue
        end = str(card.get("nearest_end") or "")[:10]
        # The house date display has ONE owner (render._day); a second
        # formatter here would put two date styles in one document.
        from agents.golden_press.render import _day
        shown = _day(end) or end
        if renewal:
            rationale = (f"{buyer} holds current client paper closing "
                         f"{shown}, so the renewal is decided here.")
        else:
            rationale = (f"{buyer} carries current rival paper closing "
                         f"{shown}, which is the displacement window.")
        office = next((_clean(r.get("office")) for r in client_rows
                       if r.get("office")), None)
        out.append(_spec(
            rule_id=rule_id, buying_component=buyer, office=office,
            target_kind="buying_component", target_name=buyer,
            rationale=rationale, joins=joins,
            evidence={
                "nearest_record_id": nearest,
                "nearest_end": end,
                "nearest_side": "client" if renewal else "rival",
                "client_records": len(client_rows),
                "rival_records": len(rival_rows),
                "rival_entities": list(card.get("rival_entities") or []),
                "materiality_tier": (card.get("materiality") or {}).get("tier"),
            },
            ordinal=index))
    return out


# --------------------------------------------------------------------------- #
# T2 displacement · one spec per R2 alert
# --------------------------------------------------------------------------- #
def normalize_r2_alerts(pack: Any, alerts: Any, receipt: dict) -> list:
    """Both R2 shapes, reduced to the one this module reads.

    WHY (measured 2026-08-06). Packs written before the current decision-rule
    revision store an alert's client history as ``client_history_record_ids``,
    a bare id list, while ``t2_from_r2`` reads ``client_history_records``,
    the joined evidence rows. Every one of the 30 legacy alerts on disk
    (riverbed 7, thinklogical 20, varonis 3) therefore joined nothing and
    produced no targeting spec. The evidence was never missing: it was under
    a different key.

    Resolution runs against the SEALED PACK ONLY. An id the pack does not
    carry is receipted as unresolvable and dropped; nothing here invents a
    record, because a fabricated join would defeat the whole point of the
    join law. Alerts already in the current shape pass through untouched.
    """
    from agents.golden_press.decision_rules import _evidence_row

    by_id = {str(r.record_id): r for r in (getattr(pack, "records", []) or [])}
    out: list = []
    migrated = 0
    unresolved: list = []
    for alert in (alerts or []):
        if not isinstance(alert, dict):
            continue
        if alert.get("client_history_records") is not None:
            out.append(alert)                       # already current shape
            continue
        ids = alert.get("client_history_record_ids")
        if ids is None:
            out.append(alert)                       # neither key: leave it
            continue
        rows = []
        for rid in ids:
            record = by_id.get(str(rid))
            if record is None:
                unresolved.append(str(rid))
                continue
            rows.append(_evidence_row(record))
        migrated += 1
        # The legacy alert also flattens what the current shape nests. Map
        # it rather than reading two dialects downstream: one translation
        # point, the notice-store lesson applied to our own schema.
        source = alert.get("source")
        if not isinstance(source, dict):
            source = {"kind": alert.get("kind"),
                      "record_id": alert.get("record_id"),
                      "url": alert.get("url"),
                      "title": alert.get("title")}
        context = alert.get("context")
        if not context:
            context = ("sole_source_intent" if alert.get("sole_source_phrase")
                       else "award" if alert.get("is_award") else "notice")
        out.append(dict(
            alert,
            client_history_records=rows,
            source=source,
            context=context,
            sentence=alert.get("sentence") or alert.get("matched_sentence"),
        ))
    receipt["r2_legacy_migrated"] = migrated
    receipt["r2_ids_unresolvable"] = sorted(set(unresolved))
    return out


def t2_from_r2(pack: Any, decisions: dict, receipt: dict) -> list:
    """R2 displacement alerts.

    The alert's own source may be a store notice, which the evidence bands
    do not render, so the join runs on the CLIENT HISTORY records the alert
    already carries: those are pack records and they render in bands 04 and
    05. An alert with no client history joins nothing and is receipted.
    """
    out = []
    alerts = normalize_r2_alerts(
        pack, ((decisions.get("r2") or {}).get("alerts") or []), receipt)
    for index, alert in enumerate(alerts, start=1):
        history = list(alert.get("client_history_records") or [])
        joins = [_join_row(r) for r in history if r.get("record_id")]
        agency = _clean(alert.get("agency")) or "this agency"
        competitor = _clean(alert.get("competitor")) or "a named rival"
        if not joins:
            receipt["no_join_not_targeted"].append(
                {"source": "R2", "buyer": agency,
                 "why": "the alert carries no client-history record to join"})
            continue
        # The buying component is the subtier on the client paper when the
        # record names one; the corridor agency is the toptier fallback.
        buyer = next((_clean(r.get("sub_agency")) for r in history
                      if r.get("sub_agency")), "") or agency
        context = _clean(alert.get("context")).replace("_", " ") or "notice"
        rationale = (f"{competitor} is named at {agency} in a {context} where "
                     f"the client already holds award history.")
        out.append(_spec(
            rule_id="T2", buying_component=buyer, office=None,
            target_kind="buying_component", target_name=buyer,
            rationale=rationale, joins=joins,
            evidence={
                "competitor": competitor,
                "context": alert.get("context"),
                "corridor_agency": agency,
                "source_kind": (alert.get("source") or {}).get("kind"),
                "client_history_records": len(history),
            },
            ordinal=index))
    return out


# --------------------------------------------------------------------------- #
# T3 adjacency · one spec per R4 qualify row
# --------------------------------------------------------------------------- #
def t3_from_r4(pack: Any, decisions: dict, receipt: dict) -> list:
    """R4 qualify rows, routed through the PAPER HOLDER.

    Nothing has posted on a forecast, so the buying component is not yet the
    actionable surface: the route is. The paper holder is the recipient on
    the client-history award that qualified the forecast, which on federal
    award data is the reseller or prime the client's product already rides.
    A row whose only recipient IS the client itself offers no third-party
    route, so no tier 4 spec exists and the row is receipted rather than
    targeted at nobody.
    """
    from agents.golden_press.rollups import resolve_relationship

    out = []
    rows = ((decisions.get("r4") or {}).get("rows") or [])
    for index, row in enumerate(rows, start=1):
        history = list(row.get("client_history_records") or [])
        joins = [_join_row(r) for r in history if r.get("record_id")]
        forecast = row.get("forecast") or {}
        title = _clean(forecast.get("title"))[:70] or "a published forecast"
        department = _clean(row.get("department")) or "this department"
        if not joins:
            receipt["no_join_not_targeted"].append(
                {"source": "R4", "buyer": department,
                 "why": "the qualify row carries no client-history record "
                        "to join"})
            continue
        holder = ""
        holder_row = None
        for record in history:
            name = _clean(record.get("recipient"))
            if not name:
                continue
            try:
                relationship = resolve_relationship(name, pack)["relationship"]
            except Exception:  # noqa: BLE001 - identity never sinks a rule
                relationship = "unknown"
            if relationship == "client_entity":
                continue
            holder, holder_row = name, record
            break
        if not holder:
            receipt["no_route_not_targeted"].append(
                {"source": "R4", "department": department, "forecast": title,
                 "why": "the client-history evidence names no paper holder "
                        "other than the client, so there is no route to work"})
            continue
        buyer = _clean(forecast.get("sub_agency")) or _clean(
            forecast.get("agency")) or department
        rationale = (f"{holder} holds the client paper that qualifies "
                     f"{title} at {department}.")
        out.append(_spec(
            rule_id="T3", buying_component=buyer, office=None,
            target_kind="paper_holder", target_name=holder,
            rationale=rationale, joins=[_join_row(holder_row)],
            evidence={
                "forecast_record_id": forecast.get("record_id"),
                "forecast_title": title,
                "department": department,
                "grain": row.get("grain"),
                "paper_holder": holder,
                "paper_holder_record_id": (holder_row or {}).get("record_id"),
                "client_history_records": len(history),
            },
            ordinal=index))
    return out


# --------------------------------------------------------------------------- #
# the one assembly the press stores on the pack
# --------------------------------------------------------------------------- #
def build_targeting(pack: Any, *, contacts: Optional[dict] = None) -> dict:
    """Targeting specs for one pack, plus the persona ladder and receipt.

    ``contacts`` is the stored enrichment payload the supply step wrote
    (``{"receipt": {...}, "contacts": [...]}``) or None when the step has
    never run for this client. It is carried onto the pack verbatim so a
    saved-inputs replay re-renders the band from the same bytes and the
    press itself never calls anything.
    """
    personas = load_personas()
    decisions = getattr(pack, "decisions", None) or {}
    receipt: dict = {
        "decisions_computed": bool(decisions),
        "rows_read": {
            "r1": len(((decisions.get("r1") or {}).get("cards") or [])),
            "r2": len(((decisions.get("r2") or {}).get("alerts") or [])),
            "r4": len(((decisions.get("r4") or {}).get("rows") or [])),
        },
        "no_join_not_targeted": [],
        "no_route_not_targeted": [],
        "personas_version": personas.get("version"),
    }
    specs: list = []
    if decisions:
        specs += t1_t2_from_r1(pack, decisions, receipt)
        specs += t2_from_r2(pack, decisions, receipt)
        specs += t3_from_r4(pack, decisions, receipt)

    # The join law, enforced against the pack's OWN record ids: a spec may
    # only join a record this pack carries, because those are the ids the
    # evidence bands render. Anything else is dropped and counted.
    known = {str(r.record_id) for r in (getattr(pack, "records", []) or [])}
    kept: list = []
    for spec in specs:
        joined = [j for j in spec["join_records"]
                  if str(j.get("record_id")) in known]
        if not joined:
            receipt["no_join_not_targeted"].append(
                {"source": spec["source_rule"], "buyer": spec["target_org"]["name"],
                 "why": "no joined record id is carried by this pack"})
            continue
        spec = dict(spec)
        spec["join_records"] = joined
        spec["join_record_ids"] = [str(j["record_id"]) for j in joined]
        kept.append(spec)

    fired = {spec["rule_id"] for spec in kept}
    sought: list = []
    for spec in kept:
        for number in spec["seeks_tiers"]:
            if number not in sought:
                sought.append(number)
    defined = [row["tier"] for row in personas["tiers"]]
    receipt["specs_built"] = len(kept)
    receipt["tiers_defined"] = defined
    receipt["tiers_sought"] = sorted(sought)
    # A defined tier no rule sought is disclosed, never silent: the ladder is
    # a tuning surface and an unused persona is a decision awaiting a rule.
    receipt["tiers_defined_not_sought"] = [t for t in defined
                                           if t not in sought]
    receipt["rules"] = [
        {"rule_id": row["rule_id"], "row_class": row["row_class"],
         "source_rule": row["source_rule"], "when": row["when"],
         "seeks_tiers": list(row["seeks_tiers"]), "basis": row["basis"],
         "fired": row["rule_id"] in fired}
        for row in personas["rules"]]

    return {
        "version": TARGETING_RULES_VERSION,
        "personas_version": personas.get("version"),
        "ladder": [dict(row) for row in personas["tiers"]],
        "specs": kept,
        "receipt": receipt,
        "contacts": list((contacts or {}).get("contacts") or []),
        "enrichment_receipt": (contacts or {}).get("receipt"),
    }
