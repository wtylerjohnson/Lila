"""Offline acceptance tests for Candidate Review v1 event discovery.

The discovery layer builds a deterministic query census and records what each
injected provider returned.  Its output is deliberately non-authoritative:
``EventLead`` rows must pass a later verification layer before they can become
``EvidenceRecord`` or ``EventRecord`` objects.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import random

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    ArtifactBinding,
    CoverageState,
    EventRecord,
    EvidenceRecord,
    SourceIdentity,
)
from agents.candidate_review_v1.event_research import (
    CLIENT_EVENT_QUERY_FAMILIES,
    CLIENT_EVENT_SOURCE_CLASSES,
    EventLead,
    EventQueryAttempt,
    EventQueryFamily,
    EventQueryManifest,
    EventQuerySpec,
    EventResearchFrame,
    EventSearchResponse,
    EventSourceClass,
    build_event_query_manifest,
    run_event_discovery,
)
from agents.candidate_review_v1.event_watch import (
    WatchFrameTags,
    load_event_watch_universe,
    select_watch_targets,
    watch_universe_sha256,
)
from agents.candidate_review_v1.pipeline import (
    RESEARCH_SNAPSHOT_CHANGE_BUDGET,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleWatchFrame,
    build_vehicle_query_manifest,
)


NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
WINDOW_START = date(2026, 7, 22)
HASH = "a" * 64
OTHER_HASH = "b" * 64
FRAME_HASH = "c" * 64


def _binding(
    client_id: str = "testco",
    *,
    scope_hash: str = HASH,
) -> ArtifactBinding:
    names = {"testco": "TestCo", "otherco": "OtherCo"}
    return ArtifactBinding(
        client_id=client_id,
        client_name=names[client_id],
        run_id="run-1",
        scope_designator="all-federal",
        scope_sha256=scope_hash,
        profile_sha256="d" * 64,
        evidence_snapshot_sha256="e" * 64,
    )


def _frame(**changes) -> EventResearchFrame:
    values = {
        "binding": _binding(),
        "revision_sha256": FRAME_HASH,
        "capabilities_missions": (
            "records management", "workflow automation",
            "public safety", "digital service delivery",
        ),
        "priority_agencies_components": (
            "Department of State", "GSA",
            "Diplomatic Security", "Technology Transformation",
        ),
        "naics_psc": ("541512", "541519", "DA10", "R408"),
        "candidate_accounts": ("State Department", "GSA"),
        "vehicle_partner_competitor_ecosystem": (
            "GSA MAS", "OASIS+", "Prime Alpha", "Prime Beta",
            "Partner Alpha", "Partner Beta",
            "Competitor Alpha", "Competitor Beta",
        ),
        "buyer_communities": ("Federal CIO", "Acquisition executives"),
        "policy_budget_themes": ("AI modernization", "zero trust"),
        "trusted_organizer_domains": (
            "afcea.org", "ncmahq.org", "events.gsa.gov"),
    }
    values.update(changes)
    return EventResearchFrame(**values)


def _manifest(
    frame: EventResearchFrame | None = None,
    *,
    window_start: date = WINDOW_START,
) -> EventQueryManifest:
    as_of = datetime(
        window_start.year,
        window_start.month,
        window_start.day,
        18,
        0,
        tzinfo=timezone.utc,
    )
    return build_event_query_manifest(
        frame or _frame(),
        as_of,
    )


def _watch_manifest(
    frame_tags: WatchFrameTags,
    *,
    digest: str | None = None,
    last_checked_at: dict[str, datetime] | None = None,
) -> EventQueryManifest:
    universe = load_event_watch_universe()
    selection = select_watch_targets(
        universe,
        frame_tags,
        as_of=NOW,
        last_checked_at=last_checked_at or {},
    )
    return build_event_query_manifest(
        _frame(),
        NOW,
        watch_selection=selection,
        watch_universe_digest=digest or watch_universe_sha256(),
    )


def _single_family_frame() -> EventResearchFrame:
    return _frame(
        capabilities_missions=("records management",),
        priority_agencies_components=(),
        naics_psc=(),
        candidate_accounts=(),
        vehicle_partner_competitor_ecosystem=(),
        buyer_communities=(),
        policy_budget_themes=(),
    )


def _lead(
    query: EventQuerySpec,
    suffix: str = "1",
    *,
    discovered_at: datetime = NOW,
    lead_id: str | None = None,
    query_id: str | None = None,
    source_class: EventSourceClass | None = None,
) -> EventLead:
    return EventLead(
        lead_id=lead_id or f"LEAD-{query.query_id}-{suffix}",
        query_id=query_id or query.query_id,
        source_class=source_class or query.source_class,
        discovered_at=discovered_at,
        title=f"Federal technology event {suffix}",
        url=f"https://events.example.org/{query.query_id}/{suffix}",
        claimed_date="September 15, 2026",
        claimed_location="Washington, DC",
        claimed_organizer="Example organizer",
    )


def _queries_by_family(
    manifest: EventQueryManifest,
    family: EventQueryFamily,
) -> tuple[EventQuerySpec, ...]:
    return tuple(
        query for query in manifest.queries
        if query.query_family == family
    )


def test_manifest_is_deterministic_complete_seven_by_four_cross_product():
    manifest = _manifest()

    assert len(manifest.queries) == 7 * 4
    assert len({query.query_id for query in manifest.queries}) == 28
    assert {
        (query.query_family, query.source_class)
        for query in manifest.queries
    } == {
        (family, source_class)
            for family in CLIENT_EVENT_QUERY_FAMILIES
            for source_class in CLIENT_EVENT_SOURCE_CLASSES
    }
    assert all(query.required for query in manifest.queries)
    assert all(query.query_text.strip() for query in manifest.queries)


def test_standing_watch_manifest_closes_every_registry_target_and_binds_digest():
    universe = load_event_watch_universe()
    manifest = _watch_manifest(
        WatchFrameTags(capability_tags=("geospatial",))
    )

    assert len(manifest.queries) > 28
    expected_target_count = sum(
        organizer.include_as_target for organizer in universe.organizers
    ) + len(universe.event_series)
    assert len(manifest.watch_target_ids) == expected_target_count == 146
    assert set(manifest.applicable_watch_target_ids).isdisjoint(
        manifest.scope_excluded_watch_target_ids
    )
    assert set(manifest.applicable_watch_target_ids) | set(
        manifest.scope_excluded_watch_target_ids
    ) == set(manifest.watch_target_ids)
    assert manifest.search_due_watch_target_ids == (
        manifest.applicable_watch_target_ids
    )
    assert manifest.cadence_deferred_watch_target_ids == ()
    assert manifest.watch_universe_sha256 == watch_universe_sha256()
    assert "usgif.org" in manifest.trusted_organizer_domains
    assert "fbcinc.com" in manifest.trusted_organizer_domains
    watch_priorities = tuple(
        query.watch_priority
        for query in manifest.queries
        if query.watch_target_id is not None
    )
    assert watch_priorities == tuple(sorted(
        watch_priorities,
        key={"tier_1": 0, "watch": 1}.__getitem__,
    ))


def test_standing_watch_scope_exclusions_are_explicit_nonattempted_queries():
    manifest = _watch_manifest(
        WatchFrameTags(capability_tags=("geospatial",))
    )
    calls: list[str] = []

    def searcher(query: EventQuerySpec):
        calls.append(query.query_id)
        return ()

    result = run_event_discovery(manifest, searcher, NOW)
    excluded = set(manifest.scope_excluded_watch_target_ids)
    excluded_specs = tuple(
        query for query in manifest.queries
        if query.watch_target_id in excluded
    )
    attempts = {row.query_id: row for row in result.attempts}

    assert excluded_specs
    assert all(not row.required for row in excluded_specs)
    assert all(
        attempts[row.query_id].state is CoverageState.SCOPE_EXCLUDED
        for row in excluded_specs
    )
    assert all(
        attempts[row.query_id].public_detail
        == "This standing watch target is outside the approved client frame."
        for row in excluded_specs
    )
    assert not ({row.query_id for row in excluded_specs} & set(calls))


def test_govevents_watch_target_is_discovery_only_not_authoritative():
    manifest = _watch_manifest(
        WatchFrameTags(capability_tags=("professional-services",))
    )
    rows = tuple(
        query for query in manifest.queries
        if query.watch_target_id == "govevents"
    )

    assert len(rows) == 1
    assert rows[0].source_class is EventSourceClass.DISCOVERY_INDEX
    assert rows[0].source_role == "discovery_only"
    assert rows[0].watch_identity_status == "verified"
    assert "discovery hosts: govevents.com, www.govevents.com" in (
        rows[0].query_text
    )
    assert "official hosts" not in rows[0].query_text
    assert "govevents.com" not in manifest.trusted_organizer_domains


def test_policy_organizers_are_due_for_every_client_without_event_leakage():
    manifest = _watch_manifest(WatchFrameTags())
    expected = {
        "act-iac", "afcea", "fbc", "fda-expositions", "forum",
        "govevents", "govexec", "gtra", "ncsi", "ndia",
    }

    assert set(manifest.applicable_watch_target_ids) == expected
    assert set(manifest.search_due_watch_target_ids) == expected
    assert not {
        query.watch_target_id
        for query in manifest.queries
        if query.watch_target_kind == "event_series" and query.required
    }


def test_pending_organizer_identity_can_only_generate_discovery_queries():
    manifest = _watch_manifest(WatchFrameTags(agency_tags=("dod",)))
    pending_ids = {
        "dgi",
        "dgi-ai-for-defense-summit",
        "dgi-cdao-government-summit",
        "cdao-federal-ready-operator",
        "cdao-federal-ready-summit",
    }
    rows = tuple(
        query for query in manifest.queries
        if query.watch_target_id in pending_ids
    )

    assert rows
    assert {row.watch_identity_status for row in rows} == {"pending"}
    assert {row.source_class for row in rows} == {
        EventSourceClass.DISCOVERY_INDEX
    }
    assert all(row.official_hosts == () for row in rows)


def test_recent_checks_do_not_defer_any_applicable_standing_watch_query():
    initial = _watch_manifest(
        WatchFrameTags(capability_tags=("geospatial",))
    )
    last_checked = {
        target_id: NOW - timedelta(days=5)
        for target_id in initial.applicable_watch_target_ids
    }
    manifest = _watch_manifest(
        WatchFrameTags(capability_tags=("geospatial",)),
        last_checked_at=last_checked,
    )
    calls: list[str] = []

    def searcher(query: EventQuerySpec):
        calls.append(query.query_id)
        return ()

    result = run_event_discovery(manifest, searcher, NOW)
    attempts = {row.query_id: row for row in result.attempts}
    applicable = set(manifest.applicable_watch_target_ids)
    excluded = set(manifest.scope_excluded_watch_target_ids)
    applicable_specs = tuple(
        row for row in manifest.queries if row.watch_target_id in applicable
    )
    excluded_specs = tuple(
        row for row in manifest.queries if row.watch_target_id in excluded
    )

    assert applicable_specs
    assert manifest.cadence_deferred_watch_target_ids == ()
    assert all(row.required for row in applicable_specs)
    assert all(row.nonrequired_reason is None for row in applicable_specs)
    assert all(attempts[row.query_id].state is CoverageState.RETURNED
               for row in applicable_specs)
    assert all(attempts[row.query_id].state is CoverageState.SCOPE_EXCLUDED
               for row in excluded_specs)
    assert {row.query_id for row in applicable_specs} <= set(calls)
    assert not ({row.query_id for row in excluded_specs} & set(calls))


def test_watch_universe_digest_invalidates_manifest_and_cache_identity():
    frame_tags = WatchFrameTags(capability_tags=("geospatial",))
    original = _watch_manifest(frame_tags)
    changed = _watch_manifest(frame_tags, digest="0" * 64)

    assert changed.watch_target_ids == original.watch_target_ids
    assert changed.queries == original.queries
    assert changed.manifest_id != original.manifest_id
    assert changed.cache_key != original.cache_key


def test_max_event_and_vehicle_censuses_fit_shared_coverage_budget():
    universe = load_event_watch_universe()
    watch_rows = (*universe.organizers, *universe.event_series)

    def all_tags(field_name: str) -> tuple[str, ...]:
        return tuple(sorted({
            value
            for row in watch_rows
            for value in getattr(row, field_name)
        }))

    selection = select_watch_targets(
        universe,
        WatchFrameTags(
            mission_tags=all_tags("mission_tags"),
            buyer_tags=all_tags("buyer_tags"),
            agency_tags=all_tags("agency_tags"),
            capability_tags=all_tags("capability_tags"),
        ),
        as_of=NOW,
        last_checked_at={},
    )
    event_manifest = build_event_query_manifest(
        _frame(),
        NOW,
        watch_selection=selection,
        watch_universe_digest=watch_universe_sha256(),
    )
    width = 128
    candidate_width = 1024
    vehicle_manifest = build_vehicle_query_manifest(
        VehicleWatchFrame(
            binding=_binding(),
            revision_sha256="f" * 64,
            approved_at=NOW - timedelta(days=1),
            window_start=date(2023, 10, 1),
            client_terms=tuple(
                f"client term {index:03d}" for index in range(width)
            ),
            agencies=tuple(
                f"Agency {index:03d}" for index in range(width)
            ),
            naics_codes=tuple(
                str(100000 + index) for index in range(width)
            ),
            psc_codes=tuple(f"A{index:03d}" for index in range(width)),
            candidate_source_identities=tuple(
                SourceIdentity(
                    source_system="sam.gov",
                    record_id=f"notice-{index:03d}",
                )
                    for index in range(candidate_width)
            ),
            known_parent_idvs=tuple(
                SourceIdentity(
                    source_system="usaspending.gov",
                    record_id=f"idv-{index:03d}",
                )
                for index in range(width)
            ),
        ),
        NOW,
    )

    assert selection.scope_excluded == ()
    assert len(event_manifest.queries) == 300
    assert len(vehicle_manifest.queries) == 4609
    assert len(event_manifest.queries) + len(vehicle_manifest.queries) \
        < CONTENT_BUDGETS["coverage_records"] == 8192
    assert len(event_manifest.queries) + len(vehicle_manifest.queries) \
        < RESEARCH_SNAPSHOT_CHANGE_BUDGET == 8192


def test_manifest_is_identical_when_every_frame_tuple_is_shuffled():
    frame = _frame()
    expected = _manifest(frame)
    tuple_fields = (
        "capabilities_missions",
        "priority_agencies_components",
        "naics_psc",
        "candidate_accounts",
        "vehicle_partner_competitor_ecosystem",
        "buyer_communities",
        "policy_budget_themes",
        "trusted_organizer_domains",
    )

    for seed in range(8):
        values = frame.model_dump(mode="python")
        for field in tuple_fields:
            shuffled = list(values[field])
            random.Random(f"{seed}:{field}").shuffle(shuffled)
            values[field] = tuple(shuffled)
        actual = _manifest(EventResearchFrame.model_validate(values))
        assert actual == expected


@pytest.mark.parametrize(
    "domain",
    (
        "com",
        "co.uk",
        "127.0.0.1",
        "localhost",
        "bad host.example",
        "github.io",
        "appspot.com",
        "pages.dev",
        "blogspot.com",
        "readthedocs.io",
        "surge.sh",
    ),
)
def test_trusted_organizer_domains_reject_public_suffixes_and_non_dns_values(
    domain: str,
):
    with pytest.raises(ValidationError, match="trusted organizer domains"):
        _frame(trusted_organizer_domains=(domain,))


@pytest.mark.parametrize("domain", ("afcea.org", "events.afcea.org"))
def test_trusted_organizer_domains_accept_registrable_and_full_hosts(
    domain: str,
):
    assert _frame(trusted_organizer_domains=(domain,)) \
        .trusted_organizer_domains == (domain,)


@pytest.mark.parametrize(
    "variant",
    (
        "client",
        "scope",
        "frame_revision",
        "window",
    ),
)
def test_manifest_and_cache_identity_change_with_every_cache_dimension(
    variant: str,
):
    base_frame = _frame()
    base_window = WINDOW_START
    if variant == "client":
        changed_frame = base_frame.model_copy(update={
            "binding": _binding("otherco"),
        })
        changed_window = base_window
    elif variant == "scope":
        changed_frame = base_frame.model_copy(update={
            "binding": _binding(scope_hash=OTHER_HASH),
        })
        changed_window = base_window
    elif variant == "frame_revision":
        changed_frame = base_frame.model_copy(update={
            "revision_sha256": "f" * 64,
        })
        changed_window = base_window
    else:
        changed_frame = base_frame
        changed_window = date(2026, 8, 1)

    before = _manifest(base_frame, window_start=base_window)
    after = _manifest(changed_frame, window_start=changed_window)

    assert after.manifest_id != before.manifest_id
    assert after.cache_key != before.cache_key


def test_manifest_uses_exact_calendar_month_horizon_endpoints():
    manifest = _manifest(window_start=date(2026, 7, 22))

    assert manifest.confirmed_event_through == date(2027, 7, 22)
    assert manifest.flagship_event_through == date(2028, 1, 22)


def test_empty_family_still_has_four_specs_marked_not_required():
    frame = _frame(buyer_communities=())
    manifest = _manifest(frame)
    buyer_specs = _queries_by_family(
        manifest, EventQueryFamily.BUYER_COMMUNITIES)

    assert len(manifest.queries) == 28
    assert len(buyer_specs) == 4
    assert {query.source_class for query in buyer_specs} == set(
        CLIENT_EVENT_SOURCE_CLASSES
    )
    assert all(not query.required for query in buyer_specs)
    assert all(
        query.required
        for query in manifest.queries
        if query.query_family != EventQueryFamily.BUYER_COMMUNITIES
    )


def test_discovery_records_success_and_skips_nonrequired_specs():
    manifest = _manifest(_single_family_frame())
    calls: list[str] = []

    def searcher(query: EventQuerySpec):
        calls.append(query.query_id)
        return (_lead(query, "2"), _lead(query, "1"))

    result = run_event_discovery(manifest, searcher, NOW)
    leads, attempts = result.leads, result.attempts
    required_ids = {
        query.query_id for query in manifest.queries if query.required
    }
    excluded = tuple(
        attempt for attempt in attempts
        if attempt.state == CoverageState.SCOPE_EXCLUDED
    )
    returned = tuple(
        attempt for attempt in attempts
        if attempt.state == CoverageState.RETURNED
    )

    assert set(calls) == required_ids
    assert len(calls) == 4
    assert len(attempts) == 28
    assert len(returned) == 4
    assert len(excluded) == 24
    assert len(leads) == 8
    assert all(attempt.records_returned == 2 for attempt in returned)
    assert all(attempt.records_returned == 0 for attempt in excluded)
    assert all(attempt.lead_ids == () for attempt in excluded)


def test_successful_zero_is_returned_not_failed():
    manifest = _manifest(_single_family_frame())
    calls: list[str] = []

    def searcher(query: EventQuerySpec):
        calls.append(query.query_id)
        return ()

    result = run_event_discovery(manifest, searcher, NOW)
    leads, attempts = result.leads, result.attempts
    required_ids = {
        query.query_id for query in manifest.queries if query.required
    }
    by_query = {attempt.query_id: attempt for attempt in attempts}

    assert leads == ()
    assert set(calls) == required_ids
    assert all(by_query[query_id].state == CoverageState.RETURNED
               for query_id in required_ids)
    assert all(by_query[query_id].records_returned == 0
               for query_id in required_ids)
    assert all(by_query[query_id].lead_ids == ()
               for query_id in required_ids)


def test_provider_exception_becomes_failed_with_sanitized_public_detail():
    manifest = _manifest(_single_family_frame())
    secret = "BEARER-TOP-SECRET-123"

    def searcher(query: EventQuerySpec):
        if query.source_class == EventSourceClass.OFFICIAL_AGENCY:
            raise RuntimeError(f"Authorization {secret} at /private/provider/path")
        return ()

    result = run_event_discovery(manifest, searcher, NOW)
    leads, attempts = result.leads, result.attempts
    failed = tuple(
        attempt for attempt in attempts
        if attempt.state == CoverageState.FAILED
    )

    assert leads == ()
    assert len(failed) == 1
    assert failed[0].records_returned == 0
    assert failed[0].lead_ids == ()
    assert failed[0].public_detail
    lowered = failed[0].public_detail.casefold()
    assert secret.casefold() not in lowered
    assert "private/provider" not in lowered
    assert "runtimeerror" not in lowered


@pytest.mark.parametrize(
    "state,records_returned,with_lead,attempted",
    (
        (CoverageState.PARTIAL, 3, True, True),
        (CoverageState.STALE_SNAPSHOT, 3, True, True),
        (CoverageState.FAILED, 0, False, True),
        (CoverageState.NOT_RUN, 0, False, False),
    ),
)
def test_typed_event_search_response_preserves_incomplete_coverage_state(
    state: CoverageState,
    records_returned: int,
    with_lead: bool,
    attempted: bool,
):
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        return EventSearchResponse(
            state=state,
            records_returned=records_returned,
            leads=(_lead(query),) if with_lead else (),
            public_detail=f"Provider reported {state.value} coverage.",
        )

    result = run_event_discovery(manifest, searcher, NOW)
    required_ids = {
        query.query_id for query in manifest.queries if query.required
    }
    rows = tuple(
        row for row in result.attempts if row.query_id in required_ids
    )

    assert rows
    assert {row.state for row in rows} == {state}
    assert all(row.records_returned == records_returned for row in rows)
    assert all((row.attempted_at is not None) is attempted for row in rows)
    assert all(bool(row.lead_ids) is with_lead for row in rows)


def test_typed_event_search_response_rejects_sensitive_public_detail():
    with pytest.raises(ValidationError, match="sensitive provider internals"):
        EventSearchResponse(
            state=CoverageState.FAILED,
            public_detail="Authorization: Bearer hidden-token",
        )


def test_attempt_and_lead_identity_closure_is_exact():
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        return (_lead(query, "B"), _lead(query, "A"))

    result = run_event_discovery(manifest, searcher, NOW)
    leads, attempts = result.leads, result.attempts
    queries = {query.query_id: query for query in manifest.queries}
    leads_by_id = {lead.lead_id: lead for lead in leads}

    assert len(leads_by_id) == len(leads)
    assert {attempt.query_id for attempt in attempts} == set(queries)
    for attempt in attempts:
        assert set(attempt.lead_ids).issubset(leads_by_id)
        assert attempt.records_returned == len(attempt.lead_ids)
        for lead_id in attempt.lead_ids:
            lead = leads_by_id[lead_id]
            assert lead.query_id == attempt.query_id
            assert lead.source_class == queries[attempt.query_id].source_class
    assert {lead.lead_id for lead in leads} == {
        lead_id for attempt in attempts for lead_id in attempt.lead_ids
    }


@pytest.mark.parametrize("mismatch", ("query", "source_class"))
def test_discovery_rejects_leads_that_do_not_match_the_invoking_query(
    mismatch: str,
):
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        if mismatch == "query":
            return (_lead(query, query_id="query:other"),)
        other = next(
            value for value in EventSourceClass
            if value != query.source_class
        )
        return (_lead(query, source_class=other),)

    with pytest.raises(ValueError, match="(?i)(query|source|match|identity)"):
        run_event_discovery(manifest, searcher, NOW)


def test_discovery_rejects_duplicate_lead_identity_across_queries():
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        return (_lead(query, lead_id="LEAD-SHARED"),)

    with pytest.raises(ValueError, match="(?i)(lead|duplicate|unique)"):
        run_event_discovery(manifest, searcher, NOW)


def test_timezone_awareness_is_required_for_leads_attempts_and_runner():
    manifest = _manifest(_single_family_frame())
    query = next(item for item in manifest.queries if item.required)
    naive = datetime(2026, 7, 22, 18, 0)

    with pytest.raises(ValidationError, match="(?i)timezone"):
        _lead(query, discovered_at=naive)
    with pytest.raises(ValidationError, match="(?i)timezone"):
        EventQueryAttempt(
            query_id=query.query_id,
            state=CoverageState.RETURNED,
            attempted_at=naive,
            records_returned=0,
            lead_ids=(),
            public_detail="",
        )
    with pytest.raises(ValueError, match="(?i)timezone"):
        run_event_discovery(manifest, lambda _query: (), naive)


def test_discovery_rejects_a_lead_timestamp_after_the_attempt():
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        return (_lead(query, discovered_at=NOW + timedelta(seconds=1)),)

    with pytest.raises(ValueError, match="(?i)(future|attempt|timestamp)"):
        run_event_discovery(manifest, searcher, NOW)


def test_discovery_leads_cannot_be_promoted_by_this_module():
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        return (_lead(query),)

    result = run_event_discovery(manifest, searcher, NOW)
    leads = result.leads
    authoritative_fields = {
        "evidence_id",
        "event_id",
        "official_source",
        "primary_source",
        "assertion_spans",
        "timing",
        "attendance_evidence_ids",
    }

    assert leads
    assert all(type(lead) is EventLead for lead in leads)
    assert all(not isinstance(lead, (EvidenceRecord, EventRecord)) for lead in leads)
    assert authoritative_fields.isdisjoint(EventLead.model_fields)


def test_searcher_cannot_return_an_authoritative_shaped_mapping():
    manifest = _manifest(_single_family_frame())

    def searcher(query: EventQuerySpec):
        return ({
            **_lead(query).model_dump(mode="python"),
            "evidence_id": "E-FORGED",
            "official_source": True,
            "primary_source": True,
        },)

    with pytest.raises((TypeError, ValueError, AttributeError)):
        run_event_discovery(manifest, searcher, NOW)
