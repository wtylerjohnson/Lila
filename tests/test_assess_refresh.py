"""Creation-free strict-run refresh (2026-07-12): the cutover pointer is
operator-owned. Runners and gate hooks refresh an EXISTING pointer only; the
2026-07-12 NETSCOUT DHS sweep proved the old unconditional materialize was an
implicit cutover trigger."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.ledger import (  # noqa: E402
    assess_client_storage_key,
    current_assess_pointer_path,
)
from tools.assess_refresh import (  # noqa: E402
    activate_current_assess_run,
    refresh_current_assess_run_if_active,
)


def _pointer_root(tmp_path, monkeypatch, client="Testco"):
    state_dir = tmp_path / "assess_runs"
    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(state_dir))
    return state_dir / assess_client_storage_key(client)


class _Run:
    run_id = "assess:v1:fresh"


def _spy_materialize(calls):
    def _fake(client_name, **kwargs):
        calls.append((client_name, kwargs))
        return _Run(), "run.json", ()
    return _fake


class TestCreationFreeRefresh:
    def test_absent_pointer_skips_and_creates_nothing(
            self, tmp_path, monkeypatch):
        root = _pointer_root(tmp_path, monkeypatch)
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))
        out = refresh_current_assess_run_if_active(
            "Testco", designator="agency_dhs")
        assert out.startswith("absent")
        assert "operator" in out  # the message names whose decision it is
        assert calls == []
        assert not root.exists()

    def test_existing_pointer_refreshes_with_passthrough_args(
            self, tmp_path, monkeypatch):
        root = _pointer_root(tmp_path, monkeypatch)
        root.mkdir(parents=True)
        (root / "agency_dhs.current.json").write_text(
            json.dumps({"run_id": "assess:v1:old"}), encoding="utf-8")
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))
        out = refresh_current_assess_run_if_active(
            "Testco", designator="agency_dhs",
            sweep_path=str(tmp_path / "sweep.json"))
        assert "refreshed assess:v1:fresh" in out
        # the CAS repair rides along: the exact observed pointer bytes
        assert calls == [("Testco", {
            "sweep_path": str(tmp_path / "sweep.json"),
            "expected_pointer": {
                "bytes": json.dumps(
                    {"run_id": "assess:v1:old"}).encode("utf-8")},
        })]

    def test_gate_derives_the_designator_when_not_given(
            self, tmp_path, monkeypatch):
        root = _pointer_root(tmp_path, monkeypatch)
        root.mkdir(parents=True)
        (root / "all.current.json").write_text("{}", encoding="utf-8")
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))
        monkeypatch.setattr(
            "agents.review.gate_designator", lambda *a, **k: None)
        out = refresh_current_assess_run_if_active("Testco")
        assert "refreshed" in out
        assert len(calls) == 1

    def test_unreadable_gate_skips_without_touching_state(
            self, tmp_path, monkeypatch):
        root = _pointer_root(tmp_path, monkeypatch)
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))

        def _boom(*a, **k):
            raise RuntimeError("gate packet unreadable")

        monkeypatch.setattr("agents.review.gate_designator", _boom)
        out = refresh_current_assess_run_if_active("Testco")
        assert out.startswith("skipped (gate designator unavailable")
        assert calls == [] and not root.exists()

    def test_refresh_failure_is_loud_and_never_raises(
            self, tmp_path, monkeypatch):
        root = _pointer_root(tmp_path, monkeypatch)
        root.mkdir(parents=True)
        (root / "all.current.json").write_text("{}", encoding="utf-8")

        def _boom(*a, **k):
            raise RuntimeError("adapter exploded")

        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run", _boom)
        out = refresh_current_assess_run_if_active(
            "Testco", designator="all")
        assert out.startswith("FAILED (RuntimeError")
        assert "holds closed" in out

    def test_activate_is_the_only_creation_path(self, tmp_path, monkeypatch):
        _pointer_root(tmp_path, monkeypatch)
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))
        out = activate_current_assess_run("Testco")
        assert "refreshed assess:v1:fresh" in out
        assert len(calls) == 1  # no pointer pre-check: operator said so


class TestServerGateHookIsCreationFree:
    def test_approve_on_pointerless_client_never_materializes(
            self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")  # noqa: F841
        import agents.assess.approval as approval
        import ui.server as srv

        binding = {
            "version": 1, "scope_designator": "all",
            "sweep_artifact": "searches_testco.json",
            "sweep_sha256": "s", "profile_sha256": "p",
        }
        monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
        monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(tmp_path / "runs"))
        monkeypatch.setattr(
            approval, "current_assess_binding", lambda *a, **k: binding)
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))
        client = srv.app.test_client()
        response = client.post("/api/review/assess-approve", json={
            "client_name": "Testco"})
        assert response.status_code == 200
        assert response.get_json()["refresh_note"].startswith("absent")
        assert calls == []
        assert not (tmp_path / "runs").exists()

    def test_active_refresh_failure_is_returned_without_undoing_approval(
            self, tmp_path, monkeypatch, capsys):
        flask = pytest.importorskip("flask")  # noqa: F841
        import agents.assess.approval as approval
        import ui.server as srv

        binding = {
            "version": 1, "scope_designator": "all",
            "sweep_artifact": "searches_testco.json",
            "sweep_sha256": "s", "profile_sha256": "p",
        }
        state_dir = tmp_path / "runs"
        pointer_root = state_dir / assess_client_storage_key("Testco")
        pointer_root.mkdir(parents=True)
        pointer = pointer_root / "all.current.json"
        pointer.write_text('{"run_id":"assess:v1:old"}', encoding="utf-8")
        pointer_before = pointer.read_bytes()
        monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
        monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(state_dir))
        monkeypatch.setattr(
            approval, "current_assess_binding", lambda *a, **k: binding)

        def _boom(*args, **kwargs):
            raise RuntimeError("adapter exploded")

        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run", _boom)
        response = srv.app.test_client().post(
            "/api/review/assess-approve", json={"client_name": "Testco"})

        assert response.status_code == 200
        body = response.get_json()
        assert body["review_approved"] is True
        assert body["refresh_note"].startswith("FAILED (RuntimeError")
        assert "holds closed" in body["refresh_note"]
        assert (tmp_path / "testco.assess_approval.json").exists()
        assert pointer.read_bytes() == pointer_before
        assert "[assess-ledger] Testco: FAILED" in capsys.readouterr().err

    def test_active_refresh_success_is_returned_to_operator(
            self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")  # noqa: F841
        import agents.assess.approval as approval
        import ui.server as srv

        binding = {
            "version": 1, "scope_designator": "all",
            "sweep_artifact": "searches_testco.json",
            "sweep_sha256": "s", "profile_sha256": "p",
        }
        state_dir = tmp_path / "runs"
        pointer_root = state_dir / assess_client_storage_key("Testco")
        pointer_root.mkdir(parents=True)
        (pointer_root / "all.current.json").write_text(
            '{"run_id":"assess:v1:old"}', encoding="utf-8")
        monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
        monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(state_dir))
        monkeypatch.setattr(
            approval, "current_assess_binding", lambda *a, **k: binding)
        calls = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _spy_materialize(calls))

        response = srv.app.test_client().post(
            "/api/review/assess-approve", json={"client_name": "Testco"})

        assert response.status_code == 200
        assert response.get_json()["refresh_note"].startswith(
            "refreshed assess:v1:fresh")
        assert len(calls) == 1

    def test_real_current_pointer_holds_closed_when_refresh_fails(
            self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")  # noqa: F841
        import ui.server as srv
        from agents.assess.live_report import (
            LiveReportState,
            resolve_current_live_report,
        )
        from tests.test_live_report_truth import (
            _cutover_sweep,
            _freeze_strict_clock,
            _persist,
            _seed_runtime,
        )

        # This is the one test in this file that resolves the seed family's
        # live projection, so it needs the family's frozen strict-ledger
        # clock (calendar-rot guard, 2026-08-03; see _freeze_strict_clock).
        _freeze_strict_clock(monkeypatch)
        searches = _cutover_sweep()
        seed = _seed_runtime(tmp_path, monkeypatch, searches)
        _persist(seed)  # explicit operator-cutover fixture; production stays read-only
        before = resolve_current_live_report(
            "Testco", searches, seed["profile"],
            state_dir=seed["state_dir"], review_dir=seed["review_dir"])
        assert before.state == LiveReportState.CURRENT
        pointer = current_assess_pointer_path(
            "Testco", "all", state_dir=seed["state_dir"])
        pointer_before = pointer.read_bytes()
        pointer_mtime_before = pointer.stat().st_mtime_ns
        approval_file = seed["review_dir"] / "testco.assess_approval.json"
        approval_before = approval_file.read_bytes()
        monkeypatch.setattr(srv, "REVIEW_DIR", str(seed["review_dir"]))
        monkeypatch.setattr(srv, "CLEANED_DIR", str(seed["cleaned_dir"]))
        monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(seed["state_dir"]))

        def _boom(*_args, **_kwargs):
            raise RuntimeError("adapter exploded")

        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run", _boom)
        response = srv.app.test_client().post(
            "/api/review/assess-approve",
            json={"client_name": "Testco", "note": "new decision"},
        )

        assert response.status_code == 200
        assert response.get_json()["refresh_note"].startswith(
            "FAILED (RuntimeError")
        assert approval_file.read_bytes() != approval_before
        assert pointer.read_bytes() == pointer_before
        assert pointer.stat().st_mtime_ns == pointer_mtime_before
        after = resolve_current_live_report(
            "Testco", searches, seed["profile"],
            state_dir=seed["state_dir"], review_dir=seed["review_dir"])
        assert after.state == LiveReportState.INVALID
        assert "review or reference inputs have changed" in (
            after.problem or "")


class TestPointerRefreshRollbackRace:
    """Scoreboard item 23 (Codex-reported QB P1, 2026-07-12): a refresh must
    never recreate a pointer the operator removed, or replace one the
    operator swapped, between the existence check and persistence. The
    observed pointer bytes ride to persist_assess_run as a compare-and-swap
    expectation; activation stays the only expectation-free path."""

    def test_refresh_does_not_recreate_concurrently_removed_pointer(
            self, tmp_path, monkeypatch):
        import agents.assess.ledger as ledger
        from tests.test_live_report_truth import (
            _cutover_sweep, _persist, _seed_runtime,
        )

        seed = _seed_runtime(tmp_path, monkeypatch, _cutover_sweep())
        _persist(seed)
        pointer = ledger.current_assess_pointer_path(
            "Testco", "all", state_dir=seed["state_dir"])
        real_materialize = ledger.materialize_current_assess_run

        def remove_after_precheck(*args, **kwargs):
            pointer.unlink()
            return real_materialize(*args, **kwargs)

        monkeypatch.setattr(
            ledger, "materialize_current_assess_run", remove_after_precheck)

        outcome = refresh_current_assess_run_if_active(
            "Testco",
            sweep_path=seed["sweep_path"],
            state_dir=seed["state_dir"],
            review_dir=seed["review_dir"],
        )

        assert outcome.startswith("FAILED")
        assert not pointer.exists()  # the operator's rollback stands

    def test_refresh_does_not_clobber_concurrently_replaced_pointer(
            self, tmp_path, monkeypatch):
        import agents.assess.ledger as ledger
        from tests.test_live_report_truth import (
            _cutover_sweep, _persist, _seed_runtime,
        )

        seed = _seed_runtime(tmp_path, monkeypatch, _cutover_sweep())
        _persist(seed)
        pointer = ledger.current_assess_pointer_path(
            "Testco", "all", state_dir=seed["state_dir"])
        operator_bytes = b'{"run_id": "assess:v1:operator-swap"}'
        real_materialize = ledger.materialize_current_assess_run

        def swap_after_precheck(*args, **kwargs):
            pointer.write_bytes(operator_bytes)
            return real_materialize(*args, **kwargs)

        monkeypatch.setattr(
            ledger, "materialize_current_assess_run", swap_after_precheck)

        outcome = refresh_current_assess_run_if_active(
            "Testco",
            sweep_path=seed["sweep_path"],
            state_dir=seed["state_dir"],
            review_dir=seed["review_dir"],
        )

        assert outcome.startswith("FAILED")
        assert pointer.read_bytes() == operator_bytes  # operator state stands

    def test_unchanged_pointer_still_refreshes_normally(
            self, tmp_path, monkeypatch):
        from tests.test_live_report_truth import (
            _cutover_sweep, _persist, _seed_runtime,
        )

        seed = _seed_runtime(tmp_path, monkeypatch, _cutover_sweep())
        _persist(seed)
        outcome = refresh_current_assess_run_if_active(
            "Testco",
            sweep_path=seed["sweep_path"],
            state_dir=seed["state_dir"],
            review_dir=seed["review_dir"],
        )
        assert outcome.startswith("refreshed assess:v1:")
