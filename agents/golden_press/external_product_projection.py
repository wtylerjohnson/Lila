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
from copy import deepcopy
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
    from agents.reports.value_evidence import value_evidence
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
        "value": next((row[key] for key in ("published_value", "obligated_dollars",
                     "ceiling_dollars", "estimated_value_range") if row.get(key) is not None), None),
        "value_evidence": value_evidence(row, source_url=_source_from_graph(row), source_id=record_id),
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


def _companion_binding(leadgen: dict, context) -> tuple[dict, dict, list[str]]:
    """A saved child is current only under the current immutable Assess parent.

    A run-id string or notice-id join is not a provenance check. The supplied
    run must equal the currently loaded run, and each serialized parent must
    agree with the native parent mapper. Invalid input remains saved history.
    """
    from agents.leadgen.from_assess import coerce_assess_run, _parent_from_live
    from agents.leadgen.press import PressLeadGenReceipt

    if not leadgen.get("receipt"):
        return {}, {}, []
    try:
        current = getattr(context, "current_assess_run", None)
        if current is None or context.gaps or not context.unchanged():
            raise ValueError("Current Assess source binding is unavailable")
        run = coerce_assess_run(leadgen.get("assessment") or {})
        if run.model_dump(mode="json") != current.model_dump(mode="json"):
            raise ValueError("Saved companion differs from the current immutable Assess run")
        receipt = PressLeadGenReceipt.model_validate(leadgen["receipt"])
        if (leadgen.get("assess_run_id") != run.run_id
                or receipt.assess_run_id != run.run_id
                or receipt.client_name != run.client_name
                or receipt.as_of != run.as_of):
            raise ValueError("Saved companion receipt has different Assess provenance")
        expected = {_parent_from_live(run, item).assessment_id: _parent_from_live(run, item)
                    for item in run.live.records}
        parents = {}
        for parent in receipt.parents:
            if not parent.notice_id:
                continue
            native = expected.get(parent.assessment_id)
            excluded = {"lead_ids", "dossier_schema_version", "identity_status"}
            if native is None or parent.model_dump(exclude=excluded) != native.model_dump(exclude=excluded):
                raise ValueError("Saved companion notice parent differs from current Assess")
            if parent.assessment_id in parents:
                raise ValueError("Saved companion repeats a notice parent")
            parents[parent.assessment_id] = parent.model_dump(mode="json")
        children = {}
        seen = set()
        for child in receipt.leads:
            parent = parents.get(child.parent_assessment_id)
            if parent is None:
                continue  # non-notice children belong to their existing surfaces
            if (child.assess_run_id != run.run_id or child.lead_id in seen
                    or child.lead_id not in parent["lead_ids"]):
                raise ValueError("Saved child does not bind its current Assess parent")
            seen.add(child.lead_id)
            children.setdefault(parent["notice_id"], []).append(child.model_dump(mode="json"))
        return parents, children, []
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {}, {}, [f"Current lead projection withheld: {exc}"]


def _withhold_notice(row: dict, gaps: list[str]) -> None:
    """Keep the selected assessment without advertising saved positive state."""
    row.setdefault("saved_assessment", {key: deepcopy(row.get(key)) for key in (
        "assessment_status", "evidence_class", "service_fit", "commercial_route",
        "window_state", "response_date", "value", "value_evidence", "next_action")})
    row.update(assessment_status="Current qualification withheld",
               evidence_class="notice_diagnostic", service_fit="Unverified",
               commercial_route="Unverified", window_state="Unverified",
               value=None, value_evidence=[], targets=[], selected_response_date=row.get("response_date"),
               response_date="", next_action="; ".join(gaps),
               current_notice_admitted=False, qualification_gaps=list(gaps))


