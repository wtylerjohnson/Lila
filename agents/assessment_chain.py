"""Resumable assessment-chain orchestration with one human checkpoint.

This module owns chain state and runner adaptation only.  Every business
operation remains in its sanctioned runner; the chain records entry into a
stage before invoking that runner, then advances only after the runner's exit
and artifact contract agree.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Protocol

from tools.artifacts import atomic_write_json

SCHEMA_VERSION = 1
STATES = (
    "INTAKE_DONE",
    "SWEEPING",
    "RELEVANCE",
    "AWAITING_TAXONOMY_GO",
    "COMPOSING",
    "REPULLING",
    "GATING_PRESS",
    "DRAFT_READY",
    "FAILED",
)
RUNNER_STATES = (
    "SWEEPING",
    "RELEVANCE",
    "COMPOSING",
    "REPULLING",
    "GATING_PRESS",
)
RESTARTABLE_STATES = (
    "INTAKE_DONE",
    "SWEEPING",
    "RELEVANCE",
    "AWAITING_TAXONOMY_GO",
    "COMPOSING",
    "REPULLING",
    "GATING_PRESS",
)
_RESTART_RANK = {
    state: index for index, state in enumerate(RESTARTABLE_STATES)
}

_ROOT = Path(__file__).resolve().parents[1]
_AUTO_BY = "assessment-chain"
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class AssessmentStateError(RuntimeError):
    """Persisted state is missing, corrupt, or incompatible."""


class PauseRequired(AssessmentStateError):
    """A downstream transition was requested without a taxonomy GO."""


@dataclass(frozen=True)
class RunnerOutcome:
    """One adapter result after exit-code and artifact interpretation."""

    success: bool
    returncode: int
    note: str
    reason: Optional[str] = None
    fix_surface: Optional[str] = None
    artifact: Optional[str] = None
    stdout: str = ""
    stderr: str = ""


class RunnerAdapter(Protocol):
    def run(self, stage: str, *, slug: str, mode: str) -> RunnerOutcome:
        """Invoke the sanctioned runner for ``stage`` and validate output."""


@dataclass(frozen=True)
class ChainResult:
    slug: str
    state: str
    paused: bool
    transitions: tuple[dict, ...]
    failure: Optional[dict]


def canonical_slug(client: str) -> str:
    from tools.capability import _slug

    slug = _slug(client)
    if not slug:
        raise ValueError("client must contain at least one letter or number")
    return slug


def default_state_dir(root: Path | str = _ROOT) -> Path:
    override = os.environ.get("LILA_ASSESSMENT_STATE_DIR")
    if override:
        return Path(override)
    return Path(root) / "data" / "state" / "assessment"


def state_path(slug: str, *, state_dir: Path | str | None = None) -> Path:
    return Path(state_dir or default_state_dir()) / f"{canonical_slug(slug)}.state.json"


def go_path(slug: str, *, state_dir: Path | str | None = None) -> Path:
    return Path(state_dir or default_state_dir()) / f"{canonical_slug(slug)}.go.json"


def _aware_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("assessment timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse_aware(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise AssessmentStateError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssessmentStateError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AssessmentStateError(f"{label} must be timezone-aware")
    return parsed


def _validate_transition(row: object, index: int) -> dict:
    if not isinstance(row, dict):
        raise AssessmentStateError(f"transition {index} must be an object")
    if set(row) != {"state", "at", "by", "note"}:
        raise AssessmentStateError(
            f"transition {index} must contain state, at, by, note")
    if row["state"] not in STATES:
        raise AssessmentStateError(
            f"transition {index} has unknown state {row['state']!r}")
    _parse_aware(row["at"], label=f"transition {index}.at")
    if not isinstance(row["by"], str) or not row["by"].strip():
        raise AssessmentStateError(f"transition {index}.by must be nonempty")
    if not isinstance(row["note"], str):
        raise AssessmentStateError(f"transition {index}.note must be text")
    return row


def _history_has_go(transitions: list[dict]) -> bool:
    """Recognize current GO rows and the exact first-release v1 shape."""
    relevance_indexes = [
        index for index, row in enumerate(transitions)
        if row["state"] == "RELEVANCE"
    ]
    start = relevance_indexes[-1] if relevance_indexes else 0
    for index in range(start, len(transitions)):
        row = transitions[index]
        if row["note"] == "taxonomy GO recorded":
            return True
        if (index > 0 and row["state"] == "COMPOSING"
                and transitions[index - 1]["state"] ==
                "AWAITING_TAXONOMY_GO"
                and row["note"] in {
                    "taxonomy GO accepted; C1 composer started",
                    "operator restart requested from COMPOSING",
                }):
            return True
    return False


def _restart_mode(row: dict) -> str | None:
    """Recognize only canonical operator restart transitions."""
    state = row["state"]
    note = row["note"]
    if state in RESTARTABLE_STATES \
            and note == f"operator restart requested from {state}":
        return "assessment"
    if state == "GATING_PRESS" \
            and note == "operator refresh restart requested from GATING_PRESS":
        return "refresh"
    return None


def _restart_target_reached(transitions: list[dict], target: str) -> bool:
    """Return whether ``target`` is reachable without skipping this epoch.

    The newest operator restart starts a new execution epoch.  A later target
    must have been reached in that epoch; an equal/earlier target is a safe
    rewind.  Before the first restart, ordinary lifetime reachability applies.
    """
    restart_indexes = [
        index for index, row in enumerate(transitions)
        if _restart_mode(row) is not None
    ]
    if not restart_indexes:
        return any(row["state"] == target for row in transitions)
    epoch_start = restart_indexes[-1]
    if any(row["state"] == target for row in transitions[epoch_start:]):
        return True
    epoch_target = transitions[epoch_start]["state"]
    return _RESTART_RANK[target] <= _RESTART_RANK[epoch_target]


def _validate_history(transitions: list[dict], *, mode: str) -> None:
    if transitions[0]["state"] != "INTAKE_DONE":
        raise AssessmentStateError("assessment history must start at INTAKE_DONE")
    normal_edges = {
        "INTAKE_DONE": {"SWEEPING"},
        "SWEEPING": {"RELEVANCE", "FAILED"},
        "RELEVANCE": {"AWAITING_TAXONOMY_GO", "FAILED"},
        "AWAITING_TAXONOMY_GO": {
            "AWAITING_TAXONOMY_GO", "COMPOSING"},
        "COMPOSING": {"REPULLING", "FAILED"},
        "REPULLING": {"GATING_PRESS", "FAILED"},
        "GATING_PRESS": {"DRAFT_READY", "FAILED"},
        "DRAFT_READY": set(),
        "FAILED": set(),
    }
    previous_time = _parse_aware(
        transitions[0]["at"], label="transition 0.at")
    for index, row in enumerate(transitions[1:], start=1):
        current_time = _parse_aware(row["at"], label=f"transition {index}.at")
        if current_time < previous_time:
            raise AssessmentStateError(
                f"transition {index}.at precedes the prior transition")
        previous = transitions[index - 1]["state"]
        current = row["state"]
        restart_mode = _restart_mode(row)
        restart = restart_mode is not None
        reserved_restart_note = (
            row["note"].startswith("operator restart requested from ")
            or row["note"].startswith(
                "operator refresh restart requested from ")
        )
        if reserved_restart_note and not restart:
            raise AssessmentStateError(
                f"transition {index} has a malformed restart note")
        legacy_pause_restart = (
            previous == "AWAITING_TAXONOMY_GO"
            and current == "COMPOSING"
            and row["note"] == "operator restart requested from COMPOSING"
            and transitions[index - 1]["note"] != "taxonomy GO recorded")
        first_current_compose = (
            previous == "AWAITING_TAXONOMY_GO"
            and current == "COMPOSING"
            and transitions[index - 1]["note"] == "taxonomy GO recorded")
        refresh_restart = restart_mode == "refresh"
        refresh = (current == "GATING_PRESS"
                   and row["note"] == "C3 refresh/press orchestration started")
        if restart:
            if current not in RESTARTABLE_STATES:
                raise AssessmentStateError(
                    f"transition {index} has an invalid restart target")
            if (not legacy_pause_restart and not first_current_compose
                    and not _restart_target_reached(
                        transitions[:index], current)):
                raise AssessmentStateError(
                    f"transition {index} restarts a stage never reached in "
                    "the current relevance cycle")
            if refresh_restart:
                if (current != "GATING_PRESS"
                        or not any(prior["note"] ==
                                   "C3 refresh/press orchestration started"
                                   for prior in transitions[:index])):
                    raise AssessmentStateError(
                        f"transition {index} has an invalid refresh restart")
            elif (not legacy_pause_restart
                  and current in ("COMPOSING", "REPULLING", "GATING_PRESS")
                  and not _history_has_go(transitions[:index])):
                raise AssessmentStateError(
                    f"transition {index} restarts past an unrecorded GO")
        elif refresh:
            pass
        elif current not in normal_edges[previous]:
            raise AssessmentStateError(
                f"illegal assessment transition {previous} -> {current}")
        if (previous == "AWAITING_TAXONOMY_GO"
                and current == "AWAITING_TAXONOMY_GO"
                and row["note"] not in {
                    "taxonomy GO recorded",
                    "operator restart requested from AWAITING_TAXONOMY_GO",
                }):
            raise AssessmentStateError(
                "repeated taxonomy pause must record a GO")
        if (previous == "AWAITING_TAXONOMY_GO" and current == "COMPOSING"
                and not (
                    (transitions[index - 1]["note"] == "taxonomy GO recorded"
                     and (row["note"].startswith("taxonomy GO accepted ")
                          or row["note"] ==
                          "operator restart requested from COMPOSING"))
                    or legacy_pause_restart
                    or row["note"] ==
                    "taxonomy GO accepted; C1 composer started")):
            raise AssessmentStateError(
                "composition transition must name the accepted taxonomy GO")
        previous_time = current_time

    inferred_mode = "assessment"
    for row in transitions:
        restart_mode = _restart_mode(row)
        if row["note"] == "C3 refresh/press orchestration started" or \
                restart_mode == "refresh":
            inferred_mode = "refresh"
        elif restart_mode == "assessment":
            inferred_mode = "assessment"
    if mode != inferred_mode:
        raise AssessmentStateError(
            f"assessment mode {mode!r} does not match transition history "
            f"mode {inferred_mode!r}")


def validate_state(payload: object, *, expected_slug: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise AssessmentStateError("assessment state root must be an object")
    if payload.get("version") != SCHEMA_VERSION:
        raise AssessmentStateError(
            f"unsupported assessment state version {payload.get('version')!r}")
    slug = payload.get("slug")
    if not isinstance(slug, str) or slug != canonical_slug(slug):
        raise AssessmentStateError("assessment state has an invalid slug")
    if expected_slug is not None and slug != canonical_slug(expected_slug):
        raise AssessmentStateError(
            f"assessment state belongs to {slug!r}, not {expected_slug!r}")
    state = payload.get("state")
    if state not in STATES:
        raise AssessmentStateError(f"unknown assessment state {state!r}")
    if payload.get("mode") not in ("assessment", "refresh"):
        raise AssessmentStateError("assessment state mode must be assessment or refresh")
    transitions = payload.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise AssessmentStateError("assessment state needs at least one transition")
    for index, row in enumerate(transitions):
        _validate_transition(row, index)
    _validate_history(transitions, mode=payload["mode"])
    if transitions[-1]["state"] != state:
        raise AssessmentStateError("current state does not match the last transition")
    if (payload.get("mode") == "refresh"
            and not any(row["note"] == "C3 refresh/press orchestration started"
                        for row in transitions)):
        raise AssessmentStateError(
            "refresh mode has no C3 refresh/press transition")
    failure = payload.get("failure")
    if state == "FAILED":
        if not isinstance(failure, dict):
            raise AssessmentStateError("FAILED state requires failure details")
        if set(failure) != {"stage", "reason", "fix_surface"}:
            raise AssessmentStateError(
                "failure must contain stage, reason, fix_surface")
        if failure["stage"] not in RUNNER_STATES:
            raise AssessmentStateError("failure names an unknown runner stage")
        if (len(transitions) < 2
                or transitions[-2]["state"] != failure["stage"]):
            raise AssessmentStateError(
                "failure.stage does not match the failed runner transition")
        for key in ("reason", "fix_surface"):
            if not isinstance(failure[key], str) or not failure[key].strip():
                raise AssessmentStateError(f"failure.{key} must be nonempty")
    elif failure is not None:
        raise AssessmentStateError("only FAILED state may carry failure details")
    return payload


def load_state(
    slug: str, *, state_dir: Path | str | None = None,
) -> Optional[dict]:
    path = state_path(slug, state_dir=state_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise AssessmentStateError(f"cannot read {path}: {exc}") from exc
    return validate_state(payload, expected_slug=slug)


def _canonical_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n") \
        .encode("utf-8")


def _write_json_if_changed(path: Path, payload: dict) -> bool:
    data = _canonical_bytes(payload)
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    atomic_write_json(path, payload)
    return True


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def client_lock(slug: str, *, state_dir: Path | str | None = None) -> Iterator[None]:
    """Serialize one client's complete chain across threads and processes."""
    root = Path(state_dir or default_state_dir())
    lock_path = root / ".locks" / f"{canonical_slug(slug)}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(lock_path):
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _latest_go_invalidation(state: dict) -> int | None:
    invalidating = {
        "operator restart requested from INTAKE_DONE",
        "operator restart requested from SWEEPING",
        "operator restart requested from RELEVANCE",
        "operator restart requested from AWAITING_TAXONOMY_GO",
    }
    indexes = [
        index for index, row in enumerate(state["transitions"])
        if row["note"] in invalidating
    ]
    return indexes[-1] if indexes else None


