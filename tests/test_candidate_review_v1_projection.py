"""Pure projection contract for Candidate Review v1 editor behavior."""

from datetime import datetime, timezone

import pytest

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateKind,
    CandidateMember,
    CandidateReviewDocument,
    CandidateUnit,
    ClusterLevel,
    DecisionBoundary,
    EditorState,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    KpiKind,
    KpiTile,
    LifecycleKind,
    SourceIdentity,
    SourceTier,
    StrategicCorridorIdentity,
    TickerItem,
    VehicleAccessPosture,
    VehicleIdentity,
    VehicleRelationship,
    VehicleRelationshipKind,
    VehicleSignal,
    VehicleSignalKind,
    VehicleSignalStatus,
)
from agents.candidate_review_v1.projection import (
    project_candidate_review,
    project_clean_export,
    reorder_candidates,
    soft_delete_candidate,
    undo_soft_delete,
)


NOW = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)
SCOPE_HASH = "a" * 64


def _evidence(evidence_id: str) -> EvidenceRecord:
    record_id = evidence_id.removeprefix("ev-")
    return EvidenceRecord(
        evidence_id=evidence_id,
        client_id="riverbed",
        run_id="run-1",
        scope_sha256=SCOPE_HASH,
        source_identity=SourceIdentity(
            source_system="usaspending.gov",
            record_id=record_id,
        ),
        source_tier=SourceTier.MARKET,
        source_kind=EvidenceKind.AWARD,
        source_name="USAspending",
        source_url=f"https://www.usaspending.gov/award/{record_id}",
        retrieved_at=NOW,
        title=f"Award {record_id}",
        excerpt=f"Source record for {record_id}",
        official_source=True,
        primary_source=True,
        supports=(EvidenceUse.BUYER,),
    )


def _candidate(candidate_id: str, member_id: str, *, shared: bool = False) \
        -> CandidateUnit:
    boundary = DecisionBoundary(
        buyer_key=f"buyer-{candidate_id}",
        eligibility_key="unrestricted",
        lifecycle=LifecycleKind.RECOMPETE_RESEARCH,
        program_key=f"program-{candidate_id}",
        access_route_key="research",
        analyst_action_key=f"validate-{candidate_id}",
    )
    return CandidateUnit(
        candidate_id=candidate_id,
        client_id="riverbed",
        kind=CandidateKind.RESEARCH_CORRIDOR,
        title=f"Corridor {candidate_id}",
        agency="Agency",
        lifecycle=LifecycleKind.RECOMPETE_RESEARCH,
        cluster_level=ClusterLevel.STRATEGIC_CORRIDOR,
        members=(CandidateMember(
            source_identity=SourceIdentity(
                source_system="usaspending.gov",
                record_id=member_id,
            ),
            member_evidence_ids=(f"ev-{member_id}",),
            decision_boundary=boundary,
        ),),
        strategic_corridor=StrategicCorridorIdentity(
            corridor_id=f"corridor-{candidate_id}",
            decision_boundary=boundary,
        ),
        supporting_evidence_ids=("ev-shared",) if shared else (),
        records_show=f"Records show activity for {candidate_id}.",
        may_suggest=f"This may suggest a route for {candidate_id}.",
        validate_next=f"Validate the next milestone for {candidate_id}.",
        cluster_reason="The member shares one buyer action.",
        strategic_action="Research the acquisition path.",
        distinctness_explanation="The buyer and action are distinct.",
        inference_chain="Award evidence leads to a research question.",
        falsifier="An official cancellation would falsify the route.",
        watch_trigger="A published notice would change the timing.",
    )


