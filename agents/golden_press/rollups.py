"""Derived rollups over an evidence pack: primes and rivals.

OFFLINE BY CONSTRUCTION. Everything here is computed from a pack already on
disk. No sweep, no network, no model call. Re-running this on the same pack
produces the same numbers.

THE COVERAGE PROBLEM, STATED UP FRONT. A pack does not carry the market. It
carries the records the selection rule chose to display, and the Riverbed
pack says so in its own words:

    screened_relevant   L2_core 425 · L2_competitor 283 · L4_forecast 67
    packed              L2_core  26 · L2_competitor  10 · L4_forecast 10

So a sum over pack.records is a sum over 46 of 775 categorized records, out
of 1,256 that passed the relevance screen. It is a TRUE statement about the
cited evidence and a FALSE statement about the market. Every rollup here
therefore carries a `coverage` block naming exactly what it summed, and
`is_market_total` is False on all of them. Nothing in this module will ever
print a capped sum next to the word "total" without that qualifier attached.

Making these market totals is not a rendering change. It needs the retrieval
stage to persist the full screened set rather than only the packed slice,
which means a sweep, which is out of scope for an offline rollup.

WHERE category_spend GETS ITS NUMBERS (2026-07-28). Not from pack.records.
A category total is the one figure a reader treats as market fact no matter
how it is captioned, and computing it over 46 of 775 records would put a 6%
sample under the word "category". It reads ONLY pack.pre_cap_aggregates,
which retrieval now computes over each full categorized segment before the
display caps cut it. A pack without that block returns available=False and
says what is missing, rather than a number that reads correct and is not.

prime_rollup and competitor_landscape stay per-entity views of CITED
EVIDENCE, which is what they honestly are.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from agents.golden_press.validate import _record_affinity

# Legal-form noise that must not defeat an entity match. "DLT SOLUTIONS, LLC"
# and "DLT Solutions" are the same company, and the whole point of the
# relationship field is catching that a prime IS the client.
_LEGAL_SUFFIX = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|company|co|plc|"
    r"holdings|group|enterprises?|services|solutions|technologies|"
    r"technology|systems|federal|public sector|government|gov)\b", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalize_entity(name: Any) -> str:
    """Casefold and strip legal form, so company names compare as companies."""
    text = _PUNCT.sub(" ", str(name or "").casefold())
    text = _LEGAL_SUFFIX.sub(" ", text)
    return " ".join(text.split())


def default_client_aliases(pack: Any) -> list[str]:
    """Seed for client_entity_aliases: the client's own name.

    Deliberately thin. The list is meant to be EDITED by an operator who
    knows the corporate tree, because the case that matters cannot be
    derived from the pack: on the Red Hat pack, DLT Solutions is TD SYNNEX
    Public Sector and carries 44% of the obligated dollars. Nothing in the
    award data says so. A human has to say so once, here.
    """
    return [str(getattr(pack, "client_name", "") or "").strip()]


def resolve_relationship(prime: str, pack: Any,
                         aliases: Optional[Iterable[str]] = None) -> dict:
    """How this awardee relates to the client.

    client_entity  the prime IS the client, under another name. This is the
                   case that turns an apparent third-party win into the
                   client's own revenue, and it is why the alias list exists.
    named_reseller a channel partner the research stage named.
    competitor     a rival that also wins prime awards. Dual role: the row
                   stays here in prime_rollup and is kept OUT of
                   competitor_landscape, so the two never double-count.
    independent    none of the above.
    """
    entities = (getattr(pack, "research", None) or {}).get("entities", {}) or {}
    key = normalize_entity(prime)
    alias_keys = {normalize_entity(a) for a in
                  (aliases if aliases is not None
                   else getattr(pack, "client_entity_aliases", None)
                   or default_client_aliases(pack)) if a}
    reseller_keys = {normalize_entity(n) for n in entities.get("reseller", [])}
    rival_keys = {normalize_entity(n) for n in entities.get("competitor", [])}

    def hit(pool: set) -> bool:
        # Exact after normalization, or one contained in the other as whole
        # words, so "TD SYNNEX Public Sector" matches "TD SYNNEX".
        if key in pool:
            return True
        return any(k and (f" {key} ".find(f" {k} ") >= 0
                          or f" {k} ".find(f" {key} ") >= 0) for k in pool)

    if hit(alias_keys):
        return {"relationship": "client_entity", "dual_role": None}
    if hit(reseller_keys):
        return {"relationship": "named_reseller", "dual_role": None}
    if hit(rival_keys):
        return {"relationship": "competitor",
                "dual_role": "wins prime awards and is a named rival; counted "
                             "here, excluded from competitor_landscape"}
    return {"relationship": "independent", "dual_role": None}


# --------------------------------------------------------------------------- #
# coverage: the qualifier that rides every number
# --------------------------------------------------------------------------- #
def coverage(pack: Any) -> dict:
    """What these sums actually cover. Attached to all three rollups."""
    selection = getattr(pack, "selection", None) or {}
    screened = selection.get("screened_relevant") or {}
    packed = selection.get("packed") or {}
    screened_total = sum(v for v in screened.values() if isinstance(v, int))
    packed_total = sum(v for v in packed.values() if isinstance(v, int))
    capped = bool(screened_total and packed_total < screened_total)
    return {
        "basis": ("capped display set" if capped else "full qualifying set"),
        "is_market_total": not capped,
        "records_summed": len(list(getattr(pack, "records", []) or [])),
        "screened_relevant": dict(screened),
        "packed": dict(packed),
        "screened_relevant_total": screened_total,
        "packed_total": packed_total,
        "selection_rule": selection.get("rule"),
        "qualifier": (
            f"Sums cover {packed_total} of {screened_total} relevance-screened "
            f"records that the selection rule categorized. These are totals "
            f"over the CITED EVIDENCE, not over the market."
            if capped else
            "Sums cover the full qualifying set."),
        "to_make_it_a_market_total": (
            "The pack persists only the packed slice, so the remaining "
            "records are not on disk and no offline computation can recover "
            "them. Retrieval would have to persist the full screened set "
            "(or re-run with the caps lifted), which requires a sweep."
            if capped else None),
    }


# --------------------------------------------------------------------------- #
# the three rollups
# --------------------------------------------------------------------------- #
def _agencies(records: Iterable[Any]) -> list[str]:
    return sorted({str(r.sub_agency or r.agency or "").strip()
                   for r in records if (r.sub_agency or r.agency)})


def category_spend(pack: Any) -> dict:
    """Category figures, read from the PRE-CAP aggregate block only.

    This function deliberately cannot compute from pack.records. Summing the
    packed slice and calling it a category total is the mislabel this build
    exists to correct: on the Riverbed pack that would be 46 of 775 records
    under the word "category". A pack with no pre_cap_aggregates therefore
    returns available=False and says what is missing, rather than returning a
    number that reads correct and is not.
    """
    block = getattr(pack, "pre_cap_aggregates", None)
    if not block:
        return {
            "available": False,
            "rows": [],
            "reason": (
                "This pack carries no pre_cap_aggregates block, so no honest "
                "category figure exists in it. The qualifying set is not on "
                "disk: the pack holds only the display slice the selection "
                "rule kept. It cannot be recovered offline."),
            "how_to_get_it": (
                "Press this client again. Retrieval now aggregates each lane "
                "segment before its cap, and the next pack will carry the "
                "figures. Re-sweeping purely to recover a historical total is "
                "not worth a metered sweep."),
            "coverage": coverage(pack),
        }
    rows = []
    for segment in block.get("segments", []):
        if not segment.get("count"):
            continue
        rows.append({
            "segment": segment["segment"],
            "records": segment["count"],
            "dollars": segment["dollars"],
            "money_basis": segment["money_basis"],
            "money_label": segment["money_label"],
            "agencies": len(segment.get("by_agency") or {}),
            "primes": len(segment.get("by_prime") or {}),
            "fiscal_years": sorted((segment.get("by_fiscal_year") or {}).keys()),
            "records_without_dollars": segment.get("records_without_dollars", 0),
            "filter_statement": segment["filter_statement"],
        })
    return {
        "available": True,
        "basis": block.get("basis"),
        "categorized_total": block.get("categorized_total"),
        "rows": rows,
        # Award dollars and forecast floors are different kinds of number and
        # are never added together. A combined figure would be part reported
        # obligation and part published lower bound.
        "obligated_across_awards": sum(
            r["dollars"] for r in rows if r["money_basis"] == "obligated"),
        "forecast_floor_across_forecasts": sum(
            r["dollars"] for r in rows if r["money_basis"] == "floor"),
        "coverage": coverage(pack),
    }


def prime_rollup(pack: Any, aliases: Optional[Iterable[str]] = None) -> dict:
    """The companies actually winning the awards, and who they are to us."""
    records = list(getattr(pack, "records", []) or [])
    alias_list = list(aliases) if aliases is not None else (
        list(getattr(pack, "client_entity_aliases", None) or
             default_client_aliases(pack)))
    buckets: dict[str, list] = {}
    for record in records:
        name = str(record.recipient or "").strip()
        if not name:
            continue
        buckets.setdefault(name, []).append(record)
    total = sum(r.obligated_dollars or 0 for r in records if r.recipient)
    rows = []
    for name, group in buckets.items():
        obligated = sum(r.obligated_dollars or 0 for r in group)
        resolved = resolve_relationship(name, pack, alias_list)
        rows.append({
            "prime": name,
            "records": len(group),
            "obligated": obligated,
            "share_of_awarded": (obligated / total) if total else 0.0,
            "agencies": _agencies(group),
            "vehicles": sorted({str(r.vehicle) for r in group if r.vehicle}),
            **resolved,
        })
    rows.sort(key=lambda r: -r["obligated"])
    by_relationship: dict[str, dict] = {}
    for row in rows:
        slot = by_relationship.setdefault(
            row["relationship"], {"primes": 0, "records": 0, "obligated": 0.0})
        slot["primes"] += 1
        slot["records"] += row["records"]
        slot["obligated"] += row["obligated"]
    return {
        "rows": rows,
        "obligated_across_primes": total,
        "client_entity_aliases": alias_list,
        "client_entity_aliases_note": (
            "EDITABLE. Seeded from the client name only. A prime that is the "
            "client under another corporate name cannot be derived from award "
            "data; add it here once and every rollup follows."),
        "by_relationship": by_relationship,
        "coverage": coverage(pack),
    }


def competitor_landscape(pack: Any,
                         aliases: Optional[Iterable[str]] = None) -> dict:
    """Named rivals, where they are winning, and where they meet the client.

    overlap_agencies is the analysis. A rival with big numbers in agencies
    the client never touches is market colour. A rival inside the client's
    own corridors is a displacement fight, and that is the row a seller acts
    on.
    """
    records = list(getattr(pack, "records", []) or [])
    entities = (getattr(pack, "research", None) or {}).get("entities", {}) or {}
    rivals = [str(n) for n in entities.get("competitor", []) if str(n).strip()]

    # Primes are a different key space, but an entity can appear in both. Any
    # rival that also wins prime awards belongs to prime_rollup, so it is
    # dropped here and the two tables share zero rows by construction.
    prime_names = {normalize_entity(r.recipient) for r in records if r.recipient}
    client_agencies = set(_agencies(
        [r for r in records if _record_affinity(r, pack) == "core"]))

    rows = []
    excluded = []
    for rival in rivals:
        key = normalize_entity(rival)
        if key in prime_names:
            excluded.append(rival)
            continue
        group = [r for r in records
                 if key in {normalize_entity(h) for h in (r.entity_hits or [])}]
        rival_agencies = _agencies(group)
        overlap = sorted(set(rival_agencies) & client_agencies)
        rows.append({
            "competitor": rival,
            "records": len(group),
            "obligated": sum(r.obligated_dollars or 0 for r in group),
            "agencies": rival_agencies,
            "overlap_agencies": overlap,
            "overlap_count": len(overlap),
            "contested": bool(overlap),
        })
    rows.sort(key=lambda r: (-r["overlap_count"], -r["obligated"]))
    return {
        "rows": rows,
        "client_agencies": sorted(client_agencies),
        "excluded_as_primes": excluded,
        "excluded_note": (
            "These named rivals also win prime awards, so they are reported "
            "in prime_rollup with a dual_role note. The two tables share zero "
            "rows."),
        "coverage": coverage(pack),
    }


def build_rollups(pack: Any, aliases: Optional[Iterable[str]] = None) -> dict:
    """All three, plus the shared coverage statement."""
    return {
        "category_spend": category_spend(pack),
        "prime_rollup": prime_rollup(pack, aliases),
        "competitor_landscape": competitor_landscape(pack, aliases),
        "coverage": coverage(pack),
    }


# --------------------------------------------------------------------------- #
# readable tables
# --------------------------------------------------------------------------- #
def _money(value: Optional[float]) -> str:
    return f"${value:,.0f}" if value else "-"


def _rule(width: int = 96) -> str:
    return "-" * width


def format_tables(rollups: dict, client: str = "") -> str:
    out: list[str] = []
    cov = rollups["coverage"]
    out.append(_rule())
    out.append(f"PACK ROLLUPS{('  ·  ' + client) if client else ''}")
    out.append(_rule())
    out.append(f"BASIS: {cov['basis'].upper()}   market total: "
               f"{cov['is_market_total']}")
    out.append(f"  {cov['qualifier']}")
    if cov.get("to_make_it_a_market_total"):
        out.append(f"  TO FIX: {cov['to_make_it_a_market_total']}")
    out.append(f"  screened {cov['screened_relevant']}")
    out.append(f"  packed   {cov['packed']}")
    out.append("")

    cat = rollups["category_spend"]
    out.append("CATEGORY SPEND  (pre-cap aggregate block; the only honest "
               "category figures)")
    if not cat["available"]:
        out.append(f"  UNAVAILABLE: {cat['reason']}")
        out.append(f"  HOW TO GET IT: {cat['how_to_get_it']}")
    else:
        out.append(f"  basis: {cat['basis']}   categorized total: "
                   f"{cat['categorized_total']:,}")
        out.append(f"  {'SEGMENT':16s} {'RECS':>6s} {'DOLLARS':>16s} "
                   f"{'AGENCIES':>8s} {'PRIMES':>7s}  MONEY BASIS")
        out.append(f"  {_rule(88)}")
        for row in cat["rows"]:
            out.append(f"  {row['segment']:16s} {row['records']:6,d} "
                       f"{_money(row['dollars']):>16s} {row['agencies']:8d} "
                       f"{row['primes']:7d}  {row['money_label']}")
        out.append(f"  awards obligated {_money(cat['obligated_across_awards'])}"
                   f" · forecast FLOOR "
                   f"{_money(cat['forecast_floor_across_forecasts'])}"
                   f"  (never added together)")
    out.append("")

    pri = rollups["prime_rollup"]
    out.append("PRIME ROLLUP  (who is actually winning the awards)")
    out.append(f"  {'PRIME':34s} {'RECS':>4s} {'OBLIGATED':>15s} {'SHARE':>7s}  "
               f"{'RELATIONSHIP':14s} AGENCIES")
    out.append(f"  {_rule(88)}")
    for row in pri["rows"]:
        out.append(f"  {row['prime'][:34]:34s} {row['records']:4d} "
                   f"{_money(row['obligated']):>15s} "
                   f"{row['share_of_awarded']*100:6.1f}%  "
                   f"{row['relationship']:14s} {len(row['agencies'])}")
        if row.get("dual_role"):
            out.append(f"      DUAL ROLE: {row['dual_role']}")
    out.append(f"  aliases in use: {pri['client_entity_aliases']}")
    out.append(f"  {pri['client_entity_aliases_note']}")
    out.append("")

    comp = rollups["competitor_landscape"]
    out.append("COMPETITOR LANDSCAPE  (named rivals; overlap is the analysis)")
    out.append(f"  {'COMPETITOR':24s} {'RECS':>4s} {'OBLIGATED':>15s} "
               f"{'OVERLAP':>7s}  OVERLAP AGENCIES")
    out.append(f"  {_rule(88)}")
    for row in comp["rows"]:
        agencies = ", ".join(row["overlap_agencies"])[:44] or "(none)"
        out.append(f"  {row['competitor'][:24]:24s} {row['records']:4d} "
                   f"{_money(row['obligated']):>15s} "
                   f"{row['overlap_count']:7d}  {agencies}")
    out.append(f"  client corridors: {len(comp['client_agencies'])} agencies")
    if comp["excluded_as_primes"]:
        out.append(f"  excluded (also primes): {comp['excluded_as_primes']}")
        out.append(f"  {comp['excluded_note']}")
    out.append(_rule())
    return "\n".join(out)
