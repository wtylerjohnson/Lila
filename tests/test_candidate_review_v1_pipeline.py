"""Acceptance tests for the bounded Candidate Review research caller."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CoverageState,
    SourceIdentity,
)
from agents.candidate_review_v1.event_research import (
    EventDiscoveryResult,
    EventLead,
    EventQueryAttempt,
    EventResearchFrame,
)
from agents.candidate_review_v1.event_watch import WatchFrameTags
from agents.candidate_review_v1.persistence import (
    GenerationPersistenceError,
    generation_basis_sha256,
    load_current_generation,
)
from agents.candidate_review_v1.pipeline import (
    CandidateReviewGenerationPlan,
    ProviderBinding,
    ProviderMode,
    ResearchProviderBindings,
    SnapshotChangeKind,
    SnapshotDiffState,
    SnapshotLane,
    execute_candidate_review_generation,
    generate_candidate_review_generation,
    plan_candidate_review_generation,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleLead,
    VehicleWatchFrame,
    VehicleWatchLane,
)


AS_OF = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)
REGISTRY = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "reference"
    / "event_watch_universe.json"
)
HASH = "a" * 64


def _binding(
    *,
    client_id: str = "testco",
    run_id: str = "run-1",
    scope_sha256: str = HASH,
) -> ArtifactBinding:
    names = {"testco": "TestCo", "otherco": "OtherCo"}
    return ArtifactBinding(
        client_id=client_id,
        client_name=names[client_id],
        run_id=run_id,
        scope_designator="all-federal",
        scope_sha256=scope_sha256,
        profile_sha256="b" * 64,
        evidence_snapshot_sha256="c" * 64,
    )


def _event_frame(
    binding: ArtifactBinding,
    *,
    revision: str = "d" * 64,
) -> EventResearchFrame:
    return EventResearchFrame(
        binding=binding,
        revision_sha256=revision,
        capabilities_missions=("workflow automation",),
        priority_agencies_components=("Department of State",),
        naics_psc=("541512",),
        candidate_accounts=("Diplomatic Security",),
        vehicle_partner_competitor_ecosystem=("OASIS+",),
        buyer_communities=("federal CIO",),
        policy_budget_themes=("IT modernization",),
        trusted_organizer_domains=("actiac.org",),
    )


def _vehicle_frame(
    binding: ArtifactBinding,
    *,
    revision: str = "e" * 64,
) -> VehicleWatchFrame:
    return VehicleWatchFrame(
        binding=binding,
        revision_sha256=revision,
        approved_at=AS_OF - timedelta(days=1),
        window_start=date(2024, 10, 1),
        client_terms=("workflow automation",),
        agencies=("Department of State",),
        naics_codes=("541512",),
        psc_codes=("DA10",),
        candidate_source_identities=(SourceIdentity(
            source_system="sam.gov",
            record_id="notice-123",
            revision_id="2026-07-21",
        ),),
        known_parent_idvs=(SourceIdentity(
            source_system="usaspending.gov",
            record_id="IDV-123",
        ),),
    )


def _provider(mode: ProviderMode, *, config: str) -> ProviderBinding:
    return ProviderBinding(
        adapter_name=(
            "fixture-replay" if mode is not ProviderMode.UNAVAILABLE else "none"
        ),
        adapter_version="1.0.0",
        config_sha256=config * 64,
        mode=mode,
    )


def _providers(mode: ProviderMode) -> ResearchProviderBindings:
    return ResearchProviderBindings(
        event=_provider(mode, config="f"),
        vehicle=_provider(mode, config="9"),
    )


def _plan(
    *,
    binding: ArtifactBinding | None = None,
    event_frame: EventResearchFrame | None = None,
    vehicle_frame: VehicleWatchFrame | None = None,
    providers: ResearchProviderBindings | None = None,
    registry_path: Path = REGISTRY,
    checks: dict[str, datetime] | None = None,
) -> CandidateReviewGenerationPlan:
    run_binding = binding or _binding()
    return plan_candidate_review_generation(
        run_binding,
        event_frame or _event_frame(run_binding),
        WatchFrameTags(
            mission_tags=("modernization",),
            buyer_tags=("civilian",),
            agency_tags=("state",),
            capability_tags=("enterprise-workflow",),
        ),
        vehicle_frame or _vehicle_frame(run_binding),
        as_of=AS_OF,
        event_last_checked_at=checks or {},
        registry_path=registry_path,
        providers=providers or _providers(ProviderMode.UNAVAILABLE),
    )


def _basis(plan: CandidateReviewGenerationPlan) -> str:
    return generation_basis_sha256(
        plan.binding,
        registries=plan.registry_payloads(),
        manifests=plan.manifest_payloads(),
    )


def test_provider_binding_is_strict_immutable_and_basis_bearing():
    provider = _provider(ProviderMode.REPLAY, config="1")
    with pytest.raises(ValidationError):
        ProviderBinding(
            adapter_name="fixture-replay",
            adapter_version="1.0.0",
            config_sha256="1" * 64,
            mode="replay",
        )
    with pytest.raises(ValidationError):
        ProviderBinding(
            adapter_name="Fixture Replay",
            adapter_version="1.0.0",
            config_sha256="1" * 64,
            mode="replay",
        )
    with pytest.raises(ValidationError):
        ProviderBinding(
            adapter_name="fixture-replay",
            adapter_version="1.0.0",
            config_sha256="not-a-hash",
            mode="replay",
        )
    with pytest.raises(ValidationError):
        provider.adapter_version = "2.0.0"

    plan = _plan(providers=ResearchProviderBindings(
        event=provider,
        vehicle=_provider(ProviderMode.REPLAY, config="2"),
    ))
    changed = _plan(providers=ResearchProviderBindings(
        event=provider.model_copy(update={"config_sha256": "3" * 64}),
        vehicle=_provider(ProviderMode.REPLAY, config="2"),
    ))
    assert plan.plan_sha256 != changed.plan_sha256
    assert _basis(plan) != _basis(changed)
    assert CandidateReviewGenerationPlan.model_validate_json(
        plan.model_dump_json()
    ) == plan


def test_plan_rejects_client_run_scope_bleed_and_naive_as_of():
    binding = _binding()
    other_client = _binding(client_id="otherco")
    with pytest.raises(ValueError, match="event research frame binding"):
        _plan(binding=binding, event_frame=_event_frame(other_client))

    other_run = _binding(run_id="run-2")
    with pytest.raises(ValueError, match="vehicle watch frame binding"):
        _plan(binding=binding, vehicle_frame=_vehicle_frame(other_run))

    other_scope = _binding(scope_sha256="8" * 64)
    with pytest.raises(ValueError, match="event research frame binding"):
        _plan(binding=binding, event_frame=_event_frame(other_scope))

    with pytest.raises(ValueError, match="timezone-aware"):
        plan_candidate_review_generation(
            binding,
            _event_frame(binding),
            WatchFrameTags(),
            _vehicle_frame(binding),
            as_of=AS_OF.replace(tzinfo=None),
            event_last_checked_at={},
            registry_path=REGISTRY,
            providers=_providers(ProviderMode.UNAVAILABLE),
        )


def test_registry_exact_byte_digest_invalidates_plan_and_generation_basis(
    tmp_path,
):
    first_path = tmp_path / "registry-a.json"
    second_path = tmp_path / "registry-b.json"
    payload = json.loads(REGISTRY.read_text())
    first_path.write_text(json.dumps(payload, indent=2) + "\n")
    second_path.write_text(json.dumps(payload, separators=(",", ":")))

    first = _plan(registry_path=first_path)
    second = _plan(registry_path=second_path)
    assert first.event_watch_universe == second.event_watch_universe
    assert (
        first.event_watch_universe_sha256
        != second.event_watch_universe_sha256
    )
    assert first.event_query_manifest.manifest_id \
        != second.event_query_manifest.manifest_id
    assert first.plan_sha256 != second.plan_sha256
    assert _basis(first) != _basis(second)


def test_frame_revisions_invalidate_plan_and_generation_basis():
    binding = _binding()
    baseline = _plan(binding=binding)
    event_changed = _plan(
        binding=binding,
        event_frame=_event_frame(binding, revision="1" * 64),
    )
    vehicle_changed = _plan(
        binding=binding,
        vehicle_frame=_vehicle_frame(binding, revision="2" * 64),
    )
    assert len({
        baseline.plan_sha256,
        event_changed.plan_sha256,
        vehicle_changed.plan_sha256,
    }) == 3
    assert len({_basis(baseline), _basis(event_changed), _basis(vehicle_changed)}) \
        == 3


def test_plan_recomputes_selection_and_manifests_to_reject_tampering():
    binding = _binding()
    baseline = _plan(binding=binding)
    alternate_tags = WatchFrameTags(
        mission_tags=("space",),
        buyer_tags=("defense",),
        agency_tags=("space-force",),
        capability_tags=("cybersecurity",),
    )
    alternate = plan_candidate_review_generation(
        binding,
        _event_frame(binding),
        alternate_tags,
        _vehicle_frame(binding),
        as_of=AS_OF,
        event_last_checked_at={},
        registry_path=REGISTRY,
        providers=_providers(ProviderMode.UNAVAILABLE),
    )
    payload = baseline.model_dump()
    payload["event_watch_selection"] = alternate.event_watch_selection
    payload["event_query_manifest"] = alternate.event_query_manifest
    with pytest.raises(ValidationError, match="selection does not match"):
        CandidateReviewGenerationPlan.model_validate(payload)

    changed_vehicle = _plan(
        binding=binding,
        vehicle_frame=_vehicle_frame(binding, revision="7" * 64),
    )
    payload = baseline.model_dump()
    payload["vehicle_query_manifest"] = changed_vehicle.vehicle_query_manifest
    with pytest.raises(ValidationError, match="vehicle manifest"):
        CandidateReviewGenerationPlan.model_validate(payload)


def test_unavailable_providers_are_not_run_never_failed_or_returned_zero(
    tmp_path,
):
    result = execute_candidate_review_generation(
        _plan(),
        state_root=tmp_path,
    )
    event_by_query = {
        row.query_id: row for row in result.event_result.attempts
    }
    for query in result.plan.event_query_manifest.queries:
        attempt = event_by_query[query.query_id]
        if query.required:
            assert attempt.state is CoverageState.NOT_RUN
        else:
            assert attempt.state is CoverageState.SCOPE_EXCLUDED
        assert attempt.attempted_at is None
        assert attempt.records_returned == 0

    vehicle_by_query = {
        row.query_id: row for row in result.vehicle_result.attempts
    }
    for query in result.plan.vehicle_query_manifest.queries:
        attempt = vehicle_by_query[query.query_id]
        expected = (
            CoverageState.NOT_RUN
            if query.required
            else CoverageState.SCOPE_EXCLUDED
        )
        assert attempt.state is expected
        assert attempt.attempted_at is None
        assert not attempt.returned_zero
    assert all(
        row.state in {CoverageState.NOT_RUN, CoverageState.SCOPE_EXCLUDED}
        for row in result.vehicle_coverage
    )


def test_runtime_provider_binding_must_match_searcher_presence(tmp_path):
    unavailable = _plan()
    with pytest.raises(ValueError, match="unavailable"):
        execute_candidate_review_generation(
            unavailable,
            event_searcher=lambda _query: (),
            state_root=tmp_path,
        )

    replay = _plan(providers=_providers(ProviderMode.REPLAY))
    with pytest.raises(ValueError, match="requires its bound searcher"):
        execute_candidate_review_generation(
            replay,
            event_searcher=lambda _query: (),
            state_root=tmp_path,
        )


def test_supplied_replay_providers_distinguish_failure_from_zero(tmp_path):
    plan = _plan(providers=_providers(ProviderMode.REPLAY))
    first_event = next(
        query for query in plan.event_query_manifest.queries if query.required
    )
    first_vehicle = next(
        query for query in plan.vehicle_query_manifest.queries if query.required
    )

    def event_searcher(query):
        if query.query_id == first_event.query_id:
            raise RuntimeError("fixture outage")
        return ()

    def vehicle_searcher(query):
        if query.query_id == first_vehicle.query_id:
            raise RuntimeError("fixture outage")
        return ()

    result = execute_candidate_review_generation(
        plan,
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        state_root=tmp_path,
    )
    event_attempts = {
        row.query_id: row for row in result.event_result.attempts
    }
    assert event_attempts[first_event.query_id].state is CoverageState.FAILED
    assert any(
        row.state is CoverageState.RETURNED and row.records_returned == 0
        for row in result.event_result.attempts
    )
    vehicle_attempts = {
        row.query_id: row for row in result.vehicle_result.attempts
    }
    assert vehicle_attempts[first_vehicle.query_id].state \
        is CoverageState.FAILED
    assert not vehicle_attempts[first_vehicle.query_id].returned_zero
    assert any(
        row.state is CoverageState.RETURNED and row.returned_zero
        for row in result.vehicle_result.attempts
    )


def test_every_run_watch_check_advances_only_after_complete_target_census(
    tmp_path,
):
    initial = _plan(providers=_providers(ProviderMode.REPLAY))
    recent_target = next(
        target for target in initial.event_watch_selection.applicable
        if target.priority.value == "watch"
    )
    prior_check = AS_OF - timedelta(days=1)
    plan = _plan(
        providers=_providers(ProviderMode.REPLAY),
        checks={recent_target.id: prior_check},
    )
    result = execute_candidate_review_generation(
        plan,
        event_searcher=lambda _query: (),
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path,
    )
    projection = result.next_event_watch_checks
    assert set(projection.advanced_target_ids) \
        == set(plan.event_query_manifest.search_due_watch_target_ids)
    assert projection.as_mapping()[recent_target.id] == AS_OF
    assert recent_target.id in projection.advanced_target_ids

    due_target = recent_target.id
    target_queries = tuple(
        query for query in plan.event_query_manifest.queries
        if query.watch_target_id == due_target
    )
    attempts = []
    for attempt in result.event_result.attempts:
        if attempt.query_id == target_queries[0].query_id:
            attempts.append(EventQueryAttempt(
                query_id=attempt.query_id,
                state=CoverageState.PARTIAL,
                attempted_at=AS_OF,
                records_returned=0,
                lead_ids=(),
                public_detail="Provider returned a partial page.",
            ))
        else:
            attempts.append(attempt)
    partial = EventDiscoveryResult(
        manifest=plan.event_query_manifest,
        attempted_at=AS_OF,
        leads=(),
        attempts=tuple(attempts),
    )
    from agents.candidate_review_v1.pipeline import (
        project_next_event_watch_checks,
    )

    projected_partial = project_next_event_watch_checks(plan, partial)
    assert due_target not in projected_partial.advanced_target_ids
    assert projected_partial.as_mapping()[due_target] == prior_check


def test_snapshot_diff_is_baselined_then_queues_unverified_material_changes(
    tmp_path,
):
    providers = _providers(ProviderMode.REPLAY)
    first_binding = _binding(run_id="snapshot-1")
    first = generate_candidate_review_generation(
        first_binding,
        _event_frame(first_binding),
        WatchFrameTags(capability_tags=("enterprise-workflow",)),
        _vehicle_frame(first_binding),
        as_of=AS_OF,
        event_last_checked_at={},
        registry_path=REGISTRY,
        providers=providers,
        event_searcher=lambda _query: (),
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path,
    )
    assert first.snapshot_diff.state is SnapshotDiffState.BASELINE
    assert first.snapshot_diff.changes == ()

    second_binding = _binding(run_id="snapshot-2")
    second_plan = plan_candidate_review_generation(
        second_binding,
        _event_frame(second_binding),
        WatchFrameTags(capability_tags=("enterprise-workflow",)),
        _vehicle_frame(second_binding),
        as_of=AS_OF,
        event_last_checked_at={},
        registry_path=REGISTRY,
        providers=providers,
    )
    event_query = next(
        row for row in second_plan.event_query_manifest.queries if row.required
    )
    vehicle_query = next(
        row for row in second_plan.vehicle_query_manifest.queries
        if row.required
        and row.lane is VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES
    )

    def event_searcher(query):
        if query.query_id != event_query.query_id:
            return ()
        return (EventLead(
            lead_id="event-lead-1",
            query_id=query.query_id,
            source_class=query.source_class,
            discovered_at=AS_OF,
            title="Federal workflow summit",
            url="https://events.example.gov/workflow-summit",
            claimed_date="2026-10-01",
        ),)

    def vehicle_searcher(query):
        if query.query_id != vehicle_query.query_id:
            return ()
        return (VehicleLead(
            lead_id="vehicle-lead-1",
            client_id=second_binding.client_id,
            run_id=second_binding.run_id,
            scope_sha256=second_binding.scope_sha256,
            query_id=query.query_id,
            lane=query.lane,
            discovered_at=AS_OF,
            title="Workflow IDIQ on-ramp notice",
            source_url="https://sam.gov/opp/vehicle-1/view",
            source_identity=SourceIdentity(
                source_system="sam.gov",
                record_id="vehicle-1",
            ),
        ),)

    second = execute_candidate_review_generation(
        second_plan,
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        state_root=tmp_path,
        committed_at=AS_OF + timedelta(seconds=1),
    )
    assert second.snapshot_diff.state is SnapshotDiffState.COMPARED
    assert {
        (row.lane, row.kind) for row in second.snapshot_diff.changes
    } == {
        (SnapshotLane.EVENT, SnapshotChangeKind.ADDED),
        (SnapshotLane.VEHICLE, SnapshotChangeKind.ADDED),
    }
    assert all(row.requires_verification for row in second.snapshot_diff.changes)
    assert all(not row.creates_ticker for row in second.snapshot_diff.changes)
    assert second.snapshot_diff.ticker_review_change_ids == tuple(
        row.change_id for row in second.snapshot_diff.changes
    )
    diff_basis = second.commit.generation.manifests["snapshot-diff-basis"]
    assert diff_basis["previous_generation"]["binding"]["run_id"] \
        == first_binding.run_id
    assert diff_basis["previous_generation"]["basis_sha256"] \
        == first.receipt.basis_sha256

    retry = execute_candidate_review_generation(
        second_plan,
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        state_root=tmp_path,
        committed_at=AS_OF + timedelta(seconds=1),
    )
    assert retry.commit.reused
    assert retry.snapshot_diff == second.snapshot_diff


def test_snapshot_diff_does_not_infer_removal_during_provider_outage(tmp_path):
    first_binding = _binding(run_id="outage-baseline")
    first_plan = _plan(
        binding=first_binding,
        event_frame=_event_frame(first_binding),
        vehicle_frame=_vehicle_frame(first_binding),
        providers=_providers(ProviderMode.REPLAY),
    )
    observed_query_id = next(
        query.query_id
        for query in first_plan.event_query_manifest.queries
        if query.required
    )

    def event_searcher(query):
        if query.query_id != observed_query_id:
            return ()
        return (EventLead(
            lead_id="event-lead-before-outage",
            query_id=query.query_id,
            source_class=query.source_class,
            discovered_at=AS_OF,
            title="Federal workflow summit",
            url="https://events.example.gov/workflow-summit",
            claimed_date="2026-10-01",
        ),)

    baseline = execute_candidate_review_generation(
        first_plan,
        event_searcher=event_searcher,
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path,
    )
    assert baseline.event_result.leads

    second_binding = _binding(run_id="outage-refresh")
    outage_plan = _plan(
        binding=second_binding,
        event_frame=_event_frame(second_binding),
        vehicle_frame=_vehicle_frame(second_binding),
        providers=_providers(ProviderMode.UNAVAILABLE),
    )
    outage = execute_candidate_review_generation(
        outage_plan,
        state_root=tmp_path,
        committed_at=AS_OF + timedelta(seconds=1),
    )

    assert outage.snapshot_diff.state is SnapshotDiffState.COMPARED
    assert outage.snapshot_diff.changes == ()
    assert outage.snapshot_diff.removal_coverage_lanes == ()
    assert any(
        row.state is CoverageState.NOT_RUN
        for row in outage.event_result.attempts
        if row.query_id in {
            query.query_id
            for query in outage.plan.event_query_manifest.queries
            if query.required
        }
    )


@pytest.mark.parametrize(
    "semantic_change",
    ("frame_revision", "query_text", "watch_registry"),
)
def test_snapshot_diff_never_infers_event_removal_across_search_semantics(
    tmp_path,
    semantic_change,
):
    registry_a = tmp_path / "registry-a.json"
    registry_b = tmp_path / "registry-b.json"
    registry_payload = json.loads(REGISTRY.read_text())
    registry_a.write_text(json.dumps(registry_payload, indent=2) + "\n")
    registry_b.write_text(json.dumps(registry_payload, separators=(",", ":")))

    first_binding = _binding(run_id=f"semantic-{semantic_change}-1")
    first_plan = _plan(
        binding=first_binding,
        event_frame=_event_frame(first_binding),
        vehicle_frame=_vehicle_frame(first_binding),
        providers=_providers(ProviderMode.REPLAY),
        registry_path=registry_a,
    )
    observed_query = next(
        query for query in first_plan.event_query_manifest.queries
        if query.required
    )

    def observed_event(query):
        if query.query_id != observed_query.query_id:
            return ()
        return (EventLead(
            lead_id=f"event-lead-{semantic_change}",
            query_id=query.query_id,
            source_class=query.source_class,
            discovered_at=AS_OF,
            title="Federal workflow summit",
            url="https://events.example.gov/workflow-summit",
            claimed_date="2026-10-01",
        ),)

    baseline = execute_candidate_review_generation(
        first_plan,
        event_searcher=observed_event,
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path / "state",
    )
    assert baseline.event_result.leads

    second_binding = _binding(run_id=f"semantic-{semantic_change}-2")
    second_frame = _event_frame(second_binding)
    second_registry = registry_a
    if semantic_change == "frame_revision":
        second_frame = _event_frame(second_binding, revision="1" * 64)
    elif semantic_change == "query_text":
        second_frame = second_frame.model_copy(update={
            "capabilities_missions": ("case management",),
        })
    else:
        second_registry = registry_b
    second_plan = _plan(
        binding=second_binding,
        event_frame=second_frame,
        vehicle_frame=_vehicle_frame(second_binding),
        providers=_providers(ProviderMode.REPLAY),
        registry_path=second_registry,
    )
    refreshed = execute_candidate_review_generation(
        second_plan,
        event_searcher=lambda _query: (),
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path / "state",
        committed_at=AS_OF + timedelta(seconds=1),
    )

    assert refreshed.snapshot_diff.state is SnapshotDiffState.COMPARED
    assert SnapshotLane.EVENT not in (
        refreshed.snapshot_diff.removal_coverage_lanes
    )
    assert not any(
        row.lane is SnapshotLane.EVENT
        and row.kind is SnapshotChangeKind.REMOVED
        for row in refreshed.snapshot_diff.changes
    )


def test_snapshot_diff_allows_removal_after_exact_comparable_event_census(
    tmp_path,
):
    first_binding = _binding(run_id="comparable-removal-1")
    first_plan = _plan(
        binding=first_binding,
        event_frame=_event_frame(first_binding),
        vehicle_frame=_vehicle_frame(first_binding),
        providers=_providers(ProviderMode.REPLAY),
    )
    observed_query = next(
        query for query in first_plan.event_query_manifest.queries
        if query.required
    )

    def observed_event(query):
        if query.query_id != observed_query.query_id:
            return ()
        return (EventLead(
            lead_id="event-lead-comparable-removal",
            query_id=query.query_id,
            source_class=query.source_class,
            discovered_at=AS_OF,
            title="Federal workflow summit",
            url="https://events.example.gov/workflow-summit",
            claimed_date="2026-10-01",
        ),)

    execute_candidate_review_generation(
        first_plan,
        event_searcher=observed_event,
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path,
    )
    second_binding = _binding(run_id="comparable-removal-2")
    second_plan = _plan(
        binding=second_binding,
        event_frame=_event_frame(second_binding),
        vehicle_frame=_vehicle_frame(second_binding),
        providers=_providers(ProviderMode.REPLAY),
    )
    refreshed = execute_candidate_review_generation(
        second_plan,
        event_searcher=lambda _query: (),
        vehicle_searcher=lambda _query: (),
        state_root=tmp_path,
        committed_at=AS_OF + timedelta(seconds=1),
    )

    event_removals = tuple(
        row for row in refreshed.snapshot_diff.changes
        if row.lane is SnapshotLane.EVENT
        and row.kind is SnapshotChangeKind.REMOVED
    )
    assert SnapshotLane.EVENT in refreshed.snapshot_diff.removal_coverage_lanes
    assert len(event_removals) == 1


def test_exact_persistence_load_and_reuse_are_deterministic(tmp_path):
    plan = _plan()
    first = execute_candidate_review_generation(plan, state_root=tmp_path)
    second = execute_candidate_review_generation(plan, state_root=tmp_path)
    loaded = load_current_generation(
        plan.binding.client_id,
        state_root=tmp_path,
        expected_binding=plan.binding,
        expected_basis_sha256=_basis(plan),
    )

    assert not first.commit.reused
    assert second.commit.reused
    assert first.receipt == second.receipt
    assert first.event_result == second.event_result
    assert first.vehicle_result == second.vehicle_result
    assert first.vehicle_coverage == second.vehicle_coverage
    assert loaded is not None
    assert loaded.receipt == first.receipt
    assert loaded.manifests["provider-bindings"] == {
        "schema_version": plan.schema_version,
        "providers": plan.providers.model_dump(mode="json"),
    }
    assert loaded.artifacts["event-discovery-result"] \
        == first.event_result.model_dump(mode="json")
    assert loaded.artifacts["vehicle-collection-result"] \
        == first.vehicle_result.model_dump(mode="json")
    assert loaded.artifacts["event-watch-checks-next"] \
        == first.next_event_watch_checks.model_dump(mode="json")
    assert loaded.artifacts["research-snapshot-diff"] \
        == first.snapshot_diff.model_dump(mode="json")
    assert loaded.registries["event-watch-universe"]["registry_sha256"] \
        == plan.event_watch_universe_sha256


def test_convenience_generation_matches_explicit_plan_execution(tmp_path):
    binding = _binding(run_id="run-convenience")
    result = generate_candidate_review_generation(
        binding,
        _event_frame(binding),
        WatchFrameTags(capability_tags=("enterprise-workflow",)),
        _vehicle_frame(binding),
        as_of=AS_OF,
        event_last_checked_at={},
        registry_path=REGISTRY,
        providers=_providers(ProviderMode.UNAVAILABLE),
        state_root=tmp_path,
    )
    assert result.receipt.binding == binding
    assert result.receipt.basis_sha256 == _basis(result.plan)
    assert result.commit.generation.pointer.run_id == binding.run_id


def test_explicit_commit_time_supports_new_basis_at_the_same_as_of(tmp_path):
    first = _plan(binding=_binding(run_id="commit-1"))
    execute_candidate_review_generation(first, state_root=tmp_path)
    second = _plan(binding=_binding(run_id="commit-2"))

    with pytest.raises(GenerationPersistenceError, match="committed after"):
        execute_candidate_review_generation(second, state_root=tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        execute_candidate_review_generation(
            second,
            state_root=tmp_path,
            committed_at=(AS_OF + timedelta(seconds=1)).replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="cannot predate"):
        execute_candidate_review_generation(
            second,
            state_root=tmp_path,
            committed_at=AS_OF - timedelta(seconds=1),
        )
    committed = execute_candidate_review_generation(
        second,
        state_root=tmp_path,
        committed_at=AS_OF + timedelta(seconds=1),
    )
    assert committed.receipt.committed_at == AS_OF + timedelta(seconds=1)
