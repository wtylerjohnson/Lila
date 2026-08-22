"""Primary assessment inputs commit transactionally with recoverable history."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.artifacts as artifacts


def test_atomic_json_replaces_only_after_validation_and_keeps_history(tmp_path):
    path = tmp_path / "searches_testco.json"
    artifacts.atomic_write_json(path, {"version": 1})
    artifacts.atomic_write_json(path, {"version": 2})

    assert json.loads(path.read_text()) == {"version": 2}
    history = list((tmp_path / ".history" / path.name).glob("*.json"))
    assert {json.loads(item.read_text())["version"] for item in history} == {1, 2}
    assert not list(tmp_path.rglob("*.tmp"))


def test_failed_replace_preserves_last_known_good_artifact(tmp_path, monkeypatch):
    path = tmp_path / "searches_testco.json"
    artifacts.atomic_write_json(path, {"version": 1})
    real_replace = artifacts.os.replace

    def fail_canonical(source, destination):
        if Path(destination) == path:
            raise OSError("simulated commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(artifacts.os, "replace", fail_canonical)
    with pytest.raises(OSError, match="simulated commit failure"):
        artifacts.atomic_write_json(path, {"version": 2})
    assert json.loads(path.read_text()) == {"version": 1}
    assert not list(tmp_path.rglob("*.tmp"))


def test_history_failure_never_creates_post_commit_ambiguity(tmp_path, monkeypatch):
    path = tmp_path / "searches_testco.json"
    artifacts.atomic_write_json(path, {"version": 1})

    def fail_history(*args, **kwargs):
        raise OSError("history unavailable")

    monkeypatch.setattr(artifacts, "_archive_bytes", fail_history)
    assert artifacts.atomic_write_json(path, {"version": 2}) == path
    assert json.loads(path.read_text()) == {"version": 2}


def test_unserializable_cycle_never_touches_current_artifact(tmp_path):
    path = tmp_path / "searches_testco.json"
    artifacts.atomic_write_json(path, {"version": 1})
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="Circular reference"):
        artifacts.atomic_write_json(path, cycle)
    assert json.loads(path.read_text()) == {"version": 1}
