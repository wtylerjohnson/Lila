"""Incremental cache and existing-system adapter acceptance tests."""

from __future__ import annotations

import pytest

from agents.golden_press import evidence_route as er
from tools.intelligence_graph.adapter import ExistingSystemsGraphAdapter


def _context():
    return er.build_context(
        "JTG, inc.",
        "jtg_inc",
        pack={"client_entity_aliases": [], "generated_at": "2026-08-20"},
        packet={"strategy": {"research_entities": [{
            "kind": "competitor",
            "name": "SOSi (SOS International)",
            "rationale": "named rival for cleared federal translation",
        }]}},
        profile={
            "capability_summary": "Language services and training: "
                                  "translation and interpretation.",
            "capability_terms": {
                "core": ["translation and interpretation", "linguist support"],
                "adjacent": ["language proficiency testing"],
                "excluded": [],
            },
        },
        route_facts={
            "named_partners": ["MAXIMUS"],
            "validated_competitors": [],
            "certifications": ["small business"],
        },
    )


def _adapter(tmp_path, **kwargs):
    return ExistingSystemsGraphAdapter(
        root=tmp_path,
        slug="jtg_inc",
        client_name="JTG, inc.",
        classification_context=_context(),
        cache_dir=tmp_path / "cache",
        **kwargs,
    )


def _notice(record_id: str, title: str) -> dict:
    return {
        "lane": "L1_notice",
        "record_id": record_id,
        "title": title,
        "description": "translation and interpretation support",
        "response_deadline": "2026-09-30",
        "set_aside": "",
    }


def test_unchanged_press_reuses_and_one_changed_record_invalidates(tmp_path):
    first_a = _notice("A", "Translation and Interpretation Services")
    first_b = _notice("B", "Linguist Support Services")

    cold = _adapter(tmp_path)
    cold_a = cold.classify_record(first_a)
    cold_b = cold.classify_record(first_b)
    assert cold.receipt()["namespaces"]["classification_decisions"] == {
        "hits": 0,
        "misses": 2,
        "reasons": {"absent": 2},
    }

    warm = _adapter(tmp_path)
    assert warm.classify_record(first_a) == cold_a
    assert warm.classify_record(first_b) == cold_b
    assert warm.receipt()["namespaces"]["classification_decisions"] == {
        "hits": 2,
        "misses": 0,
        "reasons": {"reused": 2},
    }

    changed = _adapter(tmp_path)
    changed_a = dict(first_a, title="Aerobics Instructor Services",
                     description="physical fitness instruction")
    changed_result = changed.classify_record(changed_a)
    unchanged_result = changed.classify_record(first_b)
    stats = changed.receipt()["namespaces"]["classification_decisions"]
    assert stats == {
        "hits": 1,
        "misses": 1,
        "reasons": {"input_changed": 1, "reused": 1},
    }
    assert changed_result == er.classify_record(changed_a, _context())
    assert unchanged_result == er.classify_record(first_b, _context())
    assert changed_result["evidence_class"] == "excluded"


@pytest.mark.parametrize(("field", "baseline", "changed", "dimension"), [
    (
        "set_aside_code",
        {**_notice("FIELD", "Translation Services")},
        {**_notice("FIELD", "Translation Services"),
         "set_aside_code": "8A"},
        "commercial_route",
    ),
    (
        "type_set_aside",
        {**_notice("FIELD", "Translation Services")},
        {**_notice("FIELD", "Translation Services"),
         "type_set_aside": "8A"},
        "commercial_route",
    ),
    (
        "source_fields",
        {**_notice("FIELD", "Translation Services"), "source_fields": {}},
        {**_notice("FIELD", "Translation Services"),
         "source_fields": {"set_aside_code": "8A"}},
        "commercial_route",
    ),
    (
        "anticipated_solicitation",
        {"lane": "L4_forecast", "record_id": "FIELD",
         "title": "Translation and Interpretation Forecast"},
        {"lane": "L4_forecast", "record_id": "FIELD",
         "title": "Translation and Interpretation Forecast",
         "anticipated_solicitation": "2026-09-30"},
        "window_state",
    ),
    (
        "anticipated_solicitation_close",
        {"lane": "L4_forecast", "record_id": "FIELD",
         "title": "Translation and Interpretation Forecast"},
        {"lane": "L4_forecast", "record_id": "FIELD",
         "title": "Translation and Interpretation Forecast",
         "anticipated_solicitation_close": "2026-09-30"},
        "window_state",
    ),
])
def test_every_classifier_read_field_invalidates_cached_decision(
        tmp_path, field, baseline, changed, dimension):
    cold = _adapter(tmp_path)
    first = cold.classify_record(baseline)

    rebuilt = _adapter(tmp_path)
    second = rebuilt.classify_record(changed)
    stats = rebuilt.receipt()["namespaces"]["classification_decisions"]

    assert field in changed
    assert stats == {
        "hits": 0,
        "misses": 1,
        "reasons": {"input_changed": 1},
    }
    assert second == er.classify_record(changed, _context())
    assert second[dimension] != first[dimension]


def test_amount_change_stays_fresh_without_reclassifying_record(tmp_path):
    first = {
        "lane": "L2_entity_award",
        "record_id": "AWARD-1",
        "title": "Translation and Interpretation Services",
        "description": "linguist support",
        "recipient": "MAXIMUS",
        "obligated_dollars": 100.0,
    }
    second = dict(first, obligated_dollars=250.0)

    adapter = _adapter(tmp_path)
    classified_first = adapter.classify_record(first)
    classified_second = adapter.classify_record(second)

    assert classified_first["obligated_dollars"] == 100.0
    assert classified_second["obligated_dollars"] == 250.0
    assert classified_second["evidence_class"] == \
        classified_first["evidence_class"]
    assert adapter.receipt()["namespaces"]["classification_decisions"] == {
        "hits": 1,
        "misses": 1,
        "reasons": {"absent": 1, "reused": 1},
    }


