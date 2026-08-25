from __future__ import annotations

import json

import pytest

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
    PRIME,
    STATUS_CHECK,
    TEAM,
    Opportunity,
    _v2_opportunity,
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
         "solicitation_number": "19AQMM26R1000",
         "agency": "Department of State", "office": "Acquisitions",
         "window_state": "live"},
        {"record_id": "sol-1", "title": "Solicitation Language Services",
         "solicitation_number": "19AQMM26R1000",
         "agency": "Department of State", "office": "Acquisitions",
         "window_state": "live",
         "response_deadline": "2026-09-25", "contact_email": "co@state.gov"},
    ]
    kept = canonicalize_requirement_families(rows)
    assert len(kept) == 1
    assert kept[0]["canonical_record_id"] == "sol-1"
    assert kept[0]["family_member_ids"] == ["sol-1", "ss-1"]


def test_solicitation_family_requires_buyer_namespace_and_preserves_lineage():
    predecessor = {
        "record_id": "pre-1",
        "title": "Training Analysis Evaluation Product Presolicitation",
        "description": "The Marine Corps intends to issue solicitation M6785426R8017.",
        "agency": "Department of Defense",
        "office": "Commander",
        "notice_type": "Presolicitation",
        "posted_date": "2026-07-01",
        "window_state": "stated_past",
        "url": "https://sam.gov/opp/pre-1/view",
    }
    active = {
        "record_id": "rfp-1",
        "title": "Training Analysis Evaluation Product Request for Proposal",
        "solicitation_number": "M6785426R8017",
        "agency": "Department of Defense",
        "office": "Commander",
        "notice_type": "Solicitation",
        "posted_date": "2026-08-17",
        "response_deadline": "2026-09-14T10:00:00-04:00",
        "window_state": "live",
        "url": "https://sam.gov/opp/rfp-1/view",
    }
    reused_number = {
        **active,
        "record_id": "other-office",
        "office": "Another Office",
        "url": "https://sam.gov/opp/other-office/view",
    }

    kept = canonicalize_requirement_families(
        [predecessor, active, reused_number])

    assert len(kept) == 2
    taep = next(row for row in kept
                if row["canonical_record_id"] == "rfp-1")
    assert taep["family_member_ids"] == ["pre-1", "rfp-1"]
    assert [row["record_id"] for row in taep["family_lineage"]] == [
        "pre-1", "rfp-1"]
    assert taep["family_lineage"][1]["solicitation_number"] == \
        "m6785426r8017"
    assert "department of defense|commander" in taep["family_basis"]


def test_unanchored_solicitation_mention_does_not_merge_unrelated_notice():
    rows = [{
        "record_id": "one",
        "title": "Language services market research",
        "description": "This differs from solicitation M6785426R8017.",
        "agency": "Department of Defense",
        "office": "Commander",
        "window_state": "live",
    }, {
        "record_id": "two",
        "title": "Training Analysis Evaluation Product",
        "solicitation_number": "M6785426R8017",
        "agency": "Department of Defense",
        "office": "Commander",
        "window_state": "live",
    }]
    assert len(canonicalize_requirement_families(rows)) == 2


def test_explicit_successor_solicitation_links_old_and_new_notice_identity():
    predecessor = {
        "record_id": "old-notice",
        "title": "Language support presolicitation",
        "description": (
            "The follow-on solicitation NEW26R0002 will replace this notice."),
        "solicitation_number": "OLD26R0001",
        "agency": "Department of State",
        "office": "Acquisitions",
        "posted_date": "2026-07-01",
        "source_status": False,
    }
    successor = {
        "record_id": "new-notice",
        "title": "Language support solicitation",
        "solicitation_number": "NEW26R0002",
        "agency": "Department of State",
        "office": "Acquisitions",
        "posted_date": "2026-08-20",
        "source_status": True,
    }

    kept = canonicalize_requirement_families([predecessor, successor])

    assert len(kept) == 1
    assert kept[0]["canonical_record_id"] == "new-notice"
    assert kept[0]["family_member_ids"] == ["new-notice", "old-notice"]
    old_lineage = next(row for row in kept[0]["family_lineage"]
                       if row["record_id"] == "old-notice")
    assert old_lineage["posted_solicitation_number"] == "old26r0001"
    assert old_lineage["successor_solicitation_number"] == "new26r0002"


