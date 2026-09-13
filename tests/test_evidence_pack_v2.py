from __future__ import annotations

import json

from agents.golden_press.evidence_pack_v2 import (
    build_move_receipt,
    build_target_groups,
    canonicalize_requirement_families,
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
    for i, row in enumerate(rows):
        row.update(solicitation="LANG-01", office="Acquisition", posted_date=f"2026-08-0{i+1}")
    kept = canonicalize_requirement_families(rows)
    assert len(kept) == 1
    assert kept[0]["canonical_record_id"] == "sol-1"
    assert kept[0]["family_member_ids"] == ["ss-1", "sol-1"]


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
    assert attached == (), "saved target groups cannot authorize current opportunities"

    payload_without_second = {
        **payload,
        "qualified_opportunity_records": [
            row for row in opportunities if row["record_id"] != "jtg-2"
        ],
    }
    without_second = attach_v2_targets(projected, payload_without_second)
    assert without_second == ()
    # Group construction still retains each exact opportunity/source association.
    assert {r["opportunity_record_id"] for rows in groups.values() for r in rows} == {"jtg-1", "jtg-2", "jtg-3"}


def test_unbound_v2_population_is_withheld_and_pure_translation_retains_source_fields():
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

    assert attached == ()
    from agents.golden_press.market_map_projection import _v2_opportunity
    translated = _v2_opportunity(qualified[0], tuple(groups["family-qualified"]))
    assert translated.evidence[0].source_url == "https://sam.gov/opp/qualified-1/view"
    assert {row["source_kind"] for row in translated.linked_targets} == {
        "published_contact", "apollo_enrichment", "enrichment_candidate"}
    assert [row["name"] for row in translated.contacts] == ["Pat Published"]


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
                "relationship_provenance"):
        assert key in record
    queue_item = schema["$defs"]["researchQueueItem"]
    assert "ambiguous_dimensions" in queue_item["required"]
    assert queue_item["properties"]["ambiguous_dimensions"]["minItems"] == 1
