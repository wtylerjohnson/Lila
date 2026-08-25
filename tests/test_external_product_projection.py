"""Eight-slot graph projection and deterministic external renderer."""

from __future__ import annotations

import base64
import copy
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agents.golden_press.external_product_contract import load_external_product_slots
from agents.golden_press.external_product_projection import (
    build_external_product_document,
    record_ownership_key,
)
from agents.golden_press.external_product_render import (
    OPPORTUNITY_CARD_FIELD_ORDER,
    OPPORTUNITY_CARD_REGION_ORDER,
    OPPORTUNITY_CARD_VERSION,
    capture_external_product_identity_assets,
    render_external_product,
    render_slot,
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
        "route_action": "prime", "pursuit_state": "pursue_prime",
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
        "evidence_class": "teaming_evidence", "service_fit": "direct",
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
            "schema_version": "incremental-cache-semantic-receipt-v1",
            "adapter": "existing-systems-graph-adapter-v2",
            "client_slug": "acme",
            "context_hash": "context-hash",
            "semantic_dependencies": {"rule_version": "rules-v1"},
            "providers": {"embeddings": {
                "owner": "tools.retrieval.dense",
                "kind": "existing_sqlite_sidecar",
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


def _add_review_notices(graph: dict) -> None:
    fit_hold = {
        "record_id": "NOTICE-FIT", "title": "Near-term adjacent requirement",
        "description": "Published instructional design requirement body.",
        "agency": "Department of the Navy", "sub_agency": "NAVSUP",
        "office": "Fleet Logistics Center", "naics": "611710", "psc": "U008",
        "notice_type": "Solicitation", "set_aside": "Total Small Business",
        "response_deadline": "2026-08-27", "evidence_class": "current_opportunity",
        "service_fit": "adjacent", "fit_basis": "approved adjacent capability",
        "window_state": "live", "commercial_route": "direct",
        "eligible_route": True, "route_basis": "eligible small-business route",
        "url": "https://sam.gov/opp/notice-fit/view",
        "requirement_family": "family-fit", "retrieved_at": "2026-08-23T09:00:00Z",
        "projection_decision": {
            "disposition": "needs_review",
            "reason_codes": ["FIT_ADJACENT_REVIEW"],
            "blocking_dimensions": ["service_fit"],
            "reasons": ["approved adjacent capability"],
            "decision_action": "Confirm the matched requirement scope before pursuit.",
        },
    }
    route_hold = {
        "record_id": "NOTICE-ROUTE", "title": "Unresolved-access language work",
        "description": "Published translation requirement with access unstated.",
        "agency": "Department of the Army", "naics": "541930", "psc": "R608",
        "notice_type": "Presolicitation", "set_aside": "",
        "response_deadline": "2026-08-25", "evidence_class": "current_opportunity",
        "service_fit": "direct", "fit_basis": "approved core capability",
        "window_state": "live", "commercial_route": "unknown",
        "eligible_route": False,
        "route_basis": "notice publishes no set-aside status; direct eligibility remains unresolved",
        "route_action": "verify", "pursuit_state": "pursue_verify",
        "qualification_state": "qualified",
        "projection_decision": {
            "disposition": "qualified",
            "reason_codes": [],
            "blocking_dimensions": [],
            "reasons": [],
            "decision_action": "Verify direct, vehicle, or teaming access with the buying office.",
        },
        "url": "https://sam.gov/opp/notice-route/view",
        "requirement_family": "family-route", "contact_email": "buyer@example.mil",
    }
    both_hold = {
        "record_id": "NOTICE-BOTH", "title": "Ambiguous restricted training",
        "description": "Instructional technology services.",
        "agency": "Department of the Navy", "naics": "611710", "psc": "U009",
        "notice_type": "Solicitation", "set_aside": "SDVOSB Set-Aside",
        "response_deadline": "2026-08-24", "evidence_class": "current_opportunity",
        "service_fit": "ambiguous", "fit_basis": "broad capability stem requires review",
        "window_state": "live", "commercial_route": "possible_subcontracting",
        "eligible_route": False,
        "route_basis": "access rule requires SDVOSB and bars direct pursuit",
        "url": "https://sam.gov/opp/notice-both/view",
        "requirement_family": "family-both",
        "projection_decision": {
            "disposition": "needs_review",
            "reason_codes": ["FIT_AMBIGUOUS"],
            "blocking_dimensions": ["service_fit"],
            "reasons": ["broad capability stem requires review"],
            "decision_action": "Resolve capability fit before pursuit.",
        },
    }
    graph["records"].extend((fit_hold, route_hold, both_hold))
    graph["qualified_opportunity_records"].append({
        **route_hold,
        "response_due": route_hold["response_deadline"],
        "source_url": route_hold["url"],
        "linked_targets": [],
        "technical_fit": {"basis": route_hold["fit_basis"]},
        "next_route": {"route_basis": route_hold["route_basis"]},
    })
    graph["held_opportunities"].extend((
        {"record_id": "NOTICE-FIT", "requirement_family": "family-fit",
         "qualification_state": "held_for_fit_review",
         "qualification_reason": "approved adjacent capability"},
        {"record_id": "NOTICE-BOTH", "requirement_family": "family-both",
         "qualification_state": "held_for_fit_review",
         "qualification_reason": "broad capability stem requires review"},
    ))


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
        for row in tuple(slot.records) + tuple(slot.review_records):
            if row.get("record_key"):
                assert row["record_key"] not in owners
                owners[row["record_key"]] = slot.slot_id
    priority = document.slots[0]
    assert priority.records[0]["reference_slot_id"] == "federal-opportunities"
    assert "record_key" not in priority.records[0]


def test_graph_identity_cannot_own_both_opportunity_and_teaming_slots():
    graph = _graph()
    graph["records"][0].update({
        "route_relationship": "named_partner_teaming",
        "commercial_route": "named_partner_teaming",
        "route_basis": "restricted notice names an incumbent teaming route",
    })
    graph["qualified_opportunity_records"][0].update({
        "route_relationship": "named_partner_teaming",
        "commercial_route": "named_partner_teaming",
        "route_basis": "restricted notice names an incumbent teaming route",
    })

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={"capability_terms": {"core": ["language services"]},
                 "inferred_naics": ["541930"]},
        client_name="Acme", slug="acme", as_of="2026-08-23")
    by_id = {slot.slot_id: slot for slot in document.slots}

    assert [row["source_id"] for row in
            by_id["federal-opportunities"].records] == ["NOTICE-1"]
    reference = next(row for row in by_id["teaming-opportunities"].records
                     if row["source_id"] == "NOTICE-1")
    assert reference["kind"] == "teaming_route_reference"
    assert reference["non_owning_reference"] is True
    assert record_ownership_key(reference) == ""
    assert reference["notice_reference_key"] == "graph-record:NOTICE-1"
    assert validate_external_product_document(document) == []


def test_multiple_teaming_references_survive_without_second_notice_owners():
    graph = _graph()
    first = graph["records"][0]
    first.update({
        "commercial_route": "possible_subcontracting",
        "route_relationship": "possible_subcontracting",
        "eligible_route": False,
        "route_action": "team",
        "pursuit_state": "pursue_team",
        "qualification_state": "qualified",
        "route_basis": "restricted access requires a partner",
        "projection_decision": {
            "disposition": "qualified",
            "reason_codes": [],
            "blocking_dimensions": [],
            "reasons": [],
            "decision_action": "Identify an eligible prime.",
        },
    })
    second = {
        **first,
        "record_id": "NOTICE-2",
        "title": "Second restricted language requirement",
        "requirement_family": "family-2",
        "url": "https://sam.gov/opp/notice-2/view",
    }
    graph["records"].append(second)
    graph["qualified_opportunity_records"] = [first, second]
    graph["held_opportunities"] = []
    graph["target_groups"] = {}

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    by_id = {slot.slot_id: slot for slot in document.slots}
    references = [row for row in by_id["teaming-opportunities"].records
                  if row.get("non_owning_reference")]

    assert {row["source_id"] for row in references} == {
        "NOTICE-1", "NOTICE-2"}
    assert all(record_ownership_key(row) == "" for row in references)
    assert validate_external_product_document(document) == []


def test_legacy_route_hold_is_not_silently_reclassified_by_projection():
    graph = _graph()
    row = graph["records"][0]
    row.update({
        "commercial_route": "unknown",
        "eligible_route": False,
        "route_action": "verify",
        "pursuit_state": "research_required",
        "projection_decision": {
            "disposition": "needs_review",
            "reason_codes": ["ROUTE_ELIGIBILITY_UNRESOLVED"],
            "blocking_dimensions": ["route_eligibility"],
            "reasons": ["legacy graph held route uncertainty"],
            "decision_action": "Rebuild under the current promotion law.",
        },
    })
    graph["qualified_opportunity_records"] = []
    graph["held_opportunities"] = [{
        "record_id": "NOTICE-1",
        "requirement_family": "family-1",
        "qualification_state": "needs_eligible_route",
        "projection_decision": row["projection_decision"],
    }]

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    opportunities = next(
        slot for slot in document.slots
        if slot.slot_id == "federal-opportunities")

    assert opportunities.records == ()
    assert [item["source_id"] for item in opportunities.review_records] == [
        "NOTICE-1"]
    assert document.graph_receipt["pursuit_membership_conserved"] is True
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
    assert [row["source_id"] for row in
            by_id["competitors"].review_records] == ["AWARD-RIVAL-REVIEW"]
    assert by_id["competitors"].review_records[0]["decision_state"] == \
        "needs_review"
    assert by_id["competitors"].review_records[0]["reason_codes"] == [
        "FIT_AMBIGUOUS"]
    assert by_id["competitors"].coverage["review_required"] == 1
    assert by_id["competitors"].coverage["out_of_scope"] == 0
    assert [row["source_id"] for row in by_id["agency-spending"].records] == [
        "AWARD-CLIENT"]
    assert [row["source_id"] for row in
            by_id["agency-spending"].review_records] == ["AWARD-CLIENT-OTHER"]
    assert by_id["agency-spending"].review_records[0]["decision_state"] == \
        "out_of_scope"
    assert by_id["agency-spending"].review_records[0]["reason_codes"] == [
        "FIT_OUT_OF_SCOPE"]
    assert by_id["agency-spending"].coverage["review_required"] == 0
    assert by_id["agency-spending"].coverage["out_of_scope"] == 1
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


def test_forecast_priority_link_targets_its_exact_owner_record(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    graph = _graph()
    graph["qualified_opportunity_records"] = []
    graph["records"][0]["evidence_class"] = "excluded"
    graph["records"][0]["service_fit"] = "unrelated"
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    _studio, client = render_external_product(document)
    match = re.search(
        r'class="product-priority-link" href="(#[^"]+)"', client)

    assert match is not None
    owner_href = match.group(1)
    assert owner_href.startswith("#record-")
    assert f'id="{owner_href[1:]}"' in client
    assert validate_external_product_html(client, document)["ok"] is True

    wrong = client.replace(owner_href, "#future-forecasts", 1)
    broken = validate_external_product_html(wrong, document)
    assert "priority_owner_link" in {
        row["rule"] for row in broken["violations"]}


def test_current_opportunity_holds_are_lossless_owned_and_decision_typed():
    graph = _graph()
    _add_review_notices(graph)

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    by_id = {slot.slot_id: slot for slot in document.slots}
    opportunities = by_id["federal-opportunities"]

    assert [row["source_id"] for row in opportunities.records] == [
        "NOTICE-ROUTE", "NOTICE-1"]
    route_pursuit = next(
        row for row in opportunities.records
        if row["source_id"] == "NOTICE-ROUTE")
    assert route_pursuit["route_action"] == "verify"
    assert route_pursuit["pursuit_state"] == "pursue_verify"
    assert "Verify direct" in route_pursuit["next_action"]
    assert route_pursuit["contact_email"] == "buyer@example.mil"
    assert [row["source_id"] for row in opportunities.review_records] == [
        "NOTICE-BOTH", "NOTICE-FIT"]
    reviews = {row["source_id"]: row for row in opportunities.review_records}
    assert reviews["NOTICE-FIT"]["summary"] == \
        "Published instructional design requirement body."
    assert reviews["NOTICE-FIT"]["naics"] == "611710"
    assert reviews["NOTICE-FIT"]["source_url"] == \
        "https://sam.gov/opp/notice-fit/view"
    assert reviews["NOTICE-FIT"]["reason_codes"] == ["FIT_ADJACENT_REVIEW"]
    assert reviews["NOTICE-FIT"]["projection_decision"] == \
        graph["records"][-3]["projection_decision"]
    assert reviews["NOTICE-BOTH"]["reason_codes"] == ["FIT_AMBIGUOUS"]
    assert reviews["NOTICE-BOTH"]["projection_decision"] == \
        graph["records"][-1]["projection_decision"]
    assert reviews["NOTICE-BOTH"]["blocking_dimensions"] == ["service_fit"]
    assert all(row["decision_state"] == "needs_review"
               and row["reasons"] and row["decision_action"]
               for row in opportunities.review_records)
    assert document.graph_receipt["current_opportunity_conserved"] is True
    assert document.graph_receipt["current_opportunity_ids"] == [
        "NOTICE-1", "NOTICE-BOTH", "NOTICE-FIT", "NOTICE-ROUTE"]
    assert validate_external_product_document(document) == []

    actions = by_id["priority-pursuits"].records
    assert [row["title"] for row in actions] == [
        "Unresolved-access language work", "Language Support Services",
        "Ambiguous restricted training", "Near-term adjacent requirement"]
    assert [row["reference_kind"] for row in actions] == [
        "qualified_action", "qualified_action", "decision_required",
        "decision_required"]
    assert [row["action_order"] for row in actions] == [1, 2, None, None]
    assert [row["decision_order"] for row in actions] == [None, None, 1, 2]
    decision_required_text = repr([
        row for row in actions
        if row["reference_kind"] == "decision_required"
    ]).casefold()
    assert "ranked" not in decision_required_text
    assert "pursued" not in decision_required_text


def test_validation_rejects_a_current_opportunity_removed_from_review_queue():
    graph = _graph()
    _add_review_notices(graph)
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    slot = next(item for item in document.slots
                if item.slot_id == "federal-opportunities")
    broken_slot = replace(slot, review_records=slot.review_records[1:])
    broken = replace(document, slots=tuple(
        broken_slot if item.slot_id == "federal-opportunities" else item
        for item in document.slots))

    assert "current_opportunity_conservation" in {
        row["rule"] for row in validate_external_product_document(broken)}


def test_renderer_separates_review_queue_and_renders_decision_targets(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    graph = _graph()
    _add_review_notices(graph)
    graph["target_groups"]["family-route"] = [{
        "requirement_family": "family-route",
        "opportunity_record_id": "NOTICE-ROUTE",
        "role": "contracting_officer_or_specialist",
        "name": "Route Buyer",
        "organization": "Department of the Army",
        "email": "buyer@example.mil",
        "source_kind": "published_contact",
        "target_purpose": "decision_resolution",
        "target_slot_id": "federal-opportunities",
    }]
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    _studio, client = render_external_product(document)

    assert "Needs review before pursuit" in client
    assert "These current notices remain visible" in client
    assert client.count(
        'class="product-opportunity-card product-opportunity-review"') == 2
    assert "Decision required" in client
    assert "They are not pursuit recommendations" in client
    assert '<details class="product-ledger product-review-queue" open>' in client
    assert client.count("Full evidence record") >= 2
    assert 'class="product-priority-link"' in client
    assert 'href="#opportunity-' in client
    assert "graph-record:" not in client
    assert "Pursuit actions" in client
    assert "Fit decisions required" in client
    pursuit_html = client.split(
        '<section class="plain-section product-slot" '
        'id="federal-opportunities" data-slot-id="federal-opportunities"',
        1,
    )[1].split("Needs review before pursuit", 1)[0]
    assert "Targets specific to this opportunity" in pursuit_html
    assert "Route Buyer" in pursuit_html


def test_opportunity_cards_share_one_locked_structure_and_reject_tampering(
        monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    graph = _graph()
    _add_review_notices(graph)
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    _studio, client = render_external_product(document)
    verdict = validate_external_product_html(client, document)
    opportunity_slot = next(
        slot for slot in document.slots
        if slot.slot_id == "federal-opportunities")
    expected = (
        len(opportunity_slot.records) + len(opportunity_slot.review_records))

    assert verdict["ok"], verdict["violations"]
    assert verdict["opportunity_card_contract"] == {
        "schema_version": OPPORTUNITY_CARD_VERSION,
        "expected_cards": expected,
        "rendered_cards": expected,
        "agency_seal_policy": "required_exactly_one",
        "rendered_agency_seals": expected,
        "cards_without_agency_seal": 0,
        "rendered_official_source_actions": expected * 2,
        "priority_links": len(document.slots[0].records),
        "owned_opportunities": len(opportunity_slot.records),
        "review_opportunities": len(opportunity_slot.review_records),
    }
    assert OPPORTUNITY_CARD_REGION_ORDER == (
        "status-rail", "identity", "headline", "decision", "acquisition",
        "decision-state", "intelligence", "targets", "actions", "evidence",
    )
    assert OPPORTUNITY_CARD_FIELD_ORDER == (
        "posture", "response-due", "rail-notice-type", "fit", "access",
        "status", "notice-type", "set-aside", "naics", "psc",
        "solicitation-number", "published-value",
        "evidence-read", "route-basis", "next-action",
    )
    assert client.count(
        f'data-opportunity-card-contract="{OPPORTUNITY_CARD_VERSION}"') \
        == expected
    for field in (
            "posture", "response-due", "rail-notice-type", "fit", "access",
            "status", "notice-type", "set-aside", "naics", "psc",
            "solicitation-number", "published-value",
            "evidence-read", "route-basis", "next-action"):
        assert client.count(f'data-card-field="{field}"') == expected
    assert '<article class="product-record product-priority' not in client
    assert client.count('class="product-priority-link"') == len(
        document.slots[0].records)
    assert 'data-card-context="priority-reference"' not in client
    assert "Solicitation number not published ↗" in client
    assert client.count('data-card-field="published-value"') == expected
    assert ('data-card-field="solicitation-number"><b>Solicitation number</b>'
            'Not published</div>') in client
    assert ('data-card-field="published-value"><b>Published value</b>'
            'Not published</div>') in client

    removed_source_action = client.replace(
        ' data-card-action="official-source"', '', 1)
    broken = validate_external_product_html(removed_source_action, document)
    assert "opportunity_card_source_action_count" in {
        row["rule"] for row in broken["violations"]}

    first_card = (
        f'data-opportunity-card-contract="{OPPORTUNITY_CARD_VERSION}"')
    before_card, card_and_after = client.split(first_card, 1)
    removed_seal = before_card + first_card + card_and_after.replace(
        ' data-agency-seal="true"', '', 1)
    broken = validate_external_product_html(removed_seal, document)
    assert "opportunity_card_agency_seal_count" in {
        row["rule"] for row in broken["violations"]}
    historical = validate_external_product_html(
        removed_seal, document, require_opportunity_agency_seal=False)
    assert historical["ok"], historical["violations"]
    assert historical["opportunity_card_contract"]["agency_seal_policy"] == (
        "recorded_only")
    assert historical["opportunity_card_contract"][
        "cards_without_agency_seal"] == 1

    tampered = client.replace(
        'data-card-field="psc"', 'data-card-field="psc-removed"', 1)
    broken = validate_external_product_html(tampered, document)
    assert "opportunity_card_fields" in {
        row["rule"] for row in broken["violations"]}

    owner_title = str(opportunity_slot.records[0]["title"])
    wrong_title = client.replace(
        f"<h3>{owner_title}</h3>", "<h3>Wrong opportunity title</h3>", 1)
    broken = validate_external_product_html(wrong_title, document)
    assert "opportunity_card_title" in {
        row["rule"] for row in broken["violations"]}

    source_url = str(opportunity_slot.records[0]["source_url"])
    wrong_source = client.replace(
        f'href="{source_url}"', 'href="https://sam.gov/opp/wrong/view"', 1)
    broken = validate_external_product_html(wrong_source, document)
    assert "opportunity_card_source_binding" in {
        row["rule"] for row in broken["violations"]}

    naics = str(opportunity_slot.records[0]["naics"])
    wrong_naics = client.replace(
        f'<div data-card-field="naics"><b>NAICS</b>{naics}</div>',
        '<div data-card-field="naics"><b>NAICS</b>999999</div>',
        1,
    )
    broken = validate_external_product_html(wrong_naics, document)
    assert "opportunity_card_content" in {
        row["rule"] for row in broken["violations"]}

    owner_link = re.search(
        r'class="product-priority-link" href="(#[^"]+)"', client)
    assert owner_link is not None
    destination = owner_link.group(1)[1:]
    missing_destination = client.replace(
        f'id="{destination}"', f'data-removed-id="{destination}"', 1)
    broken = validate_external_product_html(missing_destination, document)
    assert "priority_owner_destination" in {
        row["rule"] for row in broken["violations"]}

    wrong_card_slot = client.replace(
        'id="federal-opportunities" data-slot-id="federal-opportunities"',
        'id="federal-opportunities" data-slot-id="teaming-opportunities"',
        1,
    )
    broken = validate_external_product_html(wrong_card_slot, document)
    assert "opportunity_card_slot" in {
        row["rule"] for row in broken["violations"]}

    wrong_link_slot = client.replace(
        'id="priority-pursuits" data-slot-id="priority-pursuits"',
        'id="priority-pursuits" data-slot-id="agency-spending"',
        1,
    )
    broken = validate_external_product_html(wrong_link_slot, document)
    assert "priority_link_slot" in {
        row["rule"] for row in broken["violations"]}


def test_renderer_exposes_prime_team_and_verify_pursuit_paths(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    graph = _graph()
    base = copy.deepcopy(graph["records"][0])
    for action, route, eligible in (
            ("team", "possible_subcontracting", False),
            ("verify", "unknown", False)):
        row = {
            **copy.deepcopy(base),
            "record_id": f"NOTICE-{action.upper()}",
            "title": f"{action.title()} language requirement",
            "requirement_family": f"family-{action}",
            "url": f"https://sam.gov/opp/notice-{action}/view",
            "commercial_route": route,
            "eligible_route": eligible,
            "route_action": action,
            "pursuit_state": f"pursue_{action}",
        }
        graph["records"].append(row)
        graph["qualified_opportunity_records"].append({
            **row,
            "source_url": row["url"],
            "response_due": row["response_deadline"],
            "technical_fit": {"basis": row["fit_basis"]},
            "linked_targets": [],
        })

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    _studio, client = render_external_product(document)

    assert validate_external_product_document(document) == []
    assert client.count("Prime path") >= 2
    assert client.count("Teaming required") >= 2
    assert client.count("Verify access") >= 2
    assert "Prime-ready pursuit" in client
    assert "Teaming-path pursuit" in client
    assert "Route verification required" in client
    assert "pursue through an eligible prime or named partner" in client
    assert "confirm eligibility and acquisition access" in client
    assert "Service fit and route passed" not in client
    assert "1 prime; 1 team; 1 verify route" in client
    assert validate_external_product_html(client, document)["ok"] is True


def test_priority_reference_state_must_match_its_owned_record():
    document = _document()
    priority = document.slots[0]
    forged_row = {**priority.records[0],
                  "reference_kind": "decision_required",
                  "decision_state": "needs_review"}
    forged_priority = replace(priority, records=(forged_row,))
    forged = replace(document, slots=(forged_priority,) + document.slots[1:])

    assert "decision_reference_state" in {
        row["rule"] for row in validate_external_product_document(forged)}


def test_nonqualified_award_ledgers_are_collapsed_but_lossless(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    graph = _graph()
    graph["records"].extend((
        {"record_id": "AWARD-RIVAL-REVIEW", "title": "",
         "recipient": "Rival Inc", "agency": "Department of State",
         "evidence_class": "competitive_historical",
         "service_fit": "ambiguous", "commercial_route": "incumbent",
         "description": "Preserved competitive evidence body.",
         "url": "https://www.usaspending.gov/award/AWARD-RIVAL-REVIEW"},
        {"record_id": "AWARD-CLIENT-OTHER", "title": "AWARD-CLIENT-OTHER",
         "recipient": "Acme", "agency": "Department of the Navy",
         "evidence_class": "client_historical", "service_fit": "unrelated",
         "commercial_route": "direct",
         "description": "Preserved out-of-scope evidence body.",
         "url": "https://www.usaspending.gov/award/AWARD-CLIENT-OTHER"},
    ))
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    _studio, client = render_external_product(document)

    assert client.count(
        '<details class="product-ledger product-review-queue">') == 2
    assert "Preserved competitive evidence body." in client
    assert "Preserved out-of-scope evidence body." in client
    assert "Rival Inc - Department of State" in client
    assert "Acme - Department of the Navy" in client
    assert validate_external_product_html(client, document)["ok"] is True


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
    graph["records"].append(earlier)

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    actions = document.slots[0].records

    assert [row["title"] for row in actions] == [
        "Earlier Interpreter Requirement", "Language Support Services"]
    assert [row["action_order"] for row in actions] == [1, 2]
    assert all("priority" not in row and row["reference_kind"] == "qualified_action"
               for row in actions)


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
    assert scope["fields"][0]["value"] == full_scope


def test_raw_and_canonical_record_counts_are_both_explicit():
    graph = _graph()
    graph["records"].append({
        **graph["records"][2], "obligated_dollars": 500_000,
    })

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    assert document.source_receipt["graph_input_records_raw"] == 6
    assert document.source_receipt["graph_identity_canonical_records"] == 6
    assert document.source_receipt["graph_requirement_duplicates_collapsed"] == 0
    assert document.source_receipt["graph_final_records"] == 6
    assert document.source_receipt["graph_projection_canonical_records"] == 5
    assert document.source_receipt["graph_indexed_records"] == 5
    cache = document.graph_receipt["incremental_cache_receipt"]
    assert cache["schema_version"] == \
        "incremental-cache-semantic-receipt-v1"
    assert cache["providers"]["embeddings"] == {
        "owner": "tools.retrieval.dense",
        "kind": "existing_sqlite_sidecar",
    }
    assert "cache_root" not in cache
    assert "exists" not in repr(cache)
    assert "path" not in repr(cache)


def test_slot_two_does_not_treat_provider_existence_as_a_run_receipt():
    graph = _graph()
    graph["incremental_cache_receipt"]["providers"]["embeddings"].update({
        "exists": True,
        "path": "/Users/example/private/cache/dense_vectors.db",
    })
    pack = _pack()
    pack.queries = [SimpleNamespace(
        lane="L1_notice", method="BM25 full-text search",
        endpoint="https://sam.gov/api", body={"keywords": ["language"]},
        result_count=10, kept_after_screen=2, note="text-only run")]

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=pack,
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")
    slot = next(row for row in document.slots
                if row.slot_id == "research-mesh")
    receipt_metric = next(row for row in slot.metrics
                          if row["label"] == "Semantic retrieval receipt")

    assert receipt_metric["value"] == "not captured"
    assert any("no run-bound dense/vector receipt" in gap
               for gap in slot.gaps)


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
    assert "Actionable pursuit" in client
    assert "Ranked pursuit" not in client
    assert "Qualified category obligations: $1,200,000.00 = $1,200,000.00" in client
    assert "@page{size:letter;margin:.42in}" in client
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in client
    assert "break-inside:avoid;page-break-inside:avoid;overflow:hidden" in client
    assert 'class="product-ledger product-research-ledger"' in client
    assert "Replayable query receipt" in client
    assert ".product-detail, .product-review-evidence {display:none!important}" in client


def test_renderer_freezes_and_enforces_typed_agency_identity(monkeypatch):
    from agents.reports import report_assets

    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    monkeypatch.setattr(
        report_assets, "agency_seal",
        lambda _label, _client=None: _mark())
    document = _document()
    identity_assets = capture_external_product_identity_assets(document)

    assert identity_assets["agency_marks"]["agency:dos"] == _mark()
    assert identity_assets["organization_marks"] == {}
    _studio, client = render_external_product(document)
    verdict = validate_external_product_html(client, document)
    assert verdict["ok"], verdict["violations"]
    assert 'data-brand-kind="agency" data-brand-key="agency:dos"' in client
    assert 'data-identity-mark="seal" data-agency-seal="true"' in client

    tampered = client.replace(
        'data-brand-kind="agency"', 'data-brand-kind="company"', 1)
    broken = validate_external_product_html(tampered, document)
    assert "record_identity_kind" in {
        row["rule"] for row in broken["violations"]}


def test_event_organizer_never_inherits_a_federal_seal(monkeypatch):
    from agents.reports import report_assets

    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    document = _document()
    event_slot = next(slot for slot in document.slots
                      if slot.slot_id == "industry-days-events")
    event = {
        **event_slot.records[0],
        "agency": "Navy League",
        "sub_agency": "",
        "title": "Navy League event",
    }
    event_slot = replace(event_slot, records=(event,))
    document = replace(document, slots=tuple(
        event_slot if slot.slot_id == event_slot.slot_id else slot
        for slot in document.slots))

    _studio, client = render_external_product(document)
    verdict = validate_external_product_html(client, document)

    assert verdict["ok"], verdict["violations"]
    assert ('data-brand-kind="company" '
            'data-brand-key="company:navy_league"') in client
    assert 'data-brand-key="agency:navy"' not in client
    assert 'data-brand-label="Navy League" data-identity-mark="fallback"' \
        in client


@pytest.mark.parametrize(("injection", "expected_rule"), (
    ("<script>alert(1)</script>", "script_in_client_artifact"),
    ('<iframe src="https://attacker.example/"></iframe>',
     "executable_tag_in_client_artifact"),
    ('<div onpointerenter="alert(1)">unsafe</div>',
     "event_handler_in_client_artifact"),
    ('<div srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"></div>',
     "srcdoc_in_client_artifact"),
    ('<a href="java&#x0a;script:alert(1)">unsafe</a>',
     "active_url_in_client_artifact"),
    ('<meta http-equiv="refresh" content="0;url=https://attacker.example/">',
     "meta_refresh_in_client_artifact"),
    ('<style>@import url(https://attacker.example/x.css);</style>',
     "active_css_in_client_artifact"),
    ('<div style="background-image:url(jav&#x61;script:alert(1))">unsafe</div>',
     "active_css_in_client_artifact"),
    (r'<style>.x{background:url(j\61vascript:alert(1))}</style>',
     "active_css_in_client_artifact"),
    ('<svg><path fill="url(https://attacker.example/f.svg)"></path></svg>',
     "active_css_in_client_artifact"),
    ('<svg><image href="https://attacker.example/tracker.png"></image></svg>',
     "active_url_in_client_artifact"),
    ('<svg><use href="https://attacker.example/icons.svg#mark"></use></svg>',
     "active_url_in_client_artifact"),
    ('<style>.x{background-image:image-set("https://attacker.example/x.png" 1x)}</style>',
     "active_css_in_client_artifact"),
    ("<div style=\"background-image:-webkit-image-set('https://attacker.example/x.png' 1x)\"></div>",
     "active_css_in_client_artifact"),
    ('<svg><animate attributeName="href" values="javascript:alert(1)"></animate></svg>',
     "active_svg_mutation_in_client_artifact"),
    ('<svg><set attributeName="href" to="javascript:alert(1)"></set></svg>',
     "active_svg_mutation_in_client_artifact"),
))
def test_external_html_validator_rejects_active_tampering(
        monkeypatch, injection, expected_rule):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    document = _document()
    _studio, client = render_external_product(document)
    tampered = client.replace("</body>", injection + "</body>")

    verdict = validate_external_product_html(tampered, document)

    assert verdict["ok"] is False
    assert expected_rule in {row["rule"] for row in verdict["violations"]}


def test_external_html_validator_preserves_safe_links_fragments_and_marks(
        monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    document = _document()
    _studio, client = render_external_product(document)
    safe = (
        '<a href="https://example.gov/record">Federal record</a>'
        '<a href="#priority-pursuits">Priority pursuits</a>'
        '<svg><use href="#productArrow"></use></svg>'
        f'<img alt="Acme supplemental mark" src="{_mark()}">')
    supplemented = client.replace("</body>", safe + "</body>")

    verdict = validate_external_product_html(supplemented, document)

    assert verdict["ok"], verdict["violations"]


def test_renderer_escapes_a_hostile_dynamic_record_title(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    graph = _graph()
    hostile = 'Language <img src=x onerror="alert(1)"> Support'
    graph["records"][0]["title"] = hostile
    graph["qualified_opportunity_records"][0]["title"] = hostile
    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    _studio, client = render_external_product(document)
    verdict = validate_external_product_html(client, document)

    assert hostile not in client
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in client
    assert verdict["ok"], verdict["violations"]


def test_web_url_home_path_is_not_misclassified_as_a_local_path():
    graph = _graph()
    graph["records"][0]["url"] = "https://agency.gov/home/opportunities/notice-1"
    graph["qualified_opportunity_records"][0]["url"] = \
        "https://agency.gov/home/opportunities/notice-1"
    graph["qualified_opportunity_records"][0]["source_url"] = \
        "https://agency.gov/home/opportunities/notice-1"

    document = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-08-23")

    assert not any(
        row["rule"] == "local_path"
        for row in validate_external_product_document(document))


def test_each_slot_summary_renders_once():
    slot = _document().slots[0]

    rendered = render_slot(slot)

    assert rendered.count(slot.summary) == 1
    assert "product-slot-summary" not in rendered


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
        state = page.evaluate(r"""() => {
          const style = (selector) => getComputedStyle(document.querySelector(selector));
          return {
            recordDisplay: style('[data-slot-id="research-mesh"] .product-records').display,
            recordColumns: (() => {
              const value = style('[data-slot-id="research-mesh"] .product-records').gridTemplateColumns;
              const repeated = value.match(/^repeat\((\d+),/);
              return repeated ? Number(repeated[1]) : value.split(' ').length;
            })(),
            visualBreak: style('.product-visual').breakInside,
            introBreak: style('.product-slot-intro').breakInside,
            duplicateSummaryPresent: Boolean(document.querySelector('.product-slot-summary')),
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
        "duplicateSummaryPresent": False,
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
