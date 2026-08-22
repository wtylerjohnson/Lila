"""Press snapshot delta: deterministic retention-product change classes."""

from __future__ import annotations

import copy

import pytest

from agents.press_delta import (
    DELTA_CLASS_ORDER,
    baseline_delta,
    canonical_json,
    compute_delta,
    delta_feed,
    render_internal_markdown,
)


T0 = "2026-07-17T12:00:00+00:00"
T1 = "2026-07-18T12:00:00+00:00"


def _verification(status="COMPLETE", reason=None):
    return {
        "status": status,
        "reason": reason,
        "verified": ["usaspending:A1"],
        "disputed": [],
        "unverifiable": [],
    }


def _figure(key, values, *, comparable=True, retrieved=T0):
    observations = []
    for item in values:
        metric, value = item if isinstance(item, tuple) else ("reported_value", item)
        observations.append({
            "metric": metric,
            "value": value,
            "display_text": str(value),
            "retrieved_at": retrieved,
            "generated_internal_id": None,
            "component_values": [],
            "disputed": False,
        })
    return {
        "key": key,
        "source_system": "usaspending",
        "source_record_id": key.split(":", 1)[-1],
        "source_url": f"https://www.usaspending.gov/award/{key.split(':')[-1]}",
        "comparable": comparable,
        "values": observations,
    }


def _snapshot(timestamp=T0):
    return {
        "schema_version": 1,
        "client_name": "Testco",
        "slug": "testco",
        "press_timestamp": timestamp,
        "source_artifacts": {
            "sweep": {"path": "data/cleaned/searches_testco.json",
                      "sha256": "0" * 64, "generated_at": timestamp},
            "figure_source": "board_content_registry",
        },
        "candidates": [
            {"id": "candidate-keep", "kind": "sam", "title": "Keep",
             "score": 8, "tier": "core"},
            {"id": "candidate-drop", "kind": "sam", "title": "Drop",
             "score": 7, "tier": "core"},
        ],
        "candidate_screen": [
            {"id": "candidate-keep", "kind": "sam", "title": "Keep",
             "score": 8, "tier": "core", "relevant": True,
             "off_scope": False, "excluded_by_code": False,
             "response_deadline": "2026-08-15", "scope_basis": "UNSCOPED"},
            {"id": "candidate-drop", "kind": "sam", "title": "Drop",
             "score": 7, "tier": "core", "relevant": True,
             "off_scope": False, "excluded_by_code": False,
             "response_deadline": "2026-07-17", "scope_basis": "UNSCOPED"},
        ],
        "figures": [
            _figure("usaspending:A1", [("amount", 100.0)]),
            _figure("usaspending:MULTI", [
                ("amount", 10.0), ("potential_ceiling", 20.0)]),
            _figure("unkeyed:aggregate", [
                ("component_sum", 40.0)], comparable=False),
        ],
        "windows": [
            {"key": "sam:candidate-keep:response_deadline",
             "source_system": "sam", "source_record_id": "candidate-keep",
             "field": "response_deadline", "value": "2026-08-15"},
            {"key": "sam:candidate-drop:response_deadline",
             "source_system": "sam", "source_record_id": "candidate-drop",
             "field": "response_deadline", "value": "2026-07-17"},
        ],
        "recompete_events": [
            {"key": "recompete-1", "kind": "recompete",
             "date": "2027-01-01", "source_system": "usaspending",
             "source_record_id": "A1", "posture": "attack",
             "score": 45, "tier": "program"},
        ],
        "gate_state": {"status": "CLEAN", "render_date": "2026-07-17",
                       "violations": []},
        "verification": _verification(),
    }


