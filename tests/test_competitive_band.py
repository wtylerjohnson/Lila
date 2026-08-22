"""Band 06, the account-by-account competitive picture (amendment v1.3,
2026-08-05).

The engine (agents/golden_press/competitive.py) runs before composition and
writes six sidecars; the band renders ONLY from the pack's own research
slice that mirrors them, in the contract's band grammar. These tests guard
the laws the port must keep:

  1. contested accounts render as decks whose every seat cites its record,
     linked canonical (linkage law inside the band)
  2. a composer WRONG DOMAIN attestation removes the rival SEAT here exactly
     as it removes the Band 01 displacement card, the motion re-derives
     through the engine's own table, and the evidence itself stays in the
     report's other tables (the R2 seat-filter behavior, consolidated
     2026-08-05)
  3. an untested lane never prints a tested zero: the zero-state branches on
     competitor_coverage (COMPLETED SCREEN receipt / COMPETITIVE VALIDATION
     PENDING / COMPETITIVE VALIDATION NEXT), and a pre-engine pack states
     not-computed, which is not a zero
  4. research mechanics stay in the sidecars: raw lane ids never render;
     a mailto: link never counts as a cited record (the gold reference HTML
     harvested mailto: links as "cited records"; it is a visual comparator,
     never a code source)
  5. the band holds a prose slot and obeys amendment v1.2: an empty band
     refuses model prose
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import competitive as comp_mod  # noqa: E402
from agents.golden_press import render as render_mod  # noqa: E402
from agents.golden_press.records import (  # noqa: E402
    EvidencePack,
    GoldenRecord,
    LaneQuery,
)
from agents.golden_press.validate import validate_press  # noqa: E402


def _record(**kw):
    base = dict(record_id="GS35F001", lane="L2_entity_award",
                title="Network performance monitoring support",
                description="Network performance monitoring support",
                agency="Department of the Treasury",
                sub_agency="Internal Revenue Service",
                recipient="Acme Networks", obligated_dollars=1_920_000.0,
                url="https://www.usaspending.gov/award/CONT_AWD_GS35F001",
                period_end="2026-07-31", entity_hits=["AcmeFlow"])
    base.update(kw)
    return GoldenRecord(**base)


def _pack(records=None, **kw):
    return EvidencePack(client_name="Acme Networks",
                        generated_at="2026-07-27T12:00:00Z",
                        records=records if records is not None else [_record()],
                        **kw)


_RIVAL_QUERY = LaneQuery(
    lane="L2_entity_award", method="entity:competitor",
    endpoint="https://api.usaspending.gov/api/v2/search/spending_by_award/",
    body={"filters": {"keywords": ["OtherMonitor"]}},
    result_count=12, kept_after_screen=3)


def _slice(pack, competitors, lanes=("L1_notice", "L2_entity_award")):
    """The render-facing research slice, its account picture built by the
    ENGINE's own account_picture so the shape can never drift from what a
    press stores (competitive_build writes the same payload)."""
    frame = {"competitors": [
        {"canonical": name, "aliases_distinctive": [], "aliases_ambiguous": [],
         "product_brands": [], "confidence": "high"} for name in competitors]}
    accounts = comp_mod.account_picture(
        pack, frame, today=date(2026, 7, 27), client_name=pack.client_name)
    return {
        "completeness": {
            "confirmed_competitors": sorted(competitors),
            "status": "complete_found" if competitors else "complete_zero"},
        "accounts": accounts,
        "frame_confirmed": sorted(competitors),
        "lanes_searched": list(lanes),
        "rounds_run": 2,
    }


def _band(content: str, band_id: str = "competitive") -> str:
    m = re.search(rf'<section class="band" id="{band_id}".*?</section>',
                  content, re.S)
    assert m, f"band {band_id!r} did not render"
    return m.group(0)


def _contested_pack():
    """Client, rival, and channel paper all current at one buyer."""
    records = [
        _record(),
        _record(record_id="RIV1", recipient="Rival Prime Inc",
                entity_hits=["OtherMonitor"], obligated_dollars=400_000.0,
                url="https://www.usaspending.gov/award/RIV1"),
        _record(record_id="CH1", recipient="Carahsoft Technology",
                entity_hits=["SomethingElse"], obligated_dollars=90_000.0,
                url="https://www.usaspending.gov/award/CH1"),
    ]
    pack = _pack(records, research={
        "entities": {"competitor": ["OtherMonitor"], "product": ["AcmeFlow"]}},
        queries=[_RIVAL_QUERY])
    pack.research["competitive"] = _slice(pack, ["OtherMonitor"])
    return pack


# ---- contested accounts render their cited seats ---------------------------- #
def test_contested_account_renders_decks_with_every_seat_cited():
    pack = _contested_pack()
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert '<article class="deck act">' in band
    assert "Internal Revenue Service".upper() in band
    assert "defend the installed position" in band.casefold()
    # every seat cites its record, anchored canonical (linkage law)
    assert "CLIENT PAPER" in band and "RIVAL PAPER" in band \
        and "CHANNEL HOLDS" in band
    assert 'href="https://www.usaspending.gov/award/RIV1"' in band
    assert 'href="https://www.usaspending.gov/award/CH1"' in band
    assert 'href="https://www.usaspending.gov/award/CONT_AWD_GS35F001"' in band
    # the whole region stays contract-conformant
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_band_renders_in_contract_position_between_agencies_and_events():
    # Contract §1 (v1.3): 05 agencies, 06 competitive, 07 events. Numbering
    # is the render position (band_numbering is validator-enforced; this
    # pins the position itself).
    content = render_mod.render_content_region(_contested_pack(), prose={})
    assert content.index('id="agencies"') < content.index('id="competitive"') \
        < content.index('id="events"')
    band = _band(content)
    assert '<div class="b-no">06</div>' in band


# ---- the R2 seat filter, ported (consolidated out of the retired renderer) -- #
def test_wrong_domain_attestation_removes_the_rival_seat_here_too():
    """ITEM 1 seat filter (2026-08-05): a rival record the composer attested
    WRONG DOMAIN loses its displacement card in Band 01 AND its rival seat
    in this band. The motion re-derives through the engine's own motion
    table over the surviving seats, and the record itself stays in the
    report's other tables: seats are removed silently, evidence never."""
    pack = _contested_pack()
    prose = {"why_RIV1":
             "WRONG DOMAIN: an aircraft data recorder award, not this market."}
    content = render_mod.render_content_region(pack, prose=prose)
    band = _band(content)
    # the rival seat is gone from this band
    assert "RIV1" not in band
    assert "RIVAL PAPER" not in band
    # the motion re-derived over the surviving seats (client + channel):
    # the engine table says maintain visibility, never the stale defend
    assert "defend the installed position" not in band.casefold()
    assert "maintain visibility" in band.casefold()
    # the channel seat survives with its citation
    assert 'href="https://www.usaspending.gov/award/CH1"' in band
    # the evidence itself is never removed from the report
    agencies = _band(content, "agencies")
    assert 'href="https://www.usaspending.gov/award/RIV1"' in agencies
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_without_the_attestation_the_rival_seat_stands():
    """The control for the seat filter: same pack, no attestation, and the
    rival seat renders with its defend motion."""
    pack = _contested_pack()
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "RIVAL PAPER" in band and "RIV1" in band
    assert "defend the installed position" in band.casefold()


