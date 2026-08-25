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
from tools.intelligence_graph.cache import canonicalize as canonicalize_cache_value
from tools.intelligence_graph.workflow import (
    build_ambiguity_queue,
    workflow_contract_receipt,
)

SCHEMA_VERSION = "evidence-pack-v2"
_ROOT = Path(__file__).resolve().parents[2]


def _aware_utc_iso(value: str | datetime, field: str) -> str:
    """Normalize an explicitly supplied aware instant to UTC."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if "T" not in text:
            raise ValueError(f"{field} must be a timezone-aware timestamp")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _captured_input_clock(
    value: str | datetime, *, label: str, classification_as_of: str,
) -> str:
    """Validate one input receipt against the frozen classification clock."""
    captured = _aware_utc_iso(value, label)
    captured_instant = datetime.fromisoformat(
        captured.replace("Z", "+00:00"))
    classification_instant = datetime.fromisoformat(
        classification_as_of.replace("Z", "+00:00"))
    if captured_instant > classification_instant:
        raise ValueError(f"{label} is later than classification_as_of")
    return captured


_LIFECYCLE_WORDS = re.compile(
    r"\b(?:draft|final|amendment\s+\d+|sources?\s+sought|request\s+for\s+"
    r"information|rfi|presolicitation|pre\s+solicitation|solicitation|"
    r"combined\s+synopsis|special\s+notice|recompete)\b",
    flags=re.IGNORECASE)


def _solicitation_number(row: dict) -> str:
    source_fields = row.get("source_fields") or {}
    raw_payload = row.get("raw_payload") or {}
    candidates = (
        row.get("solicitation_number"), row.get("sol_number"),
        source_fields.get("solicitation_number"), source_fields.get("sol_number"),
        source_fields.get("solicitation"), raw_payload.get("solicitation"),
    )
    for candidate in candidates:
        normalized = re.sub(
            r"[^a-z0-9]+", "", str(candidate or "").casefold())
        if len(normalized) >= 6 and re.search(r"\d", normalized):
            return normalized
    title_match = re.search(
        r"\bsolicitation(?:\s+(?:number|no\.?))?\s*[:#]?\s*"
        r"([A-Z0-9][A-Z0-9._/\-]{5,})",
        str(row.get("title") or ""),
        flags=re.IGNORECASE,
    )
    description_match = re.search(
        r"\b(?:intends?|expects?|plans?)\s+to\s+issue\s+(?:the\s+)?"
        r"solicitation(?:\s+(?:number|no\.?))?\s*[:#]?\s*"
        r"([A-Z0-9][A-Z0-9._/\-]{5,})",
        str(row.get("description") or ""),
        flags=re.IGNORECASE,
    )
    match = title_match or description_match
    if not match:
        return ""
    candidate = match.group(1)
    if not re.search(r"\d", candidate):
        return ""
    return re.sub(r"[^a-z0-9]+", "", candidate.casefold())


def _successor_solicitation_number(row: dict) -> str:
    """Return only an explicitly sourced successor solicitation identity."""
    source_fields = row.get("source_fields") or {}
    raw_payload = row.get("raw_payload") or {}
    for candidate in (
            row.get("successor_solicitation_number"),
            source_fields.get("successor_solicitation_number"),
            raw_payload.get("successor_solicitation_number")):
        normalized = re.sub(
            r"[^a-z0-9]+", "", str(candidate or "").casefold())
        if len(normalized) >= 6 and re.search(r"\d", normalized):
            return normalized
    text = " ".join(str(row.get(key) or "")
                    for key in ("title", "description"))
    pattern = re.compile(
        r"\b(?:(?:successor|follow[- ]on|replacement)\s+solicitation|"
        r"(?:intends?|expects?|plans?)\s+to\s+issue\s+(?:a\s+|the\s+)?"
        r"solicitation)(?:\s+(?:number|no\.?))?\s*[:#]?\s*"
        r"([A-Z0-9][A-Z0-9._/\-]{5,})",
        flags=re.IGNORECASE)
    for match in pattern.finditer(text):
        if not re.search(r"\d", match.group(1)):
            continue
        negation_context = text[max(0, match.start() - 80):match.start()]
        if re.search(
                r"\b(?:not|no|without|neither)\b"
                r"(?:\s+(?!(?:a|the)\b)[\w-]+){0,4}"
                r"\s+(?:a|the)?\s*$",
                negation_context,
                flags=re.IGNORECASE):
            continue
        return re.sub(r"[^a-z0-9]+", "", match.group(1).casefold())
    return ""


def _notice_namespace(row: dict) -> tuple[str, str]:
    """Return the buyer namespace required for solicitation-family identity.

    Solicitation numbers are not globally unique. Match the conservative
    triage identity law: both agency and issuing office must be present before
    a solicitation number can join notices. Missing identity fails open to the
    exact notice rather than merging unrelated procurements.
    """
    source_fields = row.get("source_fields") or {}
    raw_payload = row.get("raw_payload") or {}
    agency = (
        row.get("agency") or source_fields.get("agency")
        or raw_payload.get("agency")
    )
    office = (
        row.get("office") or source_fields.get("office")
        or raw_payload.get("office")
    )
    return (
        re.sub(r"[^a-z0-9]+", " ", str(agency or "").casefold()).strip(),
        re.sub(r"[^a-z0-9]+", " ", str(office or "").casefold()).strip(),
    )


def _family_basis(title_or_row: Any, agency: Any = None) -> str:
    """Stable requirement identity independent of lifecycle posting labels.

    The agency remains part of the identity. Procurement-stage vocabulary is
    removed so a Sources Sought and later Solicitation for the same named work
    resolve to one family without relying on record ids.
    """
    if isinstance(title_or_row, dict):
        solicitation = (
            _successor_solicitation_number(title_or_row)
            or _solicitation_number(title_or_row))
        normalized_agency, normalized_office = _notice_namespace(title_or_row)
        if solicitation and normalized_agency and normalized_office:
            return (f"sam.gov|{solicitation}|{normalized_agency}|"
                    f"{normalized_office}")
        if solicitation:
            record_id = str(title_or_row.get("record_id") or "").strip()
            if record_id:
                return f"notice:{record_id}"
        record_id = str(title_or_row.get("record_id") or "").strip()
        if record_id:
            return f"notice:{record_id}"
        title = title_or_row.get("title")
        agency = title_or_row.get("agency")
        _normalized_agency, normalized_office = _notice_namespace(title_or_row)
    else:
        title = title_or_row
        normalized_office = ""
    normalized_title = _LIFECYCLE_WORDS.sub(" ", str(title or ""))
    normalized_title = re.sub(
        r"[^a-z0-9]+", " ", normalized_title.casefold()).strip()
    normalized_agency = re.sub(
        r"[^a-z0-9]+", " ", str(agency or "").casefold()).strip()
    return normalized_title + "|" + normalized_agency + "|" + normalized_office


def _family_key(title_or_row: Any, agency: Any = None) -> str:
    basis = _family_basis(title_or_row, agency)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def _family_group_tokens(rows: list[dict]) -> list[str]:
    """Resolve explicit solicitation-successor chains within one buyer.

    A -> B and B -> C are one requirement family even when no individual row
    mentions both ends of the full chain.  Solicitation identities are joined
    only when both agency and issuing office are present; every other row keeps
    the conservative exact-notice fallback supplied by ``_family_key``.
    """
    parent: dict[str, str] = {}

    def node(row: dict, solicitation: str) -> str:
        agency, office = _notice_namespace(row)
        if not solicitation or not agency or not office:
            return ""
        return f"sam.gov|{solicitation}|{agency}|{office}"

    def find(identity: str) -> str:
        parent.setdefault(identity, identity)
        while parent[identity] != identity:
            parent[identity] = parent[parent[identity]]
            identity = parent[identity]
        return identity

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        parent[second] = first

    identities: list[tuple[str, str]] = []
    successor_edges: dict[str, set[str]] = {}
    for row in rows:
        posted = node(row, _solicitation_number(row))
        successor = node(row, _successor_solicitation_number(row))
        if posted:
            find(posted)
        if successor:
            find(successor)
        if posted and successor and posted != successor:
            successor_edges.setdefault(posted, set()).add(successor)
        identities.append((posted, successor))

    accepted_edges: set[tuple[str, str]] = set()
    for posted, successors in successor_edges.items():
        # Conflicting successor claims are evidence for review, not permission
        # to collapse otherwise distinct procurement families.
        if len(successors) != 1:
            continue
        successor = next(iter(successors))
        union(posted, successor)
        accepted_edges.add((posted, successor))

    tokens: list[str] = []
    for row, (posted, successor) in zip(rows, identities):
        identity = (
            successor if not posted or posted == successor
            else successor if (posted, successor) in accepted_edges
            else posted
        )
        tokens.append(
            f"component:{find(identity)}" if identity else
            f"notice:{_family_key(row)}"
        )
    return tokens


def _notice_stage_rank(row: dict) -> int:
    label = " ".join(str(row.get(key) or "") for key in (
        "notice_type", "base_notice_type", "title")).casefold()
    if "presolicitation" in label or "pre solicitation" in label:
        return 4
    if any(term in label for term in (
            "solicitation", "request for proposal", "request for quote")):
        return 5
    if "sources sought" in label:
        return 3
    if "request for information" in label or re.search(r"\brfi\b", label):
        return 2
    if "special notice" in label:
        return 1
    return 0


def _notice_source_status(row: dict) -> Any:
    source_fields = row.get("source_fields") or {}
    for candidate in (
            row.get("source_status"), row.get("active"),
            source_fields.get("active"), source_fields.get("status")):
        if candidate is not None and candidate != "":
            return candidate
    return ""


def _notice_active_rank(row: dict) -> int:
    value = _notice_source_status(row)
    if value is True:
        return 2
    if value is False:
        return 0
    value = str(value).strip().casefold()
    notice_type = str(row.get("notice_type") or "").casefold()
    if any(term in notice_type for term in ("cancel", "inactive", "archive")):
        return 0
    if value in {"no", "false", "inactive", "cancelled", "canceled", "archived"}:
        return 0
    if value in {"yes", "true", "active"}:
        return 2
    return 1


def _canonical_quality(row: dict) -> tuple:
    """Prefer the newest active family member, then operational richness."""
    return (
        _notice_active_rank(row),
        str(row.get("posted_date") or row.get("posted") or ""),
        1 if row.get("window_state") == "live" else 0,
        _notice_stage_rank(row),
        1 if row.get("response_deadline") else 0,
        1 if row.get("contact_email") else 0,
        1 if row.get("contact_secondary_email") else 0,
        1 if row.get("url") else 0,
        len(str(row.get("description") or "")),
        str(row.get("record_id") or ""),
    )


_FAMILY_CARRY_FIELDS = (
    "description", "agency", "sub_agency", "office", "response_deadline",
    "solicitation_number", "set_aside", "set_aside_code", "type_set_aside",
    "contact_name", "contact_email", "contact_phone",
    "contact_secondary_email", "naics", "naics_code", "psc", "psc_code",
    "incumbent_name", "url",
)


def _family_field_source(row: dict) -> dict:
    return {
        "record_id": row.get("record_id"),
        "posted_date": row.get("posted_date") or row.get("posted"),
        "source_url": row.get("url"),
    }


def _reconcile_family_fields(canonical: dict, family_rows: list[dict]) -> dict:
    """Carry rich scope and missing operational facts into the active notice.

    The canonical identity remains the newest active notice.  A terse
    amendment may legitimately omit the base solicitation's scope, deadline,
    code, or contact; dropping those fields would make classification and the
    external product less truthful.  Each carried field names the family
    member that supplied it.
    """
    merged = dict(canonical)
    active_or_unknown = [
        row for row in family_rows if _notice_active_rank(row) > 0
    ] or list(family_rows)
    newest_first = sorted(
        active_or_unknown, key=_canonical_quality, reverse=True)
    inactive_fallback = sorted(
        [row for row in family_rows if row not in active_or_unknown],
        key=_canonical_quality,
        reverse=True,
    )
    sources: dict[str, dict] = {}
    for field in _FAMILY_CARRY_FIELDS:
        candidate_pool = (
            family_rows if field == "description"
            else newest_first + inactive_fallback)
        candidates = [
            row for row in candidate_pool
            if row.get(field) not in (None, "", [], {})
        ]
        if not candidates:
            continue
        if field == "description":
            source = max(candidates, key=lambda row: (
                len(str(row.get("description") or "")),
                str(row.get("posted_date") or row.get("posted") or ""),
                str(row.get("record_id") or ""),
            ))
        else:
            source = candidates[0]
        merged[field] = source.get(field)
        sources[field] = _family_field_source(source)
    merged["family_field_sources"] = sources
    merged["canonical_scope_record_id"] = (
        (sources.get("description") or {}).get("record_id")
        or canonical.get("record_id")
    )
    return merged


def canonicalize_requirement_families(rows: list[dict]) -> list[dict]:
    """Return one deterministic canonical record per requirement family.

    Every survivor receipts all source members so later lifecycle postings can
    be reconciled without a database migration.
    """
    members: dict[str, list[dict]] = {}
    for row, group_token in zip(rows, _family_group_tokens(rows)):
        members.setdefault(group_token, []).append(row)
    kept: list[dict] = []
    for group_token in sorted(members):
        family_rows = members[group_token]
        canonical = max(family_rows, key=_canonical_quality)
        fam = _family_key(canonical)
        reconciled = _reconcile_family_fields(canonical, family_rows)
        member_ids = sorted({str(r.get("record_id") or "")
                             for r in family_rows if r.get("record_id")})
        lineage = sorted(({
            "record_id": row.get("record_id"),
            "solicitation_number": (
                _successor_solicitation_number(row)
                or _solicitation_number(row)),
            "posted_solicitation_number": _solicitation_number(row),
            "successor_solicitation_number":
                _successor_solicitation_number(row),
            "title": row.get("title"),
            "notice_type": row.get("notice_type"),
            "source_status": _notice_source_status(row),
            "posted_date": row.get("posted_date") or row.get("posted"),
            "response_deadline": row.get("response_deadline"),
            "set_aside": row.get("set_aside"),
            "source_url": row.get("url"),
            "agency": row.get("agency"),
            "office": row.get("office"),
            "identity_basis": _family_basis(row),
        } for row in family_rows), key=lambda row: (
            str(row.get("posted_date") or ""),
            _notice_stage_rank(row),
            str(row.get("record_id") or ""),
        ))
        kept.append(dict(
            reconciled,
            requirement_family=fam,
            canonical_record_id=canonical.get("record_id"),
            family_member_ids=member_ids,
            family_member_count=len(family_rows),
            family_basis=_family_basis(canonical),
            family_lineage=lineage,
        ))
    return kept


def dedupe_requirements(rows: list[dict]) -> list[dict]:
    """Compatibility wrapper for the canonical family resolver."""
    return canonicalize_requirement_families(rows)


_RECORD_IDENTITY_FIELDS = (
    "title", "description", "agency", "sub_agency", "recipient",
    "obligated_dollars", "ceiling_dollars", "period_start", "period_end",
    "potential_end_date", "url", "retrieved_at", "posted_date",
    "response_deadline", "solicitation_number", "notice_type",
    "base_notice_type", "set_aside", "set_aside_code", "type_set_aside",
    "contact_name", "contact_email", "contact_phone",
    "contact_secondary_email", "office", "active", "source_status",
    "source_fields",
)

_ACQUISITION_DECISION_FIELDS = (
    "title", "description", "agency", "sub_agency", "office",
    "posted_date", "response_deadline",
    "solicitation_number", "notice_type", "base_notice_type", "set_aside",
    "set_aside_code", "type_set_aside", "contact_name", "contact_email",
    "contact_phone", "contact_secondary_email", "naics", "naics_code",
    "psc", "psc_code", "url", "retrieved_at", "active", "source_status",
    "source_fields",
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

    def source_quality(row: dict) -> tuple:
        return (
            _notice_active_rank(row),
            str(row.get("retrieved_at") or ""),
            str(row.get("posted_date") or row.get("posted") or ""),
            {"research_mesh": 3, "deep_sweep": 2}.get(
                str(row.get("source_sweep") or ""), 1),
            quality(row),
        )

    kept: list[dict] = []
    duplicates: list[dict] = []
    for key in order:
        members = grouped[key]
        selected = dict(max(members, key=quality))
        field_sources: dict[str, dict] = {}
        if len(members) > 1:
            for field in _ACQUISITION_DECISION_FIELDS:
                candidates = [
                    row for row in members
                    if row.get(field) not in (None, "", [], {})
                ]
                if not candidates:
                    continue
                winner = max(candidates, key=source_quality)
                selected[field] = winner.get(field)
                field_sources[field] = {
                    "record_id": winner.get("record_id"),
                    "source_sweep": winner.get("source_sweep") or "pressed_pack",
                }
        if field_sources:
            selected["record_field_sources"] = field_sources
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
        purpose = ("pursuit_execution"
                   if opp.get("qualification_state") == "qualified"
                   else "decision_resolution")
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
                "target_purpose": purpose,
                "target_slot_id": "federal-opportunities",
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
                "target_purpose": purpose,
                "target_slot_id": "federal-opportunities",
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
                "target_purpose": purpose,
                "target_slot_id": "federal-opportunities",
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
                "target_purpose": "decision_resolution",
                "target_slot_id": "teaming-opportunities",
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
            role_name = str(role.get("role") or "")
            teaming_role = role_name in {
                "incumbent_or_likely_prime_capture_lead",
                "partner_capture_or_bd_lead",
            }
            rows.append({
                **common,
                "role": role.get("role"),
                "organization": role.get("organization"),
                "role_needed": role.get("role_needed"),
                "name": None, "email": None, "phone": None,
                "enrichment_candidate": True,
                "source_kind": "enrichment_candidate",
                "contact_state": "needs_enrichment",
                "target_purpose": (
                    "decision_resolution" if teaming_role else purpose),
                "target_slot_id": (
                    "teaming-opportunities" if teaming_role
                    else "federal-opportunities"),
                "provenance": "role derived from the current opportunity; "
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
            "target_purpose": contact.get("target_purpose") or (
                "pursuit_execution"
                if opp and opp.get("qualification_state") == "qualified"
                else "decision_resolution"),
            "target_slot_id": contact.get("target_slot_id") or
                              "federal-opportunities",
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
    current_by_family = {
        str(row.get("requirement_family")): row
        for row in records
        if row.get("evidence_class") == "current_opportunity"
        and row.get("requirement_family")
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

    current_ids = {
        str(row.get("record_id"))
        for row in records
        if row.get("evidence_class") == "current_opportunity"
        and row.get("record_id")
    }
    qualified_ids = {
        str(row.get("record_id"))
        for row in qualified if row.get("record_id")
    }
    held_ids = {
        str(row.get("record_id"))
        for row in (payload.get("held_opportunities") or [])
        if row.get("record_id")
    }
    if (qualified_ids & held_ids or
            current_ids != qualified_ids | held_ids):
        add(
            "G008_CURRENT_OPPORTUNITY_PARTITION",
            "current opportunities must partition exactly into qualified and held records",
            current_ids=sorted(current_ids),
            qualified_ids=sorted(qualified_ids),
            held_ids=sorted(held_ids),
            overlap=sorted(qualified_ids & held_ids),
            missing=sorted(current_ids - qualified_ids - held_ids),
            unexpected=sorted((qualified_ids | held_ids) - current_ids),
        )

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
        supported = current_by_family.get(str(family))
        if not supported:
            add("G005_TARGETS_REQUIRE_CURRENT_OPPORTUNITY",
                "target group has no surviving current opportunity",
                requirement_family=family,
                target_count=len(targets or []))
            continue
        expected_record_id = supported.get("record_id")
        for target in targets or []:
            if target.get("opportunity_record_id") != expected_record_id:
                add("G007_TARGET_LINEAGE_MISMATCH",
                    "target points to a record outside its current family",
                    requirement_family=family,
                    expected_record_id=expected_record_id,
                    target_record_id=target.get("opportunity_record_id"),
                    target_name=target.get("name"))
            target_slot = str(target.get("target_slot_id") or "")
            target_purpose = str(target.get("target_purpose") or "")
            if target_slot == "federal-opportunities":
                expected_purpose = (
                    "pursuit_execution" if str(family) in qualified_by_family
                    else "decision_resolution")
                if target_purpose != expected_purpose:
                    add("G009_TARGET_PURPOSE_MISMATCH",
                        "opportunity target purpose disagrees with its decision state",
                        requirement_family=family,
                        expected_purpose=expected_purpose,
                        target_purpose=target_purpose)
            elif target_slot == "teaming-opportunities":
                route = str(supported.get("commercial_route") or "")
                if route not in {"named_partner_teaming",
                                  "possible_subcontracting"}:
                    add("G010_TEAMING_TARGET_REQUIRES_ROUTE",
                        "teaming target has no evidenced teaming route",
                        requirement_family=family, commercial_route=route)
                if target_purpose != "decision_resolution":
                    add("G009_TARGET_PURPOSE_MISMATCH",
                        "teaming target must resolve an access decision",
                        requirement_family=family,
                        target_purpose=target_purpose)
            else:
                add("G011_TARGET_SLOT_REQUIRED",
                    "current-opportunity target has no typed owning slot",
                    requirement_family=family, target_slot_id=target_slot)

    return violations


def _eligibility(record: dict, ctx: dict) -> dict:
    set_aside = str(record.get("set_aside") or "").strip()
    if (record.get("commercial_route") in er.ROUTE_RELATIONSHIPS
            and isinstance(record.get("eligible_route"), bool)):
        route = {
            "commercial_route": record.get("commercial_route"),
            "eligible_route": record.get("eligible_route"),
            "route_basis": record.get("route_basis"),
        }
    else:
        route = {
            "commercial_route": "unknown",
            "eligible_route": False,
            "route_basis": "upstream route classification is missing",
        }
    return {
        "direct": route.get("commercial_route") == "direct" and
                  bool(route.get("eligible_route")),
        "eligible_route": bool(route.get("eligible_route")),
        "commercial_route": route.get("commercial_route"),
        "access_rule": set_aside or "none stated",
        "basis": route.get("route_basis"),
    }


def _technical_fit(record: dict, ctx: dict) -> dict:
    if record.get("service_fit") in er.SERVICE_FITS:
        fit = {
            "service_fit": record.get("service_fit"),
            "fit_basis": record.get("fit_basis"),
            "fit_evidence": list(record.get("fit_evidence") or []),
        }
    else:
        fit = {
            "service_fit": "ambiguous",
            "fit_basis": "upstream service-fit classification is missing",
            "fit_evidence": [],
        }
    return {"fit": fit["service_fit"] == "direct",
            "fit_class": fit["service_fit"],
            "basis": fit["fit_basis"],
            "evidence": list(fit.get("fit_evidence") or [])}


def _incumbent(record: dict) -> Optional[str]:
    return er.incumbent_name(record)


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
        route = {
            "commercial_route": row.get("commercial_route"),
            "route_relationship": row.get("route_relationship"),
            "eligible_route": row.get("eligible_route"),
            "route_basis": row.get("route_basis"),
        }
        incumbent = _incumbent(row)
        if incumbent:
            incumbents[row["requirement_family"]] = incumbent
            row["incumbent_name"] = incumbent
            row["incumbent_basis"] = "named in notice text"
        reason_codes: list[str] = []
        blocking_dimensions: list[str] = []
        reasons: list[str] = []

        if not fit["fit"]:
            fit_class = str(fit.get("fit_class") or "ambiguous")
            reason_codes.append({
                "adjacent": "FIT_ADJACENT_REVIEW",
                "ambiguous": "FIT_AMBIGUOUS",
                "unrelated": "FIT_OUT_OF_SCOPE",
            }.get(fit_class, "FIT_REVIEW_REQUIRED"))
            blocking_dimensions.append("service_fit")
            reasons.append(str(fit.get("basis") or
                               "service fit requires review"))

        if not eligibility["eligible_route"]:
            route_class = str(route.get("commercial_route") or "unknown")
            reason_codes.append(
                "DIRECT_ROUTE_INELIGIBLE"
                if route_class == "possible_subcontracting"
                else "ROUTE_ELIGIBILITY_UNRESOLVED")
            blocking_dimensions.append("route_eligibility")
            reasons.append(str(eligibility.get("basis") or
                               "eligible commercial route is unresolved"))

        if blocking_dimensions:
            decision_action = (
                "Resolve service fit and a commercially eligible route before pursuit."
                if set(blocking_dimensions) == {"service_fit", "route_eligibility"}
                else "Confirm the matched requirement scope before pursuit."
                if blocking_dimensions == ["service_fit"]
                else "Confirm the direct or teaming access route before pursuit."
            )
            row["projection_decision"] = {
                "disposition": "needs_review",
                "reason_codes": list(dict.fromkeys(reason_codes)),
                "blocking_dimensions": list(dict.fromkeys(blocking_dimensions)),
                "reasons": list(dict.fromkeys(reasons)),
                "decision_action": decision_action,
            }
            row["qualification_state"] = (
                "held_for_fit_review"
                if "service_fit" in blocking_dimensions
                else "needs_eligible_route")
            row["qualification_reason"] = "; ".join(
                row["projection_decision"]["reasons"])
            held_rows.append(row)
            continue

        row["qualification_state"] = "qualified"
        row["projection_decision"] = {
            "disposition": "qualified",
            "reason_codes": [],
            "blocking_dimensions": [],
            "reasons": [],
            "decision_action": str(
                route.get("route_basis") or
                "Work the evidenced direct route before the published deadline"),
        }
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
            "fit_evidence": list(row.get("fit_evidence") or []),
            "route_basis": row.get("route_basis"),
            "window_basis": row.get("window_basis"),
            "matched_sentence": row.get("matched_sentence"),
            "match_route": row.get("match_route"),
            "relevance_method": row.get("relevance_method"),
            "canonical_entity": row.get("canonical_entity"),
            "canonical_record_id": row.get("canonical_record_id"),
            "family_member_ids": row.get("family_member_ids"),
            "family_lineage": row.get("family_lineage"),
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
            "projection_decision": row["projection_decision"],
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
    if record.get("source_sweep") in {"deep_sweep", "research_mesh"}:
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


def _public_sam_url(record_id: str, candidate: Any = None) -> str:
    value = str(candidate or "").strip()
    if value.startswith("https://sam.gov/opp/"):
        return value
    return f"https://sam.gov/opp/{record_id}/view"


def _research_mesh_records(path: Path) -> tuple[list[dict], dict]:
    """Convert captured search output into graph-ready notice/forecast rows."""
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    results = payload.get("results") or {}
    captured_at = str(payload.get("generated_at") or "")
    rows: list[dict] = []
    notice_count = 0
    forecast_count = 0

    for source in results.get("sam.gov") or []:
        raw = source.get("raw_payload") or {}
        record_id = str(source.get("source_id") or raw.get("notice_id") or "")
        if not record_id:
            continue
        contacts = list(source.get("contacts") or [])
        primary = next((row for row in contacts
                        if row.get("contact_type") == "primary"), {})
        secondary = next((row for row in contacts
                          if row.get("contact_type") == "secondary"), {})
        rows.append({
            "lane": "L1_notice",
            "record_id": record_id,
            "title": source.get("title") or raw.get("title"),
            "description": raw.get("description_snippet") or "",
            "agency": raw.get("agency") or source.get("agency"),
            "sub_agency": raw.get("subtier"),
            "office": raw.get("office"),
            "posted_date": raw.get("posted") or source.get("posted_date"),
            "response_deadline": (
                raw.get("deadline") or source.get("response_deadline")),
            "notice_type": raw.get("type"),
            "base_notice_type": raw.get("base_type"),
            "source_status": raw.get("active"),
            "set_aside": source.get("set_aside") or raw.get("set_aside"),
            "set_aside_code": raw.get("set_aside_code"),
            "naics": source.get("naics_code") or raw.get("naics"),
            "psc": source.get("psc_code") or raw.get("psc"),
            "solicitation_number": raw.get("solicitation"),
            "url": _public_sam_url(
                record_id, raw.get("url") or source.get("api_url")),
            "contact_name": raw.get("poc_name") or primary.get("name"),
            "contact_email": raw.get("poc_email") or primary.get("email"),
            "contact_phone": raw.get("poc_phone") or primary.get("phone"),
            "contact_secondary_email": (
                raw.get("poc_secondary_email") or secondary.get("email")),
            "relevance_matched": list(raw.get("matched") or []),
            "retrieved_at": captured_at,
            "source_fields": dict(raw),
            "source_sweep": "research_mesh",
            "match_route": "governed_research_mesh",
        })
        notice_count += 1

    forecasts = results.get("forecast_signals") or {}
    for source in forecasts.get("matched") or []:
        record_id = str(source.get("source_id") or "")
        if not record_id:
            continue
        source_fields = source.get("source_fields") or {}
        rows.append({
            **dict(source),
            "lane": "L4_forecast",
            "record_id": record_id,
            "sub_agency": source.get("sub_agency") or source.get("component"),
            "office": (source.get("office")
                       or source_fields.get("Contracting Office")),
            "naics": source.get("naics") or source.get("naics_code"),
            "psc": source.get("psc") or source.get("psc_code"),
            "retrieved_at": source.get("retrieved_at") or captured_at,
            "source_sweep": "research_mesh",
            "match_route": "governed_research_mesh",
        })
        forecast_count += 1

    return rows, {
        "artifact_name": path.name,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "generated_at": captured_at,
        "sam_notice_rows_imported": notice_count,
        "forecast_rows_imported": forecast_count,
        "selection_rule": (
            "captured SAM notice rows and matched agency forecast rows; "
            "classification remains downstream and independent"),
    }


def build_corrected_pack(slug: str, client_name: str, *,
                         classification_as_of: str | datetime,
                         captured_at: str | datetime,
                         root: Optional[Path] = None,
                         pressed_pack_path: Optional[Path] = None,
                         pack_dir: Optional[Path] = None,
                         deep_sweep_path: Optional[Path] = None,
                         research_sweep_path: Optional[Path] = None,
                         store_conn=None,
                         rendered_competitor_names: Optional[set] = None,
                         research_gaps: Optional[list] = None,
                         enrichments: Optional[list[dict]] = None,
                         target_roles: Optional[dict[str, list[dict]]] = None,
                         graph_adapter: Optional[
                             ExistingSystemsGraphAdapter] = None,
                         cache_dir: Optional[Path] = None,
                         ) -> tuple[Path, dict]:
    classification_clock = _aware_utc_iso(
        classification_as_of, "classification_as_of")
    capture_clock = _aware_utc_iso(captured_at, "captured_at")
    if datetime.fromisoformat(classification_clock.replace("Z", "+00:00")) > \
            datetime.fromisoformat(capture_clock.replace("Z", "+00:00")):
        raise ValueError("classification_as_of is later than captured_at")
    base = Path(root) if root else _ROOT
    pack_dir = (Path(pack_dir) if pack_dir is not None else
                base / "data" / "state" / "candidate_review_v1" / slug)
    pressed_pack_path = (Path(pressed_pack_path)
                         if pressed_pack_path is not None else
                         pack_dir / f"{slug}.golden_report.evidence_pack.json")
    pressed = json.loads(pressed_pack_path.read_text(encoding="utf-8"))
    _captured_input_clock(
        pressed.get("generated_at"),
        label="pressed evidence pack generated_at",
        classification_as_of=classification_clock,
    )
    ctx = er.build_context(
        client_name, slug, pack=pressed, root=base,
        as_of=classification_clock)
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
    research_mesh_rows: list[dict] = []
    research_mesh_meta: dict = {}
    if deep_sweep_path and Path(deep_sweep_path).exists():
        sweep = json.loads(Path(deep_sweep_path).read_text(encoding="utf-8"))
        sweep_meta = sweep.get("receipt") or {}
        _captured_input_clock(
            sweep_meta.get("at"),
            label="deep sweep receipt at",
            classification_as_of=classification_clock,
        )
        known = {str(r.get("record_id")) for r in records}
        for row in sweep.get("records") or []:
            if str(row.get("record_id")) not in known:
                sweep_rows.append(dict(row, source_sweep="deep_sweep"))
    if research_sweep_path and Path(research_sweep_path).exists():
        research_mesh_rows, research_mesh_meta = _research_mesh_records(
            Path(research_sweep_path))
        _captured_input_clock(
            research_mesh_meta.get("generated_at"),
            label="governed research mesh generated_at",
            classification_as_of=classification_clock,
        )
    every_raw = records + sweep_rows + research_mesh_rows
    every, record_identity_receipt = canonicalize_record_ids(every_raw)

    classified = []
    for record in every:
        if (record.get("obligated_dollars") not in (None, "")
                and not record.get("figure_type")):
            record["figure_type"] = "published obligations"
        elif (record.get("ceiling_dollars") not in (None, "")
              and not record.get("figure_type")):
            record["figure_type"] = "published ceiling"
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

    current_opportunity_rows = [
        row for row in final
        if row.get("evidence_class") == "current_opportunity"
    ]
    active_requirement_families = {
        str(row.get("requirement_family"))
        for row in current_opportunity_rows if row.get("requirement_family")
    }
    reusable_enrichments = adapter.merge_apollo_enrichments(
        enrichments,
        active_requirement_families=active_requirement_families,
    )
    target_groups = build_target_groups(
        current_opportunity_rows,
        incumbents=incumbents,
        enrichments=reusable_enrichments,
        target_roles=target_roles,
    )
    for opportunity in current_opportunity_rows:
        linked_targets = target_groups.get(
            opportunity.get("requirement_family"), [])
        opportunity["linked_targets"] = linked_targets
        if opportunity.get("qualified"):
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
        "captured_at": capture_clock,
        # Compatibility alias retained for existing graph consumers.
        "generated_at": capture_clock,
        "base_pack_generated_at": pressed.get("generated_at"),
        "canonical_entities": ctx.get("canonical_entities", {}),
        "canonical_requirement_families": [{
            "requirement_family": row.get("requirement_family"),
            "canonical_record_id": row.get("canonical_record_id"),
            "family_member_ids": list(row.get("family_member_ids") or []),
            "family_member_count": int(row.get("family_member_count") or 1),
            "family_basis": row.get("family_basis"),
            "family_lineage": list(row.get("family_lineage") or []),
            "canonical_scope_record_id": row.get(
                "canonical_scope_record_id"),
            "family_field_sources": dict(
                row.get("family_field_sources") or {}),
        } for row in final if row.get("requirement_family")],
        # Ship the same complete, JSON-stable semantic context whose hash is
        # recorded by the graph adapter. Omitting a classifier input would make
        # the receipt self-consistent but not provenance-complete.
        "classification_context": canonicalize_cache_value(ctx),
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
            "projection_decision": r.get("projection_decision") or {},
            "linked_targets": list(r.get("linked_targets") or []),
            "fit_basis": r.get("fit_basis"),
            "fit_evidence": list(r.get("fit_evidence") or []),
            "route_basis": r.get("route_basis"),
            "window_basis": r.get("window_basis"),
            "reason_codes": list(
                (r.get("projection_decision") or {}).get("reason_codes") or []),
            "blocking_dimensions": list(
                (r.get("projection_decision") or {}).get(
                    "blocking_dimensions") or []),
        } for r in held_opportunities],
        "target_groups": target_groups,
        "moved_records": moved,
        "before_after_by_class": before_after,
        "deep_sweep_receipt": sweep_meta,
        "research_mesh_receipt": research_mesh_meta,
        "research_gaps": list(research_gaps or []),
        "workflow_contract": workflow_contract_receipt(),
        "research_queue": research_queue,
        "incremental_cache_receipt": adapter.semantic_receipt(),
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
