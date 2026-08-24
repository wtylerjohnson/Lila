"""The two-dimension evidence model (JTG upstream order, 2026-08-20).

Every ordered pin, fixture-driven, no hardcoded record ids:
  1. the client cannot classify as its own competitor;
  2. a named partner/prime stays named_partner_teaming absent separate
     rival evidence;
  3. a genuine overlapping language-services provider classifies
     competitive_historical;
  4. route_relationship never changes evidence_class (independence law);
  5. one canonical requirement survives once through retrieval,
     classification and pack assembly;
  6. closed notices never appear as live pursuits;
  7. access-restricted opportunities stay partner-route or
     ineligible-direct, never promoted to direct pursuits;
  8. opportunity-linked targets stay attached to the correct requirement
     family.
"""

from __future__ import annotations

import pytest

from agents.golden_press import evidence_route as er

PACKET = {"strategy": {"research_entities": [
    {"kind": "competitor", "name": "SOSi (SOS International)",
     "rationale": "named rival for cleared federal translation"},
    {"kind": "competitor", "name": "Alutiiq",
     "rationale": "competitor in language-services federal contracting"},
]}}
ROUTE_FACTS = {
    "named_partners": ["MAXIMUS", "InDyne, Inc.", "CAE USA"],
    "validated_competitors": [],
    "certifications": ["small business"],
}
PROFILE = {
    "capability_summary": "Language services and training: translation "
                          "and interpretation, localization.",
    "capability_terms": {
        "core": ["translation and interpretation",
                 "interpretation and translation",
                 "translation and interpreting",
                 "language training", "Military Language Instructor",
                 "linguist support", "localization"],
        "adjacent": ["language proficiency testing"], "excluded": []}}


@pytest.fixture()
def ctx():
    return er.build_context(
        "JTG, inc.", "jtg_inc", pack={"client_entity_aliases": [],
                                      "generated_at": "2026-08-20"},
        packet=PACKET, profile=PROFILE, route_facts=ROUTE_FACTS)


def _award(recipient, description):
    return {"lane": "L2_entity_award", "recipient": recipient,
            "description": description, "title": "",
            "record_id": "x", "relevance_matched": ""}


def test_client_can_never_classify_as_its_own_competitor(ctx):
    row = er.classify_record(
        _award("JTG, INC.", "TRANSLATION SERVICES"), ctx)
    assert row["evidence_class"] == "client_historical"
    assert row["route_relationship"] == "direct"
    # every alias shape resolves to the client, never to competition
    for name in ("JTG", "Jtg, Inc.", "JTG INC."):
        assert er.classify_evidence(_award(name, "language services"),
                                    ctx)["evidence_class"] == \
            "client_historical"


def test_named_partner_stays_teaming_without_rival_evidence(ctx):
    row = er.classify_record(
        _award("MAXIMUS FEDERAL SERVICES, INC.",
               "OPERATIONS AND TECHNOLOGY SUPPORT INCLUDING LANGUAGE "
               "TRAINING DELIVERY"), ctx)
    # in-scope paper without market validation: held out of competitive
    # totals, teaming value on the route dimension
    assert row["evidence_class"] == "ambiguous"
    assert row["route_relationship"] == "named_partner_teaming"
    assert "held out of competitive totals" in row["evidence_basis"]

    out_of_scope = er.classify_record(
        _award("CAE USA INC.", "FLIGHT SIMULATOR MAINTENANCE"), ctx)
    assert out_of_scope["evidence_class"] == "excluded"
    assert out_of_scope["route_relationship"] == "named_partner_teaming"


def test_validated_overlapping_provider_is_competitive_historical(ctx):
    row = er.classify_record(
        _award("SOS INTERNATIONAL LLC",
               "LINGUIST SUPPORT AND TRANSLATION AND INTERPRETATION "
               "SERVICES"), ctx)
    assert row["evidence_class"] == "competitive_historical"
    assert "approved packet research entity" in row["evidence_basis"]
    assert row["route_relationship"] == "incumbent"

    # validated name but out-of-scope paper: resolved not-competitive,
    # never competitive on co-occurrence alone
    off = er.classify_record(
        _award("ALUTIIQ GLOBAL SOLUTIONS, LLC",
               "BASE OPERATIONS SUPPORT, GROUNDS MAINTENANCE"), ctx)
    assert off["evidence_class"] == "excluded"
    assert "resolved not-competitive" in off["evidence_basis"]


