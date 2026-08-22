"""Industry days reaching the events band: the seam, not the doctrine.

`tests/test_industry_days.py` pins what counts as an industry day. This file
pins the wiring that carries one into `pack.events`, which is where the
module sat unused from 2026-07-28 to 2026-07-30 producing exactly zero rows.

The three facts that cost the most to learn are pinned first, because each
one fails SILENTLY: a wrong-vocabulary source screens thousands of rows and
reports a confident zero, an exhausted generator screens none at all, and an
unrelevanced row ships a Navy dredging session into a software vendor's
report.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.events import EventRecord  # noqa: E402
from agents.golden_press.industry_days import (  # noqa: E402
    _clean, collect, drop_already_shipped, harvest_from_store, to_event_record,
)
from agents.golden_press.records import (  # noqa: E402
    EvidencePack, GoldenRecord,
)
from agents.golden_press.validate import _pack_date_keys  # noqa: E402
from agents.reports.links import build_sam_notice_link  # noqa: E402

GUID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
OTHER_GUID = "0f9e8d7c6b5a49382716f5e4d3c2b1a0"
FUTURE = "2026-12-01"


# --------------------------------------------------------------------------- #
# fixtures: a store row and a pack the row can legitimately connect to
# --------------------------------------------------------------------------- #
def _store_row(**kw) -> dict:
    """A row in the NOTICE-STORE column vocabulary, which is what `collect`
    reads. Deliberately NOT the extract's CSV headers: see the source test."""
    base = {
        "notice_id": GUID,
        "title": "Industry Day for Network Visibility Modernization",
        "notice_type": "Special Notice",
        "agency": "DEPARTMENT OF THE TREASURY",
        "subtier": "INTERNAL REVENUE SERVICE",
        "office": "IRS PROCUREMENT",
        "posted": "2026-07-20",
        "deadline": FUTURE,
        "naics": "541512",
        "description_prefix": "An industry day for interested vendors.",
        "last_seen": "2026-07-28",
    }
    base.update(kw)
    return base


def _pack(**kw) -> EvidencePack:
    """A pack whose records make the IRS a real buying corridor and
    whose research carries the capability vocabulary the screen reads."""
    record = GoldenRecord(
        record_id="GS35F001", lane="L2_entity_award",
        title="Network visibility monitoring support",
        description="Network visibility monitoring support",
        agency="DEPARTMENT OF THE TREASURY",
        sub_agency="INTERNAL REVENUE SERVICE",
        # truth purge 2026-08-03: a corridor is the CLIENT's own paper;
        # recipient identity is the fixture's corridor proof
        recipient="Riverbed", obligated_dollars=4_200_000.0,
        period_end="2027-03-31", naics="541512",
        url="https://www.usaspending.gov/award/CONT_AWD_GS35F001",
    )
    pack = EvidencePack(
        client_name="Riverbed", generated_at="2026-07-30T09:00:00",
        records=[record],
        research={"screen_terms": ["network visibility"],
                  "inferred_naics": ["541512"],
                  "entities": {"competitor": [], "product": []}},
    )
    for key, value in kw.items():
        setattr(pack, key, value)
    return pack


def _frame(pack):
    from agents.golden_press.events import pack_frame
    return pack_frame(pack)


def _map(row, pack=None, today=date(2026, 7, 30)):
    pack = pack or _pack()
    return to_event_record(
        collect([row], today=today.isoformat())["rows"][0], row,
        frame=_frame(pack), today=today)


