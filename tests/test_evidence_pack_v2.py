from __future__ import annotations

import json

import agents.golden_press.evidence_pack_v2 as evidence_pack_v2

from agents.golden_press.evidence_pack_v2 import (
    build_move_receipt,
    build_target_groups,
    canonicalize_record_ids,
    canonicalize_requirement_families,
    qualify_opportunities,
    validate_graph_contract,
)
from agents.golden_press.market_map_projection import (
    Opportunity,
    attach_v2_targets,
    opportunity_key,
)


def _opportunity(record_id: str, family: str, title: str, agency: str,
                 *, name: str = "", email: str = "") -> dict:
    return {
        "record_id": record_id,
        "requirement_family": family,
        "title": title,
        "agency": agency,
        "evidence_class": "current_opportunity",
        "contact_name": name,
        "contact_email": email,
    }


def test_requirement_family_collapses_lifecycle_postings():
    rows = [
        {"record_id": "ss-1", "title": "Sources Sought Language Services",
         "agency": "Department of State", "window_state": "live"},
        {"record_id": "sol-1", "title": "Solicitation Language Services",
         "agency": "Department of State", "window_state": "live",
         "response_deadline": "2026-09-25", "contact_email": "co@state.gov"},
    ]
    kept = canonicalize_requirement_families(rows)
    assert len(kept) == 1
    assert kept[0]["canonical_record_id"] == "sol-1"
    assert kept[0]["family_member_ids"] == ["sol-1", "ss-1"]


def test_record_identity_collapses_duplicate_award_and_receipts_conflict():
    record_id = "CONT_AWD_FA855526FB003_9700_FA855526DB003_9700"
    rows = [
        {"record_id": record_id, "title": "Language support",
         "obligated_dollars": 2_896_292.43, "recipient": "Provider",
         "period_start": "2025-10-01", "period_end": "2026-09-30",
         "url": "https://example.gov/partial"},
        {"record_id": record_id, "title": "Language support",
         "obligated_dollars": 4_213_817.34},
        {"record_id": "other", "title": "Other award"},
    ]

    canonical, receipt = canonicalize_record_ids(rows)

    assert [row["record_id"] for row in canonical] == [record_id, "other"]
    assert canonical[0]["obligated_dollars"] == 4_213_817.34
    assert receipt["input_records_raw"] == 3
    assert receipt["canonical_records"] == 2
    assert receipt["record_duplicates_collapsed"] == 1
    assert receipt["duplicate_record_ids"][0]["conflicts"][
        "obligated_dollars"] == [2_896_292.43, 4_213_817.34]


def test_vertical_target_path_keeps_sources_distinct_and_removal_cascades():
    opportunities = [
        _opportunity("jtg-1", "fam-1", "Embassy Language Instructors",
                     "Department of State", name="Pat Published",
                     email="pat@state.gov"),
        _opportunity("jtg-2", "fam-2", "DLITE III",
                     "Department of the Army"),
        _opportunity("jtg-3", "fam-3", "Interpreter Services BPA",
                     "Department of the Navy"),
    ]
    target_roles = {
        "fam-2": [{
            "role": "program_or_mission_owner",
            "organization": "Department of the Army",
            "role_needed": "DLITE III program owner",
        }],
    }
    enrichments = [{
        "requirement_family": "fam-2",
        "role": "program_or_mission_owner",
        "name": "Alex Enriched",
        "title": "Program Director",
        "organization": "Department of the Army",
        "email": "alex@example.mil",
        "phone": "+1 202 555 0101",
        "linkedin": "https://www.linkedin.com/in/alex-enriched",
        "provenance": "apollo_enrichment:operator_approved:2026-08-21",
    }]
    groups = build_target_groups(
        opportunities,
        enrichments=enrichments,
        target_roles=target_roles,
    )

    published = [row for row in groups["fam-1"]
                 if row["source_kind"] == "published_contact"]
    enriched = [row for row in groups["fam-2"]
                if row["source_kind"] == "apollo_enrichment"]
    candidates = [row for rows in groups.values() for row in rows
                  if row["source_kind"] == "enrichment_candidate"]
    assert published[0]["email"] == "pat@state.gov"
    assert enriched[0]["phone"] == "+1 202 555 0101"
    assert published[0]["source_kind"] != enriched[0]["source_kind"]
    assert all(row["name"] is None and row["email"] is None and
               row["phone"] is None for row in candidates)
    assert all(row["opportunity_record_id"] and row["opportunity_title"]
               for rows in groups.values() for row in rows)

    projected = tuple(
        Opportunity(
            key=opportunity_key("notice", row["record_id"]),
            identifier=row["record_id"],
            source_kind="notice",
            title=row["title"],
        ) for row in opportunities
    )
    payload = {
        "qualified_opportunity_records": opportunities,
        "target_groups": groups,
    }
    attached = attach_v2_targets(projected, payload)
    assert [len(row.linked_targets) for row in attached] == [2, 2, 1]

    payload_without_second = {
        **payload,
        "qualified_opportunity_records": [
            row for row in opportunities if row["record_id"] != "jtg-2"
        ],
    }
    without_second = attach_v2_targets(projected, payload_without_second)
    assert [row.identifier for row in without_second] == ["jtg-1", "jtg-3"]
    assert {target["opportunity_record_id"]
            for row in without_second for target in row.linked_targets} == {
                "jtg-1", "jtg-3"}


