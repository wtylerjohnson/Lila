"""Assessment chain: one path, one pause, crash-safe resume."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assessment_chain import (  # noqa: E402
    RUNNER_STATES,
    AssessmentChain,
    AssessmentStateError,
    PauseRequired,
    RunnerOutcome,
    SubprocessRunnerAdapter,
    assessment_status,
    compose_pair_tokens,
    go_path,
    load_state,
    state_path,
    validate_compose_pair,
    write_go,
)
from tools.artifacts import atomic_write_json  # noqa: E402


SIGNAL_QA_LINTS = {
    name: {"ok": True, "violations": []}
    for name in (
        "signal_board", "sam_workspace_links",
        "federal_link_construction", "whitelabel",
        "client_bleed", "emdash",
    )
}


class TickClock:
    def __init__(self, value: datetime | None = None):
        self.value = value or datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            value = self.value
            self.value += timedelta(seconds=1)
            return value


class FakeRunner:
    def __init__(self, failures: dict[str, RunnerOutcome] | None = None,
                 barrier: threading.Barrier | None = None):
        self.failures = failures or {}
        self.barrier = barrier
        self.calls: list[tuple[str, str, str]] = []
        self.lock = threading.Lock()

    def run(self, stage: str, *, slug: str, mode: str) -> RunnerOutcome:
        with self.lock:
            self.calls.append((slug, stage, mode))
        if self.barrier is not None and stage == "SWEEPING":
            self.barrier.wait(timeout=3)
        failure = self.failures.get(stage)
        if failure is not None:
            return failure
        return RunnerOutcome(True, 0, f"{stage.lower()} complete")


def _chain(tmp_path: Path, runner: FakeRunner | None = None,
           clock: TickClock | None = None) -> AssessmentChain:
    return AssessmentChain(
        root=tmp_path,
        state_dir=tmp_path / "state",
        runner=runner or FakeRunner(),
        clock=clock or TickClock(),
        relevance_validator=lambda _slug: True,
    )


def _seed_state(tmp_path: Path, state: str, *, mode: str = "assessment",
                failure: dict | None = None, with_go: bool = False) -> None:
    states = ["INTAKE_DONE"]
    order = [
        "SWEEPING", "RELEVANCE", "AWAITING_TAXONOMY_GO", "COMPOSING",
        "REPULLING", "GATING_PRESS", "DRAFT_READY",
    ]
    if state == "FAILED":
        states.extend(["SWEEPING", "FAILED"])
    elif state != "INTAKE_DONE":
        states.extend(order[:order.index(state) + 1])
    if "COMPOSING" in states:
        waiting_index = states.index("AWAITING_TAXONOMY_GO")
        states.insert(waiting_index + 1, "AWAITING_TAXONOMY_GO")
    base = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    notes = {
        "INTAKE_DONE": "intake artifacts accepted; assessment chain initialized",
        "SWEEPING": "sanctioned sweep runner started",
        "RELEVANCE": "sweeping complete",
        "AWAITING_TAXONOMY_GO": "relevance ready; taxonomy GO required",
        "COMPOSING": "taxonomy GO accepted from reviewer; C1 composer started",
        "REPULLING": "composition complete",
        "GATING_PRESS": "repull complete",
        "DRAFT_READY": "press complete",
        "FAILED": "SWEEPING failed: fixture; fix surface: fixture",
    }
    payload = {
        "version": 1,
        "slug": "riverbed",
        "mode": mode,
        "state": state,
        "failure": failure,
        "transitions": [],
    }
    for index, value in enumerate(states):
        note = notes[value]
        if (value == "AWAITING_TAXONOMY_GO" and index > 0
                and states[index - 1] == "AWAITING_TAXONOMY_GO"):
            note = "taxonomy GO recorded"
        if value == "COMPOSING":
            go_at = payload["transitions"][-1]["at"]
            note = (f"taxonomy GO accepted from reviewer at {go_at}; "
                    "C1 composer started")
        payload["transitions"].append({
            "state": value,
            "at": (base + timedelta(seconds=index)).isoformat(),
            "by": "fixture",
            "note": note,
        })
    target = state_path("riverbed", state_dir=tmp_path / "state")
    atomic_write_json(target, payload)
    if with_go:
        recorded = next((row for row in payload["transitions"]
                         if row["note"] == "taxonomy GO recorded"), None)
        atomic_write_json(go_path("riverbed", state_dir=tmp_path / "state"), {
            "by": "reviewer",
            "at": (recorded["at"] if recorded else
                   (base + timedelta(minutes=1)).isoformat()),
        })


def test_full_mocked_walk_has_exactly_one_pause(tmp_path):
    runner = FakeRunner()
    chain = _chain(tmp_path, runner)

    first = chain.run("Riverbed", by="intake-owner")

    assert first.state == "AWAITING_TAXONOMY_GO"
    assert first.paused is True
    assert runner.calls == [
        ("riverbed", "SWEEPING", "assessment"),
        ("riverbed", "RELEVANCE", "assessment"),
    ]
    before = state_path("riverbed", state_dir=chain.state_dir).read_bytes()
    still_waiting = chain.run("riverbed")
    assert still_waiting.paused is True
    assert state_path("riverbed", state_dir=chain.state_dir).read_bytes() == before
    assert len(runner.calls) == 2

    second = chain.run("riverbed", go=True, by="taxonomy-owner")

    assert second.state == "DRAFT_READY"
    assert second.paused is False
    assert runner.calls == [
        ("riverbed", "SWEEPING", "assessment"),
        ("riverbed", "RELEVANCE", "assessment"),
        ("riverbed", "COMPOSING", "assessment"),
        ("riverbed", "REPULLING", "assessment"),
        ("riverbed", "GATING_PRESS", "assessment"),
    ]
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert [row["state"] for row in state["transitions"]] == [
        "INTAKE_DONE", "SWEEPING", "RELEVANCE", "AWAITING_TAXONOMY_GO",
        "AWAITING_TAXONOMY_GO", "COMPOSING", "REPULLING", "GATING_PRESS",
        "DRAFT_READY",
    ]
    go = json.loads(go_path("riverbed", state_dir=chain.state_dir).read_text())
    recorded_go = state["transitions"][4]
    assert (recorded_go["by"], recorded_go["at"]) == (go["by"], go["at"])
    assert recorded_go["note"] == "taxonomy GO recorded"
    assert go["by"] in state["transitions"][5]["note"]


def test_compose_invalidating_relevance_stops_before_repull(tmp_path):
    runner = FakeRunner()
    checks = iter((True, False))
    chain = AssessmentChain(
        root=tmp_path,
        state_dir=tmp_path / "state",
        runner=runner,
        clock=TickClock(),
        relevance_validator=lambda _slug: next(checks),
    )

    assert chain.run("riverbed").paused is True
    result = chain.run("riverbed", go=True, by="taxonomy-owner")

    assert result.state == "FAILED"
    assert result.failure == {
        "stage": "COMPOSING",
        "reason": (
            "relevance basis or a sanctioned stage artifact changed "
            "before REPULLING"
        ),
        "fix_surface": (
            "--restart-from RELEVANCE and obtain a new taxonomy GO"
        ),
    }
    assert [stage for _, stage, _ in runner.calls] == [
        "SWEEPING", "RELEVANCE", "COMPOSING",
    ]


def test_press_invalidating_relevance_cannot_land_draft_ready(tmp_path):
    basis = {"current": True}

    class MutatingPressRunner(FakeRunner):
        def run(self, stage, *, slug, mode):
            outcome = super().run(stage, slug=slug, mode=mode)
            if stage == "GATING_PRESS":
                basis["current"] = False
            return outcome

    runner = MutatingPressRunner()
    chain = AssessmentChain(
        root=tmp_path,
        state_dir=tmp_path / "state",
        runner=runner,
        clock=TickClock(),
        relevance_validator=lambda _slug: basis["current"],
    )

    assert chain.run("riverbed").paused is True
    result = chain.run("riverbed", go=True, by="taxonomy-owner")

    assert result.state == "FAILED"
    assert result.failure["stage"] == "GATING_PRESS"
    assert result.failure["reason"].endswith("before DRAFT_READY")
    assert [stage for _, stage, _ in runner.calls] == [
        "SWEEPING", "RELEVANCE", "COMPOSING", "REPULLING",
        "GATING_PRESS",
    ]


def test_pause_cannot_be_preapproved_or_restarted_around(tmp_path):
    chain = _chain(tmp_path)
    with pytest.raises(PauseRequired, match="has not reached"):
        chain.run("riverbed", go=True, by="too-early")
    assert not state_path("riverbed", state_dir=chain.state_dir).exists()
    chain.run("riverbed")
    with pytest.raises(PauseRequired, match="taxonomy GO marker"):
        chain.run("riverbed", restart_from="COMPOSING")
    assert load_state("riverbed", state_dir=chain.state_dir)["state"] == \
        "AWAITING_TAXONOMY_GO"


def test_restart_requires_existing_history(tmp_path):
    chain = _chain(tmp_path)
    with pytest.raises(AssessmentStateError, match="requires an existing"):
        chain.run("riverbed", restart_from="RELEVANCE")
    assert not state_path("riverbed", state_dir=chain.state_dir).exists()


def test_predating_go_never_poisons_the_marker_and_legacy_poison_recovers(
        tmp_path):
    chain = _chain(tmp_path)
    assert chain.run("riverbed").paused is True
    marker_path = go_path("riverbed", state_dir=chain.state_dir)

    with pytest.raises(PauseRequired, match="predates"):
        write_go(
            "riverbed", by="too-early", state_dir=chain.state_dir,
            clock=TickClock(datetime(2020, 1, 1, tzinfo=timezone.utc)))
    assert not marker_path.exists()

    atomic_write_json(marker_path, {
        "by": "legacy-poison",
        "at": "2020-01-01T00:00:00+00:00",
    })
    marker = write_go(
        "riverbed", by="current-reviewer", state_dir=chain.state_dir,
        clock=TickClock(datetime(2030, 1, 1, tzinfo=timezone.utc)))

    assert marker["by"] == "current-reviewer"
    assert json.loads(marker_path.read_text(encoding="utf-8")) == marker
    assert load_state("riverbed", state_dir=chain.state_dir)[
        "transitions"][-1]["note"] == "taxonomy GO recorded"


def test_go_validates_the_current_compose_generation(tmp_path, monkeypatch):
    chain = _chain(tmp_path)
    assert chain.run("riverbed").paused is True
    observed = []

    def validate(slug, **kwargs):
        observed.append((slug, kwargs))
        return True

    monkeypatch.setattr(chain, "_relevance_is_current", validate)
    assert chain.run("riverbed", go=True, by="reviewer").state == "DRAFT_READY"

    assert observed[0][0] == "riverbed"
    assert observed[0][1]["stage"] == "COMPOSING"
    assert observed[0][1]["record"]["slug"] == "riverbed"


@pytest.mark.parametrize(("state", "with_go", "expected"), [
    ("INTAKE_DONE", False, ["SWEEPING", "RELEVANCE"]),
    ("SWEEPING", False, ["SWEEPING", "RELEVANCE"]),
    ("RELEVANCE", False, ["RELEVANCE"]),
    ("AWAITING_TAXONOMY_GO", False, []),
    ("AWAITING_TAXONOMY_GO", True,
     ["COMPOSING", "REPULLING", "GATING_PRESS"]),
    ("COMPOSING", True, ["COMPOSING", "REPULLING", "GATING_PRESS"]),
    ("REPULLING", True, ["REPULLING", "GATING_PRESS"]),
    ("GATING_PRESS", True, ["GATING_PRESS"]),
    ("DRAFT_READY", True, []),
])
def test_resume_from_every_nonfailed_state(
        tmp_path, state, with_go, expected):
    _seed_state(tmp_path, state, with_go=with_go)
    runner = FakeRunner()
    result = _chain(tmp_path, runner).run("riverbed")
    assert [stage for _, stage, _ in runner.calls] == expected
    if not with_go and state in (
            "INTAKE_DONE", "SWEEPING", "RELEVANCE",
            "AWAITING_TAXONOMY_GO"):
        assert result.state == "AWAITING_TAXONOMY_GO"
        assert result.paused is True
    else:
        assert result.state == "DRAFT_READY"


@pytest.mark.parametrize("failed_stage", RUNNER_STATES)
def test_failed_state_names_stage_reason_and_fix_and_stops(
        tmp_path, failed_stage):
    failure = RunnerOutcome(
        False, 9, "failed", reason=f"{failed_stage} exploded",
        fix_surface=f"fix {failed_stage}")
    runner = FakeRunner({failed_stage: failure})
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    if failed_stage not in ("SWEEPING", "RELEVANCE"):
        chain.run("riverbed", go=True, by="reviewer")
    result = chain.run("riverbed")
    assert result.state == "FAILED"
    assert result.failure == {
        "stage": failed_stage,
        "reason": f"{failed_stage} exploded",
        "fix_surface": f"fix {failed_stage}",
    }
    calls = len(runner.calls)
    assert chain.run("riverbed").state == "FAILED"
    assert len(runner.calls) == calls


def test_failure_metadata_must_match_preceding_runner_state(tmp_path):
    failure = RunnerOutcome(
        False, 9, "failed", reason="composer exploded",
        fix_surface="fix composer")
    chain = _chain(tmp_path, FakeRunner({"COMPOSING": failure}))
    chain.run("riverbed")
    assert chain.run("riverbed", go=True, by="reviewer").state == "FAILED"
    path = state_path("riverbed", state_dir=chain.state_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["failure"]["stage"] = "SWEEPING"
    atomic_write_json(path, payload)

    with pytest.raises(AssessmentStateError, match="failed runner"):
        load_state("riverbed", state_dir=chain.state_dir)


def test_failed_composer_is_resumable_only_by_explicit_restart(tmp_path):
    failure = RunnerOutcome(
        False, 127, "missing", reason="run_compose.py is absent at runtime",
        fix_surface="run_compose.py --client riverbed (C1 composer CLI)")
    runner = FakeRunner({"COMPOSING": failure})
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    failed = chain.run("riverbed", go=True, by="reviewer")
    assert failed.state == "FAILED"
    runner.failures.clear()

    resumed = chain.run(
        "riverbed", restart_from="COMPOSING", by="operator")

    assert resumed.state == "DRAFT_READY"
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert [row["state"] for row in state["transitions"]][-5:] == [
        "FAILED", "COMPOSING", "REPULLING", "GATING_PRESS", "DRAFT_READY",
    ]


def test_restart_before_pause_invalidates_prior_go(tmp_path):
    runner = FakeRunner()
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    marker = write_go(
        "riverbed", by="reviewer", state_dir=chain.state_dir,
        clock=chain.clock)
    assert marker["by"] == "reviewer"

    restarted = chain.run("riverbed", restart_from="RELEVANCE", by="operator")

    assert restarted.state == "AWAITING_TAXONOMY_GO"
    assert restarted.paused is True
    assert not go_path("riverbed", state_dir=chain.state_dir).exists()


def test_restart_at_pause_is_legal_and_invalidates_prior_go(tmp_path):
    chain = _chain(tmp_path)
    chain.run("riverbed")
    write_go(
        "riverbed", by="reviewer", state_dir=chain.state_dir,
        clock=chain.clock)

    restarted = chain.run(
        "riverbed", restart_from="AWAITING_TAXONOMY_GO", by="operator")

    assert restarted.state == "AWAITING_TAXONOMY_GO"
    assert restarted.paused is True
    assert not go_path("riverbed", state_dir=chain.state_dir).exists()
    assert load_state("riverbed", state_dir=chain.state_dir)[
        "transitions"][-1]["note"] == \
        "operator restart requested from AWAITING_TAXONOMY_GO"


def test_failed_restart_validation_does_not_delete_go_marker(tmp_path):
    chain = _chain(tmp_path)
    chain.run("riverbed")
    marker = write_go(
        "riverbed", by="reviewer", state_dir=chain.state_dir,
        clock=chain.clock)
    marker_path = go_path("riverbed", state_dir=chain.state_dir)
    before_state = state_path(
        "riverbed", state_dir=chain.state_dir).read_bytes()
    chain.clock = TickClock(datetime(2020, 1, 1, tzinfo=timezone.utc))

    with pytest.raises(AssessmentStateError, match="precedes"):
        chain.run("riverbed", restart_from="RELEVANCE", by="operator")

    assert state_path(
        "riverbed", state_dir=chain.state_dir).read_bytes() == before_state
    assert json.loads(marker_path.read_text(encoding="utf-8")) == marker


def test_restart_persisted_before_go_unlink_recovers_stale_marker(tmp_path):
    chain = _chain(tmp_path)
    chain.run("riverbed")
    old_marker = write_go(
        "riverbed", by="old-reviewer", state_dir=chain.state_dir,
        clock=chain.clock)
    path = state_path("riverbed", state_dir=chain.state_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transitions"].append({
        "state": "AWAITING_TAXONOMY_GO",
        "at": "2026-07-18T12:05:00+00:00",
        "by": "operator",
        "note": "operator restart requested from AWAITING_TAXONOMY_GO",
    })
    payload["state"] = "AWAITING_TAXONOMY_GO"
    atomic_write_json(path, payload)

    assert chain.run("riverbed").paused is True
    assert json.loads(go_path(
        "riverbed", state_dir=chain.state_dir).read_text()) == old_marker

    new_marker = write_go(
        "riverbed", by="new-reviewer", state_dir=chain.state_dir,
        clock=TickClock(datetime(2026, 7, 18, 12, 6, tzinfo=timezone.utc)))
    assert new_marker["by"] == "new-reviewer"
    assert new_marker != old_marker
    chain.clock = TickClock(
        datetime(2026, 7, 18, 12, 7, tzinfo=timezone.utc))
    assert chain.run("riverbed").state == "DRAFT_READY"


def test_double_go_is_byte_idempotent(tmp_path):
    chain = _chain(tmp_path)
    chain.run("riverbed")
    first = write_go(
        "riverbed", by="first-reviewer", state_dir=chain.state_dir,
        clock=chain.clock)
    path = go_path("riverbed", state_dir=chain.state_dir)
    before = path.read_bytes()
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert state["transitions"][-1] == {
        "state": "AWAITING_TAXONOMY_GO",
        "at": first["at"],
        "by": "first-reviewer",
        "note": "taxonomy GO recorded",
    }

    second = write_go(
        "riverbed", by="second-reviewer", state_dir=chain.state_dir,
        clock=TickClock(datetime(2030, 1, 1, tzinfo=timezone.utc)))

    assert second == first
    assert path.read_bytes() == before


def test_taxonomy_change_freezes_at_the_pause(tmp_path):
    runner = FakeRunner()
    chain = AssessmentChain(
        root=tmp_path, state_dir=tmp_path / "state", runner=runner,
        clock=TickClock(), relevance_validator=lambda _slug: False)
    chain.run("riverbed")

    with pytest.raises(PauseRequired, match="stale after taxonomy/scope"):
        chain.run("riverbed", go=True, by="reviewer")

    assert [stage for _, stage, _ in runner.calls] == ["SWEEPING", "RELEVANCE"]
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert state["state"] == "AWAITING_TAXONOMY_GO"
    assert state["transitions"][-1]["note"] == "taxonomy GO recorded"


def test_concurrent_clients_are_not_globally_serialized(tmp_path):
    barrier = threading.Barrier(2)
    runner = FakeRunner(barrier=barrier)
    state_dir = tmp_path / "state"
    errors = []

    def work(slug: str) -> None:
        try:
            AssessmentChain(
                root=tmp_path, state_dir=state_dir, runner=runner,
                clock=TickClock()).run(slug)
        except Exception as exc:  # noqa: BLE001 - test captures thread failures
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(slug,))
               for slug in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert load_state("alpha", state_dir=state_dir)["state"] == \
        "AWAITING_TAXONOMY_GO"
    assert load_state("beta", state_dir=state_dir)["state"] == \
        "AWAITING_TAXONOMY_GO"


def test_same_client_runs_serialize_and_do_not_duplicate_stages(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingRunner(FakeRunner):
        def run(self, stage, *, slug, mode):
            if stage == "SWEEPING":
                entered.set()
                assert release.wait(timeout=3)
            return super().run(stage, slug=slug, mode=mode)

    runner = BlockingRunner()
    clock = TickClock()
    chains = [_chain(tmp_path, runner, clock) for _ in range(2)]
    errors = []

    def work(chain):
        try:
            chain.run("riverbed")
        except Exception as exc:  # noqa: BLE001 - thread assertion channel
            errors.append(exc)

    first = threading.Thread(target=work, args=(chains[0],))
    second = threading.Thread(target=work, args=(chains[1],))
    first.start()
    assert entered.wait(timeout=3)
    second.start()
    second.join(timeout=0.1)
    assert second.is_alive()
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)
    assert not errors
    assert [stage for _, stage, _ in runner.calls] == ["SWEEPING", "RELEVANCE"]


def test_concurrent_go_writes_one_marker_and_one_transition(tmp_path):
    chain = _chain(tmp_path)
    chain.run("riverbed")
    clock = TickClock(datetime(2026, 7, 18, 13, tzinfo=timezone.utc))
    results = []

    def approve(by):
        results.append(write_go(
            "riverbed", by=by, state_dir=chain.state_dir, clock=clock))

    threads = [threading.Thread(target=approve, args=(name,))
               for name in ("reviewer-a", "reviewer-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert results[0] == results[1]
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert sum(row["note"] == "taxonomy GO recorded"
               for row in state["transitions"]) == 1


def test_refresh_delegates_once_and_lands_draft_ready(tmp_path):
    runner = FakeRunner()
    chain = _chain(tmp_path, runner)

    result = chain.run("riverbed", refresh=True, by="operator")

    assert result.state == "DRAFT_READY"
    assert runner.calls == [("riverbed", "GATING_PRESS", "refresh")]
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert state["mode"] == "refresh"
    assert [row["state"] for row in state["transitions"]] == [
        "INTAKE_DONE", "GATING_PRESS", "DRAFT_READY",
    ]


def test_interrupted_refresh_resumes_through_c3_not_plain_press(tmp_path):
    state_dir = tmp_path / "state"
    atomic_write_json(state_path("riverbed", state_dir=state_dir), {
        "version": 1,
        "slug": "riverbed",
        "mode": "refresh",
        "state": "GATING_PRESS",
        "failure": None,
        "transitions": [
            {"state": "INTAKE_DONE", "at": "2026-07-18T12:00:00+00:00",
             "by": "operator", "note": "assessment initialized"},
            {"state": "GATING_PRESS", "at": "2026-07-18T12:00:01+00:00",
             "by": "assessment-chain",
             "note": "C3 refresh/press orchestration started"},
        ],
    })
    runner = FakeRunner()
    result = _chain(
        tmp_path, runner,
        TickClock(datetime(2026, 7, 18, 12, 1, tzinfo=timezone.utc)),
    ).run("riverbed")
    assert result.state == "DRAFT_READY"
    assert runner.calls == [("riverbed", "GATING_PRESS", "refresh")]


def test_failed_refresh_restart_runs_c3_again(tmp_path):
    failed = RunnerOutcome(
        False, 9, "failed", reason="refresh interrupted",
        fix_surface="run_refresh_press.py")
    runner = FakeRunner({"GATING_PRESS": failed})
    chain = _chain(tmp_path, runner)

    assert chain.run("riverbed", refresh=True).state == "FAILED"
    runner.failures.clear()
    resumed = chain.run(
        "riverbed", restart_from="GATING_PRESS", by="operator")

    assert resumed.state == "DRAFT_READY"
    assert runner.calls == [
        ("riverbed", "GATING_PRESS", "refresh"),
        ("riverbed", "GATING_PRESS", "refresh"),
    ]
    state = load_state("riverbed", state_dir=chain.state_dir)
    assert state["mode"] == "refresh"
    assert state["transitions"][-2]["note"] == \
        "operator refresh restart requested from GATING_PRESS"


def test_downstream_restart_refuses_stale_relevance(tmp_path):
    failure = RunnerOutcome(
        False, 8, "failed", reason="composer stopped",
        fix_surface="run_compose.py")
    runner = FakeRunner({"COMPOSING": failure})
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    assert chain.run("riverbed", go=True, by="reviewer").state == "FAILED"
    chain.relevance_validator = lambda _slug: False
    before = state_path("riverbed", state_dir=chain.state_dir).read_bytes()

    with pytest.raises(PauseRequired, match="stale after taxonomy/scope"):
        chain.run("riverbed", restart_from="COMPOSING", by="operator")

    assert state_path("riverbed", state_dir=chain.state_dir).read_bytes() == before
    assert [stage for _, stage, _ in runner.calls].count("COMPOSING") == 1


@pytest.mark.parametrize("state", ["COMPOSING", "REPULLING", "GATING_PRESS"])
def test_plain_downstream_crash_resume_refuses_stale_relevance(
        tmp_path, state):
    _seed_state(tmp_path, state, with_go=True)
    runner = FakeRunner()
    chain = AssessmentChain(
        root=tmp_path, state_dir=tmp_path / "state", runner=runner,
        clock=TickClock(), relevance_validator=lambda _slug: False)
    before = state_path("riverbed", state_dir=chain.state_dir).read_bytes()

    with pytest.raises(PauseRequired, match="stale after taxonomy/scope"):
        chain.run("riverbed")

    assert runner.calls == []
    assert state_path("riverbed", state_dir=chain.state_dir).read_bytes() == before


@pytest.mark.parametrize((
    "state", "board_allowed", "expected_board",
    "sweep_allowed", "expected_sweep",
), [
    ("COMPOSING", True, None, False, None),
    ("REPULLING", False, "c" * 64, True, None),
    ("GATING_PRESS", False, "c" * 64, False, "d" * 64),
])
def test_downstream_receipt_allowances_follow_reached_runners(
        tmp_path, monkeypatch, state, board_allowed, expected_board,
        sweep_allowed, expected_sweep):
    import agents.assessment_chain as chain_module

    _seed_state(tmp_path, state, with_go=True)
    record = load_state("riverbed", state_dir=tmp_path / "state")
    if state in {"REPULLING", "GATING_PRESS"}:
        repull = next(
            row for row in record["transitions"]
            if row["state"] == "REPULLING")
        repull["note"] += "; content-sha256 " + "c" * 64
    if state == "GATING_PRESS":
        press = next(
            row for row in record["transitions"]
            if row["state"] == "GATING_PRESS")
        press["note"] += "; sweep-sha256 " + "d" * 64
    observed = []

    def validate(_slug, **kwargs):
        observed.append(kwargs)
        return True

    monkeypatch.setattr(chain_module, "relevance_receipt_valid", validate)
    chain = AssessmentChain(
        root=tmp_path, state_dir=tmp_path / "state", runner=FakeRunner(),
        clock=TickClock())

    assert chain._relevance_is_current("riverbed", record=record) is True
    assert observed[-1]["allow_board_content_change"] is board_allowed
    assert observed[-1]["expected_board_sha256"] == expected_board
    assert observed[-1]["allow_sweep_change"] is sweep_allowed
    assert observed[-1]["expected_sweep_sha256"] == expected_sweep


def test_compose_rewind_binds_latest_sanctioned_repull_hash(
        tmp_path, monkeypatch):
    import agents.assessment_chain as chain_module

    _seed_state(tmp_path, "DRAFT_READY", with_go=True)
    path = state_path("riverbed", state_dir=tmp_path / "state")
    record = json.loads(path.read_text(encoding="utf-8"))
    gate = next(
        row for row in record["transitions"]
        if row["state"] == "GATING_PRESS")
    gate["note"] += "; sweep-sha256 " + "d" * 64
    record["transitions"].append({
        "state": "COMPOSING",
        "at": "2026-07-18T08:00:09+00:00",
        "by": "operator",
        "note": "operator restart requested from COMPOSING",
    })
    record["state"] = "COMPOSING"
    atomic_write_json(path, record)
    record = load_state("riverbed", state_dir=tmp_path / "state")
    observed = []

    def validate(_slug, **kwargs):
        observed.append(kwargs)
        return True

    monkeypatch.setattr(chain_module, "relevance_receipt_valid", validate)
    chain = AssessmentChain(
        root=tmp_path, state_dir=tmp_path / "state", runner=FakeRunner(),
        clock=TickClock())

    assert chain._relevance_is_current("riverbed", record=record) is True
    assert observed[-1]["allow_board_content_change"] is True
    assert observed[-1]["expected_sweep_sha256"] == "d" * 64


def test_compose_restart_after_fresh_relevance_and_go_is_legal(tmp_path):
    failure = RunnerOutcome(
        False, 8, "failed", reason="composer stopped",
        fix_surface="run_compose.py")
    runner = FakeRunner({"COMPOSING": failure})
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    assert chain.run("riverbed", go=True, by="first-reviewer").state == "FAILED"
    runner.failures.clear()

    paused = chain.run(
        "riverbed", restart_from="RELEVANCE", by="operator")
    assert paused.state == "AWAITING_TAXONOMY_GO"
    write_go(
        "riverbed", by="second-reviewer", state_dir=chain.state_dir,
        clock=chain.clock)

    result = chain.run(
        "riverbed", restart_from="COMPOSING", by="operator")

    assert result.state == "DRAFT_READY"
    assert load_state("riverbed", state_dir=chain.state_dir)[
        "transitions"][-4]["note"] == \
        "operator restart requested from COMPOSING"


def test_new_relevance_cycle_cannot_skip_to_old_downstream_stage(tmp_path):
    runner = FakeRunner()
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    assert chain.run("riverbed", go=True, by="first-reviewer").state == \
        "DRAFT_READY"
    assert chain.run(
        "riverbed", restart_from="RELEVANCE", by="operator").paused
    write_go(
        "riverbed", by="second-reviewer", state_dir=chain.state_dir,
        clock=chain.clock)
    before_calls = list(runner.calls)

    with pytest.raises(PauseRequired, match="current execution epoch"):
        chain.run("riverbed", restart_from="GATING_PRESS", by="operator")

    assert runner.calls == before_calls
    assert load_state("riverbed", state_dir=chain.state_dir)["state"] == \
        "AWAITING_TAXONOMY_GO"


def test_restart_epoch_allows_safe_rewinds_but_not_forward_skips(tmp_path):
    runner = FakeRunner()
    chain = _chain(tmp_path, runner)
    chain.run("riverbed")
    assert chain.run("riverbed", go=True, by="reviewer").state == "DRAFT_READY"

    assert chain.run(
        "riverbed", restart_from="SWEEPING", by="operator").paused
    assert chain.run(
        "riverbed", restart_from="INTAKE_DONE", by="operator").paused

    runner.failures["SWEEPING"] = RunnerOutcome(
        False, 7, "failed", reason="sweep failed", fix_surface="sweep")
    assert chain.run(
        "riverbed", restart_from="SWEEPING", by="operator").state == "FAILED"
    with pytest.raises(AssessmentStateError, match="current execution epoch"):
        chain.run("riverbed", restart_from="RELEVANCE", by="operator")


def test_runner_exception_becomes_named_failed_state(tmp_path):
    class RaisingRunner:
        def run(self, stage, *, slug, mode):
            raise OSError("spawn unavailable")

    chain = _chain(tmp_path, RaisingRunner())
    result = chain.run("riverbed")
    assert result.state == "FAILED"
    assert result.failure["stage"] == "SWEEPING"
    assert result.failure["reason"] == "OSError: spawn unavailable"
    assert "run_searches.py" in result.failure["fix_surface"]


def test_missing_parallel_contracts_fail_named(tmp_path):
    adapter = SubprocessRunnerAdapter(root=tmp_path, python=sys.executable)

    composer = adapter.run("COMPOSING", slug="riverbed", mode="assessment")
    refresh = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert composer.success is False
    assert composer.returncode == 127
    assert composer.reason == "run_compose.py is absent at runtime"
    assert "C1 composer CLI" in composer.fix_surface
    assert refresh.success is False
    assert refresh.returncode == 127
    assert refresh.reason == "run_refresh_press.py is absent at runtime"
    assert "C3 press/refresh CLI" in refresh.fix_surface


def test_assessment_children_never_inherit_openai_session_key(
        tmp_path, monkeypatch):
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-session-secret-never-a-child")
    seen = {}

    def fake_run(argv, *, cwd, text, capture_output, check, env):
        seen["argv"] = argv
        seen["env"] = env
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter._invoke(["PYTHON", "child.py"])

    assert "OPENAI_API_KEY" not in seen["env"]
    assert seen["argv"] == ["PYTHON", "child.py"]


def test_composer_cannot_reuse_stale_content(tmp_path, monkeypatch):
    (tmp_path / "run_compose.py").write_text("# stub\n", encoding="utf-8")
    content = tmp_path / "clients" / "riverbed" / "signal_board_content.json"
    content.parent.mkdir(parents=True)
    content.write_text(
        '{"client_name":"Riverbed","composition_mode":"machine"}\n',
        encoding="utf-8")
    adapter = SubprocessRunnerAdapter(
        root=tmp_path, python="PYTHON",
        clock=TickClock(datetime(2026, 7, 18, 11, tzinfo=timezone.utc)))
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")

    def invoke(argv):
        # a fresh trail honestly bound to the STALE content bytes: the
        # content-freshness law is what must refuse the reuse
        trail = (tmp_path / "data" / "state" / "compose" /
                 "riverbed.internal.md")
        trail.parent.mkdir(parents=True, exist_ok=True)
        trail.write_text(
            "# INTERNAL compose trail · Riverbed · never leaves the shop\n"
            "- reused stale content\n"
            "pair content sha256 "
            f"{hashlib.sha256(content.read_bytes()).hexdigest()}\n",
            encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0, f"[out:compose-trail] {trail}\n", "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("COMPOSING", slug="riverbed", mode="assessment")
    assert outcome.success is False
    assert "no fresh board content" in outcome.reason


def test_c1_fails_when_internal_compose_trail_is_missing(
        tmp_path, monkeypatch):
    (tmp_path / "run_compose.py").write_text("# stub\n", encoding="utf-8")
    content = tmp_path / "clients" / "riverbed" / "signal_board_content.json"
    content.parent.mkdir(parents=True)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")

    def invoke(argv):
        content.write_text('{"client_name":"Riverbed"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("COMPOSING", slug="riverbed", mode="assessment")

    assert outcome.success is False
    assert "[out:compose-trail]" in outcome.reason


def test_c1_rejects_substring_client_collision_in_compose_trail(tmp_path):
    trail = (tmp_path / "data" / "state" / "compose" /
             "notriverbed.internal.md")
    trail.parent.mkdir(parents=True)
    content_before, trails_before = compose_pair_tokens(tmp_path, "riverbed")
    trail.write_text("# INTERNAL compose trail\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not bound to client"):
        validate_compose_pair(
            tmp_path, "riverbed", "Riverbed",
            f"[out:compose-trail] {trail}\n",
            content_before=content_before, trails_before=trails_before)


def test_c1_rejects_cross_client_or_placeholder_compose_trail(tmp_path):
    trail = (tmp_path / "data" / "state" / "compose" /
             "riverbed.internal.md")
    trail.parent.mkdir(parents=True)
    content_before, trails_before = compose_pair_tokens(tmp_path, "riverbed")
    trail.write_text(
        "# INTERNAL · Otherco · Compose trail\n"
        "- selected other-client records\n",
        encoding="utf-8")

    with pytest.raises(ValueError, match="exact client header"):
        validate_compose_pair(
            tmp_path, "riverbed", "Riverbed",
            f"[out:compose-trail] {trail}\n",
            content_before=content_before, trails_before=trails_before)


def test_first_focused_sweep_can_create_its_missing_artifact(
        tmp_path, monkeypatch):
    import agents.review as review

    artifact = tmp_path / "data" / "cleaned" / "searches_riverbed.agency_dhs.json"

    def resolve(_client, **_kwargs):
        if not artifact.exists():
            raise FileNotFoundError("focused sweep not written yet")
        return str(artifact)

    def invoke(argv):
        artifact.parent.mkdir(parents=True)
        artifact.write_text(json.dumps({
            "client": "Riverbed",
            "generated_at": "2026-07-18T12:00:00+00:00",
            "results": {},
        }), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, str(artifact), "")

    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(review, "sweep_artifact_path", resolve)
    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("SWEEPING", slug="riverbed", mode="assessment")
    assert outcome.success is True
    assert outcome.artifact == str(artifact)


def test_sweep_adapter_isolated_from_imported_checkout_root(
        tmp_path, monkeypatch):
    import agents.review as review

    own = tmp_path / "own"
    foreign = tmp_path / "foreign"
    for root in (own, foreign):
        (root / "data" / "review").mkdir(parents=True)
        (root / "data" / "cleaned").mkdir(parents=True)
    own_sweep = own / "data" / "cleaned" / "searches_riverbed.json"
    foreign_sweep = (
        foreign / "data" / "cleaned" / "searches_riverbed.json")
    own_sweep.write_text(json.dumps({
        "client": "Riverbed",
        "generated_at": "2026-07-18T11:00:00+00:00",
        "results": {},
    }), encoding="utf-8")
    foreign_sweep.write_text('{"foreign":true}\n', encoding="utf-8")
    foreign_before = foreign_sweep.read_bytes()
    monkeypatch.setattr(
        review, "REVIEW_DIR", str(foreign / "data" / "review"))
    adapter = SubprocessRunnerAdapter(root=own, python="PYTHON")
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")

    def invoke(argv):
        own_sweep.write_text(json.dumps({
            "client": "Riverbed",
            "generated_at": "2026-07-18T12:00:00+00:00",
            "results": {},
        }), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, str(own_sweep), "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("SWEEPING", slug="riverbed", mode="assessment")

    assert outcome.success is True
    assert outcome.artifact == str(own_sweep)
    assert foreign_sweep.read_bytes() == foreign_before


def test_relevance_cli_sees_only_gate_designated_sweep(tmp_path, monkeypatch):
    import agents.review as review

    cleaned = tmp_path / "data" / "cleaned"
    cleaned.mkdir(parents=True)
    selected = cleaned / "searches_riverbed.agency_dhs.json"
    selected.write_text('{"client":"Riverbed","results":{}}\n',
                        encoding="utf-8")
    (cleaned / "searches_riverbed.json").write_text(
        '{"client":"Riverbed","results":{}}\n', encoding="utf-8")
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "clients" / "riverbed").mkdir(parents=True)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        review, "sweep_artifact_path",
        lambda _client, **_kwargs: selected)
    invoked_from = []

    def invoke(argv, *, cwd=None):
        invoked_from.append(cwd)
        visible = sorted(path.name for path in (cwd / "data" / "cleaned").iterdir())
        assert visible == [selected.name]
        out = Path(argv[argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "client,record_ref,engine_score,disagreement,scope_basis\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            argv, 0, "[calibrate] 0 disagreement rows\n",
            "[calibrate] Riverbed: 0 records · scope agency_dhs\n")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("RELEVANCE", slug="riverbed", mode="assessment")

    assert outcome.success is True
    assert len(invoked_from) == 1
    assert not invoked_from[0].exists()


def test_relevance_refuses_inputs_changed_while_runner_executes(
        tmp_path, monkeypatch):
    import agents.review as review

    cleaned = tmp_path / "data" / "cleaned"
    cleaned.mkdir(parents=True)
    sweep = cleaned / "searches_riverbed.json"
    sweep.write_text('{"client":"Riverbed","results":{}}\n', encoding="utf-8")
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    client_dir = tmp_path / "clients" / "riverbed"
    client_dir.mkdir(parents=True)
    profile = client_dir / "profile.json"
    profile.write_text('{"client_name":"Riverbed"}\n', encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        review, "sweep_artifact_path", lambda _client, **_kwargs: sweep)

    def invoke(argv, *, cwd=None):
        assert cwd is not None
        out = Path(argv[argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "client,record_ref,engine_score,disagreement,scope_basis\n",
            encoding="utf-8",
        )
        profile.write_text('{"client_name":"Changed"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0, "",
            "[calibrate] Riverbed: 0 records · scope UNSCOPED\n")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("RELEVANCE", slug="riverbed", mode="assessment")

    assert outcome.success is False
    assert outcome.reason == "relevance inputs changed during relevance scoring"


def test_fresh_do_not_send_is_a_completed_draft(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")

    def invoke(argv):
        gated = reports / "riverbed.federal_opportunity_signals.DO-NOT-SEND.html"
        gated.write_text("gated draft", encoding="utf-8")
        (reports / "riverbed.federal_opportunity_signals.internal.md").write_text(
            "# INTERNAL · Riverbed · Signal Board press trail\n\n"
            "## Gate verdict\n"
            "- DO-NOT-SEND (violations below)\n",
            encoding="utf-8")
        stdout = f"sha256 {hashlib.sha256(gated.read_bytes()).hexdigest()}\n"
        return subprocess.CompletedProcess(argv, 2, stdout, "gate fired")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="assessment")
    assert outcome.success is True
    assert outcome.returncode == 2
    assert "DO-NOT-SEND" in outcome.note


def test_press_rejects_cross_client_internal_sidecar(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")

    def invoke(argv):
        clean = reports / "riverbed.federal_opportunity_signals.html"
        clean.write_text("draft", encoding="utf-8")
        (reports / "riverbed.federal_opportunity_signals.internal.md").write_text(
            "# INTERNAL · Otherco · Signal Board press trail\n\n"
            "## Gate verdict\n"
            "- CLEAN (client-final)\n",
            encoding="utf-8")
        stdout = f"sha256 {hashlib.sha256(clean.read_bytes()).hexdigest()}\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="assessment")

    assert outcome.success is False
    assert "sidecar identity" in outcome.reason


def test_c3_missing_required_outputs_fails_named(tmp_path, monkeypatch):
    (tmp_path / "run_refresh_press.py").write_text("# stub\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(
        root=tmp_path, python="PYTHON",
        clock=TickClock(datetime(2026, 7, 18, 11, tzinfo=timezone.utc)))
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_invoke",
        lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")
    assert outcome.success is False
    assert outcome.reason == (
        "C3 refresh omitted output(s): snapshot, delta, delta-summary")


@pytest.mark.parametrize(("marker_case", "reason"), [
    ("omitted", "exactly one [out:signal-board-qa] marker"),
    ("duplicate", "exactly one [out:signal-board-qa] marker"),
    ("stale", "QA output is absent or stale"),
    ("escaped", "QA output escaped"),
])
def test_c3_signal_board_qa_marker_refusals_are_closed(
        tmp_path, monkeypatch, marker_case, reason):
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    expected_qa = reports / "riverbed.federal_opportunity_signals.qa.json"
    if marker_case == "stale":
        expected_qa.write_text("{}\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_validate_refresh_artifacts",
        lambda _outputs, *, slug, client, not_before=None: None)
    monkeypatch.setattr(
        "agents.assessment_chain.validate_compose_pair",
        lambda *args, **kwargs: tmp_path / "compose.internal.md")

    def invoke(argv):
        calibration = (tmp_path / "data" / "state" / "relevance" /
                       "riverbed.calibration.csv")
        calibration.parent.mkdir(parents=True)
        calibration.write_text(
            "client,record_ref,engine_score,disagreement,scope_basis\n",
            encoding="utf-8")
        delta_root = (tmp_path / "data" / "state" / "deltas" /
                      "riverbed")
        delta_root.mkdir(parents=True)
        outputs = {
            "snapshot": delta_root / "snapshot.json",
            "delta": delta_root / "delta.json",
            "delta-summary": delta_root / "delta.internal.md",
        }
        for path in outputs.values():
            path.write_text("{}\n", encoding="utf-8")
        if marker_case in {"omitted", "stale"}:
            marker = "" if marker_case == "omitted" else (
                f"[out:signal-board-qa] {expected_qa}\n")
        elif marker_case == "duplicate":
            expected_qa.write_text("{}\n", encoding="utf-8")
            marker = (
                f"[out:signal-board-qa] {expected_qa}\n"
                f"[out:signal-board-qa] {expected_qa}\n"
            )
        else:
            escaped = tmp_path / "escaped.qa.json"
            escaped.write_text("{}\n", encoding="utf-8")
            marker = f"[out:signal-board-qa] {escaped}\n"
        stdout = marker + "".join(
            f"[out:{name}] {path}\n" for name, path in outputs.items())
        return subprocess.CompletedProcess(
            argv, 0, stdout,
            "[calibrate] Riverbed: 0 records · scope UNSCOPED · "
            "0 FP · 0 FN · 0 out-of-scope\n")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert reason in outcome.reason


def test_c3_signal_board_qa_timestamp_must_match_refresh_snapshot(
        tmp_path, monkeypatch):
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    qa = reports / "riverbed.federal_opportunity_signals.qa.json"
    snapshot_timestamp = datetime(
        2026, 7, 18, 11, tzinfo=timezone.utc)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_presentation_name",
        lambda _slug, _client: "Riverbed")
    monkeypatch.setattr(
        adapter, "_validate_refresh_artifacts",
        lambda _outputs, *, slug, client, not_before=None:
            snapshot_timestamp)
    monkeypatch.setattr(
        "agents.assessment_chain.validate_compose_pair",
        lambda *args, **kwargs: tmp_path / "compose.internal.md")
    monkeypatch.setattr(
        "agents.reports.release.signal_board_status",
        lambda *args, **kwargs: {
            "certificate_valid": True,
            "qa": {"press_timestamp": "2026-07-18T10:59:59Z"},
        })

    def invoke(argv):
        calibration = (tmp_path / "data" / "state" / "relevance" /
                       "riverbed.calibration.csv")
        calibration.parent.mkdir(parents=True)
        calibration.write_text(
            "client,record_ref,engine_score,disagreement,scope_basis\n",
            encoding="utf-8")
        qa.write_text("{}\n", encoding="utf-8")
        delta_root = (tmp_path / "data" / "state" / "deltas" /
                      "riverbed")
        delta_root.mkdir(parents=True)
        outputs = {
            "snapshot": delta_root / "snapshot.json",
            "delta": delta_root / "delta.json",
            "delta-summary": delta_root / "delta.internal.md",
        }
        for path in outputs.values():
            path.write_text("{}\n", encoding="utf-8")
        stdout = (
            f"[out:compose-trail] {tmp_path / 'compose.internal.md'}\n"
            f"[out:signal-board-qa] {qa}\n"
            + "".join(
                f"[out:{name}] {path}\n"
                for name, path in outputs.items())
        )
        return subprocess.CompletedProcess(
            argv, 0, stdout,
            "[calibrate] Riverbed: 0 records · scope UNSCOPED · "
            "0 FP · 0 FN · 0 out-of-scope\n")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert outcome.reason == (
        "C3 Signal Board QA output is invalid: Signal Board QA timestamp "
        "does not match the accepted refresh snapshot")


def test_c3_preserves_named_missing_c1_reason_and_fix_surface(
        tmp_path, monkeypatch):
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    failure = {
        "stage": "compose-refresh",
        "reason": "run_compose.py is absent at runtime",
        "fix_surface": "run_compose.py --client riverbed (C1 composer CLI)",
    }
    monkeypatch.setattr(
        adapter,
        "_invoke",
        lambda argv: subprocess.CompletedProcess(
            argv,
            127,
            "[refresh] step 3/7: compose-refresh\n",
            "[refresh:failure] " + json.dumps(failure, sort_keys=True) + "\n",
        ),
    )

    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert outcome.returncode == 127
    assert outcome.reason == "run_compose.py is absent at runtime"
    assert outcome.fix_surface == \
        "run_compose.py --client riverbed (C1 composer CLI)"


def test_c3_preserves_relayed_composer_rc2_reason_and_fix_surface(
        tmp_path, monkeypatch):
    """A single well-formed compose-refresh failure at a nonzero exit
    rides through with C1's reason, fix surface, and return code."""
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    failure = {
        "stage": "compose-refresh",
        "reason": "no core-evidenced best-fit play under the "
                  "machine-screened bar",
        "fix_surface": "the keyword/NAICS review checkpoint",
    }
    monkeypatch.setattr(
        adapter,
        "_invoke",
        lambda argv: subprocess.CompletedProcess(
            argv, 2, "[refresh] step 3/7: compose-refresh\n",
            "[refresh:failure] " + json.dumps(failure, sort_keys=True) + "\n",
        ),
    )

    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert outcome.returncode == 2
    assert outcome.note == "compose-refresh failed named"
    assert outcome.reason == failure["reason"]
    assert outcome.fix_surface == failure["fix_surface"]