def test_route_never_changes_evidence_class(ctx):
    record = _award("SOS INTERNATIONAL LLC",
                    "TRANSLATION AND INTERPRETATION SERVICES")
    before = er.classify_evidence(record, ctx)
    ctx_no_routes = dict(ctx, named_partners={}, )
    after_route = er.classify_route(record, ctx)
    # classify_evidence output is byte-identical with or without any
    # route computation, and route output carries no class key
    assert er.classify_evidence(record, ctx_no_routes) == before
    assert "evidence_class" not in after_route
    assert {"route_relationship", "route_basis"}.issubset(after_route)
    assert after_route["commercial_route"] == \
        after_route["route_relationship"]
    assert isinstance(after_route["eligible_route"], bool)
    combined = er.classify_record(record, ctx)
    assert combined["evidence_class"] == before["evidence_class"]


def test_closed_notice_is_never_a_live_pursuit(ctx):
    closed = {"lane": "L1_notice",
              "title": "Translation and Interpretation Services",
              "description": "", "response_deadline": "2026-01-13",
              "set_aside": ""}
    row = er.classify_record(closed, ctx)
    assert row["evidence_class"] == "excluded"
    assert "never a live pursuit" in row["evidence_basis"]
    live = dict(closed, response_deadline="2026-09-30")
    assert er.classify_evidence(live, ctx)["evidence_class"] == \
        "current_opportunity"
    undated = dict(closed, response_deadline="")
    assert er.classify_evidence(undated, ctx)["evidence_class"] == \
        "ambiguous"


def test_same_day_notice_uses_timezone_aware_deadline_instant(ctx):
    notice = {
        "lane": "L1_notice",
        "title": "Translation and Interpretation Services",
        "description": "",
        "response_deadline": "2026-08-24T11:00:00-05:00",
        "set_aside": "",
    }
    before = dict(ctx, as_of="2026-08-24T15:59:59Z")
    after = dict(ctx, as_of="2026-08-24T16:00:01Z")

    assert er.classify_record(notice, before)["evidence_class"] == \
        "current_opportunity"
    classified = er.classify_record(notice, after)
    assert classified["window_state"] == "stated_past"
    assert classified["evidence_class"] == "excluded"
    assert "2026-08-24T11:00:00-05:00" in classified["window_basis"]


def test_date_only_deadline_means_end_of_utc_day(ctx):
    notice = {
        "lane": "L1_notice",
        "title": "Translation and Interpretation Services",
        "description": "",
        "response_deadline": "2026-08-24",
        "set_aside": "",
    }

    same_day = er.classify_record(
        notice, dict(ctx, as_of="2026-08-24T23:59:59Z"))
    next_day = er.classify_record(
        notice, dict(ctx, as_of="2026-08-25T00:00:00Z"))

    assert same_day["window_state"] == "live"
    assert next_day["window_state"] == "stated_past"


def test_naive_deadline_timestamp_has_unknown_window(ctx):
    notice = {
        "lane": "L1_notice",
        "title": "Translation and Interpretation Services",
        "description": "",
        "response_deadline": "2026-08-24T11:00:00",
        "set_aside": "",
    }

    classified = er.classify_record(
        notice, dict(ctx, as_of="2026-08-24T18:00:00Z"))

    assert classified["window_state"] == "unstated"
    assert classified["evidence_class"] == "ambiguous"
    assert "timezone-aware" in classified["window_basis"]


def test_forecast_uses_published_anticipated_solicitation_clock(ctx):
    forecast = {
        "lane": "L4_forecast",
        "title": "Future Language Program",
        "description": "translation and interpretation services",
        "anticipated_solicitation": "07/20/2026",
        "anticipated_award": "09/01/2026",
        "fiscal_year": "2026",
    }

    classified = er.classify_record(
        forecast, dict(ctx, as_of="2026-08-24T18:00:00Z"))

    assert classified["evidence_class"] == "forecast"
    assert classified["window_state"] == "stated_past"
    assert "07/20/2026" in classified["window_basis"]
    assert "successor refresh" in classified["evidence_basis"]


