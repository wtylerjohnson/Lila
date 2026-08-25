from __future__ import annotations

import json

import pytest

from tools.intelligence_graph.workflow import (
    PURSUIT_PROMOTION_VERSION,
    ROLE_CONTRACT,
    build_ambiguity_queue,
    validate_role_contract,
    workflow_contract_receipt,
)
from tools.run_graph_stage import (
    REQUIRED_VISUAL_SURFACES,
    STAGE_ORDER,
    STAGE_TESTS,
    validate_visual_receipts,
)


def test_complementary_roles_have_one_production_writer():
    assert validate_role_contract() == []
    receipt = workflow_contract_receipt()
    assert receipt["certified"] is True
    assert receipt["roles"]["reviewer"]["mode"] == "read_only"
    assert receipt["roles"]["visual_qa"]["starts_after"] == \
        "data_layer_passed"
    assert receipt["roles"]["research_agent"]["reads"] == \
        ["research_queue"]
    assert receipt["pursuit_promotion_version"] == PURSUIT_PROMOTION_VERSION
    assert "route controls prime, team, or verify" in \
        receipt["pursuit_promotion_law"]


def test_second_schema_writer_is_rejected():
    roles = {name: dict(definition)
             for name, definition in ROLE_CONTRACT.items()}
    roles["reviewer"] = {**roles["reviewer"], "owns": ["schema"]}
    violations = validate_role_contract(roles)
    assert {row["rule_id"] for row in violations} == {
        "W001_EXCLUSIVE_WRITE_OWNER",
        "W002_READ_ROLE_CANNOT_OWN_PRODUCTION_DOMAIN",
    }


def test_research_queue_contains_only_classifier_ambiguity():
    records = [
        {"record_id": "evidence", "evidence_class": "ambiguous",
         "service_fit": "direct", "window_state": "live",
         "evidence_basis": "scope can support two evidence classes"},
        {"record_id": "fit", "evidence_class": "current_opportunity",
         "service_fit": "ambiguous", "window_state": "live",
         "fit_basis": "scope text is incomplete"},
        {"record_id": "adjacent", "evidence_class": "current_opportunity",
         "service_fit": "adjacent", "window_state": "live",
         "fit_basis": "adjacent capability evidence needs a decision"},
        {"record_id": "route", "evidence_class": "current_opportunity",
         "service_fit": "direct", "window_state": "live",
         "qualification_state": "qualified", "route_action": "verify",
         "route_basis": "published access is unstated"},
        {"record_id": "excluded", "evidence_class": "excluded",
         "service_fit": "ambiguous", "window_state": "live"},
        {"record_id": "unrelated", "evidence_class": "current_opportunity",
         "service_fit": "unrelated", "window_state": "live",
         "qualification_state": "needs_eligible_route"},
        {"record_id": "past", "evidence_class": "ambiguous",
         "service_fit": "direct", "window_state": "stated_past"},
        {"record_id": "resolved", "evidence_class": "current_opportunity",
         "service_fit": "direct", "window_state": "live"},
    ]
    queued = build_ambiguity_queue(records)
    assert [row["record_id"] for row in queued] == [
        "adjacent", "evidence", "fit", "route"]
    assert queued == build_ambiguity_queue(records)
    assert queued[0]["reasons"] == [
        "adjacent capability evidence needs a decision"]
    assert queued[3]["ambiguous_dimensions"] == ["route_eligibility"]
    assert queued[3]["route_action"] == "verify"
    assert queued[3]["reasons"] == ["published access is unstated"]


def test_test_plan_runs_full_suite_only_at_milestone():
    assert STAGE_ORDER == ("graph", "jtg", "renderer", "milestone", "release")
    assert STAGE_TESTS["milestone"] == ("tests",)
    assert all(paths != ("tests",) for stage, paths in STAGE_TESTS.items()
               if stage != "milestone")


def test_release_requires_same_commit_receipts_for_every_surface(tmp_path):
    head = "abc123"
    paths = []
    for surface in REQUIRED_VISUAL_SURFACES:
        path = tmp_path / f"{surface}.json"
        path.write_text(json.dumps({
            "receipt_id": f"visual-{surface}",
            "surface": surface,
            "status": "passed",
            "git_head": head,
        }), encoding="utf-8")
        paths.append(path)
    receipts = validate_visual_receipts(
        paths, expected_head=head)
    assert {row["surface"] for row in receipts} == \
        set(REQUIRED_VISUAL_SURFACES)
    with pytest.raises(ValueError, match="links_marks"):
        validate_visual_receipts(paths[:-1], expected_head=head)
