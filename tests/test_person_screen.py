"""The targeting screener, and the eleven real people it must not delete.

THE INCIDENT. A live IRS search returned "Vasyl Lyovin, Chief Information
Officer" against the correct Apollo org id, name and domain irs.gov. He is
in Ukraine, his timezone is Europe/Kiev, and his career runs through Fozzy
Group and Paritet Avto. Nothing in the pipeline would have stopped him
reaching the client artifact.

THE HARDER HALF. The obvious rule set to catch him DELETED SIX of the eleven
people the same live run confirmed legitimate:

  reject on private-sector career   -> deletes the FBI CTO (Meta), the IRS
                                        CIO (Gerson Lehrman), and every
                                        contracting officer hired out of
                                        industry, which is the modal federal
                                        technology career
  require a populated country       -> deletes two people whose records
                                        simply carry no country
  email domain must equal org domain-> deletes every component subdomain
                                        (jpl.nasa.gov, cbp.dhs.gov)
  reject on stale record            -> deletes the career civil servant who
                                        has not moved, for not moving

So every rule here carries a NEGATIVE CASE built from a real person's record
shape, and only ONE rule may reject. The fixtures below are the actual
records returned on 2026-08-06, trimmed to the fields the screen reads.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import person_screen as ps  # noqa: E402

TODAY = date(2026, 8, 6)
COMPONENT = {"target_org": {"kind": "buying_component",
                            "name": "Internal Revenue Service"}}
FBI = {"target_org": {"kind": "buying_component",
                      "name": "Federal Bureau of Investigation"}}
RESELLER = {"target_org": {"kind": "paper_holder",
                           "name": "DEV TECHNOLOGY GROUP INC"}}


# ---- the false positive, verbatim shape ---------------------------------- #
LYOVIN = {
    "name": "Vasyl Lyovin", "title": "Chief Information Officer",
    "country": "Ukraine", "formatted_address": "Ukraine",
    "time_zone": "Europe/Kiev",
    "email": "vasyl.lyovin@irs.gov", "email_status": "verified",
    "linkedin_url": "http://www.linkedin.com/in/vasyl-lyovin-41524360",
    "organization": {"name": "Internal Revenue Service",
                     "primary_domain": "irs.gov"},
    "employment_history": [
        {"current": True, "organization_name": "irs",
         "title": "Chief Information Officer", "start_date": "2013-12-01"},
        {"current": False, "organization_name": "CVSO"},
        {"current": False, "organization_name": "Paritet Avto"},
        {"current": False, "organization_name": "Prosper"},
        {"current": False, "organization_name": "Fozzy Group"}],
}

# ---- the eleven confirmed-legitimate people ------------------------------ #
MCCORMICK = {  # private-sector career, FBI contracting officer
    "name": "Brian McCormick", "title": "Supervisory Contracting Officer",
    "city": "Decatur", "state": "Alabama", "country": "United States",
    "formatted_address": "Decatur, AL, USA", "time_zone": "America/Chicago",
    "email": "bwmccormick@fbi.gov", "email_status": "verified",
    "linkedin_url": "http://www.linkedin.com/in/brian-mccormick",
    "organization": {"name": "Federal Bureau of Investigation (FBI)",
                     "primary_domain": "fbi.gov"},
    "employment_history": [
        {"current": True,
         "organization_name": "Federal Bureau of Investigation (FBI)",
         "start_date": "2024-07-01", "raw_last_updated_at": "2026-07-18"},
        {"current": False, "organization_name": "Parker Hannifin"},
        {"current": False, "organization_name": "O'Reilly Auto Parts"},
        {"current": False, "organization_name": "Southern Petroleum"}],
}
SWANSTROM = {  # FBI CTO out of Meta
    "name": "Geoff Swanstrom", "title": "Chief Technology Officer",
    "city": "New York", "country": "United States",
    "formatted_address": "New York, NY, USA", "time_zone": "America/New_York",
    "email": "gaswanstrom@fbi.gov", "email_status": "verified",
    "linkedin_url": "http://www.linkedin.com/in/geoffswanstrom",
    "organization": {"name": "Federal Bureau of Investigation (FBI)",
                     "primary_domain": "fbi.gov"},
    "employment_history": [
        {"current": True,
         "organization_name": "Federal Bureau of Investigation (FBI)",
         "start_date": "2026-02-01", "raw_last_updated_at": "2026-07-31"},
        {"current": False, "organization_name": "Meta"},
        {"current": False, "organization_name": "Georgia Institute of Technology"}],
}
SICHKO = {  # NO city, NO state, NO timezone; extrapolated email
    "name": "Alec Sichko", "title": "Cybersecurity Program Manager",
    "country": "United States", "formatted_address": "United States",
    "email": "asichko@fbi.gov", "email_status": "extrapolated",
    "extrapolated_email_confidence": 0.7,
    "linkedin_url": "http://www.linkedin.com/in/alec-s",
    "organization": {"name": "Federal Bureau of Investigation (FBI)",
                     "primary_domain": "fbi.gov"},
    "employment_history": [
        {"current": True,
         "organization_name": "Federal Bureau of Investigation (FBI)",
         "start_date": "2024-03-01", "raw_last_updated_at": "2026-07-07"},
        {"current": False, "organization_name": "PLC Connections LLC"}],
}
SLATE = {  # NO country at all, but a city and a timezone
    "name": "Daryl Slate", "title": "Senior Contract Specialist",
    "city": "Fort Stewart", "state": "Georgia",
    "time_zone": "America/New_York",
    "email": "dslate@fbi.gov", "email_status": "extrapolated",
    "extrapolated_email_confidence": 0.7,
    "linkedin_url": "http://www.linkedin.com/in/daryl-slate",
    "organization": {"name": "Federal Bureau of Investigation (FBI)",
                     "primary_domain": "fbi.gov"},
    "employment_history": [
        {"current": True,
         "organization_name": "Federal Bureau of Investigation (FBI)",
         "start_date": "2022-12-01", "raw_last_updated_at": "2026-06-22"},
        {"current": False, "organization_name": "US Army"}],
}
NATERNICOLA = {  # TWO concurrent current employers
    "name": "Kristi Naternicola", "title": "Program Manager",
    "city": "Fairmont", "state": "West Virginia",
    "country": "United States", "time_zone": "America/New_York",
    "email": "kanaternicola@fbi.gov", "email_status": "verified",
    "linkedin_url": "http://www.linkedin.com/in/kristi-naternicola",
    "organization": {"name": "Federal Bureau of Investigation (FBI)",
                     "primary_domain": "fbi.gov"},
    "employment_history": [
        {"current": True,
         "organization_name": "Federal Bureau of Investigation (FBI)",
         "start_date": "2015-03-01", "raw_last_updated_at": "2026-07-16"},
        {"current": True,
         "organization_name": "U.S. Department of Justice, Criminal Division",
         "start_date": "1997-03-01", "raw_last_updated_at": "2026-07-16"}],
}
MARKOW = {  # record one year stale, no prior employment rows
    "name": "Eric Markow", "title": "Acting Deputy CIO",
    "city": "Washington", "state": "District of Columbia",
    "country": "United States", "time_zone": "America/New_York",
    "email": "eric.markow@irs.gov", "email_status": "verified",
    "last_refreshed_at": "2025-08-21T03:22:24.000+00:00",
    "linkedin_url": "http://www.linkedin.com/in/eric-markow",
    "organization": {"name": "Internal Revenue Service",
                     "primary_domain": "irs.gov"},
    "employment_history": [
        {"current": True, "organization_name": "Internal Revenue Service",
         "start_date": "2024-09-01", "raw_last_updated_at": "2026-07-20"}],
}
CROCKETT = {  # lowest email confidence in the set, 0.68
    "name": "Ashley Crockett", "title": "Sr. Contracting Officer",
    "city": "San Diego", "state": "California", "country": "United States",
    "time_zone": "America/Los_Angeles",
    "email": "crockett.ashley@irs.gov", "email_status": "extrapolated",
    "extrapolated_email_confidence": 0.68,
    "linkedin_url": "http://www.linkedin.com/in/ashley-walsh-crockett",
    "organization": {"name": "Internal Revenue Service",
                     "primary_domain": "irs.gov"},
    "employment_history": [
        {"current": True, "organization_name": "Internal Revenue Service",
         "start_date": "2024-11-01", "raw_last_updated_at": "2026-07-04"},
        {"current": False, "organization_name": "NAVFAC"}],
}

LEGIT = [(MCCORMICK, FBI), (SWANSTROM, FBI), (SICHKO, FBI), (SLATE, FBI),
         (NATERNICOLA, FBI), (MARKOW, COMPONENT), (CROCKETT, COMPONENT)]


# ---- THE HEADLINE: it catches the one and keeps the rest ------------------ #
def test_the_known_false_positive_is_rejected():
    out = ps.screen_person(LYOVIN, COMPONENT, today=TODAY)
    assert out["verdict"] == ps.REJECT
    ids = {f["rule_id"] for f in out["findings"]}
    assert "S1" in ids and "S2" in ids
    reject = next(f for f in out["findings"] if f["verdict"] == ps.REJECT)
    # the rule prints the evidence it fired on, R1-R4 style
    assert "Fozzy Group" in str(reject["evidence"]["prior_employers"])
    assert reject["evidence"]["us_public_sector_priors"] == []


def test_a_pattern_generated_gov_address_is_not_corroboration():
    """THE HOLE THIS CLOSED. Apollo generates vasyl.lyovin@irs.gov from the
    resolved organisation and marks it verified, which proves the address
    validates and nothing about employment. An earlier build treated a .gov
    mailbox as a guard and the false positive walked straight through."""
    assert LYOVIN["email"].endswith("@irs.gov")
    assert LYOVIN["email_status"] == "verified"
    assert ps.is_federal_domain("irs.gov") is True
    out = ps.screen_person(LYOVIN, COMPONENT, today=TODAY)
    assert out["verdict"] == ps.REJECT
    reject = next(f for f in out["findings"] if f["verdict"] == ps.REJECT)
    assert reject["evidence"]["federal_mailbox_present"] is True


@pytest.mark.parametrize("person,spec", LEGIT,
                         ids=[p["name"] for p, _ in LEGIT])
def test_no_verified_legitimate_person_is_ever_rejected(person, spec):
    """THE LOAD-BEARING TEST. The naive rule set deleted six of these."""
    out = ps.screen_person(person, spec, today=TODAY)
    assert out["verdict"] != ps.REJECT, out["findings"]


# ---- one NEGATIVE CASE per rule, each from a real record shape ----------- #
def test_s1_declines_when_every_geography_field_is_blank():
    """Sichko has no city/state/timezone; Slate has no country at all.
    Absence must contribute nothing."""
    assert ps.s1_geography_disagreement(SICHKO, FBI) is None
    assert ps.s1_geography_disagreement(SLATE, FBI) is None
    assert ps.s1_geography_disagreement({}, FBI) is None


def test_s1_fires_only_on_a_present_foreign_value():
    finding = ps.s1_geography_disagreement(LYOVIN, COMPONENT)
    assert finding["verdict"] == ps.FLAG          # flag, never reject alone
    assert any("Europe/Kiev" in s for s in finding["evidence"]["foreign_signals"])


def test_s2_never_fires_without_a_geography_finding():
    """The compound guard. Without S1 there is no reject, whatever the
    career looks like."""
    assert ps.s2_foreign_career_corroboration(
        LYOVIN, COMPONENT, geography=None) is None


def test_s2_never_corroborates_an_empty_employment_history():
    """Markow and Naternicola have no prior rows. The vacuous-truth reading
    of 'no government employer' is what deleted six real people."""
    geo = {"why": "geography disagreement"}
    for person in (MARKOW, NATERNICOLA):
        thin = dict(person, country="Ukraine", email="x@example.com")
        assert ps.s2_foreign_career_corroboration(
            thin, COMPONENT, geography=geo) is None


def test_s2_spares_an_oconus_federal_employee_with_a_dot_mil_mailbox():
    """A garrison IT director in Germany: foreign geography, real career,
    .mil mailbox. Must survive, or the screen deletes the OCONUS federal
    workforce on sight."""
    wiesbaden = {
        "name": "Garrison IT Director", "country": "Germany",
        "time_zone": "Europe/Berlin", "email": "director@us.army.mil",
        "email_status": "verified",
        "organization": {"name": "US Army Garrison Wiesbaden",
                         "primary_domain": "army.mil"},
        "employment_history": [
            {"current": True, "organization_name": "US Army Garrison Wiesbaden"},
            {"current": False, "organization_name": "US Army"},
            {"current": False, "organization_name": "Department of Defense"}],
    }
    spec = {"target_org": {"kind": "buying_component",
                           "name": "US Army Garrison Wiesbaden"}}
    out = ps.screen_person(wiesbaden, spec, today=TODAY)
    assert out["verdict"] == ps.FLAG              # flagged, not deleted
    assert not any(f["verdict"] == ps.REJECT for f in out["findings"])


def test_s3_declines_on_a_component_domain_under_its_parent_department():
    """fbi.gov under DOJ, irs.gov under Treasury, jpl.nasa.gov under NASA.
    Domain EQUALITY would delete the federal enterprise's subdomains."""
    for domain in ("fbi.gov", "irs.gov", "jpl.nasa.gov", "cbp.dhs.gov",
                   "us.af.mil"):
        person = {"email": "someone@" + domain, "email_status": "verified"}
        assert ps.s3_federal_mailbox_authority(person, COMPONENT) is None


