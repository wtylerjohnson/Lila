"""Truthful source-universe disclosure on every Signal Board."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from agents.reports import signal_board
from agents.reports.source_coverage import (
    CURRENT_FALLBACK_SNAPSHOT,
    CURRENT_SNAPSHOT,
    FAILED,
    NOT_RUN,
    NOT_USED,
    PARTIAL,
    PARTIAL_FALLBACK,
    RETURNED,
    RETURNED_FALLBACK,
    SCOPE_EXCLUDED,
    STALE_SNAPSHOT,
    STANDARD_SWEEP_LANES,
    coverage_from_attempts,
    coverage_from_sweep,
)
from test_signal_board import _full_model
from tools.api.source_catalog import CONNECTED_NOT_USED
from tools.api.source_mesh import SOURCE_IDS as SOURCE_MESH_IDS


FAILED_LANES = {"sbir", "contract_awards", "gdelt"}


def _latest_shape_attempts() -> list[dict]:
    standard_attempts = [
        {
            "source": source,
            "ok": source not in FAILED_LANES,
            "error": ("HTTP 429 PRIVATE VENDOR RESPONSE"
                      if source in FAILED_LANES else None),
        }
        for source, _label in STANDARD_SWEEP_LANES
        if source != "web"
    ]
    child_attempts = [
        {"source": source, "ok": True, "count": 1}
        for source in SOURCE_MESH_IDS
    ]
    return standard_attempts + child_attempts


def test_standard_catalog_order_and_latest_refresh_counts_are_exact():
    coverage = coverage_from_sweep({
        "results": {"_attempts": _latest_shape_attempts()},
    })

    standard = [row for row in coverage["lanes"]
                if row["scope"] == "standard"]
    assert [row["source"] for row in standard] == [
        source for source, _label in STANDARD_SWEEP_LANES]
    assert len(standard) == len(STANDARD_SWEEP_LANES)
    assert coverage["counts"] == {
        "standard": len(STANDARD_SWEEP_LANES),
        "additional": len(SOURCE_MESH_IDS),
        "attempted": (
            len(STANDARD_SWEEP_LANES) - 1 + len(SOURCE_MESH_IDS)
        ),
        "returned": (
            len(STANDARD_SWEEP_LANES) - 1 - len(FAILED_LANES)
            + len(SOURCE_MESH_IDS)
        ),
        "fallback": 0,
        "partial": 0,
        "current_snapshot": 0,
        "stale": 0,
        "failed": 3,
        "not_run": 1,
        "scope_excluded": 0,
        "connected_not_used": len(CONNECTED_NOT_USED),
    }
    by_source = {row["source"]: row["status"] for row in standard}
    assert by_source["web"] == NOT_RUN
    assert all(by_source[source] == FAILED for source in FAILED_LANES)
    assert by_source["sam.gov"] == RETURNED


def test_unknown_future_attempts_append_and_cannot_be_silently_omitted():
    coverage = coverage_from_attempts([
        {"source": "zeta_new_api", "ok": True},
        {"source": "alpha_new_api", "ok": False},
    ])

    additional = [row for row in coverage["lanes"]
                  if row["scope"] == "additional"]
    assert [(row["source"], row["status"]) for row in additional] == [
        ("alpha_new_api", FAILED),
        ("zeta_new_api", RETURNED),
    ]
    assert coverage["counts"]["additional"] == 2
    assert coverage["counts"]["attempted"] == 2
    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))
    assert 'data-source-key="alpha_new_api"' in html
    assert 'data-source-key="zeta_new_api"' in html
    assert "2 additional attempted lanes" in html


def test_attempt_manifest_preserves_partial_outcome_without_raw_errors():
    coverage = coverage_from_attempts([
        {"source": "news", "ok": True, "partial": True,
         "summary": "one of four feeds failed"},
    ])

    row = next(row for row in coverage["lanes"]
               if row["source"] == "news")
    assert row["status"] == PARTIAL
    assert coverage["counts"]["partial"] == 1
    assert coverage["counts"]["returned"] == 1


def test_result_provenance_refines_nominal_success_without_primary_claim():
    attempts = [
        {"source": "contract_awards", "ok": True},
        {"source": "sbir", "ok": True},
    ]
    coverage = coverage_from_sweep({"results": {
        "_attempts": attempts,
        "contract_awards": {
            "fallback_used": True,
            "source_mode": "live_usaspending_fallback",
            "source_attempts": [{
                "source": "SAM.gov", "status": "failed",
                "error": "HTTP 429 PRIVATE PRIMARY ERROR",
            }],
        },
        "sbir_gov": {"_provenance": {
            "mode": "stale_official_cache",
            "stale": True,
            "retrieved_at": "2026-07-18T01:02:03+00:00",
            "source_attempts": [{
                "status": "failed", "error": "SECRET PROVIDER ERROR",
            }],
        }},
    }})

    rows = {row["source"]: row for row in coverage["lanes"]}
    assert rows["contract_awards"] == {
        "source": "contract_awards",
        "label": "Contract award and period-end records",
        "status": RETURNED_FALLBACK,
        "scope": "standard",
        "coverage_class": "required-bounded",
        "coverage_boundary": (
            "Approved NAICS lanes and the configured 18-month period-end "
            "window. The keyless USAspending census covers prime contract "
            "and IDV award summaries with action dates from 2007-10-01 "
            "through the sweep as-of date, subject to explicit total-row, "
            "archive-size, and shard safety limits. It is not an all-history "
            "federal award universe."
        ),
        "detail": "Live USAspending.gov official fallback",
    }
    assert rows["sbir"]["status"] == STALE_SNAPSHOT
    assert rows["sbir"]["detail"] == (
        "Official SBIR snapshot as of 2026-07-18; no live API return")
    assert coverage["counts"]["returned"] == 1
    assert coverage["counts"]["fallback"] == 1
    assert coverage["counts"]["partial"] == 0
    assert coverage["counts"]["current_snapshot"] == 0
    assert coverage["counts"]["stale"] == 1
    assert coverage["counts"]["failed"] == 0
    assert "PRIVATE PRIMARY ERROR" not in json.dumps(coverage)
    assert "SECRET PROVIDER ERROR" not in json.dumps(coverage)

    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))
    assert RETURNED_FALLBACK in html
    assert STALE_SNAPSHOT in html
    assert "Live USAspending.gov official fallback" in html
    assert "Official SBIR snapshot as of 2026-07-18" in html
    assert "PRIVATE PRIMARY ERROR" not in html
    assert "SECRET PROVIDER ERROR" not in html


def test_contract_partial_fallback_discloses_public_safe_lane_denominator():
    coverage = coverage_from_sweep({"results": {
        "_attempts": [{"source": "contract_awards", "ok": True,
                       "partial": True}],
        "contract_awards": {
            "fallback_used": True,
            "source_mode": "live_usaspending_fallback",
            "partial": True,
            "attempted_naics_lanes": 6,
            "successful_naics_lanes": 4,
            "failed_naics_lanes": 2,
            "omitted_naics_lanes": 3,
            "provenance": {"lane_errors": ["SECRET LANE ERROR"]},
        },
    }})

    row = next(row for row in coverage["lanes"]
               if row["source"] == "contract_awards")
    assert row["status"] == PARTIAL_FALLBACK
    assert row["detail"] == (
        "USAspending.gov official fallback · 4 of 6 attempted NAICS lanes "
        "returned · 3 requested lanes omitted")
    assert coverage["counts"]["returned"] == 1
    assert coverage["counts"]["fallback"] == 1
    assert coverage["counts"]["partial"] == 1
    assert "SECRET LANE ERROR" not in json.dumps(coverage)


def test_sbir_live_fallback_and_current_day_snapshots_are_named_exactly():
    def projected(provenance):
        return coverage_from_sweep({"results": {
            "_attempts": [{"source": "sbir", "ok": True}],
            "sbir_gov": {"_provenance": provenance},
        }})

    live_listing = projected({
        "mode": "live_official_topics_listing",
        "retrieved_at": "2026-07-20T12:00:00+00:00",
    })
    current_api = projected({
        "mode": "official_daily_cache", "cache_origin": "solicitation_api",
        "retrieved_at": "2026-07-20T12:00:00+00:00",
    })
    current_listing = projected({
        "mode": "official_daily_cache", "cache_origin": "topics_listing",
        "retrieved_at": "2026-07-20T12:00:00+00:00",
    })

    def status(model):
        return next(row["status"] for row in model["lanes"]
                    if row["source"] == "sbir")

    assert status(live_listing) == RETURNED_FALLBACK
    assert status(current_api) == CURRENT_SNAPSHOT
    assert status(current_listing) == CURRENT_FALLBACK_SNAPSHOT
    assert current_api["counts"]["current_snapshot"] == 1
    assert current_api["counts"]["fallback"] == 0
    assert current_listing["counts"]["current_snapshot"] == 1
    assert current_listing["counts"]["fallback"] == 1


def test_partial_current_snapshot_dimensions_round_trip_through_html_lint():
    coverage = coverage_from_sweep({"results": {
        "_attempts": [{"source": "forecasts", "ok": True, "partial": True}],
        "forecast_signals": {"_provenance": {
            "mode": "official_daily_cache",
            "status": "partial",
            "partial": True,
            "retrieved_at": "2026-07-20T12:00:00+00:00",
        }},
    }})
    row = next(
        row for row in coverage["lanes"] if row["source"] == "forecasts"
    )
    assert row["status"] == PARTIAL
    assert row["current_snapshot"] is True
    assert coverage["counts"]["partial"] == 1
    assert coverage["counts"]["current_snapshot"] == 1

    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))

    assert 'data-source-current-snapshot="1"' in html
    ok, violations = signal_board.lint_signal_board(html)
    assert ok, violations


def test_public_projection_discloses_statuses_without_raw_vendor_errors():
    coverage = coverage_from_attempts(_latest_shape_attempts())
    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))

    assert "Research source coverage" in html
    counts = coverage["counts"]
    assert f"{counts['standard']} standard lanes" in html
    assert f"{counts['attempted']} attempted" in html
    assert f"{counts['returned']} returned" in html
    assert "3 rate-limited/failed" in html
    assert "1 not run this refresh" in html
    assert html.count('data-source-scope="standard"') == len(
        STANDARD_SWEEP_LANES
    )
    assert html.count(f'data-source-status="{FAILED}"') == 3
    assert html.count(f'data-source-status="{NOT_RUN}"') == 1
    assert html.count(f'data-source-status="{NOT_USED}"') == len(
        CONNECTED_NOT_USED
    )
    assert "HTTP 429" not in html
    assert "PRIVATE VENDOR RESPONSE" not in html
    assert "Federal organizations represented" in html
    assert "Research coverage</div>" not in html
    assert "Evidence dock" in html
    assert html.index("Research source coverage") < html.index("Evidence dock")
    assert "SAM.gov Notice Detail and Resources" in html
    assert "SAM.gov Entity Management and Exclusions" in html
    assert "GSA Acquisition Gateway forecast" in html
    assert "did not contribute evidence or records" in html
    assert "HTTP 429" not in json.dumps(coverage)


def test_client_source_coverage_is_collapsible_connected_integration_map():
    coverage = coverage_from_attempts(_latest_shape_attempts())
    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))

    details = re.search(
        r'<details class="sb-source-coverage-details"([^>]*)>', html)
    assert details is not None
    assert "open" not in details.group(1)
    assert (
        '<summary class="sb-source-coverage-summary" '
        'id="researchSourceSummary" '
        'aria-controls="researchSourceDetails">'
    ) in html
    assert (
        '<div id="researchSourceDetails" role="region" '
        'aria-labelledby="researchSourceSummary">'
    ) in html
    connected_count = (
        len(STANDARD_SWEEP_LANES)
        + len(SOURCE_MESH_IDS)
        + len(CONNECTED_NOT_USED)
    )
    assert f"{connected_count} connected research sources" in html
    assert "CONNECTED FEDERAL RESEARCH MESH" in html
    assert html.count(
        '<span class="sb-source-client-only">CONNECTED</span>'
    ) == connected_count
    assert ".sb-source-operator-only { display: none !important; }" in html
    assert "<script" not in html


def test_source_coverage_retains_operator_truth_behind_local_view_switch():
    coverage = coverage_from_attempts(_latest_shape_attempts())
    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))

    assert (
        '<span class="sb-source-operator-only">'
        f'<span class="sb-source-counts" '
        f'data-standard="{len(STANDARD_SWEEP_LANES)}"'
    ) in html
    assert "3 rate-limited/failed" in html
    assert html.count(
        '<span class="sb-source-operator-only">RATE-LIMITED/FAILED</span>'
    ) == 3
    assert (
        '#research-sources[data-source-view="operator"] '
        '.sb-source-client-only { display: none !important; }'
    ) in html
    assert (
        '#research-sources[data-source-view="operator"] '
        '.sb-source-operator-only { display: inline !important; }'
    ) in html


def test_assessment_document_carries_stored_sweep_coverage_into_board_model(
        monkeypatch):
    from agents.reports.document import build_document
    from test_assessment_document import _searches

    sweep = _searches(2)
    sweep["results"]["_attempts"] = _latest_shape_attempts()
    monkeypatch.setattr(
        "tools.capability.client_display_name", lambda name: name)
    doc = build_document(
        "Testco", searches=sweep, qualify={}, as_of=date(2026, 7, 19),
        _live_report=False, _cap_profile=None,
        _log_unresolved_entities=False,
    )
    model = signal_board.build_model(
        doc,
        report_date="19 JUL 2026",
        figures=[{"retrieved_at": "2026-07-19T00:00:00+00:00"}],
    )

    expected_attempted = (
        len(STANDARD_SWEEP_LANES) - 1 + len(SOURCE_MESH_IDS)
    )
    expected_returned = expected_attempted - len(FAILED_LANES)
    assert doc.research_source_coverage["counts"]["attempted"] == (
        expected_attempted
    )
    assert model["source_coverage"] == doc.research_source_coverage
    assert model["source_coverage"]["counts"]["returned"] == expected_returned


def test_document_and_renderer_preserve_fallback_and_stale_source_modes(
        monkeypatch):
    from agents.reports.document import build_document
    from test_assessment_document import _searches

    sweep = _searches(2)
    sweep["results"]["_attempts"] = [
        {"source": "contract_awards", "ok": True},
        {"source": "sbir", "ok": True},
    ]
    sweep["results"]["contract_awards"] = {
        "fallback_used": True,
        "source_mode": "live_usaspending_fallback",
    }
    sweep["results"]["sbir_gov"] = {"_provenance": {
        "mode": "stale_official_cache",
        "stale": True,
        "retrieved_at": "2026-07-18T01:02:03+00:00",
    }}
    monkeypatch.setattr(
        "tools.capability.client_display_name", lambda name: name)
    doc = build_document(
        "Testco", searches=sweep, qualify={}, as_of=date(2026, 7, 19),
        _live_report=False, _cap_profile=None,
        _log_unresolved_entities=False,
    )
    model = signal_board.build_model(
        doc,
        report_date="19 JUL 2026",
        figures=[{"retrieved_at": "2026-07-19T00:00:00+00:00"}],
    )
    html = signal_board.render_signal_board(model)

    assert RETURNED_FALLBACK in html
    assert STALE_SNAPSHOT in html
    assert "Live USAspending.gov official fallback" in html
    assert "Official SBIR snapshot as of 2026-07-18" in html


def test_source_coverage_is_a_required_ordered_lint_band():
    html = signal_board.render_signal_board(dict(
        _full_model(),
        source_coverage=coverage_from_attempts(_latest_shape_attempts()),
    ))
    ok, violations = signal_board.lint_signal_board(html)
    assert ok, violations

    missing_band = html.replace(
        'id="research-sources"', 'id="removed-research-sources"', 1)
    ok, violations = signal_board.lint_signal_board(missing_band)
    assert not ok
    assert 'required band missing: id="research-sources"' in violations

    missing_row = re.sub(
        r'<div class="sb-source-lane[^>]*data-source-scope="standard".*?'
        r'</span><strong>.*?</strong></div>',
        "",
        html,
        count=1,
        flags=re.DOTALL,
    )
    ok, violations = signal_board.lint_signal_board(missing_row)
    assert not ok
    assert any(
        f"must render all {len(STANDARD_SWEEP_LANES)} standard lanes" in item
               for item in violations)


def test_scope_excluded_lane_renders_explicitly_and_reconciles():
    payload = {"records": [], "_provenance": {
        "schema_version": 1,
        "source": "darpa_opportunities",
        "status": "complete",
        "mode": "scope-excluded",
        "retrieval_mode": "unknown",
        "record_count": None,
        "public_detail": (
            "Not retrieved; excluded by engagement scope "
            "(preset:civilian@v1 · off-scope:DoD)"
        ),
    }}
    coverage = coverage_from_sweep({"results": {
        "_attempts": [{
            "source": "darpa_opportunities",
            "ok": True,
            "scope_excluded": True,
            "provenance": payload["_provenance"],
        }],
        "darpa_opportunities": payload,
    }})
    html = signal_board.render_signal_board(dict(
        _full_model(), source_coverage=coverage))

    assert (
        'data-source-key="darpa_opportunities" '
        f'data-source-status="{SCOPE_EXCLUDED}"'
    ) in html
    assert 'data-scope-excluded="1"' in html
    assert "1 excluded by engagement scope" in html
    assert "Not retrieved; excluded by engagement scope" in html
    ok, violations = signal_board.lint_signal_board(html)
    assert ok, violations


def test_source_coverage_lint_rejects_duplicate_keys_status_and_count_drift():
    html = signal_board.render_signal_board(dict(
        _full_model(),
        source_coverage=coverage_from_attempts(_latest_shape_attempts()),
    ))

    duplicate_key = html.replace(
        'data-source-key="usaspending.gov"',
        'data-source-key="sam.gov"', 1)
    ok, violations = signal_board.lint_signal_board(duplicate_key)
    assert not ok
    assert any("keys/order differ" in item for item in violations)

    bad_status = html.replace(
        f'data-source-status="{RETURNED}"',
        'data-source-status="UNVERIFIED SUCCESS"', 1)
    ok, violations = signal_board.lint_signal_board(bad_status)
    assert not ok
    assert any("invalid statuses" in item for item in violations)

    returned = coverage_from_attempts(_latest_shape_attempts())["counts"][
        "returned"
    ]
    bad_count = html.replace(
        f'data-returned="{returned}"',
        f'data-returned="{returned - 1}"',
        1,
    )
    ok, violations = signal_board.lint_signal_board(bad_count)
    assert not ok
    assert any("counts do not reconcile" in item for item in violations)