def _marker_is_stale(state: dict, marker: dict) -> bool:
    invalidated_at = _latest_go_invalidation(state)
    if invalidated_at is None:
        return False
    matches = [
        index for index, row in enumerate(state["transitions"])
        if row["state"] == "AWAITING_TAXONOMY_GO"
        and row["at"] == marker["at"] and row["by"] == marker["by"]
        and row["note"] == "taxonomy GO recorded"
    ]
    if matches:
        return matches[-1] <= invalidated_at
    marker_time = _parse_aware(marker["at"], label="GO marker at")
    restart_time = _parse_aware(
        state["transitions"][invalidated_at]["at"],
        label="GO-invalidating restart at",
    )
    return marker_time <= restart_time


def _load_go(
    slug: str, *, state_dir: Path | str, state: dict | None = None,
) -> Optional[dict]:
    path = go_path(slug, state_dir=state_dir)
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise AssessmentStateError(f"cannot read GO marker {path}: {exc}") from exc
    if not isinstance(marker, dict) or set(marker) != {"by", "at"}:
        raise AssessmentStateError("GO marker must contain exactly by and at")
    if not isinstance(marker["by"], str) or not marker["by"].strip():
        raise AssessmentStateError("GO marker by must be nonempty")
    _parse_aware(marker["at"], label="GO marker at")
    if state is not None and _marker_is_stale(state, marker):
        return None
    if state is not None:
        recorded = _history_has_go(state["transitions"]) or any(
            row["state"] == "AWAITING_TAXONOMY_GO"
            and row["at"] == marker["at"] and row["by"] == marker["by"]
            and row["note"] == "taxonomy GO recorded"
            for row in state["transitions"]
        )
        if (not recorded
                and _parse_aware(marker["at"], label="GO marker at")
                < _parse_aware(
                    state["transitions"][-1]["at"],
                    label="last transition at")):
            # Recover a first-release crash that persisted an invalid marker
            # before its state transition.  A new GO may overwrite it.
            return None
    return marker


def _validate_go_time(state: dict, marker: dict) -> None:
    marker_time = _parse_aware(marker["at"], label="GO marker at")
    tail_time = _parse_aware(
        state["transitions"][-1]["at"], label="last transition at")
    if marker_time < tail_time:
        raise PauseRequired("GO marker predates the taxonomy review pause")


def _record_go_transition(state: dict, marker: dict, *, state_dir: Path) -> None:
    invalidated_at = _latest_go_invalidation(state)
    active_rows = state["transitions"][
        invalidated_at + 1 if invalidated_at is not None else 0:]
    if any(row["state"] == "AWAITING_TAXONOMY_GO"
           and row["at"] == marker["at"] and row["by"] == marker["by"]
           and row["note"] == "taxonomy GO recorded"
           for row in active_rows):
        return
    _validate_go_time(state, marker)
    state["transitions"].append({
        "state": "AWAITING_TAXONOMY_GO",
        "at": marker["at"],
        "by": marker["by"],
        "note": "taxonomy GO recorded",
    })
    state["state"] = "AWAITING_TAXONOMY_GO"
    state["failure"] = None
    validate_state(state, expected_slug=state["slug"])
    _write_json_if_changed(
        state_path(state["slug"], state_dir=state_dir), state)