def test_s3_flags_a_commercial_mailbox_but_never_rejects():
    person = {"email": "someone@gmail.com", "email_status": "verified"}
    finding = ps.s3_federal_mailbox_authority(person, COMPONENT)
    assert finding["verdict"] == ps.FLAG


def test_s4_passes_when_one_of_two_concurrent_rows_matches():
    """Naternicola holds FBI and DOJ Criminal Division at once. Detailees
    and joint duty are normal; concurrency is not a conflict."""
    assert ps.s4_employer_concurrency(NATERNICOLA, FBI) is None


def test_s4_declines_when_there_are_no_current_rows_at_all():
    assert ps.s4_employer_concurrency(
        {"employment_history": []}, FBI) is None


def test_s5_is_inert_for_a_paper_holder_spec():
    """A reseller SHOULD have a commercial domain. Flagging it would fire on
    every tier-4 channel spec by construction."""
    person = {"organization": {"name": "DEV TECHNOLOGY GROUP INC",
                               "primary_domain": "devtechnology.com"}}
    assert ps.s5_organisation_self_reference(person, RESELLER) is None


def test_s6_reads_the_job_clock_and_never_the_record_refresh_stamp():
    """Markow's RECORD is a year stale (last_refreshed_at 2025-08-21) but his
    JOB CLAIM is current. A staleness rule on the wrong clock deletes the
    career civil servant for not moving."""
    assert MARKOW["last_refreshed_at"].startswith("2025-08-21")
    assert ps.s6_job_claim_age(MARKOW, COMPONENT, today=TODAY) is None


