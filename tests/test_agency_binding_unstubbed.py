"""WS2 (review findings 2, 15): the REAL agency binding path, no stubs.

The reliability suite monkeypatches both current_assess_binding and
assess_approval_for_release, so the actual approve-then-promote comparison
for a scoped gate was never exercised. These tests run it for real: a scoped
gate, a filter-first scoped sweep on disk, a real approval, and the same
binding recomputation run_agency_report performs at promotion.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.approval import (  # noqa: E402
    approve_assess_results, assess_approval_for_release, current_assess_binding,
)


class _Profile:
    """Minimal populated capability profile for binding digests."""

    def is_populated(self) -> bool:
        return True

    def model_dump(self, mode: str = "json") -> dict:
        return {"client_name": "Acme Federal",
                "capability_terms": {"core": ["network monitoring"]},
                "naics_boundary": ["541512"]}


def _seed(tmp_path, monkeypatch):
    review = tmp_path / "review"
    cleaned = tmp_path / "cleaned"
    review.mkdir()
    cleaned.mkdir()
    (review / "acme_federal.review.json").write_text(json.dumps({
        "client_name": "Acme Federal", "status": "approved",
        "search_scope": {"agencies": ["DHS"], "mode": "focus"},
    }))
    scoped = {"client": "Acme Federal",
              "agency_focus": {"abbr": "DHS",
                               "name": "Department of Homeland Security"},
              "search_scope": {"agencies": [{"abbr": "DHS",
                                             "name": "Department of Homeland Security"}],
                               "mode": "focus"},
              "generated_at": "2026-07-11T06:00:00",
              "results": {"sam.gov": [{"source": "sam.gov", "source_id": "P1",
                                       "title": "Network visibility"}],
                          "triage": {"P1": {"verdict": "monitor",
                                            "reason": "fit"}}}}
    scoped_path = cleaned / "searches_acme_federal.agency_dhs.json"
    scoped_path.write_text(json.dumps(scoped, indent=2, default=str))
    monkeypatch.setattr("tools.capability.load_profile",
                        lambda name: _Profile())
    return str(review), str(scoped_path), scoped


def test_scoped_approval_matches_promotion_binding_unstubbed(tmp_path, monkeypatch):
    """Approve over the scoped sweep on disk, then recompute the binding the
    way run_agency_report's promotion does (explicit sweep_path + in-memory
    dict parsed from the same file): the REAL comparison must say approved."""
    review_dir, scoped_path, _ = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", note="unstubbed", review_dir=review_dir)

    promotion_binding = current_assess_binding(
        "Acme Federal", review_dir=review_dir,
        sweep_path=scoped_path,
        sweep=json.load(open(scoped_path, encoding="utf-8")))
    approval, status, problems = assess_approval_for_release(
        "Acme Federal", current_binding=promotion_binding,
        review_dir=review_dir)
    assert status == "approved", problems
    assert problems == []
    assert approval["binding"]["scope_designator"] == "agency_dhs"
    assert approval["binding"]["sweep_artifact"] == os.path.basename(scoped_path)


def test_changed_scoped_evidence_reopens_the_gate_unstubbed(tmp_path, monkeypatch):
    """Mutating the scoped sweep after approval must read as changed evidence."""
    review_dir, scoped_path, scoped = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", note="unstubbed", review_dir=review_dir)

    scoped["results"]["sam.gov"][0]["title"] = "Different notice"
    with open(scoped_path, "w", encoding="utf-8") as f:
        json.dump(scoped, f, indent=2, default=str)
    binding = current_assess_binding(
        "Acme Federal", review_dir=review_dir,
        sweep_path=scoped_path,
        sweep=json.load(open(scoped_path, encoding="utf-8")))
    _, status, problems = assess_approval_for_release(
        "Acme Federal", current_binding=binding, review_dir=review_dir)
    assert status == "invalid"
    assert any("sweep evidence" in p for p in problems)


def test_explicit_evidence_binding_survives_missing_scoped_sweep(tmp_path, monkeypatch):
    """Finding #2: with a scoped gate whose scoped sweep is ABSENT from disk,
    a caller supplying explicit sweep_path+sweep (run_agency_report's
    promotion) must still get a binding, not a FileNotFoundError downgraded
    to a spurious DO-NOT-SEND."""
    review_dir, scoped_path, scoped = _seed(tmp_path, monkeypatch)
    os.remove(scoped_path)

    binding = current_assess_binding(
        "Acme Federal", review_dir=review_dir,
        sweep_path=scoped_path, sweep=scoped)
    assert binding["scope_designator"] == "agency_dhs"
    assert binding["sweep_artifact"] == os.path.basename(scoped_path)

    # without explicit evidence the loud failure is still correct
    with pytest.raises(FileNotFoundError):
        current_assess_binding("Acme Federal", review_dir=review_dir)