def test_c3_preserves_every_owned_stage_named_failure(tmp_path, monkeypatch):
    """The complete C3 failure vocabulary: a single well-formed marker
    for any exact stage the refresh runner owns, at a nonzero exit,
    preserves that step's reason, fix surface, and return code."""
    from agents.assessment_chain import C3_FAILURE_STAGES

    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    assert C3_FAILURE_STAGES == (
        "re-sweep", "relevance", "compose-refresh", "award re-pull",
        "candidate-review-watch", "signal-board-certification",
    )
    for stage, code in (("re-sweep", 7), ("relevance", 2),
                        ("candidate-review-watch", 2),
                        ("compose-refresh", 2), ("award re-pull", 3),
                        ("signal-board-certification", 2),
                        ("relevance", 127)):
        failure = {"stage": stage, "reason": f"{stage} named reason",
                   "fix_surface": f"{stage} fix surface"}
        marker = "[refresh:failure] " + json.dumps(
            failure, sort_keys=True) + "\n"
        monkeypatch.setattr(
            adapter, "_invoke",
            lambda argv, _stderr=marker, _code=code:
                subprocess.CompletedProcess(argv, _code, "", _stderr))
        outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")
        assert outcome.success is False, (stage, code)
        assert outcome.returncode == code
        assert outcome.note == f"{stage} failed named"
        assert outcome.reason == failure["reason"]
        assert outcome.fix_surface == failure["fix_surface"]


