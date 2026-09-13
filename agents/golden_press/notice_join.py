"""Join pack notice records against the durable store. No fetch, no network.

WHY A JOIN AND NOT A FETCH. GoldenRecord.record_id for an L1 notice IS the
sam.gov notice id (run_l1_from_sweep sets record_id=row["source_id"]), and the
store is keyed by that same id. So this is a primary-key lookup against a
local SQLite file: no metered call, no rate limit, nothing that can fail.

WHAT IT ADDS. notice_type is the field the pack has never carried, so a
report could not tell a Sources Sought from an Award Notice. That distinction
is the whole difference between "you can still shape this" and "this is
over": leverage_rank encodes it, and rank 4 (Award Notice) is NEVER eligible
for a solicitation band because the work is already placed.

DEDUPE. The extract carries one row per notice per amendment lineage, so a
single BAA reaches a pack several times under different ids with the same
title and agency. An earlier measurement collapsed 12 rows to 6 distinct
notices. Undeduped, a report shows one BAA six times and reads as six
opportunities.

MIGRATION IS A NO-OP. A record the store has never seen keeps every field it
already had and stays unranked. A missing or unreadable store leaves every
record untouched. Nothing here can fail a press.
"""

from __future__ import annotations

import re

import sqlite3
from typing import Any, Iterable, Optional

# Operator-set 2026-07-28. The ladder is about how much room is left to
# influence the requirement, not about dollar size.
LEVERAGE_RANK: dict[str, int] = {
    # 1 · the requirement is still being written; this is where influence lives
    "Sources Sought": 1,
    "Request for Information (RFI)": 1,
    "RFI": 1,
    # 2 · the shape is set, the door is open
    "Special Notice": 2,
    "Presolicitation": 2,
    # 3 · priced and posted, bid it or do not
    "Combined Synopsis/Solicitation": 3,
    "Solicitation": 3,
    # 4 · already placed. NEVER eligible for a solicitation band.
    "Award Notice": 4,
    "Justification": 4,
    "Justification and Approval (J&A)": 4,
}
UNRANKED = None
SOLICITABLE_MAX_RANK = 3      # rank 4 is history, not an opportunity


def leverage_rank(notice_type: Optional[str]) -> Optional[int]:
    """Deterministic rank from the notice type, or None when unknown.

    Unknown stays None rather than defaulting to a number: an invented rank
    would sort an unrecognised notice type into a band on no evidence.
    """
    if not notice_type:
        return UNRANKED
    return LEVERAGE_RANK.get(" ".join(str(notice_type).split()), UNRANKED)


def is_solicitable(record: Any) -> bool:
    """True when the notice still represents work that can be won."""
    rank = getattr(record, "notice_leverage_rank", None)
    return rank is not None and rank <= SOLICITABLE_MAX_RANK


def dedupe_by_title_agency(records: list) -> tuple[list, list]:
    """Compatibility name; structured identity and source chronology now own dedupe."""
    from tools.relevance.notice_family import resolve
    notices = [r for r in records if getattr(r, "lane", None) == "L1_notice"]
    dictionaries = [r.model_dump(mode="json") for r in notices]
    selected = set()
    for family in resolve(dictionaries):
        index = family["representative_ordinal"]
        selected.add(id(notices[index]))
        notices[index].source_fields = dict(notices[index].source_fields,
            notice_family_v1={k: v for k, v in family.items() if k != "representative"})
    kept = [r for r in records if getattr(r, "lane", None) != "L1_notice" or id(r) in selected]
    dropped = [r for r in notices if id(r) not in selected]
    return kept, dropped


