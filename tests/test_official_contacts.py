"""The free official-contact lane (build step 5).

A SAM point of contact is published BY the government ON a notice, so the
join law is satisfied by construction rather than by inference, and it costs
nothing. These tests pin the two things that make that safe: the join stays
tight enough not to become "every POC at this agency", and the published
name field, which is filthy, never reaches a client artifact raw.

Measured on the real store while building this: 1,638 of 2,282 candidate
identities for one client were NOT people. "Questions regarding this
solicitation", "Telephone: 4457373493", "DibbsBSM". Every one would have
rendered as a named contact.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import official_contacts as oc  # noqa: E402

_COLUMNS = ("notice_id", "title", "agency", "subtier", "office", "posted",
            "url", "sol_number", "description_prefix",
            "poc_name", "poc_email", "poc_phone", "poc_title",
            "poc_secondary_name", "poc_secondary_email",
            "poc_secondary_phone", "poc_secondary_title")


@pytest.fixture()
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE notices (%s)" % ", ".join(
        f"{c} TEXT" for c in _COLUMNS))
    yield conn
    conn.close()


def _notice(conn, **kw):
    row = {c: "" for c in _COLUMNS}
    row.update({"notice_id": "N1", "title": "KVM switch refresh",
                "agency": "DEPT OF DEFENSE",
                "subtier": "DEFENSE LOGISTICS AGENCY",
                "office": "DLA LAND", "posted": "2026-07-01",
                "url": "https://sam.gov/opp/N1/view", "sol_number": "SPE123",
                "poc_name": "Gerard Harrigan",
                "poc_email": "gerard.harrigan@us.af.mil",
                "poc_phone": "555-123-4567"})
    row.update(kw)
    conn.execute("INSERT INTO notices VALUES (%s)" % ",".join("?" * len(_COLUMNS)),
                 [row[c] for c in _COLUMNS])
    conn.commit()


def _spec(**kw):
    base = {"spec_id": "t2-01", "row_class": "displacement",
            "buying_component": "Defense Logistics Agency", "office": None,
            "target_org": {"kind": "buying_component",
                           "name": "Defense Logistics Agency"},
            "seeks_tiers": [1, 2], "persona_titles": {}, "join_record_ids": [],
            "join_records": [], "evidence": {}}
    base.update(kw)
    return base


# ===== the lane spends nothing and calls nobody =========================== #
def test_the_lane_makes_no_network_call_and_spends_no_credit(store):
    _notice(store)
    out = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert out["receipt"]["network_calls"] == 0
    assert out["receipt"]["credits_spent"] == 0
    assert out["contacts"]


def test_a_missing_store_is_a_stated_absence_not_a_crash():
    out = oc.harvest([_spec()], conn=None, capability_terms=["kvm"])
    assert out["contacts"] == []
    assert out["receipt"]["attempted"] is False
    assert out["receipt"]["why"]


# ===== the join stays tight ================================================ #
def test_a_cited_notice_binds_at_the_strongest_strength(store):
    """The POC published on a record this report already cites. No capability
    match is required, because the record itself is the join."""
    _notice(store, notice_id="CITED", title="unrelated words")
    out = oc.harvest([_spec(join_record_ids=["CITED"])], conn=store,
                     capability_terms=["kvm"], retrieved_at="2026-08-06")
    assert out["contacts"][0]["bindings"][0]["strength"] == oc.DIRECT_NOTICE


def test_without_a_capability_match_nothing_binds(store):
    """THE LINE THAT STOPS THE LANE BECOMING 'every POC at this agency'.
    Same agency, same subtier, but the notice is about something else."""
    _notice(store, title="janitorial services", description_prefix="mopping")
    out = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert out["contacts"] == []
    assert out["receipt"]["uncovered_specs"]


def test_a_different_agency_never_binds(store):
    _notice(store, agency="DEPT OF THE INTERIOR", subtier="BLM",
            office="WYOMING")
    out = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert out["contacts"] == []


def test_the_component_is_matched_against_subtier_not_only_agency(store):
    """The store files the component in `subtier`: "Defense Logistics
    Agency" matches 0 rows in `agency` and 16,149 in `subtier`. Searching
    only `agency` is why the first build of this lane screened nothing."""
    _notice(store, agency="DEPT OF DEFENSE",
            subtier="DEFENSE LOGISTICS AGENCY")
    out = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert len(out["contacts"]) == 1


# ===== the published name field is filthy and never renders raw =========== #
@pytest.mark.parametrize("raw,name,phone", [
    ("KYLE BARNER445-737-8182", "KYLE BARNER", "445-737-8182"),
    ("Alla YakoverDSN614-693-0639", "Alla Yakover", "614-693-0639"),
    ("JOHN SMITH DSN 312-555-1212", "JOHN SMITH", "312-555-1212"),
    ("Gerard Harrigan", "Gerard Harrigan", ""),
    ("O'Brien, Sean", "O'Brien Sean", ""),
])
def test_a_phone_buried_in_the_name_is_recovered_not_rendered(raw, name, phone):
    """DLA files the number inside the name. Splitting it out both cleans
    the name and recovers a phone the lane would otherwise have lost."""
    got_name, got_phone, reject = oc.clean_poc_name(raw)
    assert reject == ""
    assert got_name == name
    assert got_phone == phone


@pytest.mark.parametrize("raw", [
    "Questions regarding this solicitation",
    "Telephone: 4457373493",
    "DibbsBSM",
    "please contact us",
    "",
])
def test_a_non_person_is_refused(raw):
    """NEGATIVE. A shared mailbox or an instruction is not a named
    individual, and Band 09 names individuals. Refusing is the point: a
    wrong name in front of a caller is worse than no contact."""
    name, _phone, reject = oc.clean_poc_name(raw)
    assert name == "" and reject


def test_refused_names_are_counted_by_reason_never_silently_dropped(store):
    _notice(store, poc_name="Questions regarding this solicitation")
    out = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert out["contacts"] == []
    assert out["receipt"]["rejected"] == 1
    assert out["receipt"]["rejected_names"]


# ===== R6 · a contracting POC proves procurement only ===================== #
def test_a_poc_with_no_published_title_is_procurement_only():
    function, basis = oc.persona_function("")
    assert function == "procurement/contracting"
    assert "procurement authority only" in basis


@pytest.mark.parametrize("title", [
    "Contract Specialist", "Contracting Officer", "Purchasing Agent"])
def test_a_published_contracting_title_stays_procurement(title):
    assert oc.persona_function(title)[0] == "procurement/contracting"


@pytest.mark.parametrize("title,function", [
    ("Program Manager", "mission/program"),
    ("Contracting Officer's Representative", "mission/program"),
    ("ISSO", "security operations"),
    ("Authorizing Official", "security operations"),
])
def test_only_published_text_promotes_a_poc_beyond_procurement(title, function):
    """Publication promotes; inference never does."""
    got, basis = oc.persona_function(title)
    assert got == function
    assert title.split()[0].casefold() in basis.casefold()


def test_a_poc_carries_no_persona_tier(store):
    """No silent rung. The FUNCTION is the claim the published title
    supports; a tier would assert a seniority nothing established."""
    _notice(store)
    out = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert out["contacts"][0]["tier"] is None
    assert out["contacts"][0]["function"] == "procurement/contracting"


# ===== identity dedupe keeps its separate bindings ======================== #
def test_one_poc_serving_two_specs_dedupes_but_keeps_both_bindings(store):
    """DEDUPE THE IDENTITY, NEVER THE BINDING. One POC can justify two
    different calls; collapsing the bindings loses which record each hangs
    off, which is the evidence-union defect one lane over."""
    _notice(store)
    specs = [_spec(spec_id="t2-01", join_record_ids=["A"]),
             _spec(spec_id="t2-02", join_record_ids=["B"])]
    out = oc.harvest(specs, conn=store, capability_terms=["kvm"],
                     retrieved_at="2026-08-06")
    assert len(out["contacts"]) == 1
    contact = out["contacts"][0]
    assert sorted(contact["spec_ids"]) == ["t2-01", "t2-02"]
    assert len(contact["bindings"]) == 2
    assert {b["spec_id"] for b in contact["bindings"]} == {"t2-01", "t2-02"}


# ===== provenance, screening and the receipt ============================== #
def test_every_contact_carries_provenance_and_a_notice_source(store):
    _notice(store)
    contact = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                         retrieved_at="2026-08-06")["contacts"][0]
    assert contact["provenance"]["class"] in ("apollo", "operator")
    assert contact["provenance"]["retrieved_at"] == "2026-08-06"
    assert contact["provenance"]["source"] == oc.SOURCE_NAME
    assert contact["notice_id"] and contact["source_url"]
    assert contact["solicitation_number"] == "SPE123"


def test_every_contact_is_screened_under_the_notice_poc_policy(store):
    """Screening is a publication requirement for every lane, not only for
    Apollo, and a government POC is screened as a notice POC rather than
    interrogated for identity it already has."""
    from agents.golden_press.person_screen import NOTICE_POC, RENDERABLE_STATES
    _notice(store)
    contact = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                         retrieved_at="2026-08-06")["contacts"][0]
    assert contact["screen"]["source_class"] == NOTICE_POC
    assert contact["screen"]["state"] in RENDERABLE_STATES


def test_a_published_phone_is_classified_as_the_dial_not_a_switchboard(store):
    """The government publishes the field Apollo charges eight credits to
    reveal. It is the POC's own number on the notice, so it is the dial."""
    from agents.golden_press.phone_policy import WORK_DIRECT
    _notice(store)
    contact = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                         retrieved_at="2026-08-06")["contacts"][0]
    assert contact["phones"][0]["type"] == WORK_DIRECT
    assert contact["phones"][0]["basis_kind"] == "government_published"


def test_an_uncovered_spec_is_receipted_as_where_apollo_would_add_value(store):
    _notice(store, title="janitorial services")
    receipt = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                         retrieved_at="2026-08-06")["receipt"]
    assert len(receipt["uncovered_specs"]) == 1
    assert receipt["uncovered_specs"][0]["spec_id"] == "t2-01"
    assert "Apollo" in receipt["uncovered_specs"][0]["why"]


def test_a_secondary_poc_is_harvested_as_its_own_identity(store):
    """23,666 notices carry a second published POC. Ignoring them discards a
    third of the free supply."""
    _notice(store, poc_secondary_name="Dana Reyes",
            poc_secondary_email="dana.reyes@dla.mil",
            poc_secondary_phone="555-999-0000")
    contacts = oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                          retrieved_at="2026-08-06")["contacts"]
    assert {c["name"] for c in contacts} == {"Gerard Harrigan", "Dana Reyes"}


def test_a_non_federal_address_is_never_harvested(store):
    _notice(store, poc_email="someone@contractor.com")
    assert oc.harvest([_spec()], conn=store, capability_terms=["kvm"],
                      retrieved_at="2026-08-06")["contacts"] == []
