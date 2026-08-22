"""Deterministic account-decision rules (decision_rules.py, 2026-08-03).

Offline; fixture packs; no network, no LLM, no store file (R2's store leg
takes plain dicts). Each rule is pinned on the behavior the operator spec
names: R1's undated-record exclusion receipt and ranking, R2's verbatim
sentence receipt and shared-guard imports, R3's versioned constant that
never asserts successor-vehicle status, R4's qualify-only label, and the
prefix-only vehicle table.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.decision_rules import (  # noqa: E402
    R1_MATERIALITY,
    R2_WINDOW,
    RULE_CONSTANT_DATES,
    SEWP_V_FACTS,
    annotate_vehicles,
    build_decisions,
    classify_vehicle,
    parse_award_url,
    r1_decisions,
    r2_displacement,
    r3_vehicle_context,
    r4_adjacency,
)
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402

TODAY = date(2026, 8, 1)  # fixture family clock; rules take explicit today


def _award(record_id, *, agency="Department of Defense",
           sub="Defense Information Systems Agency", recipient="FCN, INC.",
           hits=("AcmeFlow",), period_end="2026-12-31", dollars=1_000_000.0,
           url=None, description=None):
    return GoldenRecord(
        record_id=record_id, lane="L2_entity_award", title=f"Enterprise agreement {record_id.lower()}",
        agency=agency, sub_agency=sub, recipient=recipient,
        entity_hits=list(hits),
        period_end=period_end, obligated_dollars=dollars,
        url=url or ("https://www.usaspending.gov/award/CONT_AWD_"
                    f"{record_id}_9700_-NONE-_-NONE-"),
        description=description)


def _pack(records, *, entities=None, client="Acme Networks"):
    return EvidencePack(
        client_name=client,
        generated_at="2026-08-01T12:00:00Z",
        records=records,
        research={"entities": entities or {
            "product": ["AcmeFlow"],
            "competitor": ["RivalSuite Enterprise", "Chef"],
        }},
    )


# --------------------------------------------------------------------------- #
# TASK 1 · vehicle derivation
# --------------------------------------------------------------------------- #
def test_award_url_parse_and_prefix_classification_table():
    parsed = parse_award_url(
        "https://www.usaspending.gov/award/"
        "CONT_AWD_75D30122F15723_7523_NNG15SD31B_8000")
    assert parsed == {"piid": "75D30122F15723", "agency": "7523",
                      "parent": "NNG15SD31B", "parent_agency": "8000"}
    assert parse_award_url("https://www.usaspending.gov/award/"
                           "CONT_AWD_X_1_-NONE-_-NONE-")["parent"] is None
    assert parse_award_url("https://example.gov/nothing") is None
    # the classification is PREFIX-ONLY, no lookups (operator spec)
    assert classify_vehicle("NNG15SD31B", "8000") == "SEWP V"
    assert classify_vehicle("NNG15SD31B", "4700") == "IDV (unclassified)"
    assert classify_vehicle("GS35F001GA", None) == "GSA"
    assert classify_vehicle("47QTCA20D000", None) == "GSA"
    assert classify_vehicle("HHSN316201200002W", "7529") == "HHS"
    assert classify_vehicle("HSHQDC16D00001", None) == "DHS"
    assert classify_vehicle(None, None) == "definitive"
    assert classify_vehicle("W52P1J20D0000", None) == "IDV (unclassified)"


def test_annotate_vehicles_fills_fields_and_is_idempotent():
    record = _award(
        "75D30122F15723",
        url="https://www.usaspending.gov/award/"
            "CONT_AWD_75D30122F15723_7523_NNG15SD31B_8000")
    record.parent_award_id = None
    annotate_vehicles([record])
    assert record.parent_award_id == "NNG15SD31B"
    assert record.parent_award_agency == "8000"
    assert record.vehicle_class == "SEWP V"
    annotate_vehicles([record])  # idempotent
    assert record.vehicle_class == "SEWP V"


# --------------------------------------------------------------------------- #
# R1 · head-to-head decisions
# --------------------------------------------------------------------------- #
def test_r1_requires_both_sides_current_and_ranks_by_nearest_end():
    records = [
        _award("CLIENT1", period_end="2026-10-31"),
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-09-15"),
        # second buyer: client side only, never a card
        _award("CLIENT2", agency="Department of Justice",
               sub="Offices, Boards and Divisions",
               period_end="2027-01-31"),
        # third buyer: nearer clock, card must outrank DISA's
        _award("CLIENT3", agency="Department of the Treasury",
               sub="Internal Revenue Service", period_end="2026-08-31"),
        _award("RIVAL3", agency="Department of the Treasury",
               sub="Internal Revenue Service",
               hits=("RivalSuite Enterprise",), period_end="2026-08-15"),
    ]
    out = r1_decisions(_pack(records), today=TODAY)
    cards = out["cards"]
    # D1: cards key on the awarding SUBTIER buying agency, never toptier
    assert [c["agency"] for c in cards] == [
        "Internal Revenue Service", "Defense Information Systems Agency"]
    assert cards[0]["nearest_end"] == "2026-08-15"
    assert cards[0]["nearest_record_id"] == "RIVAL3"
    assert cards[1]["rival_entities"] == ["RivalSuite Enterprise"]
    # every output carries its evidence rows
    assert cards[0]["client_records"][0]["record_id"] == "CLIENT3"
    assert cards[0]["rival_records"][0]["record_id"] == "RIVAL3"


def test_r1_excludes_undated_records_into_the_receipt_never_evaluates_them():
    records = [
        _award("CLIENT1", period_end="2026-10-31"),
        # the rival's only record has NO period end: excluded, receipted,
        # and the agency therefore has no rival side and no card
        _award("RIVALX", hits=("RivalSuite Enterprise",), period_end=None),
    ]
    out = r1_decisions(_pack(records), today=TODAY)
    assert out["cards"] == []
    assert out["receipt"]["undated_not_evaluated"] == ["RIVALX"]
    assert "period_end >= 2026-08-01" in out["receipt"]["current_basis"]


def test_r1_expired_paper_is_not_current():
    records = [
        _award("CLIENT1", period_end="2026-07-31"),  # ended before today
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-09-15"),
    ]
    out = r1_decisions(_pack(records), today=TODAY)
    assert out["cards"] == []
    assert out["receipt"]["undated_not_evaluated"] == []


def test_r1_client_recipient_identity_counts_as_client_paper():
    records = [
        # no client entity hit, but the recipient IS the client: client paper
        _award("CLIENTOWN", recipient="Acme Networks LLC", hits=()),
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-09-15"),
    ]
    out = r1_decisions(_pack(records), today=TODAY)
    assert len(out["cards"]) == 1
    assert out["cards"][0]["client_records"][0]["record_id"] == "CLIENTOWN"


# --------------------------------------------------------------------------- #
# R2 · displacement with verbatim sentence receipts
# --------------------------------------------------------------------------- #
def test_r2_store_notice_sole_source_yields_verbatim_sentence_receipt():
    pack = _pack([_award("CLIENT1", agency="Department of the Navy")])
    store_rows = [{
        "notice_id": "abc123def456", "title": "Enterprise licence intent",
        "notice_type": "Special Notice", "agency": "Department of the Navy",
        "subtier": "", "office": "", "posted": "2026-07-20", "deadline": "",
        "url": "https://sam.gov/workspace/contract/opp/abc123def456/view",
        "description_prefix": (
            "The Navy announces its intent to sole-source this requirement. "
            "The award covers RivalSuite Enterprise subscriptions for one "
            "year. Responses are due promptly."),
    }]
    out = r2_displacement(pack, store_rows)
    assert len(out["alerts"]) == 1
    alert = out["alerts"][0]
    assert alert["competitor"] == "RivalSuite Enterprise"
    assert alert["sentence"] == ("The award covers RivalSuite Enterprise "
                                 "subscriptions for one year.")
    assert alert["context"] == "sole_source_intent"
    assert alert["source"]["kind"] == "store_notice"
    assert [r["record_id"] for r in alert["client_history_records"]] \
        == ["CLIENT1"]
    assert out["receipt"]["store_notices_scanned"] == 1


def test_r2_outside_client_history_agency_never_alerts():
    pack = _pack([_award("CLIENT1", agency="Department of the Navy")])
    store_rows = [{
        "notice_id": "n1", "title": "Sole source to RivalSuite Enterprise",
        "notice_type": "Special Notice",
        "agency": "Department of the Interior",  # no client history here
        "subtier": "", "office": "", "posted": "", "deadline": "", "url": "",
        "description_prefix": "Intent to sole-source RivalSuite Enterprise.",
    }]
    out = r2_displacement(pack, store_rows)
    assert out["alerts"] == []


def test_r2_imports_the_shared_ambiguity_guard_not_a_copy():
    """'Chef' is a dictionary word: without the vendor named in the text the
    shared sam_lanes guard rejects it, and the rejection is receipted."""
    pack = _pack([_award("CLIENT1", agency="Department of the Navy")])
    store_rows = [{
        "notice_id": "n2", "title": "Kitchen services",
        "notice_type": "Special Notice", "agency": "Department of the Navy",
        "subtier": "", "office": "", "posted": "", "deadline": "", "url": "",
        "description_prefix": "Intent to sole-source. A Chef will cook.",
    }]
    out = r2_displacement(pack, store_rows)
    assert out["alerts"] == []
    assert out["receipt"]["guard_rejections"] == {
        "ambiguous_name_without_vendor": 1}


def test_r2_pack_award_naming_rival_in_corridor_alerts_with_sentence():
    records = [
        _award("CLIENT1", agency="Department of the Navy"),
        _award("AWD77", agency="Department of the Navy",
               recipient="SomePrime", hits=("RivalSuite Enterprise",),
               description=("Renewal of RivalSuite Enterprise licences for "
                            "the fleet.")),
    ]
    out = r2_displacement(_pack(records), None)
    assert len(out["alerts"]) == 1
    assert out["alerts"][0]["source"] == {
        "kind": "pack_record", "record_id": "AWD77",
        "lane": "L2_entity_award",
        "url": records[1].url, "title": "Enterprise agreement awd77"}
    assert "RivalSuite Enterprise" in out["alerts"][0]["sentence"]
    assert out["receipt"]["store_unavailable"] is True


# --------------------------------------------------------------------------- #
# R3 · vehicle context
# --------------------------------------------------------------------------- #
def test_r3_fires_only_for_the_known_expiry_vehicle_and_never_asserts_sewp_vi():
    sewp = _award("SEWPCLOCK",
                  url="https://www.usaspending.gov/award/"
                      "CONT_AWD_SEWPCLOCK_9700_NNG15SC79B_8000")
    gsa = _award("GSACLOCK",
                 url="https://www.usaspending.gov/award/"
                     "CONT_AWD_GSACLOCK_9700_GS35F001GA_4732")
    annotate_vehicles([sewp, gsa])
    context = r3_vehicle_context(sewp)
    assert context["facts"]["ordering_ends"] == "2026-09-30"
    assert context["facts"]["citation"] == "sewp.nasa.gov/contract_info.shtml"
    assert r3_vehicle_context(gsa) is None  # GSA has no dated constant
    # the constant carries vehicle dates only; no per-prime successor claim
    assert "holder" not in " ".join(SEWP_V_FACTS).lower()
    assert set(RULE_CONSTANT_DATES) == {
        "2026-09-30", "2027-01-31", "2027-04-30", "2026-11-01"}


def test_r3_contexts_bind_only_to_rule_cited_records():
    cited = _award("CLIENT1",
                   url="https://www.usaspending.gov/award/"
                       "CONT_AWD_CLIENT1_9700_NNG15SC79B_8000")
    rival = _award("RIVAL1", hits=("RivalSuite Enterprise",),
                   period_end="2026-09-15")
    uncited = _award("LONER", agency="Department of Justice",
                     sub="Offices, Boards and Divisions",
                     url="https://www.usaspending.gov/award/"
                         "CONT_AWD_LONER_9700_NNG15SD27B_8000")
    decisions = build_decisions(_pack([cited, rival, uncited]),
                                today=TODAY)
    assert "CLIENT1" in decisions["r3"]["contexts"]
    assert "LONER" not in decisions["r3"]["contexts"]


# --------------------------------------------------------------------------- #
# R4 · adjacency, qualify only
# --------------------------------------------------------------------------- #
def test_r4_joins_forecasts_to_client_history_departments_qualify_only():
    records = [
        _award("CLIENT1", agency="Department of Homeland Security",
               period_end="2020-01-31"),   # historical paper still joins
        GoldenRecord(record_id="F2026001", lane="L4_forecast",
                     title="Zero trust support", agency="DHS",
                     estimated_value_range="$1M to $5M",
                     url="https://apfs-cloud.dhs.gov/forecast/1"),
        GoldenRecord(record_id="F2026002", lane="L4_forecast",
                     title="Unrelated agency line", agency="NRC",
                     url="https://example.gov/f2"),
    ]
    out = r4_adjacency(_pack(records))
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["label"] == "qualify"
    assert row["department"] == "DHS"
    assert row["forecast"]["record_id"] == "F2026001"
    assert [r["record_id"] for r in row["client_history_records"]] \
        == ["CLIENT1"]
    # a forecast value is never summed anywhere in the rule output
    assert "dollars" not in row and "total" not in row


# --------------------------------------------------------------------------- #
# assembly + determinism
# --------------------------------------------------------------------------- #
def test_build_decisions_defaults_today_to_the_pack_date_never_wall_clock():
    records = [
        _award("CLIENT1", period_end="2026-08-15"),
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-08-10"),
    ]
    decisions = build_decisions(_pack(records))
    # pack generated_at is 2026-08-01: both sides current ON THE PACK DATE,
    # regardless of when this test runs (frozen-clock doctrine by design)
    assert decisions["today"] == "2026-08-01"
    assert len(decisions["r1"]["cards"]) == 1
    again = build_decisions(_pack(records))
    assert again == decisions  # deterministic for the same inputs


# --------------------------------------------------------------------------- #
# TASK 3 · the decision band renders deterministically, above candidate-review
# --------------------------------------------------------------------------- #
def _render(pack):
    from agents.golden_press.render import render_content_region
    return render_content_region(pack, prose={})


def test_decision_band_sits_above_candidate_review_and_passes_grammar():
    import re

    import agents.golden_press.validate as validate_mod
    pack = _pack([
        _award("CLIENT1", period_end="2026-08-15"),
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-08-10"),
    ])
    pack.decisions = build_decisions(pack, today=TODAY)
    content = _render(pack)
    bands = re.findall(r'<section class="band" id="([a-z-]+)"', content)
    # CONTRACT (2026-08-03): decisions is Band 01, the first sb-band, ahead
    # of the forward lane; the old forecast/candidate-review grammar is
    # retired by REPORT_CONTRACT.md §1.
    assert bands.index("decisions") == 0
    assert bands.index("decisions") == bands.index("forward") - 1
    assert validate_mod.check_band_grammar(content, pack) == []
    # every rule-cited id in the band is anchored to its canonical pack URL
    assert validate_mod.check_links(content, pack) == []
    band = re.search(r'<section class="band" id="decisions".*?</section>',
                     content, re.S).group(0)
    assert "NEAREST CLOCK" in band and "RIVAL PAPER" in band


def test_decision_band_states_zero_never_pads():
    """The zero is always STATED, never padded. Which zero it may state is
    a function of what was actually tested (gold-standard brief 2026-08-04):
    this pack names rivals but carries no rival query, so the band says
    PENDING. Asserting a tested 'NONE' here was the defect that let the
    Thinklogical press claim a competitive test that never ran."""
    import re
    pack = _pack([_award("CLIENT1")])          # client paper only, no rival
    pack.decisions = build_decisions(pack, today=TODAY)
    band = re.search(r'<section class="band" id="decisions".*?</section>',
                     _render(pack), re.S).group(0)
    assert "COMPETITIVE VALIDATION PENDING" in band
    assert "HEAD-TO-HEAD · NONE" not in band
    assert "RivalSuite Enterprise" in band     # the comparison set is named
    assert "DISPLACEMENT · NONE" in band


def test_decision_band_states_the_tested_zero_once_the_lane_has_run():
    """Same evidence, but the pack now proves the rival lane searched both
    supplied names: the band may state the tested zero, with its receipt."""
    import re
    pack = _pack([_award("CLIENT1")])
    pack.decisions = build_decisions(pack, today=TODAY)
    pack.queries = [{
        "lane": "L2_entity_award", "method": "entity:competitor",
        "endpoint": "https://api.usaspending.gov/api/v2/search/spending_by_award/",
        "body": {"filters": {"keywords": ["RivalSuite Enterprise", "Chef"]}},
        "executed_at": "2026-08-01T00:00:00Z",
        "result_count": 200, "kept_after_screen": 0,
    }]
    band = re.search(r'<section class="band" id="decisions".*?</section>',
                     _render(pack), re.S).group(0)
    assert "HEAD-TO-HEAD · NONE FOUND" in band
    assert "Rivals screened" in band
    assert "200" in band                       # the screened denominator
    assert "COMPETITIVE VALIDATION" not in band
    # the qualify zero moved to Band 02 (forward) with the contract
    forward = re.search(r'<section class="band" id="forward".*?</section>',
                        _render(pack), re.S).group(0)
    assert "FORWARD · NONE" in forward


def test_decision_band_on_a_pre_rules_pack_states_not_computed():
    import re
    pack = _pack([_award("CLIENT1")])
    assert pack.decisions is None              # legacy pack shape
    band = re.search(r'<section class="band" id="decisions".*?</section>',
                     _render(pack), re.S).group(0)
    assert "were not computed" in band
    assert "NONE" not in band                  # not-computed is not a zero


def test_decision_band_undated_receipt_renders():
    import re
    pack = _pack([
        _award("CLIENT1", period_end="2026-08-15"),
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-08-10"),
        _award("NODATE", period_end=None),
    ])
    pack.decisions = build_decisions(pack, today=TODAY)
    band = re.search(r'<section class="band" id="decisions".*?</section>',
                     _render(pack), re.S).group(0)
    assert "RECEIPT · UNDATED" in band and "NODATE" in band


def test_decision_band_sewp_note_renders_once_with_citation():
    import re
    pack = _pack([
        _award("CLIENT1", period_end="2026-08-15",
               url="https://www.usaspending.gov/award/"
                   "CONT_AWD_CLIENT1_9700_NNG15SC79B_8000"),
        _award("RIVAL1", hits=("RivalSuite Enterprise",),
               period_end="2026-08-10"),
    ])
    pack.decisions = build_decisions(pack, today=TODAY)
    content = _render(pack)
    assert content.count("VEHICLE NOTE · SEWP V") == 1
    assert "sewp.nasa.gov/contract_info.shtml" in content
    assert "not asserted" in content           # no SEWP VI holder claim
    import agents.golden_press.validate as validate_mod
    # the constant's dates are provenanced through the versioned constant
    assert validate_mod.check_dates(content, pack) == []


# --------------------------------------------------------------------------- #
# TASK 4 · the zero-SAM press flag
# --------------------------------------------------------------------------- #
def test_press_live_sam_flag_defaults_off(monkeypatch):
    from tools.api.sam_quota import press_live_sam_enabled
    monkeypatch.delenv("LILA_PRESS_LIVE_SAM", raising=False)
    assert press_live_sam_enabled() is False
    monkeypatch.setenv("LILA_PRESS_LIVE_SAM", "1")
    assert press_live_sam_enabled() is True


def test_live_runtime_never_builds_a_sam_source_with_the_flag_off(monkeypatch):
    """Flag off: the watch's SAM lane runs the quota-free extract screen
    because no SamGovSource is ever constructed; a configured key changes
    nothing. This is the single gate for the 28-calls class."""
    from datetime import datetime, timezone

    from agents.candidate_review_v1.live_providers import (
        load_live_provider_runtime,
    )

    monkeypatch.delenv("LILA_PRESS_LIVE_SAM", raising=False)
    monkeypatch.setenv("SAM_GOV_API_KEY", "test-key-never-spent")

    class _Binding:
        client_id = "c1"
        client_name = "Acme Networks"
        run_id = "r1"
        scope_sha256 = "s1"

    runtime = load_live_provider_runtime(
        binding=_Binding(), as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
        award_fetch=lambda url: {}, quota_guard=lambda p: True,
        sweep_loader=lambda: ((), None))
    assert runtime.vehicle_searcher._sam is None


def test_contract_number_check_admits_pack_carried_field_tokens():
    """A VA office string like '248-NETWORK CONTRACT OFFICE 8' is the
    record's own text, not a minted id (store-lane first report,
    2026-08-03). An id the pack does not carry anywhere still violates."""
    import agents.golden_press.validate as validate_mod
    pack = _pack([_award("CLIENT1")])
    pack.records[0].office = "248-NETWORK CONTRACT OFFICE 8 (36C248)"
    ok = "<p>248-NETWORK CONTRACT OFFICE 8</p>"
    assert validate_mod.check_contract_numbers(ok, pack) == []
    minted = "<p>Contract W91QUZ-99-D-0033 covers this.</p>"
    assert len(validate_mod.check_contract_numbers(minted, pack)) == 1


# --------------------------------------------------------------------------- #
# DEFECT ROUND (operator filing, 2026-08-03) · one fixture per defect class
# --------------------------------------------------------------------------- #
def test_d1_r1_subtier_grouping_kills_the_toptier_manufactured_card():
    """D1: client paper at one DoD component and rival paper at another is
    NOT a head-to-head; the same subtier is. A record with no subtier is
    receipted, never guessed onto a buying account."""
    navy_client = _award("NAVYCLIENT", sub="Department of the Navy")
    army_rival = _award("ARMYRIVAL", sub="Department of the Army",
                        hits=("RivalSuite Enterprise",))
    out = r1_decisions(_pack([navy_client, army_rival]), today=TODAY)
    assert out["cards"] == []            # the toptier DoD join is dead

    same_sub_rival = _award("NAVYRIVAL", sub="Department of the Navy",
                            hits=("RivalSuite Enterprise",))
    out = r1_decisions(_pack([navy_client, same_sub_rival]), today=TODAY)
    assert [c["agency"] for c in out["cards"]] == ["Department of the Navy"]

    no_sub = _award("NOSUB", sub=None)
    out = r1_decisions(_pack([no_sub, same_sub_rival]), today=TODAY)
    assert out["cards"] == []
    assert out["receipt"]["no_subtier_not_evaluated"] == ["NOSUB"]
    assert "awarding subtier" in out["receipt"]["current_basis"]


def test_d5_ratio_only_tiering_with_printed_scale():
    """D5 (2026-08-03): MAIN iff rival/client >= RATIO_FLOOR (0.05). The
    absolute dollar floor is REMOVED from tiering; scale prints on every
    card instead. Spec fixtures: OPO 82.5% MAIN; GAO 32.6% MAIN; State
    2.4% minor; SSA 0.65% minor. Not-computable ratios tier MAIN with the
    note, never silently demoted (G6 counts unvalued records)."""
    import re

    assert R1_MATERIALITY["ratio_floor"] == 0.05
    assert "floor_dollars" not in " ".join(R1_MATERIALITY)

    def _card(client_dollars, rival_dollars):
        records = [
            _award("C1", dollars=client_dollars),
            _award("R1X", hits=("RivalSuite Enterprise",),
                   dollars=rival_dollars),
        ]
        return r1_decisions(_pack(records), today=TODAY)["cards"][0]

    opo = _card(160_769.0, 132_591.0)          # 82.5%
    assert opo["materiality"]["tier"] == "main"
    gao = _card(1_273_575.0, 414_763.0)        # 32.6%
    assert gao["materiality"]["tier"] == "main"
    state = _card(516_655.0, 12_600.0)         # 2.4%
    assert state["materiality"]["tier"] == "minor_rival_presence"
    ssa = _card(10_880_734.0, 71_648.0)        # 0.65%
    assert ssa["materiality"]["tier"] == "minor_rival_presence"

    # G6 + not-computable: a fully unvalued rival side tiers MAIN with the
    # note; unvalued counts ride the materiality object for the card print
    unvalued = _card(1_000_000.0, None)
    m = unvalued["materiality"]
    assert m["tier"] == "main"
    assert m["ratio_not_computable"] is True
    assert m["rival_unvalued_records"] == 1

    # the renderer prints the scale and the unvalued disclosure
    from agents.golden_press.decision_rules import build_decisions
    records = [_award("C9", dollars=1_000_000.0),
               _award("R9X", hits=("RivalSuite Enterprise",), dollars=None)]
    pack = _pack(records)
    pack.decisions = build_decisions(pack, today=TODAY)
    content = _render(pack)
    assert "ratio not computable · 1 unvalued record" in content


def test_d3_r2_window_excludes_older_matches_into_the_receipt():
    """D3: an alert needs a period end on/after the press date OR action
    inside the trailing 24 months. Older matches are counted in the receipt
    with the oldest fiscal year named, never rendered as alerts."""
    import re

    pack = _pack([_award("CLIENT1", agency="Department of the Navy")])
    old_row = {
        "notice_id": "old19", "title": "Sole source award",
        "notice_type": "Special Notice", "agency": "Department of the Navy",
        "subtier": "", "office": "", "posted": "2019-03-05", "deadline": "",
        "url": "", "description_prefix":
            "Intent to sole-source RivalSuite Enterprise support.",
    }
    new_row = dict(old_row, notice_id="new26", posted="2026-07-20")
    out = r2_displacement(pack, [old_row, new_row], today=TODAY)
    assert [a["source"]["notice_id"] for a in out["alerts"]] == ["new26"]
    assert out["receipt"]["window_excluded"] == 1
    assert out["receipt"]["window_excluded_oldest_fy"] == "FY19"
    assert out["receipt"]["window"] == R2_WINDOW

    # the pack-record leg is the same defect class: a long-expired award
    # with no recent action is receipted, not alerted
    history = _award("CLIENT1", agency="Department of the Navy")
    stale = _award("STALEAWD", agency="Department of the Navy",
                   recipient="SomePrime", hits=("RivalSuite Enterprise",),
                   period_end="2020-01-31",
                   description="Renewal of RivalSuite Enterprise licences.")
    out = r2_displacement(_pack([history, stale]), None, today=TODAY)
    assert out["alerts"] == []
    assert out["receipt"]["window_excluded"] == 1
    assert out["receipt"]["window_excluded_oldest_fy"] == "FY20"

    # the band renders the receipt sentence, never the stale alert
    pack2 = _pack([history, stale])
    pack2.decisions = build_decisions(pack2, today=TODAY)
    band = re.search(r'<section class="band" id="decisions".*?</section>',
                     _render(pack2), re.S).group(0)
    assert "older displacement record" in band
    assert "FY20" in band
    assert "DISPLACEMENT ALERT" not in band


def test_d4_receipt_sentence_dedupes_title_repeated_as_description_head():
    """D4: a store notice that repeats its title as the description's head
    yields ONE verbatim sentence, not a doubled one."""
    pack = _pack([_award("CLIENT1", agency="Department of the Navy")])
    row = {
        "notice_id": "dup1",
        "title": "Sole source intent for RivalSuite Enterprise renewal",
        "notice_type": "Special Notice", "agency": "Department of the Navy",
        "subtier": "", "office": "", "posted": "2026-07-20", "deadline": "",
        "url": "",
        "description_prefix": (
            "Sole source intent for RivalSuite Enterprise renewal. The "
            "agency intends to negotiate with a single source."),
    }
    out = r2_displacement(pack, [row], today=TODAY)
    assert len(out["alerts"]) == 1
    sentence = out["alerts"][0]["sentence"]
    assert sentence == ("Sole source intent for RivalSuite Enterprise "
                        "renewal.")
    assert sentence.count("RivalSuite") == 1