def test_v2_population_is_authoritative_and_constructs_missing_rows():
    qualified = [{
        "record_id": "qualified-1",
        "requirement_family": "family-qualified",
        "title": "Language Services Requirement",
        "agency": "Department of State",
        "office": "Acquisition Office",
        "instrument": "Presolicitation",
        "response_due": "2026-09-25",
        "source_url": "https://sam.gov/opp/qualified-1/view",
        "evidence_class": "current_opportunity",
        "commercial_route": "direct",
        "window_state": "live",
        "technical_fit": {"basis": "explicit language-services scope"},
    }]
    groups = {"family-qualified": [
        {
            "opportunity_record_id": "qualified-1",
            "opportunity_title": "Language Services Requirement",
            "source_kind": "published_contact",
            "name": "Pat Published",
            "email": "pat@state.gov",
            "phone": None,
            "role": "Contracting Officer",
            "provenance": "sam_notice:qualified-1:primary_contact",
        },
        {
            "opportunity_record_id": "qualified-1",
            "opportunity_title": "Language Services Requirement",
            "source_kind": "apollo_enrichment",
            "name": "Alex Enriched",
            "email": "alex@example.mil",
            "phone": "+1 202 555 0101",
            "role": "Program Director",
            "provenance": "apollo_enrichment:operator_approved:2026-08-21",
        },
        {
            "opportunity_record_id": "qualified-1",
            "opportunity_title": "Language Services Requirement",
            "source_kind": "enrichment_candidate",
            "name": None,
            "email": None,
            "phone": None,
            "role": "Requirement owner",
            "provenance": "derived_role:family-qualified:requirement_owner",
        },
    ]}
    legacy = (Opportunity(
        key=opportunity_key("notice", "legacy-only"),
        identifier="legacy-only", source_kind="notice",
        title="Legacy-only record",
    ),)

    attached = attach_v2_targets(legacy, {
        "qualified_opportunity_records": qualified,
        "target_groups": groups,
    })

    assert [row.identifier for row in attached] == ["qualified-1"]
    assert attached[0].evidence[0].source_url == \
        "https://sam.gov/opp/qualified-1/view"
    assert {row["source_kind"] for row in attached[0].linked_targets} == {
        "published_contact", "apollo_enrichment", "enrichment_candidate"}
    assert [row["name"] for row in attached[0].contacts] == ["Pat Published"]


def test_moved_record_receipt_has_before_after_counts():
    records = [{
        "lane": "L2_entity_award",
        "record_id": "award-1",
        "recipient": "JTG, INC.",
        "evidence_class": "client_historical",
        "commercial_route": "direct",
        "evidence_basis": "recipient resolves to client alias",
    }]
    moved, totals = build_move_receipt(
        records, rendered_competitor_names={"JTG, INC."})
    assert moved[0]["prior_evidence_class"] == "competitive_historical"
    assert moved[0]["new_evidence_class"] == "client_historical"
    assert totals["competitive_historical"] == {"before": 1, "after": 0}
    assert totals["client_historical"] == {"before": 0, "after": 1}


