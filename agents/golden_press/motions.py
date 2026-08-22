"""Motions: the commercial unit a title strategy is reasoned for.

WHY (operator + reviewer, 2026-08-06). Band 09's titles came from a static
four-tier ladder, so an observability displacement at a Navy SYSCOM and a
cryptocurrency training programme at FBI Cyber searched for the same people.
The value is in reasoning about who cares about THIS capability in THIS
motion, and a motion is the unit that reasoning attaches to.

TWO KEYS, NOT ONE (reviewer ruling R1). `(row_class, org_kind)` BATCHES
specs; it does not key a title set. The extra axis is not the same on both
sides:

  buying_component -> (row_class, org_kind, mission_applicability)
      DoD carries Authorizing Official, PEO and SYSCOM programme office;
      civilian carries OCFO and bureau CIO. One set covering both is generic
      by construction, which is the defect this module exists to remove.
      Measured: red_hat's single displacement motion spans FBI, IRS and OCFO
      alongside Army, DISA, Air Force and Navy.

  paper_holder     -> (row_class, org_kind, holder_identity, route_role)
      The target is a COMPANY. Agency family is meaningless there. What
      decides the titles is which holder, and whether it sits on the cited
      record as reseller, prime, or vehicle holder.

DETERMINISTIC. Pure functions over specs and pack records: no LLM, no
network, no metered call. The inference that follows is model work; the
batching and keying that feed it are not, so a replay groups identically.

AGENCY FAMILY IS IMPORTED, NEVER REIMPLEMENTED. `tools.fceb` is the catalog
owner for what counts as defence versus civilian, following the same rule
the decision band applies to company identity.
"""

from __future__ import annotations

import re
from typing import Any

MOTIONS_VERSION = "motions.v1.2026-08-06"

# The commercial vocabulary a motion speaks. Derived, never model-written.
MOTION_LABELS = {
    ("renewal", "buying_component"): "defend renewal",
    ("displacement", "buying_component"): "validate displacement",
    ("adjacency", "buying_component"): "shape future requirement",
    ("adjacency", "paper_holder"): "engage paper holder",
    ("displacement", "paper_holder"): "partner or channel route",
    ("renewal", "paper_holder"): "engage paper holder",
}

# What a motion is trying to achieve, as an outreach objective. Deterministic
# template per class; the model never writes these.
MOTION_OBJECTIVES = {
    "defend renewal": (
        "confirm who owns the renewal decision and whether the vehicle and "
        "scope carry forward unchanged"),
    "validate displacement": (
        "establish whether the named rival is incumbent, specified, or "
        "merely referenced on this requirement"),
    "shape future requirement": (
        "reach the requirement owner while the scope is still being written"),
    "engage paper holder": (
        "establish whether this holder will carry the requirement and who "
        "inside it owns the route"),
    "partner or channel route": (
        "qualify the channel route to a requirement the client cannot reach "
        "directly"),
}

# Mission applicability, buyer side only. Deliberately coarse: the question
# a title set answers is which vocabulary a buying organisation speaks, and
# defence and civilian are the two that differ enough to matter. A finer
# split (SYSCOM vs combatant command) is a tuning question for the persona
# reference file, not a keying question.
DEFENSE = "defense"
CIVILIAN = "civilian"
MIXED = "mixed"
UNKNOWN_MISSION = "unknown"

_DEFENSE_TOKENS = (
    "defense", "defence", "army", "navy", "air force", "marine", "space force",
    "dod", "joint", "combatant", "sysCom".casefold(), "disa", "dla", "dtra",
    "socom", "centcom", "northcom", "southcom", "eucom", "indopacom",
    "national guard", "coast guard", "missile defense", "darpa",
)

# Route roles a paper holder can occupy on a cited record. What the holder
# IS decides who to call inside it: a reseller's channel manager and a
# prime's capture lead are different people doing different jobs.
RESELLER = "reseller"
PRIME = "prime"
VEHICLE_HOLDER = "vehicle_holder"
UNKNOWN_ROUTE = "unknown_route"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean(value).casefold()).strip("-")


def mission_applicability(agencies: Any) -> str:
    """Defence, civilian, mixed, or unknown, over a set of agency names.

    Positive recognition only. An agency the tokens do not recognise does not
    become civilian by default: it contributes nothing, and a motion whose
    agencies are all unrecognised keys as ``unknown`` so the inference is
    told it is reasoning without a mission signal rather than being handed a
    confident wrong one.
    """
    seen = set()
    for name in (agencies or []):
        text = _clean(name).casefold()
        if not text:
            continue
        seen.add(DEFENSE if any(tok in text for tok in _DEFENSE_TOKENS)
                 else CIVILIAN)
    if not seen:
        return UNKNOWN_MISSION
    if seen == {DEFENSE}:
        return DEFENSE
    if seen == {CIVILIAN}:
        return CIVILIAN
    return MIXED


