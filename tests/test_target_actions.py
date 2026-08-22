"""The actionable target projection (build step 6).

The chain an operator must be able to trace without inference:
    opportunity -> route -> account -> person -> why -> what to do -> records

Two things make that safe and both are pinned here: every sentence is a
DETERMINISTIC template over rule output and approved parts, so no model prose
reaches the page; and a row carries the join records of ITS OWN binding,
never the union across every spec a person happens to answer.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import motions as mo  # noqa: E402
from agents.golden_press import person_screen as ps  # noqa: E402
from agents.golden_press import target_actions as ta  # noqa: E402

_ACCEPTED = {"state": ps.ACCEPTED, "verdict": "pass", "findings": [],
             "cautions": [], "source_class": ps.NOTICE_POC}


def _spec(spec_id="t2-01", name="Defense Logistics Agency",
          row_class="displacement", kind="buying_component", records=("R1",),
          rationale="Rival is named at DLA on a record the client also holds."):
    return {"spec_id": spec_id, "row_class": row_class, "source_rule": "R2",
            "buying_component": name, "office": None,
            "target_org": {"kind": kind, "name": name},
            "rationale": rationale,
            "join_record_ids": list(records),
            "join_records": [{"record_id": r, "period_end": "2026-11-20",
                              "url": f"https://www.usaspending.gov/award/{r}"}
                             for r in records],
            "evidence": {}}


def _contact(spec_ids=("t2-01",), bindings=None, **kw):
    base = {
        "contact_id": "sam:kyle@dla.mil", "name": "Kyle Barner",
        "title": "Contract Specialist", "email": "kyle.barner@dla.mil",
        "email_status": "verified", "function": "procurement/contracting",
        "tier": None, "organization": "Defense Logistics Agency",
        "spec_id": spec_ids[0], "spec_ids": list(spec_ids),
        "phones": [{"number": "445-737-8182", "type": "work_direct",
                    "basis": "published on the notice",
                    "basis_kind": "government_published"}],
        "screen": dict(_ACCEPTED),
        "provenance": {"class": "apollo", "retrieved_at": "2026-08-06",
                       "source": "sam.notice_poc"},
        "bindings": bindings if bindings is not None else [
            {"spec_id": s, "strength": "same_subtier",
             "why": "same buying organisation, capability match",
             "join_record_ids": ["R1"]} for s in spec_ids],
    }
    base.update(kw)
    return base


def _build(specs=None, contacts=None):
    specs = specs or [_spec()]
    return ta.build_target_actions(
        mo.build_motions(specs), contacts or [_contact()],
        client="Thinklogical", scope="all", as_of="2026-08-06")


# ===== the chain is complete and deterministic ============================ #
def test_a_row_carries_the_whole_chain():
    row = _build()["rows"][0]
    for field in ("motion", "decision_rule", "spec_id", "buying_agency",
                  "target_organisation", "organisation_role", "name",
                  "persona_function", "email", "record_ids", "record_urls",
                  "why_this_account", "why_this_person", "why_now",
                  "recommended_action", "outreach_objective",
                  "screening_state", "contact_source_class"):
        assert row.get(field), f"{field} is empty"


def test_why_this_account_is_the_decision_rules_own_sentence():
    """Never rewritten and never summarised: the rule already said it."""
    spec = _spec(rationale="Vertiv is named at DLA in an award the client holds.")
    row = _build([spec])["rows"][0]
    assert row["why_this_account"] == spec["rationale"]


def test_why_now_comes_from_the_clock_already_on_the_record():
    assert "2026-11-20" in _build()["rows"][0]["why_now"]


def test_an_undated_record_says_so_rather_than_inventing_a_clock():
    spec = _spec()
    spec["join_records"] = [{"record_id": "R1"}]
    assert "unestablished" in _build([spec])["rows"][0]["why_now"]


def test_the_recommended_action_is_specific_to_the_motion():
    """Not 'reach out'. An operator should read it and know what the call
    is for."""
    displacement = _build()["rows"][0]["recommended_action"]
    assert "incumbent" in displacement and "specified" in displacement
    renewal = _build([_spec(row_class="renewal")])["rows"][0][
        "recommended_action"]
    assert "renewal decision" in renewal
    for action in ta.RECOMMENDED_ACTIONS.values():
        assert "reach out" not in action.casefold()
        assert "introduce the company" not in action.casefold()


def test_no_field_in_a_row_is_model_prose():
    """STRUCTURAL: the projection module imports no model caller at all, so
    there is no path by which prose could enter a rendered sentence."""
    from pathlib import Path
    source = Path(ta.__file__).read_text(encoding="utf-8")
    for forbidden in ("maxplan_cli", "run_claude", "title_strategy",
                      "persona_why", "buying_committee_note"):
        assert forbidden not in source


# ===== no evidence union ================================================== #
def test_one_person_on_three_specs_makes_three_rows_each_with_its_own_records():
    """THE JOIN LAW. Collapsing these into one row with the union of records
    would let a contact claim a record that does not justify calling them."""
    specs = [_spec("t2-01", records=("A",)), _spec("t2-02", records=("B",)),
             _spec("t2-03", records=("C",))]
    contact = _contact(
        spec_ids=("t2-01", "t2-02", "t2-03"),
        bindings=[{"spec_id": "t2-01", "strength": "direct_notice",
                   "why": "cited", "join_record_ids": ["A"]},
                  {"spec_id": "t2-02", "strength": "same_subtier",
                   "why": "same org", "join_record_ids": ["B"]},
                  {"spec_id": "t2-03", "strength": "same_subtier",
                   "why": "same org", "join_record_ids": ["C"]}])
    rows = _build(specs, [contact])["rows"]
    assert len(rows) == 3
    assert [r["record_ids"] for r in rows] == [["A"], ["B"], ["C"]]
    assert {r["bind_strength"] for r in rows} == {"direct_notice",
                                                  "same_subtier"}


def test_the_person_view_groups_without_merging_the_records():
    """A caller reads a call sheet, not a cross-product. The person appears
    once and every spec they cover is named underneath, still separately."""
    specs = [_spec("t2-01", records=("A",)), _spec("t2-02", records=("B",))]
    contact = _contact(
        spec_ids=("t2-01", "t2-02"),
        bindings=[{"spec_id": "t2-01", "strength": "same_subtier",
                   "why": "x", "join_record_ids": ["A"]},
                  {"spec_id": "t2-02", "strength": "same_subtier",
                   "why": "x", "join_record_ids": ["B"]}])
    group = _build(specs, [contact])["groups"][0]
    assert group["person_count"] == 1
    person = group["people"][0]
    assert len(person["covers"]) == 2
    assert [c["record_ids"] for c in person["covers"]] == [["A"], ["B"]]


# ===== screening gates the projection ===================================== #
def test_an_unscreened_person_never_reaches_the_projection():
    out = _build(contacts=[_contact(screen=None)])
    assert out["rows"] == []
    assert out["withheld"]["unscreened"] == 1


@pytest.mark.parametrize("state,rows", [
    (ps.ACCEPTED, 1), (ps.ACCEPTED_WITH_FLAGS, 1),
    (ps.REJECTED, 0), (ps.PENDING_SCREEN, 0)])
def test_only_accepted_states_produce_action_rows(state, rows):
    contact = _contact(screen=dict(_ACCEPTED, state=state))
    assert len(_build(contacts=[contact])["rows"]) == rows


def test_cautions_ride_the_row_so_a_caller_sees_them():
    contact = _contact(screen=dict(_ACCEPTED, state=ps.ACCEPTED_WITH_FLAGS,
                                   cautions=["address status is extrapolated"]))
    assert _build(contacts=[contact])["rows"][0]["cautions"] == [
        "address status is extrapolated"]


# ===== coverage is stated, never assumed ================================== #
def test_a_motion_with_no_contact_is_a_receipted_supply_gap():
    """Never a silent absence. This is exactly where Apollo would add
    value, and the list says so rather than reading as complete."""
    # NOTE the second spec is CIVILIAN. Navy and DLA would key into the same
    # motion (displacement x buying_component x defense), which is the R1
    # keying working; only a different mission family makes a second motion.
    specs = [_spec("t2-01"),
             _spec("t2-09", name="Internal Revenue Service")]
    out = _build(specs, [_contact(spec_ids=("t2-01",))])
    assert out["action_list_complete"] is False
    assert out["supply_gaps"]
    assert out["motions_covered"] < out["motions_total"]


def test_a_fully_covered_book_states_completeness_positively():
    out = _build()
    assert out["action_list_complete"] is True
    assert out["supply_gaps"] == []


def test_a_contact_bound_to_no_known_spec_is_withheld_and_counted():
    out = _build(contacts=[_contact(spec_ids=("ghost",))])
    assert out["rows"] == []
    assert out["withheld"]["no_binding"] == 1


# ===== the export carries the reasoning =================================== #
def test_the_export_is_not_reduced_to_name_title_company_email():
    """An export stripped to identity throws away every reason the person is
    on the list, which is the whole product."""
    rows = ta.action_export_rows(_build())
    assert rows
    for column in ("Why This Account", "Why This Person", "Why Now",
                   "Recommended Action", "Record IDs", "Record URLs",
                   "Bind Strength", "Screening", "Motion"):
        assert column in rows[0]
        assert rows[0][column] != ""


def test_the_export_separates_the_phone_types():
    row = ta.action_export_rows(_build())[0]
    assert row["Direct Phone"] == "445-737-8182"
    assert row["Mobile"] == "" and row["Main Line"] == ""


def test_an_unattributed_tier_exports_as_unattributed():
    assert ta.action_export_rows(_build())[0]["Persona Tier"] == "unattributed"


def test_a_government_published_contact_is_labeled_as_such():
    """The evidence class is visible: a POC the government published is not
    the same kind of thing as a commercial directory match."""
    assert ta.action_export_rows(_build())[0][
        "Contact Source"] == "government published"


def test_record_urls_resolve_to_canonical_hosts():
    row = ta.action_export_rows(_build())[0]
    assert "usaspending.gov/award/" in row["Record URLs"]
