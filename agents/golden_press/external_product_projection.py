"""Project the Federal Pursuit Graph into LILA's eight external slots.

This module is the sole graph-to-product boundary. It does not research,
classify, enrich, rank with a model, or render HTML. It consumes the certified
graph payload, the preserved evidence pack, and the existing deterministic
Market Map projection, then assigns every displayed record to one owning
slot. Slot 1 contains references to owned records, never duplicate evidence
rows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from agents.golden_press.external_product_contract import (
    CONTRACT_VERSION,
    OPERATOR_LOCKED_SLOT_SHA256,
    ExternalProductSlot,
    load_external_product_slots,
)
from agents.golden_press.release_snapshot import (
    canonical_slot_sha256,
    canonicalize_release_value,
)


EXTERNAL_PRODUCT_PROJECTION_VERSION = "lila-external-product.v9.2026-08-25"

_USASPENDING_NONE_AWARD = re.compile(
    r"^https?://(?:www\.)?usaspending\.gov/award/"
    r"((?:CONT_AWD|CONT_IDV)_[^/?#]+_-NONE-_-NONE-)/?(?:[?#].*)?$",
    re.I,
)


@dataclass(frozen=True)
class ProductSlot:
    number: int
    slot_id: str
    heading: str
    status: str
    summary: str
    metrics: tuple[dict, ...] = ()
    records: tuple[dict, ...] = ()
    visuals: tuple[dict, ...] = ()
    gaps: tuple[str, ...] = ()
    coverage: dict = field(default_factory=dict)
    review_records: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ExternalProductDocument:
    schema_version: str
    contract_version: str
    contract_sha256: str
    client_name: str
    slug: str
    as_of: str
    slots: tuple[ProductSlot, ...]
    graph_receipt: dict
    source_receipt: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufffd", "-").split())


def _serial(value: Any) -> Any:
    return canonicalize_release_value(value)


def _contract_slots(value: Any) -> tuple[ExternalProductSlot, ...]:
    if value is None:
        return load_external_product_slots()
    rows = []
    for raw in value:
        if isinstance(raw, dict):
            rows.append(ExternalProductSlot(
                number=int(raw.get("number")),
                slot_id=str(raw.get("slot_id") or ""),
                heading=" ".join(str(raw.get("heading") or "").split()),
            ))
        else:
            rows.append(ExternalProductSlot(
                number=int(getattr(raw, "number")),
                slot_id=str(getattr(raw, "slot_id")),
                heading=" ".join(str(getattr(raw, "heading")).split()),
            ))
    return tuple(rows)


def _http_url(value: Any) -> str:
    url = _text(value)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    unresolved = _USASPENDING_NONE_AWARD.match(url)
    if unresolved:
        return ("https://api.usaspending.gov/api/v2/awards/"
                f"{unresolved.group(1)}/")
    return url


def _record_key(kind: Any, identity: Any) -> str:
    clean_kind = _text(kind).casefold().replace(" ", "-") or "record"
    clean_identity = _text(identity) or "unnumbered"
    return f"{clean_kind}:{clean_identity}"


_GRAPH_PRESENTATION_KINDS = frozenset({
    "notice", "forecast", "competitive-award", "category-award",
    "teaming-route", "event",
})

_EXTERNAL_TEAMING_RELATIONSHIPS = frozenset({
    "named_partner_teaming", "possible_subcontracting",
})


def record_ownership_key(row: dict) -> str:
    """Return presentation-independent identity for one external record.

    A graph record may be displayed with different presentation kinds while
    retaining one underlying identity.  Synthetic route, query, lane, and
    pack-event rows keep their own record keys unless their producer supplies
    an explicit ownership key.
    """
    if (row.get("non_owning_reference") is True
            and _text(row.get("kind")) == "teaming_route_reference"
            and _text(row.get("notice_reference_key"))
            and _text(row.get("notice_reference_slot_id")) ==
            "federal-opportunities"):
        return ""
    explicit = _text(row.get("ownership_key"))
    if explicit:
        return explicit
    source_id = _text(row.get("source_id"))
    if source_id and _text(row.get("kind")) in _GRAPH_PRESENTATION_KINDS:
        return _record_key("graph-record", source_id)
    return _text(row.get("record_key"))


def _evidence_rows(value: Any) -> list[dict]:
    rows: list[dict] = []
    for raw in value or ():
        source_id = _text(_get(raw, "source_id") or _get(raw, "record_id"))
        source_kind = _text(_get(raw, "source_kind") or _get(raw, "kind"))
        source_url = _http_url(
            _get(raw, "source_url") or _get(raw, "url"))
        row = {
            "source_id": source_id,
            "source_kind": source_kind,
            "source_url": source_url,
            "label": _text(_get(raw, "label")),
        }
        if any(row.values()):
            rows.append(row)
    keyed = {
        (row["source_kind"], row["source_id"], row["source_url"]): row
        for row in rows
    }
    return [keyed[key] for key in sorted(keyed)]


def _graph_index(graph: dict) -> dict[str, dict]:
    return {
        _text(row.get("record_id")): row
        for row in (graph.get("records") or [])
        if _text(row.get("record_id"))
    }


def _source_from_graph(row: dict) -> str:
    return _http_url(row.get("source_url") or row.get("url"))


_DETAIL_SECTIONS = (
    ("Full published scope", (
        ("description", "Scope"),
    )),
    ("Published financial evidence", (
        ("obligated_dollars", "Obligations"),
        ("ceiling_dollars", "Ceiling"),
        ("figure_type", "Figure type"),
    )),
    ("Acquisition", (
        ("sub_agency", "Sub-agency"),
        ("notice_type", "Instrument"),
        ("instrument", "Instrument"),
        ("solicitation_number", "Solicitation number"),
        ("naics", "NAICS"),
        ("psc", "PSC"),
        ("set_aside", "Set-aside"),
        ("posted_date", "Posted"),
        ("response_deadline", "Response deadline"),
        ("period_start", "Period start"),
        ("period_end", "Period end"),
        ("potential_end_date", "Potential end"),
        ("vehicle", "Vehicle"),
        ("vehicle_class", "Vehicle class"),
        ("parent_award_id", "Parent award"),
        ("incumbent_name", "Incumbent"),
    )),
    ("Forecast timing", (
        ("fiscal_year", "Fiscal year"),
        ("anticipated_solicitation", "Anticipated solicitation"),
        ("anticipated_solicitation_close", "Anticipated close"),
        ("anticipated_award", "Anticipated award"),
        ("small_business_poc", "Small-business POC"),
    )),
    ("Published contact", (
        ("contact_name", "Name"),
        ("contact_email", "Email"),
        ("contact_phone", "Phone"),
        ("contact_secondary_email", "Secondary email"),
    )),
    ("Why LILA kept it", (
        ("evidence_basis", "Evidence class basis"),
        ("fit_basis", "Fit basis"),
        ("fit_evidence", "Capability evidence"),
        ("route_basis", "Route basis"),
        ("window_basis", "Window basis"),
        ("matched_sentence", "Matched evidence"),
        ("match_route", "Retrieval route"),
        ("relevance_method", "Relevance method"),
        ("qualification_reason", "Qualification reason"),
    )),
    ("Lineage", (
        ("canonical_entity", "Canonical entity"),
        ("canonical_record_id", "Canonical record"),
        ("requirement_family", "Requirement family"),
        ("family_member_ids", "Family members"),
        ("family_lineage", "Notice lineage"),
        ("canonical_scope_record_id", "Scope source record"),
        ("family_field_sources", "Reconciled field sources"),
        ("predecessor_contract_id", "Predecessor contract"),
        ("retrieved_at", "Retrieved"),
    )),
)


def _detail_sections(row: dict) -> list[dict]:
    """Keep decision-useful research detail through the product boundary."""
    sections: list[dict] = []
    for heading, fields in _DETAIL_SECTIONS:
        values = []
        seen: set[tuple[str, str]] = set()
        for key, label in fields:
            value = row.get(key)
            if value in (None, "", [], {}):
                continue
            marker = (label, repr(value))
            if marker in seen:
                continue
            seen.add(marker)
            values.append({"key": key, "label": label, "value": _serial(value)})
        if values:
            sections.append({"heading": heading, "fields": values})
    provenance = row.get("relationship_provenance") or {}
    provenance_fields = []
    for dimension in sorted(provenance):
        proof = provenance.get(dimension) or {}
        if not isinstance(proof, dict):
            continue
        parts = [
            _text(proof.get("evidence")),
            (f"{_text(proof.get('confidence'))} confidence"
             if _text(proof.get("confidence")) else ""),
            (f"{_text(proof.get('kind'))} basis"
             if _text(proof.get("kind")) else ""),
        ]
        value = "; ".join(part for part in parts if part)
        if value:
            provenance_fields.append({
                "key": dimension,
                "label": dimension.replace("_", " ").title(),
                "value": value,
            })
    if provenance_fields:
        sections.append({
            "heading": "Decision provenance",
            "fields": provenance_fields,
        })
    return sections


def _record_receipts(row: dict) -> list[dict]:
    receipts = list(_evidence_rows(
        row.get("evidence_receipts") or row.get("evidence") or ()))
    source_url = _source_from_graph(row)
    source_id = _text(row.get("record_id") or row.get("notice_id"))
    if source_url and not any(
            receipt.get("source_id") == source_id
            and receipt.get("source_url") == source_url
            for receipt in receipts):
        receipts.append({
            "source_id": source_id,
            "source_kind": _text(row.get("source") or row.get("lane")
                                 or row.get("evidence_class")),
            "source_url": source_url,
            "label": _text(row.get("title")) or source_id,
        })
    return _evidence_rows(receipts)


def _excerpt(value: Any, limit: int = 520) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return clipped + "..."


def _graph_record(row: dict, *, kind: str, summary: str = "") -> dict:
    record_id = _text(row.get("record_id") or row.get("notice_id"))
    title = _text(row.get("title"))
    if kind in {"category-award", "competitive-award"} and (
            not title or title == record_id):
        recipient = _text(row.get("recipient"))
        agency = _text(row.get("agency"))
        title = " - ".join(part for part in (
            recipient or "Federal award", agency) if part)
    receipt_rows = list(_record_receipts(row))
    for receipt in row.get("fit_evidence") or []:
        receipt_rows.append({
            "source_id": receipt.get("source_id"),
            "source_kind": receipt.get("source_kind"),
            "source_url": receipt.get("source_url"),
            "label": (receipt.get("title")
                      or receipt.get("matched_capability")),
        })
    for member in row.get("family_lineage") or []:
        receipt_rows.append({
            "source_id": member.get("record_id"),
            "source_kind": "sam_notice_lineage",
            "source_url": member.get("source_url"),
            "label": member.get("title") or member.get("record_id"),
        })
    record = {
        "record_key": _record_key(kind, record_id),
        "ownership_key": _record_key("graph-record", record_id),
        "kind": kind,
        "source_id": record_id,
        "title": title or record_id,
        "summary": _excerpt(
            summary or row.get("summary") or row.get("description")),
        "source_url": _source_from_graph(row),
        "agency": _text(row.get("agency")),
        "sub_agency": _text(row.get("sub_agency")
                            or _get(row.get("buyer") or {}, "sub_agency")),
        "office": _text(row.get("office")),
        "recipient": _text(row.get("recipient")),
        "response_date": _text(
            row.get("response_due") or row.get("response_deadline")
            or row.get("anticipated_solicitation_close")
            or row.get("anticipated_solicitation")
            or row.get("anticipated_award")),
        "value": row.get("published_value") or row.get("obligated_dollars")
                 or row.get("ceiling_dollars") or row.get("estimated_value_range"),
        "figure_type": _text(row.get("figure_type")),
        "naics": _text(row.get("naics") or row.get("naics_code")),
        "psc": _text(row.get("psc") or row.get("psc_code")),
        "instrument": _text(row.get("instrument") or row.get("notice_type")),
        "set_aside": _text(row.get("set_aside") or row.get("access_rule")),
        "posted_date": _text(row.get("posted_date")),
        "solicitation_number": _text(row.get("solicitation_number")),
        "period_start": _text(row.get("period_start")),
        "period_end": _text(row.get("period_end")),
        "potential_end_date": _text(row.get("potential_end_date")),
        "vehicle": _text(row.get("vehicle")),
        "vehicle_class": _text(row.get("vehicle_class")),
        "parent_award_id": _text(row.get("parent_award_id")),
        "predecessor_contract_id": _text(
            row.get("predecessor_contract_id")),
        "incumbent_name": _text(row.get("incumbent_name")),
        "fiscal_year": _text(row.get("fiscal_year")),
        "anticipated_solicitation": _text(row.get("anticipated_solicitation")),
        "anticipated_solicitation_close": _text(
            row.get("anticipated_solicitation_close")),
        "anticipated_award": _text(row.get("anticipated_award")),
        "small_business_poc": _text(row.get("small_business_poc")),
        "contact_name": _text(row.get("contact_name")),
        "contact_email": _text(row.get("contact_email")),
        "contact_phone": _text(row.get("contact_phone")),
        "contact_secondary_email": _text(row.get("contact_secondary_email")),
        "evidence_class": _text(row.get("evidence_class")),
        "service_fit": _text(row.get("service_fit")),
        "fit_basis": _text(row.get("fit_basis")),
        "fit_evidence": _serial(row.get("fit_evidence") or []),
        "window_state": _text(row.get("window_state")),
        "window_basis": _text(row.get("window_basis")),
        "commercial_route": _text(row.get("commercial_route")),
        "eligible_route": row.get("eligible_route"),
        "route_action": _text(row.get("route_action")),
        "pursuit_state": _text(row.get("pursuit_state")),
        "route_basis": _text(row.get("route_basis")),
        "requirement_family": _text(row.get("requirement_family")),
        "canonical_record_id": _text(row.get("canonical_record_id")),
        "family_member_ids": _serial(row.get("family_member_ids") or []),
        "family_lineage": _serial(row.get("family_lineage") or []),
        "canonical_scope_record_id": _text(
            row.get("canonical_scope_record_id")),
        "family_field_sources": _serial(
            row.get("family_field_sources") or {}),
        "retrieved_at": _text(row.get("retrieved_at")),
        "relationship_provenance": _serial(
            row.get("relationship_provenance") or {}),
        "projection_decision": _serial(row.get("projection_decision") or {}),
        "detail_sections": _detail_sections(row),
        "evidence_receipts": _evidence_rows(receipt_rows),
    }
    return record


def _fit_review_code(value: Any) -> str:
    fit = _text(value).casefold()
    return {
        "ambiguous": "FIT_AMBIGUOUS",
        "adjacent": "FIT_ADJACENT_REVIEW",
        "unrelated": "FIT_OUT_OF_SCOPE",
    }.get(fit, "FIT_REVIEW_REQUIRED")


def _pursuit_route_action(row: dict) -> str:
    """Read the upstream route action, with a legacy-pack compatibility map."""
    stated = _text(row.get("route_action")).casefold()
    if stated in {"prime", "team", "verify"}:
        return stated
    route = _text(row.get("commercial_route")).casefold()
    if route in {"direct", "incumbent"} \
            and row.get("eligible_route") is True:
        return "prime"
    if route in {"named_partner_teaming", "possible_subcontracting"}:
        return "team"
    return "verify"


def _pursuit_action_text(row: dict, action: str) -> str:
    upstream = row.get("projection_decision") or {}
    stated = _text(upstream.get("decision_action"))
    if stated and upstream.get("disposition") == "qualified":
        return stated
    basis = _text(row.get("route_basis"))
    if action == "prime":
        return basis or "Work the evidenced direct route before the deadline."
    if action == "team":
        return (
            "Identify and engage an eligible prime for this teaming pursuit"
            + (f": {basis}" if basis else ".")
        )
    return (
        "Verify direct, vehicle, or teaming access with the buying office"
        + (f": {basis}" if basis else ".")
    )


def _valid_projection_decision(value: Any) -> bool:
    """Accept a complete upstream non-promotion decision without rewriting it."""
    if not isinstance(value, dict):
        return False
    disposition = value.get("disposition")
    if not isinstance(disposition, str) or not disposition.strip() \
            or disposition == "qualified":
        return False
    for key in ("reason_codes", "blocking_dimensions", "reasons"):
        items = value.get(key)
        if (not isinstance(items, (list, tuple)) or not items
                or any(not isinstance(item, str) or not item.strip()
                       for item in items)):
            return False
    action = value.get("decision_action")
    return isinstance(action, str) and bool(action.strip())


def _append_review_reason(
    codes: list[str], dimensions: list[str], reasons: list[str],
    *, code: str, dimension: str, reason: Any,
) -> None:
    if code not in codes:
        codes.append(code)
    if dimension not in dimensions:
        dimensions.append(dimension)
    text = _text(reason)
    if text and text not in reasons:
        reasons.append(text)


def _review_record(
    row: dict,
    *,
    kind: str,
    hold: Optional[dict] = None,
    opportunity: bool = False,
) -> dict:
    """Return one full evidence envelope with an explicit non-promotion decision."""
    record = _graph_record(row, kind=kind)
    hold = hold or {}
    upstream = row.get("projection_decision")
    if not _valid_projection_decision(upstream):
        upstream = hold.get("projection_decision")
    if _valid_projection_decision(upstream):
        decision = _serial(upstream)
        record.update({
            "projection_decision": decision,
            "decision_state": decision["disposition"],
            "qualification_state": _text(
                row.get("qualification_state")
                or hold.get("qualification_state")),
            "reason_codes": decision["reason_codes"],
            "blocking_dimensions": decision["blocking_dimensions"],
            "reasons": decision["reasons"],
            "decision_action": decision["decision_action"],
        })
        return record

    reason_codes: list[str] = []
    blocking_dimensions: list[str] = []
    reasons: list[str] = []
    fit = _text(row.get("service_fit")).casefold()

    if fit != "direct":
        _append_review_reason(
            reason_codes, blocking_dimensions, reasons,
            code=_fit_review_code(fit), dimension="service_fit",
            reason=(row.get("fit_basis") or hold.get("qualification_reason")
                    or f"service fit is {fit or 'unresolved'}"),
        )

    qualification_reason = hold.get("qualification_reason")
    if qualification_reason and _text(qualification_reason) not in reasons:
        reasons.append(_text(qualification_reason))
    if not reason_codes:
        _append_review_reason(
            reason_codes, blocking_dimensions, reasons,
            code="PROJECTION_HOLD_UNRESOLVED", dimension="qualification",
            reason=(qualification_reason or
                    "current evidence did not clear the projection boundary"),
        )

    out_of_scope = fit == "unrelated"
    if out_of_scope:
        decision_action = (
            "Retain as market context; do not include it in qualified category "
            "totals or pursuit recommendations."
        )
    else:
        decision_action = (
            "Resolve capability fit from sourced requirement evidence before "
            "any pursuit recommendation."
        )

    record.update({
        "decision_state": "out_of_scope" if out_of_scope else "needs_review",
        "qualification_state": _text(
            row.get("qualification_state") or hold.get("qualification_state")),
        "reason_codes": reason_codes,
        "blocking_dimensions": blocking_dimensions,
        "reasons": reasons,
        "decision_action": decision_action,
    })
    record["projection_decision"] = {
        "disposition": record["decision_state"],
        "reason_codes": _serial(reason_codes),
        "blocking_dimensions": _serial(blocking_dimensions),
        "reasons": _serial(reasons),
        "decision_action": decision_action,
    }
    return record


def _canonical_graph_rows(rows: list[dict]) -> list[dict]:
    """Use one stable row per source identity at the render boundary."""
    positions: dict[str, int] = {}
    canonical: list[dict] = []

    def quality(row: dict) -> tuple[int, float, int, str]:
        populated = sum(value not in (None, "", [], {}) for value in row.values())
        obligation = row.get("obligated_dollars")
        amount = (float(obligation)
                  if isinstance(obligation, (int, float))
                  and not isinstance(obligation, bool) else float("-inf"))
        return (
            1 if amount != float("-inf") else 0,
            amount,
            populated,
            repr(sorted(row.items())),
        )

    for row in rows:
        record_id = _text(row.get("record_id"))
        key = record_id or f"anonymous:{len(canonical) + 1}"
        if key not in positions:
            positions[key] = len(canonical)
            canonical.append(row)
            continue
        index = positions[key]
        if quality(row) > quality(canonical[index]):
            canonical[index] = row
    return canonical


def _external_teaming_record(row: dict) -> bool:
    """Return whether certified graph evidence may support a Slot 6 action."""
    return (
        row.get("route_relationship") in _EXTERNAL_TEAMING_RELATIONSHIPS
        and row.get("evidence_class") not in {
            "ambiguous", "excluded", "client_historical",
            "competitive_historical", "current_opportunity",
        }
        and row.get("service_fit") == "direct"
        and row.get("eligible_route") is True
    )


def _profile_terms(profile: dict, company: Any) -> tuple[list[str], list[str], list[str]]:
    capability = profile.get("capability_terms") or {}
    approved = []
    for key in ("core", "adjacent", "mission", "structural"):
        approved.extend(capability.get(key) or [])
    approved.extend(_get(company, "researched_keywords", ()) or ())
    pending = list(_get(company, "pending_keywords", ()) or ())
    rejected = list(_get(company, "rejected_keywords", ()) or ())
    return (
        sorted({_text(term) for term in approved if _text(term)}, key=str.casefold),
        sorted({_text(term) for term in pending if _text(term)}, key=str.casefold),
        sorted({_text(term) for term in rejected if _text(term)}, key=str.casefold),
    )


_LANE_LABELS = {
    "L1_notice": "Opportunity notice search",
    "L2_entity_award": "Award and spending search",
    "L3_vehicle": "Contract vehicle search",
    "L4_forecast": "Forecast and acquisition-plan search",
    "L5_event": "Industry day and event search",
}


def _query_rows(pack: Any) -> list[dict]:
    rows: list[dict] = []
    for index, query in enumerate(_get(pack, "queries", ()) or (), start=1):
        lane = _text(_get(query, "lane")) or f"lane-{index}"
        method = _text(_get(query, "method"))
        endpoint = _http_url(_get(query, "endpoint"))
        body = _serial(_get(query, "body", {}) or {})
        executed_at = _text(_get(query, "executed_at"))
        rows.append({
            "record_key": _record_key("research-query", f"{lane}:{index}"),
            "kind": "research_query",
            "source_id": f"{lane}:{index}",
            "title": (
                f"{_LANE_LABELS.get(lane, lane.replace('_', ' '))} - "
                f"{method.replace(':', ' ') if method else 'declared query'}"
            ),
            "summary": _text(_get(query, "note")),
            "source_url": endpoint,
            "lane": lane,
            "method": method,
            "executed_at": executed_at,
            "result_count": int(_get(query, "result_count", 0) or 0),
            "kept_after_screen": int(
                _get(query, "kept_after_screen", 0) or 0),
            "body": body,
            "detail_sections": ({
                "heading": "Replayable query receipt",
                "fields": tuple(
                    field for field in (
                        {"key": "executed_at", "label": "Executed",
                         "value": executed_at},
                        {"key": "endpoint", "label": "Endpoint",
                         "value": endpoint},
                        {"key": "body", "label": "Request body",
                         "value": body},
                    ) if field["value"] not in (None, "", [], {})
                ),
            },),
        })
    return rows


def _lane_rows(pack: Any) -> list[dict]:
    rows = []
    for index, lane in enumerate(_get(pack, "lanes", ()) or (), start=1):
        identity = _text(_get(lane, "lane")) or f"lane-{index}"
        rows.append({
            "record_key": _record_key("source-lane", identity),
            "kind": "source_lane",
            "source_id": identity,
            "title": identity,
            "summary": _text(_get(lane, "detail")),
            "source_url": "",
            "status": _text(_get(lane, "status")),
        })
    return rows


def _slot_status(records: Iterable[dict], metrics: Iterable[dict]) -> str:
    return "populated" if tuple(records) or tuple(metrics) else "gap"


def _metric(label: str, value: Any, note: str = "", evidence: Any = (),
            formula: str = "") -> dict:
    return {
        "label": label,
        "value": value,
        "note": _text(note),
        "evidence": _serial(evidence or []),
        "formula": _text(formula),
    }


def _obligation_metric(label: str, rows: list[dict]) -> Optional[dict]:
    components: list[Decimal] = []
    evidence: list[dict] = []
    for row in rows:
        value = row.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        components.append(Decimal(str(value)))
        evidence.extend(row.get("evidence_receipts") or [])
    if not components:
        return None
    total = sum(components, Decimal("0"))
    display = f"${total:,.2f}"
    formula = " + ".join(f"${value:,.2f}" for value in components)
    formula += f" = {display}"
    return _metric(
        label, display,
        f"Sum of {len(components)} canonical, directly fitted award record(s).",
        evidence, formula,
    )


def _review_counts(rows: Iterable[dict]) -> dict[str, int]:
    counts = {"needs_review": 0, "out_of_scope": 0}
    for row in rows:
        state = _text(row.get("decision_state"))
        counts[state] = counts.get(state, 0) + 1
    return counts


def _target_counts(rows: Iterable[dict]) -> dict[str, int]:
    targets = [target for row in rows for target in (row.get("targets") or [])]
    return {
        "records": len(targets),
        "named_people": sum(bool(_text(target.get("name"))) for target in targets),
        "sourced_channels": sum(
            bool(_text(target.get("email") or target.get("phone")))
            for target in targets),
        "enrichment_roles": sum(
            _text(target.get("contact_state")) == "needs_enrichment"
            or bool(target.get("enrichment_candidate"))
            for target in targets),
    }


def _date_order(value: Any) -> tuple[int, str]:
    text = _text(value)
    if not text:
        return (1, "")
    normalized = text.replace("Z", "+00:00")
    try:
        return (0, datetime.fromisoformat(normalized).date().isoformat())
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y", "%Y/%m/%d", "%Y"):
        try:
            return (0, datetime.strptime(text, pattern).date().isoformat())
        except ValueError:
            continue
    return (1, text.casefold())


def _action_order(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            _date_order(row.get("response_date")),
            _text(row.get("title")).casefold(),
            _text(row.get("source_id")),
        ),
    )


def _priority_records(
    opportunities: list[dict], opportunity_reviews: list[dict],
    forecasts: list[dict],
) -> list[dict]:
    qualified_source = _action_order(opportunities)[:7]
    forecast_source = [] if qualified_source else _action_order(forecasts)[:5]
    decision_source = _action_order(opportunity_reviews)[:7]
    rows: list[dict] = []

    def append_reference(
        row: dict, *, reference_kind: str,
        action_order: Optional[int] = None,
        decision_order: Optional[int] = None,
    ) -> None:
        target_count = len(row.get("targets") or [])
        decision_state = _text(row.get("decision_state")) or "qualified"
        decision_required = reference_kind == "decision_required"
        forecast_fallback = reference_kind == "forecast_action"
        rows.append({
            "reference_key": record_ownership_key(row),
            "reference_slot_id": (
                "future-forecasts" if forecast_fallback
                else "federal-opportunities"),
            "reference_kind": reference_kind,
            "decision_state": decision_state,
            "action_order": action_order,
            "decision_order": decision_order,
            "ordering_basis": (
                "direct-fit pursuits first; earliest published timing within "
                "each action or decision group"
            ),
            "title": row.get("title"),
            "agency": _text(row.get("agency")),
            "sub_agency": _text(row.get("sub_agency")),
            "why": _text(
                "; ".join(row.get("reasons") or []) if decision_required
                else row.get("priority_basis") or row.get("service_fit")
                or row.get("summary")),
            "route": _text(row.get("commercial_route")),
            "route_action": _text(row.get("route_action")),
            "timing": _text(row.get("response_date") or row.get("window_state")),
            "next_action": _text(
                row.get("decision_action") if decision_required
                else row.get("next_action")),
            "target_count": target_count,
        })

    for index, row in enumerate(qualified_source, start=1):
        append_reference(
            row, reference_kind="qualified_action", action_order=index)
    for index, row in enumerate(forecast_source, start=1):
        append_reference(
            row, reference_kind="forecast_action", action_order=index)
    for index, row in enumerate(decision_source, start=1):
        append_reference(
            row, reference_kind="decision_required", decision_order=index)
    return rows


def _numeric_magnitude(value: Any) -> tuple[Optional[float], str]:
    """Return a cited numeric floor without converting a range into certainty."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), "published numeric value"
    if isinstance(value, dict):
        amount = value.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            return float(amount), "published numeric value"
        value = value.get("display") or value.get("label") or ""
    text = _text(value)
    amounts = []
    for number, unit in re.findall(
            r"\$?\s*([0-9]+(?:\.[0-9]+)?)\s*([KMB])?", text, re.I):
        multiplier = {"K": 1_000, "M": 1_000_000,
                      "B": 1_000_000_000}.get(unit.upper(), 1)
        amounts.append(float(number) * multiplier)
    if not amounts:
        return None, ""
    return min(amounts), "published value floor"


