"""Generic source provenance and record-level freshness preservation."""

from __future__ import annotations

from agents.reports.horizon import build_fact_bank
from agents.reports.horizon_discovery.base import program_entry
from agents.reports.source_coverage import (
    PARTIAL,
    coverage_from_sweep,
)
from run_searches import attempt_row
from tools.api.provenance import provenance_from_payload


def test_program_cache_shape_normalizes_without_leaking_errors():
    payload = {"records": [], "_provenance": {
        "source": "reginfo_unified_agenda",
        "mode": "stale_official_cache",
        "status": "partial",
        "retrieved_at": "2026-07-18T12:00:00+00:00",
        "data_as_of": "2026-06-30",
        "stale": True,
        "partial": True,
        "records_received": 0,
        "source_attempts": [{
            "source": "official_source", "status": "failed",
            "error": "PRIVATE PROVIDER FAILURE",
        }],
        "limitations": "official snapshot fallback",
    }}
    envelope = provenance_from_payload(
        payload, source="reginfo_unified_agenda")
    assert envelope is not None
    assert envelope.status == "partial"
    assert envelope.retrieval_mode == "official-cache"
    assert envelope.retrieved_at.isoformat() == "2026-07-18T12:00:00+00:00"
    assert envelope.data_as_of == "2026-06-30"
    public = envelope.public_projection()
    assert "attempts" not in public
    assert "limitations" not in public
    assert "PRIVATE PROVIDER FAILURE" not in str(public)


def test_generic_envelope_drives_attempt_and_public_coverage_status():
    failed_payload = {"records": [], "_provenance": {
        "source": "foreign_assistance",
        "mode": "failure",
        "status": "failure",
        "retrieved_at": "2026-07-20T12:00:00+00:00",
        "error": "PRIVATE NETWORK ERROR",
    }}
    attempt = attempt_row(
        "foreign_assistance", failed_payload, None, 1.0, None)
    assert attempt["ok"] is False
    assert "PRIVATE NETWORK ERROR" not in str(attempt["provenance"])

    coverage = coverage_from_sweep({"results": {
        "_attempts": [{
            "source": "reginfo_unified_agenda", "ok": True,
            "partial": True,
        }],
        "reginfo_unified_agenda": {"records": [], "_provenance": {
            "source": "reginfo_unified_agenda",
            "mode": "stale_official_cache",
            "status": "partial",
            "retrieved_at": "2026-07-18T12:00:00+00:00",
            "data_as_of": "2026-06-30",
            "stale": True,
        }},
    }})
    row = next(row for row in coverage["lanes"]
               if row["source"] == "reginfo_unified_agenda")
    assert row["status"] == PARTIAL
    assert row["stale"] is True
    assert row["detail"] == (
        "Official RegInfo Unified Agenda XML snapshot as of 2026-07-18; "
        "no live API return"
    )
    assert coverage["counts"]["partial"] == 1
    assert coverage["counts"]["stale"] == 1


def test_nested_program_truncation_is_partial_but_declared_selection_cap_is_not():
    program = {"records": [], "_provenance": {
        "source": "foreign_assistance",
        "mode": "live_official_source",
        "truncated": True,
        "retrieved_at": "2026-07-20T12:00:00+00:00",
    }}
    envelope = provenance_from_payload(program, source="foreign_assistance")
    assert envelope is not None
    assert envelope.status == "partial"
    assert attempt_row("foreign_assistance", program, None, 1.0, None)[
        "partial"
    ] is True

    bounded_selection = {
        "items": [1],
        "total_matched": 2,
        "truncated": True,
        "boundary_complete": True,
        "selection_cap": 1,
    }
    selection_attempt = attempt_row(
        "dod_contracts", bounded_selection, None, 1.0, None)
    assert selection_attempt.get("partial") is not True
    assert selection_attempt["coverage_contract"][
        "complete_within_boundary"
    ] is True


