"""Offline replay adapter contract tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CoverageState,
    SourceIdentity,
)
from agents.candidate_review_v1.event_research import (
    EventResearchFrame,
    EventSearchResponse,
)
from agents.candidate_review_v1.event_watch import WatchFrameTags
from agents.candidate_review_v1.pipeline import (
    ProviderBinding,
    ProviderMode,
    ResearchProviderBindings,
    plan_candidate_review_generation,
)
from agents.candidate_review_v1.replay_providers import (
    CurrentSweepReplay,
    CurrentSweepSource,
    EventReplayArtifact,
    EventReplaySourceArtifact,
    ReplayFileReference,
    ReplayProviderConfig,
    ReplayProviderError,
    ReplaySourceBundleConfig,
    load_replay_provider_runtime,
    load_replay_source_bundle_runtime,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleTargetKind,
    VehicleWatchFrame,
    VehicleWatchLane,
    run_vehicle_watch_collection,
)


AS_OF = datetime(2026, 7, 22, 20, tzinfo=timezone.utc)
REGISTRY = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "reference"
    / "event_watch_universe.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unavailable() -> ResearchProviderBindings:
    values = dict(
        adapter_version="1",
        config_sha256="a" * 64,
        mode=ProviderMode.UNAVAILABLE,
    )
    return ResearchProviderBindings(
        event=ProviderBinding(
            adapter_name="unavailable-event-provider",
            **values,
        ),
        vehicle=ProviderBinding(
            adapter_name="unavailable-vehicle-provider",
            **values,
        ),
    )


def _plan(
    tmp_path: Path,
    *,
    award_repulls: list[dict] | None = None,
    known_parent_idvs: tuple[SourceIdentity, ...] = (),
):
    results = {
        "sam.gov": [{
            "source": "sam.gov",
            "source_id": "ACTUAL-SAM-ID",
            "title": "Workflow automation EXPECTED-SAM-ID",
            "agency": "Department of State",
            "naics_code": "541512",
            "api_url": "https://sam.gov/opp/ACTUAL-SAM-ID/view",
        }],
        "sam_census": {
            "complete": True,
            "matched": 1,
            "active_screened": 1,
        },
    }
    if award_repulls is not None:
        results["award_repulls"] = award_repulls
    sweep_path = tmp_path / "searches_testco.json"
    sweep_path.write_text(json.dumps({
        "client": "TestCo",
        "generated_at": AS_OF.isoformat(),
        "results": results,
    }, indent=2) + "\n", encoding="utf-8")
    binding = ArtifactBinding(
        client_id="testco",
        client_name="TestCo",
        run_id="replay-run-1",
        scope_designator="all",
        scope_sha256="b" * 64,
        profile_sha256="c" * 64,
        evidence_snapshot_sha256=_sha(sweep_path),
    )
    event_frame = EventResearchFrame(
        binding=binding,
        revision_sha256="d" * 64,
        capabilities_missions=("workflow automation",),
    )
    vehicle_frame = VehicleWatchFrame(
        binding=binding,
        revision_sha256="e" * 64,
        approved_at=AS_OF - timedelta(hours=1),
        window_start=date(2026, 7, 1),
        client_terms=("workflow automation",),
        candidate_source_identities=(SourceIdentity(
            source_system="sam.gov",
            record_id="EXPECTED-SAM-ID",
        ),),
        known_parent_idvs=known_parent_idvs,
    )
    plan = plan_candidate_review_generation(
        binding,
        event_frame,
        WatchFrameTags(capability_tags=("workflow",)),
        vehicle_frame,
        as_of=AS_OF,
        event_last_checked_at={},
        registry_path=REGISTRY,
        providers=_unavailable(),
    )
    return sweep_path, plan


def test_award_subset_never_claims_complete_zero_result(tmp_path):
    expected_parent = SourceIdentity(
        source_system="usaspending.gov",
        record_id="CONT_IDV_EXPECTED_001",
    )
    sweep_path, plan = _plan(
        tmp_path,
        award_repulls=[{
            "kind": "award_repull",
            "source_system": "usaspending.gov",
            "generated_id": "CONT_AWD_UNRELATED_001",
            "parent_generated_id": "CONT_IDV_UNRELATED_001",
            "description": "Unrelated facilities maintenance",
            "retrieved_at": AS_OF.isoformat(),
        }],
        known_parent_idvs=(expected_parent,),
    )
    config = ReplayProviderConfig(
        binding=plan.binding,
        event_manifest_id=plan.event_query_manifest.manifest_id,
        vehicle_manifest_id=plan.vehicle_query_manifest.manifest_id,
        vehicle=CurrentSweepReplay(sweep_sha256=_sha(sweep_path)),
    )
    config_path = tmp_path / "replay.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    runtime = load_replay_provider_runtime(
        config_path,
        binding=plan.binding,
        event_manifest=plan.event_query_manifest,
        vehicle_manifest=plan.vehicle_query_manifest,
        sweep_path=sweep_path,
    )
    query = next(
        row for row in plan.vehicle_query_manifest.queries
        if row.lane is VehicleWatchLane.USASPENDING_IDV
        and row.target_kind is VehicleTargetKind.CLIENT_TERM
    )

    response = runtime.vehicle_searcher(query)
    assert response.state is CoverageState.PARTIAL
    assert response.records_returned == 0
    assert response.leads == ()
    result = run_vehicle_watch_collection(
        plan.vehicle_query_manifest,
        runtime.vehicle_searcher,
        AS_OF,
    )
    attempt = next(row for row in result.attempts if row.query_id == query.query_id)
    assert attempt.state is CoverageState.PARTIAL
    assert attempt.records_returned == 0
    assert attempt.returned_zero is False
    assert "not a query-scoped complete" in attempt.public_detail

    order_query = next(
        row for row in plan.vehicle_query_manifest.queries
        if row.lane is VehicleWatchLane.USASPENDING_ORDER_LINEAGE
        and row.target_source_identity == expected_parent
    )
    order_response = runtime.vehicle_searcher(order_query)
    assert order_response.state is CoverageState.PARTIAL
    assert order_response.records_returned == 0
    order_attempt = next(
        row for row in result.attempts if row.query_id == order_query.query_id
    )
    assert order_attempt.state is CoverageState.PARTIAL
    assert order_attempt.returned_zero is False


def test_vehicle_replay_keeps_exact_targets_and_unavailable_lanes_honest(
        tmp_path):
    sweep_path, plan = _plan(tmp_path)
    config = ReplayProviderConfig(
        binding=plan.binding,
        event_manifest_id=plan.event_query_manifest.manifest_id,
        vehicle_manifest_id=plan.vehicle_query_manifest.manifest_id,
        vehicle=CurrentSweepReplay(sweep_sha256=_sha(sweep_path)),
    )
    config_path = tmp_path / "replay.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    runtime = load_replay_provider_runtime(
        config_path,
        binding=plan.binding,
        event_manifest=plan.event_query_manifest,
        vehicle_manifest=plan.vehicle_query_manifest,
        sweep_path=sweep_path,
    )
    assert runtime.bindings.event.mode is ProviderMode.UNAVAILABLE
    assert runtime.bindings.vehicle.mode is ProviderMode.REPLAY
    result = run_vehicle_watch_collection(
        plan.vehicle_query_manifest,
        runtime.vehicle_searcher,
        AS_OF,
    )
    attempts = {row.query_id: row for row in result.attempts}
    exact_query = next(
        row for row in plan.vehicle_query_manifest.queries
        if row.lane is VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES
        and row.target_kind is VehicleTargetKind.CANDIDATE_SOURCE
    )
    assert attempts[exact_query.query_id].state is CoverageState.RETURNED
    assert attempts[exact_query.query_id].returned_zero is True
    assert all(
        lead.source_identity.record_id != "EXPECTED-SAM-ID"
        for lead in result.leads
    )
    assert any(
        lead.source_identity.record_id == "ACTUAL-SAM-ID"
        for lead in result.leads
    )
    for query, attempt in zip(plan.vehicle_query_manifest.queries, result.attempts):
        if query.required and query.lane is not VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES:
            assert attempt.state is CoverageState.NOT_RUN
            assert attempt.returned_zero is False


def test_event_replay_bytes_are_hash_bound_and_missing_rows_are_not_zero(
        tmp_path):
    sweep_path, plan = _plan(tmp_path)
    query = next(
        row for row in plan.event_query_manifest.queries if row.required
    )
    artifact = EventReplayArtifact(
        binding=plan.binding,
        manifest_id=plan.event_query_manifest.manifest_id,
        responses={
            query.query_id: EventSearchResponse(
                state=CoverageState.RETURNED,
                records_returned=0,
                public_detail="Bound offline replay completed with no leads.",
            ),
        },
    )
    artifact_path = tmp_path / "events.json"
    artifact_path.write_text(artifact.model_dump_json(), encoding="utf-8")
    config = ReplayProviderConfig(
        binding=plan.binding,
        event_manifest_id=plan.event_query_manifest.manifest_id,
        vehicle_manifest_id=plan.vehicle_query_manifest.manifest_id,
        event=ReplayFileReference(
            path=artifact_path.name,
            sha256=_sha(artifact_path),
        ),
    )
    config_path = tmp_path / "replay.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    runtime = load_replay_provider_runtime(
        config_path,
        binding=plan.binding,
        event_manifest=plan.event_query_manifest,
        vehicle_manifest=plan.vehicle_query_manifest,
        sweep_path=sweep_path,
    )
    assert runtime.event_searcher(query).state is CoverageState.RETURNED
    missing = next(
        row for row in plan.event_query_manifest.queries
        if row.required and row.query_id != query.query_id
    )
    response = runtime.event_searcher(missing)
    assert response.state is CoverageState.NOT_RUN
    assert response.records_returned == 0

    original_artifact = artifact_path.read_bytes()
    artifact_path.write_bytes(original_artifact + b"\n")
    with pytest.raises(ReplayProviderError, match="SHA-256 differs"):
        load_replay_provider_runtime(
            config_path,
            binding=plan.binding,
            event_manifest=plan.event_query_manifest,
            vehicle_manifest=plan.vehicle_query_manifest,
            sweep_path=sweep_path,
        )

    artifact_path.write_bytes(original_artifact)
    original_config = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        '{"schema_version":"candidate_review_v1.replay_config.v1",'
        + original_config[1:],
        encoding="utf-8",
    )
    with pytest.raises(ReplayProviderError, match="duplicate object key"):
        load_replay_provider_runtime(
            config_path,
            binding=plan.binding,
            event_manifest=plan.event_query_manifest,
            vehicle_manifest=plan.vehicle_query_manifest,
            sweep_path=sweep_path,
        )


def test_source_bundle_rebinds_semantic_events_and_exact_current_sweep(
        tmp_path):
    """Immutable inputs bind only after the post-re-pull plan exists."""

    sweep_path, plan = _plan(tmp_path)
    query = next(
        row for row in plan.event_query_manifest.queries if row.required
    )
    event_source = EventReplaySourceArtifact(
        client_id=plan.binding.client_id,
        client_name=plan.binding.client_name,
        source_data_as_of=AS_OF,
        responses={
            query.query_id: EventSearchResponse(
                state=CoverageState.PARTIAL,
                records_returned=1,
                public_detail="The immutable source contains a partial page.",
            ),
            "event-query:retired:official_agency:0000000000000000": (
                EventSearchResponse(
                    state=CoverageState.RETURNED,
                    records_returned=0,
                    public_detail=(
                        "This older semantic query is not in the current "
                        "manifest."
                    ),
                )
            ),
        },
    )
    event_path = tmp_path / "event-source.json"
    event_path.write_text(
        event_source.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    bundle = ReplaySourceBundleConfig(
        client_id=plan.binding.client_id,
        client_name=plan.binding.client_name,
        event=ReplayFileReference(
            path=event_path.name,
            sha256=_sha(event_path),
        ),
        vehicle=CurrentSweepSource(),
    )
    bundle_path = tmp_path / "source-bundle.json"
    bundle_path.write_text(
        bundle.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    runtime = load_replay_source_bundle_runtime(
        bundle_path,
        binding=plan.binding,
        event_manifest=plan.event_query_manifest,
        vehicle_manifest=plan.vehicle_query_manifest,
        sweep_path=sweep_path,
    )

    assert runtime.bindings.event.mode is ProviderMode.REPLAY
    assert runtime.bindings.vehicle.mode is ProviderMode.REPLAY
    assert runtime.event_searcher is not None
    assert runtime.event_searcher(query).state is CoverageState.PARTIAL
    missing = next(
        row for row in plan.event_query_manifest.queries
        if row.required and row.query_id != query.query_id
    )
    assert runtime.event_searcher(missing).state is CoverageState.NOT_RUN
    assert runtime.vehicle_searcher is not None
    unsupported = next(
        row for row in plan.vehicle_query_manifest.queries
        if row.required
        and row.lane not in {
            VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
            VehicleWatchLane.USASPENDING_IDV,
            VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
        }
    )
    assert runtime.vehicle_searcher(unsupported).state is CoverageState.NOT_RUN
    assert runtime.config_sha256 == _sha(bundle_path)

    old_exact = ReplayProviderConfig(
        binding=plan.binding,
        event_manifest_id=plan.event_query_manifest.manifest_id,
        vehicle_manifest_id=plan.vehicle_query_manifest.manifest_id,
        vehicle=CurrentSweepReplay(sweep_sha256=_sha(sweep_path)),
    )
    old_exact_path = tmp_path / "old-exact.json"
    old_exact_path.write_text(old_exact.model_dump_json(), encoding="utf-8")
    payload = json.loads(sweep_path.read_text(encoding="utf-8"))
    payload["results"]["award_repulls"] = []
    sweep_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReplayProviderError, match="SHA-256 differs"):
        load_replay_provider_runtime(
            old_exact_path,
            binding=plan.binding,
            event_manifest=plan.event_query_manifest,
            vehicle_manifest=plan.vehicle_query_manifest,
            sweep_path=sweep_path,
        )