def test_c3_marker_refusals_remain_closed(tmp_path, monkeypatch):
    """Unknown stages, malformed and duplicate markers, markers beside
    exit 0, and the wrong rc127 missing-composer payload all collapse to
    the invalid named failure."""
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    good = json.dumps({
        "stage": "compose-refresh", "reason": "r", "fix_surface": "f",
    }, sort_keys=True)
    unknown = json.dumps({
        "stage": "press", "reason": "r", "fix_surface": "f",
    }, sort_keys=True)
    incomplete = json.dumps({
        "stage": "relevance", "reason": "", "fix_surface": "f",
    }, sort_keys=True)
    cases = [
        ("[refresh:failure] not json\n", 2),                # malformed
        (f"[refresh:failure] {incomplete}\n", 2),           # empty reason
        (f"[refresh:failure] {good}\n[refresh:failure] {good}\n", 2),
        (f"[refresh:failure] {unknown}\n", 2),              # unknown stage
        (f"[refresh:failure] {good}\n", 0),                 # success + marker
        (f"[refresh:failure] {good}\n", 127),               # wrong 127 payload
    ]
    for stderr, code in cases:
        monkeypatch.setattr(
            adapter, "_invoke",
            lambda argv, _stderr=stderr, _code=code:
                subprocess.CompletedProcess(argv, _code, "", _stderr))
        outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")
        assert outcome.success is False, (stderr, code)
        assert outcome.reason == "C3 refresh emitted an invalid named failure"