def test_contract_awards_child_attempt_names_attest_declared_single_boundary():
    payload = {
        "recompetes": [],
        "complete": True,
        "partial": False,
        "_provenance": {
            "schema_version": 1,
            "source": "contract_awards",
            "status": "complete",
            "mode": "usaspending_async_download",
            "retrieval_mode": "live",
            "attempts": [
                {
                    "source": "USAspending award download job",
                    "status": "success",
                    "count": 0,
                }
            ],
        },
    }

    attempt = attempt_row("contract_awards", payload, None, 1.0, None)

    assert attempt["ok"] is True
    assert attempt.get("partial") is not True
    assert attempt["coverage_contract"] == {
        "required_origins": ["contract_awards_boundary"],
        "returned_origins": ["contract_awards_boundary"],
        "complete_within_boundary": True,
    }


def test_partial_daily_program_cache_keeps_source_name_and_partial_status():
    coverage = coverage_from_sweep({"results": {
        "_attempts": [{
            "source": "dod_budget_exhibits", "ok": True, "partial": True,
        }],
        "dod_budget_exhibits": {"records": [], "_provenance": {
            "source": "dod_budget_exhibits",
            "mode": "official_daily_cache",
            "status": "partial",
            "retrieved_at": "2026-07-20T12:00:00+00:00",
            "data_as_of": "2026-04",
            "partial": True,
        }},
    }})

    row = next(row for row in coverage["lanes"]
               if row["source"] == "dod_budget_exhibits")
    assert row["status"] == PARTIAL
    assert row["current_snapshot"] is True
    assert row["detail"] == (
        "Current-day official DoD budget exhibits snapshot as of 2026-07-20; "
        "no live request this refresh"
    )
    assert "SBIR" not in row["detail"]
    assert coverage["counts"]["partial"] == 1
    assert coverage["counts"]["current_snapshot"] == 1


def test_program_bank_entry_preserves_record_identity_and_dates():
    entry = program_entry(
        "Official program fact",
        "https://www.reginfo.gov/public/do/eAgendaViewRule?RIN=1234-AA00",
        "regulatory",
        source_record_id="reginfo:1234-AA00",
        retrieved_at="2026-07-18T12:00:00+00:00",
        data_as_of="2026-06-30",
    )
    assert entry is not None
    assert entry.bank_row() == {
        "text": "Official program fact",
        "source": (
            "https://www.reginfo.gov/public/do/eAgendaViewRule?"
            "RIN=1234-AA00"
        ),
        "kind": "regulatory",
        "tier": "program",
        "scope": {"kind": "government_wide"},
        "source_record_id": "reginfo:1234-AA00",
        "retrieved_at": "2026-07-18T12:00:00+00:00",
        "data_as_of": "2026-06-30",
    }


def test_acquisition_forecast_keeps_own_freshness_and_never_claims_dhs():
    record = {
        "source": "acquisition_gateway",
        "source_id": "AG-100",
        "agency": "Department of Transportation",
        "component": "Federal Highway Administration",
        "title": "Network telemetry modernization",
        "anticipated_solicitation": "2026-10-01",
        "estimated_value_range": "$5M-$10M",
        "url": "https://acquisitiongateway.gov/forecast/resources/100?nid=100",
        "retrieved_at": "2026-07-18T10:00:00+00:00",
        "data_as_of": "2026-07-17",
    }
    results = {"forecast_signals": {
        "total_records": 7630,
        "screen_line": "Agency acquisition forecast screen: 7630 records",
        "sources": [{
            "source": "acquisition_gateway",
            "label": "GSA Acquisition Gateway forecast",
            "url": "https://acquisitiongateway.gov/forecast",
            "record_count": 7630,
            "retrieved_at": "2026-07-18T09:00:00+00:00",
            "data_as_of": "2026-07-17",
        }],
        "matched": [{**record, "reasons": ["keywords:network telemetry"]}],
    }}
    bank = build_fact_bank(
        results, retrieved_at="2026-07-20T12:00:00+00:00")
    line = next(row for row in bank if row["kind"] == "forecast_line")
    assert line["text"].startswith("GSA Acquisition Gateway forecast line")
    assert "DHS forecast line" not in line["text"]
    assert line["source_record_id"] == "AG-100"
    assert line["retrieved_at"] == "2026-07-18T10:00:00+00:00"
    assert line["data_as_of"] == "2026-07-17"
    screen = next(row for row in bank if row["kind"] == "forecast_screen")
    assert screen["source"] == "https://acquisitiongateway.gov/forecast"
    assert screen["retrieved_at"] == "2026-07-18T09:00:00+00:00"
