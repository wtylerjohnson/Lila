"""Focused fail-closed tests for Candidate Review generation persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.contracts import ArtifactBinding
import agents.candidate_review_v1.persistence as persistence
from agents.candidate_review_v1.persistence import (
    CURRENT_POINTER_FILENAME,
    GENERATION_RECEIPT_FILENAME,
    CurrentGenerationPointer,
    GenerationPersistenceError,
    GenerationReceipt,
    find_reusable_generation,
    generation_basis_sha256,
    load_current_generation,
    persist_generation,
)


NOW = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 22, 21, 0, tzinfo=timezone.utc)


def _binding(*, run_id: str = "run-1", scope_hash: str = "a" * 64):
    return ArtifactBinding(
        client_id="testco",
        client_name="Testco",
        run_id=run_id,
        scope_designator="agency_dhs",
        scope_sha256=scope_hash,
        profile_sha256="b" * 64,
        evidence_snapshot_sha256="c" * 64,
    )


def _payloads():
    return {
        "registries": {
            "event-watch-universe": {
                "schema_version": "event-watch.v1",
                "targets": ["AFCEA", "ACT-IAC"],
            },
        },
        "manifests": {
            "event-query-manifest": {
                "schema_version": "event-query.v2",
                "query_ids": ["q-1", "q-2"],
            },
            "vehicle-query-manifest": {
                "schema_version": "vehicle-query.v1",
                "query_ids": ["v-1"],
            },
        },
        "artifacts": {
            "candidate-review-document": {
                "schema_version": "candidate-review.v1",
                "candidate_count": 2,
            },
            "coverage-receipt": {
                "schema_version": "coverage.v1",
                "comprehensive": False,
                "note": "This fixture makes no current-coverage claim.",
            },
        },
    }


def _commit(root: Path, *, binding=None, at=NOW):
    payloads = _payloads()
    return persist_generation(
        binding or _binding(),
        **payloads,
        state_root=root,
        committed_at=at,
    )


def test_commit_layout_binds_exact_bytes_and_writes_receipt_pointer_last(
    tmp_path,
    monkeypatch,
):
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    writes = []
    real_write = persistence.atomic_write_json

    def record_write(path, payload):
        writes.append(Path(path))
        return real_write(path, payload)

    monkeypatch.setattr(persistence, "atomic_write_json", record_write)
    result = _commit(state_root)

    assert result.reused is False
    client_root = state_root / "testco"
    generation = client_root / "generations" / "run-1"
    assert writes[-2:] == [
        generation / GENERATION_RECEIPT_FILENAME,
        client_root / CURRENT_POINTER_FILENAME,
    ]
    assert all(
        "/registries/" in str(path)
        or "/manifests/" in str(path)
        or "/artifacts/" in str(path)
        for path in writes[:-2]
    )

    pointer_raw = (client_root / CURRENT_POINTER_FILENAME).read_bytes()
    pointer = CurrentGenerationPointer.model_validate_json(pointer_raw)
    receipt_path = client_root / pointer.receipt_path
    receipt_raw = receipt_path.read_bytes()
    receipt = GenerationReceipt.model_validate_json(receipt_raw)
    assert pointer.receipt_sha256 == hashlib.sha256(receipt_raw).hexdigest()
    assert receipt.binding == _binding()
    assert result.generation.pointer == pointer
    assert result.generation.receipt == receipt
    assert result.generation.artifacts["candidate-review-document"][
        "candidate_count"
    ] == 2

    for row in (
        *receipt.registry_files,
        *receipt.manifest_files,
        *receipt.artifact_files,
    ):
        raw = (client_root / row.relative_path).read_bytes()
        assert len(raw) == row.byte_size
        assert hashlib.sha256(raw).hexdigest() == row.sha256


def test_basis_is_stable_for_equivalent_mapping_order_and_run_bound():
    payloads = _payloads()
    first = generation_basis_sha256(
        _binding(),
        registries=payloads["registries"],
        manifests=payloads["manifests"],
    )
    reordered = generation_basis_sha256(
        _binding(),
        registries={
            "event-watch-universe": {
                "targets": ["AFCEA", "ACT-IAC"],
                "schema_version": "event-watch.v1",
            },
        },
        manifests=dict(reversed(tuple(payloads["manifests"].items()))),
    )
    other_run = generation_basis_sha256(
        _binding(run_id="run-2"),
        registries=payloads["registries"],
        manifests=payloads["manifests"],
    )

    assert reordered == first
    assert other_run != first


def test_exact_same_basis_and_output_reuses_without_timestamp_or_writes(
    tmp_path,
    monkeypatch,
):
    state_root = tmp_path / "state"
    first = _commit(state_root)
    pointer_path = state_root / "testco" / CURRENT_POINTER_FILENAME
    pointer_before = pointer_path.read_bytes()

    writes = []
    real_write = persistence.atomic_write_json

    def record_write(path, payload):
        writes.append(Path(path))
        return real_write(path, payload)

    monkeypatch.setattr(persistence, "atomic_write_json", record_write)
    second = _commit(state_root, at=LATER)

    assert second.reused is True
    assert second.generation.receipt == first.generation.receipt
    assert second.generation.receipt.committed_at == NOW
    assert pointer_path.read_bytes() == pointer_before
    assert writes == []


def test_same_basis_with_different_output_fails_instead_of_hiding_drift(tmp_path):
    state_root = tmp_path / "state"
    first = _commit(state_root)
    payloads = _payloads()
    payloads["artifacts"]["candidate-review-document"]["candidate_count"] = 3

    with pytest.raises(
        GenerationPersistenceError,
        match="same generation basis produced different artifact bytes",
    ):
        persist_generation(
            _binding(),
            **payloads,
            state_root=state_root,
            committed_at=LATER,
        )

    assert load_current_generation(
        "testco", state_root=state_root
    ).receipt == first.generation.receipt


def test_reuse_never_relabels_a_generation_under_another_run(tmp_path):
    state_root = tmp_path / "state"
    first = _commit(state_root, binding=_binding(run_id="run-1"))
    second = _commit(
        state_root,
        binding=_binding(run_id="run-2"),
        at=LATER,
    )

    assert first.reused is False
    assert second.reused is False
    assert second.generation.receipt.binding.run_id == "run-2"
    assert (
        state_root / "testco" / "generations" / "run-1"
        / GENERATION_RECEIPT_FILENAME
    ).is_file()
    assert (
        state_root / "testco" / "generations" / "run-2"
        / GENERATION_RECEIPT_FILENAME
    ).is_file()
    assert find_reusable_generation(
        _binding(run_id="run-1"),
        first.generation.receipt.basis_sha256,
        state_root=state_root,
    ) is None


def test_retrying_an_older_run_cannot_roll_current_pointer_backward(tmp_path):
    state_root = tmp_path / "state"
    _commit(state_root, binding=_binding(run_id="run-1"), at=NOW)
    latest = _commit(
        state_root,
        binding=_binding(run_id="run-2"),
        at=LATER,
    )

    with pytest.raises(
        GenerationPersistenceError,
        match="cannot promote an older generation",
    ):
        _commit(
            state_root,
            binding=_binding(run_id="run-1"),
            at=LATER + timedelta(hours=1),
        )

    current = load_current_generation("testco", state_root=state_root)
    assert current is not None
    assert current.pointer == latest.generation.pointer
    assert current.receipt.binding.run_id == "run-2"


def test_failed_mid_generation_is_unloadable_then_resumes_exact_missing_files(
    tmp_path,
    monkeypatch,
):
    state_root = tmp_path / "state"
    real_write = persistence.atomic_write_json

    def fail_on_manifest(path, payload):
        if "/manifests/" in str(path):
            raise OSError("simulated manifest write failure")
        return real_write(path, payload)

    monkeypatch.setattr(persistence, "atomic_write_json", fail_on_manifest)
    with pytest.raises(OSError, match="simulated manifest write failure"):
        _commit(state_root)

    client_root = state_root / "testco"
    assert not (client_root / CURRENT_POINTER_FILENAME).exists()
    assert not (
        client_root / "generations" / "run-1" / GENERATION_RECEIPT_FILENAME
    ).exists()
    assert load_current_generation("testco", state_root=state_root) is None

    monkeypatch.setattr(persistence, "atomic_write_json", real_write)
    resumed = _commit(state_root, at=LATER)
    assert resumed.reused is False
    assert resumed.generation.receipt.committed_at == LATER
    assert load_current_generation(
        "testco", state_root=state_root
    ).receipt == resumed.generation.receipt


@pytest.mark.parametrize("ambiguous_kind", ("mismatch", "unknown", "symlink"))
def test_partial_resume_rejects_mismatch_unknown_files_and_symlinks(
    tmp_path,
    monkeypatch,
    ambiguous_kind,
):
    state_root = tmp_path / "state"
    real_write = persistence.atomic_write_json

    def fail_on_manifest(path, payload):
        if "/manifests/" in str(path):
            raise OSError("simulated manifest write failure")
        return real_write(path, payload)

    monkeypatch.setattr(persistence, "atomic_write_json", fail_on_manifest)
    with pytest.raises(OSError):
        _commit(state_root)
    monkeypatch.setattr(persistence, "atomic_write_json", real_write)

    generation = state_root / "testco" / "generations" / "run-1"
    registry = generation / "registries" / "event-watch-universe.json"
    if ambiguous_kind == "mismatch":
        registry.write_text('{"changed":true}\n', encoding="utf-8")
        match = "does not match the expected bytes"
    elif ambiguous_kind == "unknown":
        (generation / "unreceipted.json").write_text("{}\n", encoding="utf-8")
        match = "unknown file"
    else:
        (generation / "unexpected-link").symlink_to(registry)
        match = "symlink"

    with pytest.raises(GenerationPersistenceError, match=match):
        _commit(state_root, at=LATER)
    assert load_current_generation("testco", state_root=state_root) is None


def test_forged_pointer_to_partial_generation_fails_closed(tmp_path):
    state_root = tmp_path / "state"
    client_root = state_root / "testco"
    partial = client_root / "generations" / "run-1"
    partial.mkdir(parents=True)
    pointer = {
        "schema_version": persistence.CURRENT_POINTER_SCHEMA_VERSION,
        "client_id": "testco",
        "client_name": "Testco",
        "run_id": "run-1",
        "scope_designator": "agency_dhs",
        "scope_sha256": "a" * 64,
        "profile_sha256": "b" * 64,
        "evidence_snapshot_sha256": "c" * 64,
        "basis_sha256": "d" * 64,
        "receipt_path": "generations/run-1/generation.receipt.json",
        "receipt_sha256": "e" * 64,
        "committed_at": NOW.isoformat(),
    }
    persistence.atomic_write_json(
        client_root / CURRENT_POINTER_FILENAME,
        pointer,
    )

    with pytest.raises(
        GenerationPersistenceError,
        match="generation receipt is unavailable",
    ):
        load_current_generation("testco", state_root=state_root)


def test_artifact_and_receipt_tampering_are_rejected_by_exact_sha(tmp_path):
    state_root = tmp_path / "state"
    committed = _commit(state_root)
    client_root = state_root / "testco"
    artifact = committed.generation.receipt.artifact_files[0]
    artifact_path = client_root / artifact.relative_path
    artifact_path.write_text(json.dumps({"tampered": True}), encoding="utf-8")

    with pytest.raises(GenerationPersistenceError, match="failed exact SHA"):
        load_current_generation("testco", state_root=state_root)

    # Use an independent valid current generation, then alter its receipt.
    receipt_state_root = tmp_path / "receipt-state"
    second = _commit(
        receipt_state_root,
        binding=_binding(run_id="run-2"),
        at=LATER,
    )
    client_root = receipt_state_root / "testco"
    receipt_path = client_root / second.generation.pointer.receipt_path
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_payload["committed_at"] = NOW.isoformat()
    persistence.atomic_write_json(receipt_path, receipt_payload)

    with pytest.raises(
        GenerationPersistenceError,
        match="receipt failed exact SHA",
    ):
        load_current_generation("testco", state_root=receipt_state_root)


def test_pointer_path_traversal_and_symlink_escape_are_rejected(tmp_path):
    state_root = tmp_path / "state"
    _commit(state_root)
    pointer_path = state_root / "testco" / CURRENT_POINTER_FILENAME
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["receipt_path"] = "../escape.json"
    persistence.atomic_write_json(pointer_path, pointer)

    with pytest.raises(
        GenerationPersistenceError,
        match="pointer validation failed",
    ):
        load_current_generation("testco", state_root=state_root)

    other_root = tmp_path / "other-state"
    client_root = other_root / "testco"
    client_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (client_root / "generations").symlink_to(outside, target_is_directory=True)
    with pytest.raises(GenerationPersistenceError, match="symlink"):
        _commit(other_root)


def test_present_corrupt_pointer_is_not_treated_as_absent(tmp_path):
    state_root = tmp_path / "state"
    pointer = state_root / "testco" / CURRENT_POINTER_FILENAME
    pointer.parent.mkdir(parents=True)
    pointer.write_text("{not-json", encoding="utf-8")

    with pytest.raises(GenerationPersistenceError, match="not valid JSON"):
        load_current_generation("testco", state_root=state_root)


def test_receipt_and_pointer_contracts_are_frozen_and_forbid_extra_fields(
    tmp_path,
):
    committed = _commit(tmp_path / "state")
    with pytest.raises(ValidationError, match="frozen"):
        committed.generation.receipt.basis_sha256 = "f" * 64

    payload = committed.generation.pointer.model_dump(mode="json")
    payload["coverage_current"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CurrentGenerationPointer.model_validate(payload)


@pytest.mark.parametrize(
    "bad_run_id",
    ("../run", "run/one", ".", "run one", "run\\one"),
)
def test_unsafe_run_ids_never_become_storage_paths(tmp_path, bad_run_id):
    payloads = _payloads()
    with pytest.raises(ValueError, match="run_id"):
        persist_generation(
            _binding(run_id=bad_run_id),
            **payloads,
            state_root=tmp_path / "state",
        )


def test_expected_binding_and_basis_are_revalidated_on_load(tmp_path):
    state_root = tmp_path / "state"
    committed = _commit(state_root)

    loaded = load_current_generation(
        "testco",
        state_root=state_root,
        expected_binding=_binding(),
        expected_basis_sha256=committed.generation.receipt.basis_sha256,
    )
    assert loaded is not None

    with pytest.raises(GenerationPersistenceError, match="expected binding"):
        load_current_generation(
            "testco",
            state_root=state_root,
            expected_binding=_binding(scope_hash="f" * 64),
        )
    with pytest.raises(GenerationPersistenceError, match="expected basis"):
        load_current_generation(
            "testco",
            state_root=state_root,
            expected_basis_sha256="f" * 64,
        )
