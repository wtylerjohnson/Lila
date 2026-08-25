"""JTG opportunity-gold contract and pure post-retrieval evaluator tests."""

from __future__ import annotations

import ast
import builtins
import copy
import json
from pathlib import Path

from agents.golden_press.opportunity_eval import (
    evaluate_opportunity_gold,
    validate_gold_contract,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/reference/opportunity_gold/jtg_inc.json"
INPUTS = ROOT / "tests/fixtures/golden_targets/jtg_inc_real_input_slice.json"
SCHEMA = ROOT / "agents/golden_press/schemas/opportunity_gold_v1.schema.json"


def _payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_jtg_gold_contract_is_strict_about_authority_status_and_shapes():
    schema = _payload(SCHEMA)
    contract = _payload(GOLD)

    assert schema["$schema"].endswith("draft/2020-12/schema")
    assert schema["additionalProperties"] is False
    assert "boundary_authority" in schema["required"]
    assert "adjudication_status" in schema["required"]
    assert schema["properties"]["boundary_authority"]["pattern"].startswith(
        "^operator-approved")
    assert set(schema["properties"]["adjudication_status"]["enum"]) == {
        "proposed_record_matrix_pending_operator_ratification",
        "ratified_operator_adjudication",
    }
    assert set(schema["$defs"]["outcomeState"]["enum"]) == {
        "qualified", "decision_required", "research_context", "excluded"}
    assert set(schema["$defs"]["target"]["required"]) == {
        "id", "kind", "must_lead", "title_hint", "why"}
    assert set(schema["$defs"]["adjudication"]["required"]) == {
        "id", "expected_fit", "expected_state"}
    assert set(schema["$defs"]["capabilityAnchor"]["required"]) == {
        "id", "expected_class", "supports", "published_obligations",
        "figure_type"}
    assert set(schema["$defs"]["noticeFamily"]["required"]) == {
        "solicitation_number", "expected_canonical_record_id",
        "expected_member_ids", "expected_member_count", "expected_deadline",
        "expected_fit", "expected_route", "expected_slot_5_state",
        "expected_slot_6"}
    assert set(schema["$defs"]["forecast"]["required"]) == {
        "id", "expected_slot", "predecessor_contract_id", "buyer_component",
        "buyer_office"}
    assert validate_gold_contract(contract) == []

    model_authority = copy.deepcopy(contract)
    model_authority["boundary_authority"] = "model-approved by Codex"
    assert any("operator-approved" in error or "model" in error
               for error in validate_gold_contract(model_authority))

    invented_status = copy.deepcopy(contract)
    invented_status["adjudication_status"] = "model_certified"
    assert any("adjudication_status is unsupported" in error
               for error in validate_gold_contract(invented_status))

    invented_state = copy.deepcopy(contract)
    invented_state["adjudications"][0]["expected_state"] = "model_guess"
    assert any("expected_state is unsupported" in error
               for error in validate_gold_contract(invented_state))


def _synthetic_evaluator_unit_result() -> tuple[dict, dict, dict]:
    """Build evaluator-shaped data only; this does not prove classification."""
    contract = _payload(GOLD)
    frozen = _payload(INPUTS)
    records = []
    qualified = []
    held = []
    slot_5_records = []
    slot_5_reviews = []

    for adjudication in contract["adjudications"]:
        identity = adjudication["id"]
        state = adjudication["expected_state"]
        row = {
            "record_id": identity,
            "title": identity,
            "service_fit": adjudication["expected_fit"],
            "commercial_route": adjudication.get("expected_route", "unknown"),
            "evidence_class": (
                "excluded" if state == "excluded" else
                "ambiguous" if state == "research_context" else
                "current_opportunity"),
        }
        records.append(row)
        if state == "qualified":
            qualified.append({"record_id": identity})
            slot_5_records.append({"source_id": identity})
        elif state == "decision_required":
            held.append({"record_id": identity})
            slot_5_reviews.append({
                "source_id": identity, "decision_state": "needs_review"})

    anchor_expectation = contract["capability_evidence_anchors"][0]
    records.append({
        **frozen["capability_anchor"],
        "evidence_class": anchor_expectation["expected_class"],
        "service_fit": "direct",
        "fit_basis": anchor_expectation["supports"],
        "published_obligations": anchor_expectation["published_obligations"],
        "figure_type": anchor_expectation["figure_type"],
    })

    family_expectation = contract["notice_family_expectations"][0]
    family_input = frozen["taep_family"]
    member_ids = [
        *(row["record_id"] for row in family_input["predecessors"]),
        *family_input["active_notice_ids"],
    ]
    canonical_id = family_input["active_notice_ids"][-1]
    family_key = "taep-family"
    taep = {
        "record_id": canonical_id,
        "requirement_family": family_key,
        "solicitation_number": family_input["solicitation_number"],
        "title": family_input["title"],
        "response_deadline": family_input["response_deadline"],
        "service_fit": family_expectation["expected_fit"],
        "commercial_route": family_expectation["expected_route"],
        "evidence_class": "current_opportunity",
        "family_member_ids": member_ids,
    }
    records.append(taep)
    qualified.append({"record_id": canonical_id})
    slot_5_records.append({
        "source_id": canonical_id,
        "solicitation_number": family_input["solicitation_number"],
    })

    forecast_expectation = contract["forecast_expectations"][0]
    forecast = {
        **frozen["forecast"],
        "evidence_class": "forecast",
        "service_fit": "direct",
        "commercial_route": "direct",
    }
    records.append(forecast)

    graph = {
        "classification_context": {
            "as_of": contract["classification_as_of"]},
        "records": records,
        "qualified_opportunity_records": qualified,
        "held_opportunities": held,
        "canonical_requirement_families": [{
            "requirement_family": family_key,
            "canonical_record_id": canonical_id,
            "family_member_ids": member_ids,
            "family_member_count": len(member_ids),
            "family_lineage": [
                {"record_id": identity,
                 "solicitation_number": family_input["solicitation_number"]}
                for identity in member_ids
            ],
        }],
    }
    product = {
        "as_of": contract["classification_as_of"][:10],
        "source_receipt": {
            "classification_as_of": contract["classification_as_of"]},
        "slots": [
        {"slot_id": "federal-opportunities", "records": slot_5_records,
         "review_records": slot_5_reviews},
        {"slot_id": "teaming-opportunities", "records": [{
            "source_id": canonical_id,
            "record_key": f"teaming-route:{canonical_id}:named_partner_teaming",
            "kind": "teaming_route_reference",
            "commercial_route": "named_partner_teaming",
            "non_owning_reference": True,
            "notice_reference_key": f"graph-record:{canonical_id}",
            "notice_reference_slot_id": "federal-opportunities",
        }]},
        {"slot_id": forecast_expectation["expected_slot"], "records": [{
            "source_id": forecast_expectation["id"]}]},
    ]}
    return contract, graph, product


def test_pure_evaluator_checks_exact_id_outcomes_family_route_and_forecast(
        monkeypatch):
    contract, graph, product = _synthetic_evaluator_unit_result()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("post-retrieval evaluator attempted file I/O")

    with monkeypatch.context() as denied:
        denied.setattr(builtins, "open", forbidden)
        denied.setattr(Path, "read_text", forbidden)
        denied.setattr(Path, "read_bytes", forbidden)
        receipt = evaluate_opportunity_gold(
            contract, graph=graph, product=product)

    assert receipt["passed"] is True
    assert receipt["contract_errors"] == []
    assert receipt["mismatches"] == []
    assert receipt["adjudication_status"] == (
        "proposed_record_matrix_pending_operator_ratification")
    assert "M6785426R8017" in receipt["evaluated_identities"]
    assert "W15QKN-26-Q-0007" in receipt["evaluated_identities"]
    assert any(
        row["scope"] == "capability_anchor"
        and row["field"] == "figure_type"
        and row["passed"]
        for row in receipt["checks"]
    )

    broken = copy.deepcopy(graph)
    target_id = "5c7f54f562d34cde905c634de9bc5a49"
    next(row for row in broken["records"]
         if row.get("record_id") == target_id)["service_fit"] = "unrelated"
    failed = evaluate_opportunity_gold(
        contract, graph=broken, product=product)
    assert failed["passed"] is False
    mismatch = next(row for row in failed["mismatches"]
                    if row["identity"] == target_id
                    and row["field"] == "service_fit")
    assert mismatch["expected"] == "direct"
    assert mismatch["actual"] == "unrelated"

    wrong_clock = copy.deepcopy(graph)
    wrong_clock["classification_context"]["as_of"] = "2026-08-18T00:00:00Z"
    failed_clock = evaluate_opportunity_gold(
        contract, graph=wrong_clock, product=product)
    assert any(row["field"] == "graph_classification_as_of"
               for row in failed_clock["mismatches"])

    wrong_canonical = copy.deepcopy(graph)
    wrong_canonical["canonical_requirement_families"][0][
        "canonical_record_id"] = contract[
            "notice_family_expectations"][0]["expected_member_ids"][0]
    failed_canonical = evaluate_opportunity_gold(
        contract, graph=wrong_canonical, product=product)
    assert any(row["field"] == "canonical_record_id"
               for row in failed_canonical["mismatches"])


def test_curated_fixture_runs_through_classifier_and_qualification_boundary():
    from agents.golden_press import evidence_route
    from agents.golden_press.evidence_pack_v2 import (
        canonicalize_requirement_families,
        qualify_opportunities,
    )

    contract = _payload(GOLD)
    frozen = _payload(INPUTS)
    context = evidence_route.build_context(
        "JTG, inc.",
        "jtg_inc",
        as_of=contract["classification_as_of"],
        root=ROOT,
    )
    classified = [
        evidence_route.classify_record(row, context)
        for row in canonicalize_requirement_families(frozen["notices"])
    ]
    qualified, _incumbents, held = qualify_opportunities(
        classified, context)
    by_id = {row["record_id"]: row for row in classified}
    qualified_ids = {row["record_id"] for row in qualified}
    held_ids = {row["record_id"] for row in held}

    for expected in contract["adjudications"]:
        identity = expected["id"]
        actual = by_id[identity]
        assert actual["service_fit"] == expected["expected_fit"], identity
        if "expected_route" in expected:
            assert actual["commercial_route"] == expected["expected_route"], \
                identity
        expected_state = expected["expected_state"]
        actual_state = (
            "qualified" if identity in qualified_ids else
            "decision_required" if identity in held_ids else
            "excluded" if actual["evidence_class"] == "excluded" else
            "research_context"
        )
        assert actual_state == expected_state, identity


def test_real_jtg_captured_inputs_pass_normal_graph_projection_and_gold(
        tmp_path):
    from agents.golden_press.evidence_pack_v2 import build_corrected_pack
    from agents.golden_press.external_product_projection import (
        build_external_product_document,
    )
    from agents.golden_press.external_product_render import (
        render_external_product,
        validate_external_product_document,
    )
    from agents.golden_press.market_map_projection import build_market_map
    from agents.golden_press.records import EvidencePack

    contract = _payload(GOLD)
    pressed_path = (
        ROOT / "data/state/candidate_review_v1/jtg_inc/"
        "jtg_inc.golden_report.evidence_pack.json")
    sweep_path = ROOT / "data/cleaned/searches_jtg_inc.json"
    deep_path = ROOT / "data/state/retrieval/deep_sweep_jtg_inc.json"
    for path in (pressed_path, sweep_path, deep_path):
        assert path.exists(), f"real JTG proof input missing: {path.name}"

    _path, graph = build_corrected_pack(
        "jtg_inc",
        "JTG, inc.",
        root=ROOT,
        pressed_pack_path=pressed_path,
        pack_dir=tmp_path / "pack",
        deep_sweep_path=deep_path,
        research_sweep_path=sweep_path,
        classification_as_of=contract["classification_as_of"],
        captured_at=contract["classification_as_of"],
        store_conn=None,
        cache_dir=tmp_path / "cache",
    )
    pressed_payload = _payload(pressed_path)
    pressed = EvidencePack.model_validate(pressed_payload)
    profile = _payload(ROOT / "clients/jtg_inc/profile.json")
    market_inputs = _payload(
        ROOT / "data/state/candidate_review_v1/jtg_inc/"
        "jtg_inc.market_map.inputs.json")
    market_inputs["evidence_pack_v2"] = graph
    market_map = build_market_map(
        pressed,
        profile=profile,
        slug="jtg_inc",
        as_of=contract["classification_as_of"][:10],
        inputs=market_inputs,
    )
    product = build_external_product_document(
        market_map=market_map,
        graph_payload=graph,
        evidence_pack=pressed,
        profile=profile,
        client_name="JTG, inc.",
        slug="jtg_inc",
        as_of=contract["classification_as_of"][:10],
    )

    assert validate_external_product_document(product) == []
    receipt = evaluate_opportunity_gold(
        contract, graph=graph, product=product.to_dict())
    assert receipt["passed"] is True, receipt["mismatches"]
    _studio, client_html = render_external_product(product)
    assert "M6785426R8017" in client_html
    assert "W15QKN-26-Q-0007" in client_html
    assert "https://sam.gov/opp/fd11917ee4044f4eab51e95e117c4a09/view" \
        in client_html


def test_retrieval_modules_cannot_import_evaluator_or_read_gold_contract():
    evaluator_path = ROOT / "agents/golden_press/opportunity_eval.py"
    evaluator_tree = ast.parse(evaluator_path.read_text(encoding="utf-8"))
    evaluator_imports = []
    evaluator_calls = []
    for node in ast.walk(evaluator_tree):
        if isinstance(node, ast.Import):
            evaluator_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            evaluator_imports.append(node.module or "")
        elif isinstance(node, ast.Call):
            evaluator_calls.append(node.func)
    assert not any("retrieval" in module for module in evaluator_imports)
    assert not any(isinstance(call, ast.Name) and call.id == "open"
                   for call in evaluator_calls)
    assert not any(isinstance(call, ast.Attribute)
                   and call.attr in {"read_text", "read_bytes"}
                   for call in evaluator_calls)

    retrieval_paths = [
        ROOT / "agents/golden_press/retrieval.py",
        *sorted((ROOT / "tools/retrieval").glob("*.py")),
    ]
    forbidden_strings = {
        "opportunity_eval", "opportunity_gold_v1",
        "data/reference/opportunity_gold", "jtg_inc.json",
    }
    for path in retrieval_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        strings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value.casefold())
        assert not any("opportunity_eval" in module for module in imports), path
        assert not any(token in value for token in forbidden_strings
                       for value in strings), path