def test_c3_rejects_outputs_nested_below_client_delta_root(
        tmp_path, monkeypatch):
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")

    def invoke(argv):
        nested = (tmp_path / "data" / "state" / "deltas" /
                  "riverbed" / "nested")
        nested.mkdir(parents=True)
        outputs = {
            "snapshot": nested / "2026-07-18T120000Z.json",
            "delta": nested / "2026-07-18T120000Z.delta.json",
            "delta-summary": nested / "2026-07-18T120000Z.internal.md",
        }
        for path in outputs.values():
            path.write_text("{}\n", encoding="utf-8")
        stdout = "".join(
            f"[out:{name}] {path}\n" for name, path in outputs.items())
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert "not directly under" in outcome.reason


def test_c3_touching_stale_content_without_trail_cannot_prove_compose_refresh(
        tmp_path, monkeypatch):
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    client_dir = tmp_path / "clients" / "riverbed"
    client_dir.mkdir(parents=True)
    content = client_dir / "signal_board_content.json"
    content.write_text('{"client_name":"Riverbed"}\n', encoding="utf-8")
    adapter = SubprocessRunnerAdapter(
        root=tmp_path, python="PYTHON",
        clock=TickClock(datetime(2026, 7, 18, 11, tzinfo=timezone.utc)))
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_validate_refresh_artifacts",
        lambda _outputs, *, slug, client, not_before=None: None)

    def invoke(argv):
        os.utime(content, None)
        delta_dir = (tmp_path / "data" / "state" / "deltas" /
                     "riverbed")
        delta_dir.mkdir(parents=True)
        outputs = {
            "snapshot": delta_dir / "snapshot.json",
            "delta": delta_dir / "delta.json",
            "delta-summary": delta_dir / "delta.internal.md",
        }
        for path in outputs.values():
            path.write_text("{}\n", encoding="utf-8")
        stdout = "".join(
            f"[out:{name}] {path}\n" for name, path in outputs.items())
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert "required compose refresh" in outcome.reason
    assert "[out:compose-trail]" in outcome.reason


