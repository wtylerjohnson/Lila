"""Deterministic rendering of the golden content region (2026-07-27).

These tests guard the three things that made the previous LLM-composed path
unreliable, and the two traps found while building the replacement:

  1. a figure can only reach the page from the pack, never from a model
  2. no dated record is ever silently dropped from the calendar
  3. truncation never emits half an identifier
  4. a seal is never pinned to the wrong agency by substring luck
  5. prose is optional: every slot degrades to a deterministic fact
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import render as render_mod  # noqa: E402
from agents.golden_press import seals as seals_mod  # noqa: E402
from agents.golden_press.compose import _parse_prose, _prose_usable  # noqa: E402
from agents.golden_press.doctrine import BY_ID, PROSE_GRAMMAR  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402
from agents.golden_press.validate import validate_press  # noqa: E402


def _record(**kw):
    base = dict(record_id="GS35F001", lane="L2_entity_award",
                title="Network performance monitoring support",
                description="Network performance monitoring support",
                agency="Department of the Treasury",
                sub_agency="Internal Revenue Service",
                recipient="Acme Networks", obligated_dollars=1_920_000.0,
                url="https://www.usaspending.gov/award/CONT_AWD_GS35F001",
                period_end="2026-07-31")
    base.update(kw)
    return GoldenRecord(**base)


def _pack(records=None, **kw):
    return EvidencePack(client_name="Acme Networks",
                        generated_at="2026-07-27T12:00:00Z",
                        records=records if records is not None else [_record()],
                        **kw)


# ---- calendar dates -------------------------------------------------------- #
@pytest.mark.parametrize("value,expected_key", [
    ("2026-07-31", "2026-07-31"),
    ("7/31/2026", "2026-07-31"),
    ("Q2 2026", "2026-04-00"),        # calendar quarter
    ("FY2026 Q1", "2025-10-00"),      # fiscal year opens the prior October
    ("FY26 Q1", "2025-10-00"),        # two-digit fiscal year
    ("FY2026 Q3", "2026-04-00"),
    ("FY 2027", "2026-10-00"),
    ("2027", "2027-00-00"),
])
def test_calendar_when_sorts_every_published_date_shape(value, expected_key):
    assert render_mod.calendar_when(value)[0] == expected_key


@pytest.mark.parametrize("value", ["Q2 2026", "FY2026 Q3", "TBD", "2027"])
def test_calendar_never_sharpens_a_coarse_date(value):
    """A quarter displays as a quarter. Rendering "Q2 2026" as a specific day
    would invent a precision the source never published."""
    assert render_mod.calendar_when(value)[1] == value.upper()


def test_unparseable_date_sorts_last_but_is_never_dropped():
    key, label = render_mod.calendar_when("date to be determined")
    assert key.startswith("9999")
    assert label


def test_clocks_carry_only_in_report_accounts_and_flag_expired():
    """CONTRACT §2/03 replaced the calendar-completeness law: the clocks
    table carries ONLY clocks belonging to accounts in Bands 01-02, expired
    clocks stay flagged, and removals are stated in Show the work."""
    from agents.golden_press.decision_rules import build_decisions
    records = [
        _record(record_id="A1", period_end="2026-09-30"),
        _record(record_id="RIV1", entity_hits=["OtherMonitor"],
                recipient="Rival Prime Inc", period_end="2026-08-15",
                url="https://www.usaspending.gov/award/RIV1"),
        _record(record_id="OUT1", sub_agency="Bureau of Engraving and Printing",
                period_end="2026-10-31",
                url="https://www.usaspending.gov/award/OUT1"),
        _record(record_id="EXP1", period_end="2026-05-01",
                url="https://www.usaspending.gov/award/EXP1"),
    ]
    pack = _pack(records, research={
        "entities": {"competitor": ["OtherMonitor"], "product": []}})
    from datetime import date
    pack.decisions = build_decisions(pack, today=date(2026, 7, 27))
    content = render_mod.render_content_region(pack, prose={})
    clocks = content[content.index('id="clocks"'):content.index('id="paper"')]
    assert "A1" in clocks and "RIV1" in clocks
    assert "OUT1" not in clocks          # out-of-report account, removed
    assert "EXP1" in clocks              # expired stays, flagged
    assert 'class="flag"' in clocks
    method = content[content.index('id="method"'):]
    assert "removed" in method           # the removal is stated


# ---- figures come from the pack, never the model --------------------------- #
@pytest.mark.parametrize("text", [
    "This estate is worth $9,999,999 across the portfolio right now.",
    "The contract W912DY99D0001 renews on a schedule worth tracking.",
    "Roughly 47 awards sit inside this corridor at the present moment.",
    "The renewal lands in 2031 and should be worked well before then.",
])
def test_prose_carrying_a_figure_is_discarded(text):
    assert _prose_usable(text) is None


def test_clean_prose_survives():
    assert _prose_usable(
        "The largest active award anchors this estate in a single bureau.")


def test_prose_parse_salvages_a_truncated_object():
    """A generation cut off mid-object keeps every slot that did complete."""
    raw = '{"hero_context": "one complete sentence that is long enough", "b": "trunc'
    assert "hero_context" in _parse_prose(raw)


def test_render_falls_back_to_fact_when_prose_is_absent():
    """Prose is optional by design: no prose still yields a full region."""
    content = render_mod.render_content_region(_pack(), prose={})
    assert len(content) > 5000
    assert 'id="method"' in content
    assert "GS35F001" in content


def test_renderer_uses_the_plain_speak_federal_sales_doctrine():
    content = render_mod.render_content_region(_pack(), prose={})
    for band_id in ("decisions", "forward", "clocks", "paper", "agencies",
                    "events", "method"):
        assert BY_ID[band_id].heading in content
    assert PROSE_GRAMMAR == (
        "what the evidence says",
        "why it matters",
        "what to verify or do next",
        "external source",
    )


def test_paper_band_ranks_primes_with_route_caveat():
    records = [
        _record(record_id="CORE1", recipient="Prime Route LLC",
                url="https://www.usaspending.gov/award/CORE1"),
        _record(record_id="CORE2", recipient="Prime Route LLC",
                obligated_dollars=800_000,
                url="https://www.usaspending.gov/award/CORE2"),
        _record(record_id="RIVAL1", recipient="Rival Prime Inc",
                obligated_dollars=500_000, entity_hits=["OtherMonitor"],
                url="https://www.usaspending.gov/award/RIVAL1"),
    ]
    pack = _pack(records, research={
        "entities": {"competitor": ["OtherMonitor"], "product": []}
    })
    content = render_mod.render_content_region(pack, prose={})
    paper = content[content.index('id="paper"'):content.index('id="agencies"')]
    assert "Prime Route LLC" in paper
    assert "Rival Prime Inc" in paper
    assert "route" in paper.casefold()   # the route-not-partner caveat
    assert "validate vehicle access" in paper


def test_agencies_band_groups_by_buyer_with_linked_records():
    records = [
        _record(record_id="IRS1", sub_agency="Internal Revenue Service",
                url="https://www.usaspending.gov/award/IRS1"),
        _record(record_id="CBP1", agency="Department of Homeland Security",
                sub_agency="U.S. Customs and Border Protection",
                obligated_dollars=900_000,
                url="https://www.usaspending.gov/award/CBP1"),
    ]
    content = render_mod.render_content_region(_pack(records), prose={})
    section = content[content.index('id="agencies"'):content.index('id="events"')]
    assert "Internal Revenue Service" in section
    assert "U.S. Customs and Border Protection" in section
    assert 'href="https://www.usaspending.gov/award/IRS1"' in section
    assert 'href="https://www.usaspending.gov/award/CBP1"' in section
    assert "past obligations on cited records" in section
    assert "istor" in section            # the historical-context lede


def test_agencies_scope_cell_anchors_a_records_own_id_in_its_description():
    """LINKAGE LAW regression (witnessed 2026-08-05): the DCMA Data Link
    Solutions award's DAAS-era description ends with its own delivery-order
    number ("...!A!N! !N!0002"). Band 05 printed that verbatim text bare and
    the press shipped FAILED on record_id_not_linked. The law's own remedy
    applies in place: the occurrence renders inside the record's canonical
    anchor; the source text is never edited to hide the id and the check
    never learns an exception."""
    from agents.golden_press.validate import check_links
    rec = _record(
        record_id="0002",
        description="200211!000402!5700!GJ90 !WARNER ROBINS AKC/LYK "
                    "!F0960302D0088 !A!N! !N!0002",
        url="https://www.usaspending.gov/award/CONT_AWD_0002_9700")
    pack = _pack([rec])
    content = render_mod.render_content_region(pack, prose={})
    assert check_links(content, pack) == []
    section = content[content.index('id="agencies"'):content.index('id="events"')]
    assert "!A!N! !N!" in section        # the source text still shows
    # desc-cell occurrence and rec-cell id are both canonical anchors
    assert section.count(
        'href="https://www.usaspending.gov/award/CONT_AWD_0002_9700"') >= 2


def test_clocks_scope_cell_anchors_a_records_own_id_in_its_description():
    """LINKAGE LAW, clocks-table edition (2026-08-05): a short DAAS-era
    description keeps its own delivery-order number inside the 64-char
    scope clip, and clock_row's escaping would have flattened any anchor
    into bare copy. The Band 05 remedy applies through what_html: the
    occurrence rides the record's canonical anchor in place; the text is
    never edited and check_links learns no exception."""
    from agents.golden_press.validate import check_links
    rec = _record(
        record_id="0002",
        description="!N!0002 WARNER ROBINS AKC/LYK",
        url="https://www.usaspending.gov/award/CONT_AWD_0002_9700",
        period_end="2026-09-30")
    pack = _pack([rec])
    content = render_mod.render_content_region(pack, prose={})
    assert check_links(content, pack) == []
    clocks = content[content.index('id="clocks"'):content.index('id="paper"')]
    assert "WARNER ROBINS" in clocks     # the scope text still shows
    # scope-cell occurrence and record-cell id are both canonical anchors
    assert clocks.count(
        'href="https://www.usaspending.gov/award/CONT_AWD_0002_9700"') >= 2


# ---- truncation is identifier-safe ----------------------------------------- #
def test_clip_never_emits_half_an_identifier():
    """REGRESSION 2026-07-27: a character-boundary cut produced the token
    "28321323FDX0" from a longer PIID, which read to the validator as a
    contract number with no provenance."""
    text = "Support services under 28321323FDX0001 for the current period"
    source_tokens = set(text.split())
    for limit in range(4, len(text) + 5):
        out = render_mod.clip(text, limit)
        for token in out.split():
            assert token in source_tokens, (
                f"limit {limit} produced partial token {token!r}")


def test_clip_overruns_the_limit_rather_than_splitting_a_lone_token():
    """A window holding no space emits the whole token. Display width is
    cosmetic; half a contract number is a factual error."""
    assert render_mod.clip("28321323FDX0001", 8) == "28321323FDX0001"


# ---- seals ----------------------------------------------------------------- #
def test_catalogued_agency_gets_its_seal():
    assert seals_mod.resolve("Internal Revenue Service")
    assert seals_mod.resolve("CBP")


def test_alias_matching_is_whole_token_not_substring():
    """The alias "ice" (Immigration and Customs Enforcement) must not match
    inside "service", "office", or "device" and pin a Homeland Security seal
    onto an unrelated buyer."""
    catalog = {"seals": {"department of homeland security": "data:x"},
               "aliases": {"ice": "department of homeland security"}}
    assert seals_mod.resolve("Public Service Commission", catalog=catalog) is None
    assert seals_mod.resolve("Office of the Chief Information Officer",
                             catalog=catalog) is None
    assert seals_mod.resolve("ICE", catalog=catalog) == "data:x"


def test_unknown_agency_gets_no_seal_and_no_guess():
    assert seals_mod.resolve("Ministry of Fictional Affairs") is None


def test_every_deck_and_agency_row_fills_its_mark_slot():
    """mark-slot-never-empty in the library grammar: every deck card and
    every agency summary renders a mark (seal/dmk/pmk img or monogram),
    never an empty slot (successor of the sb-route-seal column test)."""
    from agents.golden_press.decision_rules import build_decisions
    pack = _pack([_record(),
                  _record(record_id="RIV1", recipient="Rival Prime Inc",
                          obligated_dollars=400_000.0,
                          entity_hits=["OtherMonitor"]),
                  _record(record_id="X9", agency="Ministry of Nowhere",
                          sub_agency=None, recipient="Mystery Prime LLC")],
                 research={"entities": {"competitor": ["OtherMonitor"],
                                        "product": []}})
    from datetime import date
    pack.decisions = build_decisions(pack, today=date(2026, 7, 27))
    content = render_mod.render_content_region(pack, prose={})
    decks = re.split(r'(?=<article class="deck)', content)[1:]
    assert decks, "fixture rendered no deck cards; test would be vacuous"
    missing = [d[:100] for d in decks
               if 'has-img' not in d and 'no-img' not in d]
    assert not missing, f"deck cards with empty mark slots: {missing}"
    agencies = re.split(r'(?=<details class="agency)', content)[1:]
    assert agencies, "fixture rendered no agency rows; vacuous"
    empty = [a[:100] for a in agencies
             if 'has-img' not in a and 'no-img' not in a]
    assert not empty, f"agency rows with empty mark slots: {empty}"


def test_missing_catalog_degrades_the_look_not_the_press():
    empty = {"seals": {}, "aliases": {}}
    assert seals_mod.resolve("Internal Revenue Service", catalog=empty) is None


# ---- the whole region ------------------------------------------------------ #
def test_rendered_region_validates_clean():
    """The rules the validator carries are unfirable from this path by
    construction. This test is the tripwire that says so."""
    pack = _pack([
        _record(),
        _record(record_id="F2026074529", lane="L4_forecast", period_end=None,
                obligated_dollars=None, recipient=None,
                anticipated_solicitation="Q2 2026",
                url="https://apfs-cloud.dhs.gov/record/4529/public-print/"),
    ])
    content = render_mod.render_content_region(pack, prose={})
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