# --------------------------------------------------------------------------- #
# 1. the source vocabulary (the defect that would have shipped a silent zero)
# --------------------------------------------------------------------------- #
def test_collect_reads_the_store_vocabulary_not_the_extract_headers():
    """`collect` speaks notice-store columns. The daily extract's CSV headers
    are different words entirely, so handing extract rows to this lane matches
    nothing and reports a confident zero rather than failing."""
    extract_shaped = {
        "NoticeId": GUID,
        "Title": "Industry Day for Network Visibility Modernization",
        "Type": "Special Notice",
        "Description": "An industry day for interested vendors.",
        "ResponseDeadLine": FUTURE,
        "Department/Ind.Agency": "DEPT OF DEFENSE",
    }
    assert collect([extract_shaped], today="2026-07-30")["rows"] == []
    # the same notice, in the vocabulary the module actually reads
    assert len(collect([_store_row()], today="2026-07-30")["rows"]) == 1


def test_a_generator_is_screened_not_exhausted():
    """The extract loader is a generator, and the events lane consumes one
    before this lane runs. Screening a spent iterator reports zero screened,
    which is indistinguishable from a clean empty result."""
    rows = (r for r in [_store_row()])
    records, receipt = harvest_from_store(rows, _pack(), today=date(2026, 7, 30))
    assert receipt["screened"] == 1
    assert len(records) == 1


# --------------------------------------------------------------------------- #
# 2. mapping and the canonical URL
# --------------------------------------------------------------------------- #
def test_a_collected_row_becomes_a_valid_event_record():
    record = _map(_store_row())
    assert isinstance(record, EventRecord)
    assert record.event_id == GUID
    assert record.source_system == "sam_extract"
    assert record.event_type == "government-hosted"
    assert record.registration_deadline == FUTURE
    assert record.registration_deadline_source
    assert record.matched_phrase == "industry day"
    assert record.naics == "541512"
    assert record.posted_date == "2026-07-20"


def test_the_url_is_the_canonical_public_notice_form():
    """The store's own url column is the login-walled sam.gov/workspace form,
    which client HTML bans outright. The public form is rebuilt from the one
    canonical builder, never string-formatted a second time."""
    record = _map(_store_row(url="https://sam.gov/workspace/contract/opp/x/view"))
    assert record.url == build_sam_notice_link(GUID).url
    assert record.url == f"https://sam.gov/opp/{GUID}/view"
    assert "workspace" not in record.url


def test_a_notice_id_that_will_not_build_a_url_does_not_ship():
    records, receipt = harvest_from_store(
        [_store_row(notice_id="not-a-guid")], _pack(), today=date(2026, 7, 30))
    assert records == []
    assert receipt["dropped_unbuildable_url"] == 1


def test_the_receipt_does_not_blame_a_bad_id_for_an_irrelevant_notice():
    """Two different drops with two different meanings: an unbuildable id is
    a data defect worth chasing, an irrelevant notice is the screen working.
    A row that is BOTH is filed once, under the defect."""
    irrelevant_and_fine = _store_row(
        notice_id=OTHER_GUID, title="Industry Day for Dredging",
        agency="DEPT OF THE INTERIOR", subtier="BUREAU OF REC", naics="237990",
        description_prefix="An industry day for dredging.")
    records, receipt = harvest_from_store(
        [irrelevant_and_fine], _pack(), today=date(2026, 7, 30))
    assert records == []
    assert receipt["dropped_unbuildable_url"] == 0     # its id was fine
    assert receipt["client_relevant"] == 0


# --------------------------------------------------------------------------- #
# 3. the event date is never asserted
# --------------------------------------------------------------------------- #
def test_the_event_date_is_never_synthesized():
    """This module has never parsed the day an industry day is held. The band
    renders TBD; it does not render an approximation."""
    record = _map(_store_row(description_prefix="An industry day is planned."))
    assert record.event_start is None
    assert record.event_end is None
    assert record.event_date_span is None


def test_a_prose_candidate_date_rides_inert_and_is_never_promoted():
    """A date lifted from prose is a lead for the operator, not a claim. It
    stays in relevance_basis, which no renderer prints and the date validator
    does not read."""
    row = _store_row(
        description_prefix="The industry day will be held 15 September 2026 "
                           "at the Navy Yard.")
    record = _map(row)
    assert record.event_start is None
    candidate = record.relevance_basis["industry_day_candidate_date"]
    assert candidate["value"] == "15 September 2026"
    assert candidate["verified"] is False
    assert candidate["source_sentence"]


