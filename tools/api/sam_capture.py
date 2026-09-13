"""Internal, content-addressed SAM collection artifacts; never review authority."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


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
        try:
            os.link(temporary, path)  # atomic publication without replacing old bytes
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError("retained SAM bytes changed")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"path": relative, "sha256": digest, "bytes": len(payload)}


def read_retained(directory: Path, receipt: dict) -> bytes:
    """Resolve only this cache's hash-addressed objects, not supplied paths."""
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid retained SAM digest")
    relative = f"objects/{digest}.bin"
    if receipt.get("path") != relative:
        raise ValueError("invalid retained SAM locator")
    path = directory / relative
    if path.resolve().parent != (directory / "objects").resolve():
        raise ValueError("retained SAM object escaped cache")
    payload = path.read_bytes()
    if len(payload) != receipt.get("bytes") or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("retained SAM bytes changed")
    return payload