def test_s6_declines_when_the_job_claim_clock_is_absent():
    assert ps.s6_job_claim_age(
        {"employment_history": [{"current": True}]}, COMPONENT,
        today=TODAY) is None


def test_s7_declines_on_a_record_with_a_blank_city_and_timezone():
    """One empty field is never two empty FAMILIES. Sichko keeps his place."""
    assert ps.s7_unverifiable_record(SICHKO, FBI) is None
    assert ps.s7_unverifiable_record(SLATE, FBI) is None


def test_s7_fires_only_when_two_whole_families_are_empty():
    hollow = {"name": "Nobody"}
    finding = ps.s7_unverifiable_record(hollow, FBI)
    assert finding["verdict"] == ps.FLAG
    assert len(finding["evidence"]["empty_families"]) >= 2


def test_s8_declines_on_a_verified_address():
    for person in (MCCORMICK, SWANSTROM, NATERNICOLA, MARKOW):
        assert ps.s8_email_deliverability(person, COMPONENT) is None


def test_s8_sets_no_confidence_floor():
    """0.68 and 0.70 are treated identically. A floor at 0.70 cuts a Senior
    Contracting Officer and nothing distinguishes her from the 0.70 rows."""
    for person in (CROCKETT, SICHKO, SLATE):
        finding = ps.s8_email_deliverability(person, COMPONENT)
        assert finding["verdict"] == ps.FLAG
        assert "no confidence floor" in finding["evidence"]["note"]


# ---- the receipt shows rules that did NOT fire, not only those that did -- #
def test_the_screen_states_every_rule_and_whether_it_fired():
    out = ps.screen_person(MCCORMICK, FBI, today=TODAY)
    assert {r["rule_id"] for r in out["rules"]} == {
        "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"}
    assert out["rules"][1]["rule_id"] == "S2"
    assert out["rules"][1]["fired"] is False
    assert out["version"].startswith("person_screen.")


def test_exactly_one_rule_is_allowed_to_reject():
    """Pinned so a later edit that promotes a flag to a reject has to change
    this number on purpose."""
    rejecters = [r for r in ps.RULES if r[2] == ps.REJECT]
    assert len(rejecters) == 1
    assert rejecters[0][0] == "S2"