def route_role(spec: dict) -> str:
    """What a paper holder IS on the cited record.

    Read from the vehicle derivation the decision band already performs
    (TASK 1 shape: parent award id parsed onto the record). A holder sitting
    on a classified vehicle is the vehicle holder; a holder named as the
    recipient of the client-adjacent award is the prime; anything else the
    pack calls a reseller stays a reseller. Unknown is stated, never guessed.
    """
    evidence = (spec or {}).get("evidence") or {}
    stated = _clean(evidence.get("route_role")).casefold()
    if stated in (RESELLER, PRIME, VEHICLE_HOLDER):
        return stated
    vehicle = _clean(evidence.get("vehicle_class")).casefold()
    if vehicle and "unclassified" not in vehicle:
        return VEHICLE_HOLDER
    rows = evidence.get("rows") or spec.get("join_records") or []
    target = _clean((spec.get("target_org") or {}).get("name")).casefold()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if target and _clean(row.get("recipient")).casefold() == target:
            return PRIME
    return RESELLER if target else UNKNOWN_ROUTE


def batching_key(spec: dict) -> tuple:
    """The BATCH a spec belongs to. Coarse by design (reviewer R1)."""
    return (_clean(spec.get("row_class")),
            _clean((spec.get("target_org") or {}).get("kind")))


def strategy_key(spec: dict, *, agencies: Any = None) -> tuple:
    """The key a TITLE SET is reasoned for. Side-dependent (reviewer R1).

    This is deliberately NOT the batching key. Two specs can batch together
    and still need different titles, which is the whole finding: red_hat's
    one displacement batch spans both mission families, and one set covering
    both would have to be generic enough to name an Authorizing Official and
    an OCFO procurement lead at once.
    """
    row_class, kind = batching_key(spec)
    if kind == "paper_holder":
        holder = _clean((spec.get("target_org") or {}).get("name"))
        return (row_class, kind, _slug(holder), route_role(spec))
    names = list(agencies or [])
    if not names:
        names = [spec.get("buying_component"),
                 (spec.get("target_org") or {}).get("name")]
    return (row_class, kind, mission_applicability(names))


def _agencies_for(spec: dict) -> list:
    """Every agency name a spec's own evidence names."""
    out: list = []
    for value in (spec.get("buying_component"),
                  (spec.get("target_org") or {}).get("name")):
        if _clean(value):
            out.append(_clean(value))
    for row in (spec.get("join_records") or []):
        if isinstance(row, dict):
            for key in ("agency", "sub_agency"):
                if _clean(row.get(key)):
                    out.append(_clean(row[key]))
    seen, unique = set(), []
    for name in out:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            unique.append(name)
    return unique


def build_motions(specs: Any) -> list:
    """Group specs into motions, keyed per R1. Deterministic and ordered.

    Returns one motion per strategy key, each carrying the specs it batches,
    the organisations and agencies it touches, and the record ids its titles
    will have to hang off. The inference layer reads this and nothing else,
    so what the model sees is exactly what the fingerprint covers.
    """
    grouped: dict = {}
    for spec in (specs or []):
        if not isinstance(spec, dict):
            continue
        agencies = _agencies_for(spec)
        key = strategy_key(spec, agencies=agencies)
        motion = grouped.setdefault(key, {
            "strategy_key": list(key),
            "batching_key": list(batching_key(spec)),
            "row_class": key[0],
            "org_kind": key[1],
            "motion": MOTION_LABELS.get(
                (key[0], key[1]), "monitor pending evidence"),
            "specs": [], "spec_ids": [], "organisations": [],
            "agencies": [], "join_record_ids": [],
        })
        motion["specs"].append(spec)
        motion["spec_ids"].append(spec.get("spec_id"))
        org = _clean((spec.get("target_org") or {}).get("name"))
        if org and org not in motion["organisations"]:
            motion["organisations"].append(org)
        for name in agencies:
            if name not in motion["agencies"]:
                motion["agencies"].append(name)
        for rid in (spec.get("join_record_ids") or []):
            if rid not in motion["join_record_ids"]:
                motion["join_record_ids"].append(str(rid))
    out = []
    for key in sorted(grouped, key=lambda k: tuple(str(p) for p in k)):
        motion = grouped[key]
        motion["objective"] = MOTION_OBJECTIVES.get(motion["motion"], "")
        motion["spec_count"] = len(motion["specs"])
        if motion["org_kind"] == "paper_holder":
            motion["holder"] = motion["organisations"][0] \
                if motion["organisations"] else ""
            motion["route_role"] = key[3]
        else:
            motion["mission_applicability"] = key[2]
        motion["motion_id"] = "-".join(
            _slug(str(part)) for part in key if _clean(str(part)))
        out.append(motion)
    return out
