"""Internal, content-addressed SAM collection artifacts; never review authority."""
from __future__ import annotations

import hashlib
import fcntl
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _object_lock(directory: Path, digest: str, *, shared: bool = False):
    locks = directory / 'object_locks'
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f'{digest}.lock').open('a+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _quarantine(directory: Path, path: Path) -> None:
    destination = directory / 'quarantine'
    destination.mkdir(parents=True, exist_ok=True)
    os.rename(path, destination / f'{path.name}.{uuid.uuid4().hex}')


def retain_bytes(directory: Path, payload: bytes) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"objects/{digest}.bin"
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with _object_lock(directory, digest):
            if path.exists() or path.is_symlink():
                if not path.is_symlink() and path.read_bytes() == payload:
                    return {"path": relative, "sha256": digest, "bytes": len(payload)}
                _quarantine(directory, path)
            try:
                os.link(temporary, path)
            except FileExistsError:
                # An uncooperative writer appeared after the locked check.
                raise ValueError('retained SAM object appeared during publication')
            except OSError:
                # Hardlinks are unavailable on some cache filesystems. Exclusive
                # creation and the shared read lock prevent partial-byte reads.
                try:
                    with path.open('xb') as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                except FileExistsError:
                    raise
                except OSError:
                    if path.exists():
                        _quarantine(directory, path)
                    raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"path": relative, "sha256": digest, "bytes": len(payload)}


def read_retained(directory: Path, receipt: dict) -> bytes:
    """Resolve only this cache's hash-addressed objects, not supplied paths."""
    if not isinstance(receipt, dict):
        raise ValueError('invalid retained SAM receipt')
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid retained SAM digest")
    relative = f"objects/{digest}.bin"
    if receipt.get("path") != relative:
        raise ValueError("invalid retained SAM locator")
    path = directory / relative
    if path.resolve().parent != (directory / "objects").resolve():
        raise ValueError("retained SAM object escaped cache")
    with _object_lock(directory, digest, shared=True):
        payload = path.read_bytes()
    if len(payload) != receipt.get("bytes") or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("retained SAM bytes changed")
    return payload
