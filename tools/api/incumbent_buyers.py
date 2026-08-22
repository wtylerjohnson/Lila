"""Incumbent buyer map — the highest-yield capability collector (2026-07-09).

Named incumbent products (ForeFlight, Jeppesen, Compusult, ...) and adjacent
capability terms are searched against USASpending award AND subaward
DESCRIPTION text. Every hit maps buyer office -> products bought -> total
spend -> next period-of-performance end. A PoP end inside 18 months is a
DISPLACEMENT WINDOW: the moment an incumbent subscription can be contested.

Quota rule: this collector NEVER touches the SAM API. Notice cross-reference
(intent-to-sole-source / J&A language) comes from the local sweep extract.
Per-source call counts are returned on every run.

Standing pipeline module, not a script: results carry source URLs and a
retrieved-at timestamp, and facts.py renders them as citable fact IDs.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Optional

from tools.api._http import post_json

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AWARD_LINK = "https://www.usaspending.gov/award/"

AWARD_FIELDS = ["Award ID", "Recipient Name", "Award Amount",
                "Awarding Agency", "Awarding Sub Agency",
                "Start Date", "End Date", "Contract Award Type", "NAICS",
                "PSC", "Description", "generated_internal_id"]
SUB_FIELDS = ["Sub-Award ID", "Sub-Awardee Name", "Sub-Award Amount",
              "Prime Recipient Name", "Prime Award ID", "Action Date",
              "Sub-Award Description", "Awarding Agency",
              "Awarding Sub Agency"]

DISPLACEMENT_WINDOW_DAYS = 548  # 18 months

_COMPANY_SUFFIXES = {
    "co", "company", "corp", "corporation", "inc", "incorporated",
    "llc", "lp", "ltd", "limited",
}


def _company_key(value: str) -> str:
    words = [
        word for word in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
        if word not in _COMPANY_SUFFIXES
    ]
    return " ".join(words)


def _is_client_recipient(recipient: str, client_name: str) -> bool:
    recipient_key = _company_key(recipient)
    client_key = _company_key(client_name)
    return bool(
        client_key
        and (recipient_key == client_key
             or recipient_key.startswith(client_key + " "))
    )


def _norm_office(*parts: str) -> str:
    """One buyer aggregates across records: uppercase, collapse whitespace
    and punctuation variants."""
    blob = " / ".join(p.strip() for p in parts if p and p.strip())
    return re.sub(r"\s+", " ", blob.upper().replace(".", "")).strip()


def _keyword_filter(term: str, years_back: int) -> dict:
    start = f"{date.today().year - years_back}-01-01"
    return {"keywords": [term],
            "time_period": [{"start_date": start,
                             "end_date": date.today().isoformat()}],
            "award_type_codes": ["A", "B", "C", "D"]}


def _search(term: str, *, subawards: bool, years_back: int,
            limit: int = 60) -> list[dict]:
    payload: dict[str, Any] = {
        "filters": _keyword_filter(term, years_back),
        "fields": SUB_FIELDS if subawards else AWARD_FIELDS,
        "subawards": subawards, "limit": min(limit, 100),
        "sort": "Sub-Award Amount" if subawards else "Award Amount",
        "order": "desc",
    }
    response = post_json(SEARCH_URL, json=payload)
    if not isinstance(response, dict):
        raise ValueError("USAspending buyer-map response must be an object")
    rows = response.get("results")
    if not isinstance(rows, list):
        raise ValueError(
            "USAspending buyer-map response requires list-valued results"
        )
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("USAspending buyer-map results must contain objects")
    identity_fields = (
        ("Sub-Award ID", "Prime Award ID")
        if subawards
        else ("generated_internal_id", "Award ID")
    )
    for row in rows:
        if not any(str(row.get(field) or "").strip()
                   for field in identity_fields):
            lane = "subaward" if subawards else "award"
            raise ValueError(
                f"USAspending buyer-map {lane} result omitted identity"
            )
    return rows


def build_buyer_map(profile, *, years_back: int = 5,
                    sam_notices: Optional[list[dict]] = None,
                    budget_seconds: float = 180.0) -> dict:
    """The buyer map. `sam_notices` is the LOCAL sweep extract (never the
    SAM API); intent-to-sole-source and J&A language attaches to its buyer
    row with the notice link so the report can quote what would break the
    sole source.

    `budget_seconds` is the wall-clock budget for the USASpending term
    sweep: under API throttling the collector stops fetching, reports the
    skipped terms honestly, and never hangs the fan-out. Skipped terms are
    disclosed in the output, not silently dropped."""
    import time as _time
    from tools.capability import incumbent_hits, match_capability

    started = _time.monotonic()
    skipped_terms: list[str] = []
    failed_terms: list[dict[str, str]] = []
    # The client's own name is an incumbent-footprint query, not a competitor
    # claim. Preserve first-seen display spelling while deduplicating the
    # potentially overlapping product/adjacent lists case-insensitively.
    terms = []
    seen_terms = set()
    for term in ([profile.client_name]
                 + list(profile.named_competitors_and_incumbents)
                 + list(profile.capability_terms.adjacent)):
        key = str(term).strip().casefold()
        if key and key not in seen_terms:
            seen_terms.add(key)
            terms.append(str(term).strip())
    calls = {"usaspending_awards": 0, "usaspending_subawards": 0, "sam_api": 0}
    buyers: dict[str, dict] = {}
    horizon = (date.today() + timedelta(days=DISPLACEMENT_WINDOW_DAYS)).isoformat()

    def _row(buyer_key: str, agency: str) -> dict:
        return buyers.setdefault(buyer_key, {
            "buyer": buyer_key, "agency": agency, "products": set(),
            "terms": set(), "total": 0.0, "records": [],
            "next_pop_end": None, "sole_source_notices": []})

    def _ingest(rec: dict, desc: str, amount: float, end: Optional[str],
                agency: str, sub_agency: str, url: str, kind: str) -> None:
        recipient = str(rec.get("Recipient Name")
                        or rec.get("Prime Recipient Name") or "")
        client_recipient = _is_client_recipient(
            recipient, profile.client_name)
        hits = list(incumbent_hits(profile, desc))
        if client_recipient:
            hits.append(profile.client_name)
        hits = sorted(set(hits), key=lambda value: value.casefold())
        # USASpending keyword results can match the recipient as well as the
        # description (for example STEELHEAD ENTERPRISES).  Apply the profile
        # veto to both fields before a named-product hit can admit the row.
        # An objective client-recipient match remains footprint evidence even
        # when its terse description happens to contain an excluded phrase.
        m = match_capability(
            profile, desc, recipient, component=f"{sub_agency} {agency}")
        if m.tier == "excluded" and not client_recipient:
            return
        if not hits and m.score < 1:
            return
        key = _norm_office(sub_agency or agency)
        b = _row(key, agency)
        b["products"].update(hits)
        b["terms"].update(m.matched)
        # gross value of matched records: keyword-matched awards counted at
        # full obligated value, a coverage signal, never lane-total framing
        b["total"] += amount or 0
        # displacement logic needs the earliest FUTURE end, not history
        if end and end >= date.today().isoformat() and (
                b["next_pop_end"] is None or end < b["next_pop_end"]):
            b["next_pop_end"] = end
        b["records"].append({
            "kind": kind, "recipient": recipient,
            "amount": amount,
            "amount_basis": "obligated_to_date",
            "end_date": end,
            # Preserve enough of the official award description for the
            # downstream relevance scorer to re-derive a matched capability
            # span.  The former 180-character cut could split the exact term
            # that nominated the record (for example, "data labeling"),
            # leaving a candidate in the buyer map but making its evidence
            # impossible to publish.
            "description": desc[:400], "url": url,
            # structured identity for downstream provenance (C1 figures,
            # award re-pull); never reconstructed from the url
            "award_id": rec.get("Award ID"),
            "generated_internal_id": rec.get("generated_internal_id"),
            # Available on the same sanctioned spending-by-award response.
            # Preserve it before compose so Section 03 can distinguish an
            # executed delivery order, purchase order, or definitive contract
            # without depending on the later award-detail re-pull.
            "award_structure": rec.get("Contract Award Type"),
            "naics": rec.get("NAICS"),
            "psc": rec.get("PSC"),
            "matched_products": hits, "matched_terms": m.matched,
            "capability_score": m.score})

    seen_award_ids: set = set()
    for term in terms:
        if _time.monotonic() - started > budget_seconds:
            skipped_terms.append(term)
            continue
        try:
            rows = _search(term, subawards=False, years_back=years_back)
            calls["usaspending_awards"] += 1
        except Exception:  # noqa: BLE001 — one term never kills the map
            failed_terms.append({"term": term, "lane": "awards"})
            rows = []
        for r in rows:
            aid = r.get("generated_internal_id") or r.get("Award ID")
            if aid in seen_award_ids:
                continue
            seen_award_ids.add(aid)
            _ingest(r, str(r.get("Description") or ""),
                    r.get("Award Amount") or 0, r.get("End Date"),
                    r.get("Awarding Agency") or "",
                    r.get("Awarding Sub Agency") or "",
                    f"{AWARD_LINK}{r.get('generated_internal_id')}"
                    if r.get("generated_internal_id") else SEARCH_URL,
                    "award")
        try:
            subs = _search(term, subawards=True, years_back=years_back)
            calls["usaspending_subawards"] += 1
        except Exception:  # noqa: BLE001
            failed_terms.append({"term": term, "lane": "subawards"})
            subs = []
        for r in subs:
            sid = r.get("Sub-Award ID")
            if sid in seen_award_ids:
                continue
            seen_award_ids.add(sid)
            _ingest(r, str(r.get("Sub-Award Description") or ""),
                    r.get("Sub-Award Amount") or 0, None,
                    r.get("Awarding Agency") or "",
                    r.get("Awarding Sub Agency") or "", SEARCH_URL, "subaward")

    # LOCAL notice cross-reference (quota rule: sam_api stays 0)
    for n in sam_notices or []:
        rp = n.get("raw_payload") or {}
        text = f"{n.get('title') or ''} {rp.get('description_snippet') or ''}"
        hits = incumbent_hits(profile, text)
        notice_match = match_capability(
            profile, text,
            component=f"{rp.get('subtier') or ''} {rp.get('agency') or ''}")
        if not hits or notice_match.tier == "excluded":
            continue
        key = _norm_office(rp.get("subtier") or rp.get("agency") or "",
                           )
        b = _row(key, rp.get("agency") or "")
        b["products"].update(h for h in hits if h != profile.client_name)
        ntype = (rp.get("type") or "").lower()
        sole = ("sole source" in text.lower() or "sole source" in ntype
                or "intent" in ntype or "justification" in ntype
                or "j&a" in text.lower())
        b["sole_source_notices"].append({
            "notice_id": n.get("source_id"), "title": n.get("title"),
            "type": rp.get("type"), "sole_source": sole,
            "office": rp.get("office"),
            "quote": (rp.get("description_snippet") or "")[:240],
            "url": n.get("api_url") or rp.get("url")})

    table = []
    for b in buyers.values():
        b["products"] = sorted(b["products"])
        b["terms"] = sorted(b["terms"])
        b["records"] = sorted(b["records"],
                              key=lambda r: -(r.get("amount") or 0))[:8]
        b["sole_source"] = any(x["sole_source"]
                               for x in b["sole_source_notices"])
        b["displacement_window"] = bool(
            b["next_pop_end"] and
            date.today().isoformat() <= b["next_pop_end"] <= horizon)
        table.append(b)
    table.sort(key=lambda b: -b["total"])
    return {"population_label": ("gross obligated value of description-"
                                 "matched award and subaward records, "
                                 f"trailing {years_back} years"),
            "buyers": table,
            "displacement_windows": [b for b in table
                                     if b["displacement_window"]],
            "retrieved_at": date.today().isoformat(),
            "window_days": DISPLACEMENT_WINDOW_DAYS,
            "skipped_terms": skipped_terms,
            "failed_terms": failed_terms,
            "calls": calls, "source": SEARCH_URL}
