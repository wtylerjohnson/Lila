"""The deterministic targeting rules behind Band 09 (amendment v1.4,
2026-08-05).

Three rules turn decision rows into call specs: T1 renewal and T2
displacement off R1 cards and R2 alerts, T3 adjacency off R4 qualify rows.
Every one of them gets a POSITIVE and a NEGATIVE case here, deliberately:
this suite has a history of rules that only ever proved they can fire, and
a rule with no negative case is a rule nobody has shown will decline.

The laws under test:
  1. the persona ladder and the row-class rules live in DATA
     (data/reference/targeting_personas.json), are validated loudly, and a
     tier the ladder does not define cannot be sought
  2. row class is READ OFF THE EVIDENCE, never guessed: an R1 card whose
     nearest clock is client paper is a renewal, one whose nearest clock is
     rival paper is a displacement window
  3. the join is the admission ticket: a row with no pack record to hang on
     produces no spec and is receipted, never silently dropped
  4. adjacency targets the vehicle and reseller side, so a row whose only
     recipient is the client itself has no route to work and produces
     nothing
  5. every rule prints its evidence the way R1 to R4 do, and the receipt
     discloses a defined tier no rule sought
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import targeting_rules as tr  # noqa: E402
from agents.golden_press.decision_rules import build_decisions  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402


def _award(**kw):
    base = dict(record_id="GS35F001", lane="L2_entity_award",
                title="Network performance monitoring support",
                description="Network performance monitoring support",
                agency="Department of the Treasury",
                sub_agency="Internal Revenue Service",
                recipient="Acme Networks", obligated_dollars=1_920_000.0,
                url="https://www.usaspending.gov/award/CONT_AWD_GS35F001",
                period_end="2026-08-31", entity_hits=["AcmeFlow"])
    base.update(kw)
    return GoldenRecord(**base)


def _pack(records, **kw):
    kw.setdefault("research", {"entities": {"competitor": ["OtherMonitor"],
                                            "product": ["AcmeFlow"]}})
    return EvidencePack(client_name="Acme Networks",
                        generated_at="2026-07-27T12:00:00Z",
                        records=records, **kw)


def _rival(**kw):
    kw.setdefault("record_id", "RIV1")
    kw.setdefault("recipient", "Rival Prime Inc")
    kw.setdefault("entity_hits", ["OtherMonitor"])
    kw.setdefault("obligated_dollars", 900_000.0)
    kw.setdefault("url", "https://www.usaspending.gov/award/CONT_AWD_RIV1")
    return _award(**kw)


def _built(pack, **kw):
    pack.decisions = build_decisions(pack)
    return tr.build_targeting(pack, **kw)


def _by_rule(out, rule_id):
    return [s for s in out["specs"] if s["rule_id"] == rule_id]


# ---- the ladder is data, and it is validated loudly ------------------------ #
def test_the_shipped_ladder_defines_four_tiers_and_only_known_targets():
    personas = tr.load_personas()
    assert [row["tier"] for row in personas["tiers"]] == [1, 2, 3, 4]
    assert personas["version"].startswith("targeting_personas.")
    for row in personas["tiers"]:
        assert row["titles"], f"tier {row['tier']} searches for nobody"
        assert row["target"] in ("buying_component", "paper_holder")
    for rule in personas["rules"]:
        assert rule["row_class"] in tr.ROW_CLASSES
        assert rule["seeks_tiers"]


@pytest.mark.parametrize("mutation,expected", [
    ({"tiers": []}, "no tier ladder"),
    ({"rules": []}, "no rules"),
])
def test_a_malformed_ladder_fails_loud_rather_than_targeting_nobody(
        tmp_path, monkeypatch, mutation, expected):
    payload = dict(tr.load_personas())
    payload.update(mutation)
    path = tmp_path / "personas.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(tr.PERSONAS_PATH_ENV, str(path))
    tr._cache.clear()
    with pytest.raises(tr.TargetingError, match=expected):
        tr.load_personas()
    tr._cache.clear()


def test_a_rule_may_not_seek_a_tier_the_ladder_does_not_define(
        tmp_path, monkeypatch):
    """The negative case for the ladder itself: a rule pointing at a rung
    that does not exist would search for a persona with no titles."""
    payload = json.loads(json.dumps(tr.load_personas()))
    payload["rules"][0]["seeks_tiers"] = [9]
    path = tmp_path / "personas.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(tr.PERSONAS_PATH_ENV, str(path))
    tr._cache.clear()
    with pytest.raises(tr.TargetingError, match="does not define"):
        tr.load_personas()
    tr._cache.clear()


# ---- T1 renewal ------------------------------------------------------------ #
def test_t1_fires_when_the_clients_own_clock_closes_first():
    """POSITIVE: client paper closes 31 AUG, rival paper 30 NOV. The nearest
    clock is the client's, so this is a renewal to defend."""
    out = _built(_pack([_award(period_end="2026-08-31"),
                        _rival(period_end="2026-11-30")]))
    specs = _by_rule(out, "T1")
    assert len(specs) == 1
    spec = specs[0]
    assert spec["row_class"] == "renewal"
    assert spec["target_org"] == {"kind": "buying_component",
                                  "name": "Internal Revenue Service"}
    # the operator's rule: renewal rows want program office plus IT procurement
    assert spec["seeks_tiers"] == [1, 2]
    assert "Contracting Officer" in spec["person_titles"]
    # the rule prints its evidence the way R1 to R4 do
    assert spec["evidence"]["nearest_side"] == "client"
    assert spec["evidence"]["nearest_record_id"] == "GS35F001"
    assert "renewal is decided here" in spec["rationale"]