def write_go(
    slug: str,
    *,
    by: str,
    state_dir: Path | str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Write the one GO marker, idempotently, only after the pause exists."""
    if not isinstance(by, str) or not by.strip():
        raise ValueError("GO requires a nonempty by value")
    root = Path(state_dir or default_state_dir())
    with client_lock(slug, state_dir=root):
        state = load_state(slug, state_dir=root)
        if state is None:
            raise PauseRequired("GO refused: assessment has not reached taxonomy review")
        existing = _load_go(slug, state_dir=root, state=state)
        if existing is not None:
            if state["state"] == "AWAITING_TAXONOMY_GO":
                _record_go_transition(state, existing, state_dir=root)
            return existing
        if state["state"] != "AWAITING_TAXONOMY_GO":
            raise PauseRequired(
                f"GO refused while assessment is {state['state']}; "
                "taxonomy review has not been reached")
        marker = {
            "by": by.strip(),
            "at": _aware_iso((clock or (lambda: datetime.now(timezone.utc)))()),
        }
        _validate_go_time(state, marker)
        _write_json_if_changed(go_path(slug, state_dir=root), marker)
        _record_go_transition(state, marker, state_dir=root)
        return marker


def assessment_status(
    slug: str, *, state_dir: Path | str | None = None,
) -> dict:
    """Read-only chain status seam for Control Room cards and the ticker."""
    canonical = canonical_slug(slug)
    state = load_state(canonical, state_dir=state_dir)
    if state is None:
        return {"slug": canonical, "state": None, "since": None,
                "alert_line": None}
    current = state["state"]
    alert = None
    if current == "AWAITING_TAXONOMY_GO":
        alert = f"{canonical} awaiting your keyword review"
    elif current == "FAILED":
        failure = state["failure"]
        alert = (f"{canonical} assessment failed at {failure['stage']}: "
                 f"{failure['reason']}")[:300]
    return {
        "slug": canonical,
        "state": current,
        "since": state["transitions"][-1]["at"],
        "alert_line": alert,
    }


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sweep_basis_sha(path: Path) -> str:
    """Hash every sweep field except the lanes owned by award re-pull."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), dict):
        raise ValueError("sweep must contain an object-valued results field")
    stable = dict(payload)
    stable["results"] = {
        name: value for name, value in payload["results"].items()
        if name not in {"award_repulls", "agency_docs"}
    }
    encoded = json.dumps(
        stable, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _latest_stage_sha(record: dict, label: str) -> str | None:
    pattern = re.compile(rf"(?:^|; ){re.escape(label)}-sha256 ([0-9a-f]{{64}})$")
    relevance_indexes = [
        index for index, row in enumerate(record["transitions"])
        if row["state"] == "RELEVANCE"
    ]
    start = relevance_indexes[-1] if relevance_indexes else 0
    for row in reversed(record["transitions"][start:]):
        match = pattern.search(row["note"])
        if match:
            return match.group(1)
    return None


def _relevance_input_binding(root: Path, slug: str) -> dict[str, dict]:
    paths = {
        "taxonomy": root / "clients" / slug / "capability_taxonomy.json",
        "profile": root / "clients" / slug / "profile.json",
        "engagement_scope": root / "clients" / slug / "engagement_scope.json",
        "signal_board_content": (
            root / "clients" / slug / "signal_board_content.json"),
        "scope_presets": root / "data" / "reference" / "scope_presets.json",
    }
    return {
        name: {
            "path": str(path),
            "sha256": _file_sha(path) if path.exists() else None,
        }
        for name, path in paths.items()
    }


REQUIRED_CALIBRATION_FIELDS = {"client", "record_ref", "engine_score",
                               "disagreement", "scope_basis"}

#: The ONE compose-trail header grammar, shared by the C1 producer
#: (agents.reports.composer.compose_trail_header) and this consumer.
COMPOSE_TRAIL_HEADER_RE = re.compile(
    r"# INTERNAL compose trail · (?P<client>.+) · never leaves the shop")

#: The ONE pair-binding grammar: the trail's final line binds the exact
#: published content generation (C1 producer:
#: agents.reports.composer.pair_binding_line). A trail whose binding does
#: not match the on-disk content is a mixed pair and is rejected.
COMPOSE_PAIR_BINDING_RE = re.compile(
    r"pair content sha256 (?P<sha>[0-9a-f]{64})")

#: The complete named-failure stage vocabulary the C3 refresh runner
#: owns (run_refresh_press.py). A single
#: well-formed [refresh:failure] marker with any EXACT stage in this
#: allowlist at a nonzero exit is preserved verbatim; unknown stages
#: stay refused.
C3_FAILURE_STAGES = (
    "re-sweep",
    "relevance",
    "compose-refresh",
    "award re-pull",
    "candidate-review-watch",
    "signal-board-certification",
)


def candidate_review_watch_enabled(root: Path | str) -> bool:
    """Whether this checkout carries the additive Candidate Review contract.

    Old isolated C1/C3 contract fixtures intentionally omit the package and
    continue to exercise their historical seam.  A checkout that includes the
    package must include and successfully run its production CLI.
    """

    return (Path(root) / "agents" / "candidate_review_v1").is_dir()


def validate_candidate_review_watch_marker(
    root: Path | str,
    slug: str,
    stdout: str,
) -> Path:
    """Validate the one watch marker against the promoted current receipt."""

    markers = re.findall(
        r"(?m)^\[out:candidate-review-watch\] (.+)$",
        stdout,
    )
    if len(markers) != 1:
        raise ValueError(
            "Candidate Review watch must emit exactly one "
            "[out:candidate-review-watch] marker"
        )
    root_path = Path(root).resolve()
    state_root = (
        root_path / "data" / "state" / "candidate_review_v1"
    ).resolve()
    marked = Path(markers[0].strip())
    receipt_path = (
        marked if marked.is_absolute() else root_path / marked
    ).resolve()
    try:
        inside = os.path.commonpath([receipt_path, state_root]) == str(state_root)
    except ValueError:
        inside = False
    if not inside or receipt_path.name != "generation.receipt.json":
        raise ValueError(
            "Candidate Review watch marker is outside its generation state root"
        )

    from agents.candidate_review_v1.persistence import (
        GenerationReceipt,
        load_current_generation,
    )

    receipt = GenerationReceipt.model_validate_json(
        receipt_path.read_text(encoding="utf-8")
    )
    if canonical_slug(receipt.binding.client_name) != canonical_slug(slug):
        raise ValueError("Candidate Review watch receipt belongs to another client")
    current = load_current_generation(
        receipt.binding.client_id,
        state_root=state_root,
        expected_binding=receipt.binding,
        expected_basis_sha256=receipt.basis_sha256,
    )
    if current is None:
        raise ValueError("Candidate Review watch marker has no current generation")
    expected = (
        state_root
        / receipt.binding.client_id
        / current.pointer.receipt_path
    ).resolve()
    if expected != receipt_path:
        raise ValueError(
            "Candidate Review watch marker does not name the promoted receipt"
        )
    return receipt_path


def validate_candidate_review_document_marker(
    root: Path | str,
    slug: str,
    stdout: str,
) -> Path:
    """Validate the one document marker against its bound QA certificate.

    Twin of ``validate_candidate_review_watch_marker``: the publication step
    emits exactly one marker naming the certificate that binds the composed
    document to the rendered bytes.
    """

    markers = re.findall(
        r"(?m)^\[out:candidate-review-document\] (.+)$",
        stdout,
    )
    if len(markers) != 1:
        raise ValueError(
            "Candidate Review must emit exactly one "
            "[out:candidate-review-document] marker"
        )
    root_path = Path(root).resolve()
    state_root = (
        root_path / "data" / "state" / "candidate_review_v1"
    ).resolve()
    marked = Path(markers[0].strip())
    certificate_path = (
        marked if marked.is_absolute() else root_path / marked
    ).resolve()
    try:
        inside = os.path.commonpath(
            [certificate_path, state_root]) == str(state_root)
    except ValueError:
        inside = False
    if not inside or not certificate_path.name.endswith(
        ".candidate_review.qa.json"
    ):
        raise ValueError(
            "Candidate Review document marker is outside its state root"
        )

    from agents.candidate_review_v1.document_release import (
        validate_certificate_schema,
    )

    payload = json.loads(certificate_path.read_text(encoding="utf-8"))
    validate_certificate_schema(payload)
    if canonical_slug(payload["client_name"]) != canonical_slug(slug):
        raise ValueError(
            "Candidate Review document certificate belongs to another client"
        )
    if certificate_path.name != (
        f"{payload['client_id']}.candidate_review.qa.json"
    ):
        raise ValueError(
            "Candidate Review document certificate does not match its client"
        )
    return certificate_path


def _named_candidate_review_failure(stderr: str) -> dict | None:
    """Parse the watch CLI's one exact safe failure object."""

    payloads = []
    for line in stderr.splitlines():
        try:
            parsed = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(parsed, dict)
            and set(parsed) == {"stage", "reason", "fix_surface"}
            and parsed.get("stage") == "candidate-review-watch"
            and all(
                isinstance(parsed.get(key), str) and parsed[key]
                for key in ("stage", "reason", "fix_surface")
            )
        ):
            payloads.append(parsed)
    return payloads[0] if len(payloads) == 1 else None


def validate_calibration_csv(path: Path, client: str) -> int:
    """Row count after proving the production headers and that every row
    belongs to this client. Shared by the chain's relevance runner, the
    C3 refresh relevance step, and C1's compose-time revalidation; one
    contract, no second format."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if not REQUIRED_CALIBRATION_FIELDS.issubset(
                set(reader.fieldnames or [])):
            raise ValueError("calibration CSV header is incomplete")
        if any(row.get("client") != client for row in rows):
            raise ValueError("calibration CSV contains another client")
    return len(rows)


def write_relevance_receipt(
    root: Path | str,
    slug: str,
    client: str,
    *,
    command: list[str],
    sweep_path: Path | str,
    started_at: str,
    completed_at: str,
    inputs: dict,
    calibration_path: Path | str,
    summary: str,
) -> Path:
    """Mint the relevance run receipt exactly as relevance_receipt_valid
    consumes it. Validates the calibration CSV first; raises ValueError,
    OSError, or csv.Error on any defect so callers fail named."""
    row_count = validate_calibration_csv(Path(calibration_path), client)
    receipt = (Path(root) / "data" / "state" / "relevance"
               / f"{canonical_slug(slug)}.run.json")
    sweep = Path(sweep_path)
    payload = {
        "version": 1,
        "slug": canonical_slug(slug),
        "client_name": client,
        "started_at": started_at,
        "completed_at": completed_at,
        "command": command,
        "sweep": {
            "path": str(sweep),
            "sha256": _file_sha(sweep),
            "stable_sha256": _sweep_basis_sha(sweep),
        },
        "inputs": inputs,
        "calibration": {
            "path": str(calibration_path), "sha256": _file_sha(
                Path(calibration_path)),
            "row_count": row_count,
        },
        "summary": summary,
    }
    _write_json_if_changed(receipt, payload)
    return receipt


def relevance_receipt_valid(
    slug: str,
    *,
    root: Path | str = _ROOT,
    allow_board_content_change: bool = False,
    expected_board_sha256: str | None = None,
    allow_sweep_change: bool = False,
    expected_sweep_sha256: str | None = None,
) -> bool:
    """Prove a client-specific relevance run still binds current evidence.

    Downstream adapters may allow only mutations their sanctioned order owns:
    compose may replace board content, and award re-pull may replace the sweep.
    Taxonomy, profile, scope, calibration, and client identity remain exact.
    """
    canonical = canonical_slug(slug)
    root_path = Path(root)
    receipt_path = (root_path / "data" / "state" / "relevance" /
                    f"{canonical}.run.json")
    calibration_path = (root_path / "data" / "state" / "relevance" /
                        f"{canonical}.calibration.csv")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        calibration = receipt["calibration"]
        sweep = receipt["sweep"]
        inputs = receipt["inputs"]
        client_name = receipt["client_name"]
        from tools.capability import ClientProfile

        profile_path = root_path / "clients" / canonical / "profile.json"
        profile = ClientProfile.model_validate_json(
            profile_path.read_text(encoding="utf-8"))
        current_inputs = _relevance_input_binding(root_path, canonical)
        bound_inputs = inputs
        if ((allow_board_content_change or expected_board_sha256 is not None)
                and isinstance(inputs, dict)):
            bound_inputs = {
                name: value for name, value in inputs.items()
                if name != "signal_board_content"
            }
            current_inputs = {
                name: value for name, value in current_inputs.items()
                if name != "signal_board_content"
            }
        # command and input-binding paths compare through realpath, so
        # equivalent filesystem aliases of one artifact (macOS /var vs
        # /private/var) never invalidate a truthful receipt; the sha256
        # bindings stay exact
        bound_command = receipt.get("command")
        command_valid = (
            isinstance(bound_command, list)
            and len(bound_command) == 6
            and bound_command[:5] == [
                "-m", "tools.relevance.calibrate", "--client", client_name,
                "--out",
            ]
            and isinstance(bound_command[5], str)
            and os.path.realpath(bound_command[5])
            == os.path.realpath(calibration_path))

        def _aliased_binding(binding: dict) -> dict:
            return {
                name: {
                    "path": os.path.realpath(str(value.get("path") or "")),
                    "sha256": value.get("sha256"),
                }
                for name, value in binding.items()
                if isinstance(value, dict)
            }

        if (receipt.get("version") != 1 or receipt.get("slug") != canonical
                or not isinstance(client_name, str) or not client_name
                or not profile.is_populated()
                or profile.client_name != client_name
                or canonical_slug(profile.client_name) != canonical
                or not str(receipt.get("summary") or "").startswith(
                    f"[calibrate] {client_name}:")
                or not command_valid
                or not isinstance(inputs, dict)
                or set(inputs) != {
                    "taxonomy", "profile", "engagement_scope",
                    "signal_board_content", "scope_presets",
                }
                or not all(isinstance(value, dict)
                           for value in bound_inputs.values())
                or _aliased_binding(bound_inputs)
                != _aliased_binding(current_inputs)):
            return False
        if expected_board_sha256 is not None:
            board = _relevance_input_binding(root_path, canonical)[
                "signal_board_content"]
            if board["sha256"] != expected_board_sha256:
                return False
        started = _parse_aware(
            receipt.get("started_at"), label="relevance started_at")
        completed = _parse_aware(
            receipt.get("completed_at"), label="relevance completed_at")
        if completed < started:
            return False
        if (os.path.realpath(str(calibration.get("path") or ""))
                != os.path.realpath(calibration_path)):
            return False
        row_count = calibration.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int) \
                or row_count < 0:
            return False
        if calibration.get("sha256") != _file_sha(calibration_path):
            return False
        with calibration_path.open(encoding="utf-8", newline="") as handle:
            if sum(1 for _ in csv.DictReader(handle)) != row_count:
                return False
        from agents.review import sweep_artifact_path

        current_sweep = Path(sweep_artifact_path(
            client_name,
            review_dir=str(root_path / "data" / "review"),
        ))
        if (os.path.realpath(str(sweep.get("path") or ""))
                != os.path.realpath(current_sweep)):
            return False
        current_sweep_sha = _file_sha(current_sweep)
        if expected_sweep_sha256 is not None:
            return current_sweep_sha == expected_sweep_sha256
        if allow_sweep_change:
            stable_sha = sweep.get("stable_sha256")
            if not isinstance(stable_sha, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", stable_sha):
                # First-release receipts predate the projection.  They may
                # resume only while byte-identical; otherwise rerun relevance.
                return sweep.get("sha256") == current_sweep_sha
            return _sweep_basis_sha(current_sweep) == stable_sha
        return sweep.get("sha256") == current_sweep_sha
    except (AssessmentStateError, KeyError, OSError, TypeError, ValueError,
            json.JSONDecodeError, csv.Error):
        return False


@dataclass(frozen=True)
class _FileToken:
    exists: bool
    mtime_ns: int = 0
    size: int = 0
    sha256: str = ""


def _file_token(path: Path) -> _FileToken:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return _FileToken(False)
    return _FileToken(True, stat.st_mtime_ns, stat.st_size, _file_sha(path))


def _fresh(path: Path, before: _FileToken) -> bool:
    after = _file_token(path)
    return after.exists and after != before


def _last_error(stdout: str, stderr: str, returncode: int) -> str:
    lines = [line.strip() for line in (stderr + "\n" + stdout).splitlines()
             if line.strip()]
    return (lines[-1] if lines else f"runner exited {returncode}")[:500]


def _named_c1_failure(stderr: str) -> dict | None:
    """C1's single named JSON failure, parsed structurally from the
    composer's stderr stream. None unless exactly one schema-valid
    payload ({stage, reason, fix_surface}, all nonempty strings) is
    present; malformed or duplicate payloads never pass as named."""
    payloads = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (isinstance(parsed, dict)
                and set(parsed) == {"stage", "reason", "fix_surface"}
                and all(isinstance(parsed[key], str) and parsed[key]
                        for key in ("stage", "reason", "fix_surface"))):
            payloads.append(parsed)
    return payloads[0] if len(payloads) == 1 else None


def compose_pair_tokens(
    root: Path | str, slug: str,
) -> tuple[_FileToken, dict[Path, _FileToken]]:
    """Pre-invocation tokens for one C1 compose: the board-content token
    plus every markdown token under the pair's approved roots. Capture
    BEFORE the composer runs; validate_compose_pair consumes them."""
    root_path = Path(root)
    content = root_path / "clients" / slug / "signal_board_content.json"
    roots = (
        root_path / "data" / "state",
        root_path / "data" / "reports",
        root_path / "clients" / slug,
    )
    trails = {
        path.resolve(): _file_token(path)
        for base in roots if base.exists()
        for path in base.rglob("*.md") if path.is_file()
    }
    return _file_token(content), trails


def validate_compose_pair(
    root: Path | str,
    slug: str,
    client: str,
    stdout: str,
    *,
    content_before: _FileToken,
    trails_before: dict[Path, _FileToken],
) -> Path:
    """THE sanctioned validation of a C1 compose invocation that exited
    zero. One owner for the marker, header, and pair-binding grammars;
    the C1 assessment adapter and the C3 refresh runner both consume it
    and neither duplicates it. Requires: exactly one
    [out:compose-trail] marker; exactly one fresh, client-bound trail
    with the exact header and a pair binding line; fresh board content
    that loads for the exact profile-owned client in machine
    composition mode; and a trail binding matching the published
    content bytes. Raises ValueError naming the first defect; returns
    the trail path."""
    root_path = Path(root)
    roots = tuple(path.resolve() for path in (
        root_path / "data" / "state",
        root_path / "data" / "reports",
        root_path / "clients" / slug,
    ))
    markers = re.findall(r"(?m)^\[out:compose-trail\] (.+)$", stdout)
    if len(markers) != 1:
        raise ValueError(
            "composer must emit exactly one [out:compose-trail] marker")
    marked = Path(markers[0].strip())
    path = (marked if marked.is_absolute() else root_path / marked).resolve()
    try:
        inside = any(os.path.commonpath([path, base]) == str(base)
                     for base in roots)
    except ValueError:
        inside = False
    if not inside:
        raise ValueError(f"compose trail escaped approved roots: {path}")
    relative = path.relative_to(root_path.resolve()).as_posix().lower()
    client_root = (root_path / "clients" / slug).resolve()
    client_bound = (
        os.path.commonpath([path, client_root]) == str(client_root)
        or path.name.lower().startswith(f"{slug.lower()}.")
    )
    if not client_bound or "compose" not in relative:
        raise ValueError(
            f"compose trail is not bound to client {slug!r}: {path}")
    if not _fresh(path, trails_before.get(path, _FileToken(False))):
        raise ValueError(
            "composer must write exactly one fresh INTERNAL compose trail")
    trail_text = path.read_text(encoding="utf-8")
    nonempty = [line.strip() for line in trail_text.splitlines()
                if line.strip()]
    if not nonempty:
        raise ValueError("fresh INTERNAL compose trail is empty")
    header = COMPOSE_TRAIL_HEADER_RE.fullmatch(nonempty[0])
    if (header is None or header.group("client") != client
            or len(nonempty) < 2):
        raise ValueError(
            "fresh INTERNAL compose trail lacks its exact client header "
            "or audit detail")
    binding = COMPOSE_PAIR_BINDING_RE.fullmatch(nonempty[-1])
    if binding is None:
        raise ValueError(
            "fresh INTERNAL compose trail lacks its pair binding line")
    content_artifact = (root_path / "clients" / slug /
                        "signal_board_content.json")
    if not _fresh(content_artifact, content_before):
        raise ValueError("no fresh board content beside the compose trail")
    from agents.reports.board_content import SignalBoardContent

    validated = SignalBoardContent.model_validate_json(
        content_artifact.read_text(encoding="utf-8"))
    if validated.client_name != client:
        raise ValueError(
            f"content client {validated.client_name!r} does not match "
            f"{client!r}")
    if getattr(validated, "composition_mode", "operator") != "machine":
        raise ValueError(
            "machine-produced content must declare composition_mode machine")
    if _file_sha(content_artifact) != binding.group("sha"):
        raise ValueError(
            "compose trail is not bound to the published content "
            "generation; the pair on disk is mixed")
    return path


def validate_current_compose_pair(
    root: Path | str,
    slug: str,
    client: str,
    *,
    return_trail_text: bool = False,
):
    """Validate the currently stored machine content/trail generation.

    Unlike ``validate_compose_pair`` this consumer does not require fresh
    writes from a just-completed subprocess.  It is the press-time tamper
    check: exact client header, machine origin, final pair binding, and the
    SHA of the bytes about to render must still agree. Returns the validated
    ``SignalBoardContent`` parsed from those exact bytes, so the press never
    validates one generation and renders a separately re-read generation.
    A press may also request the exact trail text from the same read so its
    semantic evidence gates cannot race a second filesystem read.
    """
    root_path = Path(root)
    content_artifact = (
        root_path / "clients" / slug / "signal_board_content.json")
    trail_path = root_path / "clients" / slug / "compose_trail.md"
    try:
        trail_text = trail_path.read_text(encoding="utf-8")
        content_bytes = content_artifact.read_bytes()
        content_text = content_bytes.decode("utf-8")
    except OSError as exc:
        raise ValueError(
            f"stored machine compose pair is incomplete: {exc}") from exc
    nonempty = [line.strip() for line in trail_text.splitlines()
                if line.strip()]
    if not nonempty:
        raise ValueError("stored INTERNAL compose trail is empty")
    header = COMPOSE_TRAIL_HEADER_RE.fullmatch(nonempty[0])
    if header is None or header.group("client") != client:
        raise ValueError(
            "stored INTERNAL compose trail lacks its exact client header")
    binding = COMPOSE_PAIR_BINDING_RE.fullmatch(nonempty[-1])
    if binding is None:
        raise ValueError(
            "stored INTERNAL compose trail lacks its pair binding line")
    from agents.reports.board_content import SignalBoardContent

    validated = SignalBoardContent.model_validate_json(content_text)
    if validated.client_name != client:
        raise ValueError(
            f"content client {validated.client_name!r} does not match "
            f"{client!r}")
    if getattr(validated, "composition_mode", "operator") != "machine":
        raise ValueError(
            "stored compose pair does not contain machine-produced content")
    if hashlib.sha256(content_bytes).hexdigest() != binding.group("sha"):
        raise ValueError(
            "compose trail is not bound to the stored content generation; "
            "the pair was changed after composition")
    if return_trail_text:
        return validated, trail_text
    return validated


class SubprocessRunnerAdapter:
    """Default adapters for the existing CLI contracts."""

    def __init__(
        self,
        *,
        root: Path | str = _ROOT,
        python: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root)
        self.python = python or sys.executable
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _client_name(self, slug: str) -> str:
        return self._require_client_name(slug)

    def _require_client_name(self, slug: str) -> str:
        """Return the profile-owned display identity; never guess for C1/C3."""
        from tools.capability import ClientProfile

        path = self.root / "clients" / slug / "profile.json"
        profile = ClientProfile.model_validate_json(
            path.read_text(encoding="utf-8"))
        if not profile.is_populated():
            raise ValueError(f"profile is not populated: {path}")
        if canonical_slug(profile.client_name) != canonical_slug(slug):
            raise ValueError(
                f"profile client {profile.client_name!r} does not match "
                f"slug {slug!r}")
        return profile.client_name

    def _presentation_name(self, slug: str, client: str) -> str:
        """Return the current profile-owned presentation spelling."""
        from tools.capability import ClientProfile, client_display_name

        path = self.root / "clients" / slug / "profile.json"
        profile = ClientProfile.model_validate_json(
            path.read_text(encoding="utf-8"))
        if profile.client_name != client:
            raise ValueError(
                f"profile client {profile.client_name!r} does not match "
                f"refresh client {client!r}")
        return client_display_name(client, profile=profile)

    def _invoke(
        self, argv: list[str], *, cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # The assessment process may hold the operator's session-only OpenAI
        # collaborator key.  Research, compose, watch, and renderer children
        # do not need that credential and must never inherit it.  The
        # collaborator itself runs at the parent orchestration seam.
        child_env = os.environ.copy()
        child_env.pop("OPENAI_API_KEY", None)
        return subprocess.run(
            argv,
            cwd=cwd or self.root,
            text=True,
            capture_output=True,
            check=False,
            env=child_env,
        )

    def _relevance_overlay(self, sweep: Path) -> tempfile.TemporaryDirectory:
        """Expose exactly the gate-designated sweep to the existing CLI.

        The calibrator itself stays unchanged.  Its repository-relative reads
        resolve through symlinks into this worktree, while ``data/cleaned``
        contains only the selected sweep for this invocation.
        """
        parent = self.root / "data" / "state" / "assessment"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(
            prefix=".relevance-", dir=parent)
        overlay = Path(temporary.name)
        try:
            for name in ("tools", "agents", "clients"):
                os.symlink(
                    self.root / name, overlay / name,
                    target_is_directory=True)
            overlay_data = overlay / "data"
            overlay_data.mkdir()
            cleaned = overlay_data / "cleaned"
            cleaned.mkdir()
            os.symlink(sweep, cleaned / sweep.name)
            source_data = self.root / "data"
            for child in source_data.iterdir():
                if child.name == "cleaned":
                    continue
                os.symlink(
                    child, overlay_data / child.name,
                    target_is_directory=child.is_dir())
        except Exception:
            temporary.cleanup()
            raise
        return temporary

    def _failure(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        fix: str,
        reason: str | None = None,
    ) -> RunnerOutcome:
        return RunnerOutcome(
            False,
            proc.returncode,
            "runner failed",
            reason=reason or _last_error(proc.stdout, proc.stderr, proc.returncode),
            fix_surface=fix,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def run(self, stage: str, *, slug: str, mode: str) -> RunnerOutcome:
        if mode == "refresh":
            return self._run_refresh(slug)
        methods = {
            "SWEEPING": self._run_sweep,
            "RELEVANCE": self._run_relevance,
            "COMPOSING": self._run_compose,
            "REPULLING": self._run_repull,
            "GATING_PRESS": self._run_press,
        }
        try:
            method = methods[stage]
        except KeyError as exc:
            raise ValueError(f"no runner adapter for {stage}") from exc
        return method(slug)

    def _run_sweep(self, slug: str) -> RunnerOutcome:
        from agents.review import gate_designator, sweep_artifact_path

        client = self._client_name(slug)
        review_dir = str(self.root / "data" / "review")
        try:
            designator_before = gate_designator(
                client, review_dir=review_dir)
        except (OSError, ValueError) as exc:
            return RunnerOutcome(
                False, 2, "sweep scope unavailable",
                reason=f"cannot bind the current sweep scope: {exc}",
                fix_surface="the approved review packet and workstation scope",
            )
        try:
            expected = Path(sweep_artifact_path(
                client, review_dir=review_dir))
            before = _file_token(expected)
        except FileNotFoundError:
            expected = None
            before = _FileToken(False)
        proc = self._invoke([
            self.python, str(self.root / "run_searches.py"),
            "--client", client,
        ])
        fix = "run_searches.py, the approved strategy, profile, and source keys"
        if proc.returncode != 0:
            return self._failure(proc, fix=fix)
        try:
            designator_after = gate_designator(
                client, review_dir=review_dir)
            if designator_after != designator_before:
                raise ValueError(
                    f"gate designator changed from {designator_before!r} "
                    f"to {designator_after!r} during sweep")
            resolved = Path(sweep_artifact_path(
                client, review_dir=review_dir))
        except (OSError, ValueError) as exc:
            return self._failure(
                proc, fix=fix,
                reason=f"sweep exited 0 but no current artifact resolves: {exc}")
        if expected is not None and resolved != expected:
            return self._failure(
                proc, fix=fix,
                reason=f"sweep scope changed artifact path from {expected} "
                       f"to {resolved}")
        prior = before
        if not _fresh(resolved, prior):
            return self._failure(
                proc, fix=fix,
                reason=f"sweep exited 0 without a fresh artifact at {resolved}")
        try:
            sweep = json.loads(resolved.read_text(encoding="utf-8"))
            generated = _parse_aware(
                sweep.get("generated_at"), label="sweep generated_at")
            del generated
            if sweep.get("client") != client or not isinstance(
                    sweep.get("results"), dict):
                raise ValueError("client/results do not match the runner contract")
        except (OSError, ValueError, AssessmentStateError,
                json.JSONDecodeError) as exc:
            return self._failure(
                proc, fix=fix, reason=f"fresh sweep artifact is invalid: {exc}")
        return RunnerOutcome(
            True, 0, f"fresh sweep written: {resolved.name}",
            artifact=str(resolved), stdout=proc.stdout, stderr=proc.stderr)

    def _run_relevance(self, slug: str) -> RunnerOutcome:
        client = self._client_name(slug)
        out = self.root / "data" / "state" / "relevance" / f"{slug}.calibration.csv"
        before = _file_token(out)
        inputs_before = _relevance_input_binding(self.root, slug)
        from agents.review import sweep_artifact_path

        sweep = Path(sweep_artifact_path(
            client, review_dir=str(self.root / "data" / "review")))
        sweep_before = _file_token(sweep)
        if not sweep_before.exists:
            return RunnerOutcome(
                False, 2, "relevance input missing",
                reason=f"relevance has no current sweep at {sweep}",
                fix_surface=f"run_searches.py --client {client}",
            )
        started = _aware_iso(self.clock())
        argv = [
            self.python, "-m", "tools.relevance.calibrate",
            "--client", client, "--out", str(out),
        ]
        fix = f"tools.relevance.calibrate and the {slug} taxonomy/profile"
        try:
            with self._relevance_overlay(sweep) as overlay_name:
                proc = self._invoke(argv, cwd=Path(overlay_name))
        except (OSError, ValueError) as exc:
            return RunnerOutcome(
                False, 1, "relevance isolation failed",
                reason=f"cannot isolate the gate-designated sweep: {exc}",
                fix_surface=fix,
            )
        if proc.returncode != 0:
            return self._failure(proc, fix=fix)
        combined = proc.stdout + "\n" + proc.stderr
        summaries = [line.strip() for line in combined.splitlines()
                     if line.strip().startswith(f"[calibrate] {client}:")]
        if len(summaries) != 1 or "SKIPPED" in summaries[0]:
            reason = (summaries[0] if summaries else
                      f"relevance exited 0 without a {client} summary")
            return self._failure(proc, fix=fix, reason=reason)
        if not _fresh(out, before):
            return self._failure(
                proc, fix=fix,
                reason=f"relevance exited 0 without a fresh artifact at {out}")
        if _file_token(sweep) != sweep_before:
            return self._failure(
                proc, fix=fix,
                reason="the current sweep changed during relevance scoring")
        if _relevance_input_binding(self.root, slug) != inputs_before:
            return self._failure(
                proc, fix=fix,
                reason="relevance inputs changed during relevance scoring")
        try:
            receipt = write_relevance_receipt(
                self.root, slug, client,
                command=argv[1:], sweep_path=sweep, started_at=started,
                completed_at=_aware_iso(self.clock()), inputs=inputs_before,
                calibration_path=out, summary=summaries[0])
        except (OSError, ValueError, csv.Error) as exc:
            return self._failure(
                proc, fix=fix, reason=f"fresh relevance artifact is invalid: {exc}")
        return RunnerOutcome(
            True, 0, f"relevance output ready: {out.name}",
            artifact=str(receipt), stdout=proc.stdout, stderr=proc.stderr)

    def _run_compose(self, slug: str) -> RunnerOutcome:
        script = self.root / "run_compose.py"
        fix = f"run_compose.py --client {slug} (C1 composer CLI)"
        if not script.exists():
            return RunnerOutcome(
                False, 127, "composer unavailable",
                reason="run_compose.py is absent at runtime",
                fix_surface=fix,
            )
        try:
            client = self._require_client_name(slug)
        except Exception as exc:  # noqa: BLE001 - profile gate owns identity
            return RunnerOutcome(
                False, 2, "composer identity unavailable",
                reason=f"C1 cannot establish exact client identity: {exc}",
                fix_surface=f"clients/{slug}/profile.json",
            )
        artifact = self.root / "clients" / slug / "signal_board_content.json"
        before, trails_before = compose_pair_tokens(self.root, slug)
        proc = self._invoke([self.python, str(script), "--client", slug])
        if proc.returncode != 0:
            named = _named_c1_failure(proc.stderr)
            if named is not None:
                # C1's structured payload survives: its reason and fix
                # surface ride the outcome, its stage names the note
                return RunnerOutcome(
                    False, proc.returncode,
                    f"composer failed at {named['stage']}",
                    reason=named["reason"],
                    fix_surface=named["fix_surface"],
                    stdout=proc.stdout, stderr=proc.stderr,
                )
            return self._failure(proc, fix=fix)
        try:
            trail = validate_compose_pair(
                self.root, slug, client, proc.stdout,
                content_before=before, trails_before=trails_before)
        except (FileNotFoundError, OSError, ValueError,
                json.JSONDecodeError) as exc:
            return self._failure(
                proc, fix=fix,
                reason=f"composer exited 0 without valid board content: {exc}")
        return RunnerOutcome(
            True, 0,
            f"board content composed: {artifact.name}; trail {trail.name}",
            artifact=str(artifact), stdout=proc.stdout, stderr=proc.stderr)

    def _run_repull(self, slug: str) -> RunnerOutcome:
        from agents.review import sweep_artifact_path

        client = self._client_name(slug)
        sweep_path = Path(sweep_artifact_path(
            client, review_dir=str(self.root / "data" / "review")))
        before = _file_token(sweep_path)
        proc = self._invoke([
            self.python, "-m", "tools.api.award_repull", "--client", client,
        ])
        fix = "tools.api.award_repull, cited award identities, and USAspending"
        if proc.returncode != 0:
            return self._failure(proc, fix=fix)
        if not _fresh(sweep_path, before):
            return self._failure(
                proc, fix=fix,
                reason=f"award re-pull exited 0 without updating {sweep_path}")
        try:
            from agents.reports.board_content import SignalBoardContent
            from tools.api.award_repull import board_award_gids

            sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
            rows = (sweep.get("results") or {}).get("award_repulls")
            if not isinstance(rows, list):
                raise ValueError("results.award_repulls is absent")
            content_path = (self.root / "clients" / slug /
                            "signal_board_content.json")
            content = SignalBoardContent.model_validate_json(
                content_path.read_text(encoding="utf-8"))
            expected = board_award_gids(content)
            actual = [row.get("generated_id") for row in rows]
            if actual != expected:
                raise ValueError(
                    f"award_repulls identities {actual!r} != cited {expected!r}")
            for index, row in enumerate(rows):
                _parse_aware(
                    row.get("retrieved_at"),
                    label=f"award_repulls[{index}].retrieved_at")
        except (OSError, ValueError, AssessmentStateError,
                json.JSONDecodeError) as exc:
            return self._failure(
                proc, fix=fix, reason=f"re-pulled sweep is invalid: {exc}")
        watch_stdout = ""
        watch_stderr = ""
        if candidate_review_watch_enabled(self.root):
            watch_script = self.root / "run_candidate_review_watch.py"
            watch_fix = (
                f"run_candidate_review_watch.py --client {slug} "
                "(approved Candidate Review research inputs)"
            )
            if not watch_script.is_file():
                return RunnerOutcome(
                    False,
                    127,
                    "candidate-review-watch unavailable",
                    reason="run_candidate_review_watch.py is absent at runtime",
                    fix_surface=watch_fix,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                )
            sweep_after_repull = _file_token(sweep_path)
            watch_proc = self._invoke([
                self.python,
                str(watch_script),
                "--client",
                slug,
            ])
            watch_stdout = watch_proc.stdout
            watch_stderr = watch_proc.stderr
            if watch_proc.returncode != 0:
                named = _named_candidate_review_failure(watch_proc.stderr)
                if named is not None:
                    return RunnerOutcome(
                        False,
                        watch_proc.returncode,
                        "candidate-review-watch failed named",
                        reason=named["reason"],
                        fix_surface=named["fix_surface"],
                        stdout=proc.stdout + watch_proc.stdout,
                        stderr=proc.stderr + watch_proc.stderr,
                    )
                return RunnerOutcome(
                    False,
                    watch_proc.returncode,
                    "candidate-review-watch failed",
                    reason=_last_error(
                        watch_proc.stdout,
                        watch_proc.stderr,
                        watch_proc.returncode,
                    ),
                    fix_surface=watch_fix,
                    stdout=proc.stdout + watch_proc.stdout,
                    stderr=proc.stderr + watch_proc.stderr,
                )
            try:
                validate_candidate_review_watch_marker(
                    self.root,
                    slug,
                    watch_proc.stdout,
                )
                if _file_token(sweep_path) != sweep_after_repull:
                    raise ValueError(
                        "Candidate Review watch changed the re-pulled sweep"
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return RunnerOutcome(
                    False,
                    2,
                    "candidate-review-watch receipt invalid",
                    reason=(
                        "Candidate Review watch exited 0 without a valid "
                        f"promoted receipt: {exc}"
                    ),
                    fix_surface=watch_fix,
                    stdout=proc.stdout + watch_proc.stdout,
                    stderr=proc.stderr + watch_proc.stderr,
                )
        note = f"award records re-pulled: {len(rows)}"
        if watch_stdout:
            note += "; Candidate Review watch completed"
        return RunnerOutcome(
            True, 0, note,
            artifact=str(sweep_path),
            stdout=proc.stdout + watch_stdout,
            stderr=proc.stderr + watch_stderr)

    def _press_paths(self, client: str) -> tuple[Path, Path, Path]:
        """Report artifact paths ride the PRESS-ARTIFACT slug family
        (agents.press_snapshot.client_slug of the display name), which is
        run_signal_board's own naming; it is deliberately distinct from
        the canonical client slug that names clients/ and assessment
        state."""
        from agents.press_snapshot import client_slug

        base = (self.root / "data" / "reports" /
                f"{client_slug(client)}.federal_opportunity_signals")
        return (Path(f"{base}.html"), Path(f"{base}.DO-NOT-SEND.html"),
                Path(f"{base}.internal.md"))

    def _interpret_press(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        slug: str,
        client: str,
        before: tuple[_FileToken, _FileToken, _FileToken],
        fix: str,
    ) -> RunnerOutcome:
        clean, gated, internal = self._press_paths(client)
        fresh_clean = _fresh(clean, before[0])
        fresh_gated = _fresh(gated, before[1])
        fresh_internal = _fresh(internal, before[2])
        if proc.returncode == 0 and fresh_clean and fresh_internal:
            try:
                trail = internal.read_text(encoding="utf-8")
            except OSError as exc:
                return self._failure(proc, fix=fix, reason=str(exc))
            if (not trail.startswith(
                    f"# INTERNAL · {client} · Signal Board press trail\n")
                    or "\n## Gate verdict\n- CLEAN (client-final)\n"
                    not in trail):
                return self._failure(
                    proc, fix=fix,
                    reason=("press exited 0 but INTERNAL sidecar identity or "
                            "CLEAN verdict is invalid"))
            match = re.search(r"(?m)^sha256 ([0-9a-f]{64})$", proc.stdout)
            if match is None or match.group(1) != _file_sha(clean):
                return self._failure(
                    proc, fix=fix,
                    reason="press stdout SHA does not match the fresh clean HTML")
            if gated.exists():
                return self._failure(
                    proc, fix=fix,
                    reason="press left a DO-NOT-SEND sibling beside clean HTML")
            return RunnerOutcome(
                True, 0, f"clean draft pressed: {clean.name}",
                artifact=str(clean), stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode == 2 and fresh_gated and fresh_internal:
            try:
                trail = internal.read_text(encoding="utf-8")
            except OSError as exc:
                return self._failure(proc, fix=fix, reason=str(exc))
            if (not trail.startswith(
                    f"# INTERNAL · {client} · Signal Board press trail\n")
                    or "\n## Gate verdict\n"
                       "- DO-NOT-SEND (violations below)\n" not in trail):
                return self._failure(
                    proc, fix=fix,
                    reason=("gated press sidecar identity or DO-NOT-SEND "
                            "verdict is invalid"))
            match = re.search(r"(?m)^sha256 ([0-9a-f]{64})$", proc.stdout)
            if match is None or match.group(1) != _file_sha(gated):
                return self._failure(
                    proc, fix=fix,
                    reason="press stdout SHA does not match DO-NOT-SEND HTML")
            if clean.exists():
                return self._failure(
                    proc, fix=fix,
                    reason="press left clean HTML beside DO-NOT-SEND HTML")
            return RunnerOutcome(
                True, 2,
                f"gated draft pressed: {gated.name}; DO-NOT-SEND",
                artifact=str(gated), stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode == 0:
            return self._failure(
                proc, fix=fix,
                reason="press exited 0 without a fresh clean HTML + INTERNAL pair")
        return self._failure(proc, fix=fix)

    def _run_press(self, slug: str) -> RunnerOutcome:
        client = self._client_name(slug)
        paths = self._press_paths(client)
        before = tuple(_file_token(path) for path in paths)
        proc = self._invoke([
            self.python, str(self.root / "run_signal_board.py"),
            "--client", client,
        ])
        return self._interpret_press(
            proc, slug=slug, client=client, before=before,
            fix=f"run_signal_board.py and {paths[2]}",
        )

    def _validate_refresh_artifacts(
        self,
        outputs: dict[str, Path],
        *,
        slug: str,
        client: str,
        not_before: datetime | None = None,
    ) -> datetime:
        """Consume the C3 schema owners and prove one paired output set."""
        snapshot_path = outputs["snapshot"]
        delta_path = outputs["delta"]
        summary_path = outputs["delta-summary"]
        if len({snapshot_path, delta_path, summary_path}) != 3:
            raise ValueError("snapshot, delta, and summary paths must be distinct")
        if not snapshot_path.name.endswith(".json"):
            raise ValueError("snapshot output must end in .json")
        stamp = snapshot_path.name[:-len(".json")]
        if (delta_path.name != f"{stamp}.delta.json"
                or summary_path.name != f"{stamp}.internal.md"):
            raise ValueError("C3 snapshot, delta, and summary are not one pair")

        from agents.press_delta import (  # type: ignore[import-not-found]
            DELTA_CLASS_ORDER,
            DELTA_SCHEMA_VERSION,
            delta_feed,
            render_internal_markdown,
        )
        from agents.press_snapshot import (  # type: ignore[import-not-found]
            PressSnapshot,
            client_slug,
            snapshot_filename,
        )

        snapshot = PressSnapshot.model_validate_json(
            snapshot_path.read_text(encoding="utf-8"))
        expected_slug = client_slug(client)
        if snapshot.client_name != client or snapshot.slug != expected_slug:
            raise ValueError(
                f"snapshot identity {snapshot.client_name!r}/{snapshot.slug!r} "
                f"does not match {client!r}/{expected_slug!r}")
        # C3 deliberately names snapshots at whole-second precision.  Floor
        # the adapter boundary to that same schema precision so a snapshot
        # minted during the invocation's start second is not misclassified as
        # predating it.  The replacement-sensitive output tokens above still
        # prove this invocation wrote the file; any earlier second remains a
        # hard failure.
        not_before_second = (
            not_before.astimezone(timezone.utc).replace(microsecond=0)
            if not_before is not None else None
        )
        if (not_before_second is not None
                and snapshot.press_timestamp.astimezone(timezone.utc)
                < not_before_second):
            raise ValueError("snapshot predates this C3 refresh invocation")
        expected_snapshot = snapshot_filename(snapshot.press_timestamp)
        if snapshot_path.name != expected_snapshot:
            raise ValueError(
                f"snapshot filename {snapshot_path.name!r} does not match "
                f"press timestamp {expected_snapshot!r}")
        stamp = expected_snapshot[:-len(".json")]
        if (delta_path.name != f"{stamp}.delta.json"
                or summary_path.name != f"{stamp}.internal.md"):
            raise ValueError("C3 snapshot, delta, and summary are not one pair")
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
        if not isinstance(delta, dict):
            raise ValueError("delta output root is not an object")
        required_root = {
            "schema_version", "client_name", "slug", "baseline_created",
            "before_timestamp", "after_timestamp", "counts",
            "verification", "items",
        }
        if set(delta) != required_root:
            raise ValueError("delta root does not match the complete C3 schema")
        if delta.get("schema_version") != DELTA_SCHEMA_VERSION:
            raise ValueError("delta schema version does not match C3")
        if delta.get("client_name") != client \
                or delta.get("slug") != expected_slug:
            raise ValueError("delta client identity does not match the snapshot")
        after = _parse_aware(
            delta.get("after_timestamp"), label="delta after_timestamp")
        if after.astimezone(timezone.utc) != snapshot.press_timestamp.astimezone(
                timezone.utc):
            raise ValueError("delta timestamp does not match the snapshot")
        baseline = delta.get("baseline_created")
        before_timestamp = delta.get("before_timestamp")
        if not isinstance(baseline, bool):
            raise ValueError("delta baseline_created must be boolean")
        if baseline:
            if before_timestamp is not None:
                raise ValueError("baseline delta must not name a prior timestamp")
        else:
            before = _parse_aware(
                before_timestamp, label="delta before_timestamp")
            if before >= after:
                raise ValueError("delta before_timestamp must precede after_timestamp")
        verification = delta.get("verification")
        if (not isinstance(verification, dict)
                or set(verification) != {
                    "status", "reason", "verified", "disputed",
                    "unverifiable"}
                or verification.get("status") not in {"COMPLETE", "DEFERRED"}
                or (verification.get("reason") is not None
                    and not isinstance(verification.get("reason"), str))
                or any(not isinstance(verification.get(key), list)
                       or any(not isinstance(value, str)
                              for value in verification.get(key, []))
                       for key in ("verified", "disputed", "unverifiable"))):
            raise ValueError("delta verification does not match the C3 schema")
        counts = delta.get("counts")
        items = delta.get("items")
        if (not isinstance(counts, dict)
                or set(counts) != set(DELTA_CLASS_ORDER)
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 0 for value in counts.values())
                or not isinstance(items, list)):
            raise ValueError("delta counts/items do not match the C3 schema")
        actual = {kind: 0 for kind in DELTA_CLASS_ORDER}
        for item in items:
            if not isinstance(item, dict) or item.get("kind") not in actual:
                raise ValueError("delta contains an unknown item class")
            if (not isinstance(item.get("id"), str) or not item["id"]
                    or "before" not in item or "after" not in item):
                raise ValueError("delta item lacks id/before/after evidence")
            kind = item["kind"]
            if kind in {"NEW", "RECOMPETE_APPEARED"} and (
                    item["before"] is not None
                    or not isinstance(item["after"], dict)):
                raise ValueError(f"{kind} delta item has invalid evidence")
            if kind == "DROPPED" and (
                    not isinstance(item["before"], dict)
                    or (item["after"] is not None
                        and not isinstance(item["after"], dict))
                    or item.get("reason") not in {
                        "expired_window", "out_of_scope",
                        "fell_below_threshold", "not_present", "unknown",
                    }):
                raise ValueError("DROPPED delta item has invalid evidence")
            if kind == "FIGURE_DRIFT":
                observation = item.get("verification")
                if (not isinstance(item["before"], dict)
                        or not isinstance(item["after"], dict)
                        or item["before"] == item["after"]
                        or not isinstance(observation, dict)
                        or observation.get("status") not in {
                            "COMPLETE", "DEFERRED"}
                        or (observation.get("reason") is not None
                            and not isinstance(
                                observation.get("reason"), str))):
                    raise ValueError(
                        "FIGURE_DRIFT has invalid evidence or verification")
            if kind == "WINDOW_MOVED" and (
                    not isinstance(item["before"], dict)
                    or not isinstance(item["after"], dict)
                    or item["before"] == item["after"]):
                raise ValueError("WINDOW_MOVED has invalid evidence")
            if kind == "GATE_STATE_CHANGED" and (
                    item["id"] != "gate_state"
                    or item["before"] == item["after"]):
                raise ValueError("GATE_STATE_CHANGED has invalid evidence")
            actual[item["kind"]] += 1
        if counts != actual:
            raise ValueError("delta counts do not match its items")
        delta_feed(delta)
        expected_summary = render_internal_markdown(delta)
        if summary_path.read_text(encoding="utf-8") != expected_summary:
            raise ValueError("delta INTERNAL summary does not render from its JSON")
        return snapshot.press_timestamp

    def _run_refresh(self, slug: str) -> RunnerOutcome:
        script = self.root / "run_refresh_press.py"
        fix = f"run_refresh_press.py --client {slug} (C3 press/refresh CLI)"
        if not script.exists():
            return RunnerOutcome(
                False, 127, "refresh unavailable",
                reason="run_refresh_press.py is absent at runtime",
                fix_surface=fix,
            )
        try:
            client = self._require_client_name(slug)
        except Exception as exc:  # noqa: BLE001 - profile gate owns identity
            return RunnerOutcome(
                False, 2, "refresh identity unavailable",
                reason=f"C3 cannot establish exact client identity: {exc}",
                fix_surface=f"clients/{slug}/profile.json",
            )
        fix = f"run_refresh_press.py --client {client} (C3 press/refresh CLI)"
        paths = self._press_paths(client)
        before = tuple(_file_token(path) for path in paths)
        calibration = (self.root / "data" / "state" / "relevance" /
                       f"{slug}.calibration.csv")
        content_before, compose_trails_before = compose_pair_tokens(
            self.root, slug)
        calibration_before = _file_token(calibration)
        refresh_started = self.clock()
        if refresh_started.tzinfo is None or refresh_started.utcoffset() is None:
            return RunnerOutcome(
                False, 2, "refresh clock invalid",
                reason="C3 refresh clock must be timezone-aware",
                fix_surface=fix,
            )
        # delta artifacts are owned by the press/snapshot subsystem and
        # ride ITS slug family, not the canonical assessment slug
        from agents.press_snapshot import client_slug as _press_slug

        report_root = (self.root / "data" / "reports").resolve()
        qa_expected = (
            report_root
            / f"{_press_slug(client)}.federal_opportunity_signals.qa.json"
        )
        qa_before = _file_token(qa_expected)
        delta_root = (self.root / "data" / "state" / "deltas" /
                      _press_slug(client))
        delta_before = {
            path.resolve(): _file_token(path)
            for path in delta_root.rglob("*") if path.is_file()
        } if delta_root.exists() else {}
        proc = self._invoke([self.python, str(script), "--client", client,
                             "--now", refresh_started.isoformat()])
        failure_markers = re.findall(
            r"(?m)^\[refresh:failure\] (.+)$", proc.stderr)
        if failure_markers:
            expected = {
                "stage": "compose-refresh",
                "reason": "run_compose.py is absent at runtime",
                "fix_surface": (
                    f"run_compose.py --client {slug} (C1 composer CLI)"
                ),
            }
            try:
                named_failure = json.loads(failure_markers[0])
            except json.JSONDecodeError:
                named_failure = None
            well_formed = (
                isinstance(named_failure, dict)
                and set(named_failure) == {"stage", "reason", "fix_surface"}
                and all(isinstance(named_failure[key], str)
                        and named_failure[key]
                        for key in ("stage", "reason", "fix_surface")))
            if (len(failure_markers) != 1 or proc.returncode == 0
                    or not well_formed
                    or named_failure["stage"] not in C3_FAILURE_STAGES
                    or (proc.returncode == 127
                        and named_failure["stage"] == "compose-refresh"
                        and named_failure != expected)):
                # malformed, duplicate, unknown-stage,
                # success-with-failure-marker, and wrong rc127
                # missing-composer payloads stay refused
                return self._failure(
                    proc, fix=fix,
                    reason="C3 refresh emitted an invalid named failure")
            if named_failure == expected and proc.returncode == 127:
                return RunnerOutcome(
                    False, proc.returncode, "compose-refresh unavailable",
                    reason=expected["reason"],
                    fix_surface=expected["fix_surface"],
                    stdout=proc.stdout, stderr=proc.stderr,
                )
            # a single well-formed marker for any stage the refresh
            # runner owns, at a nonzero exit, preserves that step's
            # reason, fix surface, and return code verbatim
            return RunnerOutcome(
                False, proc.returncode,
                f"{named_failure['stage']} failed named",
                reason=named_failure["reason"],
                fix_surface=named_failure["fix_surface"],
                stdout=proc.stdout, stderr=proc.stderr,
            )
        if candidate_review_watch_enabled(self.root):
            try:
                validate_candidate_review_watch_marker(
                    self.root,
                    slug,
                    proc.stdout,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return self._failure(
                    proc,
                    fix=fix,
                    reason=(
                        "C3 skipped or failed the required Candidate Review "
                        f"watch generation: {exc}"
                    ),
                )
        output_matches = re.findall(
            r"(?m)^\[out:(snapshot|delta|delta-summary)\] (.+)$",
            proc.stdout,
        )
        duplicates = sorted({
            name for name, _path in output_matches
            if sum(1 for candidate, _raw in output_matches
                   if candidate == name) > 1
        })
        if duplicates:
            return self._failure(
                proc, fix=fix,
                reason="C3 refresh repeated output marker(s): "
                       + ", ".join(duplicates))
        output_rows = dict(output_matches)
        missing = [name for name in ("snapshot", "delta", "delta-summary")
                   if name not in output_rows]
        if missing:
            if proc.returncode != 0:
                return self._failure(proc, fix=fix)
            return self._failure(
                proc, fix=fix,
                reason="C3 refresh omitted output(s): " + ", ".join(missing))
        parsed_paths: dict[str, Path] = {}
        delta_base = delta_root.resolve()
        for name, raw in output_rows.items():
            path = Path(raw.strip())
            if not path.is_absolute():
                path = self.root / path
            resolved = path.resolve()
            try:
                inside = os.path.commonpath([resolved, delta_base]) == str(delta_base)
            except ValueError:
                inside = False
            if not inside:
                return self._failure(
                    proc, fix=fix,
                    reason=f"C3 {name} output escaped {delta_base}: {resolved}")
            if resolved.parent != delta_base:
                return self._failure(
                    proc, fix=fix,
                    reason=f"C3 {name} output is not directly under "
                           f"{delta_base}: {resolved}")
            if not _fresh(resolved, delta_before.get(resolved, _FileToken(False))):
                return self._failure(
                    proc, fix=fix,
                    reason=f"C3 {name} output is absent or stale: {resolved}")
            parsed_paths[name] = resolved
        try:
            accepted_press_timestamp = self._validate_refresh_artifacts(
                parsed_paths, slug=slug, client=client,
                not_before=refresh_started)
        except (ImportError, OSError, ValueError, KeyError,
                json.JSONDecodeError) as exc:
            return self._failure(
                proc, fix=fix, reason=f"C3 delta output is invalid: {exc}")
        try:
            validate_compose_pair(
                self.root, slug, client, proc.stdout,
                content_before=content_before,
                trails_before=compose_trails_before)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._failure(
                proc, fix=fix,
                reason=f"C3 skipped or failed the required compose refresh: {exc}")
        combined = proc.stdout + "\n" + proc.stderr
        relevance_summaries = [
            line.strip() for line in combined.splitlines()
            if line.strip().startswith(f"[calibrate] {client}:")
        ]
        if (not _fresh(calibration, calibration_before)
                or len(relevance_summaries) != 1
                or "SKIPPED" in relevance_summaries[0]):
            return self._failure(
                proc, fix=fix,
                reason="C3 skipped or failed the required relevance refresh")
        try:
            with calibration.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                required = {
                    "client", "record_ref", "engine_score",
                    "disagreement", "scope_basis",
                }
                if not required.issubset(set(reader.fieldnames or [])):
                    raise ValueError("calibration CSV header is incomplete")
                if any(row.get("client") != client for row in rows):
                    raise ValueError("calibration CSV contains another client")
        except (OSError, ValueError, csv.Error) as exc:
            return self._failure(
                proc, fix=fix,
                reason=f"C3 relevance refresh is invalid: {exc}")
        qa_markers = re.findall(
            r"(?m)^\[out:signal-board-qa\] (.+)$", proc.stdout)
        if len(qa_markers) != 1:
            return self._failure(
                proc, fix=fix,
                reason=("C3 refresh must emit exactly one "
                        "[out:signal-board-qa] marker"))
        marked_qa = Path(qa_markers[0].strip())
        if not marked_qa.is_absolute():
            marked_qa = self.root / marked_qa
        resolved_qa = marked_qa.resolve()
        try:
            qa_inside = os.path.commonpath(
                [resolved_qa, report_root]) == str(report_root)
        except ValueError:
            qa_inside = False
        if not qa_inside:
            return self._failure(
                proc, fix=fix,
                reason=("C3 Signal Board QA output escaped "
                        f"{report_root}: {resolved_qa}"))
        if resolved_qa.parent != report_root or resolved_qa != qa_expected:
            return self._failure(
                proc, fix=fix,
                reason=("C3 Signal Board QA output is not the direct expected "
                        f"certificate {qa_expected}: {resolved_qa}"))
        if not _fresh(resolved_qa, qa_before):
            return self._failure(
                proc, fix=fix,
                reason=("C3 Signal Board QA output is absent or stale: "
                        f"{resolved_qa}"))
        try:
            presentation_name = self._presentation_name(slug, client)
            from agents.reports.release import signal_board_status

            if proc.returncode == 0:
                certified_html = paths[0]
            elif proc.returncode == 2:
                certified_html = paths[1]
            else:
                raise ValueError(
                    "Signal Board QA marker accompanied an unsupported "
                    f"refresh exit {proc.returncode}")
            certificate = signal_board_status(
                str(certified_html),
                exact_client_name=client,
                presentation_name=presentation_name,
                slug=_press_slug(client),
                qa_path=str(resolved_qa),
            )
            if not certificate.get("certificate_valid"):
                raise ValueError(certificate.get("reason") or
                                 "certificate validation failed")
            qa_payload = certificate.get("qa")
            qa_timestamp = _parse_aware(
                qa_payload.get("press_timestamp")
                if isinstance(qa_payload, dict) else None,
                label="Signal Board QA press_timestamp",
            )
            if qa_timestamp.astimezone(timezone.utc) != \
                    accepted_press_timestamp.astimezone(timezone.utc):
                raise ValueError(
                    "Signal Board QA timestamp does not match the accepted "
                    "refresh snapshot")
        except (ImportError, OSError, TypeError, ValueError) as exc:
            return self._failure(
                proc, fix=fix,
                reason=f"C3 Signal Board QA output is invalid: {exc}")
        outcome = self._interpret_press(
            proc, slug=slug, client=client, before=before, fix=fix)
        if outcome.success:
            return RunnerOutcome(
                True, outcome.returncode,
                outcome.note + f"; delta {parsed_paths['delta'].name}",
                artifact=str(parsed_paths["delta"]),
                stdout=proc.stdout, stderr=proc.stderr,
            )
        return outcome


class AssessmentChain:
    """State-machine owner.  Runners are replaceable only at the adapter seam."""

    def __init__(
        self,
        *,
        root: Path | str = _ROOT,
        state_dir: Path | str | None = None,
        runner: RunnerAdapter | None = None,
        clock: Callable[[], datetime] | None = None,
        relevance_validator: Callable[[str], bool] | None = None,
    ) -> None:
        self.root = Path(root)
        self.state_dir = Path(state_dir or default_state_dir(self.root))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.runner = runner or SubprocessRunnerAdapter(
            root=self.root, clock=self.clock)
        self.relevance_validator = relevance_validator

    def _relevance_is_current(
        self,
        slug: str,
        *,
        record: dict | None = None,
        stage: str | None = None,
    ) -> bool:
        if self.relevance_validator is not None:
            return self.relevance_validator(slug)
        effective_stage = stage or (record["state"] if record else None)
        content_sha = _latest_stage_sha(record, "content") if record else None
        sweep_sha = _latest_stage_sha(record, "sweep") if record else None
        if effective_stage in {"REPULLING", "GATING_PRESS"} \
                and content_sha is None:
            return False
        if effective_stage == "GATING_PRESS" and sweep_sha is None:
            return False
        return relevance_receipt_valid(
            slug,
            root=self.root,
            # A runner being resumed may have partially written the artifact
            # it owns, so that artifact is unbound only while the same runner
            # will run again.  Later stages require the exact append-only
            # post-run receipt hash.
            allow_board_content_change=effective_stage == "COMPOSING",
            expected_board_sha256=(content_sha if effective_stage in {
                "REPULLING", "GATING_PRESS"} else None),
            allow_sweep_change=effective_stage == "REPULLING",
            expected_sweep_sha256=(
                sweep_sha if effective_stage == "GATING_PRESS"
                or (effective_stage == "COMPOSING" and sweep_sha is not None)
                else None
            ),
        )

    def _now(self) -> str:
        return _aware_iso(self.clock())

    def _persist(self, record: dict) -> None:
        validate_state(record, expected_slug=record["slug"])
        _write_json_if_changed(
            state_path(record["slug"], state_dir=self.state_dir), record)

    def _append(
        self,
        record: dict,
        state: str,
        *,
        by: str,
        note: str,
        at: str | None = None,
        failure: dict | None = None,
        mode: str | None = None,
    ) -> None:
        if state not in STATES:
            raise ValueError(f"unknown assessment state {state}")
        row = {"state": state, "at": at or self._now(),
               "by": by, "note": note}
        _validate_transition(row, len(record["transitions"]))
        record["transitions"].append(row)
        record["state"] = state
        record["failure"] = failure if state == "FAILED" else None
        if mode is not None:
            record["mode"] = mode
        self._persist(record)

    def _initialize(self, slug: str, *, by: str) -> dict:
        record = {
            "version": SCHEMA_VERSION,
            "slug": slug,
            "mode": "assessment",
            "state": "INTAKE_DONE",
            "failure": None,
            "transitions": [{
                "state": "INTAKE_DONE",
                "at": self._now(),
                "by": by,
                "note": "intake artifacts accepted; assessment chain initialized",
            }],
        }
        self._persist(record)
        return record

    def _result(self, record: dict, start: int, *, paused: bool = False) -> ChainResult:
        return ChainResult(
            slug=record["slug"], state=record["state"], paused=paused,
            transitions=tuple(record["transitions"][start:]),
            failure=record["failure"],
        )

    def _fail(
        self, record: dict, stage: str, outcome: RunnerOutcome,
    ) -> None:
        reason = outcome.reason or f"runner exited {outcome.returncode}"
        fix = outcome.fix_surface or f"the sanctioned {stage} runner"
        failure = {"stage": stage, "reason": reason, "fix_surface": fix}
        self._append(
            record, "FAILED", by=_AUTO_BY,
            note=f"{stage} failed: {reason}; fix surface: {fix}",
            failure=failure,
        )

    def _run_stage(self, stage: str, *, slug: str, mode: str) -> RunnerOutcome:
        try:
            return self.runner.run(stage, slug=slug, mode=mode)
        except Exception as exc:  # noqa: BLE001 - every runner error is state
            fixes = {
                "SWEEPING": "run_searches.py, strategy/profile, and source keys",
                "RELEVANCE": "tools.relevance.calibrate and relevance inputs",
                "COMPOSING": f"run_compose.py --client {slug} (C1 composer CLI)",
                "REPULLING": "tools.api.award_repull and cited award records",
                "GATING_PRESS": (
                    f"run_refresh_press.py --client {slug} (C3 press/refresh CLI)"
                    if mode == "refresh" else "run_signal_board.py and its INTERNAL sidecar"),
            }
            return RunnerOutcome(
                False, 1, "runner raised",
                reason=f"{type(exc).__name__}: {str(exc)[:400]}",
                fix_surface=fixes[stage],
            )

    def _stage_receipt_sha(
        self, outcome: RunnerOutcome, *, stage: str,
    ) -> str | None:
        if self.relevance_validator is not None:
            return None
        if not outcome.artifact:
            raise ValueError(f"{stage} returned no artifact for its receipt")
        path = Path(outcome.artifact)
        if not path.is_absolute():
            path = self.root / path
        return _file_sha(path.resolve())

    def _basis_changed(self, stage: str) -> RunnerOutcome:
        return RunnerOutcome(
            False, 2, "relevance basis changed",
            reason=("relevance basis or a sanctioned stage artifact changed "
                    f"before {stage}"),
            fix_surface="--restart-from RELEVANCE and obtain a new taxonomy GO",
        )

    def _invalidate_go(self, slug: str) -> None:
        marker = go_path(slug, state_dir=self.state_dir)
        try:
            marker.unlink()
        except FileNotFoundError:
            pass

    def _restart(self, record: dict, target: str, *, by: str) -> None:
        if target not in RESTARTABLE_STATES:
            raise AssessmentStateError(
                f"--restart-from must be one of {', '.join(RESTARTABLE_STATES)}")
        first_current_compose = (
            target == "COMPOSING"
            and record["state"] == "AWAITING_TAXONOMY_GO")
        if (not first_current_compose
                and not _restart_target_reached(
                    record["transitions"], target)):
            error = (PauseRequired if target in (
                "COMPOSING", "REPULLING", "GATING_PRESS")
                else AssessmentStateError)
            raise error(
                f"cannot restart from {target}; that stage was not reached "
                "in the current execution epoch")
        refresh_restart = (
            record["mode"] == "refresh" and target == "GATING_PRESS")
        if refresh_restart:
            if not any(row["note"] ==
                       "C3 refresh/press orchestration started"
                       for row in record["transitions"]):
                raise AssessmentStateError(
                    "cannot restart refresh without a prior C3 transition")
        elif target in ("COMPOSING", "REPULLING", "GATING_PRESS"):
            marker = _load_go(
                record["slug"], state_dir=self.state_dir, state=record)
            if marker is None:
                raise PauseRequired(
                    f"cannot restart from {target} without a taxonomy GO marker")
            if record["state"] == "AWAITING_TAXONOMY_GO":
                _record_go_transition(
                    record, marker, state_dir=self.state_dir)
            if not _history_has_go(record["transitions"]):
                raise PauseRequired(
                    f"cannot restart from {target} without a recorded taxonomy GO")
            if not self._relevance_is_current(
                    record["slug"], record=record, stage=target):
                raise PauseRequired(
                    "relevance output is stale after taxonomy/scope changes; "
                    "rerun --restart-from RELEVANCE")
        invalidate_go = not refresh_restart and target not in {
            "COMPOSING", "REPULLING", "GATING_PRESS"}
        self._append(
            record, target, by=by,
            note=(f"operator refresh restart requested from {target}"
                  if refresh_restart
                  else f"operator restart requested from {target}"),
            mode="refresh" if refresh_restart else "assessment",
        )
        if invalidate_go:
            # Persist the restart first.  If validation or the atomic state
            # write fails, the durable approval marker remains untouched.
            self._invalidate_go(record["slug"])

    def _run_refresh(
        self, record: dict, start: int, *, enter: bool = True,
    ) -> ChainResult:
        if enter:
            self._append(
                record, "GATING_PRESS", by=_AUTO_BY,
                note="C3 refresh/press orchestration started",
                mode="refresh",
            )
        outcome = self._run_stage(
            "GATING_PRESS", slug=record["slug"], mode="refresh")
        if not outcome.success:
            self._fail(record, "GATING_PRESS", outcome)
            return self._result(record, start)
        self._append(
            record, "DRAFT_READY", by=_AUTO_BY,
            note=outcome.note or "refresh press completed; draft ready",
            mode="refresh",
        )
        return self._result(record, start)

    def run(
        self,
        client: str,
        *,
        by: str = "assessment-cli",
        go: bool = False,
        restart_from: str | None = None,
        refresh: bool = False,
    ) -> ChainResult:
        slug = canonical_slug(client)
        if not isinstance(by, str) or not by.strip():
            raise ValueError("by must be nonempty")
        if go and refresh:
            raise ValueError("--go and --refresh cannot be combined")
        if go and restart_from:
            raise ValueError("--go and --restart-from cannot be combined")
        if refresh and restart_from:
            raise ValueError("--refresh and --restart-from cannot be combined")

        with client_lock(slug, state_dir=self.state_dir):
            record = load_state(slug, state_dir=self.state_dir)
            start = len(record["transitions"]) if record is not None else 0
            if restart_from and record is None:
                raise AssessmentStateError(
                    "--restart-from requires an existing assessment history")
            if go:
                if record is None:
                    raise PauseRequired(
                        "GO refused: assessment has not reached taxonomy review")
                existing = _load_go(
                    slug, state_dir=self.state_dir, state=record)
                if existing is None:
                    if record["state"] != "AWAITING_TAXONOMY_GO":
                        raise PauseRequired(
                            f"GO refused while assessment is {record['state']}")
                    marker = {"by": by.strip(), "at": self._now()}
                    _validate_go_time(record, marker)
                    _write_json_if_changed(
                        go_path(slug, state_dir=self.state_dir), marker)
                    _record_go_transition(
                        record, marker, state_dir=self.state_dir)
                elif record["state"] == "AWAITING_TAXONOMY_GO":
                    _record_go_transition(
                        record, existing, state_dir=self.state_dir)
            elif record is None:
                self._invalidate_go(slug)
                record = self._initialize(slug, by=by.strip())

            assert record is not None
            if restart_from:
                self._restart(record, restart_from, by=by.strip())
            if refresh:
                return self._run_refresh(record, start)
            if (record["mode"] == "refresh"
                    and record["state"] == "GATING_PRESS"):
                return self._run_refresh(record, start, enter=False)
            if record["state"] == "FAILED":
                return self._result(record, start)
            if record["state"] == "DRAFT_READY":
                return self._result(record, start)
            if (record["mode"] == "assessment"
                    and record["state"] in {
                        "COMPOSING", "REPULLING", "GATING_PRESS"}
                    and not self._relevance_is_current(
                        slug, record=record)):
                raise PauseRequired(
                    "relevance output is stale after taxonomy/scope or "
                    "profile changes; rerun --restart-from RELEVANCE")

            while True:
                state = record["state"]
                if state == "INTAKE_DONE":
                    self._append(
                        record, "SWEEPING", by=_AUTO_BY,
                        note="sanctioned sweep runner started",
                        mode="assessment",
                    )
                    state = "SWEEPING"
                if state == "SWEEPING":
                    outcome = self._run_stage(
                        state, slug=slug, mode="assessment")
                    if not outcome.success:
                        self._fail(record, state, outcome)
                        return self._result(record, start)
                    self._append(
                        record, "RELEVANCE", by=_AUTO_BY,
                        note=outcome.note or "sweep completed; relevance started")
                    state = "RELEVANCE"
                if state == "RELEVANCE":
                    outcome = self._run_stage(
                        state, slug=slug, mode="assessment")
                    if not outcome.success:
                        self._fail(record, state, outcome)
                        return self._result(record, start)
                    self._append(
                        record, "AWAITING_TAXONOMY_GO", by=_AUTO_BY,
                        note=(outcome.note + "; taxonomy GO required")
                        if outcome.note else "relevance ready; taxonomy GO required")
                    return self._result(record, start, paused=True)
                if state == "AWAITING_TAXONOMY_GO":
                    marker = _load_go(
                        slug, state_dir=self.state_dir, state=record)
                    if marker is None:
                        return self._result(record, start, paused=True)
                    _record_go_transition(
                        record, marker, state_dir=self.state_dir)
                    if not self._relevance_is_current(
                            slug, record=record, stage="COMPOSING"):
                        raise PauseRequired(
                            "relevance output is stale after taxonomy/scope "
                            "changes; rerun --restart-from RELEVANCE")
                    self._append(
                        record, "COMPOSING", by=_AUTO_BY,
                        note=(f"taxonomy GO accepted from {marker['by']} at "
                              f"{marker['at']}; C1 composer started"))
                    state = "COMPOSING"
                if state == "COMPOSING":
                    outcome = self._run_stage(
                        state, slug=slug, mode="assessment")
                    if not outcome.success:
                        self._fail(record, state, outcome)
                        return self._result(record, start)
                    try:
                        content_sha = self._stage_receipt_sha(
                            outcome, stage="COMPOSING")
                    except (OSError, ValueError) as exc:
                        self._fail(record, state, RunnerOutcome(
                            False, 2, "compose receipt missing",
                            reason=str(exc),
                            fix_surface="run_compose.py artifact contract"))
                        return self._result(record, start)
                    if not self._relevance_is_current(
                            slug, record=record, stage="COMPOSING"):
                        self._fail(record, state, self._basis_changed("REPULLING"))
                        return self._result(record, start)
                    compose_note = outcome.note or (
                        "composition completed; award re-pull started")
                    if content_sha is not None:
                        compose_note += f"; content-sha256 {content_sha}"
                    self._append(
                        record, "REPULLING", by=_AUTO_BY,
                        note=compose_note)
                    state = "REPULLING"
                if state == "REPULLING":
                    if not self._relevance_is_current(
                            slug, record=record, stage="REPULLING"):
                        self._fail(record, state, self._basis_changed("REPULLING"))
                        return self._result(record, start)
                    outcome = self._run_stage(
                        state, slug=slug, mode="assessment")
                    if not outcome.success:
                        self._fail(record, state, outcome)
                        return self._result(record, start)
                    try:
                        sweep_sha = self._stage_receipt_sha(
                            outcome, stage="REPULLING")
                    except (OSError, ValueError) as exc:
                        self._fail(record, state, RunnerOutcome(
                            False, 2, "repull receipt missing",
                            reason=str(exc),
                            fix_surface="tools.api.award_repull artifact contract"))
                        return self._result(record, start)
                    if not self._relevance_is_current(
                            slug, record=record, stage="REPULLING"):
                        self._fail(record, state, self._basis_changed("GATING_PRESS"))
                        return self._result(record, start)
                    repull_note = outcome.note or (
                        "award re-pull completed; gated press started")
                    if sweep_sha is not None:
                        repull_note += f"; sweep-sha256 {sweep_sha}"
                    self._append(
                        record, "GATING_PRESS", by=_AUTO_BY,
                        note=repull_note)
                    state = "GATING_PRESS"
                if state == "GATING_PRESS":
                    if not self._relevance_is_current(
                            slug, record=record, stage="GATING_PRESS"):
                        self._fail(
                            record, state, self._basis_changed("GATING_PRESS"))
                        return self._result(record, start)
                    outcome = self._run_stage(
                        state, slug=slug, mode="assessment")
                    if not outcome.success:
                        self._fail(record, state, outcome)
                        return self._result(record, start)
                    if not self._relevance_is_current(
                            slug, record=record, stage="GATING_PRESS"):
                        self._fail(
                            record, state, self._basis_changed("DRAFT_READY"))
                        return self._result(record, start)
                    self._append(
                        record, "DRAFT_READY", by=_AUTO_BY,
                        note=outcome.note or "gated press completed; draft ready")
                    return self._result(record, start)
