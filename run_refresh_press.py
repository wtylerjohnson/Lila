#!/usr/bin/env python3
"""Refresh one shipped Signal Board and persist its comparable delta state.

This is the single Command Center entry for the retention product.  It calls
the established runners in order and owns no collector, relevance, gate, or
rendering logic:

1. deterministic re-sweep (LLM-bearing lanes preserved, not rerun)
2. deterministic relevance calibration
3. C1 compose refresh
4. scoped award re-pull
5. Candidate Review event and vehicle watch persistence
6. adversarial verification observation, when the pass is available
7. Signal Board press, including its relevance and release gates
8. snapshot and delta persistence

A completed gated press is still comparable state.  Signal Board writes its
INTERNAL sidecar before returning nonzero for a DO-NOT-SEND result, so a fresh
sidecar authorizes snapshotting while the original nonzero status remains the
outer job status.  A pre-render failure writes no fresh sidecar and therefore
cannot mint a snapshot.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, "data", "reports")
COMPOSE_SCRIPT = os.path.join(ROOT, "run_compose.py")
WATCH_SCRIPT = os.path.join(ROOT, "run_candidate_review_watch.py")

DEFERRED_REASON = "adversarial verify pass unavailable on this baseline"

VerificationObservation = dict[str, Any]
Executor = Callable[..., Any]
Verifier = Callable[[str, datetime], VerificationObservation]
Persister = Callable[..., Any]
Certifier = Callable[..., Any]


def _slug(value: str) -> str:
    """The sanctioned canonical slugger; late-bound so importing this
    runner reads no state. One slug family names the calibration CSV,
    the relevance receipt, and the compose client: no local
    reimplementation may drift from the receipt contract."""
    from agents.assessment_chain import canonical_slug
    return canonical_slug(value)


def _press_artifact_slug(value: str) -> str:
    """The press artifact family's slug, owned by agents.press_snapshot
    (run_signal_board's naming). The INTERNAL-sidecar token below must
    match the board's write path exactly; it is a different family from
    the canonical receipt slug on purpose."""
    from agents.press_snapshot import client_slug
    return client_slug(value)


def _profile_owned_identity(value: str) -> tuple[str, str]:
    """Canonical path slug plus the profile's exact source-facing name."""
    from tools.capability import load_profile

    slug = _slug(value)
    profile = load_profile(slug)
    if profile is None or not profile.is_populated():
        raise ValueError(f"no populated capability profile for {slug!r}")
    display = str(profile.client_name or "").strip()
    if not display or _slug(display) != slug:
        raise ValueError(
            f"profile client {display!r} does not match requested slug "
            f"{slug!r}")
    return slug, display


def _press_timestamp(now: Optional[Callable[[], datetime] | datetime]) \
        -> datetime:
    moment = now() if callable(now) else now
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("refresh press timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).replace(microsecond=0)


def _file_generation(path: str) -> Optional[tuple[int, int, int]]:
    """A replacement-sensitive file token, without reading mutable content."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def _returncode(result: Any) -> int:
    if isinstance(result, int):
        return result
    value = getattr(result, "returncode", None)
    if not isinstance(value, int):
        raise TypeError("refresh child executor returned no integer returncode")
    return value


def _run_child(command: list[str], executor: Executor) -> int:
    # With no stdout/stderr capture the child streams directly into the outer
    # Command Center's disk-backed job log and heartbeat surface.
    return _returncode(executor(command, cwd=ROOT, check=False))


def _deferred(reason: str = DEFERRED_REASON) -> VerificationObservation:
    return {
        "status": "DEFERRED",
        "reason": reason,
        "verified": [],
        "disputed": [],
        "unverifiable": [],
    }


def _normalize_verification(value: Any) -> VerificationObservation:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("verification observation is not an object")
    status = value.get("status")
    if status not in {"COMPLETE", "DEFERRED"}:
        raise ValueError("verification status must be COMPLETE or DEFERRED")
    reason = value.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("verification reason must be text or null")
    normalized: VerificationObservation = {
        "status": status,
        "reason": reason,
    }
    for key in ("verified", "disputed", "unverifiable"):
        rows = value.get(key)
        if (not isinstance(rows, list)
                or any(not isinstance(row, str) for row in rows)):
            raise ValueError(f"verification {key} must be a list of keys")
        normalized[key] = sorted(set(rows))
    return normalized


def _observe_verification(
    client_name: str,
    press_timestamp: datetime,
    verifier: Optional[Verifier],
) -> VerificationObservation:
    if verifier is None:
        return _deferred()
    try:
        return _normalize_verification(verifier(client_name, press_timestamp))
    except Exception as exc:  # noqa: BLE001 - unavailability is recorded
        return _deferred(
            "adversarial verify pass failed: "
            f"{type(exc).__name__}: {str(exc)[:180]}")


def _legacy_all_federal_error(client_name: str) -> Optional[str]:
    """Return why this press is not the legacy All Federal workstation."""
    expected = os.environ.get("LILA_EXPECT_WORKSTATION_ID")
    native = os.environ.get("LILA_EXPECT_NATIVE_WORKSTATION")
    if native == "1":
        return "Refresh press + delta is unavailable in native workstations"
    if expected not in (None, "all"):
        return (
            "Refresh press + delta is limited to the legacy All Federal "
            f"workstation, not {expected!r}"
        )

    # Direct CLI calls have no Command Center binding.  Ask the established
    # review owner whether the current packet is agency-scoped; missing legacy
    # packets resolve to All Federal by that owner's existing contract.
    from agents.review import gate_designator

    designator = gate_designator(client_name, workstation_id=expected)
    if designator is not None:
        return (
            "Refresh press + delta is limited to the legacy All Federal "
            f"workstation; current scope is {designator!r}"
        )
    return None


def _default_persister(
    client_name: str,
    *,
    press_timestamp: datetime,
    verification: VerificationObservation,
) -> Any:
    """Late-bind the persistence facade; importing this runner reads no state."""
    module = importlib.import_module("agents.refresh_delta")
    persist = getattr(module, "persist_refresh_press")
    return persist(
        client_name,
        press_timestamp=press_timestamp,
        verification=verification,
        root=ROOT,
    )


def _default_certifier(
    client_name: str,
    *,
    press_timestamp: datetime,
) -> os.PathLike[str] | str:
    """Late-bind the hash-bound Signal Board release certificate."""
    module = importlib.import_module("agents.reports.signal_board_release")
    certify = getattr(module, "certify_signal_board")
    return certify(
        client_name,
        report_dir=REPORT_DIR,
        press_timestamp=press_timestamp,
    )


def _path_value(result: Any, *keys: str) -> Optional[os.PathLike[str] | str]:
    for key in keys:
        if isinstance(result, dict) and result.get(key) is not None:
            return result[key]
        value = getattr(result, key, None)
        if value is not None:
            return value
    return None


def _output_paths(result: Any) -> tuple[
        Optional[os.PathLike[str] | str], os.PathLike[str] | str,
        os.PathLike[str] | str]:
    """Accept the delta layer's object/dict shape plus a compact test tuple."""
    if isinstance(result, tuple):
        if len(result) == 2:
            return None, result[0], result[1]
        if len(result) == 3:
            return result[0], result[1], result[2]
    snapshot = _path_value(result, "snapshot_path", "snapshot")
    delta = _path_value(result, "delta_path", "json_path", "delta_json_path")
    summary = _path_value(
        result, "summary_path", "markdown_path", "delta_summary_path")
    if delta is None or summary is None:
        raise ValueError(
            "delta persister must return delta and markdown summary paths")
    return snapshot, delta, summary


def _relevance_step(client_name: str, client_slug: str, relevance_path: str,
                    moment: datetime, execute, command: list[str]) -> int:
    """Run sanctioned calibration and mint the SAME relevance receipt the
    chain writes. This step is captured (not streamed): the calibrate
    summary line is the receipt's summary, both streams re-emit
    immediately, and calibrate runs in seconds so the heartbeat surface
    loses nothing material.

    The calibration freshness token and the complete sanctioned relevance
    input binding are captured BEFORE the run. A receipt mints only after
    a fresh calibration artifact, exactly one valid (non-SKIPPED) client
    summary, a production-valid CSV, and sweep/input bindings unchanged
    during calibration; a calibrator that exits zero without writing
    fails here, before compose, press, or delta. Every failure on this
    step emits exactly ONE named [refresh:failure] marker."""
    from pathlib import Path

    from agents.assessment_chain import (
        _file_token, _fresh, _relevance_input_binding,
        write_relevance_receipt)
    from agents.review import sweep_artifact_path

    def _named(reason: str, code: int = 2) -> int:
        print("[refresh:failure] " + json.dumps({
            "stage": "relevance",
            "reason": reason,
            "fix_surface": " ".join(command[1:]),
        }, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        return code

    try:
        inputs_before = _relevance_input_binding(Path(ROOT), client_slug)
        calibration_before = _file_token(Path(relevance_path))
        sweep_path = Path(sweep_artifact_path(client_name))
        sweep_before = _file_token(sweep_path)
    except Exception as exc:  # noqa: BLE001 - binding capture is named
        return _named(f"cannot capture the relevance input binding: {exc}")
    started_at = moment.isoformat()
    proc = execute(command, cwd=ROOT, check=False,
                   capture_output=True, text=True)
    out_text = getattr(proc, "stdout", "") or ""
    err_text = getattr(proc, "stderr", "") or ""
    if out_text:
        print(out_text, end="", flush=True)
    if err_text:
        print(err_text, end="", file=sys.stderr, flush=True)
    code = _returncode(proc)
    if code != 0:
        return _named(f"relevance failed with exit {code}", code)
    summaries = [line.strip() for line in
                 (out_text + "\n" + err_text).splitlines()
                 if line.strip().startswith(f"[calibrate] {client_name}:")]
    if len(summaries) != 1 or "SKIPPED" in summaries[0]:
        return _named(
            f"calibration exited 0 without one valid {client_name} summary")
    if not _fresh(Path(relevance_path), calibration_before):
        return _named("calibration exited 0 without a fresh artifact at "
                      f"{relevance_path}")
    if _file_token(sweep_path) != sweep_before:
        return _named("the current sweep changed during calibration")
    if _relevance_input_binding(Path(ROOT), client_slug) != inputs_before:
        return _named("relevance inputs changed during calibration")
    try:
        write_relevance_receipt(
            ROOT, client_slug, client_name,
            command=command[1:],
            sweep_path=sweep_path,
            started_at=started_at,
            completed_at=_press_timestamp(None).isoformat(),
            inputs=inputs_before,
            calibration_path=relevance_path,
            summary=summaries[0])
    except Exception as exc:  # noqa: BLE001 - receipt gate is named
        return _named(f"relevance receipt could not be minted: {exc}")
    return 0


def _compose_step(execute, command: list[str],
                  client_slug: str) -> tuple[int, str, Optional[str]]:
    """Run the C1 compose refresh captured (not streamed): stderr is the
    composer's ONLY named-failure channel, so this boundary must hold the
    bytes of the current invocation to relay them. Both streams re-emit
    immediately, and the sanctioned no-flag compose is deterministic and
    runs in seconds, so the heartbeat surface loses nothing material.

    Exit 0 alone proves nothing. The published pair is validated through
    the ONE sanctioned helper (agents.assessment_chain
    .validate_compose_pair, the marker/header/binding grammar owner):
    exactly one [out:compose-trail] marker, exactly one fresh trail,
    fresh loader-valid board content for the exact profile-owned client
    in machine composition mode, and a trail/content sha binding match.
    Returns (returncode, captured stderr, defect): defect names the
    validation failure when exit 0 published no valid pair."""
    from agents.assessment_chain import (
        compose_pair_tokens, validate_compose_pair)
    try:
        content_before, trails_before = compose_pair_tokens(ROOT, client_slug)
    except OSError as exc:
        return 2, "", f"cannot token the compose pair: {exc}"
    proc = execute(command, cwd=ROOT, check=False,
                   capture_output=True, text=True)
    out_text = getattr(proc, "stdout", "") or ""
    err_text = getattr(proc, "stderr", "") or ""
    if out_text:
        print(out_text, end="", flush=True)
    if err_text:
        print(err_text, end="", file=sys.stderr, flush=True)
    code = _returncode(proc)
    if code != 0:
        return code, err_text, None
    try:
        from tools.capability import load_profile
        profile = load_profile(client_slug)
        if profile is None or not profile.is_populated():
            raise ValueError(
                f"no populated capability profile for {client_slug!r}")
        validate_compose_pair(
            ROOT, client_slug, profile.client_name, out_text,
            content_before=content_before, trails_before=trails_before)
    except Exception as exc:  # noqa: BLE001 - the pair gate is named
        return code, err_text, (
            "compose refresh exited 0 without a valid published pair: "
            f"{exc}")
    return code, err_text, None


def _candidate_review_watch_step(
    execute,
    command: list[str],
    client_slug: str,
) -> tuple[int, str, Optional[str]]:
    """Run the watch CLI and prove its marker names the promoted receipt."""

    from agents.assessment_chain import validate_candidate_review_watch_marker

    proc = execute(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    out_text = getattr(proc, "stdout", "") or ""
    err_text = getattr(proc, "stderr", "") or ""
    if out_text:
        print(out_text, end="", flush=True)
    if err_text:
        print(err_text, end="", file=sys.stderr, flush=True)
    code = _returncode(proc)
    if code != 0:
        return code, err_text, None
    try:
        validate_candidate_review_watch_marker(ROOT, client_slug, out_text)
    except Exception as exc:  # noqa: BLE001 - named outer refresh boundary
        return code, err_text, (
            "Candidate Review watch exited 0 without a valid promoted "
            f"receipt: {exc}"
        )
    return code, err_text, None


def _award_repull_step(
    client_name: str,
    execute: Executor,
    command: list[str],
) -> tuple[int, Optional[str]]:
    """Run award re-pull and prove its gate-designated sweep is fresh.

    Exit zero is not evidence that the sanctioned mutation happened.  Capture
    the exact sweep selected by the current review scope before invocation and
    require its content digest to change before Candidate Review can read it.
    A timestamp-only rewrite is not a fresh research result.  This is the
    refresh-path counterpart to the assessment adapter's REPULLING artifact
    gate.
    """

    from agents.assessment_chain import _file_token
    from agents.review import sweep_artifact_path

    try:
        sweep_path = Path(sweep_artifact_path(
            client_name,
            review_dir=os.path.join(ROOT, "data", "review"),
        ))
        before = _file_token(sweep_path)
    except Exception as exc:  # noqa: BLE001 - outer refresh names the gate
        return 2, f"cannot isolate the gate-designated sweep: {exc}"

    code = _run_child(command, execute)
    if code != 0:
        return code, None
    after = _file_token(sweep_path)
    if not after.exists or after.sha256 == before.sha256:
        return 2, (
            "award re-pull exited 0 without changing the contents of "
            f"{sweep_path}"
        )
    return 0, None


def _named_compose_failure(child_stderr: str) -> Optional[dict]:
    """C1's single schema-valid named failure, parsed from the captured
    stderr of the CURRENT composer invocation. None unless exactly one
    payload with exactly {stage, reason, fix_surface}, all nonempty
    strings, is present."""
    payloads = []
    for line in child_stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if (isinstance(parsed, dict)
                and set(parsed) == {"stage", "reason", "fix_surface"}
                and all(isinstance(parsed[key], str) and parsed[key]
                        for key in ("stage", "reason", "fix_surface"))):
            payloads.append(parsed)
    return payloads[0] if len(payloads) == 1 else None


def _named_watch_failure(child_stderr: str) -> Optional[dict]:
    """The watch CLI's single schema-valid named failure."""

    from agents.assessment_chain import _named_candidate_review_failure

    return _named_candidate_review_failure(child_stderr)


def run_refresh_press(
    client_name: str,
    *,
    now: Optional[Callable[[], datetime] | datetime] = None,
    executor: Optional[Executor] = None,
    verifier: Optional[Verifier] = None,
    persister: Optional[Persister] = None,
    certifier: Optional[Certifier] = None,
) -> int:
    """Run the sanctioned refresh steps and preserve their release outcome."""
    try:
        client_slug, client_name = _profile_owned_identity(client_name)
    except Exception as exc:  # noqa: BLE001 - identity is a named gate
        slug = _slug(client_name)
        print("[refresh:failure] " + json.dumps({
            "stage": "client-identity",
            "reason": str(exc),
            "fix_surface": f"clients/{slug}/profile.json (+ New Client)",
        }, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        return 2

    scope_error = _legacy_all_federal_error(client_name)
    if scope_error is not None:
        print(f"[refresh] {scope_error}", file=sys.stderr)
        return 2

    moment = _press_timestamp(now)
    execute = executor or subprocess.run
    persist = persister or _default_persister
    certify = certifier or _default_certifier
    py = sys.executable
    relevance_path = os.path.join(
        ROOT, "data", "state", "relevance",
        f"{client_slug}.calibration.csv",
    )
    prerequisite_steps = [
        ("re-sweep", [
            py, "run_searches.py", "--client", client_name,
            "--skip", "web", "triage", "picture", "--preserve-skipped",
        ]),
        ("relevance", [
            py, "-m", "tools.relevance.calibrate",
            "--client", client_name, "--out", relevance_path,
        ]),
    ]
    from agents.assessment_chain import candidate_review_watch_enabled

    watch_enabled = candidate_review_watch_enabled(ROOT)
    prerequisite_steps.extend([
        ("compose-refresh", [
            py, "run_compose.py", "--client", client_slug,
        ]),
        ("award re-pull", [
            py, "-m", "tools.api.award_repull", "--client", client_name,
        ]),
    ])
    if watch_enabled:
        prerequisite_steps.append(("candidate-review-watch", [
            py, "run_candidate_review_watch.py", "--client", client_slug,
        ]))
    total_steps = 8 if watch_enabled else 7

    def _named_step_failure(name: str, code: int, command: list[str],
                            child_stderr: str = "") -> None:
        """Named failure through the refresh boundary. compose-refresh
        relays the composer's own named payload, parsed from the captured
        stderr of the CURRENT invocation, so the stored reason and fix
        surface remain C1's. Malformed or missing payloads keep the
        generic named failure; nothing stale can be relayed."""
        payload = {
            "stage": name,
            "reason": f"{name} failed with exit {code}",
            "fix_surface": " ".join(command),
        }
        if name == "compose-refresh":
            relayed = _named_compose_failure(child_stderr)
            if relayed is not None:
                payload = {
                    "stage": "compose-refresh",
                    "reason": relayed["reason"],
                    "fix_surface": relayed["fix_surface"],
                }
        elif name == "candidate-review-watch":
            relayed = _named_watch_failure(child_stderr)
            if relayed is not None:
                payload = relayed
        print("[refresh:failure] "
              + json.dumps(payload, sort_keys=True, ensure_ascii=False),
              file=sys.stderr)

    for index, (name, command) in enumerate(prerequisite_steps, start=1):
        print(
            f"[refresh] step {index}/{total_steps}: "
            f"{name}: {' '.join(command[1:])}",
            flush=True,
        )
        if name == "candidate-review-watch" and not os.path.isfile(WATCH_SCRIPT):
            failure = {
                "stage": name,
                "reason": "run_candidate_review_watch.py is absent at runtime",
                "fix_surface": (
                    f"run_candidate_review_watch.py --client {client_slug} "
                    "(approved Candidate Review research inputs)"
                ),
            }
            print(
                "[refresh:failure] "
                + json.dumps(failure, sort_keys=True, ensure_ascii=False),
                file=sys.stderr,
            )
            return 127
        if name == "compose-refresh" and not os.path.isfile(COMPOSE_SCRIPT):
            failure = {
                "stage": name,
                "reason": "run_compose.py is absent at runtime",
                "fix_surface": (
                    f"run_compose.py --client {client_slug} "
                    "(C1 composer CLI)"
                ),
            }
            print(
                "[refresh:failure] "
                + json.dumps(failure, sort_keys=True, ensure_ascii=False),
                file=sys.stderr,
            )
            return 127
        if name == "relevance":
            code = _relevance_step(client_name, client_slug, relevance_path,
                                   moment, execute, command)
            if code != 0:
                # _relevance_step already emitted its single named marker
                return code
            continue
        if name == "candidate-review-watch":
            code, child_stderr, defect = _candidate_review_watch_step(
                execute,
                command,
                client_slug,
            )
            if code != 0:
                _named_step_failure(
                    name,
                    code,
                    command[1:],
                    child_stderr=child_stderr,
                )
                return code
            if defect is not None:
                print("[refresh:failure] " + json.dumps({
                    "stage": name,
                    "reason": defect,
                    "fix_surface": (
                        f"run_candidate_review_watch.py --client {client_slug} "
                        "(approved Candidate Review research inputs)"
                    ),
                }, sort_keys=True, ensure_ascii=False), file=sys.stderr)
                return 2
            continue
        if name == "compose-refresh":
            code, child_stderr, defect = _compose_step(execute, command,
                                                       client_slug)
            if code != 0:
                _named_step_failure(name, code, command[1:],
                                    child_stderr=child_stderr)
                return code
            if defect is not None:
                # exit 0 without a valid published pair stops the refresh
                # HERE: re-pull, press, and delta never run over it
                print("[refresh:failure] " + json.dumps({
                    "stage": "compose-refresh",
                    "reason": defect,
                    "fix_surface": (
                        f"run_compose.py --client {client_slug} "
                        "(C1 composer CLI)"),
                }, sort_keys=True, ensure_ascii=False), file=sys.stderr)
                return 2
            continue
        if name == "award re-pull":
            code, defect = _award_repull_step(
                client_name,
                execute,
                command,
            )
            if code != 0:
                if defect is None:
                    _named_step_failure(name, code, command[1:])
                else:
                    print("[refresh:failure] " + json.dumps({
                        "stage": name,
                        "reason": defect,
                        "fix_surface": " ".join(command[1:]),
                    }, sort_keys=True, ensure_ascii=False), file=sys.stderr)
                return code
            continue
        code = _run_child(command, execute)
        if code != 0:
            _named_step_failure(name, code, command[1:])
            return code

    verification_step = 6 if watch_enabled else 5
    press_step = 7 if watch_enabled else 6
    persistence_step = 8 if watch_enabled else 7
    print(
        f"[refresh] step {verification_step}/{total_steps}: "
        "adversarial figure verification",
        flush=True,
    )
    observation = _observe_verification(client_name, moment, verifier)
    print(f"[refresh] adversarial verification {observation['status']}: "
          f"{observation['reason'] or 'complete'}")

    internal_path = os.path.join(
        REPORT_DIR,
        f"{_press_artifact_slug(client_name)}"
        ".federal_opportunity_signals.internal.md",
    )
    internal_before = _file_generation(internal_path)
    # The press child inherits this run's moment as its render date: with
    # the default real-clock moment this is identity, and under an injected
    # replay clock it keeps the document date and the figure-freshness gate
    # on ONE clock (a frozen world otherwise reads as stale 14 days after
    # its fixtures were stamped, witnessed 2026-08-04).
    press_command = [py, "run_signal_board.py", "--client", client_name,
                     "--render-date", moment.date().isoformat()]
    print(
        f"[refresh] step {press_step}/{total_steps}: "
        f"press: {' '.join(press_command[1:])}",
        flush=True,
    )
    press_code = _run_child(press_command, execute)
    internal_after = _file_generation(internal_path)
    if internal_after is None or internal_after == internal_before:
        payload = {
            "stage": "signal-board-certification",
            "reason": (
                "Signal Board press did not write a fresh INTERNAL sidecar; "
                "no snapshot or delta was written"
            ),
            "fix_surface": (
                f"run_signal_board.py --client {client_name} "
                "(Signal Board press + QA)"
            ),
        }
        print("[refresh:failure] " + json.dumps(
            payload, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        return press_code if press_code != 0 else 2
    try:
        qa_path = certify(client_name, press_timestamp=moment)
    except Exception as exc:  # noqa: BLE001 - certificate failure is named
        payload = {
            "stage": "signal-board-certification",
            "reason": (
                "Signal Board release certification failed: "
                f"{type(exc).__name__}: {str(exc)[:220]}"
            ),
            "fix_surface": (
                f"run_signal_board.py --client {client_name} "
                "(Signal Board press + QA)"
            ),
        }
        print("[refresh:failure] " + json.dumps(
            payload, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        return press_code if press_code != 0 else 2
    print(f"[out:signal-board-qa] {Path(qa_path)}")

    print(
        f"[refresh] step {persistence_step}/{total_steps}: delta",
        flush=True,
    )
    try:
        persisted = persist(
            client_name,
            press_timestamp=moment,
            verification=observation,
        )
        snapshot_path, delta_path, summary_path = _output_paths(persisted)
    except Exception as exc:  # noqa: BLE001 - press remains, delta failure loud
        print("[refresh] delta persistence failed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if snapshot_path is not None:
        print(f"[out:snapshot] {Path(snapshot_path)}")
    print(f"[out:delta] {Path(delta_path)}")
    print(f"[out:delta-summary] {Path(summary_path)}")
    return press_code


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", required=True)
    parser.add_argument(
        "--now",
        help="explicit timezone-aware ISO moment for this refresh; the "
             "orchestrating adapter passes its own clock so a replayed "
             "world keeps every stage on one clock (default: real now)")
    args = parser.parse_args(argv)
    moment = datetime.fromisoformat(args.now) if args.now else None
    return run_refresh_press(args.client, now=moment)


if __name__ == "__main__":
    raise SystemExit(main())
