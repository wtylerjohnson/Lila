"""What kind of record is this identifier, and what may it prove?

THE GAP THIS CLOSES. A capability contract cites identifiers as evidence:
SP470125F0285, N0003026SSN7001, 24bd13ce699a4dc18df7d6f836efba4a. They look
alike and they are not alike. The first is a Defense Logistics Agency
contract award. The second is a Navy sources-sought NOTICE. The third is a
SAM notice id. Until something resolves an identifier to its KIND, nothing
downstream can honour the rule that an award proves spend and a notice proves
an opportunity, because nothing knows which it is holding.

Measured 2026-08-07 on the apexanalytix contract: of 17 cited identifiers,
15 are real awards totalling $85,549,824.90 and 2 are notices. An earlier
pass reported all 17 as "not found", because both API calls were malformed
and a 422 reads exactly like an empty result if nobody looks at the body.
"Not found" and "I asked wrongly" are different answers.

ORDER OF RESOLUTION IS FREE-FIRST. The local notice store answers in
microseconds at zero cost, so it is asked first; USAspending is asked only
for what the store does not hold. Neither is metered, but the ordering keeps
a bulk resolution off the network for records already held.

WHAT COMES BACK IS TYPED. Every resolution returns an `EvidenceReference`
whose `claim_roles` come from the resolved kind, never from the caller's
expectation. Ask for an opportunity and hand it an award, and the reference
raises rather than renders.
"""

from __future__ import annotations

import re
from typing import Any

from agents.golden_press.evidence_objects import (
    AWARD, CLAIM_CONTACT, CLAIM_OPPORTUNITY, CLAIM_PAPER_HOLDER,
    CLAIM_RECIPIENT, CLAIM_SPEND, NOTICE, EvidenceReference,
)

REFERENCE_RESOLVER_VERSION = "reference_resolver.v1.2026-08-07"

# A 32-character hex string is a SAM notice id and nothing else.
_SAM_NOTICE_ID = re.compile(r"^[0-9a-f]{32}$", re.I)

# Identifier shapes that are notice numbers rather than award PIIDs. These
# are worth knowing before the network call, not after it: SSN is a Navy
# sources-sought number, and asking an award API for one wastes a round trip
# to learn something the string already said.
_NOTICE_SHAPED = re.compile(r"(SSN\d|_FMF_|-RFI-|RFI$|SS$)", re.I)

UNRESOLVED = "unresolved"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _from_notice(row: Any) -> dict:
    """A store row becomes a typed opportunity reference."""
    notice_id = _clean(row["notice_id"])
    roles = [CLAIM_OPPORTUNITY]
    if _clean(row["poc_email"] if "poc_email" in row.keys() else ""):
        roles.append(CLAIM_CONTACT)
    return {
        "kind": NOTICE,
        "identifier": _clean(row["sol_number"]) or notice_id,
        "title": _clean(row["title"]),
        "org": _clean(row["subtier"] if "subtier" in row.keys() else "")
               or _clean(row["agency"] if "agency" in row.keys() else ""),
        "amount": None,
        "reference": EvidenceReference(
            source_id=_clean(row["sol_number"]) or notice_id,
            source_kind=NOTICE,
            source_url=(f"https://sam.gov/opp/{notice_id}/view"
                        if _SAM_NOTICE_ID.match(notice_id) else ""),
            claim_roles=tuple(roles),
            label=_clean(row["title"])),
    }


def _from_award(piid: str, rows: list) -> dict:
    """An award record becomes a typed spend reference.

    NOT an opportunity reference. This is the rule the whole module exists to
    keep mechanical: an award names who holds the paper and what was spent,
    and it says nothing about what is open.
    """
    top = rows[0]
    return {
        "kind": AWARD,
        "identifier": piid,
        "title": _clean(top.get("Description")) or piid,
        "org": (_clean(top.get("Awarding Sub Agency"))
                or _clean(top.get("Awarding Agency"))),
        "recipient": _clean(top.get("Recipient Name")),
        "amount": float(top.get("Award Amount") or 0.0),
        "award_group": _clean(top.get("_award_group")),
        "records": len(rows),
        "reference": EvidenceReference(
            source_id=piid, source_kind=AWARD,
            claim_roles=(CLAIM_SPEND, CLAIM_RECIPIENT, CLAIM_PAPER_HOLDER),
            label=_clean(top.get("Recipient Name"))),
    }


def resolve(identifier: str, *, conn: Any = None, poster: Any = None,
            allow_network: bool = True) -> dict:
    """Resolve one identifier to its kind, its record and its evidence.

    `conn` is an open notice-store connection; `poster` is injectable so a
    test never touches the network. An identifier that resolves nowhere
    returns kind `unresolved` with the reason, which is a finding rather
    than an exception.
    """
    ident = _clean(identifier)
    if not ident:
        return {"kind": UNRESOLVED, "identifier": "", "why": "empty"}

    if conn is not None:
        try:
            row = conn.execute(
                "SELECT notice_id, title, notice_type, sol_number, poc_email, "
                "agency, subtier FROM notices "
                "WHERE notice_id = ? OR sol_number = ? LIMIT 1",
                (ident, ident)).fetchone()
        except Exception:                                     # noqa: BLE001
            row = None
        if row is not None:
            return _from_notice(row)

    # A notice-shaped identifier the store does not hold is a notice we have
    # not ingested, not an award. Saying so beats a pointless award lookup.
    if _SAM_NOTICE_ID.match(ident) or _NOTICE_SHAPED.search(ident):
        return {"kind": UNRESOLVED, "identifier": ident,
                "why": "notice identifier not present in the local store",
                "next_action": "Ingest the notice, then resolve again."}

    if not allow_network:
        return {"kind": UNRESOLVED, "identifier": ident,
                "why": "not in the local store and network lookup was off"}

    from tools.api.usaspending import resolve_award
    rows = resolve_award(ident, poster=poster)
    if rows:
        return _from_award(ident, rows)
    return {"kind": UNRESOLVED, "identifier": ident,
            "why": "no award or notice carries this identifier",
            "next_action": "Confirm the identifier with its source."}


def resolve_all(identifiers: Any, *, conn: Any = None, poster: Any = None,
                allow_network: bool = True) -> dict:
    """Bulk resolution with a receipt of what each identifier turned out to be."""
    resolved: dict = {}
    for ident in dict.fromkeys(_clean(i) for i in (identifiers or [])):
        if ident:
            resolved[ident] = resolve(ident, conn=conn, poster=poster,
                                      allow_network=allow_network)
    kinds: dict = {}
    for entry in resolved.values():
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    spend = sum(float(e.get("amount") or 0.0)
                for e in resolved.values() if e["kind"] == AWARD)
    return {"resolved": resolved,
            "receipt": {"identifiers": len(resolved), "by_kind": kinds,
                        "award_dollars": spend}}
