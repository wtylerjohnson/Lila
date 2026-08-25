"""Small, deterministic fixtures for the ten known graph failures.

These tests exercise shared classification and evidence seams. They are
deliberately cheaper than re-rendering four client reports for every change.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agents.golden_press import evidence_route as er
from agents.golden_press.evidence_objects import EvidenceError, ExactMoney
from agents.golden_press.evidence_pack_v2 import (
    build_target_groups,
    canonicalize_requirement_families,
    validate_graph_contract,
)
from agents.golden_press.market_map_render import _money


PACKET = {"strategy": {"research_entities": [
    {"kind": "competitor", "name": "SOSi (SOS International)",
     "rationale": "named rival for cleared federal translation"},
]}}
PROFILE = {
    "capability_summary": "Language services and training: translation "
                          "and interpretation, localization.",
    "capability_terms": {
        "core": ["translation and interpretation", "language services",
                 "foreign language instructor", "linguist support"],
        "adjacent": ["language proficiency testing"],
        "excluded": [],
    },
}
ROUTE_FACTS = {
    "named_partners": ["MAXIMUS", "InDyne, Inc.", "CAE USA"],
    "validated_competitors": [],
    "certifications": ["small business"],
}


@pytest.fixture()
def ctx():
    return er.build_context(
        "JTG, inc.", "jtg_inc",
        pack={"client_entity_aliases": ["JTG Inc", "JTG Incorporated"],
              "generated_at": "2026-08-20"},
        packet=PACKET, profile=PROFILE, route_facts=ROUTE_FACTS)


def _award(recipient: str, description: str) -> dict:
    return {"lane": "L2_entity_award", "recipient": recipient,
            "description": description, "title": "", "record_id": "x"}


def _notice(title: str, description: str = "") -> dict:
    return {"lane": "L1_notice", "title": title,
            "description": description,
            "response_deadline": "2026-09-30", "set_aside": ""}


def test_client_as_competitor_is_rejected(ctx):
    row = er.classify_record(
        _award("JTG, INC.", "translation and interpretation services"), ctx)
    assert row["evidence_class"] == "client_historical"
    assert row["evidence_class"] != "competitive_historical"


def test_alias_of_client_as_competitor_is_rejected(ctx):
    row = er.classify_record(
        _award("JTG Incorporated", "language services"), ctx)
    assert row["evidence_class"] == "client_historical"
    assert row["evidence_class"] != "competitive_historical"


def test_orphan_ihs_targets_are_removed():
    opportunities = [{
        "record_id": "state-1", "requirement_family": "fam-state",
        "title": "Embassy Language Instructors",
        "agency": "Department of State",
    }]
    enrichments = [{
        "requirement_family": "fam-ihs", "role": "chief_information_officer",
        "name": "Stale IHS Contact", "organization": "Indian Health Service",
        "email": "stale@example.gov", "phone": "+1 202 555 0100",
    }]
    groups = build_target_groups(opportunities, enrichments=enrichments)
    assert set(groups) == {"fam-state"}
    assert all(target.get("organization") != "Indian Health Service"
               for rows in groups.values() for target in rows)


def test_aerobics_instructor_false_match_is_rejected(ctx):
    row = er.classify_record(
        _notice("Aerobics Instructor Services",
                "physical fitness instruction and training"), ctx)
    assert row["service_fit"] == "unrelated"
    assert row["evidence_class"] == "excluded"


def test_unrelated_instructor_false_match_is_rejected(ctx):
    row = er.classify_record(
        _notice("General Classroom Instructor Support",
                "curriculum and classroom instruction"), ctx)
    assert row["service_fit"] == "unrelated"
    assert row["evidence_class"] == "excluded"


def test_partner_is_not_mistaken_for_rival(ctx):
    row = er.classify_record(
        _award("MAXIMUS FEDERAL SERVICES, INC.",
               "translation and interpretation services"), ctx)
    assert row["route_relationship"] == "named_partner_teaming"
    assert row["evidence_class"] != "competitive_historical"


def test_restricted_set_aside_is_not_ranked_direct(ctx):
    record = _notice("Translation and Interpretation Services")
    record["set_aside"] = "IEE"
    row = er.classify_record(record, ctx)
    assert row["eligible_route"] is False
    assert row["commercial_route"] == "possible_subcontracting"


def test_past_forecast_is_not_ranked_current(ctx):
    row = er.classify_record({
        "lane": "L4_forecast",
        "title": "Translation and Interpretation Support",
        "description": "language services", "release_date": "2026-03-01",
    }, ctx)
    assert row["window_state"] == "stated_past"
    assert row["evidence_class"] == "forecast"
    assert row["evidence_class"] != "current_opportunity"


def test_title_only_lifecycle_similarity_fails_open_without_notice_identity():
    rows = [
        {"record_id": "ss-1", "title": "Sources Sought Language Services",
         "agency": "Department of State", "window_state": "live"},
        {"record_id": "sol-1", "title": "Solicitation Language Services",
         "agency": "Department of State", "window_state": "live",
         "response_deadline": "2026-09-25"},
    ]
    kept = canonicalize_requirement_families(rows)
    assert len(kept) == 2
    assert {row["canonical_record_id"] for row in kept} == {"sol-1", "ss-1"}


def test_displayed_amount_with_broken_receipt_is_rejected():
    amount = ExactMoney(
        amount=Decimal("125000.00"), basis="obligated on cited records",
        population="one cited award")
    with pytest.raises(EvidenceError, match="must carry source evidence"):
        _money(amount, "broken-receipt")


def test_graph_contract_receipt_catches_cross_relationship_violations():
    payload = {
        "classification_context": {
            "client_aliases": ["JTG, INC.", "JTG Incorporated"],
            "named_partners": {"maximus federal services inc": "cited"},
        },
        "records": [{
            "record_id": "self-1", "recipient": "JTG Incorporated",
            "evidence_class": "competitive_historical",
            "window_state": "live", "commercial_route": "direct",
            "eligible_route": True,
        }, {
            "record_id": "past-1", "recipient": "",
            "evidence_class": "current_opportunity",
            "window_state": "stated_past", "commercial_route": "direct",
            "eligible_route": False,
        }],
        "canonical_requirement_families": [],
        "qualified_opportunity_records": [],
        "target_groups": {"orphan-family": [{
            "opportunity_record_id": "removed-ihs",
            "name": "Stale IHS target",
        }]},
    }
    rules = {row["rule_id"] for row in validate_graph_contract(payload)}
    assert rules == {
        "G001_CLIENT_CANNOT_BE_COMPETITOR",
        "G003_PAST_WINDOW_CANNOT_BE_CURRENT",
        "G004_DIRECT_ROUTE_REQUIRES_ELIGIBILITY",
        "G005_TARGETS_REQUIRE_CURRENT_OPPORTUNITY",
        "G008_CURRENT_OPPORTUNITY_PARTITION",
    }
