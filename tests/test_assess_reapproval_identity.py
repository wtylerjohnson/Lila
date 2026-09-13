"""Cycle 5 item 8: content-identical Assess reapproval is pointer-stable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import agents.assess.approval as approval_module
from agents.assess import current_run_identity
from agents.assess.approval import approval_path, approve_assess_results
from agents.assess.ledger import current_assess_pointer_path
from tests.test_assess_approval import CLIENT, _seed
from tests.test_live_report_truth import _cutover_sweep, _persist, _seed_runtime
from tools.assess_refresh import refresh_current_assess_run_if_active


class _ApprovalClock(datetime):
    value = datetime(2026, 7, 10, 12, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.value if tz is None else cls.value.astimezone(tz)


def _immutable_artifacts(state_dir: Path, pointer: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(state_dir)): path.read_bytes()
        for path in state_dir.rglob("*.json")
        if path != pointer
    }


def test_identical_reapproval_keeps_current_run_and_pointer_identity(
        tmp_path, monkeypatch):
    """Exercise the real writer, creation-free refresh, and T5 identity seam."""
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)  # explicit test setup for a client already cut over
    monkeypatch.setattr(approval_module, "datetime", _ApprovalClock)

    first = approve_assess_results(
        "Testco", note="review complete", review_dir=str(seed["review_dir"]),
        partial_release_approved=None)
    refreshed = refresh_current_assess_run_if_active(
        "Testco", sweep_path=seed["sweep_path"], state_dir=seed["state_dir"],
        review_dir=seed["review_dir"])
    assert refreshed.startswith("refreshed assess:v2:")

    pointer = current_assess_pointer_path(
        "Testco", "all", state_dir=seed["state_dir"])
    approval_file = Path(approval_path(
        "Testco", review_dir=str(seed["review_dir"])))
    pointer_before = pointer.read_bytes()
    pointer_mtime_before = pointer.stat().st_mtime_ns
    approval_before = approval_file.read_bytes()
    approval_mtime_before = approval_file.stat().st_mtime_ns
    artifacts_before = _immutable_artifacts(Path(seed["state_dir"]), pointer)
    identity_before = current_run_identity(
        "Testco", "all", review_dir=str(seed["review_dir"]))
    assert identity_before is not None

    _ApprovalClock.value += timedelta(hours=1)
    second = approve_assess_results(
        "Testco", note="review complete", review_dir=str(seed["review_dir"]),
        partial_release_approved=None)
    refreshed_again = refresh_current_assess_run_if_active(
        "Testco", sweep_path=seed["sweep_path"], state_dir=seed["state_dir"],
        review_dir=seed["review_dir"])

    assert refreshed_again.startswith("refreshed assess:v2:")
    assert second == first
    assert approval_file.read_bytes() == approval_before
    assert approval_file.stat().st_mtime_ns == approval_mtime_before
    assert pointer.read_bytes() == pointer_before
    assert pointer.stat().st_mtime_ns == pointer_mtime_before
    assert _immutable_artifacts(Path(seed["state_dir"]), pointer) \
        == artifacts_before
    assert current_run_identity(
        "Testco", "all", review_dir=str(seed["review_dir"])) \
        == identity_before


def test_changed_note_and_invalid_prior_timestamp_still_rewrite(
        tmp_path, monkeypatch):
    review_dir, _sweep_path, _profile_path = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(approval_module, "datetime", _ApprovalClock)
    first = approve_assess_results(
        CLIENT, note="first review", review_dir=str(review_dir))
    approval_file = Path(approval_path(CLIENT, review_dir=str(review_dir)))

    _ApprovalClock.value += timedelta(hours=1)
    changed = approve_assess_results(
        CLIENT, note="focused re-review", review_dir=str(review_dir))
    assert changed["note"] == "focused re-review"
    assert changed["approved_at"] != first["approved_at"]

    malformed = dict(changed, approved_at="2026-07-10T14:01:00")
    approval_file.write_text(
        approval_module.json.dumps(malformed), encoding="utf-8")
    _ApprovalClock.value += timedelta(hours=1)
    repaired = approve_assess_results(
        CLIENT, note="focused re-review", review_dir=str(review_dir))
    assert repaired["approved_at"] == _ApprovalClock.value.isoformat(
        timespec="seconds")
    assert repaired != malformed


def test_transient_optional_manifest_failure_keeps_current_identity(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    monkeypatch.setattr(approval_module, "datetime", _ApprovalClock)
    approve_assess_results(
        "Testco", note="review complete", review_dir=str(seed["review_dir"]),
        partial_release_approved=None)
    assert refresh_current_assess_run_if_active(
        "Testco", sweep_path=seed["sweep_path"], state_dir=seed["state_dir"],
        review_dir=seed["review_dir"]).startswith("refreshed assess:v2:")

    pointer = current_assess_pointer_path(
        "Testco", "all", state_dir=seed["state_dir"])
    approval_file = Path(approval_path(
        "Testco", review_dir=str(seed["review_dir"])))
    pointer_before = pointer.read_bytes()
    approval_before = approval_file.read_bytes()
    identity_before = current_run_identity(
        "Testco", "all", review_dir=str(seed["review_dir"]))
    assert identity_before is not None

    def _manifest_unavailable(*_args, **_kwargs):
        raise OSError("temporary manifest read failure")

    monkeypatch.setattr(
        approval_module, "evidence_manifest", _manifest_unavailable)
    _ApprovalClock.value += timedelta(hours=1)
    approve_assess_results(
        "Testco", note="review complete", review_dir=str(seed["review_dir"]),
        partial_release_approved=None)
    assert refresh_current_assess_run_if_active(
        "Testco", sweep_path=seed["sweep_path"], state_dir=seed["state_dir"],
        review_dir=seed["review_dir"]).startswith("refreshed assess:v2:")

    assert approval_file.read_bytes() == approval_before
    assert pointer.read_bytes() == pointer_before
    assert current_run_identity(
        "Testco", "all", review_dir=str(seed["review_dir"])) \
        == identity_before