def test_t1_declines_when_the_rival_clock_closes_first():
    """NEGATIVE for T1: same buyer, same records, rival clock nearest. No
    renewal spec exists; the card becomes a displacement window instead."""
    out = _built(_pack([_award(period_end="2026-11-30"),
                        _rival(period_end="2026-08-31")]))
    assert _by_rule(out, "T1") == []
    assert len(_by_rule(out, "T2")) == 1


# ---- T2 displacement, off an R1 card --------------------------------------- #
def test_t2_fires_when_the_rival_clock_closes_first():
    """POSITIVE: the displacement window is the rival's own expiry."""
    out = _built(_pack([_award(period_end="2026-11-30"),
                        _rival(period_end="2026-08-31")]))
    spec = _by_rule(out, "T2")[0]
    assert spec["row_class"] == "displacement"
    assert spec["evidence"]["nearest_side"] == "rival"
    assert spec["seeks_tiers"] == [1, 2]
    assert "displacement window" in spec["rationale"]
    # both sides of the card are joinable evidence
    assert set(spec["join_record_ids"]) == {"GS35F001", "RIV1"}


def test_t2_declines_when_no_rival_holds_current_paper():
    """NEGATIVE for T2: client paper alone makes no head-to-head card at
    all, so no displacement spec can exist."""
    out = _built(_pack([_award()]))
    assert _by_rule(out, "T2") == []
    assert out["receipt"]["rows_read"]["r1"] == 0


# ---- T2 displacement, off an R2 alert -------------------------------------- #
def _alert(**kw):
    base = {
        "competitor": "OtherMonitor", "matched_text": "OtherMonitor",
        "sentence": "The Government intends to award to OtherMonitor.",
        "context": "sole_source_intent",
        "agency": "Department of the Treasury",
        "source": {"kind": "store_notice", "notice_id": "abc123"},
        "client_history_records": [
            {"record_id": "GS35F001",
             "url": "https://www.usaspending.gov/award/CONT_AWD_GS35F001",
             "recipient": "Acme Networks",
             "agency": "Department of the Treasury",
             "sub_agency": "Internal Revenue Service",
             "period_end": "2026-08-31"}],
    }
    base.update(kw)
    return base


def test_t2_fires_on_a_displacement_alert_and_joins_the_client_history():
    """POSITIVE: the alert's own source is a store notice the evidence bands
    never render, so the spec joins the CLIENT HISTORY records instead."""
    pack = _pack([_award()])
    pack.decisions = build_decisions(pack)
    pack.decisions["r2"] = {"alerts": [_alert()], "receipt": {}}
    out = tr.build_targeting(pack)
    spec = _by_rule(out, "T2")[0]
    assert spec["row_class"] == "displacement"
    assert spec["join_record_ids"] == ["GS35F001"]
    assert spec["buying_component"] == "Internal Revenue Service"
    assert spec["evidence"]["source_kind"] == "store_notice"
    assert "OtherMonitor is named" in spec["rationale"]


def test_t2_declines_an_alert_with_no_client_history_to_join():
    """NEGATIVE for T2 on R2: the join is the admission ticket. An alert
    with nothing to hang on produces no spec and is receipted by name."""
    pack = _pack([_award()])
    pack.decisions = build_decisions(pack)
    pack.decisions["r2"] = {"alerts": [_alert(client_history_records=[])],
                            "receipt": {}}
    out = tr.build_targeting(pack)
    assert _by_rule(out, "T2") == []
    untargeted = out["receipt"]["no_join_not_targeted"]
    assert any(row["source"] == "R2" for row in untargeted)