def test_successor_solicitation_chain_resolves_transitively_within_buyer():
    common = {
        "agency": "Department of State",
        "office": "Acquisitions",
    }
    oldest = {
        **common,
        "record_id": "notice-a",
        "title": "Language support market research",
        "description": "The follow-on solicitation B26R0002 will replace it.",
        "solicitation_number": "A26R0001",
        "posted_date": "2026-06-01",
        "source_status": False,
    }
    middle = {
        **common,
        "record_id": "notice-b",
        "title": "Language support presolicitation",
        "description": "The successor solicitation C26R0003 will replace it.",
        "solicitation_number": "B26R0002",
        "posted_date": "2026-07-01",
        "source_status": False,
    }
    newest = {
        **common,
        "record_id": "notice-c",
        "title": "Language support solicitation",
        "solicitation_number": "C26R0003",
        "posted_date": "2026-08-01",
        "source_status": True,
    }

    kept = canonicalize_requirement_families([oldest, middle, newest])

    assert len(kept) == 1
    assert kept[0]["canonical_record_id"] == "notice-c"
    assert kept[0]["family_member_ids"] == [
        "notice-a", "notice-b", "notice-c"]


def test_negated_successor_text_does_not_join_notice_families():
    common = {
        "agency": "Department of State",
        "office": "Acquisitions",
    }
    unrelated = {
        **common,
        "record_id": "unrelated",
        "title": "Unrelated language market research",
        "description": (
            "This is not a follow-on solicitation NEW26R0002 and must not "
            "be treated as its predecessor."),
        "solicitation_number": "OTHER26R0001",
    }
    current = {
        **common,
        "record_id": "current",
        "title": "Language support solicitation",
        "solicitation_number": "NEW26R0002",
    }

    kept = canonicalize_requirement_families([unrelated, current])

    assert len(kept) == 2


@pytest.mark.parametrize("negated", [
    "This does not constitute a successor solicitation NEW26R0002.",
    "This does not plan a follow-on solicitation NEW26R0002.",
    "This is not considered a replacement solicitation NEW26R0002.",
    "This proceeds without issuing a follow-on solicitation NEW26R0002.",
    "Neither a successor solicitation NEW26R0002 nor a replacement is planned.",
])
def test_successor_negation_variants_fail_open(negated):
    common = {
        "agency": "Department of State",
        "office": "Acquisitions",
    }
    rows = [{
        **common,
        "record_id": "old",
        "title": "Market research",
        "description": negated,
        "solicitation_number": "OLD26R0001",
    }, {
        **common,
        "record_id": "new",
        "title": "Language support",
        "solicitation_number": "NEW26R0002",
    }]

    assert len(canonicalize_requirement_families(rows)) == 2


def test_later_positive_successor_mention_survives_earlier_negation():
    common = {
        "agency": "Department of State",
        "office": "Acquisitions",
    }
    rows = [{
        **common,
        "record_id": "old",
        "title": "Market research",
        "description": (
            "This is not a follow-on solicitation WRONG26R0001. "
            "The successor solicitation NEW26R0002 replaces this notice."),
        "solicitation_number": "OLD26R0001",
    }, {
        **common,
        "record_id": "new",
        "title": "Language support",
        "solicitation_number": "NEW26R0002",
    }]

    kept = canonicalize_requirement_families(rows)

    assert len(kept) == 1
    assert kept[0]["family_member_ids"] == ["new", "old"]