def enrich_from_store(records: Iterable, *,
                      conn: Optional[sqlite3.Connection] = None) -> dict:
    """Fill notice fields on L1 records from the store. Mutates in place.

    Returns a receipt naming every miss and its reason, because "0 records
    gained a notice_type" has to be distinguishable from "the store was not
    there".
    """
    records = list(records)
    l1 = [r for r in records if getattr(r, "lane", None) == "L1_notice"]
    receipt = {"l1_records": len(l1), "matched": 0, "unmatched": 0,
               "misses": [], "store_rows": None, "collisions": 0,
               "error": None}
    if not l1:
        receipt["error"] = "no L1 notice records in this pack"
        return receipt

    owned = conn is None
    try:
        if conn is None:
            from tools.notice_store import connect
            conn = connect()
        receipt["store_rows"] = conn.execute(
            "SELECT COUNT(*) c FROM notices").fetchone()["c"]
    except Exception as exc:  # noqa: BLE001 - the join never fails a press
        receipt["error"] = f"store unreadable: {type(exc).__name__}"
        return receipt

    try:
        for record in l1:
            rid = str(getattr(record, "record_id", "") or "")
            row = conn.execute(
                "SELECT * FROM notices WHERE notice_id = ?", (rid,)).fetchone()
            if row is None:
                receipt["unmatched"] += 1
                receipt["misses"].append({
                    "record_id": rid,
                    "reason": ("empty record_id" if not rid else
                               "notice id absent from the store: it closed "
                               "before the store's first ingest, or the sweep "
                               "predates the store")})
                continue
            receipt["matched"] += 1
            record.notice_type = row["notice_type"] or None
            record.notice_leverage_rank = leverage_rank(row["notice_type"])
            # Only fill what the pack is missing. The pack's own value came
            # from the sweep and is not overwritten by a later snapshot.
            if not getattr(record, "response_deadline", None):
                record.response_deadline = row["deadline"] or None
            if not getattr(record, "set_aside", None):
                record.set_aside = row["set_aside"] or None
            if not getattr(record, "office", None):
                record.office = row["office"] or None
            if not getattr(record, "url", None):
                from agents.reports.links import build_sam_notice_link
                try:
                    record.url = build_sam_notice_link(rid.lower()).url
                except ValueError:
                    record.url = row["url"] or None
    finally:
        if owned:
            conn.close()
    return receipt


# --------------------------------------------------------------------------- #
# contacts
# --------------------------------------------------------------------------- #
# A contracting officer's address resolves against the NAME the notice already
# carries. Guessing from the address alone under-counts real people: an
# earlier sample scored "Dittmer@wapa.gov" as a shared mailbox purely because
# it is not first.last, while the notice named "Jonathan Dittmer" right beside
# it. The name field settles it, so it is used.
_ROUTING_TOKENS = frozenset({
    "civ", "mil", "ctr", "usa", "usaf", "usn", "usmc", "jr", "sr", "ii", "iii",
})
_SPLIT = re.compile(r"[^a-z]+")


def contact_quality(name: str | None, email: str | None) -> str:
    """named_individual | surname_only | shared_mailbox. Deterministic.

    named_individual  two or more parts of the address match the person named
                      on the notice (jessica.b.rooks.civ@army.mil / Jessica B.
                      Rooks)
    surname_only      exactly one part matches (Dittmer@wapa.gov / Jonathan
                      Dittmer). A REAL PERSON, addressed by surname. Never
                      scored as shared.
    shared_mailbox    nothing in the address matches the named person, so the
                      mail lands in a role or team box regardless of who is
                      listed
    """
    address = str(email or "").strip().lower()
    if not address or "@" not in address:
        return "shared_mailbox"
    local = address.split("@", 1)[0]
    parts = {p for p in _SPLIT.split(local) if len(p) >= 2} - _ROUTING_TOKENS
    named = {p for p in _SPLIT.split(str(name or "").lower()) if len(p) >= 2}
    if not named:
        return "shared_mailbox"
    matched = parts & named
    if len(matched) >= 2:
        return "named_individual"
    if len(matched) == 1:
        return "surname_only"
    return "shared_mailbox"


# Once a solicitation is open, vendor communication belongs in the official
# Q&A process. The contact is still carried, because a client may need to know
# WHO owns the buy, but handing them a CO to cold-call mid-procurement is
# advice that damages the client.
CONTACT_DIRECT_MAX_RANK = 2


def contact_use(rank: int | None) -> str:
    return ("direct" if rank is not None and rank <= CONTACT_DIRECT_MAX_RANK
            else "formal_channel_only")