def _notice_family(row: dict, context) -> None:
    """Expose the current family without substituting a nonselected result."""
    if context is None or not context.unchanged():
        return
    family = context.family_for(row["source_id"])
    if not family:
        return
    from tools.relevance.temporal import record_time
    members = []
    for member in family["members"]:
        original = member["original_record"]
        raw = original.get("raw_payload") or {}
        raw = raw if isinstance(raw, dict) else {}
        active = [str(d["active"]).strip().casefold() for d in (original, raw) if d.get("active") is not None]
        states = [str(d["status"]).strip().casefold() for d in (original, raw) if d.get("status") is not None]
        types = [str(d[k]).strip().casefold() for d in (original, raw)
                 for k in ("notice_type", "type") if d.get(k) is not None]
        closed = any(v in {"no", "false", "0"} for v in active) or any(
            v in {"closed", "cancelled", "canceled", "inactive", "awarded"} for v in states) or any(
            v in {"award", "award notice", "award notification", "awarded", "a"} for v in types)
        state = "inactive or closed" if closed else "active source claim" if active and all(
            v in {"yes", "true", "1"} for v in active) else "status unknown"
        members.append({"source_id": member["source_id"], "title": _text(original.get("title")),
            "source_url": _http_url(original.get("api_url") or original.get("url") or raw.get("uiLink") or raw.get("url")),
            "source_status": state, "original_active_claims": active, "original_status_claims": states,
            "notice_types": types, "lineage_status": member["status"],
            "superseded_by": member.get("superseded_by"), "record_sha256": member["record_sha256"],
            "posted": member["posted"], "deadline": record_time(original, "deadline")})
    representative = next((m for m in members if m["source_id"] == family["representative_id"]), None)
    row.update(family_member_ids=list(family["member_ids"]), family_members=members,
               family_order_status=family["order_status"], current_representative=representative,
               canonical_record_id=family["representative_id"],
               family_history_scope=family["history_scope"])
    if family["order_status"] != "ordered" or family["representative_id"] != row["source_id"]:
        row["selected_response_date"] = row.get("selected_response_date") or row.pop("response_date", "")
        row["response_date"] = ""
        row["window_state"] = ("Family chronology unresolved" if family["order_status"] != "ordered" else
                               f"Selected notice superseded; current family {representative['source_status']}")