def test_a_candidate_date_never_reaches_the_validator_as_a_pack_date():
    """The inert candidate must not widen date provenance: if it did, the
    report could state 15 September and the validator would allow it."""
    row = _store_row(
        description_prefix="The industry day will be held 15 September 2026.")
    pack = _pack()
    pack.events = [_map(row).model_dump()]
    assert "2026-09-15" not in _pack_date_keys(pack)


# --------------------------------------------------------------------------- #
# 4. date provenance
# --------------------------------------------------------------------------- #
def test_every_date_on_the_record_is_a_pack_date():
    """A date RENDERED in the events band that is not on the event record is
    a date_without_provenance violation, so every date the record carries
    must reconcile once the record is in the pack."""
    pack = _pack()
    record = _map(_store_row(), pack=pack)
    pack.events = [record.model_dump()]
    keys = _pack_date_keys(pack)
    for value in (record.registration_deadline, record.posted_date):
        assert value in keys, f"{value} is rendered but has no provenance"


# --------------------------------------------------------------------------- #
# 5. relevance: the screen that keeps 67 unrelated events out
# --------------------------------------------------------------------------- #
def test_an_industry_day_with_no_connection_to_the_pack_does_not_ship():
    """Measured 2026-07-30 the live store held 67 distinct future industry
    days, essentially none connected to any one client's book. `collect` does
    not screen for relevance, so the seam must."""
    records, receipt = harvest_from_store(
        [_store_row(title="Industry Day for Shipyard Dredging",
                    agency="DEPT OF THE INTERIOR", subtier="BUREAU OF REC",
                    naics="237990",
                    description_prefix="An industry day for dredging.")],
        _pack(), today=date(2026, 7, 30))
    assert records == []
    assert receipt["distinct_after_dedupe"] == 1   # it IS an industry day
    assert receipt["client_relevant"] == 0         # it is not THIS client's


def test_a_shipped_row_carries_a_relevance_line_bound_to_a_pack_fact():
    record = _map(_store_row())
    assert record.relevance
    assert record.relevance_basis
    assert record.relevance_basis.get("strength") in {"capability", "corridor"}


# --------------------------------------------------------------------------- #
# 6. decode wreckage
# --------------------------------------------------------------------------- #
def test_replacement_characters_never_reach_a_client_title():
    """The store ingests with errors="replace", so seven of the 67 live
    titles carry U+FFFD where a cp1252 dash was. lint_emdash does not fire on
    it, which is exactly why it would have shipped."""
    record = _map(_store_row(title="Reverse Industry Day � Capabilities"))
    assert "�" not in record.name
    assert record.name == "Reverse Industry Day Capabilities"


def test_clean_collapses_whitespace_without_eating_content():
    assert _clean("  a   b � c ") == "a b c"
    assert _clean(None) == ""


# --------------------------------------------------------------------------- #
# 7. dedupe against tier A
# --------------------------------------------------------------------------- #
def test_a_notice_already_shipped_by_tier_a_is_dropped():
    """The same notice legitimately reaches both tiers: tier A sees it in
    today's extract and this lane sees it in the store that ingested that
    same extract. One gathering, one row."""
    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    tier_a = [EventRecord(event_id=GUID, name="same notice, other tier",
                          event_type="government-hosted",
                          source_system="sam_extract", url="https://sam.gov/x")]
    assert drop_already_shipped(records, tier_a) == []


def test_dedupe_reads_tier_a_rows_in_either_shape():
    """Tier A hands over EventRecords, but a resumed pack carries plain
    dicts. Matching only one shape would silently double every row."""
    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    assert drop_already_shipped(records, [{"event_id": GUID}]) == []
    assert drop_already_shipped(records, [{"event_id": OTHER_GUID}]) == records


