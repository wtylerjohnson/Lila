"""Refresh Press orchestration: existing runners, one ordered entry."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_refresh_press as refresh  # noqa: E402
import run_searches  # noqa: E402
from agents import press_snapshot  # noqa: E402
from agents import refresh_delta  # noqa: E402


CLIENT = "Testco"
NOW = datetime(2026, 7, 18, 14, 30, tzinfo=timezone.utc)
REAL_PROFILE_OWNED_IDENTITY = refresh._profile_owned_identity
REAL_AWARD_REPULL_STEP = refresh._award_repull_step


@pytest.fixture(autouse=True)
def _mock_parallel_c1_contract(tmp_path, monkeypatch):
    script = tmp_path / "parallel-c1" / "run_compose.py"
    script.parent.mkdir()
    script.write_text("# C1 contract stub\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "COMPOSE_SCRIPT", str(script))


@pytest.fixture(autouse=True)
def _release_certification_unit_scope(monkeypatch):
    """Runner-order tests mock the renderer, so they also mock its QA seam."""
    def certify(client_name, *, press_timestamp):
        path = (Path(refresh.REPORT_DIR)
                / "testco.federal_opportunity_signals.qa.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    monkeypatch.setattr(refresh, "_default_certifier", certify)


@pytest.fixture(autouse=True)
def _profile_identity_unit_scope(monkeypatch):
    """Most runner tests use the synthetic Testco command identity."""
    monkeypatch.setattr(
        refresh, "_profile_owned_identity",
        lambda value: (refresh._slug(value), value))


@pytest.fixture(autouse=True)
def _legacy_refresh_steps_unit_scope(monkeypatch):
    """Existing C3 unit probes isolate the pre-Candidate-Review stages.

    Dedicated tests below enable and pin the additive watch stage.  Keeping
    this isolation avoids making every historical child-executor double mint a
    real generation receipt.
    """

    import agents.assessment_chain as assessment_chain

    monkeypatch.setattr(
        assessment_chain,
        "candidate_review_watch_enabled",
        lambda _root: False,
    )


def _internal(reports: Path) -> Path:
    return reports / "testco.federal_opportunity_signals.internal.md"


def _outputs(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "data" / "state" / "deltas" / "testco"
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "snapshot_path": root / "2026-07-18T143000Z.json",
        "delta_path": root / "2026-07-18T143000Z.delta.json",
        "summary_path": root / "2026-07-18T143000Z.internal.md",
    }
    for path in paths.values():
        path.write_text("{}\n", encoding="utf-8")
    return paths


def _successful_executor(commands: list[list[str]], reports: Path):
    def execute(command, **kwargs):
        commands.append(command)
        assert kwargs == {"cwd": refresh.ROOT, "check": False}
        if "run_signal_board.py" in command:
            reports.mkdir(parents=True, exist_ok=True)
            _internal(reports).write_text("# fresh press\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)
    return execute


@pytest.fixture(autouse=True)
def _relevance_step_unit_scope(monkeypatch):
    """These unit tests pin the OTHER refresh steps; the relevance step's
    capture + receipt minting is covered end to end in
    test_composer_integration. The stub calls through to the executor
    exactly as the legacy child runner did, so every pinned command list,
    kwargs assertion, and failure propagation stays byte-identical."""
    def step(client_name, client_slug, relevance_path, moment,
             execute, command):
        return refresh._returncode(
            execute(command, cwd=refresh.ROOT, check=False))
    monkeypatch.setattr(refresh, "_relevance_step", step)


@pytest.fixture(autouse=True)
def _compose_step_unit_scope(monkeypatch):
    """Same scoping for the compose step: its stderr capture and named
    relay are covered end to end in test_composer_integration; the stub
    calls through to the executor exactly as the legacy child runner
    did."""
    def step(execute, command, client_slug):
        return refresh._returncode(
            execute(command, cwd=refresh.ROOT, check=False)), "", None
    monkeypatch.setattr(refresh, "_compose_step", step)


@pytest.fixture(autouse=True)
def _award_repull_step_unit_scope(monkeypatch):
    """Legacy ordering probes mock child execution, not artifact mutation.

    Dedicated adversarial tests below restore the real freshness boundary.
    """

    def step(_client_name, execute, command):
        return refresh._returncode(
            execute(command, cwd=refresh.ROOT, check=False)), None

    monkeypatch.setattr(refresh, "_award_repull_step", step)


def test_order_timestamp_verification_and_outputs_are_pinned(
        tmp_path, monkeypatch, capsys):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    commands: list[list[str]] = []
    now_calls = []
    persisted = []
    verified = []
    events = []

    def clock():
        now_calls.append(True)
        return NOW

    def verifier(client_name, press_timestamp):
        events.append("verify")
        verified.append((client_name, press_timestamp))
        return {
            "status": "COMPLETE", "reason": None,
            "verified": ["F2", "F1", "F1"],
            "disputed": ["F4"], "unverifiable": ["F3"],
        }

    def persister(client_name, *, press_timestamp, verification):
        events.append("persist")
        persisted.append((client_name, press_timestamp, verification))
        return _outputs(tmp_path)

    def certifier(client_name, *, press_timestamp):
        events.append("certify")
        path = reports / "testco.federal_opportunity_signals.qa.json"
        path.write_text("{}\n", encoding="utf-8")
        return path

    base_executor = _successful_executor(commands, reports)

    def execute(command, **kwargs):
        if "run_signal_board.py" in command:
            events.append("signal_board")
        elif "tools.relevance.calibrate" in command:
            events.append("relevance")
        elif "run_compose.py" in command:
            events.append("compose_refresh")
        elif any("award_repull" in part for part in command):
            events.append("award_repull")
        else:
            events.append("re_sweep")
        return base_executor(command, **kwargs)

    code = refresh.run_refresh_press(
        CLIENT,
        now=clock,
        executor=execute,
        verifier=verifier,
        persister=persister,
        certifier=certifier,
    )

    py = refresh.sys.executable
    assert code == 0
    assert commands == [
        [py, "run_searches.py", "--client", CLIENT, "--skip", "web",
         "triage", "picture", "--preserve-skipped"],
        [py, "-m", "tools.relevance.calibrate", "--client", CLIENT,
         "--out", os.path.join(
             refresh.ROOT, "data", "state", "relevance",
             "testco.calibration.csv")],
        [py, "run_compose.py", "--client", "testco"],
        [py, "-m", "tools.api.award_repull", "--client", CLIENT],
        # 2026-08-04: the press child rides this run's moment as its render
        # date so a replayed world keeps document date and figure freshness
        # on one clock.
        [py, "run_signal_board.py", "--client", CLIENT,
         "--render-date", NOW.date().isoformat()],
    ]
    assert now_calls == [True]
    assert events == [
        "re_sweep", "relevance", "compose_refresh", "award_repull",
        "verify", "signal_board", "certify", "persist"]
    assert verified == [(CLIENT, NOW)]
    assert persisted == [(CLIENT, NOW, {
        "status": "COMPLETE", "reason": None,
        "verified": ["F1", "F2"], "disputed": ["F4"],
        "unverifiable": ["F3"],
    })]
    output = capsys.readouterr().out
    assert "[out:snapshot]" in output
    assert "[out:signal-board-qa]" in output
    assert "[out:delta]" in output
    assert "[out:delta-summary]" in output


def test_candidate_review_watch_runs_after_compose_and_award_repull(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    watch_script = tmp_path / "run_candidate_review_watch.py"
    watch_script.write_text("# watch contract stub\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "WATCH_SCRIPT", str(watch_script))
    import agents.assessment_chain as assessment_chain

    monkeypatch.setattr(
        assessment_chain,
        "candidate_review_watch_enabled",
        lambda _root: True,
    )
    commands: list[list[str]] = []
    execute = _successful_executor(commands, reports)

    def watch_step(executor, command, client_slug):
        assert client_slug == "testco"
        code = refresh._returncode(
            executor(command, cwd=refresh.ROOT, check=False)
        )
        return code, "", None

    monkeypatch.setattr(refresh, "_candidate_review_watch_step", watch_step)

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=execute,
        persister=lambda *_args, **_kwargs: _outputs(tmp_path),
    ) == 0
    assert [command[1] for command in commands] == [
        "run_searches.py",
        "-m",
        "run_compose.py",
        "-m",
        "run_candidate_review_watch.py",
        "run_signal_board.py",
    ]


def test_candidate_review_watch_failure_after_repull_stops_before_press(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    watch_script = tmp_path / "run_candidate_review_watch.py"
    watch_script.write_text("# watch contract stub\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "WATCH_SCRIPT", str(watch_script))
    import agents.assessment_chain as assessment_chain

    monkeypatch.setattr(
        assessment_chain,
        "candidate_review_watch_enabled",
        lambda _root: True,
    )
    commands: list[list[str]] = []
    execute = _successful_executor(commands, reports)
    failure = {
        "stage": "candidate-review-watch",
        "reason": "approved frame is unavailable",
        "fix_surface": "approve the Candidate Review frame",
    }

    def watch_step(executor, command, client_slug):
        assert client_slug == "testco"
        executor(command, cwd=refresh.ROOT, check=False)
        return 2, json.dumps(failure) + "\n", None

    monkeypatch.setattr(refresh, "_candidate_review_watch_step", watch_step)
    persisted: list[bool] = []
    verified: list[bool] = []

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=execute,
        verifier=lambda *_args: verified.append(True),
        persister=lambda *_args, **_kwargs: persisted.append(True),
    ) == 2
    assert [command[1] for command in commands] == [
        "run_searches.py",
        "-m",
        "run_compose.py",
        "-m",
        "run_candidate_review_watch.py",
    ]
    assert commands[3][2] == "tools.api.award_repull"
    assert verified == []
    assert persisted == []


def test_lowercase_slug_uses_profile_display_for_source_bound_steps(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    monkeypatch.setattr(
        refresh, "_legacy_all_federal_error", lambda _client: None)
    import tools.capability as capability
    monkeypatch.setattr(capability, "load_profile", lambda _slug: SimpleNamespace(
        client_name="Riverbed", is_populated=lambda: True))
    monkeypatch.setattr(
        refresh, "_profile_owned_identity", REAL_PROFILE_OWNED_IDENTITY)
    commands = []
    observed = []

    def execute(command, **kwargs):
        assert kwargs == {"cwd": refresh.ROOT, "check": False}
        commands.append(command)
        if "run_signal_board.py" in command:
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "riverbed.federal_opportunity_signals.internal.md") \
                .write_text("# fresh press\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def certifier(client_name, *, press_timestamp):
        observed.append(("certify", client_name))
        path = reports / "riverbed.federal_opportunity_signals.qa.json"
        path.write_text("{}\n", encoding="utf-8")
        return path

    def persister(client_name, *, press_timestamp, verification):
        observed.append(("persist", client_name))
        return _outputs(tmp_path)

    assert refresh.run_refresh_press(
        "riverbed", now=NOW, executor=execute,
        verifier=lambda client, _moment: {
            "status": "COMPLETE", "reason": None,
            "verified": [client], "disputed": [], "unverifiable": [],
        },
        persister=persister, certifier=certifier,
    ) == 0

    py = refresh.sys.executable
    assert commands == [
        [py, "run_searches.py", "--client", "Riverbed", "--skip", "web",
         "triage", "picture", "--preserve-skipped"],
        [py, "-m", "tools.relevance.calibrate", "--client", "Riverbed",
         "--out", os.path.join(
             refresh.ROOT, "data", "state", "relevance",
             "riverbed.calibration.csv")],
        [py, "run_compose.py", "--client", "riverbed"],
        [py, "-m", "tools.api.award_repull", "--client", "Riverbed"],
        [py, "run_signal_board.py", "--client", "Riverbed",
         "--render-date", NOW.date().isoformat()],
    ]
    assert observed == [
        ("certify", "Riverbed"), ("persist", "Riverbed")]


def test_default_verifier_is_explicitly_deferred(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    observations = []

    def persister(_client, *, press_timestamp, verification):
        assert press_timestamp == NOW
        observations.append(verification)
        return _outputs(tmp_path)

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=_successful_executor([], reports),
        persister=persister,
    ) == 0
    assert observations == [{
        "status": "DEFERRED",
        "reason": "adversarial verify pass unavailable on this baseline",
        "verified": [], "disputed": [], "unverifiable": [],
    }]


def test_verifier_failure_is_recorded_not_silent(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    observations = []

    def unavailable(_client, _moment):
        raise RuntimeError("adapter offline")

    def persister(_client, *, press_timestamp, verification):
        observations.append(verification)
        return _outputs(tmp_path)

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=_successful_executor([], reports),
        verifier=unavailable,
        persister=persister,
    ) == 0
    assert observations[0]["status"] == "DEFERRED"
    assert observations[0]["reason"] == (
        "adversarial verify pass failed: RuntimeError: adapter offline")


def test_re_sweep_failure_stops_before_repull_press_and_delta(
        tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "REPORT_DIR", str(tmp_path / "reports"))
    commands = []
    persisted = []

    def execute(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=7)

    assert refresh.run_refresh_press(
        CLIENT, now=NOW, executor=execute,
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 7
    assert len(commands) == 1
    assert commands[0][1] == "run_searches.py"
    assert persisted == []


def test_relevance_failure_stops_before_compose_repull_press_and_delta(
        tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "REPORT_DIR", str(tmp_path / "reports"))
    commands = []
    persisted = []

    def execute(command, **_kwargs):
        commands.append(command)
        code = 9 if "tools.relevance.calibrate" in command else 0
        return SimpleNamespace(returncode=code)

    assert refresh.run_refresh_press(
        CLIENT, now=NOW, executor=execute,
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 9
    assert [command[1] for command in commands] == ["run_searches.py", "-m"]
    assert commands[-1][2] == "tools.relevance.calibrate"
    assert persisted == []


def test_c1_absent_fails_named_at_compose_refresh_and_rerun_resumes(
        tmp_path, monkeypatch, capsys):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    compose_script = tmp_path / "missing" / "run_compose.py"
    monkeypatch.setattr(refresh, "COMPOSE_SCRIPT", str(compose_script))
    commands = []
    persisted = []

    def execute(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    assert refresh.run_refresh_press(
        CLIENT, now=NOW, executor=execute,
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 127
    assert [command[1] for command in commands] == ["run_searches.py", "-m"]
    assert persisted == []
    stderr = capsys.readouterr().err
    marker = next(
        line.removeprefix("[refresh:failure] ")
        for line in stderr.splitlines()
        if line.startswith("[refresh:failure] ")
    )
    assert json.loads(marker) == {
        "stage": "compose-refresh",
        "reason": "run_compose.py is absent at runtime",
        "fix_surface": "run_compose.py --client testco (C1 composer CLI)",
    }

    compose_script.parent.mkdir()
    compose_script.write_text("# C1 contract stub\n", encoding="utf-8")
    resumed_commands = []
    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=_successful_executor(resumed_commands, reports),
        persister=lambda *_a, **_k: _outputs(tmp_path),
    ) == 0
    assert len(resumed_commands) == 5


def test_award_repull_failure_stops_before_press_and_delta(
        tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "REPORT_DIR", str(tmp_path / "reports"))
    commands = []
    persisted = []
    verified = []

    def execute(command, **_kwargs):
        commands.append(command)
        code = 2 if "tools.api.award_repull" in command else 0
        return SimpleNamespace(returncode=code)

    assert refresh.run_refresh_press(
        CLIENT, now=NOW, executor=execute,
        verifier=lambda *_args: verified.append(True),
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 2
    assert [command[1] for command in commands] == [
        "run_searches.py", "-m", "run_compose.py", "-m"]
    assert commands[-1][2] == "tools.api.award_repull"
    assert verified == []
    assert persisted == []


def test_award_repull_exit_zero_without_fresh_sweep_stops_before_watch_press(
        tmp_path, monkeypatch, capsys):
    reports = tmp_path / "reports"
    sweep_path = tmp_path / "searches_testco.json"
    sweep_path.write_text('{"results": {}}\n', encoding="utf-8")
    watch_script = tmp_path / "run_candidate_review_watch.py"
    watch_script.write_text("# watch boundary\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    monkeypatch.setattr(refresh, "WATCH_SCRIPT", str(watch_script))
    monkeypatch.setattr(
        refresh,
        "_award_repull_step",
        REAL_AWARD_REPULL_STEP,
    )
    import agents.assessment_chain as assessment_chain
    import agents.review as review

    monkeypatch.setattr(
        assessment_chain,
        "candidate_review_watch_enabled",
        lambda _root: True,
    )
    monkeypatch.setattr(
        review,
        "sweep_artifact_path",
        lambda _client, **_kwargs: str(sweep_path),
    )
    commands: list[list[str]] = []
    persisted: list[bool] = []
    verified: list[bool] = []

    def execute(command, **_kwargs):
        commands.append(command)
        if any("award_repull" in part for part in command):
            # Rewriting the same bytes changes file metadata but is still a
            # semantically empty re-pull and must not unlock watch execution.
            sweep_path.write_bytes(sweep_path.read_bytes())
        return SimpleNamespace(returncode=0)

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=execute,
        verifier=lambda *_args: verified.append(True),
        persister=lambda *_args, **_kwargs: persisted.append(True),
    ) == 2
    assert [command[1] for command in commands] == [
        "run_searches.py",
        "-m",
        "run_compose.py",
        "-m",
    ]
    assert commands[-1][2] == "tools.api.award_repull"
    assert not any(
        "run_candidate_review_watch.py" in part
        or "run_signal_board.py" in part
        for command in commands
        for part in command
    )
    assert verified == []
    assert persisted == []
    markers = [
        line.removeprefix("[refresh:failure] ")
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("[refresh:failure] ")
    ]
    assert len(markers) == 1
    assert json.loads(markers[0]) == {
        "stage": "award re-pull",
        "reason": (
            "award re-pull exited 0 without changing the contents of "
            f"{sweep_path}"
        ),
        "fix_surface": "-m tools.api.award_repull --client Testco",
    }


def test_fresh_award_repull_mutation_allows_watch_and_press(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    sweep_path = tmp_path / "searches_testco.json"
    sweep_path.write_text('{"results": {}}\n', encoding="utf-8")
    watch_script = tmp_path / "run_candidate_review_watch.py"
    watch_script.write_text("# watch boundary\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    monkeypatch.setattr(refresh, "WATCH_SCRIPT", str(watch_script))
    monkeypatch.setattr(
        refresh,
        "_award_repull_step",
        REAL_AWARD_REPULL_STEP,
    )
    import agents.assessment_chain as assessment_chain
    import agents.review as review

    monkeypatch.setattr(
        assessment_chain,
        "candidate_review_watch_enabled",
        lambda _root: True,
    )
    monkeypatch.setattr(
        review,
        "sweep_artifact_path",
        lambda _client, **_kwargs: str(sweep_path),
    )
    commands: list[list[str]] = []

    def execute(command, **_kwargs):
        commands.append(command)
        if any("award_repull" in part for part in command):
            sweep_path.write_text(
                '{"results": {"award_repulls": []}}\n',
                encoding="utf-8",
            )
        if "run_signal_board.py" in command:
            reports.mkdir(parents=True, exist_ok=True)
            _internal(reports).write_text("# fresh press\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def watch_step(executor, command, client_slug):
        assert client_slug == "testco"
        code = refresh._returncode(
            executor(command, cwd=refresh.ROOT, check=False)
        )
        return code, "", None

    monkeypatch.setattr(refresh, "_candidate_review_watch_step", watch_step)

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=execute,
        persister=lambda *_args, **_kwargs: _outputs(tmp_path),
    ) == 0
    assert [command[1] for command in commands] == [
        "run_searches.py",
        "-m",
        "run_compose.py",
        "-m",
        "run_candidate_review_watch.py",
        "run_signal_board.py",
    ]
    assert commands[3][2] == "tools.api.award_repull"


def test_gated_press_with_fresh_internal_sidecar_snapshots_and_preserves_exit(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    _internal(reports).write_text("# prior press\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    commands = []
    persisted = []

    def execute(command, **_kwargs):
        commands.append(command)
        if "run_signal_board.py" in command:
            _internal(reports).write_text("# current gated press\n",
                                          encoding="utf-8")
            return SimpleNamespace(returncode=2)
        return SimpleNamespace(returncode=0)

    def persister(*args, **kwargs):
        persisted.append((args, kwargs))
        return _outputs(tmp_path)

    assert refresh.run_refresh_press(
        CLIENT, now=NOW, executor=execute, persister=persister,
    ) == 2
    assert len(commands) == 5
    assert len(persisted) == 1


def test_pre_render_press_failure_without_fresh_sidecar_writes_no_delta(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    _internal(reports).write_text("# prior press\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    persisted = []

    def execute(command, **_kwargs):
        code = 2 if "run_signal_board.py" in command else 0
        return SimpleNamespace(returncode=code)

    assert refresh.run_refresh_press(
        CLIENT, now=NOW, executor=execute,
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 2
    assert persisted == []


def test_success_press_without_fresh_sidecar_fails_named_before_delta(
        tmp_path, monkeypatch, capsys):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    persisted = []

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=lambda *_a, **_k: SimpleNamespace(returncode=0),
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 2
    assert persisted == []
    markers = [json.loads(line.removeprefix("[refresh:failure] "))
               for line in capsys.readouterr().err.splitlines()
               if line.startswith("[refresh:failure] ")]
    assert len(markers) == 1
    assert markers[0]["stage"] == "signal-board-certification"
    assert "fresh INTERNAL sidecar" in markers[0]["reason"]


def test_certificate_failure_is_named_and_stops_before_delta(
        tmp_path, monkeypatch, capsys):
    reports = tmp_path / "reports"
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    persisted = []

    def broken(*_args, **_kwargs):
        raise ValueError("hash mismatch")

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=_successful_executor([], reports),
        certifier=broken,
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 2
    assert persisted == []
    markers = [json.loads(line.removeprefix("[refresh:failure] "))
               for line in capsys.readouterr().err.splitlines()
               if line.startswith("[refresh:failure] ")]
    assert len(markers) == 1
    assert markers[0]["stage"] == "signal-board-certification"
    assert "hash mismatch" in markers[0]["reason"]


def test_verifier_sidecar_write_cannot_masquerade_as_a_rendered_press(
        tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    _internal(reports).write_text("# prior press\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "REPORT_DIR", str(reports))
    persisted = []

    def verifier(_client, _moment):
        _internal(reports).write_text(
            "# verifier observation\n", encoding="utf-8")
        return _deferred_observation()

    def execute(command, **_kwargs):
        code = 2 if "run_signal_board.py" in command else 0
        return SimpleNamespace(returncode=code)

    assert refresh.run_refresh_press(
        CLIENT,
        now=NOW,
        executor=execute,
        verifier=verifier,
        persister=lambda *_a, **_k: persisted.append(True),
    ) == 2
    assert persisted == []


def test_preserve_skipped_keeps_values_and_refreshes_other_lanes():
    previous = {
        "web": [{"url": "https://example.test", "nested": {"rank": 1}}],
        "triage": {"N1": {"verdict": "monitor", "reason": "watch"}},
        "research_picture": {
            "headline": "Prior approved synthesis",
            "watchlist": ["N1"],
        },
        "sam.gov": [{"source_id": "OLD"}],
        "usaspending.gov": [{"old": True}],
    }
    refreshed = {
        "sam.gov": [{"source_id": "NEW"}],
        "usaspending.gov": [{"new": True}],
    }
    expected_bytes = {
        key: json.dumps(previous[key], ensure_ascii=False, separators=(",", ":"))
        for key in ("web", "triage", "research_picture")
    }

    preserved = run_searches._preserve_skipped_results(
        previous, refreshed, ["web", "triage", "picture"])

    assert preserved == ["web", "triage", "research_picture"]
    for key, exact in expected_bytes.items():
        assert json.dumps(
            refreshed[key], ensure_ascii=False, separators=(",", ":")) == exact
    assert refreshed["sam.gov"] == [{"source_id": "NEW"}]
    assert refreshed["usaspending.gov"] == [{"new": True}]
    refreshed["web"][0]["nested"]["rank"] = 9
    assert previous["web"][0]["nested"]["rank"] == 1


def test_command_center_maps_refresh_press_to_stateful_adapter():
    import ui.server as server

    command = server._step_cmd("refresh_press", CLIENT, {})
    assert command == [
        server.sys.executable, "run_assessment.py", "--client", CLIENT,
        "--refresh"]


def test_refresh_press_rejects_non_all_federal_workstation(
        monkeypatch, capsys):
    calls = []

    for workstation_id, native in (("agency_dhs", "0"), ("all", "1")):
        monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", workstation_id)
        monkeypatch.setenv("LILA_EXPECT_NATIVE_WORKSTATION", native)
        assert refresh.run_refresh_press(
            CLIENT,
            now=NOW,
            executor=lambda *_args, **_kwargs: calls.append(True),
        ) == 2
    assert calls == []
    assert "legacy All Federal" in capsys.readouterr().err


def test_command_center_exposes_exact_signal_board_refresh_sequence():
    # 2026-07-24 one-refresh law (operator ruling 2026-07-23): the refresh
    # cockpit is retired from the client page. The refresh_press step and
    # its API contract stay; the single Refresh control returns the
    # operator to the Analyst Layer instead.
    import ui.server as srv
    source = open(os.path.join(
        os.path.dirname(srv.__file__), "index.html")).read()
    assert "buildRefreshPress(refreshSlot)" not in source
    assert "refreshPressSlot" not in source
    assert "btn-one-refresh" in source
    assert "return to the Analyst Layer" in source
def _snapshot(moment: datetime, *, candidate: bool = False):
    source = press_snapshot.SourceArtifact(
        path="data/sweeps/testco.json", sha256="a" * 64)
    candidates = []
    screen = []
    if candidate:
        candidates.append(press_snapshot.Candidate(
            id="SAM-NEW", kind="sam.gov", title="New notice",
            score=88, tier="core"))
        screen.append(press_snapshot.CandidateScreen(
            id="SAM-NEW", kind="sam.gov", title="New notice",
            score=88, tier="core", relevant=True))
    return press_snapshot.PressSnapshot(
        client_name=CLIENT,
        slug="testco",
        press_timestamp=moment,
        source_artifacts=press_snapshot.SourceArtifacts(
            sweep=source, figure_source="none"),
        candidates=candidates,
        candidate_screen=screen,
        figures=[],
        windows=[],
        recompete_events=[],
        gate_state=press_snapshot.GateState(
            status="CLEAN", render_date=moment.date().isoformat()),
        verification=press_snapshot.VerificationState(),
    )


def _deferred_observation():
    return {
        "status": "DEFERRED",
        "reason": refresh.DEFERRED_REASON,
        "verified": [],
        "disputed": [],
        "unverifiable": [],
    }


def test_persistence_baseline_second_press_and_same_timestamp_are_stable(
        tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    later = NOW + timedelta(days=1)

    def build(_client, *, press_timestamp, **_kwargs):
        return _snapshot(press_timestamp, candidate=press_timestamp == later)

    monkeypatch.setattr(press_snapshot, "build_press_snapshot", build)
    first = refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=NOW,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )
    assert {key: value.name for key, value in first.items()} == {
        "snapshot_path": "2026-07-18T143000Z.json",
        "delta_path": "2026-07-18T143000Z.delta.json",
        "summary_path": "2026-07-18T143000Z.internal.md",
    }
    first_bytes = {key: path.read_bytes() for key, path in first.items()}
    baseline = json.loads(first["delta_path"].read_text(encoding="utf-8"))
    assert baseline["baseline_created"] is True
    assert baseline["items"] == []

    repeated = refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=NOW,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )
    assert {key: path.read_bytes() for key, path in repeated.items()} == first_bytes

    second = refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=later,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )
    delta = json.loads(second["delta_path"].read_text(encoding="utf-8"))
    assert delta["baseline_created"] is False
    assert delta["before_timestamp"] == NOW.isoformat().replace("+00:00", "Z")
    assert delta["counts"]["NEW"] == 1
    assert [(row["kind"], row["id"]) for row in delta["items"]] == [
        ("NEW", "SAM-NEW")]

    feed = refresh_delta.load_latest_delta_feed(
        CLIENT, state_root=state_root)
    assert feed["total"] == 1
    assert feed["suppressed"] == 0
    assert feed["items"][0]["kind"] == "NEW"
    assert feed["items"][0]["slug"] == "testco"


def test_persistence_uses_pre_refresh_shortlist_for_drop_and_is_idempotent(
        tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    later = NOW + timedelta(days=1)

    def build(_client, *, press_timestamp, **_kwargs):
        snapshot = _snapshot(press_timestamp)
        shortlisted = press_timestamp == NOW
        score = 88 if shortlisted else 2
        tier = "core" if shortlisted else "adjacent"
        candidate = press_snapshot.Candidate(
            id="SAM-DROP", kind="sam.gov", title="Dropped notice",
            score=88, tier="core",
        )
        screen = press_snapshot.CandidateScreen(
            id="SAM-DROP", kind="sam.gov", title="Dropped notice",
            score=score, tier=tier, relevant=shortlisted,
        )
        return snapshot.model_copy(update={
            "candidates": [candidate] if shortlisted else [],
            "candidate_screen": [screen],
        })

    monkeypatch.setattr(press_snapshot, "build_press_snapshot", build)
    refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=NOW,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )
    refreshed = refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=later,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )

    delta = json.loads(refreshed["delta_path"].read_text(encoding="utf-8"))
    assert delta["before_timestamp"] == NOW.isoformat().replace("+00:00", "Z")
    assert delta["counts"] == {
        "NEW": 0,
        "DROPPED": 1,
        "FIGURE_DRIFT": 0,
        "WINDOW_MOVED": 0,
        "RECOMPETE_APPEARED": 0,
        "GATE_STATE_CHANGED": 0,
    }
    assert len(delta["items"]) == 1
    dropped = delta["items"][0]
    assert (dropped["kind"], dropped["id"], dropped["reason"]) == (
        "DROPPED", "SAM-DROP", "fell_below_threshold")
    assert dropped["before"]["score"] == 88
    assert dropped["after"]["score"] == 2
    assert dropped["after"]["relevant"] is False

    refreshed_bytes = {
        name: path.read_bytes() for name, path in refreshed.items()
    }
    repeated = refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=later,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )
    assert {
        name: path.read_bytes() for name, path in repeated.items()
    } == refreshed_bytes


def test_persistence_normalizes_legacy_gate_violation_before_summary(
        tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    client_dir = state_root / "testco"
    client_dir.mkdir(parents=True)
    prior = _snapshot(NOW).model_dump(mode="json")
    prior["gate_state"] = {
        "status": "DO_NOT_SEND",
        "render_date": NOW.date().isoformat(),
        "violations": [
            "'PURPOSE' belongs to client '_meta' — false-positive metadata"
        ],
    }
    (client_dir / "2026-07-18T143000Z.json").write_text(
        json.dumps(prior), encoding="utf-8")
    later = NOW + timedelta(days=1)
    monkeypatch.setattr(
        press_snapshot,
        "build_press_snapshot",
        lambda *_args, **_kwargs: _snapshot(later),
    )

    persisted = refresh_delta.persist_refresh_press(
        CLIENT,
        press_timestamp=later,
        verification=_deferred_observation(),
        root=tmp_path,
        state_root=state_root,
    )

    summary = persisted["summary_path"].read_text(encoding="utf-8")
    delta = json.loads(persisted["delta_path"].read_text(encoding="utf-8"))
    assert "—" not in summary
    assert delta["counts"]["GATE_STATE_CHANGED"] == 1
    assert delta["items"][0]["before"]["violations"] == [
        "'PURPOSE' belongs to client '_meta', false-positive metadata"
    ]


def test_latest_delta_feed_is_empty_without_a_press(tmp_path):
    assert refresh_delta.load_latest_delta_feed(
        CLIENT, state_root=tmp_path) == {
            "items": [], "total": 0, "suppressed": 0}