def test_conflicting_successor_claims_do_not_overmerge_families():
    common = {
        "agency": "Department of State",
        "office": "Acquisitions",
        "solicitation_number": "OLD26R0001",
    }
    rows = [{
        **common,
        "record_id": "old-claims-b",
        "successor_solicitation_number": "NEW26R0002",
    }, {
        **common,
        "record_id": "old-claims-c",
        "successor_solicitation_number": "OTHER26R0003",
    }, {
        "record_id": "new-b",
        "agency": common["agency"],
        "office": common["office"],
        "solicitation_number": "NEW26R0002",
    }, {
        "record_id": "new-c",
        "agency": common["agency"],
        "office": common["office"],
        "solicitation_number": "OTHER26R0003",
    }]

    kept = canonicalize_requirement_families(rows)

    assert len(kept) == 3
    old_family = next(row for row in kept
                      if "old-claims-b" in row["family_member_ids"])
    assert old_family["family_member_ids"] == [
        "old-claims-b", "old-claims-c"]


def test_successor_link_requires_same_complete_buyer_namespace():
    base = {
        "record_id": "old",
        "title": "Market research",
        "solicitation_number": "OLD26R0001",
        "successor_solicitation_number": "NEW26R0002",
        "agency": "Department of State",
    }
    missing_office = [base, {
        "record_id": "new",
        "solicitation_number": "NEW26R0002",
        "agency": "Department of State",
    }]
    other_office = [{**base, "office": "Office A"}, {
        "record_id": "new",
        "solicitation_number": "NEW26R0002",
        "agency": "Department of State",
        "office": "Office B",
    }]

    assert len(canonicalize_requirement_families(missing_office)) == 2
    assert len(canonicalize_requirement_families(other_office)) == 2


def test_structured_successor_field_links_and_chain_is_order_independent():
    common = {
        "agency": "Department of State",
        "office": "Acquisitions",
    }
    rows = [{
        **common,
        "record_id": "old",
        "solicitation_number": "OLD26R0001",
        "successor_solicitation_number": "MID26R0002",
        "source_status": False,
    }, {
        **common,
        "record_id": "middle",
        "solicitation_number": "MID26R0002",
        "successor_solicitation_number": "NEW26R0003",
        "source_status": False,
    }, {
        **common,
        "record_id": "new",
        "solicitation_number": "NEW26R0003",
        "source_status": True,
    }]

    forward = canonicalize_requirement_families(rows)
    reverse = canonicalize_requirement_families(list(reversed(rows)))

    assert forward == reverse
    assert len(forward) == 1
    assert forward[0]["canonical_record_id"] == "new"


def test_structured_solicitation_sentinel_does_not_merge_unrelated_notices():
    rows = [{
        "record_id": "one",
        "title": "Language services market research",
        "solicitation_number": "PENDING",
        "agency": "Department of Defense",
        "office": "Commander",
        "window_state": "live",
    }, {
        "record_id": "two",
        "title": "Training range support",
        "solicitation_number": "UNKNOWN",
        "agency": "Department of Defense",
        "office": "Commander",
        "window_state": "live",
    }]
    assert len(canonicalize_requirement_families(rows)) == 2


def test_same_title_without_solicitation_fails_open_to_notice_identity():
    rows = [{
        "record_id": "office-one",
        "title": "Translation Services",
        "agency": "Department of State",
        "office": "Embassy One",
        "window_state": "live",
    }, {
        "record_id": "office-two",
        "title": "Translation Services",
        "agency": "Department of State",
        "office": "Embassy Two",
        "window_state": "live",
    }]
    assert len(canonicalize_requirement_families(rows)) == 2


def test_active_notice_beats_newer_cancelled_family_member():
    base = {
        "title": "Language Support Solicitation",
        "solicitation_number": "19AQMM26R0001",
        "agency": "Department of State",
        "office": "Acquisitions",
        "notice_type": "Solicitation",
        "response_deadline": "2026-09-30T17:00:00-04:00",
        "window_state": "live",
    }
    active = {
        **base,
        "record_id": "active",
        "posted_date": "2026-08-17",
        "source_status": True,
    }
    cancelled = {
        **base,
        "record_id": "cancelled",
        "posted_date": "2026-08-18",
        "notice_type": "Cancellation",
        "source_status": False,
    }

    kept = canonicalize_requirement_families([active, cancelled])

    assert len(kept) == 1
    assert kept[0]["canonical_record_id"] == "active"
    assert kept[0]["family_member_ids"] == ["active", "cancelled"]