def test_dedupe_keeps_a_notice_tier_a_never_saw():
    """The whole point of the store as a source: 6.4% of notices vanish from
    the extract every three days, and those are the ones tier A cannot see."""
    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    tier_a = [EventRecord(event_id=OTHER_GUID, name="a different event",
                          event_type="government-hosted",
                          source_system="sam_extract", url="https://sam.gov/y")]
    assert drop_already_shipped(records, tier_a) == records


def test_two_distinct_notices_both_survive():
    rows = [_store_row(), _store_row(notice_id=OTHER_GUID,
                                     title="Vendor Day for Packet Capture")]
    records, receipt = harvest_from_store(rows, _pack(), today=date(2026, 7, 30))
    assert {r.event_id for r in records} == {GUID, OTHER_GUID}
    assert receipt["client_relevant"] == 2


# --------------------------------------------------------------------------- #
# 8. verification and the empty case
# --------------------------------------------------------------------------- #
def test_harvest_does_not_claim_verification_it_has_not_done():
    """`harvest_from_store` returns CANDIDATES. The caller verifies live, and
    an unverified row is what the validator refuses to ship."""
    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    assert records[0].url_verified is False
    assert records[0].url_status is None


def test_an_unverified_url_is_skipped_and_disclosed():
    from agents.golden_press.events import verify_urls

    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    verified, skipped = verify_urls(records, fetch=lambda url: (404, ""))
    assert verified == []
    assert skipped[0]["event_id"] == GUID
    assert skipped[0]["reason"] == "HTTP 404"


def test_a_verified_url_is_marked_with_its_real_status():
    from agents.golden_press.events import verify_urls

    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    verified, skipped = verify_urls(records, fetch=lambda url: (200, "ok"))
    assert skipped == []
    assert verified[0].url_verified is True
    assert verified[0].url_status == 200


def test_no_industry_days_leaves_the_pack_exactly_as_it_was():
    """An empty result must leave no events and no empty band, and still
    state what it screened rather than reading as a hole."""
    pack = _pack()
    records, receipt = harvest_from_store([], pack, today=date(2026, 7, 30))
    assert records == []
    assert pack.events == []
    assert receipt["screened"] == 0
    assert receipt["client_relevant"] == 0
    assert receipt["event_phrases"]                 # the screen is disclosed
    assert receipt["excluded_as_procurement_mechanics"]
    assert receipt["source"] == "sam_notice_store"
    assert receipt["metered_quota_spent"] == 0


def test_a_past_industry_day_does_not_ship():
    records, receipt = harvest_from_store(
        [_store_row(deadline="2026-01-05")], _pack(), today=date(2026, 7, 30))
    assert records == []
    assert receipt["matched_event_phrase"] == 1
    assert receipt["still_future"] == 0


# --------------------------------------------------------------------------- #
# 9. the band renders what it is handed
# --------------------------------------------------------------------------- #
def test_a_row_without_an_account_tie_rides_the_monitor_strip():
    """G5 (2026-08-03): featured requires open registration or a future
    date AND a Band 01/02 account tie. A pack with no decision accounts
    features nothing; the verified row rides the monitor strip, links
    only, no claim attached."""
    from agents.golden_press.events import verify_urls
    from agents.golden_press.render import render_events

    records, _ = harvest_from_store([_store_row()], _pack(),
                                    today=date(2026, 7, 30))
    verified, _skipped = verify_urls(records, fetch=lambda url: (200, "ok"))
    html = render_events(9, {"events": [verified[0].model_dump()],
                             "generated_at": "2026-07-30T09:00:00",
                             "decisions": None}, {})
    assert "MONITOR · LINKS ONLY · NO CLAIM ATTACHED" in html
    assert f'href="{build_sam_notice_link(GUID).url}"' in html
    assert "Industry Day for Network Visibility Modernization" in html
    assert '<article class="event">' not in html   # never featured


