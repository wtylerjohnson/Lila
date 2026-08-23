"""L1 notice lane, read from the durable store instead of a cached sweep.

WHY THIS REPLACES THE SWEEP PATH. run_l1_from_sweep reads
data/cleaned/searches_<slug>.json, a cached artifact from a metered run that
may be weeks old and may not exist at all. That is why the lane reported
DEGRADED on both real packs and why both carry zero L1 records. The store
holds the full active universe, indexed, answering in ~13ms at zero quota,
and it keeps notices sam.gov has already dropped.

WHAT THE STORE ALREADY KNOWS. notice_type, response_deadline, set_aside and
office come out of the store because the store is their writer. There is no
second lookup and no enrichment fetch: the SELECT that finds the notice is
the SELECT that fills the record.

RANK 4 NEVER ENTERS THE SOLICITATION LANE. An Award Notice is a placed
contract. It can still be evidence of who holds what, which is a different
question, so it is returned separately rather than discarded.

THE SCREEN IS UNCHANGED. screen_relevance runs exactly as it does for every
other lane. This module chooses WHICH notices to offer it, never how it
judges them.

NOTHING HERE BLOCKS. An unreadable store returns empty with a stated reason
and the caller falls back to the sweep path.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Optional

from agents.golden_press.notice_join import (
    SOLICITABLE_MAX_RANK, contact_quality, contact_use,
    dedupe_by_title_agency, leverage_rank,
)
from agents.golden_press.sam_lanes import (
    _corpus_negatives, _pattern, aliases, build_matchers, reject_entity_hit,
)
from agents.golden_press.records import GoldenRecord, LaneQuery
from agents.golden_press.retrieval import (
    L1_PACK_CAP, TECH_NAICS_PREFIXES, TECH_PSC_PREFIXES, _log, _notice_url,
    _now_iso, _word_hits, screen_relevance, strip_entity_names,
)
from agents.golden_press.term_tiers import evaluate_term, load_guards

# Description text stored per notice. The screen sees the prefix, which is
# what the store kept, and the record carries it forward unchanged.
_SELECT = """
SELECT notice_id, title, notice_type, agency, subtier, office, posted,
       deadline, naics, psc, set_aside, url, description_prefix, last_seen,
       poc_name, poc_email, poc_phone, poc_secondary_email
  FROM notices
