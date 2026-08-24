"""Corrected evidence pack assembly: schema v2 (JTG upstream order,
2026-08-20).

Consumes the pressed evidence pack plus the deep-sweep research rows,
classifies every record on the two independent dimensions
(evidence_route), collapses duplicate requirements to one canonical
record per family, builds qualified-opportunity blocks and
opportunity-linked target groups, and writes
<slug>.evidence_pack.v2.json beside the pressed pack. The renderer
consumes the v2 fields directly; nothing here renders, restyles, or
writes client prose.

Ledger honesty: every record whose v2 class differs from its rendered
seat in the delivered report is listed in moved_records with prior seat,
new class and the evidence basis. Research gaps are structured data.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.golden_press import evidence_route as er
from tools.intelligence_graph.adapter import ExistingSystemsGraphAdapter
from tools.intelligence_graph.workflow import (
    build_ambiguity_queue,
    workflow_contract_receipt,
)

SCHEMA_VERSION = "evidence-pack-v2"
_ROOT = Path(__file__).resolve().parents[2]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LIFECYCLE_WORDS = re.compile(
    r"\b(?:draft|final|amendment\s+\d+|sources?\s+sought|request\s+for\s+"
    r"information|rfi|presolicitation|pre\s+solicitation|solicitation|"
    r"combined\s+synopsis|special\s+notice|recompete)\b",
    flags=re.IGNORECASE)


def _family_basis(title: Any, agency: Any) -> str:
    """Stable requirement identity independent of lifecycle posting labels.

    The agency remains part of the identity. Procurement-stage vocabulary is
    removed so a Sources Sought and later Solicitation for the same named work
    resolve to one family without relying on record ids.
    """
    normalized_title = _LIFECYCLE_WORDS.sub(" ", str(title or ""))
    normalized_title = re.sub(
        r"[^a-z0-9]+", " ", normalized_title.casefold()).strip()
    normalized_agency = re.sub(
        r"[^a-z0-9]+", " ", str(agency or "").casefold()).strip()
    return normalized_title + "|" + normalized_agency


def _family_key(title: Any, agency: Any) -> str:
    basis = _family_basis(title, agency)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def _canonical_quality(row: dict) -> tuple:
    """Prefer the most actionable and best-evidenced family member."""
    return (
        1 if row.get("window_state") == "live" else 0,
        1 if row.get("response_deadline") else 0,
        1 if row.get("contact_email") else 0,
        1 if row.get("url") else 0,
        len(str(row.get("description") or "")),
        str(row.get("record_id") or ""),
    )


def canonicalize_requirement_families(rows: list[dict]) -> list[dict]:
    """Return one deterministic canonical record per requirement family.

    Every survivor receipts all source members so later lifecycle postings can
    be reconciled without a database migration.
    """
    members: dict[str, list[dict]] = {}
    for row in rows:
        fam = _family_key(row.get("title"), row.get("agency"))
        members.setdefault(fam, []).append(row)
    kept: list[dict] = []
    for fam in sorted(members):
        family_rows = members[fam]
        canonical = max(family_rows, key=_canonical_quality)
        member_ids = sorted({str(r.get("record_id") or "")
                             for r in family_rows if r.get("record_id")})
        kept.append(dict(
            canonical,
            requirement_family=fam,
            canonical_record_id=canonical.get("record_id"),
            family_member_ids=member_ids,
            family_member_count=len(family_rows),
            family_basis=_family_basis(canonical.get("title"),
                                       canonical.get("agency")),
        ))
    return kept


def dedupe_requirements(rows: list[dict]) -> list[dict]:
    """Compatibility wrapper for the canonical family resolver."""
    return canonicalize_requirement_families(rows)


_RECORD_IDENTITY_FIELDS = (
    "title", "description", "agency", "sub_agency", "recipient",
    "obligated_dollars", "ceiling_dollars", "period_start", "period_end",
    "potential_end_date", "url", "retrieved_at",
)


def canonicalize_record_ids(rows: list[dict]) -> tuple[list[dict], dict]:
    """Collapse repeated source identities before classification or rollup.

    The first observed position remains stable. When duplicate award rows
    disagree, prefer the greatest stated obligation for award identities, then
    the most complete row. A partial action amount must not replace the same
    award's larger total obligation.
    Every conflict remains visible in the receipt.
    """
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    anonymous = 0
    for raw in rows:
        row = dict(raw)
        record_id = str(row.get("record_id") or "").strip()
        if record_id:
            key = f"record:{record_id}"
        else:
            anonymous += 1
            key = f"anonymous:{anonymous}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)

    def quality(row: dict) -> tuple:
        populated = sum(
            row.get(field) not in (None, "", [], {})
            for field in _RECORD_IDENTITY_FIELDS
        )
        obligation = row.get("obligated_dollars")
        stated_obligation = (
            float(obligation)
            if isinstance(obligation, (int, float)) and not isinstance(obligation, bool)
            else float("-inf")
        )
        return (
            1 if stated_obligation != float("-inf") else 0,
            stated_obligation,
            1 if row.get("detail_enriched") is True else 0,
            populated,
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
        )

    kept: list[dict] = []
    duplicates: list[dict] = []
    for key in order:
        members = grouped[key]
        selected = max(members, key=quality)
        kept.append(selected)
        if len(members) == 1:
            continue
        conflicts: dict[str, list[Any]] = {}
        for field in _RECORD_IDENTITY_FIELDS:
            values = []
            seen = set()
            for member in members:
                value = member.get(field)
                marker = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, default=str)
                if marker in seen:
                    continue
                seen.add(marker)
                values.append(value)
            if len(values) > 1:
                conflicts[field] = values
        duplicates.append({
            "record_id": selected.get("record_id"),
            "input_rows": len(members),
            "discarded_rows": len(members) - 1,
            "selected_obligated_dollars": selected.get("obligated_dollars"),
            "conflicts": conflicts,
        })
    return kept, {
        "input_records_raw": len(rows),
        "canonical_records": len(kept),
        "record_duplicates_collapsed": len(rows) - len(kept),
        "duplicate_record_ids": duplicates,
        "selection_rule": (
            "preserve first identity position; choose greatest stated award "
            "obligation, then detail-enriched, then most complete, then stable JSON"
        ),
    }


def build_target_groups(opportunities: list[dict], *,
                        incumbents: Optional[dict[str, str]] = None,
                        enrichments: Optional[list[dict]] = None,
                        target_roles: Optional[dict[str, list[dict]]] = None,
                        ) -> dict[str, list[dict]]:
    """Targets grouped under the requirement they support, with exact
    public-source provenance; enrichment candidates carry identity and
    role only, never fabricated contact data."""
    groups: dict[str, list[dict]] = {}
    for opp in opportunities:
        fam = opp.get("requirement_family")
        if not fam:
            continue
        rows = groups.setdefault(fam, [])
        rid = opp.get("record_id")
        common = {
            "requirement_family": fam,
            "opportunity_record_id": rid,
            "opportunity_title": opp.get("title"),
        }
        name = str(opp.get("contact_name") or "").strip()
        email = str(opp.get("contact_email") or "").strip()
        if name or email:
            rows.append({
                **common,
                "role": "contracting_officer_or_specialist",
                "name": name or None, "email": email or None,
                "phone": str(opp.get("contact_phone") or "").strip() or None,
                "organization": opp.get("agency"),
                "source_kind": "published_contact",
                "contact_state": "sourced",
                "provenance": f"sam_notice:{rid}:primary_contact",
                "source_record_id": rid,
            })
        sec = str(opp.get("contact_secondary_email") or "").strip()
        if sec:
            rows.append({
                **common,
                "role": "contracting_officer_or_specialist",
                "name": None, "email": sec, "phone": None,
                "organization": opp.get("agency"),
                "source_kind": "published_contact",
                "contact_state": "sourced",
                "provenance": f"sam_notice:{rid}:secondary_contact",
                "source_record_id": rid,
            })
        sb = str(opp.get("small_business_poc") or "").strip()
        if sb:
            rows.append({
                **common,
                "role": "agency_small_business_or_industry_engagement",
                "name": sb, "email": None, "phone": None,
                "organization": opp.get("agency"),
                "source_kind": "published_contact",
                "contact_state": "sourced",
                "provenance": f"sam_notice:{rid}:small_business_poc",
                "source_record_id": rid,
            })
        incumbent = (incumbents or {}).get(fam)
        if incumbent:
            rows.append({
                **common,
                "role": "incumbent_or_likely_prime_capture_lead",
                "organization": incumbent,
                "role_needed": "capture or BD lead at the incumbent",
                "enrichment_candidate": True,
                "source_kind": "enrichment_candidate",
                "contact_state": "needs_enrichment",
                "name": None, "email": None, "phone": None,
                "provenance": "identity from notice text; contact data "
                              "requires enrichment (no credits spent)",
                "source_record_id": rid,
            })
        specified_roles = list((target_roles or {}).get(fam) or [])
        if not specified_roles:
            specified_roles = [{
                "role": "program_or_mission_owner",
                "organization": opp.get("agency"),
                "role_needed": "program or mission owner for this requirement",
            }]
            route = str(opp.get("commercial_route") or
                        opp.get("route_relationship") or "")
            if route in {"incumbent", "named_partner_teaming",
                         "possible_subcontracting"} and not incumbent:
                specified_roles.append({
                    "role": "partner_capture_or_bd_lead",
                    "organization": opp.get("route_organization"),
                    "role_needed": "capture or BD lead for the evidenced route",
                })
        existing_roles = {r.get("role") for r in rows}
        for role in specified_roles:
            if role.get("role") in existing_roles:
                continue
            rows.append({
                **common,
                "role": role.get("role"),
                "organization": role.get("organization"),
                "role_needed": role.get("role_needed"),
                "name": None, "email": None, "phone": None,
                "enrichment_candidate": True,
                "source_kind": "enrichment_candidate",
                "contact_state": "needs_enrichment",
                "provenance": "role derived from the qualified opportunity; "
                              "identity and contact fields require enrichment",
                "source_record_id": rid,
            })

    # Enrichments are opt-in records and never overwrite public-source rows.
    for contact in enrichments or []:
        fam = contact.get("requirement_family")
        if fam not in groups:
            continue
        opp = next((o for o in opportunities
                    if o.get("requirement_family") == fam), None)
        groups[fam].append({
            "requirement_family": fam,
            "opportunity_record_id": opp.get("record_id") if opp else None,
            "opportunity_title": opp.get("title") if opp else None,
            "role": contact.get("role"),
            "name": contact.get("name"),
            "title": contact.get("title"),
            "organization": contact.get("organization"),
            "email": contact.get("email"),
            "phone": contact.get("phone"),
            "linkedin": contact.get("linkedin"),
            "source_kind": "apollo_enrichment",
            "contact_state": "enriched",
            "provenance": contact.get("provenance") or
                          "apollo_enrichment:operator_approved",
            "source_record_id": opp.get("record_id") if opp else None,
        })
    return groups


def validate_graph_contract(payload: dict) -> list[dict]:
    """Return deterministic violations of the press-time graph contract.

    This is intentionally independent of the renderer.  It prevents a pack
    from carrying relationships that the evidence layer has already ruled
    impossible, and gives every press a small machine-readable certification
    receipt before any client-facing component is assembled.
    """
    violations: list[dict] = []

    def add(rule_id: str, message: str, **evidence: Any) -> None:
        violations.append({
            "rule_id": rule_id,
            "message": message,
            "evidence": evidence,
        })

    records = list(payload.get("records") or [])
    context = payload.get("classification_context") or {}
    # The relationship matcher operates on canonical normalized identities.
    # Normalizing again at the certification boundary keeps hand-authored,
    # replayed, and newly built packs under the same alias law.
    aliases = {
        er._norm(value)
        for value in (context.get("client_aliases") or [])
        if er._norm(value)
    }
    raw_partners = context.get("named_partners") or {}
    partner_names = (raw_partners.keys()
                     if isinstance(raw_partners, dict)
                     else raw_partners)
    partners = {
        er._norm(value): (raw_partners.get(value, "")
                          if isinstance(raw_partners, dict) else "")
        for value in partner_names
        if er._norm(value)
    }

    qualified = list(payload.get("qualified_opportunity_records") or [])
    qualified_by_family = {
        str(row.get("requirement_family")): row
        for row in qualified if row.get("requirement_family")
    }

    family_rows = list(payload.get("canonical_requirement_families") or [])
    family_keys = [str(row.get("requirement_family"))
                   for row in family_rows if row.get("requirement_family")]
    duplicate_families = sorted(
        key for key, count in Counter(family_keys).items() if count > 1)
    for family in duplicate_families:
        add("G006_DUPLICATE_REQUIREMENT_FAMILY",
            "canonical requirement family appears more than once",
            requirement_family=family)

    for row in records:
        record_id = row.get("record_id")
        evidence_class = row.get("evidence_class")
        recipient = row.get("recipient")
        if (evidence_class == "competitive_historical" and
                er._recipient_matches(recipient, aliases)):
            add("G001_CLIENT_CANNOT_BE_COMPETITOR",
                "client entity or alias classified as a competitor",
                record_id=record_id, recipient=recipient)
        if (evidence_class == "competitive_historical" and
                er._recipient_matches(recipient, partners)):
            add("G002_PARTNER_CANNOT_BE_COMPETITOR_WITHOUT_SEPARATE_EVIDENCE",
                "named partner classified as a competitor on the same record",
                record_id=record_id, recipient=recipient)
        if (row.get("window_state") == "stated_past" and
                evidence_class == "current_opportunity"):
            add("G003_PAST_WINDOW_CANNOT_BE_CURRENT",
                "past-dated record classified as a current opportunity",
                record_id=record_id)
        if (evidence_class == "current_opportunity" and
                row.get("commercial_route") == "direct" and
                row.get("eligible_route") is not True):
            add("G004_DIRECT_ROUTE_REQUIRES_ELIGIBILITY",
                "current direct opportunity has no eligible route",
                record_id=record_id, set_aside=row.get("set_aside"))

    for family, targets in sorted(
            (payload.get("target_groups") or {}).items()):
        supported = qualified_by_family.get(str(family))
        if not supported:
            add("G005_TARGETS_REQUIRE_QUALIFIED_OPPORTUNITY",
                "target group has no surviving qualified opportunity",
                requirement_family=family,
                target_count=len(targets or []))
            continue
        expected_record_id = supported.get("record_id")
        for target in targets or []:
            if target.get("opportunity_record_id") != expected_record_id:
                add("G007_TARGET_LINEAGE_MISMATCH",
                    "target points to a record outside its qualified family",
                    requirement_family=family,
                    expected_record_id=expected_record_id,
                    target_record_id=target.get("opportunity_record_id"),
                    target_name=target.get("name"))

    return violations


def _eligibility(record: dict, ctx: dict) -> dict:
    set_aside = str(record.get("set_aside") or "").strip()
    route = er.classify_route(record, ctx)
    return {
        "direct": route.get("commercial_route") == "direct" and
                  bool(route.get("eligible_route")),
        "eligible_route": bool(route.get("eligible_route")),
        "commercial_route": route.get("commercial_route"),
        "access_rule": set_aside or "none stated",
        "basis": route.get("route_basis"),
    }


def _technical_fit(record: dict, ctx: dict) -> dict:
    fit = er.classify_service_fit(record, ctx)
    return {"fit": fit["service_fit"] == "direct",
            "fit_class": fit["service_fit"],
            "basis": fit["fit_basis"]}


def _incumbent(record: dict) -> Optional[str]:
    text = " ".join(str(record.get(k) or "")
                    for k in ("title", "description"))
    m = er._INCUMBENT_RE.search(text)
    return m.group(1).strip() if m else None


def _hydrate_pocs(record: dict, store_conn) -> dict:
    """Fill notice contacts from the store when the pack row lacks them."""
    if record.get("contact_email") or store_conn is None:
        return record
    rid = str(record.get("record_id") or "")
    row = store_conn.execute(
        "SELECT poc_title, poc_name, poc_email, poc_phone, "
        "poc_secondary_email, office FROM notices WHERE notice_id = ?",
        (rid,)).fetchone()
    if row is None:
        return record
    out = dict(record)
    out.setdefault("contact_name", row["poc_name"])
    out["contact_name"] = out.get("contact_name") or row["poc_name"]
    out["contact_email"] = record.get("contact_email") or row["poc_email"]
    out["contact_phone"] = record.get("contact_phone") or row["poc_phone"]
    out["contact_secondary_email"] = (
        record.get("contact_secondary_email") or row["poc_secondary_email"])
    out["office"] = out.get("office") or row["office"]
    return out


def qualify_opportunities(records: list[dict], ctx: dict
                          ) -> tuple[list[dict], dict[str, str], list[dict]]:
    """Apply the promotion boundary after evidence classification.

    A record is a qualified opportunity only when the source window is current,
    the requirement fits the approved capability frame, and a commercially
    eligible route exists.  Held records stay in the evidence pack with a
    machine-readable reason; they are not silently dropped or promoted.
    """
    qualified_rows: list[dict] = []
    incumbents: dict[str, str] = {}
    held_rows: list[dict] = []
    for row in records:
        if row.get("evidence_class") != "current_opportunity":
            continue
        fit = _technical_fit(row, ctx)
        eligibility = _eligibility(row, ctx)
        route = er.classify_route(row, ctx)
        if not fit["fit"]:
            row["qualification_state"] = "held_for_fit_review"
            row["qualification_reason"] = fit["basis"]
            held_rows.append(row)
            continue
        if not eligibility["eligible_route"]:
            row["qualification_state"] = "needs_eligible_route"
            row["qualification_reason"] = eligibility["basis"]
            held_rows.append(row)
            continue

        incumbent = _incumbent(row)
        if incumbent:
            incumbents[row["requirement_family"]] = incumbent
            row["incumbent_name"] = incumbent
            row["incumbent_basis"] = "named in notice text"
        row["qualification_state"] = "qualified"
        row["qualified"] = {
            "notice_id": row.get("record_id"),
            "record_id": row.get("record_id"),
            "requirement_family": row.get("requirement_family"),
            "title": row.get("title"),
            "source_url": row.get("url"),
            "agency": row.get("agency"),
            "sub_agency": row.get("sub_agency"),
            "office": row.get("office"),
            "buyer": {"agency": row.get("agency"),
                      "sub_agency": row.get("sub_agency"),
                      "office": row.get("office")},
            "summary": row.get("description"),
            "description": row.get("description"),
            "naics": row.get("naics") or row.get("naics_code"),
            "psc": row.get("psc") or row.get("psc_code"),
            "instrument": row.get("notice_type"),
            "set_aside": row.get("set_aside"),
            "access_rule": row.get("set_aside") or "none stated",
            "posted_date": row.get("posted_date") or row.get("posted"),
            "solicitation_number": (
                row.get("solicitation_number") or row.get("sol_number")
                or (row.get("source_fields") or {}).get("sol_number")
            ),
            "response_due": row.get("response_deadline"),
            "published_value": row.get("ceiling_dollars")
                               or row.get("estimated_value_range"),
            "vehicle": row.get("vehicle"),
            "parent_award_id": row.get("parent_award_id"),
            "vehicle_class": row.get("vehicle_class"),
            "incumbent_name": row.get("incumbent_name"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "potential_end_date": row.get("potential_end_date"),
            "anticipated_solicitation": row.get("anticipated_solicitation"),
            "anticipated_solicitation_close": row.get(
                "anticipated_solicitation_close"),
            "anticipated_award": row.get("anticipated_award"),
            "fiscal_year": row.get("fiscal_year"),
            "small_business_poc": row.get("small_business_poc"),
            "contact_name": row.get("contact_name"),
            "contact_email": row.get("contact_email"),
            "contact_phone": row.get("contact_phone"),
            "contact_secondary_email": row.get("contact_secondary_email"),
            "evidence_basis": row.get("evidence_basis"),
            "fit_basis": row.get("fit_basis"),
            "route_basis": row.get("route_basis"),
            "window_basis": row.get("window_basis"),
            "matched_sentence": row.get("matched_sentence"),
            "match_route": row.get("match_route"),
            "relevance_method": row.get("relevance_method"),
            "canonical_entity": row.get("canonical_entity"),
            "canonical_record_id": row.get("canonical_record_id"),
            "family_member_ids": row.get("family_member_ids"),
            "retrieved_at": row.get("retrieved_at"),
            "evidence_receipts": [{
                "source_id": row.get("record_id"),
                "source_kind": "notice",
                "source_url": row.get("url"),
                "label": row.get("title"),
            }],
            "evidence_class": row.get("evidence_class"),
            "service_fit": row.get("service_fit"),
            "window_state": row.get("window_state"),
            "commercial_route": route.get("commercial_route"),
            "eligible_route": route.get("eligible_route"),
            "eligibility": eligibility,
            "technical_fit": fit,
            "relationship_provenance": row.get(
                "relationship_provenance"),
            "next_route": route,
        }
        qualified_rows.append(row)
    return qualified_rows, incumbents, held_rows


def _legacy_evidence_class(record: dict, rendered_names: set[str]) -> str:
    """Reconstruct the pre-v2 seat for a transparent move receipt.

    This is intentionally a receipt model, not a second production
    classifier.  Explicit prior labels win.  Otherwise it mirrors the old
    report's lane-based placement closely enough to state what visibly moved.
    """
    explicit = str(record.get("prior_evidence_class") or
                   record.get("legacy_evidence_class") or "").strip()
    if explicit in er.EVIDENCE_CLASSES:
        return explicit
    if record.get("source_sweep") == "deep_sweep":
        return "not_in_prior_pack"
    lane = str(record.get("lane") or "")
    if lane == "L1_notice":
        return "current_opportunity"
    if lane == "L2_entity_award":
        recipient = er._norm(record.get("recipient"))
        return ("competitive_historical"
                if not rendered_names or recipient in rendered_names
                else "ambiguous")
    if lane == "L4_forecast":
        return "forecast"
    if lane == "L5_event":
        return "event"
    return "ambiguous"


def build_move_receipt(records: list[dict],
                       rendered_competitor_names: Optional[set] = None
                       ) -> tuple[list[dict], dict[str, dict[str, int]]]:
    """Return every pre-v2 to v2 classification move and class counts."""
    rendered = {er._norm(name)
                for name in (rendered_competitor_names or set())}
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    moved: list[dict] = []
    for record in records:
        prior = _legacy_evidence_class(record, rendered)
        current = str(record.get("evidence_class") or "ambiguous")
        record["prior_evidence_class"] = prior
        before[prior] += 1
        after[current] += 1
        if prior in {"not_in_prior_pack", current}:
            continue
        moved.append({
            "record_id": record.get("record_id"),
            "title": record.get("title"),
            "recipient": record.get("recipient"),
            "prior_evidence_class": prior,
            "new_evidence_class": current,
            "commercial_route": record.get("commercial_route"),
            "window_state": record.get("window_state"),
            "service_fit": record.get("service_fit"),
            "evidence": record.get("evidence_basis"),
        })
    classes = sorted(set(before) | set(after))
    totals = {cls: {"before": before[cls], "after": after[cls]}
              for cls in classes}
    return moved, totals


def _move_receipt_markdown(client_name: str, payload: dict) -> str:
    lines = [
        f"# {client_name} evidence classification moves",
        "",
        "## Before and after by class",
        "",
        "| Evidence class | Before | After |",
        "| --- | ---: | ---: |",
    ]
    for cls, counts in sorted(
            (payload.get("before_after_by_class") or {}).items()):
        lines.append(
            f"| {cls} | {counts.get('before', 0)} | "
            f"{counts.get('after', 0)} |")
    lines += ["", "## Moved records", "",
              "| Record | Prior class | New class | Commercial route | Reason |",
              "| --- | --- | --- | --- | --- |"]
    for row in payload.get("moved_records") or []:
        reason = str(row.get("evidence") or "").replace("|", "/")
        lines.append(
            f"| {row.get('record_id') or ''} | "
            f"{row.get('prior_evidence_class') or ''} | "
            f"{row.get('new_evidence_class') or ''} | "
            f"{row.get('commercial_route') or ''} | {reason} |")
    if not payload.get("moved_records"):
        lines.append("| None |  |  |  | No pre-v2 records changed class. |")
    lines.append("")
    return "\n".join(lines)


def build_corrected_pack(slug: str, client_name: str, *,
                         root: Optional[Path] = None,
                         pressed_pack_path: Optional[Path] = None,
                         pack_dir: Optional[Path] = None,
                         deep_sweep_path: Optional[Path] = None,
                         store_conn=None,
                         rendered_competitor_names: Optional[set] = None,
                         research_gaps: Optional[list] = None,
                         enrichments: Optional[list[dict]] = None,
                         target_roles: Optional[dict[str, list[dict]]] = None,
                         graph_adapter: Optional[
                             ExistingSystemsGraphAdapter] = None,
                         cache_dir: Optional[Path] = None,
                         ) -> tuple[Path, dict]:
    base = Path(root) if root else _ROOT
    pack_dir = (Path(pack_dir) if pack_dir is not None else
                base / "data" / "state" / "candidate_review_v1" / slug)
    pressed_pack_path = (Path(pressed_pack_path)
                         if pressed_pack_path is not None else
                         pack_dir / f"{slug}.golden_report.evidence_pack.json")
    pressed = json.loads(pressed_pack_path.read_text(encoding="utf-8"))
    ctx = er.build_context(client_name, slug, pack=pressed, root=base)
    adapter = graph_adapter or ExistingSystemsGraphAdapter(
        root=base,
        slug=slug,
        client_name=client_name,
        classification_context=ctx,
        cache_dir=cache_dir,
    )

    records = [dict(r) for r in pressed.get("records") or []]
    sweep_rows: list[dict] = []
    sweep_meta: dict = {}
    if deep_sweep_path and Path(deep_sweep_path).exists():
        sweep = json.loads(Path(deep_sweep_path).read_text(encoding="utf-8"))
        sweep_meta = sweep.get("receipt") or {}
        known = {str(r.get("record_id")) for r in records}
        for row in sweep.get("records") or []:
            if str(row.get("record_id")) not in known:
                sweep_rows.append(dict(row, source_sweep="deep_sweep"))
    every_raw = records + sweep_rows
    every, record_identity_receipt = canonicalize_record_ids(every_raw)

    classified = []
    for record in every:
        if record.get("lane") == "L1_notice" and store_conn is not None:
            record = _hydrate_pocs(record, store_conn)
        classified.append(adapter.classify_record(record))

    l1 = [r for r in classified if r.get("lane") == "L1_notice"]
    rest = [r for r in classified if r.get("lane") != "L1_notice"]
    l1_deduped = dedupe_requirements(l1)
    dropped_dupes = len(l1) - len(l1_deduped)
    final = rest + l1_deduped

    opportunities, incumbents, held_opportunities = qualify_opportunities(
        final, ctx)
    research_queue = build_ambiguity_queue(final)

    active_requirement_families = {
        str(row.get("requirement_family"))
        for row in opportunities if row.get("requirement_family")
    }
    reusable_enrichments = adapter.merge_apollo_enrichments(
        enrichments,
        active_requirement_families=active_requirement_families,
    )
    target_groups = build_target_groups(
        opportunities,
        incumbents=incumbents,
        enrichments=reusable_enrichments,
        target_roles=target_roles,
    )
    for opportunity in opportunities:
        linked_targets = target_groups.get(
            opportunity.get("requirement_family"), [])
        opportunity["linked_targets"] = linked_targets
        opportunity["qualified"]["linked_targets"] = linked_targets

    moved, before_after = build_move_receipt(
        final, rendered_competitor_names)

    def _count(cls):
        return sum(1 for r in final if r.get("evidence_class") == cls)

    counts = {
        "client_historical": _count("client_historical"),
        "competitive_historical": _count("competitive_historical"),
        "current_opportunity": _count("current_opportunity"),
        "forecast": _count("forecast"),
        "event": _count("event"),
        "excluded": _count("excluded"),
        "ambiguous": _count("ambiguous"),
        "teaming_route_records": sum(
            1 for r in final
            if r.get("route_relationship") == "named_partner_teaming"),
        "opportunity_linked_targets": sum(
            len(v) for v in target_groups.values()),
        "needs_eligible_route": sum(
            1 for r in held_opportunities
            if r.get("qualification_state") == "needs_eligible_route"),
        "held_for_fit_review": sum(
            1 for r in held_opportunities
            if r.get("qualification_state") == "held_for_fit_review"),
        "ambiguous_research_queue": len(research_queue),
        "requirement_duplicates_collapsed": dropped_dupes,
        "input_records_raw": record_identity_receipt["input_records_raw"],
        "canonical_records": record_identity_receipt["canonical_records"],
        "record_duplicates_collapsed": record_identity_receipt[
            "record_duplicates_collapsed"],
    }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "client_name": client_name,
        "slug": slug,
        "generated_at": _now_iso(),
        "base_pack_generated_at": pressed.get("generated_at"),
        "canonical_entities": ctx.get("canonical_entities", {}),
        "canonical_requirement_families": [{
            "requirement_family": row.get("requirement_family"),
            "canonical_record_id": row.get("canonical_record_id"),
            "family_member_ids": list(row.get("family_member_ids") or []),
            "family_member_count": int(row.get("family_member_count") or 1),
            "family_basis": row.get("family_basis"),
        } for row in final if row.get("requirement_family")],
        "classification_context": {
            "client_aliases": sorted(ctx["client_aliases"]),
            "validated_competitors": ctx["validated_competitors"],
            "named_partners": ctx["named_partners"],
            "scope_terms": ctx["scope_terms"],
            "core_terms": ctx.get("core_terms", []),
            "adjacent_terms": ctx.get("adjacent_terms", []),
            "core_signal_pairs": ctx.get("core_signal_pairs", []),
            "adjacent_signal_pairs": ctx.get("adjacent_signal_pairs", []),
            "excluded_terms": ctx.get("excluded_terms", []),
            "excluded_codes": ctx.get("excluded_codes", []),
            "canonical_entities": ctx.get("canonical_entities", {}),
            "as_of": ctx["as_of"],
        },
        "counts": counts,
        "record_identity_receipt": record_identity_receipt,
        "records": final,
        "qualified_opportunities": [r["record_id"] for r in opportunities],
        "qualified_opportunity_records": [r["qualified"]
                                            for r in opportunities],
        "held_opportunities": [{
            "record_id": r.get("record_id"),
            "requirement_family": r.get("requirement_family"),
            "qualification_state": r.get("qualification_state"),
            "qualification_reason": r.get("qualification_reason"),
            "evidence_class": r.get("evidence_class"),
            "commercial_route": r.get("commercial_route"),
            "eligible_route": r.get("eligible_route"),
        } for r in held_opportunities],
        "target_groups": target_groups,
        "moved_records": moved,
        "before_after_by_class": before_after,
        "deep_sweep_receipt": sweep_meta,
        "research_gaps": list(research_gaps or []),
        "workflow_contract": workflow_contract_receipt(),
        "research_queue": research_queue,
        "incremental_cache_receipt": adapter.receipt(),
    }
    payload["graph_contract_violations"] = validate_graph_contract(payload)
    payload["graph_contract_certified"] = not bool(
        payload["graph_contract_violations"])
    out = pack_dir / f"{slug}.evidence_pack.v2.json"
    receipt = pack_dir / f"{slug}.evidence_pack.v2.moved_records.md"
    payload["moved_records_receipt"] = str(receipt)
    from tools.artifacts import atomic_write_json
    atomic_write_json(out, payload)
    from tools.atomic_io import atomic_write_text
    atomic_write_text(str(receipt), _move_receipt_markdown(
        client_name, payload))
    return out, payload
