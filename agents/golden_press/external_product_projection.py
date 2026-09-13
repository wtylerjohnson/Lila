"""Project the Federal Pursuit Graph into LILA's eight external slots.

This module is the sole graph-to-product boundary. It does not research,
classify, enrich, rank with a model, or render HTML. It consumes the certified
graph payload, the preserved evidence pack, and the existing deterministic
Market Map projection, then assigns every displayed record to one owning
slot. Slot 1 contains references to owned records, never duplicate evidence
rows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from agents.golden_press.external_product_contract import (
    CONTRACT_VERSION,
    load_external_product_slots,
)


EXTERNAL_PRODUCT_PROJECTION_VERSION = "lila-external-product.v1.2026-08-23"


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


@dataclass(frozen=True)
class ExternalProductDocument:
    schema_version: str
    contract_version: str
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
    return " ".join(str(value or "").split())


def _serial(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serial(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serial(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serial(item) for item in value]
    return value


def _http_url(value: Any) -> str:
    from agents.golden_press.market_map_render import _resolving_source_url
    url = _resolving_source_url(_text(value))
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _record_key(kind: Any, identity: Any) -> str:
    clean_kind = _text(kind).casefold().replace(" ", "-") or "record"
    clean_identity = _text(identity) or "unnumbered"
    return f"{clean_kind}:{clean_identity}"


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


def _money(value: Any) -> Optional[dict]:
    if value is None:
        return None
    return {
        "display": _text(_get(value, "display")),
        "amount": _get(value, "amount"),
        "basis": _text(_get(value, "basis")),
        "population": _text(_get(value, "population")),
        "evidence": _evidence_rows(_get(value, "evidence", ())),
    }


def _graph_index(graph: dict) -> dict[str, dict]:
    return {
        _text(row.get("record_id")): row
        for row in (graph.get("records") or [])
        if _text(row.get("record_id"))
    }


def _source_from_graph(row: dict) -> str:
    return _http_url(row.get("source_url") or row.get("url"))


def _graph_record(row: dict, *, kind: str, summary: str = "") -> dict:
    record_id = _text(row.get("record_id") or row.get("notice_id"))
    return {
        "record_key": _record_key(kind, record_id),
        "kind": kind,
        "source_id": record_id,
        "title": _text(row.get("title")) or record_id,
        "summary": _text(summary),
        "source_url": _source_from_graph(row),
        "agency": _text(row.get("agency")),
        "office": _text(row.get("office")),
        "recipient": _text(row.get("recipient")),
        "response_date": _text(
            row.get("response_due") or row.get("response_deadline")),
        "value": row.get("published_value") or row.get("obligated_dollars")
                 or row.get("ceiling_dollars") or row.get("estimated_value_range"),
        "evidence_class": _text(row.get("evidence_class")),
        "service_fit": _text(row.get("service_fit")),
        "window_state": _text(row.get("window_state")),
        "commercial_route": _text(row.get("commercial_route")),
        "relationship_provenance": _serial(
            row.get("relationship_provenance") or {}),
    }


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


def _query_rows(pack: Any) -> list[dict]:
    rows: list[dict] = []
    for index, query in enumerate(_get(pack, "queries", ()) or (), start=1):
        lane = _text(_get(query, "lane")) or f"lane-{index}"
        method = _text(_get(query, "method"))
        endpoint = _http_url(_get(query, "endpoint"))
        rows.append({
            "record_key": _record_key("research-query", f"{lane}:{index}"),
            "kind": "research_query",
            "source_id": f"{lane}:{index}",
            "title": f"{lane} · {method or 'declared query'}",
            "summary": _text(_get(query, "note")),
            "source_url": endpoint,
            "lane": lane,
            "method": method,
            "result_count": int(_get(query, "result_count", 0) or 0),
            "kept_after_screen": int(
                _get(query, "kept_after_screen", 0) or 0),
            "body": _serial(_get(query, "body", {}) or {}),
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


def _metric(label: str, value: Any, note: str = "", evidence: Any = ()) -> dict:
    return {
        "label": label,
        "value": value,
        "note": _text(note),
        "evidence": _serial(evidence or []),
    }


def _priority_records(opportunities: list[dict], forecasts: list[dict]) -> list[dict]:
    source = opportunities[:7]
    rows = []
    for index, row in enumerate(source, start=1):
        target_count = len(row.get("targets") or [])
        rows.append({
            "reference_key": row["record_key"],
            "reference_slot_id": (
                "federal-opportunities" if opportunities else "future-forecasts"),
            "priority": index,
            "title": row.get("title"),
            "why": _text(row.get("priority_basis") or row.get("service_fit")
                         or row.get("summary")),
            "route": _text(row.get("commercial_route")),
            "timing": _text(row.get("response_date") or row.get("window_state")),
            "next_action": _text(row.get("next_action")),
            "target_count": target_count,
        })
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
    leadgen: Optional[dict] = None,
    notice_context=None,
) -> ExternalProductDocument:
    """Assign the certified graph and preserved research to eight slots."""

    from agents.golden_press.notice_bridge import NoticeReadContext
    if not isinstance(notice_context, NoticeReadContext) or not notice_context.accepts_graph(graph_payload):
        saved = {str(r.get("record_id") or r.get("notice_id")): r
                 for r in graph_payload.get("qualified_opportunity_records") or []}
        diagnostic_rows = [dict(r, lane=r.get("lane") or "L1_notice") if str(r.get("record_id")) in saved else dict(r)
                           for r in graph_payload.get("records") or []]
        known = {str(r.get("record_id")) for r in diagnostic_rows}
        diagnostic_rows.extend(dict(r, lane="L1_notice") for sid, r in saved.items() if sid not in known)
        graph_payload = dict(graph_payload, records=diagnostic_rows,
            qualified_opportunities=[], qualified_opportunity_records=[])
    profile = profile or {}
    contract_slots = load_external_product_slots()
    graph_rows = list(graph_payload.get("records") or [])
    graph_index = _graph_index(graph_payload)
    company = _get(market_map, "company_understanding")
    footprint = _get(market_map, "category_footprint")
    competition = _get(market_map, "competitive_position")

    claimed: set[str] = set()

    def claim(row: dict) -> bool:
        key = row["record_key"]
        if key in claimed:
            return False
        claimed.add(key)
        return True

    # Slot 5: qualified current opportunities and their exact target groups.
    opportunity_rows: list[dict] = []
    target_groups = graph_payload.get("target_groups") or {}
    for raw in graph_payload.get("qualified_opportunity_records") or []:
        row = _graph_record(raw, kind="notice")
        family = _text(raw.get("requirement_family"))
        targets = list(raw.get("linked_targets") or target_groups.get(family) or [])
        row.update({
            "assessment_status": "Qualified opportunity",
            "requirement_family": family,
            "targets": _serial(targets),
            "next_action": _text((raw.get("next_route") or {}).get("route_basis")
                                 or raw.get("qualification_reason")),
            "priority_basis": _text((raw.get("technical_fit") or {}).get("basis")),
        })
        if claim(row):
            opportunity_rows.append(row)

    # Operator ruling 2026-09-08: assessment visibility precedes lead readiness.
    # Keep the qualified shortlist separate from the complete review population.
    qualified_rows = list(opportunity_rows)
    held = {str(r.get("record_id")): r
            for r in graph_payload.get("held_opportunities") or []}
    original = {str(_get(r, "record_id")): r
                for r in _get(evidence_pack, "records", ()) or ()}
    for raw in graph_rows:
        if raw.get("lane") != "L1_notice":
            continue
        row = _graph_record(raw, kind="notice")
        if not claim(row):
            continue
        source = original.get(str(raw.get("record_id")), {})
        disposition = held.get(str(raw.get("record_id")), {})
        fields = _get(source, "source_fields", {}) or {}
        row.update({
            "kind": "Opportunity assessment",
            "assessment_status": _text(disposition.get("qualification_state")
                                       or fields.get("classification")
                                       or raw.get("evidence_class")),
            "summary": _text(_get(source, "description")),
            "next_action": _text(disposition.get("qualification_reason")
                                 or fields.get("recommendation")
                                 or "Review requirement, fit, timing, and purchase route."),
            "requirement_family": _text(raw.get("requirement_family")),
            "source_as_of": _text(_get(source, "retrieved_at")
                                  or _get(evidence_pack, "generated_at")),
            "targets": [],
        })
        opportunity_rows.append(row)

    leadgen = leadgen or {}
    child_receipt = leadgen.get("receipt") or {}
    if child_receipt and _text(child_receipt.get("client_name")).casefold() != _text(client_name).casefold():
        raise ValueError("lead generation receipt belongs to a different client")
    parents = {p.get("assessment_id"): p for p in child_receipt.get("parents", [])}
    children: dict[str, list[dict]] = {}
    for child in child_receipt.get("leads", []):
        parent = parents.get(child.get("parent_assessment_id"), {})
        if parent.get("notice_id"):
            children.setdefault(parent["notice_id"], []).append(child)
    for row in opportunity_rows:
        source = original.get(row["source_id"], {})
        row["summary"] = row.get("summary") or _text(_get(source, "description"))
        row["source_as_of"] = row.get("source_as_of") or _text(_get(source, "retrieved_at"))
        if not row.get("targets") and any(_get(source, k) for k in ("contact_name", "contact_email", "contact_phone")):
            row["targets"] = [{
                "name": _get(source, "contact_name") or "Published notice contact",
                "organization": _get(source, "office") or _get(source, "agency"),
                "email": _get(source, "contact_email"), "phone": _get(source, "contact_phone"),
                "source_kind": "Published notice contact; follow official communication instructions",
                "source_url": row.get("source_url"),
            }]
        if re.fullmatch(r"[0-9a-f]{32}", row["source_id"]):
            from agents.reports.links import build_sam_notice_link
            row["source_url"] = build_sam_notice_link(row["source_id"]).url
        evidence_date = (leadgen.get("evidence_dates") or {}).get(row["source_id"])
        clock = (leadgen.get("evidence_clocks") or {}).get(row["source_id"])
        if clock is not None:
            row["source_as_of"] = clock.get("source_as_of")
            row["source_clock_status"] = clock.get("status")
            row["source_clock_coverage"] = f"{clock['known']} of {clock['total']} evidence acquisition times recorded"
        elif evidence_date:
            row["source_as_of"] = min(d for d in (row.get("source_as_of"), evidence_date) if d)
        row["lead_rows"] = children.get(row["source_id"], [])
        reviewed = next((c for c in row["lead_rows"] if c.get("research")), None)
        if reviewed:
            row["research"] = reviewed["research"]
            row["targets"] = reviewed.get("targets", [])
            row["next_action"] = reviewed["research"]["next_ask"]
            row["assessment_status"] = reviewed["research"]["status"].replace("_", " ")
            row["service_fit"] = "Requirement-specific fit remains to be established"
            row["commercial_route"] = reviewed["research"]["route"]
            parent = parents.get(reviewed.get("parent_assessment_id"), {})
            if parent.get("live_classification") == "awarded_or_closed":
                row["window_state"] = "Historical notice closed; follow-on status needs confirmation"
        row["lead_status"] = ", ".join(sorted({
            child["lead_tier"] for child in row["lead_rows"]
        })) or ("No child lead" if leadgen.get("status") == "complete"
                else "Lead generation input needs refresh")

    # Slot 7: graph-classified forecasts, separate from live opportunities.
    qualified_rows = [r for r in qualified_rows if not r.get("research")]
    forecast_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "forecast":
            continue
        row = _graph_record(raw, kind="forecast")
        row["next_action"] = "Shape or monitor before a solicitation posts."
        if claim(row):
            forecast_rows.append(row)

    # Slot 4 owns competitive award evidence.
    competitor_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "competitive_historical":
            continue
        row = _graph_record(raw, kind="competitive-award")
        if claim(row):
            competitor_rows.append(row)

    # Slot 6 owns partner/teaming evidence that has not already been assigned.
    teaming_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("route_relationship") != "named_partner_teaming":
            continue
        row = _graph_record(raw, kind="teaming-route")
        if claim(row):
            teaming_rows.append(row)
    for index, route in enumerate(_get(market_map, "teaming_routes", ()) or (), start=1):
        evidence = _evidence_rows(_get(route, "evidence", ()))
        source_id = next((r["source_id"] for r in evidence if r["source_id"]),
                         f"route-{index}")
        row = {
            "record_key": _record_key("teaming-route", source_id),
            "kind": "teaming_route",
            "source_id": source_id,
            "title": _text(_get(route, "organisation")) or "Teaming route",
            "summary": _text(_get(route, "why")),
            "source_url": next((r["source_url"] for r in evidence
                                if r["source_url"]), ""),
            "agency": _text(_get(route, "agency")),
            "role": _text(_get(route, "role")),
            "target_role": _text(_get(route, "target_role")),
            "person": _text(_get(route, "person")),
            "next_action": _text(_get(route, "action")),
            "evidence": evidence,
        }
        if claim(row):
            teaming_rows.append(row)

    # Slot 3 owns client/category spending records not assigned elsewhere.
    spending_rows: list[dict] = []
    for raw in graph_rows:
        if raw.get("evidence_class") != "client_historical":
            continue
        row = _graph_record(raw, kind="category-award")
        if claim(row):
            spending_rows.append(row)

    spending_metrics: list[dict] = []
    total = _money(_get(footprint, "total"))
    if total:
        spending_metrics.append(_metric(
            "Cited category obligations", total.get("display") or total.get("amount"),
            _text(_get(footprint, "meaning")), total.get("evidence")))
    for agency, money, count in (_get(footprint, "by_agency", ()) or ()): 
        amount = _money(money) or {}
        spending_metrics.append(_metric(
            _text(agency), amount.get("display") or amount.get("amount"),
            f"{int(count or 0)} cited award record(s)", amount.get("evidence")))

    competitor_metrics: list[dict] = []
    competitor_total = _money(_get(competition, "total"))
    if competitor_total:
        competitor_metrics.append(_metric(
            "Competitor obligations", competitor_total.get("display")
            or competitor_total.get("amount"),
            "Sum of the cited competitive award records.",
            competitor_total.get("evidence")))

    approved, pending, rejected = _profile_terms(profile, company)
    naics = sorted({_text(code) for code in (
        _get(company, "naics", ()) or profile.get("inferred_naics") or [])
        if _text(code)})
    query_rows = _query_rows(evidence_pack)
    lane_rows = _lane_rows(evidence_pack)
    mesh_records = query_rows + lane_rows
    graph_cache = graph_payload.get("incremental_cache_receipt") or {}
    providers = graph_cache.get("providers") or {}
    embedding = providers.get("embeddings") or {}
    semantic_evidenced = bool(embedding.get("exists") or any(
        any(token in _text(row.get("method")).casefold()
            for token in ("semantic", "dense", "embedding", "vector", "bm25", "fts"))
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

    priorities = _priority_records(qualified_rows, forecast_rows)
    if leadgen.get("status") == "complete":
        priorities = _priority_records([
            r for r in opportunity_rows
            if any(c.get("lead_tier") in {"LEAD_T1", "LEAD_T2"}
                   for c in r.get("lead_rows", []))
        ][:3], [])
    reviewed_rows = [r for r in opportunity_rows if r.get("research")]
    if reviewed_rows:
        # Deliberate current research selection; all candidates remain in slot 5.
        priorities = [{
            "reference_key": row["record_key"], "reference_slot_id": "federal-opportunities",
            "priority": index, "title": row["title"], "source_url": row.get("source_url"),
            "priority_kind": row["research"]["status"].replace("_", " "),
            "why": row["research"]["rationale"], "route": row["research"]["route"],
            "timing": row["research"]["why_now"], "next_action": row["research"]["next_ask"],
            "target_count": len(row.get("targets") or []),
        } for index, row in enumerate(
            [r for r in reviewed_rows if r["research"]["priority"]][:3], 1)]
    priority_metrics = [
        _metric("Ranked pursuits", len(priorities),
                "References the owned opportunity or forecast record below."),
        _metric("Named targets", sum(len(r.get("targets") or [])
                                     for r in opportunity_rows),
                "Targets remain bound to their qualifying opportunity."),
    ] if priorities else []

    slots_by_id = {
        "priority-pursuits": ProductSlot(
            1, "priority-pursuits", contract_slots[0].heading,
            _slot_status(priorities, priority_metrics),
            "The pursuits that deserve the next unit of seller attention.",
            tuple(priority_metrics), tuple(priorities), (),
            (() if priorities else (
                "No pursuit cleared the current qualification boundary; resolve "
                "the displayed graph and coverage holds before promotion.",)),
            {"ranked_from": "qualified opportunities, then forecasts"}),
        "research-mesh": ProductSlot(
            2, "research-mesh", contract_slots[1].heading,
            "populated", "The approved vocabulary, boundaries, query lanes, and receipts used to find this market.",
            tuple(mesh_metrics), tuple(mesh_records), (), tuple(mesh_gaps),
            {"approved_terms": approved, "pending_terms": pending,
             "rejected_terms": rejected, "naics": naics,
             "semantic_receipt_present": semantic_evidenced}),
        "agency-spending": ProductSlot(
            3, "agency-spending", contract_slots[2].heading,
            _slot_status(spending_rows, spending_metrics),
            "Category-specific historical obligations, never mislabeled as pipeline or market size.",
            tuple(spending_metrics), tuple(spending_rows), (),
            (() if spending_rows or spending_metrics else (
                "No category spending record is qualified in this edition; re-run the award lane under the approved frame.",)),
            {"record_count": int(_get(footprint, "record_count", 0) or 0),
             "period": _text(_get(footprint, "period"))}),
        "competitors": ProductSlot(
            4, "competitors", contract_slots[3].heading,
            _slot_status(competitor_rows, competitor_metrics),
            "Named product competitors and the federal award evidence that establishes their position.",
            tuple(competitor_metrics), tuple(competitor_rows), (),
            (() if competitor_rows else (
                "No identified competitor has a qualified award record in the current evidence pack; preserve this as an observed zero and continue award research.",)),
            {"screened": int(_get(competition, "screened", 0) or 0)}),
        "federal-opportunities": ProductSlot(
            5, "federal-opportunities", contract_slots[4].heading,
            _slot_status(opportunity_rows, ()),
            "Opportunity assessments with qualification status and lead generation. Candidates remain visible for relevance review; a HOLD lead does not remove its assessment.",
            (), tuple(opportunity_rows), (),
            (() if opportunity_rows else (
                "No current opportunity cleared evidence, fit, window, and route eligibility together; held records remain in the graph receipt.",)),
            {"assessed": len(opportunity_rows), "qualified": len(qualified_rows),
             "held": len(graph_payload.get("held_opportunities") or []),
             "leadgen_status": leadgen.get("status", "not_run"),
             "lead_tiers": leadgen.get("tiers", {})}),
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
    graph_receipt = {
        "schema_version": graph_payload.get("schema_version"),
        "certified": bool(graph_payload.get("graph_contract_certified")),
        "violations": _serial(graph_payload.get("graph_contract_violations") or []),
        "workflow_contract": _serial(graph_payload.get("workflow_contract") or {}),
        "incremental_cache_receipt": _serial(graph_cache),
        "owned_record_keys": sorted(claimed),
        "owned_record_count": len(claimed),
    }
    source_receipt = {
        "evidence_pack_generated_at": _text(_get(evidence_pack, "generated_at")),
        "evidence_records": len(_get(evidence_pack, "records", ()) or ()),
        "query_count": len(query_rows),
        "lane_count": len(lane_rows),
        "graph_records": len(graph_rows),
        "graph_indexed_records": len(graph_index),
    }
    return ExternalProductDocument(
        schema_version=EXTERNAL_PRODUCT_PROJECTION_VERSION,
        contract_version=CONTRACT_VERSION,
        client_name=_text(client_name), slug=_text(slug), as_of=_text(as_of),
        slots=slots, graph_receipt=graph_receipt,
        source_receipt=source_receipt,
    )


__all__ = (
    "EXTERNAL_PRODUCT_PROJECTION_VERSION",
    "ExternalProductDocument",
    "ProductSlot",
    "build_external_product_document",
)