@pytest.mark.parametrize("field", ["event_start", "event_end"])
def test_the_record_never_carries_an_event_day(field):
    assert getattr(_map(_store_row()), field) is None


# --------------------------------------------------------------------------- #
# 9b. the second route in: a room worth being in
#
# `score_relevance` asks whether the pack already holds a cited award at this
# agency. Right question for a live bid, wrong one for a gathering: it makes a
# client earn their way into a room by already winning there, and an industry
# day missed is not re-run. So an event also qualifies on the two boundaries
# the OPERATOR set. BOTH legs are required; measured 2026-07-30 on the live
# store, either leg alone fails:
#   NAICS alone  -> 2 DoD events into a civilian-scoped engagement.
#   scope alone  -> FEMA Emergency Meal, Star of Life Ambulances, ship repair.
#   both         -> 0, which is why this widening is not a floodgate.
# --------------------------------------------------------------------------- #
class _Scope:
    """Minimal stand-in: civilian means everything except Defense."""

    resolved_basis = "preset:civilian@v1"


@pytest.fixture
def civilian(monkeypatch):
    def _in_scope(record, scope):
        agency = " ".join(str(record.get("agency") or "").split()).casefold()
        return ("defense" not in agency and "army" not in agency), "stub"

    monkeypatch.setattr("tools.relevance.scope.in_scope", _in_scope)
    return _Scope()


def test_an_in_boundary_event_at_an_uncited_agency_is_a_room_to_be_in(civilian):
    """The whole point: NASA is in scope and the notice is in the NAICS
    boundary, but the pack holds no NASA award. Under the corridor screen
    alone this event is invisible. The title deliberately carries NO client
    capability term, so only the boundary route can admit it."""
    records, receipt = harvest_from_store(
        [_store_row(agency="NATIONAL AERONAUTICS AND SPACE ADMINISTRATION",
                    subtier="NASA HQ", office="NASA PROCUREMENT",
                    naics="541512",
                    title="Industry Day for Ground Systems Sustainment",
                    description_prefix="An industry day for vendors.")],
        _pack(), today=date(2026, 7, 30), scope=civilian)
    assert len(records) == 1
    assert records[0].relevance_basis["strength"] == "boundary"
    assert receipt["kept_by_route"] == {"boundary": 1}


def test_the_room_line_never_claims_a_corridor_it_does_not_have(civilian):
    records, _ = harvest_from_store(
        [_store_row(agency="NATIONAL AERONAUTICS AND SPACE ADMINISTRATION",
                    subtier="NASA HQ", office="NASA PROCUREMENT",
                    naics="541512",
                    title="Industry Day for Ground Systems Sustainment",
                    description_prefix="An industry day for vendors.")],
        _pack(), today=date(2026, 7, 30), scope=civilian)
    line = records[0].relevance
    assert "no cited award in this pack" in line
    assert "room to be in" in line


def test_out_of_scope_is_refused_even_inside_the_naics_boundary(civilian):
    """THE CASE THAT KILLED THE FIRST PROPOSAL: NAICS alone would have pushed
    DoD industry days into a civilian-scoped engagement."""
    records, receipt = harvest_from_store(
        [_store_row(agency="DEPT OF DEFENSE", subtier="DEPT OF THE NAVY",
                    naics="541512")],
        _pack(), today=date(2026, 7, 30), scope=civilian)
    assert records == []
    assert receipt["kept_by_route"] == {}
    assert receipt["engagement_scope"] == "preset:civilian@v1"


def test_in_scope_but_outside_the_naics_boundary_is_refused(civilian):
    """The other half: scope alone returns emergency meals and ambulances."""
    records, _ = harvest_from_store(
        [_store_row(agency="HOMELAND SECURITY, DEPARTMENT OF", naics="311999",
                    title="FEMA 2026 Emergency Meal Industry Day",
                    description_prefix="An industry day for meal vendors.")],
        _pack(), today=date(2026, 7, 30), scope=civilian)
    assert records == []


