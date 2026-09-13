"""Metamorphic properties for the Federal Pursuit Graph contract."""

from __future__ import annotations

from agents.golden_press import evidence_route as er


def _context(*, as_of="2026-08-20", competitors=None, partners=None):
    return er.build_context(
        "JTG, inc.",
        "jtg_inc",
        pack={
            "client_entity_aliases": ["JTG INC", "JTG, Incorporated"],
            "generated_at": as_of,
        },
        packet={"strategy": {"research_entities": [
            {"kind": "competitor", "name": name, "rationale": "cited"}
            for name in (competitors or [])
        ]}},
        profile={
            "capability_summary": "Language services and training.",
            "capability_terms": {
                "core": ["translation and interpretation", "linguist support"],
                "adjacent": ["language proficiency testing"],
                "excluded": [],
            },
        },
        route_facts={
            "named_partners": partners or [],
            "validated_competitors": [],
            "certifications": ["small business"],
        },
        as_of=as_of,
    )


def _award(recipient: str) -> dict:
    return {
        "lane": "L2_entity_award",
        "record_id": "AWARD-1",
        "title": "Translation and Interpretation Services",
        "description": "linguist support",
        "recipient": recipient,
        "obligated_dollars": 100.0,
    }


def test_alias_punctuation_and_case_do_not_change_client_identity():
    context = _context()
    variants = ["JTG, INC.", "jtg inc", "JTG, Incorporated"]
    classified = [er.classify_record(_award(name), context)
                  for name in variants]
    assert {row["evidence_class"] for row in classified} == {
        "client_historical"}
    assert len({row["canonical_entity_id"] for row in classified}) == 1


def test_advancing_as_of_changes_window_state_without_changing_identity():
    record = {
        "lane": "L1_notice",
        "record_id": "NOTICE-1",
        "title": "Translation and Interpretation Services",
        "description": "linguist support",
        "response_deadline": "2026-09-01",
    }
    live = er.classify_record(record, _context(as_of="2026-08-20"))
    past = er.classify_record(record, _context(as_of="2026-10-01"))
    assert live["record_id"] == past["record_id"] == "NOTICE-1"
    assert live["window_state"] == "live"
    assert past["window_state"] == "stated_past"
    assert live["evidence_class"] == "ambiguous"
    assert past["evidence_class"] != "current_opportunity"


def test_partner_and_competitor_roles_remain_separate_dimensions():
    context = _context(competitors=["MAXIMUS"], partners=["MAXIMUS"])
    row = er.classify_record(_award("MAXIMUS"), context)
    assert row["evidence_class"] == "competitive_historical"
    assert row["route_relationship"] == "named_partner_teaming"
    dimensions = set(row["relationship_provenance"])
    assert {"evidence_class", "commercial_route"} <= dimensions
    assert row["relationship_provenance"]["evidence_class"]
    assert row["relationship_provenance"]["commercial_route"]