def test_source_url_and_mark_resolvers_compute_once(tmp_path):
    adapter = _adapter(tmp_path)
    calls = {"source": 0, "url": 0, "mark": 0}

    def compute(kind, value):
        calls[kind] += 1
        return value

    for _ in range(2):
        assert adapter.cache_source_extract(
            "sam:A", source_revision={"etag": "v1"},
            extractor=lambda: compute("source", {"title": "A"}),
        ) == {"title": "A"}
        assert adapter.resolve_url(
            "A", "https://sam.gov/opp/A/view",
            resolver=lambda: compute("url", {
                "url": "https://sam.gov/opp/A/view", "resolved": True}),
        )["resolved"] is True
        assert adapter.resolve_mark(
            "agency:state", {"agency": "Department of State"},
            resolver=lambda: compute("mark", {"asset": "state-seal"}),
        ) == {"asset": "state-seal"}
    assert calls == {"source": 1, "url": 1, "mark": 1}


def test_apollo_rows_survive_repress_with_requirement_lineage(tmp_path):
    adapter = _adapter(tmp_path)
    rows = [{
        "apollo_id": "person-1",
        "name": "A Person",
        "title": "Program Manager",
        "organization": "Department of State",
        "email": "person@example.gov",
        "mobile": "+1 202-555-0101",
        "requirement_family": "family-abc",
    }]
    assert adapter.merge_apollo_enrichments(rows) == rows

    replay = _adapter(tmp_path).merge_apollo_enrichments(None)
    assert replay == rows
    assert replay[0]["requirement_family"] == "family-abc"


def test_requirement_edge_deletion_prunes_only_unsupported_targets(tmp_path):
    rows = [
        {
            "apollo_id": "person-1", "name": "A Person",
            "requirement_family": "family-a",
        },
        {
            "apollo_id": "person-1", "name": "A Person",
            "requirement_family": "family-b",
        },
    ]
    adapter = _adapter(tmp_path)
    assert len(adapter.merge_apollo_enrichments(
        rows, active_requirement_families={"family-a", "family-b"})) == 2

    one_edge_removed = _adapter(tmp_path)
    retained = one_edge_removed.merge_apollo_enrichments(
        None, active_requirement_families={"family-b"})
    assert [row["requirement_family"] for row in retained] == ["family-b"]
    assert one_edge_removed.receipt()["actions"]["pruned"] == 1

    all_edges_removed = _adapter(tmp_path)
    assert all_edges_removed.merge_apollo_enrichments(
        None, active_requirement_families=set()) == []
    assert all_edges_removed.receipt()["actions"]["pruned"] == 1


@pytest.mark.parametrize("changed", [
    {"schema_version": "evidence-pack-v3"},
    {"rule_version": "rules-v-next"},
    {"model_version": "model-v-next"},
    {"configuration": {"minimum_score": 0.75}},
    {"deterministic_seed": 41},
    {"as_of": "2026-08-21"},
])
def test_semantic_dependency_change_invalidates_without_changing_meaning(
        tmp_path, changed):
    record = _notice("A", "Translation and Interpretation Services")
    baseline = _adapter(tmp_path)
    expected = baseline.classify_record(record)

    rebuilt = _adapter(tmp_path, **changed)
    assert rebuilt.classify_record(record) == expected
    stats = rebuilt.receipt()["namespaces"]["classification_decisions"]
    assert stats == {
        "hits": 0,
        "misses": 1,
        "reasons": {"dependency_changed": 1},
    }
    assert rebuilt.receipt()["actions"]["invalidations"] >= 1


def test_mark_cache_is_client_scoped(tmp_path):
    calls = []
    jtg = _adapter(tmp_path)
    arista = ExistingSystemsGraphAdapter(
        root=tmp_path,
        slug="arista",
        client_name="Arista Networks",
        classification_context=_context(),
        cache_dir=tmp_path / "cache",
    )
    assert jtg.resolve_mark(
        "agency:state", {"brand": "jtg"},
        resolver=lambda: calls.append("jtg") or {"asset": "jtg-state"},
    ) == {"asset": "jtg-state"}
    assert arista.resolve_mark(
        "agency:state", {"brand": "arista"},
        resolver=lambda: calls.append("arista") or {"asset": "arista-state"},
    ) == {"asset": "arista-state"}
    assert calls == ["jtg", "arista"]


def test_adapter_receipts_existing_system_ownership(tmp_path):
    receipt = _adapter(tmp_path).receipt()
    assert receipt["providers"]["embeddings"]["owner"] == \
        "tools.retrieval.dense"
    assert receipt["providers"]["contact_graph"]["owner"] == \
        "tools.contact_graph"
    assert receipt["providers"]["classification"]["kind"] == \
        "migrated_consumer"
    assert receipt["providers"]["renderer"]["kind"] == \
        "graph_backed_consumer"


def test_semantic_receipt_is_identical_for_cold_and_warm_cache(tmp_path):
    record = _notice("A", "Translation and Interpretation Services")
    cold = _adapter(tmp_path)
    cold.classify_record(record)

    warm = _adapter(tmp_path)
    warm.classify_record(record)

    assert cold.receipt()["actions"] != warm.receipt()["actions"]
    assert cold.semantic_receipt() == warm.semantic_receipt()
    semantic = cold.semantic_receipt()
    assert semantic["schema_version"] == \
        "incremental-cache-semantic-receipt-v1"
    serialized = repr(semantic)
    for forbidden in (
            "cache_root", "exists", "hits", "misses", "actions", "path"):
        assert forbidden not in serialized