def test_aerobics_and_generic_instructor_records_fail_fit(ctx):
    for title in ("Aerobics Instructor Services",
                  "Fitness and Wellness Instructor",
                  "General Classroom Instructor Support"):
        record = {"lane": "L1_notice", "title": title,
                  "description": "instruction and training services",
                  "response_deadline": "2026-09-30", "set_aside": ""}
        row = er.classify_record(record, ctx)
        assert row["service_fit"] == "unrelated"
        assert row["evidence_class"] == "excluded"

    language = {"lane": "L1_notice",
                "title": "Foreign Language Instructor Services",
                "description": "translation and interpretation support",
                "response_deadline": "2026-09-30", "set_aside": ""}
    row = er.classify_record(language, ctx)
    assert row["service_fit"] == "direct"
    assert row["evidence_class"] == "current_opportunity"


def test_pack_derived_semantic_pairs_preserve_real_jtg_wording(ctx):
    valid_titles = (
        "Department of War Language Interpretation and Translation "
        "Enterprise (DLITE) III DRAFT Solicitation",
        "BPA - Translation and Interpreting Service PSC R608",
        "Language Instructor Services for U.S. Embassy Islamabad",
    )
    for title in valid_titles:
        record = {
            "lane": "L1_notice", "title": title, "description": "",
            "response_deadline": "2026-09-30", "set_aside": "",
        }
        row = er.classify_record(record, ctx)
        assert row["service_fit"] == "direct", row["fit_basis"]
        assert row["evidence_class"] == "current_opportunity"

    aerobics = {
        "lane": "L1_notice", "title": "Aerobics Instructor Services",
        "description": "physical fitness instruction and training",
        "response_deadline": "2026-09-30", "set_aside": "",
    }
    row = er.classify_record(aerobics, ctx)
    assert row["service_fit"] == "unrelated"
    assert row["evidence_class"] == "excluded"


def test_stated_past_forecast_never_becomes_current_opportunity(ctx):
    record = {"lane": "L4_forecast",
              "title": "Translation and Interpretation Support",
              "description": "language training and linguist support",
              "release_date": "2026-03-01"}
    row = er.classify_record(record, ctx)
    assert row["window_state"] == "stated_past"
    assert row["evidence_class"] == "forecast"
    assert "successor refresh" in row["evidence_basis"]


def test_indian_set_aside_requires_eligible_route(ctx):
    record = {"lane": "L1_notice",
              "title": "Translation and Interpretation Services",
              "description": "language services",
              "response_deadline": "2026-09-30", "set_aside": "IEE"}
    row = er.classify_record(record, ctx)
    assert row["evidence_class"] == "current_opportunity"
    assert row["commercial_route"] == "possible_subcontracting"
    assert row["eligible_route"] is False

    certified = dict(ctx, client_certifications=[
        "indian economic enterprise"])
    route = er.classify_route(record, certified)
    assert route["commercial_route"] == "direct"
    assert route["eligible_route"] is True


def test_long_set_aside_labels_and_blank_access_fail_closed(ctx):
    base = {
        "lane": "L1_notice",
        "title": "Translation and Interpretation Services",
        "description": "language services",
        "response_deadline": "2026-09-30",
    }
    small = er.classify_route(
        {**base, "set_aside": "Total Small Business Set-Aside"}, ctx)
    assert small["commercial_route"] == "direct"
    assert small["eligible_route"] is True

    sam_variant = er.classify_route(
        {**base, "set_aside": "Small Business Set Aside - Total"}, ctx)
    assert sam_variant["commercial_route"] == "direct"
    assert sam_variant["eligible_route"] is True

    veteran = er.classify_route(
        {**base, "set_aside": "Veteran-Owned Small Business Set Aside"}, ctx)
    assert veteran["commercial_route"] == "possible_subcontracting"
    assert veteran["eligible_route"] is False

    sdvosb = er.classify_route(
        {**base, "set_aside": (
            "Service-Disabled Veteran-Owned Small Business Set-Aside")}, ctx)
    assert sdvosb["commercial_route"] == "possible_subcontracting"
    assert sdvosb["eligible_route"] is False

    blank = er.classify_route({**base, "set_aside": ""}, ctx)
    assert blank["commercial_route"] == "unknown"
    assert blank["eligible_route"] is False
    assert "unresolved" in blank["route_basis"]

    unrestricted = er.classify_route(
        {**base, "set_aside": "No Set aside used"}, ctx)
    assert unrestricted["commercial_route"] == "direct"
    assert unrestricted["eligible_route"] is True