# ---- an untested lane never prints a tested zero ---------------------------- #
def test_screened_zero_prints_the_completed_screen_receipt():
    """Rivals supplied AND the lane ran: the stated zero is the receipt of
    what was read (comparison set, lanes in client vocabulary, as-of date,
    accounts read), plus the commercial read when history or forward demand
    exists without current rival paper."""
    records = [
        _record(),
        _record(record_id="OLD1", recipient="Rival Prime Inc",
                entity_hits=["OtherMonitor"], obligated_dollars=250_000.0,
                period_end="2025-01-01",
                url="https://www.usaspending.gov/award/OLD1"),
    ]
    pack = _pack(records, research={
        "entities": {"competitor": ["OtherMonitor"], "product": ["AcmeFlow"]}},
        queries=[_RIVAL_QUERY])
    pack.research["competitive"] = _slice(pack, ["OtherMonitor"])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "COMPLETED SCREEN · SCOPE RECEIPT" in band
    assert "OtherMonitor" in band
    assert "27 JUL 2026" in band          # as-of, the pack's own date
    assert "COMMERCIAL READ" in band      # history exists; position shapeable
    # client lane vocabulary, never raw lane ids (sidecar machinery)
    assert "federal award records" in band
    assert "L2_entity_award" not in band and "L1_notice" not in band
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_supplied_but_unscreened_lane_states_validation_pending():
    """competitor_zero_untested doctrine: a named comparison set whose paper
    was never read may not produce a completed-screen receipt."""
    pack = _pack([_record()], research={
        "entities": {"competitor": ["OtherMonitor"], "product": ["AcmeFlow"]}})
    pack.research["competitive"] = _slice(pack, ["OtherMonitor"])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "COMPETITIVE VALIDATION PENDING" in band
    assert "OtherMonitor" in band         # the set is named, not asserted
    assert "COMPLETED SCREEN" not in band
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_no_supplied_rivals_states_validation_next_never_a_zero():
    pack = _pack([_record()], research={"entities": {"product": ["AcmeFlow"]}})
    pack.research["competitive"] = _slice(pack, [])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "COMPETITIVE VALIDATION NEXT" in band
    assert "COMPLETED SCREEN" not in band
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_pre_engine_pack_states_not_computed_which_is_not_a_zero():
    """A pack pressed before the competitive engine carries no research
    slice: the band states that the picture was not computed (the decision
    band's not-computed precedent), and the region stays conformant."""
    pack = _pack()
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "not computed" in band
    assert "COMPLETED SCREEN" not in band
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_required_field_probe_rejects_a_band_with_no_decks_and_no_posture():
    """Contract §2/06 enforcement: a competitive band that renders neither
    account decks nor a sanctioned posture fails required_field."""
    from agents.golden_press.validate import check_required_fields
    pack = _contested_pack()
    content = render_mod.render_content_region(pack, prose={})
    hollow = re.sub(
        r'(<section class="band" id="competitive").*?(</section>)',
        r'\1><div class="b-no">06</div>\2', content, flags=re.S)
    assert any(v["detail"].startswith("06 competitive")
               for v in check_required_fields(hollow, pack))
    assert not any(v["detail"].startswith("06 competitive")
                   for v in check_required_fields(content, pack))