"""


def _boundary_clause(naics_prefixes=None, psc_prefixes=None) -> tuple[str, list]:
    """SQL for the corrected NAICS/PSC boundary. Prefix matching in SQL so the
    78K-row scan never reaches Python."""
    parts, params = [], []
    for prefix in (naics_prefixes or TECH_NAICS_PREFIXES):
        parts.append("naics LIKE ?")
        params.append(f"{prefix}%")
    for prefix in (psc_prefixes or TECH_PSC_PREFIXES):
        parts.append("psc LIKE ?")
        params.append(f"{prefix}%")
    return "(" + " OR ".join(parts) + ")", params


def _has_qualifying_code(row, *, naics_prefixes=None,
                         psc_prefixes=None) -> bool:
    """A record code inside the boundary that admitted the record.

    The old implementation queried with the client's boundary and then
    re-qualified every term against the global technology prefixes. That
    made legitimate non-IT markets impossible: a language-services record
    in 541930 could pass JTG's boundary and still be discarded by the term
    tier. One boundary now governs both the SQL screen and term eligibility.
    """
    naics = str(row["naics"] or "")
    psc = str(row["psc"] or "")
    naics_basis = tuple(str(p) for p in
                        (naics_prefixes or TECH_NAICS_PREFIXES))
    psc_basis = tuple(str(p) for p in
                      (psc_prefixes or TECH_PSC_PREFIXES))
    return (any(naics.startswith(p) for p in naics_basis)
            or any(psc.startswith(p) for p in psc_basis))


def _tiered_capability_hits(text, terms, *, has_tech_code, distinctive,
                            guards, receipt) -> list[str]:
    """Capability hits that survive the tier ladder (term_tiers).

    Tier 1 distinctive names match alone. Tier 2 phrases and tier 3 single
    common words require a tech code on the record, and tier 3 additionally
    requires a domain anchor nearby. Every rejection is counted in the receipt
    so a killed match is explainable rather than silently absent.
    """
    hits: list[str] = []
    for term in terms:
        verdict = evaluate_term(term, text, has_tech_code=has_tech_code,
                                distinctive_names=distinctive, guards=guards)
        if verdict is None:
            continue
        if verdict.get("rejected"):
            why = str(verdict.get("why", "guard"))
            receipt["tier_rejections"][why] = (
                receipt["tier_rejections"].get(why, 0) + 1)
            continue
        hits.append(term)
        key = f"tier{verdict['tier']}"
        receipt["tier_hits"][key] = receipt["tier_hits"].get(key, 0) + 1
    return hits


def _guarded_entity_hits(text, matchers, vendor_pattern, receipt, row):
    """Entity names that survive the SHARED guard in sam_lanes."""
    if not matchers:
        return [], {}
    vendor_present = bool(vendor_pattern.search(text)) if vendor_pattern else False
    product_hits = {n for n, m in matchers.items()
                    if m["kind"] in ("product", "competitor")
                    and m["pattern"].search(text)}
    kept = []
    for name, matcher in matchers.items():
        if not matcher["pattern"].search(text):
            continue
        why = reject_entity_hit(matcher, vendor_present=vendor_present,
                                product_hits=product_hits, text=text,
                                negative_phrases=_corpus_negatives())
        if why:
            receipt["guard_rejections"][why] = (
                receipt["guard_rejections"].get(why, 0) + 1)
            receipt["guard_killed_titles"].setdefault(why, [])
            if len(receipt["guard_killed_titles"][why]) < 40:
                receipt["guard_killed_titles"][why].append(
                    f"{name} :: {str(row['title'] or '')[:70]}")
            continue
        kept.append(name)
    return kept, {}


def run_l1_from_store(
    capability_terms: list[str],
    *,
    entity_terms: Optional[list[str]] = None,
    entities: Optional[dict] = None,
    vendor: Optional[str] = None,
    naics_boundary: Optional[list] = None,
    psc_boundary: Optional[list] = None,
    conn: Optional[sqlite3.Connection] = None,
    cap: int = L1_PACK_CAP,
    include_awards: bool = False,
    excluded_terms: Optional[list[str]] = None,
    as_of: str = "",
) -> tuple[list[GoldenRecord], list[LaneQuery], dict]:
    """(records, queries, receipt) for the L1 lane, from the store.

    include_awards=False keeps rank 4 out, which is the solicitation lane.
    Pass True to ask the evidence question instead.
    """
    entity_terms = list(entity_terms or [])
    receipt: dict[str, Any] = {
        "source": "notice_store", "metered_quota_spent": 0,
        "store_rows": None, "in_corridor": 0, "screened": 0,
        "term_hits": 0, "kept": 0, "deduped_away": 0,
        "rank_4_excluded": 0, "by_rank": {}, "error": None,
        "store_last_ingest": None,
        "guards_applied": bool(entities),
        "guard_rejections": {}, "guard_killed_titles": {},
        "tier_hits": {}, "tier_rejections": {},
        "qualifying_code_basis": "client" if (naics_boundary or psc_boundary)
        else "default_technology",
        "client_exclusions": 0, "closed_historic": 0,
    }
    guards = load_guards()
    # Guarded path when the caller supplies entities BY KIND plus the vendor
    # name. Without them there is nothing to anchor an ambiguous name to and
    # no way to know which term is a reseller, so the guards cannot run and
    # the receipt says so rather than implying protection that is not there.
    matchers = build_matchers(entities, vendor) if entities else {}
    vendor_pattern = _pattern(aliases(vendor)) if vendor else None
    distinctive = [n for n, m in matchers.items() if m.get("distinctive")] \
        or [t for t in entity_terms if len(str(t).split()) > 1]
    # A brand name sitting in the CAPABILITY list bypasses the entity guard,
    # because capability terms are exempt by design. Intake really does emit
    # them: Riverbed's frame carried "SteelHead" and "Aternity" as capability
    # terms, and "SteelHead" then matched an aquaculture sole-source. Strip
    # them here, with the same shared helper every other screening lane uses.
    if entities:
        all_names = [n for v in entities.values() for n in v]
        capability_terms = strip_entity_names(capability_terms, all_names)
        receipt["capability_terms_stripped"] = sorted(
            set(all_names) & set(capability_terms or []) )
    owned = conn is None
    try:
        if conn is None:
            from tools.notice_store import connect
            conn = connect()
        from tools.notice_store import last_ingest
        receipt["store_rows"] = conn.execute(
            "SELECT COUNT(*) c FROM notices").fetchone()["c"]
        li = last_ingest(conn)
        receipt["store_last_ingest"] = li["ingest_date"] if li else None
    except Exception as exc:  # noqa: BLE001 - never blocks; caller falls back
        receipt["error"] = f"store unreadable: {type(exc).__name__}: {exc}"
        return [], [], receipt

    try:
        clause, params = _boundary_clause(naics_boundary, psc_boundary)
        rows = conn.execute(f"{_SELECT} WHERE {clause}", params).fetchall()
        receipt["in_corridor"] = len(rows)

        scored: list[tuple[int, Any, list[str], Optional[int]]] = []
        for row in rows:
            rank = leverage_rank(row["notice_type"])
            if not include_awards and rank is not None and rank > SOLICITABLE_MAX_RANK:
                receipt["rank_4_excluded"] += 1
                continue
            text = " ".join(str(row[k] or "") for k in
                            ("title", "description_prefix"))
            if excluded_terms:
                from agents.golden_press.coverage_families import phrase_matches
                if any(phrase_matches(term, text) for term in excluded_terms):
                    receipt["client_exclusions"] += 1
                    continue
            deadline = str(row["deadline"] or "")[:10]
            if deadline and as_of and deadline < str(as_of)[:10]:
                receipt["closed_historic"] += 1
                continue
            # TWO INDEPENDENT ROUTES IN, EACH WITH ITS OWN GUARD.
            #   capability terms -> the tier ladder, qualified by the
            #                       client's NAICS/PSC boundary
            #   entity names     -> reject_entity_hit, the shared guard that
            #                       needs the vendor present for an ambiguous
            #                       name and for any reseller
            # Either route qualifies a record. Neither can be waved through by
            # a code alone, which is what the removed auto-pass used to do.
            ent_hits, _ = _guarded_entity_hits(
                text, matchers, vendor_pattern, receipt, row)
            if not matchers:
                # Unguarded path: the caller gave a flat term list with no
                # kinds and no vendor, so there is nothing to anchor against.
                # The receipt already says guards_applied is False.
                ent_hits = _word_hits(text, entity_terms)
            screen_terms = list(capability_terms) + distinctive
            verdict = screen_relevance(
                naics=row["naics"], psc=row["psc"],
                description=text,
                capability_terms=screen_terms,
                naics_boundary=naics_boundary, psc_boundary=psc_boundary,
                distinctive_names=distinctive)
            if not verdict and not ent_hits:
                continue
            receipt["screened"] += 1
            # CAPABILITY terms are procurement language: no vendor to anchor
            # against, their own negative-context guards, never sent through
            # the entity guard. They run the TIER LADDER instead, which is what
            # makes a single common word like "observability" usable at all: a
            # bare string match on it returns Navy stealth research, so tier 3
            # requires a tech code AND a domain anchor before it counts.
            cap_hits = _tiered_capability_hits(
                text, capability_terms,
                has_tech_code=_has_qualifying_code(
                    row, naics_prefixes=naics_boundary,
                    psc_prefixes=psc_boundary),
                distinctive=distinctive, guards=guards, receipt=receipt)
            hits = cap_hits + ent_hits
            if not hits:
                continue
            receipt["term_hits"] += 1
            scored.append((len(hits), row, hits, rank))

        # most term hits, then most shapeable, then soonest deadline
        scored.sort(key=lambda s: (-s[0], s[3] or 99,
                                   str(s[1]["deadline"] or "9999")))
        records = [_to_record(row, hits, rank) for _, row, hits, rank in scored]
        kept, dropped = dedupe_by_title_agency(records)
        receipt["deduped_away"] = len(dropped)
        if len(kept) > cap:
            _log(f"L1(store) capped at {cap} of {len(kept)} deduped notices; "
                 "the cap is loud, never silent")
        kept = kept[:cap]
        receipt["kept"] = len(kept)
        by_rank: dict[str, int] = {}
        for r in kept:
            key = str(r.notice_leverage_rank)
            by_rank[key] = by_rank.get(key, 0) + 1
        receipt["by_rank"] = by_rank

        query = LaneQuery(
            lane="L1_notice", method="notice_store",
            endpoint="data/state/notice_store/notices.db (local, zero quota)",
            body={"capability_terms": capability_terms,
                  "entity_terms": entity_terms,
                  "naics_prefixes": list(naics_boundary
                                         or TECH_NAICS_PREFIXES),
                  "psc_prefixes": list(psc_boundary
                                       or TECH_PSC_PREFIXES),
                  "qualifying_code_basis": receipt[
                      "qualifying_code_basis"],
                  "excluded_terms": list(excluded_terms or ()),
                  "as_of": str(as_of)[:10],
                  "rank_4_excluded": not include_awards,
                  "store_last_ingest": receipt["store_last_ingest"]},
            executed_at=_now_iso(),
            result_count=receipt["in_corridor"],
            kept_after_screen=len(kept))
        return kept, [query], receipt
    finally:
        if owned:
            conn.close()


def _to_record(row, hits: list[str], rank: Optional[int]) -> GoldenRecord:
    """One store row as a GoldenRecord, fully populated from the writer."""
    return GoldenRecord(
        record_id=str(row["notice_id"] or ""),
        lane="L1_notice",
        title=str(row["title"] or ""),
        agency=row["agency"] or None,
        sub_agency=row["subtier"] or None,
        office=row["office"] or None,
        naics=row["naics"] or None,
        psc=row["psc"] or None,
        set_aside=row["set_aside"] or None,
        posted_date=row["posted"] or None,
        response_deadline=row["deadline"] or None,
        description=row["description_prefix"] or None,
        notice_type=row["notice_type"] or None,
        notice_leverage_rank=rank,
        url=_notice_url(row["notice_id"], row["url"]),
        entity_hits=hits,
        contact_name=row["poc_name"] or None,
        contact_email=row["poc_email"] or None,
        contact_phone=row["poc_phone"] or None,
        contact_secondary_email=row["poc_secondary_email"] or None,
        contact_quality=contact_quality(row["poc_name"], row["poc_email"]),
        contact_use=contact_use(rank),
        relevance_method="notice_store_capability_search",
        relevance_matched=hits[:6],
        retrieved_at=str(row["last_seen"] or ""),
    )
