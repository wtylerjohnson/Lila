"""Regression coverage for Cycle 5 review item 19."""

from __future__ import annotations

import copy
from datetime import date

from agents.reports.document import build_document
from agents.reports.lint import lint_counts, lint_screen_census
from agents.reports.views import render_assessment
from tests.test_assessment_document import _searches
from tests.test_live_report_truth import (
    NOW,
    _cutover_sweep,
    _persist,
    _qualify,
    _seed_runtime,
)


def test_pointerless_zero_triage_keeps_legacy_landscape_copy(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(tmp_path / "assess_runs"))
    monkeypatch.setenv("LILA_REVIEW_DIR", str(tmp_path / "review"))
    searches = _searches(0)
    searches["search_scope"] = {"all": True}
    searches["results"]["triage"] = {}
    searches["results"]["sam_census"] = {
        "matched": 1,
        "active_screened": 4_127,
        "retrieved": 1,
        "complete": True,
        "source": "sam_extract",
    }

    doc = build_document(
        "Testco", searches=searches, qualify=None,
        as_of=date(2026, 7, 7), _cap_profile=None,
    )
    assert not list((tmp_path / "assess_runs").rglob("*.current.json"))
    assert doc.verdict_totals == {}

    for view in ("client", "sales", "internal"):
        html = render_assessment(doc, view)
        landscape = ("No notices were screened in this sweep; the market pull "
                     "returned none to triage. This is a data gap, not an "
                     "all-clear.")
        # opportunity-set doctrine: the audit landscape rides internal/sales;
        # the client view carries the opportunity set alone.
        if view == "client":
            assert landscape not in html
        else:
            assert landscape in html
        assert "have triage decisions available for the opportunity board" \
            not in html
        assert 'data-screen-census="1" data-screened="0"' not in html
        assert lint_counts(html).ok
        assert lint_screen_census(html).ok


def test_current_incomplete_ledger_keeps_held_census_reconciliation(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    rejected = copy.deepcopy(searches["results"]["sam.gov"][1])
    rejected.update({
        "source": "web",
        "source_id": "N5",
        "title": "Non-SAM row mixed into the collection",
        "api_url": "https://sam.gov/opp/N5/view",
    })
    rejected["raw_payload"] = dict(
        rejected["raw_payload"], notice_id="N5")
    searches["results"]["sam.gov"].append(rejected)
    searches["results"]["triage"]["N5"] = {
        "verdict": "pursue",
        "reason": "legacy promotion must not survive",
    }
    searches["results"]["sam_census"].update(
        matched=5, active_screened=5, complete=True)
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)

    doc = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=NOW.date(),
    )

    for view in ("client", "sales", "internal"):
        html = render_assessment(doc, view)
        # opportunity-set doctrine: the held-census reconciliation is internal/
        # sales audit; the client view stays a clean opportunity set.
        if view == "client":
            assert "live-source evidence gate is held closed" not in html
            assert 'data-held-closed="1"' not in html
        else:
            assert "live-source evidence gate is held closed" in html
            assert "4 of 5 census records" in html
            assert 'data-held-closed="1"' in html
        assert "pull returned none" not in html
        assert lint_counts(html).ok
        assert lint_screen_census(html).ok