def test_the_boundary_is_the_approved_one_not_an_incidental_award_code():
    """`pack_frame` merges approved codes with whatever codes the pack's
    awards happen to carry. Widening the band on an incidental code would let
    the operator's boundary drift without anyone deciding to."""
    from agents.golden_press.industry_days import naics_boundary

    pack = _pack()
    pack.research["inferred_naics"] = ["541512"]
    pack.records[0].naics = "999999"
    assert naics_boundary(pack) == {"541512"}


def test_a_cited_corridor_still_wins_and_says_so(civilian):
    """Additive only: this route may widen the band, never narrow it, and an
    event the corridor screen already admits keeps its corridor line."""
    records, receipt = harvest_from_store(
        [_store_row()], _pack(), today=date(2026, 7, 30), scope=civilian)
    assert len(records) == 1
    assert records[0].relevance_basis["strength"] in {"capability", "corridor"}
    assert "room to be in" not in records[0].relevance


# --------------------------------------------------------------------------- #
# 10. the certified press path
#
# A malformed events row does not merely look wrong: golden_press ships the
# report anyway with certified=False, writes a .FAILED.html copy, and the
# Desktop delivery parks it as "... DRAFT n.html" instead of the clean name.
# These two tests are the tripwire for that.
# --------------------------------------------------------------------------- #
def _shipped_pack():
    from agents.golden_press.events import verify_urls

    pack = _pack()
    records, _ = harvest_from_store([_store_row()], pack,
                                    today=date(2026, 7, 30))
    verified, _skipped = verify_urls(records, fetch=lambda url: (200, "ok"))
    pack.events = [r.model_dump() for r in verified]
    return pack


def test_a_pack_carrying_an_industry_day_validates_clean():
    """certified=True is exactly verdict['ok']. An industry day that fails
    here is a DRAFT-named file on the operator's Desktop."""
    from agents.golden_press import render as render_mod
    from agents.golden_press.validate import validate_press

    pack = _shipped_pack()
    content = render_mod.render_content_region(pack, prose={})
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_the_industry_day_actually_renders_in_the_events_band():
    """A clean verdict on a pack whose event never rendered would be a green
    light on an empty band."""
    from agents.golden_press import render as render_mod

    pack = _shipped_pack()
    content = render_mod.render_content_region(pack, prose={})
    assert 'id="events"' in content
    band = content[content.find('id="events"'):content.find('id="evidence"')]
    assert "Industry Day for Network Visibility Modernization" in band
    assert build_sam_notice_link(GUID).url in band


def test_an_events_free_pack_states_its_zero_in_the_band():
    """CONTRACT §1 (2026-08-03): every band is always present; an
    events-free pack renders the zero-state, never an absent band."""
    from agents.golden_press import render as render_mod
    from agents.golden_press.validate import validate_press

    pack = _pack()
    content = render_mod.render_content_region(pack, prose={})
    assert 'id="events"' in content
    assert validate_press(content, content, pack)["ok"]


# --------------------------------------------------------------------------- #
# 11. three deadline states, not one comparison (audit 2026-07-30)
#
# `str(deadline)[:10] < cutoff` treated an EMPTY deadline as past - "" sorts
# before every date - and silently dropped 103 of 696 matched gatherings
# (14.8%, measured live). An industry day posted without a structured
# deadline is normal contracting-officer behavior, not a finished event.
# --------------------------------------------------------------------------- #
def test_an_undated_but_active_industry_day_is_kept():
    """The notice is still in the extract (its last_seen equals the store's
    newest), so the room is live even though no deadline was published."""
    rows = [_store_row(deadline="", last_seen="2026-07-28"),
            _store_row(notice_id=OTHER_GUID, title="Some other notice",
                       notice_type="Solicitation",
                       description_prefix="routine buy",
                       last_seen="2026-07-28")]
    out = collect(rows, today="2026-07-30")
    assert len(out["rows"]) == 1
    assert out["rows"][0]["registration_deadline"] is None
    assert out["receipt"]["undated_kept_active"] == 1


