"""DRAFT tests (land at tests/test_recompete_context.py).

The ODOS worked example, end to end: a fixture play on 70SBUR22F00000113
renders RECOMPETE KNOWN referencing the ODOS IV forecast; the same fixture
minus the calendar/forecast entry renders NO RECOMPETE FOUND in client
HTML; an unindexed program family demotes to CALENDAR GAP, INTERNAL
sidecar only.
"""
from agents.reports.recompete_context import (
    PlayKey,
    normalize_program_name,
    successor_events,
)

ODOS_KEY = PlayKey(award_id="70SBUR22F00000113",
                   idv_piid="HHSN316201200193W",
                   program_name="USCIS ODOS III / 3.1",
                   agency="Department of Homeland Security")

#: the ODOS IV forecast fixture: five-year multiple-award small business
#: set-aside via GSA MAS, publicly forecast on APFS (worked example)
ODOS_IV_FORECAST = {
    "source": "dhs_apfs", "source_id": "F2026075501",
    "agency": "DHS", "component": "USCIS",
    "title": "Outcome-based Delivery and DevSecOps Services IV (ODOS IV)",
    "description": ("Full recompete of Outcome-based Delivery and DevSecOps "
                    "Services III (ODOS III), currently performed under task "
                    "order 70SBUR22F00000113. Five-year multiple-award small "
                    "business set-aside via GSA MAS."),
    "naics_code": "541512",
    "anticipated_solicitation": "2026-11-15",
    "url": "https://apfs-cloud.dhs.gov/forecast/75501",
    "retrieved_at": "2026-07-17T15:06:03+00:00",
}

USCIS_CAL = {"generated": "2026-07-17", "window_months": 24, "attack": [
    {"award_id": "70SBUR22F00000113", "awarding_agency":
     "Department of Homeland Security", "awarding_office": "USCIS",
     "pop_end": "2026-09-24", "obligated": 174537374.57,
     "description": "OUTCOME-BASED DELIVERY AND DEVSECOPS SERVICES (ODOS) III",
     "naics": "541512"}], "defend": []}


def test_normalization_collapses_generations():
    assert normalize_program_name("ODOS III") == "odos"
    assert normalize_program_name("ODOS IV") == "odos"
    a = normalize_program_name("Outcome-based Delivery and DevSecOps Services IV (ODOS IV)")
    assert "odos" in a.split() and "iv" not in a.split()


def test_p1_award_exact_beats_everything():
    ctx = successor_events(ODOS_KEY, USCIS_CAL, [ODOS_IV_FORECAST])
    assert ctx.state == "known"
    assert ctx.matched_by == "award_idv"
    assert ctx.event.tier == "record"
    assert ctx.event.kind == "recompete"
    assert ctx.event.source_record_id == "F2026075501"


def test_calendar_pop_end_is_expiry_not_recompete():
    ctx = successor_events(ODOS_KEY, USCIS_CAL, [])

    assert ctx.state == "known"
    assert ctx.event.kind == "expiry"
    assert ctx.event.date == "2026-09-24"


def test_generic_forecast_is_neutral_until_source_says_recompete():
    generic = dict(
        ODOS_IV_FORECAST,
        title="Outcome-based Delivery Services acquisition forecast",
        description=(
            "Planning record for predecessor task order "
            "70SBUR22F00000113. Acquisition approach remains under review."
        ),
    )

    ctx = successor_events(ODOS_KEY, None, [generic])

    assert ctx.state == "known"
    assert ctx.event.kind == "forecast"
    assert ctx.event.source_record_id == "F2026075501"


def test_p1_forecast_citing_award_id_is_record_backed():
    ctx = successor_events(ODOS_KEY, None, [ODOS_IV_FORECAST])
    assert ctx.state == "known" and ctx.matched_by == "award_idv"
    assert ctx.event.source_system == "apfs"
    assert ctx.event.source_record_id == "F2026075501"
    assert ctx.event.source_url == "https://apfs-cloud.dhs.gov/forecast/"
    assert ctx.event.tier == "record"
    assert ctx.event.retrieved_at == "2026-07-17T15:06:03+00:00"


def test_p3_name_stem_is_always_analyst():
    fuzzy = dict(ODOS_IV_FORECAST,
                 description="Five-year multiple-award SB set-aside via GSA MAS.")
    ctx = successor_events(
        PlayKey(program_name="USCIS ODOS III", agency="DHS"), None, [fuzzy])
    assert ctx.state == "known" and ctx.matched_by == "name_stem"
    assert ctx.event.tier == "analyst"


