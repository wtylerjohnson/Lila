"""Atomic file replacement — the ONE copy (2026-07-11).

Reports, QA sidecars, and approval artifacts are crash-durability surfaces:
an interrupted build must never leave a torn file where a prior good one
stood. The review of the gate build found this scaffold copy-pasted three
times; durability fixes now land here or nowhere.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any


def atomic_write_text(path: str, text: str) -> None:
    """Replace one text artifact atomically, preserving any prior good file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.",
                               suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        mode = (os.stat(path).st_mode & 0o777) if os.path.exists(path) else 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: str, payload: Any, *, indent: int = 2):
    """Commit operational JSON through the validated, historical writer.

    ``indent`` remains in this compatibility surface for existing callers;
    primary JSON artifacts use the canonical formatting and content-addressed
    history owned by :mod:`tools.artifacts`.
    """
    del indent
    from tools.artifacts import atomic_write_json as _write_primary_json
    return _write_primary_json(path, payload)