# ---- T3 adjacency ---------------------------------------------------------- #
def _forecast(**kw):
    base = dict(record_id="APFS-77", lane="L4_forecast",
                title="Enterprise network modernisation",
                agency="Department of the Treasury",
                sub_agency="Internal Revenue Service",
                url="https://apfs-cloud.dhs.gov/record/77/public-print/",
                estimated_value_range="$5M to $10M", fiscal_year="2027")
    base.update(kw)
    return GoldenRecord(**base)


def test_t3_fires_and_routes_through_the_paper_holder_not_the_buyer():
    """POSITIVE: nothing has posted, so the actionable surface is the
    reseller holding the client's paper, and tier 4 is the channel rung."""
    out = _built(_pack([_award(recipient="Carahsoft Technology"),
                        _forecast()]))
    spec = _by_rule(out, "T3")[0]
    assert spec["row_class"] == "adjacency"
    assert spec["target_org"] == {"kind": "paper_holder",
                                  "name": "Carahsoft Technology"}
    assert spec["seeks_tiers"] == [4]
    assert "Partner Alliance Manager" in spec["person_titles"]
    assert spec["evidence"]["paper_holder"] == "Carahsoft Technology"
    assert spec["join_record_ids"] == ["GS35F001"]


def test_t3_declines_when_the_client_is_its_own_only_paper_holder():
    """NEGATIVE for T3: the client winning its own award directly is not a
    route to work. No spec, and the receipt says why."""
    out = _built(_pack([_award(recipient="Acme Networks"), _forecast()]))
    assert _by_rule(out, "T3") == []
    assert out["receipt"]["no_route_not_targeted"]
    assert "no paper holder" in \
        out["receipt"]["no_route_not_targeted"][0]["why"]


# ---- the join law, at the rule layer --------------------------------------- #
def test_a_spec_joining_a_record_this_pack_does_not_carry_is_dropped():
    """NEGATIVE for the join law: a decision row referencing an id the pack
    lost cannot hang a call on anything, so it never becomes a spec."""
    pack = _pack([_award()])
    pack.decisions = build_decisions(pack)
    pack.decisions["r2"] = {"alerts": [_alert(client_history_records=[
        {"record_id": "GHOST9", "url": "https://example.gov/ghost",
         "recipient": "Nobody", "agency": "Department of the Treasury",
         "sub_agency": "Internal Revenue Service", "period_end": "2026-08-31"}
    ])], "receipt": {}}
    out = tr.build_targeting(pack)
    assert out["specs"] == []
    assert any("no joined record id is carried by this pack" in row["why"]
               for row in out["receipt"]["no_join_not_targeted"])


# ---- the receipt discloses, never hides ------------------------------------ #
def test_the_receipt_names_every_rule_and_whether_it_fired():
    out = _built(_pack([_award(period_end="2026-08-31"),
                        _rival(period_end="2026-11-30")]))
    rules = {row["rule_id"]: row for row in out["receipt"]["rules"]}
    assert set(rules) == {"T1", "T2", "T3"}
    assert rules["T1"]["fired"] is True
    assert rules["T2"]["fired"] is False and rules["T3"]["fired"] is False
    # every rule states its own basis, so a reader can see why a persona
    # set was sought without opening this file
    assert all(row["basis"] and row["when"] for row in rules.values())


def test_a_defined_tier_no_rule_sought_is_disclosed_not_silent():
    """The polysemy-corpus property applied to the ladder: a recorded rung
    that nothing consumes is documented, never quietly absent."""
    out = _built(_pack([_award(period_end="2026-08-31"),
                        _rival(period_end="2026-11-30")]))
    receipt = out["receipt"]
    assert receipt["tiers_defined"] == [1, 2, 3, 4]
    assert receipt["tiers_sought"] == [1, 2]
    assert receipt["tiers_defined_not_sought"] == [3, 4]


def test_targeting_is_deterministic_for_one_pack():
    """Replay determinism: the band must re-render byte-identically, so the
    rules may not depend on a wall clock or dict ordering."""
    records = [_award(period_end="2026-08-31"), _rival(period_end="2026-11-30")]
    first = _built(_pack(records))
    second = _built(_pack(records))
    assert json.dumps(first, sort_keys=True) == json.dumps(second,
                                                           sort_keys=True)


def test_a_pack_with_no_decisions_states_that_rather_than_a_zero():
    pack = _pack([_award()])
    out = tr.build_targeting(pack)
    assert out["specs"] == []
    assert out["receipt"]["decisions_computed"] is False


def test_no_em_dash_reaches_a_targeting_string():
    """House rule R9 at the source: the rules author copy the band prints."""
    out = _built(_pack([_award(recipient="Carahsoft Technology"),
                        _rival(period_end="2026-11-30"), _forecast()]))
    assert "—" not in json.dumps(out, ensure_ascii=False)
    assert "—" not in json.dumps(tr.load_personas(), ensure_ascii=False)