def test_opportunity_decision_records_every_blocking_dimension(monkeypatch):
    row = {
        "record_id": "notice-dual-hold",
        "requirement_family": "family-dual-hold",
        "evidence_class": "current_opportunity",
    }
    monkeypatch.setattr(
        evidence_pack_v2, "_technical_fit",
        lambda *_args: {
            "fit": False,
            "fit_class": "ambiguous",
            "basis": "broad capability stem requires review",
        })
    monkeypatch.setattr(
        evidence_pack_v2, "_eligibility",
        lambda *_args: {
            "eligible_route": False,
            "basis": "restricted set-aside bars direct pursuit",
        })
    monkeypatch.setattr(
        evidence_pack_v2.er, "classify_route",
        lambda *_args: {
            "commercial_route": "possible_subcontracting",
            "eligible_route": False,
            "route_basis": "restricted set-aside bars direct pursuit",
        })

    qualified, _incumbents, held = qualify_opportunities([row], {})

    assert qualified == []
    assert held == [row]
    decision = row["projection_decision"]
    assert decision["disposition"] == "needs_review"
    assert decision["reason_codes"] == [
        "FIT_AMBIGUOUS", "DIRECT_ROUTE_INELIGIBLE"]
    assert decision["blocking_dimensions"] == [
        "service_fit", "route_eligibility"]
    assert decision["reasons"] == [
        "broad capability stem requires review",
        "restricted set-aside bars direct pursuit",
    ]
    assert row["qualification_state"] == "held_for_fit_review"


def test_graph_contract_requires_exhaustive_disjoint_opportunity_partition():
    current = {
        "record_id": "notice-held",
        "requirement_family": "family-held",
        "evidence_class": "current_opportunity",
        "commercial_route": "unknown",
        "eligible_route": False,
        "window_state": "live",
    }
    payload = {
        "records": [current],
        "classification_context": {
            "client_aliases": [], "named_partners": []},
        "canonical_requirement_families": [{
            "requirement_family": "family-held"}],
        "qualified_opportunity_records": [],
        "held_opportunities": [{
            "record_id": "notice-held",
            "requirement_family": "family-held",
            "qualification_state": "needs_eligible_route",
        }],
        "target_groups": {},
    }
    assert not any(
        row["rule_id"] == "G008_CURRENT_OPPORTUNITY_PARTITION"
        for row in validate_graph_contract(payload))

    payload["held_opportunities"] = []
    violation = next(
        row for row in validate_graph_contract(payload)
        if row["rule_id"] == "G008_CURRENT_OPPORTUNITY_PARTITION")
    assert violation["evidence"]["missing"] == ["notice-held"]


def test_schema_pins_the_graph_contract():
    path = ("agents/golden_press/schemas/evidence_pack_v2.schema.json")
    schema = json.loads(open(path, encoding="utf-8").read())
    assert schema["properties"]["schema_version"]["const"] == \
        "evidence-pack-v2"
    assert "canonical_entities" in schema["required"]
    assert "canonical_requirement_families" in schema["required"]
    assert "incremental_cache_receipt" in schema["required"]
    assert "workflow_contract" in schema["required"]
    assert "research_queue" in schema["required"]
    assert "graph_contract_certified" in schema["required"]
    assert "graph_contract_violations" in schema["required"]
    record = schema["$defs"]["classifiedRecord"]["properties"]
    for key in ("canonical_entity", "requirement_family", "evidence_class",
                "commercial_route", "window_state",
                "relationship_provenance", "projection_decision"):
        assert key in record
    decision = schema["$defs"]["projectionDecision"]
    assert decision["properties"]["disposition"]["enum"] == [
        "qualified", "needs_review"]
    queue_item = schema["$defs"]["researchQueueItem"]
    assert "ambiguous_dimensions" in queue_item["required"]
    assert queue_item["properties"]["ambiguous_dimensions"]["minItems"] == 1