def test_c3_complete_compose_without_fresh_relevance_is_still_refused(
        tmp_path, monkeypatch):
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    client_dir = tmp_path / "clients" / "riverbed"
    client_dir.mkdir(parents=True)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_validate_refresh_artifacts",
        lambda _outputs, *, slug, client, not_before=None: None)

    def invoke(argv):
        content = client_dir / "signal_board_content.json"
        content.write_text(
            json.dumps({
                "client_name": "Riverbed",
                "composition_mode": "machine",
                "scale_label": "Client-footprint obligated history",
                "scale": {
                    "basis": "client_obligated_to_date",
                    "rows": [],
                    "total": "",
                },
            }) + "\n",
            encoding="utf-8")
        trail = (tmp_path / "data" / "state" / "compose" /
                 "riverbed.internal.md")
        trail.parent.mkdir(parents=True)
        trail.write_text(
            "# INTERNAL compose trail · Riverbed · never leaves the shop\n"
            "- refreshed fixture records deterministically\n"
            "pair content sha256 "
            f"{hashlib.sha256(content.read_bytes()).hexdigest()}\n",
            encoding="utf-8",
        )
        delta_dir = (tmp_path / "data" / "state" / "deltas" /
                     "riverbed")
        delta_dir.mkdir(parents=True)
        outputs = {
            "snapshot": delta_dir / "snapshot.json",
            "delta": delta_dir / "delta.json",
            "delta-summary": delta_dir / "delta.internal.md",
        }
        for path in outputs.values():
            path.write_text("{}\n", encoding="utf-8")
        stdout = f"[out:compose-trail] {trail}\n" + "".join(
            f"[out:{name}] {path}\n" for name, path in outputs.items())
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")

    assert outcome.success is False
    assert outcome.reason == \
        "C3 skipped or failed the required relevance refresh"