def _document() -> CandidateReviewDocument:
    candidates = (
        _candidate("c-a", "a", shared=True),
        _candidate("c-b", "b", shared=True),
        _candidate("c-c", "c"),
    )
    evidence = tuple(
        _evidence(evidence_id)
        for evidence_id in ("ev-a", "ev-b", "ev-c", "ev-shared", "ev-global")
    )
    ticker_items = (
        TickerItem(
            ticker_id="t-a",
            label="Candidate A",
            source_evidence_id="ev-a",
            evidence_ids=("ev-a",),
            candidate_ids=("c-a",),
        ),
        TickerItem(
            ticker_id="t-shared",
            label="Shared signal",
            source_evidence_id="ev-shared",
            evidence_ids=("ev-shared",),
            candidate_ids=("c-a", "c-b"),
        ),
        TickerItem(
            ticker_id="t-b",
            label="Candidate B",
            source_evidence_id="ev-b",
            evidence_ids=("ev-b",),
            candidate_ids=("c-b",),
        ),
        TickerItem(
            ticker_id="t-global",
            label="Historical context",
            source_evidence_id="ev-global",
            evidence_ids=("ev-global",),
            historical_context=True,
        ),
        TickerItem(
            ticker_id="t-c",
            label="Candidate C",
            source_evidence_id="ev-c",
            evidence_ids=("ev-c",),
            candidate_ids=("c-c",),
        ),
    )
    return CandidateReviewDocument(
        document_id="riverbed-review-1",
        baseline_document_sha256="d" * 64,
        client_name="Riverbed",
        binding=ArtifactBinding(
            client_id="riverbed",
            client_name="Riverbed",
            run_id="run-1",
            scope_designator="all-federal",
            scope_sha256=SCOPE_HASH,
            profile_sha256="b" * 64,
            evidence_snapshot_sha256="c" * 64,
        ),
        as_of=NOW,
        generated_at=NOW,
        candidates=candidates,
        evidence=evidence,
        ticker_items=ticker_items,
        kpi_tiles=(KpiTile(
            kpi_id="candidate-count",
            kind=KpiKind.CANDIDATE_COUNT,
            eyebrow="Review queue",
            value="3",
            title="Candidate opportunities",
            note="Visible candidate count",
            computed=True,
        ),),
        editor_state=EditorState(
            client_id="riverbed",
            document_id="riverbed-review-1",
            baseline_document_sha256="d" * 64,
            candidate_order=tuple(item.candidate_id for item in candidates),
        ),
    )


def _delete_a(document: CandidateReviewDocument) -> CandidateReviewDocument:
    return soft_delete_candidate(
        document,
        "c-a",
        deleted_at=NOW,
        undo_token="undo-c-a",
    )


def _document_with_vehicle_signal() -> CandidateReviewDocument:
    document = _document()
    excerpt = "OASIS+ is the vehicle named by parent IDV IDV-1."
    evidence = EvidenceRecord(
        evidence_id="ev-vehicle",
        client_id="riverbed",
        run_id="run-1",
        scope_sha256=SCOPE_HASH,
        source_identity=SourceIdentity(
            source_system="usaspending.gov",
            record_id="IDV-1",
        ),
        source_tier=SourceTier.PROGRAM,
        source_kind=EvidenceKind.VEHICLE,
        source_name="Official vehicle record",
        source_url="https://www.usaspending.gov/award/IDV-1",
        retrieved_at=NOW,
        title="OASIS+ vehicle record",
        excerpt=excerpt,
        official_source=True,
        primary_source=True,
        supports=(EvidenceUse.ACCESS,),
        assertion_spans=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.VEHICLE_IDENTITY,
            quote=excerpt,
        ),),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-1",
        agency="GSA",
        name="OASIS+",
        idv_piid="IDV-1",
        evidence_ids=(evidence.evidence_id,),
    )
    signal = VehicleSignal(
        signal_id="signal:vehicle-access",
        client_id="riverbed",
        run_id="run-1",
        scope_sha256=SCOPE_HASH,
        title="OASIS+ access research",
        agency="GSA",
        kind=VehicleSignalKind.ACCESS_PATH,
        status=VehicleSignalStatus.RESEARCH,
        vehicle=identity,
        relationship=VehicleRelationship(
            relationship_id="relationship:vehicle-access",
            vehicle_id=identity.vehicle_id,
            kind=VehicleRelationshipKind.VEHICLE_ONLY,
            access_posture=VehicleAccessPosture.UNKNOWN,
            evidence_ids=(evidence.evidence_id,),
        ),
        candidate_ids=("c-a",),
        records_show="The official record identifies the parent IDV.",
        may_suggest="The vehicle may be relevant to later agency ordering.",
        validate_next="Confirm holder or teaming access before capture planning.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=NOW,
    )
    return CandidateReviewDocument.model_validate({
        **document.model_dump(mode="python"),
        "vehicle_signals": (signal,),
        "evidence": (*document.evidence, evidence),
    })


def test_reorder_drives_candidate_numbering_and_candidate_linked_ticker_order():
    document = _document()
    reordered = reorder_candidates(document, ("c-c", "c-a", "c-b"))
    projection = project_candidate_review(reordered)

    assert document.editor_state.candidate_order == ("c-a", "c-b", "c-c")
    assert reordered.editor_state.candidate_order == ("c-c", "c-a", "c-b")
    assert reordered.editor_state.revision == 1
    assert projection.candidate_ids == ("c-c", "c-a", "c-b")
    assert [item.number for item in projection.candidates] == ["01", "02", "03"]
    assert [item.ticker_id for item in projection.ticker_items] == [
        "t-c", "t-a", "t-shared", "t-b", "t-global",
    ]
    assert projection.evidence_ids == ("ev-c", "ev-a", "ev-shared", "ev-b")
    assert projection.kpi_tiles[0].value == "3"
    assert reorder_candidates(reordered, projection.candidate_ids) is reordered