def test_newest_active_notice_keeps_identity_and_carries_rich_base_scope():
    base = {
        "record_id": "base-rfp",
        "title": "Language Curriculum Support Solicitation",
        "description": (
            "Design, develop, implement, and evaluate language curricula "
            "and instructional courseware for military learners."),
        "solicitation_number": "N0018926R1000",
        "agency": "DEPT OF DEFENSE",
        "office": "NAVSUP FLT LOG CTR NORFOLK",
        "notice_type": "Solicitation",
        "posted_date": "2026-08-10",
        "response_deadline": "2026-09-10T17:00:00-04:00",
        "set_aside": "No Set aside used",
        "contact_email": "base@example.mil",
        "source_status": False,
        "url": "https://sam.gov/opp/base-rfp/view",
    }
    amendment = {
        "record_id": "amendment-1",
        "title": "Amendment 1",
        "description": "See amendment attachment.",
        "solicitation_number": "N0018926R1000",
        "agency": "DEPT OF DEFENSE",
        "office": "NAVSUP FLT LOG CTR NORFOLK",
        "notice_type": "Presolicitation",
        "posted_date": "2026-08-20",
        "response_deadline": "2026-09-14T17:00:00-04:00",
        "source_status": True,
        "url": "https://sam.gov/opp/amendment-1/view",
    }

    kept = canonicalize_requirement_families([base, amendment])

    assert len(kept) == 1
    row = kept[0]
    assert row["canonical_record_id"] == "amendment-1"
    assert row["url"].endswith("/amendment-1/view")
    assert row["response_deadline"] == "2026-09-14T17:00:00-04:00"
    assert row["description"] == base["description"]
    assert row["set_aside"] == "No Set aside used"
    assert row["contact_email"] == "base@example.mil"
    assert row["canonical_scope_record_id"] == "base-rfp"
    assert row["family_field_sources"]["description"]["record_id"] == \
        "base-rfp"
    assert row["family_field_sources"]["response_deadline"]["record_id"] == \
        "amendment-1"


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


def test_duplicate_notice_reconciles_fresh_acquisition_fields_by_source():
    stale = {
        "lane": "L1_notice",
        "record_id": "same-notice",
        "title": "Training Analysis Evaluation Product",
        "description": "Long attachment-derived scope " * 20,
        "agency": "DEPT OF DEFENSE",
        "office": "COMMANDER",
        "response_deadline": "2026-07-31",
        "set_aside": "",
        "contact_email": "",
        "active": "No",
    }
    fresh = {
        "lane": "L1_notice",
        "record_id": "same-notice",
        "title": "Training Analysis Evaluation Product RFP",
        "description": "Training and education design and development.",
        "agency": "DEPT OF DEFENSE",
        "office": "COMMANDER",
        "response_deadline": "2026-09-14T10:00:00-04:00",
        "solicitation_number": "M6785426R8017",
        "set_aside": "8(a) Set-Aside (FAR 19.8)",
        "set_aside_code": "8A",
        "contact_email": "anitra@example.mil",
        "source_status": "Yes",
        "retrieved_at": "2026-08-19T16:48:51Z",
        "source_fields": {"active": "Yes"},
        "source_sweep": "research_mesh",
    }

    rows, receipt = canonicalize_record_ids([stale, fresh])

    assert receipt["record_duplicates_collapsed"] == 1
    assert rows[0]["response_deadline"] == "2026-09-14T10:00:00-04:00"
    assert rows[0]["solicitation_number"] == "M6785426R8017"
    assert rows[0]["set_aside_code"] == "8A"
    assert rows[0]["contact_email"] == "anitra@example.mil"
    assert rows[0]["source_status"] == "Yes"
    assert rows[0]["description"] == fresh["description"]
    assert rows[0]["record_field_sources"]["response_deadline"][
        "source_sweep"] == "research_mesh"


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