def test_c3_schema_validation_rejects_cross_client_snapshot(
        tmp_path, monkeypatch):
    from types import ModuleType, SimpleNamespace

    root = tmp_path / "data" / "state" / "deltas" / "riverbed"
    root.mkdir(parents=True)
    outputs = {
        "snapshot": root / "2026-07-18T120000Z.json",
        "delta": root / "2026-07-18T120000Z.delta.json",
        "delta-summary": root / "2026-07-18T120000Z.internal.md",
    }
    for path in outputs.values():
        path.write_text("{}\n", encoding="utf-8")

    snapshot_module = ModuleType("agents.press_snapshot")

    class FakeSnapshot:
        @classmethod
        def model_validate_json(cls, _text):
            return SimpleNamespace(
                client_name="Another Client", slug="another_client",
                press_timestamp=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )

    snapshot_module.PressSnapshot = FakeSnapshot
    snapshot_module.client_slug = lambda value: "another_client"
    snapshot_module.snapshot_filename = lambda _moment: \
        "2026-07-18T120000Z.json"
    delta_module = ModuleType("agents.press_delta")
    delta_module.DELTA_CLASS_ORDER = ("NEW",)
    delta_module.DELTA_SCHEMA_VERSION = 1
    delta_module.delta_feed = lambda _payload: {"items": []}
    delta_module.render_internal_markdown = lambda _payload: "# INTERNAL\n"
    monkeypatch.setitem(sys.modules, "agents.press_snapshot", snapshot_module)
    monkeypatch.setitem(sys.modules, "agents.press_delta", delta_module)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")

    with pytest.raises(ValueError, match="snapshot identity"):
        adapter._validate_refresh_artifacts(
            outputs, slug="riverbed", client="Riverbed")


