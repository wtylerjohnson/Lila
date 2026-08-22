"""Sweep-level completeness is stricter than a successful fan-out process."""
from __future__ import annotations

import pytest

from agents.reports.source_coverage import (
    COMPREHENSIVE,
    NOT_COMPREHENSIVE,
    STANDARD_SWEEP_LANES,
    VERDICT_COMPLETE,
    VERDICT_COMPLETE_WITHIN_BOUNDARY,
    VERDICT_FAILED,
    VERDICT_GATED,
    VERDICT_NOT_RUN,
    VERDICT_PARTIAL,
    VERDICT_SCOPE_EXCLUDED,
    VERDICT_SCREENED_ZERO,
    VERDICT_STALE,
    coverage_verdict_from_sweep,
)
from tools.api.source_catalog import STANDARD_SOURCE_SPECS


def _required_lane_count() -> int:
    return sum(
        spec.coverage_class != "advisory"
        for spec in STANDARD_SOURCE_SPECS
    )


def _bounded_sources() -> tuple[str, ...]:
    return tuple(
        spec.task_key
        for spec in STANDARD_SOURCE_SPECS
        if spec.task_key and spec.coverage_class == "required-bounded"
    )


def _attempts() -> list[dict]:
    spec_by_task = {
        spec.task_key: spec for spec in STANDARD_SOURCE_SPECS if spec.task_key
    }
    rows = []
    for source, _label in STANDARD_SWEEP_LANES:
        row = {"source": source, "ok": True, "count": 1}
        spec = spec_by_task[source]
        if spec.coverage_class == "required-bounded":
            row["coverage_contract"] = {
                "required_origins": list(spec.coverage_origins),
                "returned_origins": list(spec.coverage_origins),
                "complete_within_boundary": True,
            }
        rows.append(row)
    return rows


def _lane(verdict, source: str):
    return next(lane for lane in verdict.lanes if lane.source == source)


def test_complete_and_explicit_screened_zero_are_comprehensive():
    attempts = _attempts()
    attempts[0]["count"] = 0

    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
    }})

    assert verdict.comprehensive is True
    assert verdict.verdict == COMPREHENSIVE
    assert verdict.blocking_sources == ()
    assert _lane(verdict, attempts[0]["source"]).state == VERDICT_SCREENED_ZERO
    assert _lane(verdict, attempts[1]["source"]).state == (
        VERDICT_COMPLETE_WITHIN_BOUNDARY)
    projected = verdict.as_dict()
    assert projected["required_lanes"] == _required_lane_count()
    assert projected["counts"][VERDICT_SCREENED_ZERO] == 1
    bounded_count = sum(
        spec.coverage_class == "required-bounded"
        for spec in STANDARD_SOURCE_SPECS
    )
    assert projected["counts"][VERDICT_COMPLETE_WITHIN_BOUNDARY] == bounded_count
    assert projected["counts"][VERDICT_COMPLETE] == (
        len(STANDARD_SWEEP_LANES) - bounded_count - 1)
    assert projected["coverage_contract_met"] is True
    assert projected["source_coverage_complete"] is True
    assert projected["universe_comprehensive"] is False
    assert projected["advisory_gaps"] == []
    assert projected["bounded_sources"] == list(_bounded_sources())


def test_advisory_partial_is_visible_but_does_not_block_decision_coverage():
    attempts = _attempts()
    row = next(item for item in attempts if item["source"] == "gdelt")
    row["partial"] = True

    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
    }})

    lane = _lane(verdict, "gdelt")
    assert lane.coverage_class == "advisory"
    assert lane.required is False
    assert lane.state == VERDICT_PARTIAL
    assert verdict.comprehensive is True
    assert verdict.blocking_sources == ()
    assert verdict.advisory_gaps == ("gdelt",)
    projected = verdict.as_dict()
    assert projected["coverage_contract_met"] is True
    assert projected["universe_comprehensive"] is False
    assert projected["advisory_gaps"] == ["gdelt"]


def _bounded_budget_payload(*statuses: str) -> dict:
    origins = (
        "pacific_deterrence_initiative",
        "counter_drug_activities",
    )
    return {
        "records": [{"id": "budget-row"}],
        "_provenance": {
            "schema_version": 1,
            "source": "dod_budget_exhibits",
            "status": "partial",
            "mode": "live_official_bounded",
            "retrieval_mode": "live",
            "record_count": 1,
            "attempts": [
                {
                    "source": origins[index - 1],
                    "status": status,
                    "count": 1 if status == "success" else 0,
                }
                for index, status in enumerate(statuses, start=1)
            ],
        },
    }


def test_required_bounded_partial_is_complete_when_every_declared_origin_returns():
    attempts = _attempts()
    row = next(
        item for item in attempts
        if item["source"] == "dod_budget_exhibits"
    )
    row["partial"] = True

    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
        "dod_budget_exhibits": _bounded_budget_payload(
            "success", "success"),
    }})

    lane = _lane(verdict, "dod_budget_exhibits")
    assert lane.coverage_class == "required-bounded"
    assert lane.required is True
    assert lane.state == VERDICT_COMPLETE_WITHIN_BOUNDARY
    assert lane.dimensions == (VERDICT_COMPLETE_WITHIN_BOUNDARY,)
    assert lane.coverage_boundary
    assert verdict.comprehensive is True
    assert verdict.blocking_sources == ()
    assert verdict.bounded_sources == _bounded_sources()
    assert verdict.universe_comprehensive is False