def _changed_snapshot():
    current = _snapshot(T1)
    current["candidates"] = [
        {"id": "candidate-keep", "kind": "sam", "title": "Keep",
         "score": 8, "tier": "core"},
        {"id": "candidate-new", "kind": "sam", "title": "New",
         "score": 9, "tier": "core"},
    ]
    current["candidate_screen"] = [
        {"id": "candidate-keep", "kind": "sam", "title": "Keep",
         "score": 8, "tier": "core", "relevant": True,
         "off_scope": False, "excluded_by_code": False,
         "response_deadline": "2026-09-01", "scope_basis": "UNSCOPED"},
        {"id": "candidate-drop", "kind": "sam", "title": "Drop",
         "score": 7, "tier": "core", "relevant": False,
         "off_scope": False, "excluded_by_code": False,
         "response_deadline": "2026-07-17", "scope_basis": "UNSCOPED"},
        {"id": "candidate-new", "kind": "sam", "title": "New",
         "score": 9, "tier": "core", "relevant": True,
         "off_scope": False, "excluded_by_code": False,
         "response_deadline": None, "scope_basis": "UNSCOPED"},
    ]
    current["figures"] = [
        _figure("usaspending:A1", [("amount", 125.0)], retrieved=T1),
        _figure("usaspending:MULTI", [
            ("potential_ceiling", 20.0), ("amount", 10.0)], retrieved=T1),
        _figure("unkeyed:aggregate", [
            ("component_sum", 99.0)], comparable=False, retrieved=T1),
    ]
    current["windows"] = [
        {"key": "sam:candidate-keep:response_deadline",
         "source_system": "sam", "source_record_id": "candidate-keep",
         "field": "response_deadline", "value": "2026-09-01"},
    ]
    current["recompete_events"].append(
        {"key": "recompete-2", "kind": "recompete",
         "date": "2027-03-01", "source_system": "usaspending",
         "source_record_id": "A2", "posture": "attack",
         "score": 50, "tier": "program"})
    current["gate_state"] = {
        "status": "DO_NOT_SEND",
        "render_date": "2026-07-18",
        "violations": ["FIGURE_DISPUTED"],
    }
    current["verification"] = _verification(
        "DEFERRED", "adversarial verify pass unavailable on this baseline")
    return current


def test_all_delta_classes_have_fixed_order_and_exact_fixture_payloads():
    delta = compute_delta(_snapshot(), _changed_snapshot())

    assert [item["kind"] for item in delta["items"]] == list(DELTA_CLASS_ORDER)
    assert delta["counts"] == {kind: 1 for kind in DELTA_CLASS_ORDER}
    assert delta["items"][0]["id"] == "candidate-new"

    dropped = delta["items"][1]
    assert dropped["id"] == "candidate-drop"
    assert dropped["reason"] == "expired_window"

    drift = delta["items"][2]
    assert drift["id"] == "usaspending:A1"
    assert drift["before"]["values"] == [{"metric": "amount", "value": 100.0}]
    assert drift["after"]["values"] == [{"metric": "amount", "value": 125.0}]
    assert drift["verification"] == {
        "status": "DEFERRED",
        "reason": "adversarial verify pass unavailable on this baseline",
    }

    moved = delta["items"][3]
    assert moved == {
        "kind": "WINDOW_MOVED",
        "id": "sam:candidate-keep:response_deadline",
        "before": {"response_deadline": "2026-08-15"},
        "after": {"response_deadline": "2026-09-01"},
    }
    assert delta["items"][4]["id"] == "recompete-2"
    assert delta["items"][5]["before"]["status"] == "CLEAN"
    assert delta["items"][5]["after"]["status"] == "DO_NOT_SEND"


@pytest.mark.parametrize(
    ("screen", "window_end", "expected"),
    [
        ({"off_scope": True, "below_threshold": True},
         "2026-07-17", "expired_window"),
        ({"off_scope": True, "below_threshold": True},
         "2026-08-17", "out_of_scope"),
        ({"score": 2, "relevant": False},
         "2026-08-17", "fell_below_threshold"),
        ({"score": 7, "relevant": False}, "2026-08-17", "unknown"),
        (None, "2026-08-17", "not_present"),
    ],
)
def test_drop_reason_precedence(screen, window_end, expected):
    before = _snapshot()
    before["windows"][1]["value"] = window_end
    after = copy.deepcopy(before)
    after["press_timestamp"] = T1
    after["candidates"] = [after["candidates"][0]]
    after["candidate_screen"] = [after["candidate_screen"][0]]
    if screen is not None:
        after["candidate_screen"].append({
            "id": "candidate-drop",
            "response_deadline": window_end,
            **screen,
        })

    delta = compute_delta(before, after)

    dropped = next(item for item in delta["items"] if item["kind"] == "DROPPED")
    assert dropped["reason"] == expected


def test_drop_expiry_is_derived_from_exact_snapshot_window_shape():
    before = _snapshot()
    after = copy.deepcopy(before)
    after["press_timestamp"] = T1
    after["candidates"] = [after["candidates"][0]]
    after["candidate_screen"] = [after["candidate_screen"][0]]
    after["windows"] = [after["windows"][0]]

    delta = compute_delta(before, after)

    dropped = next(item for item in delta["items"] if item["kind"] == "DROPPED")
    assert dropped["reason"] == "expired_window"