def test_c3_snapshot_accepts_runner_second_precision_at_subsecond_start(
        tmp_path):
    from agents.press_delta import (
        baseline_delta,
        canonical_json,
        render_internal_markdown,
    )
    from agents.press_snapshot import PressSnapshot, snapshot_filename

    press_timestamp = datetime(
        2026, 7, 19, 19, 5, 9, tzinfo=timezone.utc)
    snapshot = PressSnapshot.model_validate({
        "client_name": "Mark43",
        "slug": "mark43",
        "press_timestamp": press_timestamp,
        "source_artifacts": {
            "sweep": {"path": "data/cleaned/searches_mark43.json",
                      "sha256": "0" * 64},
            "figure_source": "none",
        },
        "candidates": [],
        "candidate_screen": [],
        "figures": [],
        "windows": [],
        "recompete_events": [],
        "gate_state": {"status": "CLEAN", "render_date": "2026-07-19"},
        "verification": {"status": "COMPLETE", "reason": None},
    })
    stamp = snapshot_filename(press_timestamp)[:-len(".json")]
    root = tmp_path / "data" / "state" / "deltas" / "mark43"
    root.mkdir(parents=True)
    outputs = {
        "snapshot": root / f"{stamp}.json",
        "delta": root / f"{stamp}.delta.json",
        "delta-summary": root / f"{stamp}.internal.md",
    }
    outputs["snapshot"].write_text(
        snapshot.model_dump_json() + "\n", encoding="utf-8")
    delta = baseline_delta(snapshot)
    outputs["delta"].write_text(canonical_json(delta), encoding="utf-8")
    outputs["delta-summary"].write_text(
        render_internal_markdown(delta), encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")

    adapter._validate_refresh_artifacts(
        outputs,
        slug="mark43",
        client="Mark43",
        not_before=press_timestamp.replace(microsecond=875_000),
    )

    with pytest.raises(ValueError, match="snapshot predates"):
        adapter._validate_refresh_artifacts(
            outputs,
            slug="mark43",
            client="Mark43",
            not_before=press_timestamp + timedelta(seconds=1, microseconds=1),
        )


def test_c3_schema_validation_rejects_placeholder_delta(
        tmp_path, monkeypatch):
    from types import ModuleType, SimpleNamespace

    root = tmp_path / "data" / "state" / "deltas" / "riverbed"
    root.mkdir(parents=True)
    outputs = {
        "snapshot": root / "2026-07-18T120000Z.json",
        "delta": root / "2026-07-18T120000Z.delta.json",
        "delta-summary": root / "2026-07-18T120000Z.internal.md",
    }
    for path in outputs.values():
        path.write_text("{}\n", encoding="utf-8")

    snapshot_module = ModuleType("agents.press_snapshot")

    class FakeSnapshot:
        @classmethod
        def model_validate_json(cls, _text):
            return SimpleNamespace(
                client_name="Riverbed", slug="riverbed",
                press_timestamp=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )

    snapshot_module.PressSnapshot = FakeSnapshot
    snapshot_module.client_slug = lambda value: "riverbed"
    snapshot_module.snapshot_filename = lambda _moment: \
        "2026-07-18T120000Z.json"
    delta_module = ModuleType("agents.press_delta")
    delta_module.DELTA_CLASS_ORDER = ("NEW",)
    delta_module.DELTA_SCHEMA_VERSION = 1
    delta_module.delta_feed = lambda _payload: {"items": []}
    delta_module.render_internal_markdown = lambda _payload: "# INTERNAL\n"
    monkeypatch.setitem(sys.modules, "agents.press_snapshot", snapshot_module)
    monkeypatch.setitem(sys.modules, "agents.press_delta", delta_module)
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")

    with pytest.raises(ValueError, match="complete C3 schema"):
        adapter._validate_refresh_artifacts(
            outputs, slug="riverbed", client="Riverbed")


@pytest.mark.parametrize("press_code", [0, 2])
def test_c1_command_and_complete_c3_sequence_are_accepted(
        tmp_path, monkeypatch, press_code):
    (tmp_path / "run_compose.py").write_text("# contract stub\n", encoding="utf-8")
    (tmp_path / "run_refresh_press.py").write_text(
        "# contract stub\n", encoding="utf-8")
    (tmp_path / "clients" / "riverbed").mkdir(parents=True)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    adapter = SubprocessRunnerAdapter(
        root=tmp_path, python="PYTHON",
        clock=TickClock(datetime(2026, 7, 18, 11, tzinfo=timezone.utc)))
    monkeypatch.setattr(adapter, "_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_require_client_name", lambda _slug: "Riverbed")
    monkeypatch.setattr(
        adapter, "_presentation_name",
        lambda _slug, _client: "Riverbed")
    monkeypatch.setattr(
        adapter, "_validate_refresh_artifacts",
        lambda _outputs, *, slug, client, not_before=None:
            datetime(2026, 7, 18, 11, tzinfo=timezone.utc),
    )
    calls = []

    def invoke(argv):
        calls.append(argv)
        if argv[1].endswith("run_compose.py"):
            content = (tmp_path / "clients" / "riverbed" /
                       "signal_board_content.json")
            content.write_text(
                json.dumps({
                    "client_name": "Riverbed",
                    "composition_mode": "machine",
                    "scale_label": "Client-footprint obligated history",
                    "scale": {
                        "basis": "client_obligated_to_date",
                        "rows": [],
                        "total": "",
                    },
                }) + "\n", encoding="utf-8")
            trail = (tmp_path / "data" / "state" / "compose" /
                     "riverbed.internal.md")
            trail.parent.mkdir(parents=True)
            trail.write_text(
                "# INTERNAL compose trail · Riverbed · never leaves the shop\n"
                "- selected fixture records deterministically\n"
                "pair content sha256 "
                f"{hashlib.sha256(content.read_bytes()).hexdigest()}\n",
                encoding="utf-8")
            stdout = f"[out:compose-trail] {trail}\n"
        else:
            from agents.reports.signal_board_presentation import (
                presentation_digest,
            )

            presentation_sha256 = presentation_digest(
                "Riverbed", root=tmp_path)
            content = (tmp_path / "clients" / "riverbed" /
                       "signal_board_content.json")
            content.write_text(
                json.dumps({
                    "client_name": "Riverbed",
                    "composition_mode": "machine",
                    "hero_context": "refresh",
                    "scale_label": "Client-footprint obligated history",
                    "scale": {
                        "basis": "client_obligated_to_date",
                        "rows": [],
                        "total": "",
                    },
                }) + "\n",
                encoding="utf-8")
            calibration = (tmp_path / "data" / "state" / "relevance" /
                           "riverbed.calibration.csv")
            calibration.parent.mkdir(parents=True)
            calibration.write_text(
                "client,record_ref,engine_score,disagreement,scope_basis\n",
                encoding="utf-8",
            )
            trail = (tmp_path / "data" / "state" / "compose" /
                     "riverbed.internal.md")
            trail.write_text(
                "# INTERNAL compose trail · Riverbed · never leaves the shop\n"
                "- refreshed fixture records deterministically\n"
                "pair content sha256 "
                f"{hashlib.sha256(content.read_bytes()).hexdigest()}\n",
                encoding="utf-8",
            )
            blocked = press_code == 2
            html = (tmp_path / "data" / "reports" /
                    ("riverbed.federal_opportunity_signals"
                     + (".DO-NOT-SEND" if blocked else "") + ".html"))
            html.write_text(
                "<html><title>Riverbed · Federal Opportunity Pre-Assessment"
                "</title></html>", encoding="utf-8")
            (tmp_path / "data" / "reports" /
             "riverbed.federal_opportunity_signals.internal.md").write_text(
                 "# INTERNAL · Riverbed · Signal Board press trail\n\n"
                 "## Presentation snapshot\n"
                 f"- SHA256 {presentation_sha256}\n\n"
                 "## Gate verdict\n"
                 + ("- DO-NOT-SEND (violations below)\n" if blocked
                    else "- CLEAN (client-final)\n"),
                 encoding="utf-8")
            qa = (tmp_path / "data" / "reports" /
                  "riverbed.federal_opportunity_signals.qa.json")
            qa.write_text(json.dumps({
                "schema_version": 2,
                "lint_contract_version": 1,
                "client_name": "Riverbed",
                "presentation_name": "Riverbed",
                "slug": "riverbed",
                "state": "do_not_send" if blocked else "release",
                "release_eligible": not blocked,
                "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
                "presentation_sha256": presentation_sha256,
                "press_timestamp": "2026-07-18T11:00:00Z",
                "gate_verdict": (
                    "- DO-NOT-SEND (violations below)" if blocked
                    else "- CLEAN (client-final)"),
                "static_lints": SIGNAL_QA_LINTS,
                "federal_link_manifest": [],
            }) + "\n", encoding="utf-8")
            delta_dir = (tmp_path / "data" / "state" / "deltas" /
                         "riverbed")
            delta_dir.mkdir(parents=True)
            snapshot = delta_dir / "snapshot.json"
            delta = delta_dir / "delta.json"
            summary = delta_dir / "delta.internal.md"
            snapshot.write_text("{}\n", encoding="utf-8")
            delta.write_text("{}\n", encoding="utf-8")
            summary.write_text("# INTERNAL refresh delta\n", encoding="utf-8")
            stdout = (
                f"sha256 {hashlib.sha256(html.read_bytes()).hexdigest()}\n"
                f"[out:compose-trail] {trail}\n"
                f"[out:signal-board-qa] {qa}\n"
                f"[out:snapshot] {snapshot}\n"
                f"[out:delta] {delta}\n"
                f"[out:delta-summary] {summary}\n"
            )
        stderr = (
            "[calibrate] Riverbed: 0 records · scope UNSCOPED · "
            "0 FP · 0 FN · 0 out-of-scope\n"
            if argv[1].endswith("run_refresh_press.py") else ""
        )
        code = press_code if argv[1].endswith("run_refresh_press.py") else 0
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr(adapter, "_invoke", invoke)
    assert adapter.run("COMPOSING", slug="riverbed", mode="assessment").success
    refreshed = adapter.run("GATING_PRESS", slug="riverbed", mode="refresh")
    assert refreshed.success is True
    assert refreshed.returncode == press_code
    if press_code == 2:
        assert "DO-NOT-SEND" in refreshed.note
    assert "delta" in refreshed.note
    assert len(calls) == 2
    assert calls[0] == ["PYTHON", str(tmp_path / "run_compose.py"),
                        "--client", "riverbed"]
    # 2026-08-04: C3 hands its own clock to the child refresh so a replayed
    # world keeps every stage on one clock. The moment is the adapter
    # clock's tick; the contract pins the flag and an aware ISO value.
    assert calls[1][:4] == ["PYTHON", str(tmp_path / "run_refresh_press.py"),
                            "--client", "Riverbed"]
    assert calls[1][4] == "--now"
    assert datetime.fromisoformat(calls[1][5]).tzinfo is not None


def test_corrupt_state_fails_loudly_and_status_is_read_only(tmp_path):
    path = state_path("riverbed", state_dir=tmp_path / "state")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(AssessmentStateError, match="cannot read"):
        load_state("riverbed", state_dir=tmp_path / "state")
    assert path.read_bytes() == before


def test_first_release_v1_post_go_history_remains_readable(tmp_path):
    _seed_state(tmp_path, "DRAFT_READY", with_go=True)
    path = state_path("riverbed", state_dir=tmp_path / "state")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transitions"] = [
        row for row in payload["transitions"]
        if row["note"] != "taxonomy GO recorded"
    ]
    composing = next(
        row for row in payload["transitions"] if row["state"] == "COMPOSING")
    composing["note"] = "taxonomy GO accepted; C1 composer started"
    atomic_write_json(path, payload)

    loaded = load_state("riverbed", state_dir=tmp_path / "state")

    assert loaded["version"] == 1
    assert loaded["state"] == "DRAFT_READY"
    runner = FakeRunner()
    assert _chain(tmp_path, runner).run(
        "riverbed", restart_from="GATING_PRESS", by="operator").state == \
        "DRAFT_READY"
    assert [stage for _, stage, _ in runner.calls] == ["GATING_PRESS"]