def test_an_undated_dropped_notice_is_stale_not_a_room():
    """Gone from the extract with no date: an unknowable gathering."""
    rows = [_store_row(deadline="", last_seen="2026-07-20"),
            _store_row(notice_id=OTHER_GUID, title="Some other notice",
                       notice_type="Solicitation",
                       description_prefix="routine buy",
                       last_seen="2026-07-28")]
    out = collect(rows, today="2026-07-30")
    assert out["rows"] == []
    assert out["receipt"]["undated_dropped_stale"] == 1


def test_a_recently_closed_industry_day_is_kept_and_flagged():
    """Mirrors tier A: a just-missed gathering is intelligence about the
    program, flagged, never hidden."""
    out = collect([_store_row(deadline="2026-07-20")], today="2026-07-30")
    assert len(out["rows"]) == 1
    assert out["receipt"]["recently_closed_kept"] == 1
    record = _map(_store_row(deadline="2026-07-20"))
    assert record.registration_closed is True


def test_a_long_closed_industry_day_still_drops():
    out = collect([_store_row(deadline="2026-01-05")], today="2026-07-30")
    assert out["rows"] == []
    assert out["receipt"]["recently_closed_kept"] == 0


def test_an_undated_kept_room_renders_without_inventing_a_date():
    """No deadline, no event date: the band renders TBD and 'date not
    published', never an approximation - and the record must still ship."""
    rows = [_store_row(deadline="", last_seen="2026-07-28")]
    records, receipt = harvest_from_store(rows, _pack(),
                                          today=date(2026, 7, 30))
    assert len(records) == 1
    assert records[0].registration_deadline is None
    assert records[0].event_start is None
    assert records[0].registration_closed is False


def test_event_note_dedupes_on_the_cited_record_not_the_sentence():
    """_CITED_RECORD was referenced by _event_note and never defined: any
    featured event carrying a relevance note raised NameError and killed
    the press. It stayed hidden because an event had to clear the account
    tie to be featured at all; the v1.2 unbound-feature fallback exposed
    it. Two notes citing the SAME record render once; a different record
    renders again."""
    from agents.golden_press.render import _event_note

    seen: set = set()
    first = ("Department of the Navy buying account · your pack cites "
             "N0018918FZA15 at $436,549 · event NAICS 334118 is inside "
             "the boundary")
    same_record = ("your Department of the Navy corridor cites "
                   "N0018918FZA15 at $436,549")
    other_record = ("your Department of the Air Force corridor cites "
                    "FA875117FA049 at $8,076,995")

    assert "N0018918FZA15" in _event_note(first, seen)
    assert _event_note(same_record, seen) == ""
    assert "FA875117FA049" in _event_note(other_record, seen)
    assert _event_note(None, seen) == ""


def test_event_relevance_notes_never_emit_banned_vocabulary():
    """Both note builders write text straight into the artifact once an
    event is featured. The conference builder said 'your X corridor cites
    ...', which is a banned invented term (contract L7): it failed the
    Thinklogical press the moment the v1.2 fallback let conference events
    render. Every note template is now checked against the live list."""
    import inspect
    import re

    from agents.golden_press import events, events_conferences
    from agents.golden_press.contract import banned_vocabulary

    terms = [t for t in banned_vocabulary() if t]
    assert terms, "banned list must be non-empty for this test to bite"

    for module in (events, events_conferences):
        source = inspect.getsource(module)
        # Only the literals that become note text: f-strings appended to a
        # note, not comments or identifiers.
        for literal in re.findall(r'f"([^"]{20,})"', source):
            for term in terms:
                assert not re.search(rf"\b{re.escape(term)}\b", literal,
                                     re.I), (
                    f"{module.__name__} builds note text containing the "
                    f"banned term {term!r}: {literal[:80]!r}")