@pytest.mark.parametrize(("route_action", "role", "expected_purpose"), (
    ("prime", "program_or_mission_owner", "pursuit_execution"),
    ("team", "partner_capture_or_bd_lead", "pursuit_execution"),
    ("verify", "partner_capture_or_bd_lead", "decision_resolution"),
))
def test_target_purpose_follows_pursuit_route_action(
        route_action, role, expected_purpose):
    opportunity = _opportunity(
        f"opp-{route_action}", f"family-{route_action}",
        "Language and curriculum requirement", "Department of State")
    opportunity.update({
        "qualification_state": "qualified",
        "route_action": route_action,
        "commercial_route": (
            "direct" if route_action == "prime"
            else "possible_subcontracting"),
    })
    groups = build_target_groups(
        [opportunity],
        target_roles={f"family-{route_action}": [{
            "role": role,
            "organization": "Department of State",
            "role_needed": "named route owner",
        }]},
    )

    assert {row["target_purpose"] for row in
            groups[f"family-{route_action}"]} == {expected_purpose}


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


@pytest.mark.parametrize((
        "route_action", "commercial_route", "eligible_route",
        "expected_motion", "expected_access"), (
    ("prime", "direct", True, PRIME, "Bid directly."),
    ("team", "possible_subcontracting", False, TEAM,
     "Engage an eligible prime for the teaming path."),
    ("verify", "direct", False, STATUS_CHECK,
     "Verify a direct, vehicle, or teaming path."),
    ("verify", "incumbent", False, STATUS_CHECK,
     "Verify a direct, vehicle, or teaming path."),
))
def test_v2_market_map_projection_consumes_route_action(
        route_action, commercial_route, eligible_route,
        expected_motion, expected_access):
    row = {
        "record_id": f"route-{route_action}",
        "title": "Current language requirement",
        "agency": "Department of State",
        "evidence_class": "current_opportunity",
        "instrument": "Solicitation",
        "response_due": "2026-09-30",
        "source_url": f"https://sam.gov/opp/route-{route_action}/view",
        "commercial_route": commercial_route,
        "eligible_route": eligible_route,
        "route_action": route_action,
        "technical_fit": {"basis": "published language-services scope"},
    }

    projected = _v2_opportunity(row, ())

    assert projected.motion == expected_motion
    assert projected.access_route == expected_access
    if route_action == "verify":
        assert projected.action.startswith("Verify the response path")


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