def test_first_release_v1_pause_to_compose_restart_remains_readable(tmp_path):
    _seed_state(tmp_path, "AWAITING_TAXONOMY_GO")
    path = state_path("riverbed", state_dir=tmp_path / "state")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transitions"].append({
        "state": "COMPOSING",
        "at": "2026-07-18T08:00:04+00:00",
        "by": "legacy-operator",
        "note": "operator restart requested from COMPOSING",
    })
    payload["state"] = "COMPOSING"
    atomic_write_json(path, payload)

    loaded = load_state("riverbed", state_dir=tmp_path / "state")

    assert loaded["version"] == 1
    assert loaded["state"] == "COMPOSING"


def test_first_release_v1_second_cycle_pause_restart_remains_readable(
        tmp_path):
    _seed_state(tmp_path, "DRAFT_READY", with_go=True)
    path = state_path("riverbed", state_dir=tmp_path / "state")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transitions"].extend([
        {
            "state": "RELEVANCE",
            "at": "2026-07-18T08:00:09+00:00",
            "by": "legacy-operator",
            "note": "operator restart requested from RELEVANCE",
        },
        {
            "state": "AWAITING_TAXONOMY_GO",
            "at": "2026-07-18T08:00:10+00:00",
            "by": "assessment-chain",
            "note": "relevance ready; taxonomy GO required",
        },
        {
            "state": "COMPOSING",
            "at": "2026-07-18T08:00:11+00:00",
            "by": "legacy-operator",
            "note": "operator restart requested from COMPOSING",
        },
    ])
    payload["state"] = "COMPOSING"
    atomic_write_json(path, payload)

    assert load_state("riverbed", state_dir=tmp_path / "state")["state"] == \
        "COMPOSING"


def test_malformed_reserved_restart_note_fails_closed(tmp_path):
    _seed_state(tmp_path, "SWEEPING")
    path = state_path("riverbed", state_dir=tmp_path / "state")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["transitions"].append({
        "state": "RELEVANCE",
        "at": "2026-07-18T08:00:02+00:00",
        "by": "fixture",
        "note": "operator restart requested from DRAFT_READY",
    })
    payload["state"] = "RELEVANCE"
    atomic_write_json(path, payload)

    with pytest.raises(AssessmentStateError, match="malformed restart note"):
        load_state("riverbed", state_dir=tmp_path / "state")


def test_c3_transition_cannot_masquerade_as_assessment_mode(tmp_path):
    path = state_path("riverbed", state_dir=tmp_path / "state")
    atomic_write_json(path, {
        "version": 1,
        "slug": "riverbed",
        "mode": "assessment",
        "state": "GATING_PRESS",
        "failure": None,
        "transitions": [
            {"state": "INTAKE_DONE", "at": "2026-07-18T12:00:00+00:00",
             "by": "fixture", "note": "intake"},
            {"state": "GATING_PRESS", "at": "2026-07-18T12:00:01+00:00",
             "by": "fixture",
             "note": "C3 refresh/press orchestration started"},
        ],
    })

    with pytest.raises(AssessmentStateError, match="does not match"):
        load_state("riverbed", state_dir=tmp_path / "state")


@pytest.mark.parametrize("transitions", [
    [{"state": "DRAFT_READY", "at": "2026-07-18T12:00:00+00:00",
      "by": "fixture", "note": "impossible"}],
    [
        {"state": "INTAKE_DONE", "at": "2026-07-18T12:00:02+00:00",
         "by": "fixture", "note": "intake"},
        {"state": "SWEEPING", "at": "2026-07-18T12:00:01+00:00",
         "by": "fixture", "note": "sweep"},
    ],
])
def test_impossible_or_nonmonotonic_history_fails_closed(
        tmp_path, transitions):
    path = state_path("riverbed", state_dir=tmp_path / "state")
    atomic_write_json(path, {
        "version": 1, "slug": "riverbed", "mode": "assessment",
        "state": transitions[-1]["state"], "failure": None,
        "transitions": transitions,
    })
    with pytest.raises(AssessmentStateError):
        load_state("riverbed", state_dir=tmp_path / "state")


def test_status_contract_uses_tail_transition_time(tmp_path):
    _seed_state(tmp_path, "AWAITING_TAXONOMY_GO")
    state = load_state("riverbed", state_dir=tmp_path / "state")
    assert assessment_status("riverbed", state_dir=tmp_path / "state") == {
        "slug": "riverbed",
        "state": "AWAITING_TAXONOMY_GO",
        "since": state["transitions"][-1]["at"],
        "alert_line": "riverbed awaiting your keyword review",
    }
    assert assessment_status("absent", state_dir=tmp_path / "state") == {
        "slug": "absent", "state": None, "since": None, "alert_line": None,
    }
