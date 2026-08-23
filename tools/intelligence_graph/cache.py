"""Content-addressed persistence for expensive, deterministic computations.

Entries are addressed by a stable logical identity and validated by hashes of
their inputs, dependencies, and producer version.  A changed record therefore
invalidates its own entry without forcing a full-corpus research pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from tools.atomic_io import atomic_write_text

CACHE_SCHEMA_VERSION = "intelligence-cache-v1"
_SAFE_NAMESPACE = re.compile(r"[^a-z0-9_.-]+")


def canonicalize(value: Any) -> Any:
    """Return a JSON-stable representation of common operational values."""
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        items = [canonicalize(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(
            item, sort_keys=True, ensure_ascii=False, default=str))
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_hash(value: Any) -> str:
    data = json.dumps(
        canonicalize(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CacheResult:
    value: Any
    state: str
    reason: str
    entry_path: Path
    input_hash: str
    dependency_hash: str

    @property
    def reused(self) -> bool:
        return self.state == "hit"


class PersistentCache:
    """Atomic filesystem cache with explicit invalidation provenance."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    @staticmethod
    def _namespace(value: str) -> str:
        cleaned = _SAFE_NAMESPACE.sub("-", str(value).casefold()).strip("-.")
        if not cleaned:
            raise ValueError("cache namespace must not be empty")
        return cleaned

    def _entry_path(self, namespace: str, identity: str) -> Path:
        digest = stable_hash({"namespace": namespace, "identity": identity})
        return self.root / self._namespace(namespace) / digest[:2] / f"{digest}.json"

    @staticmethod
    def _load(path: Path) -> Optional[dict]:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(entry, dict):
            return None
        if entry.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        return entry

    def get_or_compute(
        self,
        namespace: str,
        identity: str,
        *,
        inputs: Any,
        dependencies: Any = None,
        producer: str,
        compute: Callable[[], Any],
        provenance: Optional[dict] = None,
    ) -> CacheResult:
        """Reuse one valid entry or recompute only this logical identity."""
        namespace = self._namespace(namespace)
        identity = str(identity)
        path = self._entry_path(namespace, identity)
        input_hash = stable_hash(inputs)
        dependency_hash = stable_hash(dependencies)
        prior = self._load(path)
        reason = "absent"
        if prior is not None:
            if prior.get("producer") != producer:
                reason = "producer_changed"
            elif prior.get("input_hash") != input_hash:
                reason = "input_changed"
            elif prior.get("dependency_hash") != dependency_hash:
                reason = "dependency_changed"
            else:
                return CacheResult(
                    value=prior.get("value"), state="hit", reason="reused",
                    entry_path=path, input_hash=input_hash,
                    dependency_hash=dependency_hash,
                )

        value = compute()
        entry = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "namespace": namespace,
            "identity": identity,
            "producer": producer,
            "input_hash": input_hash,
            "dependency_hash": dependency_hash,
            "provenance": canonicalize(provenance or {}),
            "value": canonicalize(value),
        }
        atomic_write_text(
            str(path), json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        return CacheResult(
            value=entry["value"], state="miss", reason=reason,
            entry_path=path, input_hash=input_hash,
            dependency_hash=dependency_hash,
        )

    def iter_entries(self, namespace: str) -> Iterable[dict]:
        folder = self.root / self._namespace(namespace)
        if not folder.exists():
            return
        for path in sorted(folder.glob("*/*.json")):
            entry = self._load(path)
            if entry is not None:
                yield entry

    def values(self, namespace: str, *, identity_prefix: str = "") -> list[Any]:
        return [
            entry.get("value")
            for entry in self.iter_entries(namespace)
            if str(entry.get("identity") or "").startswith(identity_prefix)
        ]

    def delete(self, namespace: str, identity: str) -> bool:
        """Delete one logical cache entry, returning whether it existed.

        Relationship-backed caches use this when a source edge is deleted.  A
        stale value is never retained merely because its former input is no
        longer present in the current corpus.
        """
        path = self._entry_path(self._namespace(namespace), str(identity))
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def prune(
        self,
        namespace: str,
        *,
        remove_when: Callable[[dict], bool],
    ) -> list[str]:
        """Remove entries selected by ``remove_when`` and return identities."""
        removed: list[str] = []
        for entry in list(self.iter_entries(namespace)):
            if not remove_when(entry):
                continue
            identity = str(entry.get("identity") or "")
            if identity and self.delete(namespace, identity):
                removed.append(identity)
        return sorted(removed)
