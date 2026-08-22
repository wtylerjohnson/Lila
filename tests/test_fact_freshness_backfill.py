"""Freshness backfill audit (2026-07-16): recovery counts over stored sweeps.

Doctrine: the audit is read-only. Sweep bytes are hash-bound into operator
approvals, so the migration recovers retrieval times at load instead of
rewriting artifacts; this tool only counts what recovers vs what is
UNKNOWN_FRESHNESS.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from test_assessment_document import _searches  # noqa: E402

from tools.fact_freshness_backfill import audit  # noqa: E402


def _write(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_audit_counts_backfilled_and_unknown(tmp_path):
    cleaned = tmp_path / "cleaned"
    review = tmp_path / "review"
    cleaned.mkdir()
    review.mkdir()

    dated = _searches(2)                      # carries generated_at
    undated = _searches(1)
    undated.pop("generated_at")
    undated["client"] = "Legacy Co"
    _write(str(cleaned / "searches_testco.json"), dated)
    _write(str(cleaned / "searches_legacy_co.json"), undated)
    _write(str(review / "legacy_co.horizon.json"), {
        "fact_bank": [
            {"id": "H1", "text": "x", "source": "https://apfs-cloud.dhs.gov/",
             "kind": "forecast_screen"},
            {"id": "H2", "text": "y", "source": "https://apfs-cloud.dhs.gov/",
             "kind": "forecast_screen",
             "retrieved_at": "2026-07-01T00:00:00+00:00"},
        ]})

    report = audit(cleaned_dir=str(cleaned), review_dir=str(review))

    dated_row = report["sweeps"]["searches_testco.json"]
    undated_row = report["sweeps"]["searches_legacy_co.json"]
    assert dated_row["facts"] > 0
    assert dated_row["backfilled"] == dated_row["facts"]
    assert dated_row["unknown_freshness"] == 0
    assert dated_row["oldest_retrieved_at"] is not None

    assert undated_row["facts"] > 0
    assert undated_row["backfilled"] == 0
    assert undated_row["unknown_freshness"] == undated_row["facts"]
    assert undated_row["sweep_generated_at"] is None

    horizon = report["horizon_banks"]["legacy_co.horizon.json"]
    assert horizon == {"rows": 2, "with_retrieved_at": 1}

    totals = report["totals"]
    assert totals["facts"] == dated_row["facts"] + undated_row["facts"]
    assert totals["unknown_freshness"] == undated_row["facts"]
    assert not report["errors"]


def test_audit_is_read_only(tmp_path):
    cleaned = tmp_path / "cleaned"
    review = tmp_path / "review"
    cleaned.mkdir()
    review.mkdir()
    sweep = cleaned / "searches_testco.json"
    _write(str(sweep), _searches(1))
    before = sweep.read_bytes()

    audit(cleaned_dir=str(cleaned), review_dir=str(review))

    assert sweep.read_bytes() == before
    assert sorted(os.listdir(cleaned)) == ["searches_testco.json"]
    assert os.listdir(review) == []
