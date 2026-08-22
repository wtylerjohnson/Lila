"""Unstubbed contract tests for the CURRENT Assess-run identity seam."""

from __future__ import annotations

import inspect
import json
import logging
import os
import shutil
from datetime import datetime

from agents.assess.approval import required_blocker_manifest
from agents.assess.binding import build_binding, digest
from agents.assess.ledger import (
    assess_client_storage_key,
    assess_projection_input_manifest,
    build_assess_run,
    current_assess_pointer_path,
    current_run_identity,
    persist_assess_run,
)
from tests.test_assess_ledger import NOW, _profile, _sweep


def _seed(tmp_path, monkeypatch):
    """Persist one run through real binding, ledger, and pointer machinery."""
    data_dir = tmp_path / "data"
    review_dir = data_dir / "review"
    cleaned_dir = data_dir / "cleaned"
    state_dir = data_dir / "state" / "assess_runs"
    clients_dir = tmp_path / "clients"
    review_dir.mkdir(parents=True)
    cleaned_dir.mkdir(parents=True)

    searches = _sweep()
    profile = _profile()
    sweep_path = cleaned_dir / "searches_testco.json"
    sweep_path.write_text(json.dumps(searches), encoding="utf-8")
    profile_path = clients_dir / "testco" / "profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8")

    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(state_dir))
    monkeypatch.setattr("tools.capability.CLIENTS_DIR", str(clients_dir))

    binding = build_binding(
        scope_designator="all",
        sweep_artifact=sweep_path.name,
        sweep=searches,
        profile=profile,
    )
    projection_inputs = assess_projection_input_manifest(
        "Testco", review_dir=review_dir)
    run, diagnostics, posting_index = build_assess_run(
        "Testco",
        searches,
        profile,
        binding,
        projection_inputs=projection_inputs,
        as_of=NOW,
    )
    persist_assess_run(
        run,
        binding,
        diagnostics,
        posting_index,
        projection_inputs,
    )
    return {
        "review_dir": review_dir,
        "state_dir": state_dir,
        "sweep_path": sweep_path,
        "run": run,
    }


def test_current_run_identity_returns_exact_frozen_keys(tmp_path, monkeypatch):
    # 2026-07-12 T5: the sidecar seam is a projection of a fully validated
    # CURRENT pointer, not a second pointer validator.
    seeded = _seed(tmp_path, monkeypatch)

    signature = inspect.signature(current_run_identity)
    assert list(signature.parameters) == [
        "client_name", "designator", "review_dir"]
    assert signature.parameters["review_dir"].kind \
        == inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["review_dir"].default is None

    identity = current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"]))

    assert identity is not None
    assert set(identity) == {
        "run_id",
        "pointer_digest",
        "blocker_manifest_sha256",
        "persisted_at",
    }
    assert identity["run_id"] == seeded["run"].run_id
    pointer = json.loads(current_assess_pointer_path(
        "Testco", "all").read_text(encoding="utf-8"))
    assert identity["pointer_digest"] == digest(pointer)
    assert identity["blocker_manifest_sha256"] == \
        required_blocker_manifest(seeded["run"].coverage)["sha256"]
    assert datetime.fromisoformat(identity["persisted_at"]).tzinfo is not None


def test_current_run_identity_absent_is_none_without_warning(
        tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(tmp_path / "state"))
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Never Backfilled", "all", review_dir=str(tmp_path / "review")) is None
    assert not caplog.records


def test_current_run_identity_corrupt_pointer_fails_none_and_logs(
        tmp_path, monkeypatch, caplog):
    seeded = _seed(tmp_path, monkeypatch)
    pointer_path = current_assess_pointer_path("Testco", "all")
    pointer_path.write_text("{not-json", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "validation failed" in caplog.text


def test_current_run_identity_pointer_read_error_is_not_absence(
        tmp_path, monkeypatch, caplog):
    seeded = _seed(tmp_path, monkeypatch)
    pointer_path = current_assess_pointer_path("Testco", "all")
    pointer_path.unlink()
    pointer_path.mkdir()
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "current Assess identity unavailable" in caplog.text
    assert "directory" in caplog.text.lower()


def test_current_run_identity_rejects_pointer_replacement_during_load(
        tmp_path, monkeypatch, caplog):
    # The real loader validates the original pointer. The instrumentation then
    # atomically replaces it before the identity seam's second snapshot.
    import agents.assess.ledger as ledger

    seeded = _seed(tmp_path, monkeypatch)
    pointer_path = current_assess_pointer_path("Testco", "all")
    original = json.loads(pointer_path.read_text(encoding="utf-8"))
    replacement = dict(original)
    replacement["schema_version"] = 999
    replacement["artifact"] = "../" + original["artifact"]
    real_loader = ledger.load_current_assess_run

    def load_then_replace(*args, **kwargs):
        payload = real_loader(*args, **kwargs)
        replacement_path = pointer_path.with_name("replacement.current.json")
        replacement_path.write_text(
            json.dumps(replacement), encoding="utf-8")
        os.replace(replacement_path, pointer_path)
        return payload

    monkeypatch.setattr(ledger, "load_current_assess_run", load_then_replace)
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert replacement["run_id"] == original["run_id"]
    assert replacement["artifact_sha256"] == original["artifact_sha256"]
    assert current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "pointer schema or artifact fields are invalid" in caplog.text


def test_current_run_identity_rejects_semantically_valid_atomic_replacement(
        tmp_path, monkeypatch, caplog):
    import agents.assess.ledger as ledger

    seeded = _seed(tmp_path, monkeypatch)
    pointer_path = current_assess_pointer_path("Testco", "all")
    replacement = json.loads(pointer_path.read_text(encoding="utf-8"))
    replacement["extra_field_allowed_by_loader"] = "replacement inode"
    real_loader = ledger.load_current_assess_run

    def load_then_replace(*args, **kwargs):
        payload = real_loader(*args, **kwargs)
        replacement_path = pointer_path.with_name("replacement.current.json")
        replacement_path.write_text(
            json.dumps(replacement), encoding="utf-8")
        os.replace(replacement_path, pointer_path)
        return payload

    monkeypatch.setattr(ledger, "load_current_assess_run", load_then_replace)
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "pointer changed during validation" in caplog.text


def test_current_run_identity_stale_binding_fails_none_and_logs(
        tmp_path, monkeypatch, caplog):
    seeded = _seed(tmp_path, monkeypatch)
    changed = json.loads(seeded["sweep_path"].read_text(encoding="utf-8"))
    changed["results"]["sam.gov"][0]["title"] = "Changed after persist"
    seeded["sweep_path"].write_text(json.dumps(changed), encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "sweep evidence changed" in caplog.text


def test_current_run_identity_stale_projection_input_fails_none_and_logs(
        tmp_path, monkeypatch, caplog):
    seeded = _seed(tmp_path, monkeypatch)
    (seeded["review_dir"] / "testco.horizon.json").write_text(
        json.dumps({"changed": True}), encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Testco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "review or reference inputs changed" in caplog.text


def test_current_run_identity_cross_client_pointer_fails_none_and_logs(
        tmp_path, monkeypatch, caplog):
    seeded = _seed(tmp_path, monkeypatch)
    source_root = seeded["state_dir"] / assess_client_storage_key("Testco")
    other_root = seeded["state_dir"] / assess_client_storage_key("Otherco")
    shutil.copytree(source_root, other_root)
    caplog.set_level(logging.WARNING, logger="agents.assess.ledger")

    assert current_run_identity(
        "Otherco", "all", review_dir=str(seeded["review_dir"])) is None
    assert "validation failed" in caplog.text
