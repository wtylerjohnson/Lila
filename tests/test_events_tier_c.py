"""Tier C conferences reach the press, honestly.

AUDIT FINDING 2026-07-30 (the events C+): six live-verified conferences were
fetched and stored on 2026-07-28 and NOTHING ever read them - the AUSA/AFCEA
circuit a federal sales VP plans quarters around sat on disk while every
report shipped without it. And the roster's location regex accepted month
names as states: "Logos Give to AFA Air, Space" was stored as a LOCATION,
ready to render into a client report.

Pinned here: the harvest returns CANDIDATES (live verification per press is
the lane's law; stored quarterly verification is a schedule, not proof); the
engagement scope gates conferences exactly like industry days, because the
term path could ship an Army conference into a civilian engagement on the
word "network"; and the location extractor prefers absence over garbage.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.events_conferences import (  # noqa: E402
    harvest_tier_c, location_from_span, match_to_pack,
)
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402

TODAY = date(2026, 7, 30)


def _pack():
    """A civilian pack: VA is a cited corridor, vocabulary is network-shaped
    (the exact shape that used to leak DoD conferences through the term
    path)."""
    return EvidencePack(
        client_name="Riverbed", generated_at="2026-07-30T09:00:00",
        records=[GoldenRecord(
            record_id="VA118", lane="L2_entity_award",
            agency="Department of Veterans Affairs",
            sub_agency="Department of Veterans Affairs",
            # truth purge 2026-08-03: a corridor is the CLIENT's own paper;
            # recipient identity is the fixture's corridor proof
            recipient="Riverbed", obligated_dollars=15_000_000.0,
            period_end="2027-03-31", naics="541512",
            url="https://www.usaspending.gov/award/VA118")],
        research={"screen_terms": ["network monitoring", "health it"],
                  "inferred_naics": ["541512"], "entities": {}})


def _roster():
    return {"conferences": {
        "hhs_himss": {
            "name": "HIMSS Global Health Conference", "org": "HIMSS",
            "url": "https://www.himss.org/global-conference",
            "event_start": "2027-04-05", "event_end": "2027-04-08",
            "date_span": "March 5 - April 8, 2027 Chicago, IL",
            "location": "Chicago, IL", "http_status": 200,
            "agencies": ["Department of Health and Human Services",
                         "Department of Veterans Affairs"],
            "topics": ["health it", "ehr"],
            "verified_at": "2026-07-27T00:00:00Z"},
        "afcea_augusta": {
            "name": "TechNet Augusta", "org": "AFCEA",
            "url": "https://events.afcea.org/augusta26",
            "event_start": "2026-08-17", "event_end": "2026-08-20",
            "date_span": "August 17-20, 2026",
            "location": None, "http_status": 200,
            "agencies": ["Department of the Army", "Department of Defense"],
            "topics": ["signal", "cyber", "network"],
            "verified_at": "2026-07-27T00:00:00Z"},
        "ended_expo": {
            "name": "Last Year Expo", "org": "Org",
            "url": "https://x.org/expo",
            "event_start": "2026-01-05", "event_end": "2026-01-07",
            "date_span": "January 5-7, 2026", "location": None,
            "http_status": 200,
            "agencies": ["Department of Veterans Affairs"],
            "topics": ["health it"], "verified_at": "2026-07-27T00:00:00Z"},
    }}


class _CivilianScope:
    resolved_basis = "preset:civilian@v1"


@pytest.fixture()
def civilian(monkeypatch):
    def _in_scope(record, scope):
        agency = str(record.get("agency") or "").casefold()
        return ("defense" not in agency and "army" not in agency), "stub"

    monkeypatch.setattr("tools.relevance.scope.in_scope", _in_scope)
    return _CivilianScope()


# --------------------------------------------------------------------------- #
# the harvest
# --------------------------------------------------------------------------- #
def test_a_corridor_conference_is_kept_as_a_candidate(civilian):
    records, stats = harvest_tier_c(
        _pack(), roster=_roster(), today=TODAY, scope=civilian)
    assert [r.event_id for r in records] == ["conf:hhs_himss"]
    assert stats["kept"] == 1
    row = records[0]
    assert "Department of Veterans Affairs" in row.relevance
    assert "VA118" in row.relevance          # the corridor cites its record
    assert row.event_start == "2027-04-05"   # structured, provenance-safe


def test_the_harvest_never_claims_live_verification(civilian):
    """The roster's stored check is quarterly; the lane's law is live
    verification per press. Candidates leave unverified and the press runs
    them through the same verify_urls as tiers A and B."""
    records, _ = harvest_tier_c(
        _pack(), roster=_roster(), today=TODAY, scope=civilian)
    assert records[0].url_verified is False
    assert records[0].url_status is None


def test_the_term_path_cannot_leak_a_dod_conference_into_civilian(civilian):
    """THE LEAK: TechNet Augusta's topics say "network", the civilian pack's
    vocabulary says "network monitoring", and without the scope gate the
    Army conference ships into a civilian engagement on one word."""
    records, stats = harvest_tier_c(
        _pack(), roster=_roster(), today=TODAY, scope=civilian)
    assert not any("afcea" in r.event_id for r in records)
    assert stats["out_of_scope"] == 1


def test_unscoped_engagements_still_get_the_term_path(civilian):
    records, _ = harvest_tier_c(
        _pack(), roster=_roster(), today=TODAY, scope=None)
    assert any("afcea" in r.event_id for r in records)


def test_an_ended_conference_never_ships(civilian):
    records, stats = harvest_tier_c(
        _pack(), roster=_roster(), today=TODAY, scope=civilian)
    assert not any("ended" in r.event_id for r in records)
    assert stats["already_ended"] == 1


def test_an_agencyless_conference_is_government_wide(civilian):
    """RSA-style rows name no agencies; they pass scope and stand on the
    term path alone."""
    roster = {"conferences": {"rsac": {
        "name": "RSA Conference", "org": "RSAC",
        "url": "https://www.rsaconference.com",
        "event_start": "2027-05-16", "event_end": "2027-05-19",
        "date_span": "May 16-19, 2027 San Francisco, CA",
        "location": "San Francisco, CA", "http_status": 200,
        "agencies": [], "topics": ["network monitoring", "security"],
        "verified_at": "2026-07-27T00:00:00Z"}}}
    records, _ = harvest_tier_c(
        _pack(), roster=roster, today=TODAY, scope=civilian)
    assert [r.event_id for r in records] == ["conf:rsac"]


# --------------------------------------------------------------------------- #
# the location extractor
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("span,expected", [
    ("March 9-12, 2027 Chicago, IL registration open", "Chicago, IL"),
    ("held at National Harbor, MD this October", "National Harbor, MD"),
    ("Logos Give to AFA Air, Space", None),             # the live garbage
    ("Join us at WEST, February 16-18", None),
    ("With assistance from the U.S. Army Cyber Center of Excellence, "
     "August", None),
    ("", None),
    (None, None),
])
def test_locations_prefer_absence_over_garbage(span, expected):
    assert location_from_span(span) == expected


# --------------------------------------------------------------------------- #
# relevance still binds to pack facts
# --------------------------------------------------------------------------- #
def test_an_unconnected_conference_does_not_match():
    from agents.golden_press.events import pack_frame

    relevance, basis = match_to_pack(
        {"name": "Farm Expo", "org": "AgOrg",
         "agencies": ["Department of Agriculture"], "topics": ["tractors"]},
        pack_frame(_pack()))
    assert relevance is None and basis == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------- #
# the validator accepts an escaped query-string anchor
# --------------------------------------------------------------------------- #
def test_a_query_string_event_url_validates_when_anchored():
    """REGRESSION 2026-07-30: check_events compared the RAW url against
    attribute-escaped HTML, so the first event URL carrying '&' (AFCEA WEST)
    failed certification with the anchor present and correct. sam.gov
    canonical URLs carry no ampersands, which is why tiers A and B never
    tripped it."""
    import html as html_mod

    from agents.golden_press.validate import check_events

    url = "https://www.westconference.org/W27/C.aspx?ID=123711&sortMenu=101002"
    pack = _pack()
    pack.events = [{
        "event_id": "conf:afcea_west", "name": "AFCEA WEST",
        "event_type": "industry conference", "source_system":
        "curated_conference", "url": url, "url_verified": True,
        "url_status": 200, "host": "AFCEA / USNI",
        "event_start": "2027-02-16", "event_end": "2027-02-18",
        "relevance": "corridor", "relevance_basis": {}}]
    content = (
        '<section class="band" id="events">'
        f'<a href="{html_mod.escape(url, quote=True)}">AFCEA WEST</a>'
        '</section>'
        '<section class="band" id="evidence"></section>')
    violations = [v.rule for v in check_events(content, pack)]
    assert "event_not_linked" not in violations