def test_relationship_provenance_is_structured(ctx):
    row = er.classify_record(
        _award("MAXIMUS FEDERAL SERVICES, INC.",
               "TRANSLATION AND INTERPRETATION SERVICES"), ctx)
    assert set(row["relationship_provenance"]) == {
        "evidence_class", "commercial_route", "window_state", "service_fit"}
    for receipt in row["relationship_provenance"].values():
        assert receipt["kind"] in er.PROVENANCE_TYPES
        assert receipt["confidence"] in {"high", "medium", "low"}
        assert receipt["rule_id"]


def test_access_restricted_stays_partner_route_never_direct(ctx):
    sdvosb = {"lane": "L1_notice", "title": "Tele-Interpreter Services",
              "description": "Nexus Universal has provided foreign "
                             "language translation services since 2021.",
              "response_deadline": "2026-09-17", "set_aside": "SDVOSBS"}
    row = er.classify_record(sdvosb, ctx)
    assert row["evidence_class"] == "current_opportunity"  # class intact
    assert row["route_relationship"] == "named_partner_teaming"
    assert "bars direct pursuit" in row["route_basis"]

    open_route = er.classify_route(
        dict(sdvosb, set_aside=""), ctx)
    assert open_route["route_relationship"] == "incumbent"
    assert open_route["eligible_route"] is False
    assert "Nexus Universal" in open_route["route_basis"]

    # small-business set-aside with the client's own certification: direct
    sba = er.classify_route(
        {"lane": "L1_notice", "title": "Translation Services",
         "description": "", "response_deadline": "2026-09-01",
         "set_aside": "SBA"}, ctx)
    assert sba["route_relationship"] == "direct"


def test_canonical_requirement_survives_once(ctx):
    from agents.golden_press.evidence_pack_v2 import dedupe_requirements
    rows = [
        {"lane": "L1_notice", "record_id": "n1",
         "title": "WSNC Interpreter Translation Services IDIQ",
         "agency": "VETERANS AFFAIRS, DEPARTMENT OF",
         "response_deadline": "2026-09-24", "evidence_class":
             "current_opportunity"},
        {"lane": "L1_notice", "record_id": "n2",
         "title": "WSNC Interpreter Translation Services IDIQ",
         "agency": "VETERANS AFFAIRS, DEPARTMENT OF",
         "response_deadline": "2026-09-24", "evidence_class":
             "current_opportunity"},
        {"lane": "L1_notice", "record_id": "n3",
         "title": "Different Requirement", "agency": "GSA",
         "response_deadline": "2026-09-01", "evidence_class":
             "current_opportunity"},
    ]
    kept = dedupe_requirements(rows)
    assert len(kept) == 2
    families = {r["requirement_family"] for r in kept}
    assert len(families) == 2


def test_targets_stay_attached_to_their_requirement_family(ctx):
    from agents.golden_press.evidence_pack_v2 import build_target_groups
    opportunities = [
        {"record_id": "n1", "requirement_family": "fam-a",
         "evidence_class": "current_opportunity",
         "contact_name": "Pat CO", "contact_email": "pat.co@va.gov",
         "contact_phone": "555-0100", "title": "Interpreter IDIQ"},
        {"record_id": "n3", "requirement_family": "fam-b",
         "evidence_class": "current_opportunity",
         "contact_name": "Sam KO", "contact_email": "sam.ko@gsa.gov",
         "contact_phone": "", "title": "Translation BPA"},
    ]
    groups = build_target_groups(opportunities, incumbents={
        "fam-a": "Nexus Universal"})
    assert set(groups) == {"fam-a", "fam-b"}
    fam_a_roles = {t["role"] for t in groups["fam-a"]}
    assert "contracting_officer_or_specialist" in fam_a_roles
    assert any(t.get("enrichment_candidate") and
               t["organization"] == "Nexus Universal"
               for t in groups["fam-a"])
    # provenance is exact and public-source for every government contact
    for rows in groups.values():
        for t in rows:
            if not t.get("enrichment_candidate"):
                assert t["provenance"].startswith("sam_notice:")
    # no cross-attachment: fam-b carries only its own notice's people
    assert all(t.get("source_record_id") == "n3"
               for t in groups["fam-b"] if not t.get("enrichment_candidate"))


def test_legacy_event_lane_keeps_event_evidence(ctx):
    row = er.classify_record({
        "lane": "L5_event", "record_id": "event-1",
        "title": "Federal Language Industry Day",
    }, ctx)
    assert row["evidence_class"] == "event"