def _direction_visuals(forecasts: list[dict], client_name: str) -> tuple[dict, ...]:
    if not forecasts:
        return ()
    points = []
    edges = []
    for index, row in enumerate(forecasts[:12], start=1):
        agency = _text(row.get("agency")) or "Unstated agency"
        numeric, basis = _numeric_magnitude(row.get("value"))
        if numeric is not None:
            points.append({
                "record_key": row["record_key"],
                "label": row.get("title"),
                "agency": agency,
                "timing": row.get("response_date") or row.get("window_state"),
                "magnitude": numeric,
                "magnitude_label": row.get("value"),
                "magnitude_basis": basis,
                "source_url": row.get("source_url"),
            })
        edges.extend((
            {"from": client_name, "to": agency, "relationship": "shapes"},
            {"from": agency, "to": row["record_key"],
             "relationship": "forecast signal"},
        ))
    visuals = []
    if points:
        visuals.append({
            "type": "directional_graph",
            "title": "Published forecast value floor by signal",
            "note": ("Bars use only cited numeric values. Ranges and 'over' "
                     "statements plot their published floor, not an estimate."),
            "points": points,
        })
    visuals.append({
        "type": "relationship_vector_map",
        "title": "Client-to-buyer forecast vectors", "edges": edges,
    })
    return tuple(visuals)


