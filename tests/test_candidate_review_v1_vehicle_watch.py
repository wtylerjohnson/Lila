"""Offline contract tests for Candidate Review v1 vehicle collection."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    ArtifactBinding,
    CoverageState,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    SourceIdentity,
    SourceTier,
    VehicleSignal,
)
from agents.candidate_review_v1.vehicle_watch import (
    VEHICLE_WATCH_SCHEMA_VERSION,
    RestrictedVehicleExport,
    VehicleLead,
    VehicleQuerySpec,
    VehicleSearchResponse,
    VehicleTargetKind,
    VehicleWatchFrame,
    VehicleWatchLane,
    build_vehicle_query_manifest,
    project_vehicle_watch_coverage,
    run_vehicle_watch_collection,
)


AS_OF = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
ATTEMPTED_AT = datetime(2026, 7, 22, 17, 0, tzinfo=timezone.utc)
HASH = "a" * 64
FRAME_HASH = "b" * 64
CANDIDATE_SOURCE = SourceIdentity(
    source_system="sam.gov",
    record_id="notice-123",
    revision_id="2026-07-21",
)
PARENT_IDV = SourceIdentity(
    source_system="usaspending.gov",
    record_id="IDV-123",
)
ORDER = SourceIdentity(
    source_system="usaspending.gov",
    record_id="ORDER-456",
)


def _binding(client_id: str = "testco") -> ArtifactBinding:
    names = {"testco": "TestCo", "otherco": "OtherCo"}
    return ArtifactBinding(
        client_id=client_id,
        client_name=names[client_id],
        run_id="run-1",
        scope_designator="all-federal",
        scope_sha256=HASH,
        profile_sha256="c" * 64,
        evidence_snapshot_sha256="d" * 64,
    )


def _frame(**changes) -> VehicleWatchFrame:
    values = {
        "binding": _binding(),
        "revision_sha256": FRAME_HASH,
        "approved_at": datetime(
            2026, 7, 21, 18, 0, tzinfo=timezone.utc
        ),
        "window_start": date(2023, 10, 1),
        "client_terms": ("workflow automation", "records management"),
        "agencies": ("Department of State", "GSA"),
        "naics_codes": ("541512", "541519"),
        "psc_codes": ("DA10", "R408"),
        "candidate_source_identities": (CANDIDATE_SOURCE,),
        "known_parent_idvs": (PARENT_IDV,),
    }
    values.update(changes)
    return VehicleWatchFrame(**values)


def _query(
    manifest,
    lane: VehicleWatchLane,
    kind: VehicleTargetKind,
    *,
    target_value: str | None = None,
) -> VehicleQuerySpec:
    rows = tuple(
        row for row in manifest.queries
        if row.lane is lane
        and row.target_kind is kind
        and (target_value is None or row.target_value == target_value)
    )
    assert len(rows) == 1
    return rows[0]


def _lead(
    query: VehicleQuerySpec,
    suffix: str = "1",
    *,
    client_id: str = "testco",
    source_identity: SourceIdentity | None = None,
    parent: SourceIdentity | None = None,
    order: SourceIdentity | None = None,
) -> VehicleLead:
    if query.target_kind is VehicleTargetKind.CANDIDATE_SOURCE:
        source_identity = query.target_source_identity
    if query.lane is VehicleWatchLane.USASPENDING_IDV:
        parent = parent or (
            query.target_source_identity
            if query.target_kind is VehicleTargetKind.KNOWN_PARENT_IDV
            else PARENT_IDV
        )
    if query.lane is VehicleWatchLane.USASPENDING_ORDER_LINEAGE:
        parent = parent or query.target_source_identity
        order = order or ORDER
    if query.target_kind is VehicleTargetKind.KNOWN_PARENT_IDV:
        parent = query.target_source_identity
    return VehicleLead(
        lead_id=f"lead-{query.query_id[:12]}-{suffix}",
        client_id=client_id,
        run_id="run-1",
        scope_sha256=HASH,
        query_id=query.query_id,
        lane=query.lane,
        discovered_at=ATTEMPTED_AT,
        title=f"Unverified vehicle record {suffix}",
        source_url=f"https://example.gov/vehicle/{suffix}",
        source_identity=source_identity or SourceIdentity(
            source_system="example.gov",
            record_id=f"record-{suffix}",
        ),
        parent_idv_identity=parent,
        order_identity=order,
    )


def _evidence(lead: VehicleLead, **changes) -> EvidenceRecord:
    values = {
        "evidence_id": f"EV-{lead.lead_id}",
        "client_id": lead.client_id,
        "run_id": lead.run_id,
        "scope_sha256": lead.scope_sha256,
        "source_identity": lead.source_identity,
        "source_tier": SourceTier.PROGRAM,
        "source_kind": EvidenceKind.VEHICLE,
        "source_name": "Official vehicle record",
        "source_url": lead.source_url,
        "retrieved_at": ATTEMPTED_AT,
        "title": "Verified vehicle record",
        "excerpt": "The official record identifies this acquisition vehicle.",
        "official_source": True,
        "primary_source": True,
        "supports": (EvidenceUse.ACCESS,),
    }
    values.update(changes)
    return EvidenceRecord(**values)


def test_manifest_is_deterministic_and_target_addressable():
    original = build_vehicle_query_manifest(_frame(), AS_OF)
    reordered = build_vehicle_query_manifest(_frame(
        client_terms=tuple(reversed(_frame().client_terms)),
        agencies=tuple(reversed(_frame().agencies)),
        naics_codes=tuple(reversed(_frame().naics_codes)),
        psc_codes=tuple(reversed(_frame().psc_codes)),
    ), AS_OF)

    assert reordered == original
    assert original.frame_sha256 == _frame().frame_sha256
    assert len({row.query_id for row in original.queries}) \
        == len(original.queries)
    assert len({(row.lane, row.target_id) for row in original.queries}) \
        == len(original.queries)
    assert {row.lane for row in original.queries} == set(VehicleWatchLane)


def test_maximum_valid_vehicle_census_fits_deliberate_coverage_budget():
    assert VEHICLE_WATCH_SCHEMA_VERSION == "candidate_review_v1.vehicle_watch.v2"
    width = 128
    candidate_width = 1024
    frame = _frame(
        client_terms=tuple(f"client term {index:03d}" for index in range(width)),
        agencies=tuple(f"Agency {index:03d}" for index in range(width)),
        naics_codes=tuple(str(100000 + index) for index in range(width)),
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
    )

    manifest = build_vehicle_query_manifest(frame, AS_OF)

    assert len(manifest.queries) == 4609
    assert len(manifest.queries) < CONTENT_BUDGETS["coverage_records"]


def test_candidate_identity_capacity_rejects_overflow_without_slicing():
    with pytest.raises(ValidationError, match="1024 values"):
        _frame(candidate_source_identities=tuple(
            SourceIdentity(
                source_system="sam.gov",
                record_id=f"notice-{index:04d}",
            )
            for index in range(1025)
        ))


def test_manifest_changes_across_clients_and_collection_rejects_client_bleed():
    original = build_vehicle_query_manifest(_frame(), AS_OF)
    other = build_vehicle_query_manifest(
        _frame(binding=_binding("otherco")), AS_OF
    )
    assert other.manifest_id != original.manifest_id

    target = _query(
        original,
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM,
        target_value="records management",
    )

    def searcher(query: VehicleQuerySpec):
        return (_lead(query, client_id="otherco"),) \
            if query.query_id == target.query_id else ()

    with pytest.raises(ValueError, match="another client"):
        run_vehicle_watch_collection(original, searcher, ATTEMPTED_AT)


def test_absent_restricted_export_is_scope_excluded_not_zero_results():
    manifest = build_vehicle_query_manifest(_frame(), AS_OF)
    calls: list[str] = []

    def searcher(query: VehicleQuerySpec):
        calls.append(query.query_id)
        return ()

    result = run_vehicle_watch_collection(manifest, searcher, ATTEMPTED_AT)
    restricted_query = _query(
        manifest,
        VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT,
        VehicleTargetKind.EMPTY_LANE,
    )
    attempt = {
        row.query_id: row for row in result.attempts
    }[restricted_query.query_id]

    assert restricted_query.required is False
    assert restricted_query.query_id not in calls
    assert attempt.state is CoverageState.SCOPE_EXCLUDED
    assert attempt.attempted_at is None
    assert attempt.returned_zero is False


def test_failed_and_successful_zero_queries_remain_distinct():
    manifest = build_vehicle_query_manifest(_frame(), AS_OF)
    failed_query = next(row for row in manifest.queries if row.required)

    def searcher(query: VehicleQuerySpec):
        if query.query_id == failed_query.query_id:
            raise RuntimeError("secret provider stack")
        return ()

    result = run_vehicle_watch_collection(manifest, searcher, ATTEMPTED_AT)
    attempts = {row.query_id: row for row in result.attempts}
    failure = attempts[failed_query.query_id]
    zero = next(
        row for row in result.attempts
        if row.state is CoverageState.RETURNED
    )

    assert failure.state is CoverageState.FAILED
    assert failure.records_returned == 0
    assert failure.returned_zero is False
    assert "secret" not in failure.public_detail
    assert zero.records_returned == 0
    assert zero.returned_zero is True
    assert zero.attempted_at == ATTEMPTED_AT


def test_partial_stale_and_not_run_are_preserved_as_separate_states():
    snapshot = RestrictedVehicleExport(
        snapshot_id="restricted-export-7",
        source_name="Authorized market intelligence export",
        snapshot_sha256="e" * 64,
        captured_at=datetime(
            2026, 6, 1, 12, 0, tzinfo=timezone.utc
        ),
        fresh_through=date(2026, 6, 30),
        authorization_reference="operator authorization 7",
    )
    manifest = build_vehicle_query_manifest(
        _frame(restricted_export=snapshot), AS_OF
    )
    partial_query = _query(
        manifest,
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM,
        target_value="records management",
    )
    not_run_query = _query(
        manifest,
        VehicleWatchLane.OFFICIAL_AGENCY_PROGRAM_FORECAST,
        VehicleTargetKind.AGENCY,
        target_value="GSA",
    )
    restricted_query = _query(
        manifest,
        VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT,
        VehicleTargetKind.RESTRICTED_SNAPSHOT,
    )
    partial_lead = _lead(partial_query)

    def searcher(query: VehicleQuerySpec):
        if query.query_id == partial_query.query_id:
            return VehicleSearchResponse(
                state=CoverageState.PARTIAL,
                records_returned=2,
                leads=(partial_lead,),
                public_detail="Provider returned a truncated page.",
            )
        if query.query_id == not_run_query.query_id:
            return VehicleSearchResponse(
                state=CoverageState.NOT_RUN,
                public_detail="Authorized provider was unavailable.",
            )
        return ()

    result = run_vehicle_watch_collection(manifest, searcher, ATTEMPTED_AT)
    attempts = {row.query_id: row for row in result.attempts}

    assert attempts[partial_query.query_id].state is CoverageState.PARTIAL
    assert attempts[partial_query.query_id].records_returned == 2
    assert attempts[not_run_query.query_id].state is CoverageState.NOT_RUN
    assert attempts[not_run_query.query_id].attempted_at is None
    assert attempts[restricted_query.query_id].state \
        is CoverageState.STALE_SNAPSHOT


def test_parent_piid_never_creates_a_heuristic_vehicle_name():
    manifest = build_vehicle_query_manifest(_frame(), AS_OF)
    parent_query = _query(
        manifest,
        VehicleWatchLane.USASPENDING_IDV,
        VehicleTargetKind.KNOWN_PARENT_IDV,
    )
    lead_values = _lead(parent_query).model_dump(mode="python")
    lead_values["vehicle_name"] = "Guessed OASIS+"

    assert "IDV-123" in parent_query.query_text
    assert "OASIS" not in parent_query.query_text
    with pytest.raises(ValidationError, match="Extra inputs"):
        VehicleLead.model_validate(lead_values)


def test_order_lineage_keeps_source_parent_and_order_identities_separate():
    manifest = build_vehicle_query_manifest(_frame(), AS_OF)
    query = _query(
        manifest,
        VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
        VehicleTargetKind.KNOWN_PARENT_IDV,
    )
    with pytest.raises(ValidationError, match="parent and order"):
        _lead(query, order=None).model_copy(update={"order_identity": None}) \
            .model_dump()
        VehicleLead(**{
            **_lead(query).model_dump(mode="python"),
            "order_identity": None,
        })

    lead = _lead(query)
    result = run_vehicle_watch_collection(
        manifest,
        lambda row: (lead,) if row.query_id == query.query_id else (),
        ATTEMPTED_AT,
    )
    stored = next(row for row in result.leads if row.lead_id == lead.lead_id)

    assert stored.source_identity.canonical_key != \
        stored.parent_idv_identity.canonical_key
    assert stored.parent_idv_identity == PARENT_IDV
    assert stored.order_identity == ORDER


def test_vehicle_lead_cannot_create_a_candidate_or_vehicle_signal():
    manifest = build_vehicle_query_manifest(_frame(), AS_OF)
    query = _query(
        manifest,
        VehicleWatchLane.GSA_PROGRAM_RECORDS,
        VehicleTargetKind.CLIENT_TERM,
        target_value="records management",
    )
    lead = _lead(query)

    assert lead.creates_candidate is False
    assert "candidate_ids" not in type(lead).model_fields
    with pytest.raises(ValidationError):
        VehicleSignal.model_validate(lead.model_dump(mode="python"))


def test_coverage_projection_is_exact_and_requires_verified_evidence_identity():
    manifest = build_vehicle_query_manifest(_frame(), AS_OF)
    query = _query(
        manifest,
        VehicleWatchLane.GSA_PROGRAM_RECORDS,
        VehicleTargetKind.CLIENT_TERM,
        target_value="records management",
    )
    lead = _lead(query)
    result = run_vehicle_watch_collection(
        manifest,
        lambda row: (lead,) if row.query_id == query.query_id else (),
        ATTEMPTED_AT,
    )
    evidence = _evidence(lead)
    coverage = project_vehicle_watch_coverage(
        result,
        {query.query_id: (evidence,)},
    )
    row = next(item for item in coverage if item.query_id == query.query_id)

    assert len(coverage) == len(manifest.queries)
    assert row.source == VehicleWatchLane.GSA_PROGRAM_RECORDS.value
    assert row.query_family == "vehicle_watch:client_term"
    assert row.watch_target_id == query.target_id
    assert row.records_returned == 1
    assert row.records_accepted == 1
    assert row.accepted_evidence_ids == (evidence.evidence_id,)
    assert row.query_manifest_id == manifest.manifest_id

    unrelated = _evidence(lead, source_identity=SourceIdentity(
        source_system="gsa.gov",
        record_id="unrelated",
    ))
    with pytest.raises(ValueError, match="observed identity"):
        project_vehicle_watch_coverage(
            result,
            {query.query_id: (unrelated,)},
        )

    unverified = _evidence(lead, official_source=False)
    with pytest.raises(ValueError, match="official evidence"):
        project_vehicle_watch_coverage(
            result,
            {query.query_id: (unverified,)},
        )
