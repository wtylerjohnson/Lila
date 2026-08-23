"""Eight-slot graph projection and deterministic external renderer."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from agents.golden_press.external_product_contract import load_external_product_slots
from agents.golden_press.external_product_projection import (
    build_external_product_document,
)
from agents.golden_press.external_product_render import (
    render_external_product,
    validate_external_product_document,
    validate_external_product_html,
)


def _mark() -> str:
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="48"><rect width="120" height="48" fill="black"/></svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode()


def _market_map():
    evidence = (SimpleNamespace(
        source_id="AWARD-CLIENT", source_kind="award",
        source_url="https://www.usaspending.gov/award/AWARD-CLIENT",
        label="Client award"),)
    money = SimpleNamespace(display="$1.2M", amount=1200000,
                            basis="obligations", population="cited awards",
                            evidence=evidence)
    return SimpleNamespace(
        company_understanding=SimpleNamespace(
            researched_keywords=("language services", "interpretation"),
            pending_keywords=("mission linguist",), rejected_keywords=(),
            naics=("541930",)),
        category_footprint=SimpleNamespace(
            total=money, by_agency=(("Department of State", money, 1),),
            record_count=1, meaning="Cited historical obligations.",
            period="FY2025"),
        competitive_position=SimpleNamespace(total=None, screened=1),
        teaming_routes=(SimpleNamespace(
            organisation="Prime Partner", role="prime", agency="State",
            why="Holds relevant federal paper.", target_role="capture lead",
            person="", action="Open a teaming conversation.", evidence=()),),
    )


def _graph():
    notice = {
        "record_id": "NOTICE-1", "title": "Language Support Services",
        "agency": "Department of State", "office": "Acquisitions",
        "response_deadline": "2026-09-30", "evidence_class": "current_opportunity",
        "service_fit": "direct", "window_state": "live",
        "commercial_route": "direct", "eligible_route": True,
        "url": "https://sam.gov/opp/notice-1/view",
        "requirement_family": "family-1",
    }
    target = {
        "requirement_family": "family-1", "opportunity_record_id": "NOTICE-1",
        "role": "contracting_officer_or_specialist", "name": "Alex Buyer",
        "organization": "Department of State", "email": "alex@example.gov",
        "source_kind": "published_contact",
    }
    forecast = {
        "record_id": "FORECAST-1", "title": "Future Language Program",
        "agency": "Department of State", "evidence_class": "forecast",
        "service_fit": "direct", "window_state": "fy_only",
        "commercial_route": "unknown", "estimated_value_range": "$5M-$10M",
        "url": "https://example.gov/forecast/1",
    }
    competitor = {
        "record_id": "AWARD-RIVAL", "title": "Language Services Award",
        "recipient": "Rival Inc", "agency": "Department of State",
        "evidence_class": "competitive_historical", "service_fit": "direct",
        "window_state": "stated_past", "commercial_route": "incumbent",
        "obligated_dollars": 750000,
        "url": "https://www.usaspending.gov/award/AWARD-RIVAL",
    }
    client_award = {
        "record_id": "AWARD-CLIENT", "title": "Interpretation Award",
        "recipient": "Acme", "agency": "Department of State",
        "evidence_class": "client_historical", "service_fit": "direct",
        "window_state": "stated_past", "commercial_route": "direct",
        "obligated_dollars": 1200000,
        "url": "https://www.usaspending.gov/award/AWARD-CLIENT",
    }
    return {
        "schema_version": "evidence-pack-v2",
        "graph_contract_certified": True,
        "graph_contract_violations": [],
        "records": [notice, forecast, competitor, client_award],
        "qualified_opportunity_records": [{
            **notice, "response_due": notice["response_deadline"],
            "source_url": notice["url"], "linked_targets": [target],
            "technical_fit": {"basis": "approved core capability"},
            "next_route": {"route_basis": "eligible direct route"},
        }],
        "target_groups": {"family-1": [target]},
        "held_opportunities": [],
        "workflow_contract": {"certified": True},
        "incremental_cache_receipt": {
            "providers": {"embeddings": {"exists": True}},
        },
    }


def _pack():
    return SimpleNamespace(
        generated_at="2026-08-23T10:00:00Z",
        records=[1, 2, 3, 4],
        lanes=[SimpleNamespace(lane="L1_notice", status="live", detail="returned")],
        queries=[SimpleNamespace(
            lane="L1_notice", method="BM25 + dense semantic fusion",
            endpoint="https://sam.gov/api", body={"keywords": ["language services"]},
            result_count=64, kept_after_screen=8, note="guard ladder applied")],
        events=[{"id": "EVENT-1", "title": "State Department Industry Day",
                 "url": "https://state.gov/events/industry-day",
                 "date": "2026-10-15", "agency": "Department of State"}],
        events_screen={"screened": 4, "kept": 1},
    )


def _document():
    return build_external_product_document(
        market_map=_market_map(), graph_payload=_graph(), evidence_pack=_pack(),
        profile={"capability_terms": {"core": ["language services"]},
                 "inferred_naics": ["541930"]},
        client_name="Acme", slug="acme", as_of="2026-08-23")


def test_graph_maps_into_exact_operator_locked_slots():
    document = _document()
    expected = load_external_product_slots()
    assert [(slot.number, slot.slot_id, slot.heading) for slot in document.slots] == [
        (slot.number, slot.slot_id, slot.heading) for slot in expected]
    assert validate_external_product_document(document) == []


def test_each_evidence_record_has_one_owning_slot_and_priority_only_references():
    document = _document()
    owners = {}
    for slot in document.slots:
        for row in slot.records:
            if row.get("record_key"):
                assert row["record_key"] not in owners
                owners[row["record_key"]] = slot.slot_id
    priority = document.slots[0]
    assert priority.records[0]["reference_slot_id"] == "federal-opportunities"
    assert "record_key" not in priority.records[0]


def test_forecasts_and_live_opportunities_are_separate_and_targets_stay_bound():
    document = _document()
    by_id = {slot.slot_id: slot for slot in document.slots}
    live = by_id["federal-opportunities"].records
    future = by_id["future-forecasts"].records
    assert [row["source_id"] for row in live] == ["NOTICE-1"]
    assert [row["source_id"] for row in future] == ["FORECAST-1"]
    assert live[0]["targets"][0]["requirement_family"] == live[0]["requirement_family"]
    assert {visual["type"] for visual in by_id["future-forecasts"].visuals} == {
        "directional_graph", "relationship_vector_map"}


def test_scriptless_renderer_carries_all_slots_marks_sources_and_visuals(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    document = _document()
    studio, client = render_external_product(document)
    verdict = validate_external_product_html(client, document)
    assert verdict["ok"], verdict["violations"]
    assert "<script" in studio.lower()
    assert "<script" not in client.lower()
    assert client.count('data-slot-id="') == 8
    assert 'class="product-svg direction-graph"' in client
    assert 'class="product-svg vector-map"' in client
    assert "https://sam.gov/opp/notice-1/view" in client


def test_slot_with_no_content_remains_as_named_gap():
    graph = _graph()
    graph["records"] = [row for row in graph["records"]
                        if row["evidence_class"] not in {"forecast", "competitive_historical"}]
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    by_id = {slot.slot_id: slot for slot in document.slots}
    assert by_id["competitors"].status == "gap"
    assert by_id["competitors"].gaps
    assert by_id["future-forecasts"].status == "gap"
    assert by_id["future-forecasts"].gaps
