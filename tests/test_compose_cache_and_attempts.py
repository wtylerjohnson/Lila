"""P2 foundation (sectioned compose cache) + P3 (attempt manifests).

Offline; cache dir and ledger isolated via env (conftest pattern).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports import compose_cache as cc  # noqa: E402


def _validator(payload):
    if not isinstance(payload, dict) or "thesis" not in payload:
        raise ValueError("wrong shape")
    return payload


def test_cached_call_pays_once_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path))
    calls = []

    def producer():
        calls.append(1)
        return {"thesis": ["a", "b", "c"]}

    digest = cc.canonical_digest({"facts": ["F1"], "directive": None})
    key = cc.section_key(client="Acme Federal", scope="all",
                         section="narrative", inputs_digest=digest)
    first, hit1 = cc.cached_call(key=key, validator=_validator,
                                 producer=producer, meta={"n_facts": 1})
    second, hit2 = cc.cached_call(key=key, validator=_validator,
                                  producer=producer)
    assert (hit1, hit2) == (False, True)
    assert first == second and len(calls) == 1
    # a different inputs digest is a different key: producer pays again
    key2 = cc.section_key(client="Acme Federal", scope="all",
                          section="narrative",
                          inputs_digest=cc.canonical_digest({"facts": ["F2"]}))
    cc.cached_call(key=key2, validator=_validator, producer=producer)
    assert len(calls) == 2


def test_corrupt_or_invalid_entries_are_misses_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path))
    key = cc.section_key(client="Acme", scope="all", section="news",
                         inputs_digest="a" * 24)
    # corrupt file on disk
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(tmp_path / f"{key}.json", "w") as f:
        f.write("{not json")
    assert cc.load_section(key, _validator) is None
    # schema-invalid content is a miss too
    cc.save_section(key, {"wrong": "shape"})
    assert cc.load_section(key, _validator) is None
    # producer exception leaves no entry behind
    key3 = cc.section_key(client="Acme", scope="all", section="boards",
                          inputs_digest="b" * 24)
    with pytest.raises(RuntimeError):
        cc.cached_call(key=key3, validator=_validator,
                       producer=lambda: (_ for _ in ()).throw(RuntimeError("llm down")))
    assert cc.load_section(key3, _validator) is None
    assert cc.invalidate(key) is True and cc.invalidate(key) is False


def test_scope_and_section_isolate_cache_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path))
    d = cc.canonical_digest({"same": "inputs"})
    keys = {cc.section_key(client="Acme", scope=s, section=sec, inputs_digest=d)
            for s in ("all", "agency_dhs") for sec in ("narrative", "news")}
    assert len(keys) == 4  # scoped runs can never serve another scope's prose


# ---- P3: coverage ledger v2 + sweep attempt manifest -------------------------

def test_record_pull_v2_attempt_fields_and_v1_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_FORECAST_COVERAGE",
                       str(tmp_path / "coverage.json"))
    from tools.api.forecasts.coverage import coverage_table, record_pull
    # v2: full attempt manifest
    record_pull("sweep:sam.gov", "sweep", 619, ok=True,
                note="census complete", retrieved=619, total_known=78446,
                complete=True, elapsed_s=41.234)
    # v1 caller shape untouched
    record_pull("dhs_apfs", "DHS", 736, ok=True)
    led = json.load(open(tmp_path / "coverage.json"))
    pull = led["sweep:sam.gov"]["last_pull"]
    assert pull["retrieved"] == 619 and pull["total_known"] == 78446
    assert pull["complete"] is True and pull["elapsed_s"] == 41.2
    assert "last_pull" in led["dhs_apfs"] and led["dhs_apfs"]["records"] == 736
    # failed attempt keeps prior records but logs the error
    record_pull("sweep:web", "sweep", None, ok=False,
                error="timeout after 240s", elapsed_s=240.0)
    led = json.load(open(tmp_path / "coverage.json"))
    assert led["sweep:web"]["last_pull"]["error"].startswith("timeout")
    assert led["sweep:web"]["status"] == "error"
    assert {r["source"] for r in coverage_table()} >= {"sweep:sam.gov", "dhs_apfs"}


def test_attempt_row_counts_and_errors():
    from run_searches import (
        _sam_census_receipt,
        _usaspending_evidence_receipt,
        attempt_row,
    )
    ok_list = attempt_row("sam.gov", [1, 2, 3], "3 notices", 41.25, None)
    assert ok_list == {"source": "sam.gov", "ok": True, "elapsed_s": 41.2,
                       "count": 3, "summary": "3 notices"}
    ok_dict = attempt_row("buyer_map", {"buyers": [1] * 93}, "93 buyers", 12.0, None)
    assert ok_dict["count"] == 93 and ok_dict["ok"] is True
    assert ok_dict["coverage_contract"] == {
        "required_origins": ["incumbent_buyer_map_query"],
        "returned_origins": ["incumbent_buyer_map_query"],
        "complete_within_boundary": True,
    }
    failed = attempt_row("web", None, None, 240.0, TimeoutError("240s"))
    assert failed["ok"] is False and "240s" in failed["error"]
    assert "count" not in failed

    soft_failed = attempt_row(
        "dod_contracts", {"items": [], "error": "feed down"},
        "0 announcements", 1.0, None)
    assert soft_failed["ok"] is False
    assert soft_failed["error"] == "feed down"

    partial = attempt_row(
        "news", {"items": [{"title": "returned"}],
                 "errors": {"one-feed": "timeout"}},
        "1 item", 2.0, None)
    assert partial["ok"] is True
    assert partial["partial"] is True
    assert "error" not in partial
    assert partial["coverage_contract"]["complete_within_boundary"] is False

    census_partial = attempt_row(
        "sam.gov",
        [{"source_id": "N-1"}],
        "1 notice via incomplete live fallback",
        1.0,
        None,
        partial_override=True,
    )
    assert census_partial["ok"] is True
    assert census_partial["count"] == 1
    assert census_partial["partial"] is True

    assert _sam_census_receipt({
        "source": "sam.gov live API",
        "complete": False,
        "matched": 1,
        "active_screened": 7,
        "retrieved": 5,
        "attempt_manifest": {
            "requested": [{"id": "one"}, {"id": "two"}],
            "executed": [{"id": "one"}],
            "omitted": [{"id": "two"}],
        },
    }) == {
        "complete": False,
        "source": "sam.gov live API",
        "stale_cache": 0,
        "requested_passes": 2,
        "executed_passes": 1,
        "omitted_passes": 1,
        "matched": 1,
        "active_screened": 7,
        "retrieved": 5,
    }

    assert _sam_census_receipt({
        "source": "sam.gov live API",
        "complete": True,
        "stale_cache": 2,
    })["stale_cache"] == 2

    usa_ok = _usaspending_evidence_receipt([{
        "agency_breakdown": [{"agency": "Defense", "total_obligated": 1}],
        "addressable": {"total_obligated": 1},
    }])
    assert usa_ok == {
        "complete": True,
        "lanes_returned": 1,
        "agency_breakdown_failures": 0,
        "addressable_failures": 0,
    }
    usa_partial = _usaspending_evidence_receipt([{
        "agency_breakdown": [{"error": "aggregate down"}],
        "addressable": {"error": "keyword slice down"},
    }])
    assert usa_partial == {
        "complete": False,
        "lanes_returned": 1,
        "agency_breakdown_failures": 1,
        "addressable_failures": 1,
    }

    for payload in (
        {"items": [1], "truncated": True},
        {"buyers": [1], "skipped_terms": ["cloudvision"]},
        {"records": [1], "omitted_naics_lanes": 2},
        {"records": [1], "failed_naics_lanes": ["541512"]},
        {"records": [1], "complete": False},
    ):
        bounded = attempt_row("bounded", payload, "bounded result", 0.1, None)
        assert bounded["ok"] is True
        assert bounded["partial"] is True