# ---- sidecar mechanics stay out; mailto is never a citation ------------------ #
def test_a_mailto_link_never_counts_as_a_cited_record():
    """The gold reference HTML harvests mailto: links as "cited records"; it
    is a visual comparator only. A slice row carrying a mailto target (a
    poisoned or hand-edited artifact) renders as text, never as a record
    citation, and the band emits no mailto anchor at all."""
    pack = _contested_pack()
    slice_payload = pack.research["competitive"]
    for account in slice_payload["accounts"]["accounts"]:
        for row in account["competitor_current"]:
            row["url"] = "mailto:contracting.officer@irs.gov"
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "mailto:" not in band
    # the id may not render bare (linkage law): the row falls back to its
    # recipient text instead of an unanchored identifier
    assert "RIV1" not in band
    assert "Rival Prime Inc" in band
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


# ---- prose slot: registered, and an empty band refuses it (v1.2) ------------ #
def test_band_competitive_prose_slot_is_registered():
    keys = {slot["key"] for slot in render_mod.prose_slots(_pack())}
    assert "band_competitive" in keys


def test_empty_band_refuses_model_prose_and_contested_band_accepts_it():
    """AMENDMENT v1.2: a band that renders no rows takes its deterministic
    lede; model prose renders only over actual rows."""
    planted = ("Defend the installed base first and work the channel route "
               "second across these accounts.")
    zero_pack = _pack([_record()], research={
        "entities": {"competitor": ["OtherMonitor"], "product": ["AcmeFlow"]}})
    zero_pack.research["competitive"] = _slice(zero_pack, ["OtherMonitor"])
    zero_content = render_mod.render_content_region(
        zero_pack, prose={"band_competitive": planted})
    assert planted not in _band(zero_content)

    full = render_mod.render_content_region(
        _contested_pack(), prose={"band_competitive": planted})
    assert planted in _band(full)


# ---- the press seam: engine output renders as-is ---------------------------- #
def test_the_engines_own_output_renders_through_the_press_slice():
    """Gated code paths are untested code (handoff trap list): this drives
    the REAL engine (competitive_build) over a scripted lane builder, mounts
    its output on the pack exactly as press.golden_press does, and proves
    the render slice the press constructs is the slice the band consumes,
    end to end, validator-clean."""

    class _Entity:
        def __init__(self, kind, name):
            self.kind, self.name = kind, name

    class _Strategy:
        def __init__(self, client_name, entities=()):
            self.client_name = client_name
            self.research_entities = list(entities)
            self.keywords = []
            self.inferred_naics = []

        def model_copy(self, update):
            clone = _Strategy(self.client_name, self.research_entities)
            for key, value in update.items():
                setattr(clone, key, value)
            return clone

    def _build(strategy, **kwargs):
        entities: dict = {"product": ["AcmeFlow"]}
        for entity in strategy.research_entities:
            entities.setdefault(entity.kind, []).append(entity.name)
        rivals = entities.get("competitor") or []
        return EvidencePack(
            client_name=strategy.client_name,
            generated_at="2026-07-27T12:00:00Z",
            records=[
                _record(),
                _record(record_id="RIV1", recipient="Rival Prime Inc",
                        entity_hits=["OtherMonitor"],
                        obligated_dollars=400_000.0,
                        url="https://www.usaspending.gov/award/RIV1"),
            ],
            queries=[LaneQuery(
                lane="L2_entity_award", method="entity:competitor",
                endpoint="https://api.usaspending.gov/api/v2/search/"
                         "spending_by_award/",
                body={"filters": {"keywords": [name]}},
                result_count=12, kept_after_screen=3) for name in rivals],
            research={"entities": entities})

    import pathlib
    strategy = _Strategy("Acme Networks",
                         [_Entity("competitor", "OtherMonitor")])
    result = comp_mod.competitive_build(
        strategy, sweep=None, profile=None, scope=None,
        root=pathlib.Path(__file__).resolve().parents[1],
        today=date(2026, 7, 27), build_pack=_build)
    pack = result["pack"]
    # the exact slice press.golden_press mounts on the pack (press.py)
    pack.research["competitive"] = {
        "completeness": result["completeness"],
        "accounts": result["accounts"],
        "frame_confirmed": result["completeness"]["confirmed_competitors"],
        "lanes_searched": sorted({
            str(c.get("lane")) for c in result["search_ledger"]["cells"]
            if c.get("status") == "covered" and c.get("competitor") != "*"}),
        "rounds_run": result["search_ledger"]["rounds_run"],
    }
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert '<article class="deck act">' in band
    assert "defend the installed position" in band.casefold()
    assert 'href="https://www.usaspending.gov/award/RIV1"' in band
    assert "L2_entity_award" not in band     # client vocabulary only
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