def test_fit_review_retains_route_as_an_independent_action(monkeypatch):
    row = {
        "record_id": "notice-dual-hold",
        "requirement_family": "family-dual-hold",
        "evidence_class": "current_opportunity",
        "commercial_route": "possible_subcontracting",
        "eligible_route": False,
        "route_basis": "restricted set-aside bars direct pursuit",
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
    qualified, _incumbents, held = qualify_opportunities([row], {})

    assert qualified == []
    assert held == [row]
    decision = row["projection_decision"]
    assert decision["disposition"] == "needs_review"
    assert decision["reason_codes"] == ["FIT_AMBIGUOUS"]
    assert decision["blocking_dimensions"] == ["service_fit"]
    assert decision["reasons"] == ["broad capability stem requires review"]
    assert row["qualification_state"] == "held_for_fit_review"
    assert row["pursuit_state"] == "research_required"
    assert row["route_action"] == "team"


@pytest.mark.parametrize(("route", "eligible", "expected_action"), (
    ("unknown", False, "verify"),
    ("possible_subcontracting", False, "team"),
    ("named_partner_teaming", True, "team"),
    ("direct", True, "prime"),
    ("incumbent", True, "prime"),
))
def test_direct_fit_promotes_with_independent_route_action(
        monkeypatch, route, eligible, expected_action):
    row = {
        "record_id": f"notice-{route}",
        "requirement_family": f"family-{route}",
        "title": "Language services",
        "evidence_class": "current_opportunity",
        "service_fit": "direct",
        "fit_basis": "approved language-services phrase",
        "fit_evidence": [{"source_id": "capability-frame"}],
        "window_state": "live",
        "commercial_route": route,
        "route_relationship": route,
        "eligible_route": eligible,
        "route_basis": "published route evidence",
    }
    monkeypatch.setattr(
        evidence_pack_v2, "_technical_fit",
        lambda *_args: {
            "fit": True,
            "fit_class": "direct",
            "basis": "approved language-services phrase",
            "evidence": [{"source_id": "capability-frame"}],
        })

    qualified, _incumbents, held = qualify_opportunities([row], {})

    assert held == []
    assert qualified == [row]
    assert row["qualification_state"] == "qualified"
    assert row["route_action"] == expected_action
    assert row["pursuit_state"] == f"pursue_{expected_action}"
    assert row["qualified"]["eligible_route"] is eligible
    assert row["qualified"]["route_action"] == expected_action


def test_qualification_consumes_stamped_dimensions_once(monkeypatch):
    row = {
        "record_id": "notice-stamped",
        "requirement_family": "family-stamped",
        "title": "JTS Curriculum Development and Support",
        "evidence_class": "current_opportunity",
        "service_fit": "direct",
        "fit_basis": "approved core capability phrase",
        "fit_evidence": [{"source_id": "W9124N12C0086"}],
        "window_state": "live",
        "window_basis": "published response deadline is live",
        "commercial_route": "direct",
        "route_relationship": "direct",
        "eligible_route": True,
        "route_basis": "unrestricted notice",
    }
    monkeypatch.setattr(
        evidence_pack_v2.er, "classify_service_fit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("qualification reclassified service fit")))
    monkeypatch.setattr(
        evidence_pack_v2.er, "classify_route",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("qualification reclassified route")))

    qualified, _incumbents, held = qualify_opportunities([row], {})

    assert held == []
    assert qualified[0]["qualified"]["fit_evidence"] == [
        {"source_id": "W9124N12C0086"}]
    assert qualified[0]["qualified"]["route_basis"] == \
        "unrestricted notice"


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


def test_graph_contract_rejects_route_only_hold_of_direct_fit():
    current = {
        "record_id": "notice-fit",
        "requirement_family": "family-fit",
        "evidence_class": "current_opportunity",
        "service_fit": "direct",
        "commercial_route": "unknown",
        "eligible_route": False,
        "window_state": "live",
    }
    payload = {
        "records": [current],
        "classification_context": {
            "client_aliases": [], "named_partners": []},
        "canonical_requirement_families": [{
            "requirement_family": "family-fit"}],
        "qualified_opportunity_records": [],
        "held_opportunities": [{
            "record_id": "notice-fit",
            "requirement_family": "family-fit",
            "qualification_state": "needs_eligible_route",
        }],
        "target_groups": {},
    }

    violation = next(
        row for row in validate_graph_contract(payload)
        if row["rule_id"] == "G013_ROUTE_INDEPENDENT_PROMOTION")
    assert violation["evidence"]["expected_qualified_ids"] == ["notice-fit"]


def test_schema_pins_the_graph_contract():
    path = ("agents/golden_press/schemas/evidence_pack_v2.schema.json")
    schema = json.loads(open(path, encoding="utf-8").read())
    assert schema["properties"]["schema_version"]["const"] == \
        "evidence-pack-v2"
    assert "canonical_entities" in schema["required"]
    assert "canonical_requirement_families" in schema["required"]
    assert "incremental_cache_receipt" in schema["required"]
    assert "captured_at" in schema["required"]
    assert "generated_at" in schema["required"]
    assert "workflow_contract" in schema["required"]
    assert "research_queue" in schema["required"]
    assert "graph_contract_certified" in schema["required"]
    assert "graph_contract_violations" in schema["required"]
    record = schema["$defs"]["classifiedRecord"]["properties"]
    for key in ("canonical_entity", "requirement_family", "evidence_class",
                "commercial_route", "route_action", "pursuit_state",
                "window_state",
                "relationship_provenance", "projection_decision"):
        assert key in record
    decision = schema["$defs"]["projectionDecision"]
    assert decision["properties"]["disposition"]["enum"] == [
        "qualified", "needs_review"]
    queue_item = schema["$defs"]["researchQueueItem"]
    assert "ambiguous_dimensions" in queue_item["required"]
    assert queue_item["properties"]["ambiguous_dimensions"]["minItems"] == 1
    cache = schema["properties"]["incremental_cache_receipt"]
    assert cache["properties"]["schema_version"]["const"] == \
        "incremental-cache-semantic-receipt-v1"
    assert "cache_root" not in cache["properties"]
    assert "namespaces" not in cache["properties"]


def test_v2_schema_accepts_the_pre_lineage_frozen_snapshot():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(open(
        "agents/golden_press/schemas/evidence_pack_v2.schema.json",
        encoding="utf-8").read())
    snapshot = json.loads(open(
        "tests/fixtures/release_snapshot/jtg_reproducibility_snapshot.json",
        encoding="utf-8").read())

    jsonschema.Draft202012Validator(schema).validate(snapshot["graph"])


def test_corrected_pack_requires_explicit_aware_clocks(tmp_path):
    with pytest.raises(TypeError, match="classification_as_of"):
        evidence_pack_v2.build_corrected_pack(
            "client", "Client", root=tmp_path)

    with pytest.raises(ValueError, match="classification_as_of"):
        evidence_pack_v2.build_corrected_pack(
            "client", "Client",
            classification_as_of="2026-08-24",
            captured_at="2026-08-24T18:00:00Z",
            root=tmp_path,
        )

    with pytest.raises(ValueError, match="captured_at"):
        evidence_pack_v2.build_corrected_pack(
            "client", "Client",
            classification_as_of="2026-08-24T18:00:00Z",
            captured_at="2026-08-24T18:00:00",
            root=tmp_path,
        )


def test_corrected_pack_rejects_pressed_pack_after_classification(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pressed = pack_dir / "client.golden_report.evidence_pack.json"
    pressed.write_text(json.dumps({
        "generated_at": "2026-08-24T17:01:00Z",
        "client_entity_aliases": [],
        "records": [],
    }), encoding="utf-8")

    with pytest.raises(
            ValueError,
            match="pressed evidence pack generated_at is later"):
        evidence_pack_v2.build_corrected_pack(
            "client", "Client",
            classification_as_of="2026-08-24T17:00:00Z",
            captured_at="2026-08-24T17:05:00Z",
            root=tmp_path,
            pack_dir=pack_dir,
            pressed_pack_path=pressed,
        )


def test_corrected_pack_rejects_deep_sweep_after_classification(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pressed = pack_dir / "client.golden_report.evidence_pack.json"
    pressed.write_text(json.dumps({
        "generated_at": "2026-08-24T17:00:00Z",
        "client_entity_aliases": [],
        "records": [],
    }), encoding="utf-8")
    deep = tmp_path / "deep.json"
    deep.write_text(json.dumps({
        "records": [],
        "receipt": {"at": "2026-08-24T17:01:00Z"},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="deep sweep receipt at is later"):
        evidence_pack_v2.build_corrected_pack(
            "client", "Client",
            classification_as_of="2026-08-24T17:00:00Z",
            captured_at="2026-08-24T17:05:00Z",
            root=tmp_path,
            pack_dir=pack_dir,
            pressed_pack_path=pressed,
            deep_sweep_path=deep,
        )


def test_corrected_pack_rejects_research_mesh_after_classification(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pressed = pack_dir / "client.golden_report.evidence_pack.json"
    pressed.write_text(json.dumps({
        "generated_at": "2026-08-24T17:00:00Z",
        "client_entity_aliases": [],
        "records": [],
    }), encoding="utf-8")
    research = tmp_path / "searches_client.json"
    research.write_text(json.dumps({
        "generated_at": "2026-08-24T17:01:00Z",
        "results": {},
    }), encoding="utf-8")

    with pytest.raises(
            ValueError,
            match="governed research mesh generated_at is later"):
        evidence_pack_v2.build_corrected_pack(
            "client", "Client",
            classification_as_of="2026-08-24T17:00:00Z",
            captured_at="2026-08-24T17:05:00Z",
            root=tmp_path,
            pack_dir=pack_dir,
            pressed_pack_path=pressed,
            research_sweep_path=research,
        )


def test_corrected_pack_uses_frozen_clocks_and_semantic_receipt(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pressed_path = pack_dir / "client.golden_report.evidence_pack.json"
    pressed_path.write_text(json.dumps({
        "generated_at": "2026-08-24T17:00:00Z",
        "client_entity_aliases": [],
        "records": [],
    }), encoding="utf-8")

    _path, payload = evidence_pack_v2.build_corrected_pack(
        "client", "Client",
        classification_as_of="2026-08-24T11:00:00-06:00",
        captured_at="2026-08-24T17:05:00+00:00",
        root=tmp_path,
        pack_dir=pack_dir,
        pressed_pack_path=pressed_path,
        cache_dir=tmp_path / "cache",
    )

    assert payload["classification_context"]["as_of"] == \
        "2026-08-24T17:00:00Z"
    assert payload["captured_at"] == "2026-08-24T17:05:00Z"
    assert payload["generated_at"] == payload["captured_at"]
    receipt = payload["incremental_cache_receipt"]
    assert receipt["schema_version"] == \
        "incremental-cache-semantic-receipt-v1"
    assert "cache_root" not in receipt
    assert "actions" not in receipt


def test_research_mesh_handoff_preserves_notice_and_forecast_decision_fields(
        tmp_path):
    artifact = tmp_path / "searches_jtg_inc.json"
    artifact.write_text(json.dumps({
        "generated_at": "2026-08-19T16:48:51Z",
        "results": {
            "sam.gov": [{
                "source_id": "taep-rfp",
                "title": "Training Analysis Evaluation Product RFP",
                "set_aside": "8(a) Set-Aside (FAR 19.8)",
                "contacts": [{
                    "contact_type": "primary",
                    "name": "Anitra Kinsey",
                    "email": "anitra@example.mil",
                }],
                "raw_payload": {
                    "notice_id": "taep-rfp",
                    "solicitation": "M6785426R8017",
                    "agency": "DEPT OF DEFENSE",
                    "subtier": "DEPT OF THE NAVY",
                    "office": "COMMANDER",
                    "posted": "2026-08-17",
                    "deadline": "2026-09-14T10:00:00-04:00",
                    "type": "Solicitation",
                    "set_aside_code": "8A",
                    "naics": "611710",
                    "psc": "U008",
                    "description_snippet": "Training and education analysis, design, development, implementation, and evaluation.",
                },
            }],
            "forecast_signals": {"matched": [{
                "source_id": "W15QKN-26-Q-0007",
                "source": "army_acquisition_forecast",
                "agency": "Department of the Army",
                "component": "USARC",
                "title": "MIRC Language Training Hours",
                "description": "Command: USARC; Contracting office: ACC-NJ",
                "anticipated_solicitation": "2026-07-10",
                "anticipated_solicitation_close": "2026-08-07",
                "estimated_value_range": "$25K to $250K",
                "predecessor_contract_id": "W15QKN25F0407",
                "url": "https://api.army.mil/forecast.xlsx",
                "source_fields": {"Contracting Office": "ACC-NJ"},
            }]},
        },
    }), encoding="utf-8")

    rows, receipt = evidence_pack_v2._research_mesh_records(artifact)

    taep = next(row for row in rows if row["record_id"] == "taep-rfp")
    mirc = next(row for row in rows
                if row["record_id"] == "W15QKN-26-Q-0007")
    assert taep["solicitation_number"] == "M6785426R8017"
    assert taep["response_deadline"] == "2026-09-14T10:00:00-04:00"
    assert taep["contact_email"] == "anitra@example.mil"
    assert mirc["sub_agency"] == "USARC"
    assert mirc["office"] == "ACC-NJ"
    assert mirc["predecessor_contract_id"] == "W15QKN25F0407"
    assert receipt["sam_notice_rows_imported"] == 1
    assert receipt["forecast_rows_imported"] == 1
