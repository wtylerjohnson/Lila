"""Motions, blind title inference, and the workshop (build steps 1-3).

Each block pins one reviewer ruling. NO TEST CALLS A MODEL: every caller is
injected, which is also the structural proof that the inference layer has a
seam a press could never reach through.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import motions as mo  # noqa: E402
from agents.golden_press import title_strategy as ts  # noqa: E402
from agents.golden_press.decision_rules import build_decisions  # noqa: E402
from agents.golden_press.press import _press_stamp  # noqa: E402
from agents.golden_press.records import EvidencePack  # noqa: E402
from agents.golden_press.targeting_rules import build_targeting  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PACKS = REPO / "data" / "state" / "candidate_review_v1"


def _spec(row_class="displacement", kind="buying_component",
          name="Department of the Navy", **kw):
    base = {"spec_id": f"t2-{name[:4].lower()}", "row_class": row_class,
            "buying_component": name,
            "target_org": {"kind": kind, "name": name},
            "join_record_ids": ["R1"], "join_records": [], "evidence": {}}
    base.update(kw)
    return base


# ===== R1 · batching key is not the strategy key ========================== #
def test_the_batching_key_is_coarse_and_the_strategy_key_is_not():
    navy = _spec(name="Department of the Navy")
    irs = _spec(name="Internal Revenue Service")
    assert mo.batching_key(navy) == mo.batching_key(irs)      # same batch
    assert mo.strategy_key(navy) != mo.strategy_key(irs)      # different set


def test_a_buyer_side_motion_keys_on_mission_applicability():
    assert mo.strategy_key(_spec(name="Defense Information Systems Agency"))[2] \
        == mo.DEFENSE
    assert mo.strategy_key(_spec(name="Internal Revenue Service"))[2] \
        == mo.CIVILIAN


def test_a_paper_holder_motion_never_keys_on_agency_family():
    """The target is a COMPANY. Agency family is meaningless there; what
    decides the titles is which holder and its route role."""
    key = mo.strategy_key(_spec(row_class="adjacency", kind="paper_holder",
                                name="FCN, INC."))
    assert key[1] == "paper_holder"
    assert mo.DEFENSE not in key and mo.CIVILIAN not in key
    assert key[2] == "fcn-inc" and key[3] in (
        mo.RESELLER, mo.PRIME, mo.VEHICLE_HOLDER, mo.UNKNOWN_ROUTE)


def test_an_unrecognised_agency_is_unknown_not_silently_civilian():
    """NEGATIVE: positive recognition only. A confident wrong mission signal
    is worse than a stated absence, because the inference would reason from
    it without being told it was a guess."""
    assert mo.mission_applicability([]) == mo.UNKNOWN_MISSION
    assert mo.mission_applicability(["  "]) == mo.UNKNOWN_MISSION


def test_a_motion_spanning_both_families_keys_as_mixed():
    assert mo.mission_applicability(
        ["Department of the Navy", "Internal Revenue Service"]) == mo.MIXED


def test_red_hats_one_displacement_batch_splits_by_mission():
    """THE MEASUREMENT THAT MOTIVATED R1. One batch, eight organisations,
    both families. A single title set would have to name an Authorizing
    Official and an OCFO procurement lead at once."""
    path = PACKS / "red_hat" / "red_hat.golden_report.evidence_pack.json"
    if not path.exists():
        pytest.skip("red_hat pack not on this machine")
    pack = EvidencePack.model_validate(
        json.loads(path.read_text(encoding="utf-8")))
    stamp = _press_stamp(pack)
    fresh = build_decisions(
        pack, today=date.fromisoformat(stamp) if stamp else None)
    if (pack.decisions or {}).get("r2"):
        fresh["r2"] = pack.decisions["r2"]
    pack.decisions = fresh
    specs = build_targeting(pack)["specs"]
    batches = {mo.batching_key(s) for s in specs}
    built = mo.build_motions(specs)
    assert len(built) > len(batches), "keying did not split any batch"
    displacement = [m for m in built
                    if m["row_class"] == "displacement"
                    and m["org_kind"] == "buying_component"]
    assert {m["mission_applicability"] for m in displacement} == {
        mo.DEFENSE, mo.CIVILIAN}


def test_motions_are_ordered_and_carry_their_own_evidence():
    specs = [_spec(name="Internal Revenue Service"),
             _spec(name="Department of the Navy")]
    built = mo.build_motions(specs)
    assert [m["motion_id"] for m in built] == sorted(
        m["motion_id"] for m in built)
    for motion in built:
        assert motion["spec_ids"] and motion["join_record_ids"]
        assert motion["motion"] == "validate displacement"
        assert motion["objective"]


# ===== R7 · the fingerprint covers the payload, not the record ids ======== #
def _payload(**kw):
    motion = mo.build_motions([_spec()])[0]
    return ts.inference_payload(
        motion, capability={"terms": ["linux"]},
        records=[{"record_id": "R1", "agency": "Navy"}],
        rules_version="targeting_rules.v1", **kw)


def test_the_fingerprint_is_stable_across_identical_runs():
    assert ts.fingerprint(_payload()) == ts.fingerprint(_payload())


@pytest.mark.parametrize("field,value", [
    ("prompt_version", "title_prompt.v2"),
    ("schema_version", "title_strategy.v2"),
])
def test_a_prompt_or_schema_bump_stales_the_strategy(field, value):
    """Hashing record ids alone would let a rewritten prompt silently reuse
    an approved title set."""
    assert ts.fingerprint(_payload()) != ts.fingerprint(_payload(**{field: value}))


def test_changed_capability_vocabulary_stales_the_strategy():
    motion = mo.build_motions([_spec()])[0]
    a = ts.inference_payload(motion, capability={"terms": ["linux"]})
    b = ts.inference_payload(motion, capability={"terms": ["linux", "kvm"]})
    assert ts.fingerprint(a) != ts.fingerprint(b)


def test_changed_evidence_stales_the_strategy():
    motion = mo.build_motions([_spec()])[0]
    a = ts.inference_payload(motion, records=[{"record_id": "R1"}])
    b = ts.inference_payload(motion, records=[{"record_id": "R1",
                                               "obligated_dollars": 5.0}])
    assert ts.fingerprint(a) != ts.fingerprint(b)


# ===== R2 · blind generation plus deterministic diff ====================== #
def _gen(*_a, **_k):
    return {"personas": [
        {"title": "Program Manager", "function": "mission/program"},
        {"title": "Contracting Officer", "function": "procurement/contracting"},
        {"title": "Chief Information Officer", "function": "mission/program"}]}


def _chal(*_a, **_k):
    return {"recommended": [
        {"title": "Contracting Officer", "function": "procurement/contracting"}],
        "likely_omissions": [
            {"title": "Authorizing Official", "function": "security operations",
             "why": "the DoD ATO gate"}],
        "tempting_but_wrong": [
            {"title": "Chief Information Officer", "function": "mission/program",
             "why": "too senior to own this buy"}]}


def _diff():
    payload = _payload()
    return ts.diff_strategies(ts.generate_titles(payload, caller=_gen),
                              ts.challenge_titles(payload, caller=_chal),
                              org_kind="buying_component")


def test_the_challenger_never_sees_the_generators_output():
    """STRUCTURAL: challenge_titles takes only the payload. There is no
    parameter through which the generator's list could reach it, so
    anchoring is impossible rather than merely discouraged."""
    import inspect
    params = set(inspect.signature(ts.challenge_titles).parameters)
    assert params == {"payload", "caller"}


def test_the_diff_separates_agreement_additions_and_disputes():
    diff = _diff()
    assert [p["title"] for p in diff["agreement"]] == [
        "Program Manager", "Contracting Officer"]
    assert [p["title"] for p in diff["additions"]] == ["Authorizing Official"]
    assert [p["title"] for p in diff["disputes"]] == [
        "Chief Information Officer"]
    assert diff["disputes"][0]["dispute_reason"]
    assert diff["material_disagreement"] is True


def test_title_matching_survives_ordinary_punctuation_drift():
    """'Sr. Contracting Officer' and 'Senior Contracting Officer' are one
    proposal, not two, or the diff invents disagreement."""
    assert ts._title_key("Sr. Contracting Officer") == \
        ts._title_key("Senior Contracting Officer")
    assert ts._title_key("Director, Cyber") == ts._title_key("Director Cyber")


def test_a_dead_challenger_is_usable_not_fatal():
    """NEGATIVE: losing the recall pass must never lose the motion."""
    payload = _payload()
    def boom(*_a, **_k):
        raise RuntimeError("cli not logged in")
    challenge = ts.challenge_titles(payload, caller=boom)
    diff = ts.diff_strategies(ts.generate_titles(payload, caller=_gen),
                              challenge, org_kind="buying_component")
    assert len(diff["agreement"]) == 3
    assert diff["disputes"] == []
    assert diff["challenge_unavailable"]


def test_a_generator_with_no_usable_title_raises():
    """NEGATIVE: an empty strategy must be loud. Silently proceeding would
    search for nobody and read downstream as a tested zero."""
    with pytest.raises(ts.TitleStrategyError, match="no usable title"):
        ts.generate_titles(_payload(), caller=lambda *a, **k: {"personas": []})


def test_a_malformed_persona_is_dropped_never_repaired():
    """An invented function or seniority would be a claim about how the
    buying committee works."""
    out = ts.generate_titles(_payload(), caller=lambda *a, **k: {"personas": [
        {"title": "Program Manager"}, {"no_title": "x"}, "garbage"]})
    assert len(out["personas"]) == 1
    assert out["personas"][0]["function"] is None


# ===== R3 · the operator wins; a dispute withholds, never blocks ========== #
def test_a_disputed_title_is_withheld_from_supply_but_kept_visible():
    workshop = ts.build_workshop(_diff(), motion_id="m1")
    assert "Chief Information Officer" not in ts.approved_titles(workshop)
    disputed = [r for r in workshop[ts.NEEDS_JUDGMENT]
                if r["title"] == "Chief Information Officer"]
    assert disputed and disputed[0]["dispute_reason"]


def test_a_challenger_addition_needs_judgment_before_it_searches():
    workshop = ts.build_workshop(_diff(), motion_id="m1")
    assert "Authorizing Official" not in ts.approved_titles(workshop)
    assert any(r["title"] == "Authorizing Official"
               for r in workshop[ts.NEEDS_JUDGMENT])


def test_a_dispute_alone_does_not_block_the_motion():
    blocked, reasons = ts.supply_blocked(
        ts.build_workshop(_diff(), motion_id="m1"),
        org_kind="buying_component")
    assert blocked is False and reasons == []


def test_the_motion_blocks_only_when_a_required_function_is_uncovered():
    """A buyer-side motion with nobody who can put it on contract is a real
    finding about the engagement, receipted rather than raised."""
    workshop = ts.build_workshop(
        ts.diff_strategies(
            {"personas": [{"title": "Program Manager",
                           "function": "mission/program"}]},
            {"recommended": [], "likely_omissions": [],
             "tempting_but_wrong": []},
            org_kind="buying_component"), motion_id="m1")
    blocked, reasons = ts.supply_blocked(workshop,
                                         org_kind="buying_component")
    assert blocked is True
    assert any("procurement" in r for r in reasons)


# ===== R4 · functional coverage, never a persona count ==================== #
def test_there_is_no_persona_floor():
    """Two titles covering both required functions is complete; the legacy
    min_length=4 would have demanded padding."""
    diff = ts.diff_strategies(
        {"personas": [{"title": "Program Manager", "function": "mission/program"},
                      {"title": "Contracting Officer",
                       "function": "procurement/contracting"}]},
        {"recommended": [], "likely_omissions": [], "tempting_but_wrong": []},
        org_kind="buying_component")
    assert len(diff["agreement"]) == 2
    assert diff["coverage_complete"] is True
    assert diff["functions_missing"] == []


def test_a_paper_holder_motion_requires_only_the_channel_function():
    diff = ts.diff_strategies(
        {"personas": [{"title": "Channel Sales Director",
                       "function": "channel/prime"}]},
        {"recommended": [], "likely_omissions": [], "tempting_but_wrong": []},
        org_kind="paper_holder")
    assert diff["coverage_complete"] is True


# ===== R5 · persona.why is internal ======================================= #
def test_persona_why_rides_the_workshop_and_is_not_client_copy():
    """It is reasoning about a CATEGORY, and the band's client copy is
    assembled from deterministic templates over the approved title."""
    workshop = ts.build_workshop(_diff(), motion_id="m1")
    assert "why" in workshop[ts.NEEDS_JUDGMENT][0]
    from agents.golden_press import render
    source = Path(render.__file__).read_text(encoding="utf-8")
    assert "persona_why" not in source
    assert "buying_committee_note" not in source


# ===== R8 · the ladder is an explicit import, never a fallback ============ #
def test_the_ladder_is_never_reached_automatically():
    """STRUCTURAL: no module in the title path calls import_ladder. A
    fallback that fires silently becomes the default, which is exactly how a
    25-credit cap became an invisible coverage limit in this same lane."""
    for name in ("title_strategy.py", "motions.py"):
        source = (REPO / "agents" / "golden_press" / name).read_text(
            encoding="utf-8")
        calls = [line for line in source.splitlines()
                 if "import_ladder(" in line and not line.lstrip().startswith(
                     ("def ", "#", '"'))]
        assert calls == [], f"{name} calls import_ladder automatically"


def test_an_imported_ladder_title_is_labeled_as_an_operator_import():
    rows = ts.import_ladder({"seeks_tiers": [1]}, motion_id="m1")
    assert rows
    assert all(r["provenance"] == ts.FROM_LADDER for r in rows)
    assert all("explicit operator import" in r["lane_reason"] for r in rows)