def build_external_product_document(
    *,
    market_map: Any,
    graph_payload: dict,
    evidence_pack: Any,
    profile: Optional[dict],
    client_name: str,
    slug: str,
    as_of: str,
    contract_slots: Any = None,
    contract_version: str = CONTRACT_VERSION,
    contract_sha256: str = OPERATOR_LOCKED_SLOT_SHA256,
) -> ExternalProductDocument:
    """Assign the certified graph and preserved research to eight slots."""

    profile = profile or {}
    contract_slots = _contract_slots(contract_slots)
    if canonical_slot_sha256(contract_slots) != contract_sha256:
        raise ValueError("external product slot rows do not match their digest")
    graph_rows_raw = list(graph_payload.get("records") or [])
    graph_rows = _canonical_graph_rows(graph_rows_raw)
    graph_index = _graph_index({"records": graph_rows})
    company = _get(market_map, "company_understanding")
    footprint = _get(market_map, "category_footprint")

    claimed: set[str] = set()
    claimed_references: set[str] = set()

    def claim(row: dict) -> bool:
        key = record_ownership_key(row)
        if not key:
            reference_key = _text(row.get("record_key"))
            if not reference_key or reference_key in claimed_references:
                return False
            claimed_references.add(reference_key)
            return True
        if key in claimed:
            return False
        claimed.add(key)
        return True

    # Slot 5: direct-fit current pursuits and their exact target groups.
    opportunity_rows: list[dict] = []
    opportunity_review_rows: list[dict] = []
    target_groups = graph_payload.get("target_groups") or {}
    qualified_opportunity_ids: set[str] = set()

    def pursuit_record(raw: dict) -> dict:
        row = _graph_record(raw, kind="notice")
        route_action = _pursuit_route_action(raw)
        decision_action = _pursuit_action_text(raw, route_action)
        row.update({
            "decision_state": "qualified",
            "route_action": route_action,
            "pursuit_state": f"pursue_{route_action}",
            "decision_action": decision_action,
            "reason_codes": [],
            "blocking_dimensions": [],
            "reasons": [],
        })
        family = _text(raw.get("requirement_family"))
        targets = [
            target for target in
            list(raw.get("linked_targets") or target_groups.get(family) or [])
            if target.get("target_slot_id") in {
                None, "", "federal-opportunities"}
        ]
        row.update({
            "requirement_family": family,
            "targets": _serial(targets),
            "next_action": decision_action,
            "priority_basis": _text(
                (raw.get("technical_fit") or {}).get("basis")
                or raw.get("fit_basis")),
        })
        return row

    for raw in graph_payload.get("qualified_opportunity_records") or []:
        row = pursuit_record(raw)
        if claim(row):
            opportunity_rows.append(row)
            qualified_opportunity_ids.add(_text(row.get("source_id")))

    held_by_id = {
        _text(row.get("record_id")): row
        for row in (graph_payload.get("held_opportunities") or [])
        if _text(row.get("record_id"))
    }
    for raw in graph_rows:
        record_id = _text(raw.get("record_id"))
        if (raw.get("evidence_class") != "current_opportunity"
                or record_id in qualified_opportunity_ids):
            continue
        row = _review_record(
            raw, kind="notice", hold=held_by_id.get(record_id),
            opportunity=True)
        row.update({
            "requirement_family": _text(raw.get("requirement_family")),
            "targets": _serial([
                target for target in list(
                    raw.get("linked_targets") or
                    target_groups.get(_text(raw.get("requirement_family"))) or [])
                if target.get("target_slot_id") in {
                    None, "", "federal-opportunities"}
            ]),
            "next_action": row.get("decision_action"),
        })
        if claim(row):
            opportunity_review_rows.append(row)

    # Slot 7: graph-classified forecasts, separate from live opportunities.
    forecast_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "forecast":
            continue
        row = _graph_record(raw, kind="forecast")
        row["next_action"] = (
            "Published solicitation timing has passed; locate the successor "
            "notice or refresh the forecast before acting."
            if raw.get("window_state") == "stated_past" else
            "Shape or monitor before a solicitation posts."
        )
        if claim(row):
            forecast_rows.append(row)

    # Slot 4 owns competitive award evidence.
    competitor_rows: list[dict] = []
    competitor_review_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "competitive_historical":
            continue
        if raw.get("service_fit") != "direct":
            row = _review_record(raw, kind="competitive-award")
            if claim(row):
                competitor_review_rows.append(row)
            continue
        row = _graph_record(raw, kind="competitive-award")
        if claim(row):
            competitor_rows.append(row)

    # Slot 6 owns partner/teaming evidence that has not already been assigned.
    # Market Map routes are presentation enrichments, not an alternate admission
    # path. Every one must resolve to certified, client-admissible graph evidence.
    teaming_rows: list[dict] = []
    for raw in graph_rows:
        if (raw.get("evidence_class") != "current_opportunity"
                or raw.get("commercial_route") not in
                _EXTERNAL_TEAMING_RELATIONSHIPS):
            continue
        source_id = _text(raw.get("record_id"))
        family = _text(raw.get("requirement_family"))
        decision = raw.get("projection_decision") or {}
        route = _text(raw.get("commercial_route"))
        row = {
            "record_key": _record_key(
                "teaming-route", f"{source_id}:{route}"),
            "non_owning_reference": True,
            "kind": "teaming_route_reference",
            "source_id": source_id,
            "title": f"Teaming path: {_text(raw.get('title'))}",
            "summary": _text(raw.get("route_basis")),
            "source_url": _source_from_graph(raw),
            "agency": _text(raw.get("agency")),
            "commercial_route": route,
            "route_basis": _text(raw.get("route_basis")),
            "decision_state": _text(decision.get("disposition")) or "needs_review",
            "decision_action": _text(decision.get("decision_action")),
            "notice_reference_key": _record_key("graph-record", source_id),
            "notice_reference_slot_id": "federal-opportunities",
            "requirement_family": family,
            "targets": _serial([
                target for target in list(target_groups.get(family) or [])
                if target.get("target_slot_id") == "teaming-opportunities"
            ]),
            "evidence_receipts": _record_receipts(raw),
        }
        if claim(row):
            teaming_rows.append(row)
    for index, route in enumerate(_get(market_map, "teaming_routes", ()) or (), start=1):
        evidence = _evidence_rows(_get(route, "evidence", ()))
        qualified_evidence = [
            receipt for receipt in evidence
            if _external_teaming_record(graph_index.get(receipt["source_id"], {}))
        ]
        if not qualified_evidence:
            continue
        source_id = qualified_evidence[0]["source_id"]
        row = {
            "record_key": _record_key("teaming-route", source_id),
            "ownership_key": _record_key("graph-record", source_id),
            "kind": "teaming_route",
            "source_id": source_id,
            "title": _text(_get(route, "organisation")) or "Teaming route",
            "summary": _text(_get(route, "why")),
            "source_url": next((r["source_url"] for r in qualified_evidence
                                if r["source_url"]), ""),
            "agency": _text(_get(route, "agency")),
            "role": _text(_get(route, "role")),
            "target_role": _text(_get(route, "target_role")),
            "person": _text(_get(route, "person")),
            "next_action": _text(_get(route, "action")),
            "evidence": qualified_evidence,
        }
        if claim(row):
            teaming_rows.append(row)
    for raw in graph_rows:
        if not _external_teaming_record(raw):
            continue
        row = _graph_record(raw, kind="teaming-route")
        if claim(row):
            teaming_rows.append(row)

    # Slot 3 owns client/category spending records not assigned elsewhere.
    spending_rows: list[dict] = []
    spending_review_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "client_historical":
            continue
        if raw.get("service_fit") != "direct":
            row = _review_record(raw, kind="category-award")
            if claim(row):
                spending_review_rows.append(row)
            continue
        row = _graph_record(raw, kind="category-award")
        if claim(row):
            spending_rows.append(row)

    spending_review_counts = _review_counts(spending_review_rows)
    competitor_review_counts = _review_counts(competitor_review_rows)
    spending_total = _obligation_metric(
        "Qualified category obligations", spending_rows)
    spending_metrics = ([spending_total] if spending_total else []) + [
        _metric("Qualified category awards", len(spending_rows),
                "Canonical client awards with direct capability-fit evidence."),
        _metric("Review-required awards",
                spending_review_counts.get("needs_review", 0),
                "Open adjudication records excluded from qualified totals."),
        _metric("Out-of-scope records",
                spending_review_counts.get("out_of_scope", 0),
                "Terminal context retained outside qualified totals."),
    ]

    competitor_total = _obligation_metric(
        "Qualified competitor obligations", competitor_rows)
    competitor_metrics = [competitor_total] if competitor_total else []

    approved, pending, rejected = _profile_terms(profile, company)
    naics = sorted({_text(code) for code in (
        _get(company, "naics", ()) or profile.get("inferred_naics") or [])
        if _text(code)})
    query_rows = _query_rows(evidence_pack)
    lane_rows = _lane_rows(evidence_pack)
    mesh_records = query_rows + lane_rows
    graph_cache = graph_payload.get("incremental_cache_receipt") or {}
    semantic_evidenced = bool(any(
        any(token in _text(row.get("method")).casefold()
            for token in ("semantic", "dense", "embedding", "vector"))
        for row in query_rows))
    mesh_metrics = [
        _metric("Approved search terms", len(approved),
                "Client and buyer language admitted to retrieval."),
        _metric("NAICS boundary", len(naics), ", ".join(naics) or "Not stated"),
        _metric("Recorded queries", len(query_rows),
                "Exact query bodies remain in the evidence pack."),
        _metric("Semantic retrieval receipt",
                "present" if semantic_evidenced else "not captured",
                "The slot states the receipt boundary rather than implying a run."),
    ]
    mesh_gaps = []
    if not semantic_evidenced:
        mesh_gaps.append(
            "Semantic retrieval is part of the governed research system, but this "
            "edition carries no run-bound dense/vector receipt; capture it on the next sweep.")
    if pending:
        mesh_gaps.append(
            f"{len(pending)} proposed term(s) await operator approval before search.")

    # Slot 8 owns events from graph rows and the pack event lane.
    event_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "event":
            continue
        row = _graph_record(raw, kind="event")
        if claim(row):
            event_rows.append(row)
    for index, event in enumerate(_get(evidence_pack, "events", ()) or (), start=1):
        identity = _text(_get(event, "record_id") or _get(event, "id")
                         or _get(event, "url") or f"event-{index}")
        row = {
            "record_key": _record_key("event", identity),
            "kind": "event",
            "source_id": identity,
            "title": _text(_get(event, "name") or _get(event, "title")),
            "summary": _text(_get(event, "fit") or _get(event, "why")),
            "source_url": _http_url(_get(event, "url") or _get(event, "source_url")),
            "date": _text(_get(event, "date") or _get(event, "event_start")),
            "location": _text(_get(event, "location")),
            "agency": _text(_get(event, "agency") or _get(event, "host")),
            "next_action": _text(_get(event, "action")),
        }
        if claim(row):
            event_rows.append(row)

    opportunity_rows = _action_order(opportunity_rows)
    opportunity_review_rows = _action_order(opportunity_review_rows)
    forecast_rows = _action_order(forecast_rows)
    priorities = _priority_records(
        opportunity_rows, opportunity_review_rows, forecast_rows)
    target_counts = _target_counts(opportunity_rows)
    pursuit_priority_count = sum(
        row.get("reference_kind") in {"qualified_action", "forecast_action"}
        for row in priorities)
    prime_pursuits = sum(
        row.get("route_action") == "prime" for row in opportunity_rows)
    teaming_pursuits = sum(
        row.get("route_action") == "team" for row in opportunity_rows)
    verify_pursuits = sum(
        row.get("route_action") == "verify" for row in opportunity_rows)
    priority_metrics = [
        _metric("Pursuit actions", pursuit_priority_count,
                "Pursuit or forecast actions ordered before adjudication work."),
        _metric("Fit decisions required", len(opportunity_review_rows),
                "Current leads retained below with unresolved service fit."),
        _metric("Target records", target_counts["records"],
                (f"{target_counts['named_people']} named people; "
                 f"{target_counts['sourced_channels']} sourced contact channels; "
                 f"{target_counts['enrichment_roles']} roles still need enrichment.")),
    ] if priorities else []

    slots_by_id = {
        "priority-pursuits": ProductSlot(
            1, "priority-pursuits", contract_slots[0].heading,
            _slot_status(priorities, priority_metrics),
            "The direct, teaming, and route-verification pursuits that deserve the next unit of seller attention.",
            tuple(priority_metrics), tuple(priorities), (),
            (() if priorities else (
                "No direct-fit pursuit is current; resolve the displayed fit "
                "and coverage gaps before promotion.",)),
            {"ordered_from": (
                 "direct-fit pursuits (or forecast fallback), then a separate "
                 "fit-decision queue"),
             "ordering_basis": (
                 "direct-fit pursuits first; earliest published timing within "
                 "each action or decision group")}),
        "research-mesh": ProductSlot(
            2, "research-mesh", contract_slots[1].heading,
            "populated", "The approved vocabulary, boundaries, query lanes, and receipts used to find this market.",
            tuple(mesh_metrics), tuple(mesh_records), (), tuple(mesh_gaps),
            {"approved_terms": approved, "pending_terms": pending,
             "rejected_terms": rejected, "naics": naics,
             "semantic_receipt_present": semantic_evidenced}),
        "agency-spending": ProductSlot(
            3, "agency-spending", contract_slots[2].heading,
            _slot_status(spending_rows + spending_review_rows, spending_metrics),
            "Category-specific historical obligations, never mislabeled as pipeline or market size.",
            tuple(spending_metrics), tuple(spending_rows), (),
            (() if spending_rows or spending_metrics else (
                "No category spending record is qualified in this edition; re-run the award lane under the approved frame.",)),
            {"qualified_awards": len(spending_rows),
             "review_required": spending_review_counts.get("needs_review", 0),
             "out_of_scope": spending_review_counts.get("out_of_scope", 0),
             "classified_client_awards": (
                 len(spending_rows) + len(spending_review_rows)),
             "period": _text(_get(footprint, "period"))},
            review_records=tuple(spending_review_rows)),
        "competitors": ProductSlot(
            4, "competitors", contract_slots[3].heading,
            _slot_status(
                competitor_rows + competitor_review_rows,
                competitor_metrics),
            "Named product competitors and the federal award evidence that establishes their position.",
            tuple(competitor_metrics + [
                _metric("Qualified competitive awards", len(competitor_rows),
                        "Canonical directly fitted awards tied to approved competitors."),
                _metric("Review-required awards",
                        competitor_review_counts.get("needs_review", 0),
                        "Open adjudication records excluded from qualified totals."),
                _metric("Out-of-scope records",
                        competitor_review_counts.get("out_of_scope", 0),
                        "Terminal context retained outside qualified totals."),
            ]),
            tuple(competitor_rows), (),
            (() if competitor_rows else (
                "No identified competitor has a qualified award record in the current evidence pack; review records below remain outside qualified totals.",)),
            {"qualified_awards": len(competitor_rows),
             "review_required": competitor_review_counts.get("needs_review", 0),
             "out_of_scope": competitor_review_counts.get("out_of_scope", 0),
             "classified_competitor_awards": (
                 len(competitor_rows) + len(competitor_review_rows))},
            review_records=tuple(competitor_review_rows)),
        "federal-opportunities": ProductSlot(
            5, "federal-opportunities", contract_slots[4].heading,
            _slot_status(opportunity_rows + opportunity_review_rows, ()),
            "Direct-fit current pursuits with prime, teaming, or route-verification actions and opportunity-specific targets.",
            (
                _metric("Direct-fit pursuits", len(opportunity_rows),
                        "Cleared evidence, fit, and live timing; route remains an independent action."),
                _metric("Prime path", prime_pursuits,
                        "A direct or incumbent-displacement response path is evidenced."),
                _metric("Teaming path", teaming_pursuits,
                        "The fit survives with an eligible-prime action."),
                _metric("Verify route", verify_pursuits,
                        "The fit survives while the buying office or vehicle path is verified."),
                _metric("Fit review", len(opportunity_review_rows),
                        "Preserved as research-required leads, outside pursuit totals."),
            ), tuple(opportunity_rows), (),
            (() if opportunity_rows else (
                "No current opportunity cleared evidence, direct fit, and live timing; research-required records remain visible below.",)),
            {"direct_fit_pursuits": len(opportunity_rows),
             "prime": prime_pursuits,
             "team": teaming_pursuits,
             "verify": verify_pursuits,
             "fit_review": len(opportunity_review_rows),
             "qualified": len(opportunity_rows),
             "held": len(opportunity_review_rows),
             "current_opportunity_records": (
                 len(opportunity_rows) + len(opportunity_review_rows))},
            review_records=tuple(opportunity_review_rows)),
        "teaming-opportunities": ProductSlot(
            6, "teaming-opportunities", contract_slots[5].heading,
            _slot_status(teaming_rows, ()),
            "Evidence-backed partner routes and the roles needed to work them.",
            (), tuple(teaming_rows), (),
            (() if teaming_rows else (
                "No teaming route cleared the relationship-evidence boundary; research likely primes and vehicle holders for the surviving opportunities.",)),
            {"routes": len(teaming_rows)}),
        "future-forecasts": ProductSlot(
            7, "future-forecasts", contract_slots[6].heading,
            _slot_status(forecast_rows, ()),
            "Forward demand signals kept distinct from posted opportunities and historical awards.",
            (), tuple(forecast_rows), _direction_visuals(forecast_rows, client_name),
            (() if forecast_rows else (
                "No forecast record sits inside the approved frame in this edition; re-pull agency forecasts, budget evidence, and federal news under the locked vocabulary.",)),
            {"forecast_records": len(forecast_rows)}),
        "industry-days-events": ProductSlot(
            8, "industry-days-events", contract_slots[7].heading,
            _slot_status(event_rows, ()),
            "Official events tied to a buyer, pursuit, or evidenced market route.",
            (), tuple(event_rows), (),
            (() if event_rows else (
                "No verified industry day or event is tied to the current pursuits; re-check official agendas and registration pages before the next release.",)),
            _serial(_get(evidence_pack, "events_screen", {}) or {})),
    }

    slots = tuple(slots_by_id[slot.slot_id] for slot in contract_slots)
    current_opportunity_ids = sorted({
        _text(row.get("record_id")) for row in graph_rows
        if row.get("evidence_class") == "current_opportunity"
        and _text(row.get("record_id"))
    })
    projected_current_opportunity_ids = sorted({
        _text(row.get("source_id"))
        for row in opportunity_rows + opportunity_review_rows
        if _text(row.get("source_id"))
    })
    orphan_held_ids = sorted(set(held_by_id) - set(current_opportunity_ids))
    graph_qualified_ids = sorted({
        _text(row.get("record_id"))
        for row in (graph_payload.get("qualified_opportunity_records") or [])
        if _text(row.get("record_id"))
    })
    projected_pursuit_ids = sorted({
        _text(row.get("source_id")) for row in opportunity_rows
        if _text(row.get("source_id"))
    })
    graph_receipt = {
        "schema_version": graph_payload.get("schema_version"),
        "certified": bool(graph_payload.get("graph_contract_certified")),
        "violations": _serial(graph_payload.get("graph_contract_violations") or []),
        "workflow_contract": _serial(graph_payload.get("workflow_contract") or {}),
        "incremental_cache_receipt": _serial(graph_cache),
        "owned_record_keys": sorted(claimed),
        "owned_record_count": len(claimed),
        "current_opportunity_ids": current_opportunity_ids,
        "projected_current_opportunity_ids": projected_current_opportunity_ids,
        "current_opportunity_conserved": (
            current_opportunity_ids == projected_current_opportunity_ids),
        "graph_qualified_opportunity_ids": graph_qualified_ids,
        "projected_pursuit_ids": projected_pursuit_ids,
        "pursuit_membership_conserved": (
            graph_qualified_ids == projected_pursuit_ids),
        "orphan_held_opportunity_ids": orphan_held_ids,
    }
    identity_receipt = graph_payload.get("record_identity_receipt") or {}
    graph_counts = graph_payload.get("counts") or {}
    source_receipt = {
        "evidence_pack_generated_at": _text(_get(evidence_pack, "generated_at")),
        "graph_generated_at": _text(graph_payload.get("generated_at")),
        "graph_captured_at": _text(graph_payload.get("captured_at")),
        "classification_as_of": _text(
            (graph_payload.get("classification_context") or {}).get("as_of")),
        "evidence_records_raw": len(_get(evidence_pack, "records", ()) or ()),
        "evidence_records": len({
            _text(_get(row, "record_id")) or f"anonymous:{index}"
            for index, row in enumerate(_get(evidence_pack, "records", ()) or ())
        }),
        "query_count": len(query_rows),
        "lane_count": len(lane_rows),
        "graph_input_records_raw": int(
            graph_counts.get("input_records_raw")
            or identity_receipt.get("input_records_raw")
            or len(graph_rows_raw)),
        "graph_identity_canonical_records": int(
            graph_counts.get("canonical_records")
            or identity_receipt.get("canonical_records")
            or len(graph_rows_raw)),
        "graph_requirement_duplicates_collapsed": int(
            graph_counts.get("requirement_duplicates_collapsed") or 0),
        "graph_final_records": len(graph_rows_raw),
        "graph_projection_canonical_records": len(graph_rows),
        "graph_indexed_records": len(graph_index),
        "record_identity_receipt": _serial(identity_receipt),
    }
    return ExternalProductDocument(
        schema_version=EXTERNAL_PRODUCT_PROJECTION_VERSION,
        contract_version=contract_version,
        contract_sha256=contract_sha256,
        client_name=_text(client_name), slug=_text(slug), as_of=_text(as_of),
        slots=slots, graph_receipt=graph_receipt,
        source_receipt=source_receipt,
    )


__all__ = (
    "EXTERNAL_PRODUCT_PROJECTION_VERSION",
    "ExternalProductDocument",
    "ProductSlot",
    "build_external_product_document",
    "record_ownership_key",
)
