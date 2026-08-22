"""Sectioned compose cache (P2, 2026-07-11) — one owner, both report paths.

The full-federal build spends ~10 minutes in one monolithic deliberate over
the whole fact pack, re-paid on every press even when the evidence is
unchanged. This module gives compose calls resume-never-rebuy economics at
SECTION granularity: each section keys on a canonical digest of exactly the
inputs that shape it (its fact subset, the shared review, the directive, the
schema version). Unchanged inputs -> disk hit, zero LLM spend.

run_agency_report grew its own single-call content cache first; this module
generalizes that pattern so both paths share one implementation (single-owner
doctrine). Cache entries are schema-validated on load — a stale or corrupt
entry is a MISS, never a crash and never an unvalidated render input.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from tools.atomic_io import atomic_write_text

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "compose_cache"
CACHE_SCHEMA_VERSION = 1


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_COMPOSE_CACHE_DIR", str(_DEFAULT_DIR)))


def canonical_digest(value: Any) -> str:
    """Stable digest of any JSON-able input (pydantic models included)."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def section_key(*, client: str, scope: str, section: str,
                inputs_digest: str, schema_version: int = CACHE_SCHEMA_VERSION
                ) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in client).strip("_").lower()
    return f"{slug}.{scope}.{section}.v{schema_version}.{inputs_digest[:24]}"


def _path_for(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


def load_section(key: str, validator: Callable[[dict], Any]) -> Optional[Any]:
    """Validated cache read: returns the validated object or None (miss).
    Any read/parse/validation failure is a miss — stale entries can never
    reach a render unvalidated."""
    try:
        with open(_path_for(key), encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        return validator(payload.get("content"))
    except Exception:  # noqa: BLE001 — schema drift invalidates the entry
        return None


def save_section(key: str, content: Any, *, meta: Optional[dict] = None) -> None:
    payload = {
        "cache_schema": CACHE_SCHEMA_VERSION,
        "key": key,
        "meta": meta or {},
        "content": (content.model_dump(mode="json")
                    if hasattr(content, "model_dump") else content),
    }
    os.makedirs(_cache_dir(), exist_ok=True)
    atomic_write_text(str(_path_for(key)),
                      json.dumps(payload, indent=1, ensure_ascii=False))


def invalidate(key: str) -> bool:
    try:
        os.remove(_path_for(key))
        return True
    except OSError:
        return False


def identity_token(projection: Any) -> Optional[str]:
    """Strict-run contribution to compose/cache identity (Cycle 4, 2026-07-12).

    Maps a resolved LiveReportProjection onto the one token cache keys carry:
    CURRENT -> the immutable run id (a pointer swap re-prices compose exactly
    once); INVALID -> 'strict:invalid' (held-closed lanes can never share
    prose with legacy truth or with any specific run); ABSENT/None -> None,
    and the caller must then OMIT the key entirely so every pre-cutover
    digest stays byte-identical (dormancy).
    """
    if projection is None:
        return None
    from agents.assess.live_report import LiveReportState
    if projection.state == LiveReportState.CURRENT:
        return projection.run_id
    if projection.state == LiveReportState.INVALID:
        return "strict:invalid"
    return None


def unresolved_token(client_name: str, designator: Optional[str]) -> Optional[str]:
    """Fail-closed token for a press whose strict resolution ERRORED.

    A pointer file on disk means strict state governs this scope, so an
    unresolvable projection must still bust caches and can never pair with a
    pointer run id ('strict:unresolved'). No pointer file means a legacy
    client, whose transient read error must not re-price an unchanged
    compose (None).
    """
    try:
        from agents.assess.ledger import current_assess_pointer_path
        pointer = current_assess_pointer_path(client_name, designator or "all")
        return "strict:unresolved" if pointer.exists() else None
    except Exception:  # noqa: BLE001 - unknown strict state is strict state
        return "strict:unresolved"


def compose_identity_token(client_name: str, searches: Optional[dict],
                           profile: Any, *,
                           designator: Optional[str] = None) -> Optional[str]:
    """Resolve the current strict projection and return its cache token.

    Convenience for callers that need only the token (the agency runner);
    callers that also consume the projection resolve it themselves and use
    :func:`identity_token` / :func:`unresolved_token` directly, so one press
    observes one strict state.
    """
    try:
        from agents.assess.live_report import resolve_current_live_report
        projection = resolve_current_live_report(
            client_name, searches or {}, profile)
        return identity_token(projection)
    except Exception:  # noqa: BLE001 - identity must decide, never crash a press
        return unresolved_token(client_name, designator)


def cached_call(*, key: str, validator: Callable[[dict], Any],
                producer: Callable[[], Any],
                meta: Optional[dict] = None) -> tuple[Any, bool]:
    """(result, cache_hit). The producer runs ONLY on a miss; its result is
    persisted before returning, so a crash after an expensive compose never
    re-pays it. Producers that raise leave no cache entry behind."""
    hit = load_section(key, validator)
    if hit is not None:
        return hit, True
    fresh = producer()
    save_section(key, fresh, meta=meta)
    return fresh, False