def _current_research(child: dict, notice_id: str, context) -> bool:
    book = getattr(context, "current_reviewed_cases", None)
    if book is None:
        return False
    case = next((c for c in book.cases if c.record.notice_id == notice_id), None)
    return bool(case and child.get("research") == case.research.model_dump(mode="json")
                and child.get("targets", []) == [t.model_dump(mode="json") for t in case.targets])


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
                     "statements plot the source's published floor. Forecast amounts "
                     "are planning estimates, not committed spending or seller pipeline."),
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
    supplied_graph = graph_payload
    context = notice_context if isinstance(notice_context, NoticeReadContext) else None
    graph_current = context is not None and context.accepts_graph(graph_payload)
    if not graph_current:
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
    admissions = {}
    target_groups = graph_payload.get("target_groups") or {}
    for raw in graph_payload.get("qualified_opportunity_records") or []:
        decision = context.decision(raw)
        admissions[str(raw.get("record_id"))] = decision
        if not decision["admitted"]:
            continue
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
            "current_notice_admitted": True,
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
            "targets": [],
        })
        opportunity_rows.append(row)

    leadgen = leadgen or {}
    child_receipt = leadgen.get("receipt") or {}
    if child_receipt and _text(child_receipt.get("client_name")).casefold() != _text(client_name).casefold():
        raise ValueError("lead generation receipt belongs to a different client")
    saved_parents = {p.get("assessment_id"): p for p in child_receipt.get("parents", [])}
    saved_children: dict[str, list[dict]] = {}
    for child in child_receipt.get("leads", []):
        parent = saved_parents.get(child.get("parent_assessment_id"), {})
        if parent.get("notice_id"):
            saved_children.setdefault(parent["notice_id"], []).append(child)
    parents, children, companion_gaps = _companion_binding(leadgen, context)
    for row in opportunity_rows:
        sid = row["source_id"]
        source = original.get(row["source_id"], {})
        decision = admissions.get(sid) or (context.decision(graph_index.get(sid) or {"record_id": sid})
                                          if graph_current else {})
        admitted = graph_current and decision.get("admitted") is True
        row["current_notice_admitted"] = admitted
        row["summary"] = row.get("summary") or _text(_get(source, "description"))
        row["saved_source_as_of"] = _text(_get(source, "retrieved_at"))
        row["source_as_of"] = None
        row["source_clock_status"] = "unverified source acquisition"
        row["source_clock_coverage"] = "Source acquisition unavailable; refresh the current bound assessment."
        if not admitted:
            gaps = decision.get("gaps") or ["Current original source and graph admission are unavailable; refresh the bound assessment."]
            _withhold_notice(row, gaps)
        material = context.material_fields(sid) if admitted else {}
        if admitted:
            row["summary"] = _text(material.get("description"))
        if admitted and not row.get("targets"):
            row["targets"] = [{
                "name": contact.get("name") or "Published notice contact",
                "role": contact.get("title") or "Published notice contact",
                "organization": material.get("office") or material.get("agency"),
                "email": contact.get("email"), "phone": contact.get("phone"),
                "source_slot": contact.get("source_slot"),
                "source_kind": "Published notice contact; follow official communication instructions",
                "source_url": row.get("source_url"),
            } for contact in material.get("source_notice_contacts") or []]
        if re.fullmatch(r"[0-9a-f]{32}", row["source_id"]):
            from agents.reports.links import build_sam_notice_link
            row["source_url"] = build_sam_notice_link(row["source_id"]).url
        current_run = getattr(context, "current_assess_run", None)
        clock_parent = next((p for p in current_run.live.records if p.notice_id == sid), None) if (
            current_run is not None and not context.gaps and context.unchanged()) else None
        if clock_parent is not None:
            from agents.assess.source_clock import acquisition_summary
            # Companion display summaries are not an independent clock authority.
            clock = acquisition_summary(clock_parent.authoritative_evidence)
            row["source_as_of"] = clock.get("source_as_of")
            row["source_clock_status"] = clock.get("status")
            row["source_clock_coverage"] = f"{clock['known']} of {clock['total']} evidence acquisition times recorded"
        bound = children.get(sid, [])
        reviewed = next((c for c in bound if c.get("research") and _current_research(c, sid, context)), None)
        row["lead_rows"] = [c for c in bound if (admitted and not c.get("research"))
                            or (reviewed and c.get("research") and _current_research(c, sid, context))]
        row["saved_lead_rows"] = [deepcopy(c) for c in saved_children.get(sid, []) if c not in row["lead_rows"]]
        if row["saved_lead_rows"]:
            row["lead_projection_gaps"] = companion_gaps or [
                "Saved lead history lacks current notice admission or matching reviewed-research authority."]
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
        })) or ("Current lead withheld; saved history retained" if row["saved_lead_rows"] else
                "No child lead" if leadgen.get("status") == "complete"
                else "Lead generation input needs refresh")
        _notice_family(row, context)

    # Assessment retention does not move held notices behind qualified notices.
    native_order = {str(raw.get("record_id")): i for i, raw in enumerate(graph_rows)}
    opportunity_rows.sort(key=lambda row: native_order.get(row["source_id"], len(native_order)))

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
            if r.get("current_notice_admitted") and any(c.get("lead_tier") in {"LEAD_T1", "LEAD_T2"}
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
        "current_notice_graph_admitted": graph_current,
        "lead_projection_gaps": companion_gaps,
    }
    source_receipt = {
        "evidence_pack_generated_at": _text(_get(evidence_pack, "generated_at")),
        "evidence_records": len(_get(evidence_pack, "records", ()) or ()),
        "query_count": len(query_rows),
        "lane_count": len(lane_rows),
        "graph_records": len(graph_rows),
        "graph_indexed_records": len(graph_index),
    }
    if context is not None and not context.unchanged():
        # A source/pointer change during projection invalidates every positive
        # derived from that snapshot. Rebuild once with saved history only.
        return build_external_product_document(
            market_map=market_map, graph_payload=supplied_graph, evidence_pack=evidence_pack,
            profile=profile, client_name=client_name, slug=slug, as_of=as_of,
            leadgen=leadgen, notice_context=None)
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
