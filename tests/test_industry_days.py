"""Industry days: a filter and a label, never a parsed event date."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.industry_days import (  # noqa: E402
    PROCUREMENT_MECHANICS, candidate_event_date, collect, is_industry_day,
)


def _row(**kw):
    base = {"notice_id": "n1", "title": "A notice", "notice_type": "Special Notice",
            "agency": "DEPT OF THE NAVY", "subtier": "NAVAIR", "office": "OFF",
            "deadline": "2026-12-01", "description_prefix": ""}
    base.update(kw)
    return base


# ---- what counts ----------------------------------------------------------- #
def test_an_event_in_the_title_counts():
    assert is_industry_day("Special Notice", "INDUSTRY DAY FOR UAS TRAINING")


def test_a_special_notice_naming_the_event_in_its_body_counts():
    """Agencies title these by program and say 'industry day' inside."""
    assert is_industry_day("Special Notice", "Next Generation Command and Control",
                           "An industry day will be held for interested vendors.")


def test_a_solicitation_describing_its_own_steps_does_not_count():
    """This is the procurement talking about itself, not an event."""
    assert not is_industry_day(
        "Solicitation", "Audio-Visual System Upgrade",
        "A site visit will be held prior to the response date.")


@pytest.mark.parametrize("phrase", PROCUREMENT_MECHANICS)
def test_procurement_mechanics_are_never_events(phrase):
    """site visit matched 145 notices and pre-proposal conference 63. Both are
    routine steps inside construction and AV solicitations, and including them
    returned Galley Grab-N-Go and Automatic Door Operators."""
    assert not is_industry_day("Solicitation", f"Some buy", f"A {phrase} is planned.")


# ---- the event date is never asserted -------------------------------------- #
def test_a_posting_date_is_never_offered_as_an_event_date():
    """EVERY date my first pass surfaced on live data was a posting or
    amendment date. Rendering one beside an industry day reads as 'the event
    is that day' and is wrong every time."""
    assert candidate_event_date(
        "AMENDMENT 01 (Posted July 21, 2026): the purpose is to clarify.") is None
    assert candidate_event_date(
        "This notification, posted 24 June 2026, is established to provide.") is None


def test_a_real_event_date_is_offered_with_its_sentence_and_marked_unverified():
    out = candidate_event_date(
        "Registration is open. The industry day will be held 12 March 2027 at "
        "Patuxent River. Vendors must register.")
    assert out["value"] == "12 March 2027"
    assert out["verified"] is False
    assert "will be held" in out["source_sentence"]
    assert out["caution"]


def test_no_date_shaped_text_yields_nothing():
    assert candidate_event_date("No dates here at all.") is None
    assert candidate_event_date("") is None
    assert candidate_event_date(None) is None


# ---- the band -------------------------------------------------------------- #
def test_past_events_are_dropped_and_counted():
    rows = [_row(notice_id="old", title="Industry Day A", deadline="2026-01-01"),
            _row(notice_id="new", title="Industry Day B", deadline="2026-12-01")]
    out = collect(rows, today="2026-07-29")
    assert [r["notice_id"] for r in out["rows"]] == ["new"]
    assert out["receipt"]["matched_event_phrase"] == 2
    assert out["receipt"]["still_future"] == 1


def test_the_same_event_posted_repeatedly_renders_once():
    """Live data had one Air Force notice posted ten times."""
    rows = [_row(notice_id=f"n{i}", title="Industry Day X") for i in range(6)]
    out = collect(rows, today="2026-07-29")
    assert len(out["rows"]) == 1
    assert out["receipt"]["distinct_after_dedupe"] == 1


def test_rows_sort_by_registration_deadline():
    rows = [_row(notice_id="b", title="Industry Day B", deadline="2026-12-01"),
            _row(notice_id="a", title="Industry Day A", deadline="2026-09-01")]
    out = collect(rows, today="2026-07-29")
    assert [r["notice_id"] for r in out["rows"]] == ["a", "b"]


def test_an_empty_band_still_states_what_it_screened():
    """Absence must read as coverage, not as a hole."""
    out = collect([_row(title="Unrelated buy", notice_type="Solicitation")],
                  today="2026-07-29")
    assert out["rows"] == []
    r = out["receipt"]
    assert r["screened"] == 1
    assert r["matched_event_phrase"] == 0
    assert r["event_phrases"] and r["excluded_as_procurement_mechanics"]
    assert "never parsed" in r["event_date_policy"]