def test_p3_webeoc_records_boilerplate_cannot_join_axon_program():
    unrelated = {
        "source": "dhs_apfs",
        "source_id": "F-WEBEOC",
        "title": "WebEOC Unified Command Platform migration",
        "description": (
            "Move the existing WebEOC environment to a hosted platform with "
            "reporting, analytics, and records-retention capabilities."
        ),
        "anticipated_solicitation": "2027-01-15",
    }

    ctx = successor_events(
        PlayKey(program_name="AXON RECORDS & STANDARDS"),
        None,
        [unrelated],
    )

    assert ctx.state == "gap"
    assert ctx.event is None


def test_p3_purpose_boilerplate_cannot_join_renewal_to_data_science():
    data_science = {
        "source": "dhs_apfs",
        "source_id": "F-DATA-SCIENCE",
        "title": "Data Science Services",
        "description": (
            "The purpose of this contract is to obtain data science services "
            "and collaborate with an in-house analytics team."
        ),
        "anticipated_solicitation": "2027-03-01",
    }
    renewal = (
        "THE PURPOSE OF THIS REQUIREMENT IS FOR THE RENEWAL OF HARDWARE AND "
        "SOFTWARE MAINTENANCE AND LICENSE FOR RIVERBED BRAND NAME PRODUCTS"
    )

    ctx = successor_events(
        PlayKey(program_name=renewal), None, [data_science])

    assert ctx.state == "gap"
    assert ctx.event is None


def test_p3_exact_stylized_token_and_two_distinctive_words_still_join():
    webeoc = successor_events(
        PlayKey(program_name="TSA WebEOC III"),
        None,
        [{
            "source": "dhs_apfs",
            "source_id": "F-WEBEOC",
            "title": "WebEOC cloud migration follow-on",
        }],
    )
    two_words = successor_events(
        PlayKey(program_name="Falcon Beacon III"),
        None,
        [{
            "source": "dhs_apfs",
            "source_id": "F-FALCON",
            "title": "Falcon Beacon IV follow-on",
        }],
    )
    exact_brand = successor_events(
        PlayKey(program_name="DYNATRACE SOFTWARE LICENSES"),
        None,
        [{
            "source": "dhs_apfs",
            "source_id": "F-DYNATRACE",
            "title": "Dynatrace renewal follow-on",
        }],
    )

    assert webeoc.state == "known"
    assert webeoc.event.source_record_id == "F-WEBEOC"
    assert two_words.state == "known"
    assert two_words.event.source_record_id == "F-FALCON"
    assert exact_brand.state == "known"
    assert exact_brand.event.source_record_id == "F-DYNATRACE"
    assert {webeoc.event.tier, two_words.event.tier,
            exact_brand.event.tier} == {"analyst"}


def test_p3_one_ordinary_distinctive_token_is_not_enough():
    ctx = successor_events(
        PlayKey(program_name="Falcon Records"),
        None,
        [{
            "source": "dhs_apfs",
            "source_id": "F-ARCHIVE",
            "title": "Falcon Archive follow-on",
        }],
    )

    assert ctx.state == "gap"
    assert ctx.event is None


def test_p3_stem_winner_is_deterministic_under_forecast_reordering():
    rows = [{
        "source": "dhs_apfs",
        "source_id": source_id,
        "title": "Falcon Beacon follow-on",
        "anticipated_solicitation": date,
    } for source_id, date in (
        ("F-B", "2027-04-01"),
        ("F-A", "2027-05-01"),
    )]
    key = PlayKey(program_name="Falcon Beacon III")

    forward = successor_events(key, None, rows)
    reversed_rows = successor_events(key, None, list(reversed(rows)))

    assert forward.event.source_record_id == "F-A"
    assert reversed_rows.event.source_record_id == "F-A"


def test_not_found_requires_covered_family():
    covered_cal = {"generated": "2026-07-17", "attack": [
        {"award_id": "OTHER123", "awarding_agency":
         "Department of Homeland Security", "pop_end": "2027-01-01",
         "description": "unrelated USCIS lane"}], "defend": []}
    ctx = successor_events(
        PlayKey(award_id="70SBURXXF000", program_name="No Such Program",
                agency="Department of Homeland Security"), covered_cal, [])
    assert ctx.state == "not_found"
    assert "no successor event" in ctx.reason


def test_gap_when_no_calendar_or_family_unindexed():
    assert successor_events(ODOS_KEY, None, []).state == "gap"
    foreign_cal = {"generated": "2026-07-17", "attack": [
        {"award_id": "N0002420C5605", "awarding_agency":
         "Department of Defense", "pop_end": "2026-08-03",
         "description": "CONSOLES"}], "defend": []}
    ctx = successor_events(ODOS_KEY, foreign_cal, [])
    assert ctx.state == "gap"
    assert "outside the calendar" in ctx.reason