def test_soft_delete_updates_visible_count_numbering_ticker_and_orphan_evidence():
    deleted = _delete_a(_document())
    projection = project_candidate_review(deleted)

    # Recovery data remains in the canonical draft.
    assert [item.candidate_id for item in deleted.candidates] \
        == ["c-a", "c-b", "c-c"]
    assert deleted.editor_state.tombstones[0].candidate_id == "c-a"
    assert deleted.editor_state.undo.token == "undo-c-a"
    CandidateReviewDocument.model_validate(deleted.model_dump(mode="python"))

    # Visible state is compressed and independently numbered.
    assert projection.candidate_count == 2
    assert projection.candidate_ids == ("c-b", "c-c")
    assert [item.number for item in projection.candidates] == ["01", "02"]
    assert projection.kpi_tiles[0].value == "2"

    # A-only ticker and evidence disappear. Shared evidence remains for B and
    # the ticker no longer leaks the deleted candidate ID.
    assert [item.ticker_id for item in projection.ticker_items] == [
        "t-shared", "t-b", "t-c", "t-global",
    ]
    shared_ticker = projection.ticker_items[0]
    assert shared_ticker.candidate_ids == ("c-b",)
    assert "ev-a" not in projection.evidence_ids
    assert "ev-shared" in projection.evidence_ids
    assert projection.evidence_ids == ("ev-b", "ev-shared", "ev-c")
    # The ticker is not allowed to make the Evidence dock self-referential.
    # Its immutable source still resolves in the canonical document, but a
    # ticker-only record is not a visible dock row.
    assert "ev-global" not in projection.evidence_ids
    assert "ev-global" in projection.source_evidence_ids
    assert {
        evidence_id
        for item in projection.ticker_items
        for evidence_id in item.evidence_ids
    }.issubset(set(projection.source_evidence_ids))


def test_vehicle_signal_survives_linked_candidate_deletion_without_counting():
    document = _document_with_vehicle_signal()
    before = project_candidate_review(document)
    deleted = _delete_a(document)
    after = project_clean_export(deleted)

    assert before.candidate_count == 3
    assert len(before.vehicle_signals) == 1
    assert before.vehicle_signals[0].candidate_ids == ("c-a",)
    assert after.candidate_count == 2
    assert len(after.vehicle_signals) == 1
    assert after.vehicle_signals[0].candidate_ids == ()
    assert "ev-vehicle" in after.evidence_ids
    assert "ev-vehicle" in after.source_evidence_ids


def test_visible_reorder_keeps_tombstone_recoverable_and_undo_restores_prior_order():
    deleted = _delete_a(_document())
    reordered = reorder_candidates(deleted, ("c-c", "c-b"))

    assert reordered.editor_state.candidate_order == ("c-a", "c-c", "c-b")
    assert project_candidate_review(reordered).candidate_ids == ("c-c", "c-b")

    restored = undo_soft_delete(reordered, undo_token="undo-c-a")
    assert restored.editor_state.candidate_order == ("c-a", "c-b", "c-c")
    assert restored.editor_state.tombstones == ()
    assert restored.editor_state.undo is None
    assert project_candidate_review(restored).candidate_ids \
        == ("c-a", "c-b", "c-c")


def test_clean_export_removes_recovery_state_and_every_hidden_reference():
    deleted = _delete_a(_document())
    draft = project_candidate_review(deleted)
    exported = project_clean_export(deleted)

    assert draft.editor_state.tombstones
    assert draft.editor_state.undo is not None
    assert exported.clean_export is True
    assert exported.candidate_ids == ("c-b", "c-c")
    assert exported.editor_state.candidate_order == ("c-b", "c-c")
    assert exported.editor_state.tombstones == ()
    assert exported.editor_state.undo is None
    assert all("c-a" not in item.candidate_ids for item in exported.ticker_items)
    assert "ev-a" not in exported.evidence_ids
    assert "ev-a" not in exported.source_evidence_ids
    assert "ev-global" in exported.source_evidence_ids

    serialized = repr(exported)
    assert "undo-c-a" not in serialized
    assert "ev-a" not in serialized
    assert "candidate_id='c-a'" not in serialized


def test_projection_operations_reject_ambiguous_or_stale_editor_actions():
    document = _document()
    with pytest.raises(ValueError, match="every visible candidate"):
        reorder_candidates(document, ("c-a", "c-b"))
    with pytest.raises(ValueError, match="duplicate"):
        reorder_candidates(document, ("c-a", "c-a", "c-c"))
    with pytest.raises(ValueError, match="unknown candidate"):
        soft_delete_candidate(
            document,
            "missing",
            deleted_at=NOW,
            undo_token="undo-missing",
        )

    deleted = _delete_a(document)
    with pytest.raises(ValueError, match="already deleted"):
        _delete_a(deleted)
    with pytest.raises(ValueError, match="does not match"):
        undo_soft_delete(deleted, undo_token="stale-token")
