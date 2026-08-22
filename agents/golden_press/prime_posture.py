"""Can this client prime it, or do they have to sub it? Derived, never set.

THE OPERATOR RULING (2026-08-06): derive prime-versus-sub from award
history, as smart as the evidence allows, rather than making it a per-client
switch. A switch encodes an opinion once and then quietly ages; award
history is checkable and moves on its own.

WHAT THE EVIDENCE ACTUALLY SAYS. Three separate questions, and conflating
them is what makes a report tell a client to chase work they cannot hold:

  1 HAS THIS CLIENT EVER PRIMED?    is the client the RECIPIENT on a federal
                                    award. apexanalytix: zero, measured. Its
                                    products reach government only through
                                    Carahsoft and FCN, so every play it has
                                    is a channel play, and that is a finding
                                    about the company, not a gap in ours.
  2 WHERE HAS IT PRIMED?            an agency it has never sold to directly
                                    is a harder prime than one it has.
  3 HOW BIG A THING CAN IT HOLD?    the largest award it has actually held is
                                    the honest ceiling. A vendor whose
                                    biggest prime contract is $500K is not
                                    priming a $50M requirement, and saying so
                                    is more useful than flattering them.

THE CEILING RULE. A requirement materially larger than anything the client
has ever held prices as a SUB play even at an agency where they have primed.
"Materially" is a stated multiple (CEILING_MULTIPLE), not a vibe, and the
multiple rides the receipt so the operator can move it.

EVERY VERDICT CARRIES ITS EVIDENCE. A posture with no supporting record is
`unestablished`, which renders as a stated unknown. It is never quietly
defaulted to prime, because telling a client to prime something they cannot
hold wastes the one thing the report is for.
"""

from __future__ import annotations

from typing import Any

PRIME_POSTURE_VERSION = "prime_posture.v1.2026-08-06"

PRIME = "prime"
SUB = "sub"
EITHER = "either"
UNESTABLISHED = "unestablished"

# How much bigger than the client's demonstrated ceiling a requirement has to
# be before it prices as a sub play. Two is deliberately generous: a vendor
# can reasonably stretch to double what they have held, and beyond that the
# claim needs more than optimism.
CEILING_MULTIPLE = 2.0


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _dollars(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def derive_posture(pack: Any) -> dict:
    """What the award history says this client can hold, with its evidence.

    Pure over the pack. Uses ``rollups.resolve_relationship`` for recipient
    identity so a client alias fixed there is fixed here, exactly as the
    decision rules do.
    """
    from agents.golden_press.rollups import resolve_relationship

    records = list(getattr(pack, "records", []) or [])
    primed: list = []
    channel: list = []
    for record in records:
        recipient = _clean(getattr(record, "recipient", ""))
        if not recipient:
            continue
        relationship = resolve_relationship(recipient, pack)["relationship"]
        row = {
            "record_id": getattr(record, "record_id", None),
            "recipient": recipient,
            "agency": _clean(getattr(record, "agency", "")),
            "sub_agency": _clean(getattr(record, "sub_agency", "")),
            "obligated": _dollars(getattr(record, "obligated_dollars", 0)),
            "ceiling": _dollars(getattr(record, "ceiling_dollars", 0)),
            "vehicle": _clean(getattr(record, "vehicle", "")),
            "vehicle_class": _clean(getattr(record, "vehicle_class", "")),
        }
        if relationship == "client_entity":
            primed.append(row)
        elif relationship == "named_reseller":
            channel.append(row)

    # The demonstrated ceiling is the LARGEST single award actually held,
    # obligated or ceiling, whichever the record states higher. Summing
    # across awards would invent a capacity nobody demonstrated.
    demonstrated = max(
        [max(r["obligated"], r["ceiling"]) for r in primed] or [0.0])
    prime_agencies = sorted({r["agency"] for r in primed if r["agency"]})
    prime_subagencies = sorted({r["sub_agency"] for r in primed
                                if r["sub_agency"]})
    channel_partners = sorted({r["recipient"] for r in channel})

    if primed:
        posture = EITHER if channel else PRIME
    elif channel:
        posture = SUB
    else:
        posture = UNESTABLISHED

    return {
        "version": PRIME_POSTURE_VERSION,
        "posture": posture,
        "has_primed": bool(primed),
        "prime_award_count": len(primed),
        "prime_agencies": prime_agencies,
        "prime_subagencies": prime_subagencies,
        "demonstrated_ceiling": demonstrated,
        "ceiling_multiple": CEILING_MULTIPLE,
        "channel_partners": channel_partners,
        "channel_award_count": len(channel),
        "prime_evidence": primed[:12],
        "channel_evidence": channel[:12],
        "why": _posture_sentence(posture, primed, channel, demonstrated,
                                 channel_partners),
    }


def _posture_sentence(posture, primed, channel, ceiling, partners) -> str:
    """Deterministic. States what the records show, nothing more."""
    if posture == PRIME:
        return (f"The client is the named recipient on {len(primed)} cited "
                f"award(s); the largest single award held is "
                f"${ceiling:,.0f}.")
    if posture == EITHER:
        return (f"The client primes ({len(primed)} cited award(s), largest "
                f"${ceiling:,.0f}) and also reaches government through "
                f"{len(partners)} named channel partner(s).")
    if posture == SUB:
        return (f"No cited award names the client as recipient. Its route to "
                f"government on this evidence is through "
                f"{len(partners)} named channel partner(s): "
                f"{', '.join(partners[:4])}.")
    return ("No cited award names the client as a recipient and no channel "
            "partner is named, so the route to government is unestablished "
            "on this evidence.")


def route_for(opportunity: Any, posture: dict) -> dict:
    """prime | sub | either | unestablished for ONE opportunity, with why.

    Three tests, in order, each of which can only ever DOWNGRADE toward sub.
    Nothing here upgrades a client into priming something their history does
    not support.
    """
    value = max(_dollars(getattr(opportunity, "obligated_dollars", 0)
                         if not isinstance(opportunity, dict)
                         else opportunity.get("obligated_dollars")),
                _dollars(getattr(opportunity, "ceiling_dollars", 0)
                         if not isinstance(opportunity, dict)
                         else opportunity.get("ceiling_dollars")))
    agency = _clean(getattr(opportunity, "agency", None)
                    if not isinstance(opportunity, dict)
                    else opportunity.get("agency"))

    base = posture.get("posture", UNESTABLISHED)
    if base in (SUB, UNESTABLISHED):
        return {"route": base, "why": posture.get("why", ""),
                "test": "client posture"}

    reasons = []
    ceiling = _dollars(posture.get("demonstrated_ceiling"))
    multiple = float(posture.get("ceiling_multiple") or CEILING_MULTIPLE)
    if ceiling and value and value > ceiling * multiple:
        reasons.append(
            f"the requirement at ${value:,.0f} is more than {multiple:g}x the "
            f"largest award the client has held (${ceiling:,.0f})")
    known = set(posture.get("prime_agencies") or []) | set(
        posture.get("prime_subagencies") or [])
    if agency and known and not any(
            agency.casefold() in k.casefold() or k.casefold() in agency.casefold()
            for k in known):
        reasons.append(
            f"the client has no cited prime award at {agency}")

    if not reasons:
        return {"route": PRIME,
                "why": ("the client has primed at this scale and, where "
                        "stated, at this organisation"),
                "test": "ceiling and agency both cleared"}
    if len(reasons) >= 2:
        return {"route": SUB, "why": "; ".join(reasons),
                "test": "ceiling and agency both failed"}
    return {"route": EITHER, "why": "; ".join(reasons),
            "test": "one test failed; primeable with a partner or a stretch"}
