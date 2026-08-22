"""Pursuit grade rubric: reproducibility, N/A redistribution, precedence,
letter bands. Pure function — no I/O, no LLM."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.pursuit_grade import WEIGHTS, grade  # noqa: E402

AS_OF = date(2026, 7, 6)

FULL = {
    "triage_verdict": "pursue",
    "qualify_fit": "strong_fit",
    "dossier_fit": "strong_fit",
    "response_deadline": "2026-07-30",           # 24d -> timing 4
    "vehicle": "SEWP VI BPA order",
    "vehicle_catalog": [{"name": "SEWP VI", "verified": True,
                         "software_vendor_path": "resell via NASA SEWP holders"}],
    "recompete": {"awardee": "Incumbent Corp", "completion": "2026-12-01"},
    "naics_median_award": 792_700.0,
}


def test_same_inputs_same_grade_always():
    a, b = grade(FULL, as_of=AS_OF), grade(FULL, as_of=AS_OF)
    assert a.model_dump() == b.model_dump()      # reproducible, no vibes
    assert a.scored_dimensions == 5
    # fit 4*.35 + timing 4*.25 + vehicle 4*.20 + incumbency 1*.10 + dollar 4*.10
    assert a.score == 3.7
    assert a.letter == "A"


def test_worksheet_shows_the_math():
    g = grade(FULL, as_of=AS_OF)
    by = {d.dimension: d for d in g.dimensions}
    assert "precedence: dossier > qualify > triage" in by["fit"].basis
    assert "24d to due" in by["timing"].basis
    assert "SEWP VI" in by["vehicle"].basis
    assert "MARKET PROXY" in by["dollar"].basis
    assert abs(sum(d.effective_weight for d in g.dimensions) - 1.0) < 1e-6


def test_na_dimensions_redistribute_weight_and_say_why():
    thin = {"triage_verdict": "pursue", "response_deadline": "2026-07-30",
            "vehicle_catalog": []}
    g = grade(thin, as_of=AS_OF)
    by = {d.dimension: d for d in g.dimensions}
    assert g.scored_dimensions == 3               # fit, timing, vehicle
    assert by["incumbency"].score is None
    assert "SAM quota" in by["incumbency"].na_reason
    assert by["dollar"].na_reason
    assert by["incumbency"].effective_weight == 0.0
    live = WEIGHTS["fit"] + WEIGHTS["timing"] + WEIGHTS["vehicle"]
    assert abs(by["fit"].effective_weight - WEIGHTS["fit"] / live) < 1e-4
    # fit 4, timing 4, vehicle 3 (full-and-open) reweighted
    expected = (4 * WEIGHTS["fit"] + 4 * WEIGHTS["timing"] + 3 * WEIGHTS["vehicle"]) / live
    assert abs(g.score - round(expected, 3)) < 1e-9


def test_fit_precedence_dossier_over_triage():
    # triage says pursue (4) but the dossier's deeper read says stretch (2)
    g = grade({"triage_verdict": "pursue", "dossier_fit": "stretch",
               "response_deadline": "2026-07-30"}, as_of=AS_OF)
    fit = next(d for d in g.dimensions if d.dimension == "fit")
    assert fit.score == 2.0 and "dossier" in fit.basis


def test_timing_bands_and_expired():
    def t(deadline):
        g = grade({"triage_verdict": "pursue", "response_deadline": deadline}, as_of=AS_OF)
        return next(d for d in g.dimensions if d.dimension == "timing").score
    assert t("2026-07-01") == 0.0    # expired
    assert t("2026-07-10") == 2.0    # 4d, immediate-decision territory
    assert t("2026-08-10") == 4.0    # 35d sweet spot
    assert t("2026-10-10") == 3.0    # 96d runway
    assert t("2027-06-01") == 2.0    # distant
    assert next(d for d in grade({"triage_verdict": "pursue"}, as_of=AS_OF).dimensions
                if d.dimension == "timing").na_reason == "undated notice"


def test_letter_bands():
    # discard-verdict, expired, unknown vehicle in catalog -> hard fail
    g = grade({"triage_verdict": "discard", "response_deadline": "2026-01-01",
               "vehicle": "Mystery IDIQ",
               "vehicle_catalog": [{"name": "SEWP VI", "verified": True,
                                    "software_vendor_path": "x"}]}, as_of=AS_OF)
    assert g.letter == "F"
    # monitor + sweet-spot timing + open competition -> mid band
    g2 = grade({"triage_verdict": "monitor", "response_deadline": "2026-08-10"},
               as_of=AS_OF)
    assert g2.letter in ("B", "C")
