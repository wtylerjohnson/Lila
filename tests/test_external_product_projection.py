"""Eight-slot graph projection and deterministic external renderer."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

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
            person="", action="Open a teaming conversation.",
            evidence=(SimpleNamespace(
                source_id="ROUTE-SAFE", source_kind="notice",
                source_url="https://sam.gov/opp/route-safe/view",
                label="Supported teaming route"),)),),
    )


def _graph():
    notice = {
        "record_id": "NOTICE-1", "title": "Language Support Services",
        "description": "Cleared interpretation and translation support.",
        "agency": "Department of State", "office": "Acquisitions",
        "sub_agency": "Diplomatic Security", "naics": "541930",
        "psc": "R608", "notice_type": "Solicitation",
        "set_aside": "Total Small Business Set-Aside",
        "posted_date": "2026-08-01", "solicitation_number": "19AQMM26R0001",
        "contact_name": "Alex Buyer", "contact_email": "alex@example.gov",
        "fit_basis": "approved core capability evidence",
        "route_basis": "client attests small business",
        "window_basis": "published response deadline is live",
        "matched_sentence": "interpretation and translation support",
        "retrieved_at": "2026-08-23T09:00:00Z",
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
        "description": "Planned multilingual support for overseas posts.",
        "agency": "Department of State", "evidence_class": "forecast",
        "sub_agency": "Bureau of Administration", "naics": "541930",
        "fiscal_year": "2027", "anticipated_solicitation": "2027-02-01",
        "anticipated_award": "2027-06-15",
        "small_business_poc": "Sam POC <sam@example.gov>",
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
    safe_route = {
        "record_id": "ROUTE-SAFE", "title": "Prime access route",
        "agency": "Department of State",
        "evidence_class": "current_opportunity", "service_fit": "direct",
        "window_state": "live", "commercial_route": "named_partner_teaming",
        "route_relationship": "named_partner_teaming", "eligible_route": True,
        "url": "https://sam.gov/opp/route-safe/view",
    }
    return {
        "schema_version": "evidence-pack-v2",
        "graph_contract_certified": True,
        "graph_contract_violations": [],
        "records": [notice, forecast, competitor, client_award, safe_route],
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
            "cache_root": "/Users/example/private/cache",
            "providers": {"embeddings": {
                "exists": True,
                "path": "/Users/example/private/cache/dense_vectors.db",
            }},
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


def test_graph_identity_cannot_own_both_opportunity_and_teaming_slots():
    graph = _graph()
    graph["records"].append({
        **graph["records"][0],
        "route_relationship": "named_partner_teaming",
        "commercial_route": "named_partner_teaming",
    })

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={"capability_terms": {"core": ["language services"]},
                 "inferred_naics": ["541930"]},
        client_name="Acme", slug="acme", as_of="2026-08-23")
    by_id = {slot.slot_id: slot for slot in document.slots}

    assert [row["source_id"] for row in
            by_id["federal-opportunities"].records] == ["NOTICE-1"]
    assert "NOTICE-1" not in {
        row["source_id"] for row in by_id["teaming-opportunities"].records}
    assert validate_external_product_document(document) == []


def test_teaming_slot_rejects_ambiguous_excluded_and_unrelated_graph_rows():
    graph = _graph()
    graph["records"].extend((
        {
            "record_id": "ROUTE-AMBIGUOUS",
            "title": "Unresolved partner paper",
            "evidence_class": "ambiguous",
            "service_fit": "direct",
            "route_relationship": "named_partner_teaming",
            "commercial_route": "named_partner_teaming",
            "eligible_route": True,
            "url": "https://www.usaspending.gov/award/ROUTE-AMBIGUOUS",
        },
        {
            "record_id": "ROUTE-EXCLUDED",
            "title": "Out of scope partner paper",
            "evidence_class": "excluded",
            "service_fit": "unrelated",
            "route_relationship": "named_partner_teaming",
            "commercial_route": "named_partner_teaming",
            "eligible_route": True,
            "url": "https://www.usaspending.gov/award/ROUTE-EXCLUDED",
        },
        {
            "record_id": "ROUTE-ADJACENT",
            "title": "Weakly adjacent partner paper",
            "evidence_class": "client_historical",
            "service_fit": "adjacent",
            "route_relationship": "named_partner_teaming",
            "commercial_route": "named_partner_teaming",
            "eligible_route": True,
            "url": "https://www.usaspending.gov/award/ROUTE-ADJACENT",
        },
    ))

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    teaming = next(slot for slot in document.slots
                   if slot.slot_id == "teaming-opportunities")

    assert [row["source_id"] for row in teaming.records] == ["ROUTE-SAFE"]
    assert validate_external_product_document(document) == []


def test_external_spending_and_competition_hold_non_direct_fit_records():
    graph = _graph()
    graph["records"].extend((
        {
            "record_id": "AWARD-RIVAL-REVIEW",
            "title": "Generic technology award", "recipient": "Rival Inc",
            "evidence_class": "competitive_historical",
            "service_fit": "ambiguous", "commercial_route": "incumbent",
            "obligated_dollars": 9_000_000,
            "url": "https://www.usaspending.gov/award/AWARD-RIVAL-REVIEW",
        },
        {
            "record_id": "AWARD-CLIENT-OTHER",
            "title": "Unrelated facilities award", "recipient": "Acme",
            "evidence_class": "client_historical",
            "service_fit": "unrelated", "commercial_route": "direct",
            "obligated_dollars": 8_000_000,
            "url": "https://www.usaspending.gov/award/AWARD-CLIENT-OTHER",
        },
    ))

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    by_id = {slot.slot_id: slot for slot in document.slots}

    assert [row["source_id"] for row in by_id["competitors"].records] == [
        "AWARD-RIVAL"]
    assert by_id["competitors"].coverage["fit_review_holds"] == 1
    assert [row["source_id"] for row in by_id["agency-spending"].records] == [
        "AWARD-CLIENT"]
    assert by_id["agency-spending"].coverage["fit_review_holds"] == 1
    assert by_id["agency-spending"].metrics[0]["value"] == "$1,200,000.00"


def test_synthetic_teaming_route_cannot_bypass_certified_graph_admission():
    market_map = _market_map()
    market_map.teaming_routes = (SimpleNamespace(
        organisation="Unsupported Prime", role="prime", agency="State",
        why="Legacy synthesis promoted an excluded award.",
        target_role="capture lead", person="", action="Call the prime.",
        evidence=(SimpleNamespace(
            source_id="ROUTE-EXCLUDED", source_kind="award",
            source_url="https://www.usaspending.gov/award/ROUTE-EXCLUDED",
            label="Excluded route"),)),)
    graph = _graph()
    graph["records"].append({
        "record_id": "ROUTE-EXCLUDED", "title": "Out of scope paper",
        "evidence_class": "excluded", "service_fit": "unrelated",
        "route_relationship": "named_partner_teaming",
        "commercial_route": "named_partner_teaming", "eligible_route": True,
        "url": "https://www.usaspending.gov/award/ROUTE-EXCLUDED",
    })

    document = build_external_product_document(
        market_map=market_map, graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    teaming = next(slot for slot in document.slots
                   if slot.slot_id == "teaming-opportunities")

    assert [row["source_id"] for row in teaming.records] == ["ROUTE-SAFE"]
    assert "ROUTE-EXCLUDED" not in {
        row["source_id"] for row in teaming.records}
    assert validate_external_product_document(document) == []


def test_external_projection_repairs_nonresolving_usaspending_award_route():
    graph = _graph()
    client_award = next(row for row in graph["records"]
                        if row["record_id"] == "AWARD-CLIENT")
    client_award["url"] = (
        "https://www.usaspending.gov/award/"
        "CONT_AWD_AWARDCLIENT_9700_-NONE-_-NONE-")

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    spending = next(slot for slot in document.slots
                    if slot.slot_id == "agency-spending")

    assert spending.records[0]["source_url"] == (
        "https://api.usaspending.gov/api/v2/awards/"
        "CONT_AWD_AWARDCLIENT_9700_-NONE-_-NONE-/")


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
    assert live[0]["summary"] == \
        "Cleared interpretation and translation support."
    assert live[0]["naics"] == "541930"
    assert live[0]["contact_email"] == "alex@example.gov"
    assert future[0]["summary"] == \
        "Planned multilingual support for overseas posts."
    assert future[0]["anticipated_award"] == "2027-06-15"
    assert {section["heading"] for section in future[0]["detail_sections"]} >= {
        "Full published scope", "Acquisition", "Forecast timing"}


def test_slot_one_is_deadline_ordered_and_never_claims_a_rank():
    graph = _graph()
    later = graph["qualified_opportunity_records"][0]
    earlier = {
        **later,
        "record_id": "NOTICE-2",
        "notice_id": "NOTICE-2",
        "title": "Earlier Interpreter Requirement",
        "response_due": "2026-08-31",
        "response_deadline": "2026-08-31",
        "source_url": "https://sam.gov/opp/notice-2/view",
        "url": "https://sam.gov/opp/notice-2/view",
        "requirement_family": "family-2",
        "linked_targets": [],
    }
    graph["qualified_opportunity_records"].append(earlier)

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    actions = document.slots[0].records

    assert [row["title"] for row in actions] == [
        "Earlier Interpreter Requirement", "Language Support Services"]
    assert [row["action_order"] for row in actions] == [1, 2]
    assert all("priority" not in row for row in actions)


def test_long_scope_is_excerpted_for_scan_but_preserved_in_detail():
    graph = _graph()
    full_scope = " ".join(
        ["Detailed published forecast requirement \ufffd"] * 40)
    forecast = next(row for row in graph["records"]
                    if row["evidence_class"] == "forecast")
    forecast["description"] = full_scope

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    rendered = next(slot for slot in document.slots
                    if slot.slot_id == "future-forecasts").records[0]
    scope = next(section for section in rendered["detail_sections"]
                 if section["heading"] == "Full published scope")

    assert len(rendered["summary"]) <= 523
    assert rendered["summary"].endswith("...")
    assert scope["fields"][0]["value"] == full_scope.replace("\ufffd", "-")


def test_raw_and_canonical_record_counts_are_both_explicit():
    graph = _graph()
    graph["records"].append({
        **graph["records"][2], "obligated_dollars": 500_000,
    })

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    assert document.source_receipt["graph_records_raw"] == 6
    assert document.source_receipt["graph_records"] == 5
    assert document.source_receipt["graph_indexed_records"] == 5
    assert document.graph_receipt["incremental_cache_receipt"][
        "cache_root"] == "local-artifact:cache"
    assert document.graph_receipt["incremental_cache_receipt"]["providers"][
        "embeddings"]["path"] == "local-artifact:dense_vectors.db"


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
    assert client.count('<section class="plain-section product-slot') == 8
    assert client.count('class="product-slot-intro"') == 8
    assert 'class="product-svg direction-graph"' in client
    assert 'class="product-svg vector-map"' in client
    assert "https://sam.gov/opp/notice-1/view" in client
    assert client.count('class="receipt-print"') == 8
    assert 'href="url"' not in client
    assert ">label ↗</a>" not in client
    assert ">NOTICE-1 ↗</a>" in client
    assert "Evidence and decision detail" in client
    assert "Cleared interpretation and translation support." in client
    assert "19AQMM26R0001" in client
    assert "approved core capability evidence" in client
    assert "Deadline-ordered action" in client
    assert "Ranked pursuit" not in client
    assert "Qualified category obligations: $1,200,000.00 = $1,200,000.00" in client
    assert "@page{size:letter;margin:.42in}" in client
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in client
    assert "break-inside:avoid;page-break-inside:avoid;overflow:hidden" in client


def test_print_media_uses_paginated_layout_and_static_receipts(monkeypatch, tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.export_pdf import find_chrome

    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    _studio, client = render_external_product(_document())
    artifact = tmp_path / "lila-print-contract.html"
    artifact.write_text(client, encoding="utf-8")

    with sync_playwright() as manager:
        browser = manager.chromium.launch(
            headless=True, executable_path=find_chrome())
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.emulate_media(media="print")
        page.goto(artifact.as_uri(), wait_until="load")
        state = page.evaluate("""() => {
          const style = (selector) => getComputedStyle(document.querySelector(selector));
          return {
            recordDisplay: style('[data-slot-id="research-mesh"] .product-records').display,
            recordColumns: style('[data-slot-id="research-mesh"] .product-records').gridTemplateColumns.split(' ').length,
            visualBreak: style('.product-visual').breakInside,
            introBreak: style('.product-slot-intro').breakInside,
            duplicateSummary: style('.product-slot-summary').display,
            interactiveReceipt: style('.receipts-appendix .inline-work').display,
            printReceipt: style('.receipts-appendix .receipt-print').display,
          };
        }""")
        browser.close()

    assert state == {
        "recordDisplay": "grid",
        "recordColumns": 2,
        "visualBreak": "avoid",
        "introBreak": "avoid",
        "duplicateSummary": "none",
        "interactiveReceipt": "none",
        "printReceipt": "block",
    }


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
