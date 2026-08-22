"""Focused acceptance tests for the standing event-watch universe."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.event_watch import (
    EVENT_WATCH_SCHEMA_VERSION,
    EventWatchUniverse,
    WatchCadence,
    WatchFrameTags,
    WatchIdentityStatus,
    WatchPriority,
    WatchSourceRole,
    WatchTargetKind,
    load_event_watch_universe,
    select_watch_targets,
    watch_universe_sha256,
)


LOCKED_POLICY_TIER_1_IDS = {
    "act-iac-imagine-nation-elc",
    "afcea-technet-cyber",
    "afcea-technet-augusta",
    "afcea-west",
    "air-space-cyber-conference",
    "ausa-annual-meeting",
    "cdao-federal-ready-summit",
    "fedscoop-it-modernization-summit",
    "geoint-symposium",
    "intelligence-national-security-summit",
    "public-sector-health-it-summit",
    "sea-air-space",
    "space-symposium",
    # The policy makes the verified client-matched Treasury/fraud lane
    # priority-one.  Applicability still controls whether these seeds run.
    "anti-fraud-coalition-events",
    "do-not-pay-vendor-forums",
    "fincyber-today-summit",
    "ncfta-cyber-events",
    "treasury-bfs-industry-vendor-events",
    "treasury-industry-days",
}

LOCKED_POLICY_FAMILY_IDS = {
    "act-iac-acquisition-excellence",
    "act-iac-emerging-technology-summit",
    "act-iac-imagine-nation-elc",
    "afcea-defensive-cyber-ops",
    "afcea-health-it-events",
    "afcea-homeland-security-conference",
    "afcea-technet-augusta",
    "afcea-technet-cyber",
    "afcea-technet-indo-pacific",
    "afcea-west",
    "ai-expo-national-competitiveness",
    "ai-fedlab-summit",
    "air-space-cyber-conference",
    "anti-fraud-coalition-events",
    "army-small-business-conferences",
    "ausa-annual-meeting",
    "aws-public-sector-summit",
    "border-security-expo",
    "cdao-federal-ready-summit",
    "cms-industry-days",
    "data-summit",
    "databricks-public-sector-summit",
    "dgi-ai-for-defense-summit",
    "dgi-cdao-government-summit",
    "dha-industry-days",
    "dhs-small-business-conferences",
    "digital-health-government-summit",
    "do-not-pay-vendor-forums",
    "dodiis-worldwide-conference",
    "doj-technology-day",
    "earth-observation-summit",
    "esri-fedgis",
    "fcw-cyber-summit",
    "federal-identity-forum-expo",
    "federal-news-network-cyber-leaders",
    "fedinsider-ai-summit",
    "fedscoop-ai-summit",
    "fedscoop-it-modernization-summit",
    "fincyber-today-summit",
    "gartner-government-symposiums",
    "geoint-symposium",
    "geospatial-intelligence-forum",
    "google-cloud-public-sector-summit",
    "govcio-ai-summit",
    "govcio-digital-summit",
    "government-procurement-conference",
    "govexec-ai-data-summit",
    "govexec-cxo-summit",
    "gsa-fast-conference",
    "health-datapalooza-government-programming",
    "hhs-osdbu-industry-days",
    "himss-federal-public-sector-health",
    "intelligence-national-security-summit",
    "isc-west-government-programming",
    "meritalk-cyber-central",
    "microsoft-federal-ai-cloud",
    "military-health-system-conference",
    "nasa-industry-technology-days",
    "nasa-sewp-conferences-industry-events",
    "national-homeland-security-conference",
    "navy-gold-coast",
    "ncfta-cyber-events",
    "ncma-world-congress",
    "ndia-cybersecurity-symposium",
    "noaa-industry-days",
    "oasis-plus-industry-events",
    "oracle-government-cloud-summit",
    "psc-federal-acquisition-conference",
    "public-sector-health-it-summit",
    "satellite-conference",
    "sba-govcon-summit",
    "sea-air-space",
    "servicenow-federal-forum",
    "smallsat-conference",
    "snowflake-government-symposium",
    "space-symposium",
    "treasury-bfs-industry-vendor-events",
    "treasury-industry-days",
    "va-healthcare-innovation-summit",
    "va-national-acquisition-center-events",
}

ALL_CLIENT_ORGANIZER_IDS = {
    "act-iac",
    "afcea",
    "fbc",
    "fda-expositions",
    "forum",
    "govevents",
    "govexec",
    "gtra",
    "ncsi",
    "ndia",
}

NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)

SUPPLIED_ORGANIZER_QUERY_NAMES = {
    "ACT-IAC",
    "AFCEA",
    "FDA Expo",
    "FBC",
    "FORUM",
    "GovEvents",
    "GovExec",
    "GTRA",
    "NCSI",
    "NDIA",
}


def _json_clone(
    universe: EventWatchUniverse,
    mutate,
) -> EventWatchUniverse:
    payload = universe.model_dump(mode="json")
    mutate(payload)
    return EventWatchUniverse.model_validate_json(json.dumps(payload))


def _target_keys(rows) -> tuple[tuple[str, str], ...]:
    return tuple((row.kind.value, row.id) for row in rows)


def test_curated_registry_loads_supplied_universe_without_event_claims():
    universe = load_event_watch_universe()

    assert universe.schema_version == EVENT_WATCH_SCHEMA_VERSION
    assert len(universe.organizers) == 68
    assert {
        event.event_id for event in universe.event_series
    } == LOCKED_POLICY_FAMILY_IDS
    assert len(universe.event_series) == len(LOCKED_POLICY_FAMILY_IDS) == 80
    assert {
        event.event_id
        for event in universe.event_series
        if event.priority is WatchPriority.TIER_1
    } == LOCKED_POLICY_TIER_1_IDS

    query_names = {
        value
        for organizer in universe.organizers
        for value in (
            organizer.canonical_name,
            *organizer.query_aliases,
        )
    }
    assert SUPPLIED_ORGANIZER_QUERY_NAMES <= query_names

    payload = universe.model_dump(mode="json")

    def keys(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                yield key
                yield from keys(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from keys(nested)

    forbidden_claim_fields = {
        "date",
        "due_date",
        "attendance",
        "sponsor",
        "sponsor_range",
        "decision_density",
    }
    assert not (set(keys(payload)) & forbidden_claim_fields)


def test_registry_models_are_frozen_and_forbid_unknown_fields():
    universe = load_event_watch_universe()

    with pytest.raises(ValidationError, match="frozen"):
        universe.revision_id = "changed"

    payload = universe.model_dump(mode="json")
    payload["unreviewed_field"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EventWatchUniverse.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "host",
    (
        "https://afcea.org",
        "github.io",
        "localhost",
        "127.0.0.1",
    ),
)
def test_official_hosts_reuse_exact_trusted_host_validation(host: str):
    universe = load_event_watch_universe()

    def mutate(payload):
        payload["organizers"][0]["official_hosts"] = [host]

    with pytest.raises(ValidationError, match="official_hosts|trusted organizer"):
        _json_clone(universe, mutate)


def test_pending_identity_cannot_silently_authorize_a_host():
    universe = load_event_watch_universe()

    def mutate(payload):
        row = next(
            item for item in payload["organizers"]
            if item["organizer_id"] == "dgi"
        )
        assert row["identity_status"] == "pending"
        row["official_hosts"] = ["fbcinc.com"]

    with pytest.raises(ValidationError, match="pending organizer identities"):
        _json_clone(universe, mutate)


def test_govevents_is_discovery_only_and_never_an_authoritative_host():
    universe = load_event_watch_universe()
    govevents = next(
        organizer for organizer in universe.organizers
        if organizer.organizer_id == "govevents"
    )

    assert govevents.role is WatchSourceRole.DISCOVERY_ONLY
    assert govevents.identity_status is WatchIdentityStatus.VERIFIED
    assert govevents.official_hosts == (
        "govevents.com", "www.govevents.com")
    assert "govevents.com" not in universe.authoritative_hosts
    assert "afcea.org" in universe.authoritative_hosts


def test_supplied_organizer_links_are_bound_as_authoritative_hosts():
    universe = load_event_watch_universe()

    assert {
        "ncsi.com",
        "www.ncsi.com",
        "fdaexpo.com",
        "www.fdaexpo.com",
        "fbcinc.com",
        "www.fbcinc.com",
        "events.govforum.io",
        "gtra.org",
        "actiac.org",
        "afcea.org",
        "ndia.org",
        "govexec.com",
    } <= set(universe.authoritative_hosts)
    assert "govevents.com" not in universe.authoritative_hosts


def test_aliases_ids_and_references_are_globally_unambiguous():
    universe = load_event_watch_universe()

    organizer_ids = [row.organizer_id for row in universe.organizers]
    event_ids = [row.event_id for row in universe.event_series]
    assert len(organizer_ids) == len(set(organizer_ids))
    assert len(event_ids) == len(set(event_ids))
    assert set(organizer_ids).isdisjoint(event_ids)
    assert {row.organizer_id for row in universe.event_series} <= set(
        organizer_ids
    )

    target_organizers = tuple(
        row for row in universe.organizers if row.include_as_target
    )
    names = [
        name.casefold()
        for row in (*target_organizers, *universe.event_series)
        for name in (row.canonical_name, *row.query_aliases)
    ]
    assert len(names) == len(set(names))


def test_cross_kind_id_and_query_name_collisions_are_rejected():
    universe = load_event_watch_universe()

    def collide_id(payload):
        payload["event_series"][0]["event_id"] = (
            payload["organizers"][0]["organizer_id"]
        )

    with pytest.raises(ValidationError, match="must be disjoint"):
        _json_clone(universe, collide_id)

    def collide_name(payload):
        payload["event_series"][0]["canonical_name"] = (
            payload["organizers"][0]["canonical_name"]
        )

    with pytest.raises(ValidationError, match="watch target query name"):
        _json_clone(universe, collide_name)


def test_frame_tags_are_normalized_deduplicated_and_ordered():
    frame = WatchFrameTags.model_validate_json(json.dumps({
        "mission_tags": [" AI / ML ", "ai-ml", "Cyber Security"],
        "buyer_tags": ["Federal CIO", "federal-cio"],
        "agency_tags": ["DoD"],
        "capability_tags": ["Data & Analytics"],
    }))

    assert frame.mission_tags == ("ai-ml", "cyber-security")
    assert frame.buyer_tags == ("federal-cio",)
    assert frame.agency_tags == ("dod",)
    assert frame.capability_tags == ("data-analytics",)


def test_selection_is_a_complete_ordered_partition_with_required_projection():
    universe = load_event_watch_universe()
    selection = select_watch_targets(
        universe,
        WatchFrameTags(capability_tags=("geospatial",)),
        as_of=NOW,
        last_checked_at={},
    )

    applicable_keys = _target_keys(selection.applicable)
    excluded_keys = _target_keys(selection.scope_excluded)
    assert applicable_keys == tuple(sorted(applicable_keys))
    assert excluded_keys == tuple(sorted(excluded_keys))
    assert set(applicable_keys).isdisjoint(excluded_keys)
    assert len(applicable_keys) + len(excluded_keys) == (
        len(universe.organizers) - 2 + len(universe.event_series)
    )
    assert {
        row.id for row in selection.applicable
        if row.kind is WatchTargetKind.EVENT_SERIES
    } == {
        "earth-observation-summit",
        "esri-fedgis",
        "geoint-symposium",
        "geospatial-intelligence-forum",
        "noaa-industry-days",
    }

    target = next(
        row for row in selection.applicable
        if row.id == "geoint-symposium"
    )
    assert target.canonical_name == "GEOINT Symposium"
    assert target.priority is WatchPriority.TIER_1
    assert target.role is WatchSourceRole.ORGANIZER
    assert target.identity_status is WatchIdentityStatus.VERIFIED
    assert target.official_hosts == ("usgif.org",)
    assert target.query_terms == (
        "GEOINT Symposium",
        "USGIF GEOINT Symposium",
    )
    assert target.capability_tags == (
        "artificial-intelligence",
        "data-analytics",
        "geospatial",
    )


def test_all_client_organizers_stay_in_scope_but_series_require_tags():
    universe = load_event_watch_universe()
    empty = select_watch_targets(
        universe,
        WatchFrameTags(),
        as_of=NOW,
        last_checked_at={},
    )
    assert {row.id for row in empty.applicable} == ALL_CLIENT_ORGANIZER_IDS
    assert empty.search_due == empty.applicable
    assert empty.cadence_deferred == ()
    assert not {
        row.id for row in empty.applicable
        if row.kind is WatchTargetKind.EVENT_SERIES
    }
    target_count = sum(
        organizer.include_as_target for organizer in universe.organizers
    ) + len(universe.event_series)
    assert len(empty.scope_excluded) == (
        target_count - len(ALL_CLIENT_ORGANIZER_IDS)
    )

    forward = select_watch_targets(
        universe,
        WatchFrameTags(
            mission_tags=("cybersecurity", "space"),
            agency_tags=("dod", "hhs"),
        ),
        as_of=NOW,
        last_checked_at={},
    )
    reversed_input = select_watch_targets(
        universe,
        WatchFrameTags(
            mission_tags=("space", "cybersecurity"),
            agency_tags=("hhs", "dod"),
        ),
        as_of=NOW,
        last_checked_at={},
    )
    assert forward == reversed_input


def test_priority_never_changes_applicability_or_every_run_cadence():
    universe = load_event_watch_universe()
    frame = WatchFrameTags(mission_tags=("space",))

    def mutate(payload):
        for row in payload["organizers"]:
            if row["organizer_id"] == "space-foundation":
                row["priority"] = "tier_1"
        for row in payload["event_series"]:
            if row["event_id"] == "space-symposium":
                row["priority"] = "watch"

    reprioritized = _json_clone(universe, mutate)
    baseline = select_watch_targets(
        universe,
        frame,
        as_of=NOW,
        last_checked_at={},
    )
    changed = select_watch_targets(
        reprioritized,
        frame,
        as_of=NOW,
        last_checked_at={},
    )

    assert {
        (row.kind, row.id) for row in baseline.applicable
    } == {
        (row.kind, row.id) for row in changed.applicable
    }
    assert next(
        row for row in baseline.applicable
        if row.id == "space-symposium"
    ).cadence is WatchCadence.EVERY_RUN
    assert next(
        row for row in changed.applicable
        if row.id == "space-symposium"
    ).cadence is WatchCadence.EVERY_RUN
    assert baseline.search_due == baseline.applicable
    assert changed.search_due == changed.applicable
    assert baseline.cadence_deferred == changed.cadence_deferred == ()


def test_every_applicable_target_runs_even_when_recently_checked():
    universe = load_event_watch_universe()
    frame = WatchFrameTags(
        mission_tags=("space",),
        capability_tags=("geospatial",),
    )
    initial = select_watch_targets(
        universe,
        frame,
        as_of=NOW,
        last_checked_at={},
    )
    recently_checked = {
        row.id: NOW - timedelta(minutes=1)
        for row in initial.applicable
    }
    current = select_watch_targets(
        universe,
        frame,
        as_of=NOW,
        last_checked_at=recently_checked,
    )

    assert current.applicable == initial.applicable
    assert current.search_due == current.applicable
    assert current.cadence_deferred == ()
    assert {
        row.priority for row in current.search_due
    } == {WatchPriority.TIER_1, WatchPriority.WATCH}


def test_cadence_inputs_are_aware_known_and_not_in_the_future():
    universe = load_event_watch_universe()
    frame = WatchFrameTags()

    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        select_watch_targets(
            universe,
            frame,
            as_of=NOW.replace(tzinfo=None),
            last_checked_at={},
        )
    with pytest.raises(ValueError, match="unknown watch target"):
        select_watch_targets(
            universe,
            frame,
            as_of=NOW,
            last_checked_at={"not-a-target": NOW},
        )
    with pytest.raises(ValueError, match="cannot be after as_of"):
        select_watch_targets(
            universe,
            frame,
            as_of=NOW,
            last_checked_at={"act-iac": NOW + timedelta(seconds=1)},
        )


def test_pending_organizers_and_series_project_as_discovery_only_identities():
    universe = load_event_watch_universe()
    selection = select_watch_targets(
        universe,
        WatchFrameTags(agency_tags=("dod",)),
        as_of=NOW,
        last_checked_at={},
    )
    dgi = next(row for row in selection.applicable if row.id == "dgi")
    cdao = next(
        row for row in selection.applicable
        if row.id == "cdao-federal-ready-summit"
    )

    assert dgi.identity_status is WatchIdentityStatus.PENDING
    assert cdao.identity_status is WatchIdentityStatus.PENDING
    assert dgi.official_hosts == cdao.official_hosts == ()


def test_non_target_organizers_do_not_duplicate_their_event_series():
    universe = load_event_watch_universe()
    selection = select_watch_targets(
        universe,
        WatchFrameTags(agency_tags=("hhs",)),
        as_of=NOW,
        last_checked_at={},
    )
    ids = {row.id for row in (*selection.applicable, *selection.scope_excluded)}

    assert "public-sector-health-it" not in ids
    assert "identity-forum" not in ids
    assert "public-sector-health-it-summit" in ids
    assert "federal-identity-forum-expo" in ids


def test_registry_sha256_is_over_exact_bytes(tmp_path):
    source_bytes = (
        __import__(
            "agents.candidate_review_v1.event_watch",
            fromlist=["DEFAULT_EVENT_WATCH_UNIVERSE_PATH"],
        )
        .DEFAULT_EVENT_WATCH_UNIVERSE_PATH
        .read_bytes()
    )
    original = tmp_path / "original.json"
    whitespace_changed = tmp_path / "whitespace-changed.json"
    original.write_bytes(source_bytes)
    whitespace_changed.write_bytes(source_bytes + b"\n")

    assert watch_universe_sha256(original) == hashlib.sha256(
        source_bytes
    ).hexdigest()
    assert watch_universe_sha256(whitespace_changed) != (
        watch_universe_sha256(original)
    )
    assert load_event_watch_universe(original) == (
        load_event_watch_universe(whitespace_changed)
    )
