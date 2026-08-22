"""Lifecycle stage derivation: one owner, every stage pinned (2026-07-17).

Doctrine: stages are derived from artifacts on disk, never hand-set; a
valid-profile-no-sweep client is Onboarding WITH the press-sweep
affordance, not an error; Refresh Due applies only from Shipped, when the
oldest figure sits outside (or cannot be proven inside) the freshness
window. Hermetic: every module that resolves paths points at one tmp root.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui.lifecycle as lc  # noqa: E402

TODAY = date(2026, 7, 17)


@pytest.fixture()
def book(tmp_path, monkeypatch):
    import agents.review as review
    import tools.capability as cap
    monkeypatch.setattr(lc, "_ROOT", str(tmp_path))
    monkeypatch.setattr(cap, "ROOT", str(tmp_path))
    monkeypatch.setattr(cap, "CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "data" / "review"))
    for d in ("clients", "data/review", "data/cleaned", "data/reports",
              "data/state/relevance"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _mk_profile(root, slug="testco", populated=True):
    d = root / "clients" / slug
    d.mkdir(parents=True, exist_ok=True)
    profile = {
        "client_name": "Testco",
        "capability_terms": {"core": ["video wall"] if populated else [],
                             "adjacent": [], "excluded": []},
        "naics_boundary": ["334310"] if populated else [],
    }
    (d / "profile.json").write_text(json.dumps(profile), encoding="utf-8")


def _mk_sweep(root, slug="testco"):
    (root / "data" / "cleaned" / f"searches_{slug}.json").write_text(
        json.dumps({"client": "Testco", "results": {"sam.gov": []}}),
        encoding="utf-8")


def _mk_calibration(root, client="Testco"):
    (root / "data" / "state" / "relevance" / "calibration.csv").write_text(
        "client,record_ref,engine_score\n"
        f"{client},X1,6\n", encoding="utf-8")


def _mk_content(root, slug="testco", retrieved=None, unknown=False):
    figures = []
    if not unknown:
        retrieved = retrieved or datetime(2026, 7, 16, tzinfo=timezone.utc)
        figures = [{"text": "$1.00M", "raw": 1000000.0,
                    "retrieved_at": retrieved.isoformat()}]
    else:
        figures = [{"text": "$1.00M", "raw": 1000000.0}]
    (root / "clients" / slug / "signal_board_content.json").write_text(
        json.dumps({"client_name": "Testco", "scale_total": "$1.00M",
                    "figures": figures}), encoding="utf-8")


def _mk_press(root, slug="testco", gated=False, order=0):
    name = (f"{slug}.federal_opportunity_signals"
            f"{'.DO-NOT-SEND' if gated else ''}.html")
    path = root / "data" / "reports" / name
    path.write_text("<html></html>", encoding="utf-8")
    when = 1_800_000_000 + order
    os.utime(path, (when, when))


def test_missing_profile_is_onboarding_without_affordance(book):
    (book / "clients" / "testco").mkdir()
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "onboarding"
    assert card["sweep_ready"] is False
    assert "No profile, no sweep" in card["gate_error"]


def test_valid_profile_no_sweep_is_onboarding_with_press_affordance(book):
    _mk_profile(book)
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "onboarding"
    assert card["sweep_ready"] is True          # the press-sweep affordance
    assert card["gate_error"] is None


def test_swept(book):
    _mk_profile(book)
    _mk_sweep(book)
    assert lc.derive_stage("testco", today=TODAY)["stage"] == "swept"


def test_shortlisted_via_calibration_rows(book):
    _mk_profile(book)
    _mk_sweep(book)
    _mk_calibration(book)
    assert lc.derive_stage("testco", today=TODAY)["stage"] == "shortlisted"


def test_composed(book):
    _mk_profile(book)
    _mk_sweep(book)
    _mk_content(book)
    assert lc.derive_stage("testco", today=TODAY)["stage"] == "composed"


def test_gated_when_do_not_send_is_newest(book):
    _mk_profile(book)
    _mk_sweep(book)
    _mk_content(book)
    _mk_press(book, gated=False, order=0)
    _mk_press(book, gated=True, order=10)      # newer DO-NOT-SEND
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "gated"
    assert card["headline_stat"] is None       # nothing shipped to headline


def test_shipped_with_headline_and_green_light(book):
    _mk_profile(book)
    _mk_sweep(book)
    _mk_content(book, retrieved=datetime(2026, 7, 16, tzinfo=timezone.utc))
    _mk_press(book, gated=True, order=0)
    _mk_press(book, gated=False, order=10)     # newer clean press
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "shipped"
    assert card["headline_stat"] == "$1.00M"
    assert card["freshness"]["light"] == "green"
    assert card["last_press"] is not None


def test_amber_within_three_days_of_expiry(book):
    from agents.reports.verification import FRESHNESS_MAX_AGE_DAYS
    _mk_profile(book)
    _mk_sweep(book)
    edge = datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc) \
        - timedelta(days=FRESHNESS_MAX_AGE_DAYS - 2)
    _mk_content(book, retrieved=edge)
    _mk_press(book, gated=False)
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "shipped"
    assert card["freshness"] == {"light": "amber", "basis": "expiring",
                                 "days_left": 2}


def test_refresh_due_when_outside_window(book):
    from agents.reports.verification import FRESHNESS_MAX_AGE_DAYS
    _mk_profile(book)
    _mk_sweep(book)
    stale = datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc) \
        - timedelta(days=FRESHNESS_MAX_AGE_DAYS + 5)
    _mk_content(book, retrieved=stale)
    _mk_press(book, gated=False)
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "refresh_due"
    assert card["freshness"]["light"] == "red"
    assert card["freshness"]["basis"] == "stale"


def test_unknown_freshness_on_shipped_is_refresh_due_red(book):
    _mk_profile(book)
    _mk_sweep(book)
    _mk_content(book, unknown=True)
    _mk_press(book, gated=False)
    card = lc.derive_stage("testco", today=TODAY)
    assert card["stage"] == "refresh_due"
    assert card["freshness"] == {"light": "red", "basis": "unknown",
                                 "days_left": None}


def test_radar_aggregates_the_book(book):
    _mk_profile(book, "alpha")
    _mk_profile(book, "beta")
    _mk_sweep(book, "beta")
    # beta's sweep resolves via client name Testco -> slug testco; give it
    # its own artifact name to stay distinct
    (book / "data" / "cleaned" / "searches_testco.json").write_text(
        json.dumps({"client": "Testco", "results": {}}), encoding="utf-8")
    out = lc.radar(today=TODAY)
    assert out["scoreboard"]["clients"] == 2
    assert sum(out["scoreboard"]["stages"].values()) == 2
    assert set(out["scoreboard"]["freshness"]) == {"green", "amber", "red"}