def test_required_bounded_partial_blocks_when_one_declared_origin_fails():
    attempts = _attempts()
    row = next(
        item for item in attempts
        if item["source"] == "dod_budget_exhibits"
    )
    row["partial"] = True

    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
        "dod_budget_exhibits": _bounded_budget_payload(
            "success", "failed"),
    }})

    lane = _lane(verdict, "dod_budget_exhibits")
    assert lane.state == VERDICT_PARTIAL
    assert lane.dimensions == (VERDICT_PARTIAL,)
    assert verdict.comprehensive is False
    assert verdict.blocking_sources == ("dod_budget_exhibits",)


def test_bounded_contract_awards_partial_without_receipt_blocks_contract():
    attempts = _attempts()
    row = next(
        item for item in attempts if item["source"] == "contract_awards"
    )
    row["partial"] = True
    row.pop("coverage_contract")

    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
    }})

    lane = _lane(verdict, "contract_awards")
    assert lane.coverage_class == "required-bounded"
    assert lane.required is True
    assert lane.state == VERDICT_PARTIAL
    assert verdict.comprehensive is False
    assert verdict.blocking_sources == ("contract_awards",)


@pytest.mark.parametrize(
    ("defect", "expected_state"),
    [
        ("partial", VERDICT_PARTIAL),
        ("stale", VERDICT_STALE),
        ("failed", VERDICT_FAILED),
        ("not-run", VERDICT_NOT_RUN),
        ("gated", VERDICT_GATED),
    ],
)
def test_each_required_lane_defect_prevents_comprehensive_claim(
    defect: str,
    expected_state: str,
):
    attempts = _attempts()
    source = "sbir"
    row = next(item for item in attempts if item["source"] == source)
    results: dict = {"_attempts": attempts}
    kwargs = {}

    if defect == "partial":
        row["partial"] = True
    elif defect == "stale":
        results["sbir_gov"] = {"_provenance": {
            "schema_version": 1,
            "source": "sbir",
            "status": "complete",
            "mode": "stale_official_cache",
            "retrieval_mode": "official-cache",
            "stale": True,
            "record_count": 1,
        }}
    elif defect == "failed":
        row["ok"] = False
    elif defect == "not-run":
        attempts.remove(row)
    elif defect == "gated":
        kwargs["gated_sources"] = {source}

    verdict = coverage_verdict_from_sweep(
        {"results": results}, **kwargs,
    )

    assert verdict.comprehensive is False
    assert verdict.verdict == NOT_COMPREHENSIVE
    assert verdict.blocking_sources == (source,)
    assert _lane(verdict, source).state == expected_state


def test_partial_stale_lane_retains_both_blocking_dimensions():
    attempts = _attempts()
    row = next(item for item in attempts if item["source"] == "sbir")
    row["partial"] = True
    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
        "sbir_gov": {"_provenance": {
            "schema_version": 1,
            "source": "sbir",
            "status": "partial",
            "mode": "stale_official_cache",
            "retrieval_mode": "official-cache",
            "stale": True,
            "record_count": 1,
        }},
    }})

    lane = _lane(verdict, "sbir")
    assert lane.state == VERDICT_PARTIAL
    assert lane.dimensions == (VERDICT_PARTIAL, VERDICT_STALE)
    assert verdict.as_dict()["counts"][VERDICT_STALE] == 1
    assert verdict.comprehensive is False


def test_scope_excluded_lane_is_named_and_removed_from_required_denominator():
    attempts = _attempts()
    source = "darpa_opportunities"
    row = next(item for item in attempts if item["source"] == source)
    provenance = {
        "schema_version": 1,
        "source": source,
        "status": "complete",
        "mode": "scope-excluded",
        "retrieval_mode": "unknown",
    }
    row.update({"scope_excluded": True, "provenance": provenance})

    verdict = coverage_verdict_from_sweep({"results": {
        "_attempts": attempts,
        source: {"records": [], "_provenance": provenance},
    }})

    lane = _lane(verdict, source)
    assert lane.state == VERDICT_SCOPE_EXCLUDED
    assert lane.required is False
    assert verdict.comprehensive is True
    assert verdict.as_dict()["required_lanes"] == _required_lane_count() - 1


def test_unknown_gated_lane_fails_named_instead_of_being_ignored():
    with pytest.raises(ValueError, match="unknown standard lanes"):
        coverage_verdict_from_sweep(
            {"results": {"_attempts": _attempts()}},
            gated_sources={"typo_lane"},
        )


def test_structurally_inapplicable_lane_can_leave_required_denominator():
    attempts = _attempts()
    attempts = [row for row in attempts if row["source"] != "hierarchy"]

    verdict = coverage_verdict_from_sweep(
        {"results": {"_attempts": attempts}},
        scope_excluded_sources={"hierarchy"},
    )

    lane = _lane(verdict, "hierarchy")
    assert verdict.comprehensive is True
    assert lane.required is False
    assert lane.state == VERDICT_SCOPE_EXCLUDED