def test_figure_multisets_ignore_order_and_retrieval_time_and_skip_unkeyed():
    before = _snapshot()
    after = copy.deepcopy(before)
    after["press_timestamp"] = T1
    after["figures"][0] = _figure(
        "usaspending:A1", [("amount", 100.0)], retrieved=T1)
    after["figures"][1] = _figure(
        "usaspending:MULTI", [
            ("potential_ceiling", 20.0), ("amount", 10.0)], retrieved=T1)
    after["figures"][2] = _figure(
        "unkeyed:aggregate", [
            ("component_sum", 999.0)], comparable=False, retrieved=T1)

    delta = compute_delta(before, after)

    assert not [item for item in delta["items"]
                if item["kind"] == "FIGURE_DRIFT"]

    after["figures"][1] = _figure(
        "usaspending:MULTI", [
            ("amount", 20.0), ("potential_ceiling", 10.0)], retrieved=T1)
    changed = compute_delta(before, after)
    drift = [item for item in changed["items"]
             if item["kind"] == "FIGURE_DRIFT"]
    assert [item["id"] for item in drift] == ["usaspending:MULTI"]


def test_dispute_observer_runs_once_per_drift_and_delta_never_resolves(
        monkeypatch):
    from agents.reports import verification

    def forbidden(*_args, **_kwargs):
        raise AssertionError("delta attempted to resolve a figure dispute")

    monkeypatch.setattr(verification, "arbitrate", forbidden)
    monkeypatch.setattr(verification, "append_resolution", forbidden)
    observed = []

    def observer(item):
        observed.append(item)
        return {
            "status": "COMPLETE",
            "reason": None,
            "verified": [],
            "disputed": [item["id"]],
            "unverifiable": [],
        }

    delta = compute_delta(
        _snapshot(), _changed_snapshot(), dispute_observer=observer)

    assert [item["id"] for item in observed] == ["usaspending:A1"]
    drift = next(item for item in delta["items"]
                 if item["kind"] == "FIGURE_DRIFT")
    assert drift["verification"]["status"] == "COMPLETE"
    assert drift["verification"]["disputed"] == ["usaspending:A1"]


def test_order_and_serialization_are_deterministic_and_idempotent():
    before = _snapshot()
    after = _changed_snapshot()
    once = compute_delta(before, after)

    permuted_before = copy.deepcopy(before)
    permuted_after = copy.deepcopy(after)
    for field in ("candidates", "candidate_screen", "figures", "windows",
                  "recompete_events"):
        permuted_before[field].reverse()
        permuted_after[field].reverse()
    twice = compute_delta(permuted_before, permuted_after)

    assert twice == once
    assert canonical_json(twice) == canonical_json(once)
    assert canonical_json(once) == canonical_json(once)


def test_self_delta_is_empty_and_first_press_creates_zero_item_baseline():
    snapshot = _snapshot()

    self_delta = compute_delta(snapshot, snapshot)
    baseline = baseline_delta(snapshot)

    assert self_delta["baseline_created"] is False
    assert self_delta["items"] == []
    assert baseline["baseline_created"] is True
    assert baseline["before_timestamp"] is None
    assert baseline["after_timestamp"] == T0
    assert baseline["items"] == []
    assert all(count == 0 for count in baseline["counts"].values())


def test_gate_render_date_alone_is_not_a_gate_state_change():
    before = _snapshot()
    after = copy.deepcopy(before)
    after["press_timestamp"] = T1
    after["gate_state"]["render_date"] = "2026-07-18"

    delta = compute_delta(before, after)

    assert not [item for item in delta["items"]
                if item["kind"] == "GATE_STATE_CHANGED"]


def test_internal_markdown_and_feed_are_stable_internal_seams():
    delta = compute_delta(_snapshot(), _changed_snapshot())

    markdown = render_internal_markdown(delta)
    feed = delta_feed(delta)

    assert markdown.startswith("# INTERNAL · Testco · Refresh delta")
    assert "—" not in markdown
    assert "## Figure Drift" in markdown
    assert feed["total"] == 6
    assert feed["suppressed"] == 0
    assert len(feed["items"]) == feed["total"]
    assert set(feed["items"][0]) == {
        "id", "kind", "slug", "client_name", "text", "when"}
    assert {item["when"] for item in feed["items"]} == {"2026-07-18"}
    assert feed == delta_feed(delta)


def test_snapshot_identity_mismatch_fails_loudly():
    current = _changed_snapshot()
    current["client_name"] = "Otherco"
    with pytest.raises(ValueError, match="different clients"):
        compute_delta(_snapshot(), current)


def test_accepts_the_snapshot_owners_pydantic_model_directly():
    from agents.press_snapshot import PressSnapshot

    snapshot = PressSnapshot.model_validate(_snapshot())

    assert compute_delta(snapshot, snapshot)["items"] == []
    assert baseline_delta(snapshot)["baseline_created"] is True
