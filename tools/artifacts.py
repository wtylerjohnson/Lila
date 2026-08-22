"""Transactional persistence for primary operational JSON artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _archive_bytes(path: Path, data: bytes) -> None:
    """Keep a content-addressed last-known-good copy without overwriting one."""
    try:
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            return
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    digest = hashlib.sha256(data).hexdigest()
    history = path.parent / ".history" / path.name
    history.mkdir(parents=True, exist_ok=True)
    archived = history / f"{digest}.json"
    if archived.exists():
        return
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{digest}.", suffix=".tmp", dir=history)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, archived)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: str | os.PathLike, payload: dict[str, Any]) -> Path:
    """Validate, fsync, and atomically replace a JSON object.

    Both the prior valid artifact and the newly committed artifact are retained
    by content hash under a hidden sibling history directory. If serialization,
    validation, writing, or replacement fails, the canonical file is untouched.
    """
    if not isinstance(payload, dict):
        raise TypeError("primary JSON artifact root must be an object")
    target = Path(path)
    data = (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n") \
        .encode("utf-8")
    checked = json.loads(data)
    if not isinstance(checked, dict):
        raise ValueError("serialized JSON artifact root is not an object")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        prior = target.read_bytes()
    except FileNotFoundError:
        prior = None
    if prior is not None:
        try:
            _archive_bytes(target, prior)
        except Exception:  # noqa: BLE001 - history is best-effort only
            pass

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Re-read the exact bytes that will be promoted; a short write or disk
        # corruption cannot replace the last usable assessment input.
        verified = json.loads(tmp.read_text(encoding="utf-8"))
        if not isinstance(verified, dict):
            raise ValueError("temporary JSON artifact failed validation")
        os.chmod(
            tmp, (target.stat().st_mode & 0o777) if target.exists() else 0o644)
        os.replace(tmp, target)
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:  # noqa: BLE001 - commit already succeeded
            pass
        try:
            _archive_bytes(target, data)
        except OSError:
            pass
        return target
    finally:
        if tmp.exists():
            tmp.unlink()
