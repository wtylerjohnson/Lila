"""Events-join truth purge (operator filing, 2026-08-03).

The apexanalytix press proved the defect class: awards to Accenture, IBM,
and Palantir carrying the bare dictionary-word entity hit 'Platform' minted
DoD/VA/ED corridors, the records' own NAICS codes widened the boundary, and
seven DoD/VA filler events shipped in an AP-audit client's report. The purge:
a corridor is the CLIENT's own funded paper under the shared guards, the
boundary is the operator-approved inferred list only, and a client whose
corridors connect to no relevant event renders the zero-state screening
receipt, never filler.

Offline; fixture packs; no network, no LLM.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.events import (  # noqa: E402
    harvest_tier_a,
    pack_frame,
    score_relevance,
)
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402

TODAY = date(2026, 8, 1)


def _award(record_id, *, agency, sub=None, recipient="SOME PRIME LLC",
           hits=(), title="Enterprise services", description=None,
           naics=None, dollars=1_000_000.0):
    return GoldenRecord(
        record_id=record_id, lane="L2_entity_award", title=title,
        agency=agency, sub_agency=sub, recipient=recipient,
        entity_hits=list(hits), obligated_dollars=dollars,
        period_end="2027-03-31", naics=naics,
        url=f"https://www.usaspending.gov/award/CONT_AWD_{record_id}",
        description=description)


def _pack(records, *, client="apexanalytix", entities=None, naics=None):
    return EvidencePack(
        client_name=client, generated_at="2026-08-01T12:00:00Z",
        records=records,
        research={
            "screen_terms": ["AP recovery audit", "supplier onboarding"],
            "inferred_naics": list(naics or ["541219"]),
            "entities": entities or {
                "product": ["apexanalytix Platform", "SmartVM"],
                "competitor": [],
            },
        })


def _dod_event_row(naics="541511"):
    return {
        "NoticeId": "abc123", "Title": "Army Intelligence Data Platform RFI",
        "Type": "Special Notice",
        "Description": "Industry day for the data platform program.",
        "NaicsCode": naics,
        "Department/Ind.Agency": "DEPT OF DEFENSE", "Sub-Tier": "DEPT OF THE ARMY",
        "Office": "ARMY CONTRACTING",
    }


def test_a_strangers_award_never_mints_a_corridor():
    """The apexanalytix class exactly: a Palantir award surfaced by the bare
    dictionary-word hit 'Platform' (guarded out: common word, vendor absent
    from the record's own text) must not put the client in a DoD corridor."""
    junk = _award("W519TC26F0012", agency="Department of Defense",
                  sub="Department of the Army", recipient="PALANTIR USG INC",
                  hits=("Platform",), naics="541511",
                  title="Army data platform integration",
                  description="Data platform integration services.")
    frame = pack_frame(_pack([junk]))
    assert frame["agencies"] == {}
    assert frame["agency_evidence"] == {}
    rationale, basis = score_relevance(_dod_event_row(), frame)
    assert rationale is None and basis == {}


def test_client_paper_mints_corridors_three_ways():
    """Recipient identity; a distinctive product hit; the vendor named in
    the record's own text. All three are imported owners, not copies."""
    by_recipient = _award("OWN1", agency="Department of the Treasury",
                          sub="Internal Revenue Service",
                          recipient="apexanalytix")
    by_distinctive_hit = _award(
        "HIT1", agency="Department of Veterans Affairs",
        recipient="CARAHSOFT TECHNOLOGY CORP", hits=("SmartVM",),
        description="SmartVM subscription renewal for supplier validation.")
    by_vendor_text = _award(
        "TXT1", agency="General Services Administration",
        recipient="RESELLER LLC",
        description="apexanalytix recovery audit services, option year two.")
    frame = pack_frame(_pack([by_recipient, by_distinctive_hit,
                              by_vendor_text]))
    assert "department of the treasury" in frame["agencies"]
    assert "department of veterans affairs" in frame["agencies"]
    assert "general services administration" in frame["agencies"]


def test_the_boundary_is_the_approved_list_never_a_record_code():
    """pack_frame's NAICS set is the operator-approved inferred list ONLY,
    aligning with industry_days.naics_boundary: a stranger record's 541511
    must not admit a 541511 event into the boundary."""
    own = _award("OWN1", agency="Department of the Treasury",
                 recipient="apexanalytix", naics="541511")
    frame = pack_frame(_pack([own], naics=["541219"]))
    assert frame["naics"] == {"541219"}
    # corridor exists (client's own paper) but the event NAICS is outside
    # the approved boundary: the corridor tier must refuse
    row = _dod_event_row(naics="541511")
    row["Department/Ind.Agency"] = "TREASURY, DEPARTMENT OF THE"
    rationale, basis = score_relevance(row, frame)
    assert rationale is None and basis == {}


def test_a_competitor_side_award_never_mints_a_client_corridor():
    rival = _award("RIV1", agency="Department of the Navy",
                   recipient="RIVAL CORP", hits=("Quorum",),
                   description="Quorum legislative tracking licences.")
    pack = _pack([rival], client="FiscalNote",
                 entities={"product": ["FiscalNote Platform"],
                           "competitor": ["Quorum"]})
    frame = pack_frame(pack)
    assert frame["agencies"] == {}


def test_zero_relevant_events_render_the_screening_receipt_not_filler():
    """The band renders the stated zero with its screen; grammar requires
    the band when a screen exists; and the receipt states real counts."""
    import agents.golden_press.validate as validate_mod
    from agents.golden_press.render import render_content_region

    junk = _award("W519TC26F0012", agency="Department of Defense",
                  sub="Department of the Army", recipient="PALANTIR USG INC",
                  hits=("Platform",),
                  description="Data platform integration services.")
    pack = _pack([junk])
    rows = [_dod_event_row()]
    events, stats = harvest_tier_a(rows, pack, today=TODAY)
    assert events == []                     # the DoD filler class is dead
    pack.events = []
    pack.events_screen = {"tier": "A", "source": "sam_extract",
                          "metered_quota_spent": 0, **stats}
    content = render_content_region(pack, prose={})
    assert validate_mod.check_band_grammar(content, pack) == []
    band = re.search(r'<section class="band" id="events".*?</section>',
                     content, re.S).group(0)
    assert "SCREENING RECEIPT · ZERO RELEVANT EVENTS" in band
    assert "notices screened" in band
    assert "metered SAM quota spent: 0" in band
    assert "Army Intelligence" not in band  # no filler, stated zero only


def test_a_pack_with_neither_events_nor_screen_states_the_zero():
    """CONTRACT §1: the events band is always present; a pack with no
    events and no screen states its zero with counts unavailable."""
    import agents.golden_press.validate as validate_mod
    from agents.golden_press.render import render_content_region

    pack = _pack([_award("OWN1", agency="Department of the Treasury",
                         recipient="apexanalytix")])
    assert pack.events_screen is None
    content = render_content_region(pack, prose={})
    assert validate_mod.check_band_grammar(content, pack) == []
    band = re.search(r'<section class="band" id="events".*?</section>',
                     content, re.S).group(0)
    assert "screening counts unavailable" in band
