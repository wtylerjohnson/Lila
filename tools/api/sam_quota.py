"""SAM.gov call ledger — meter the scarcest resource in the system.

Every SAM-keyed endpoint (opportunities search, contract awards, entity
management, exclusions) draws from ONE api.data.gov daily quota of 10/day
per key. THAT CEILING IS PERMANENT: there is no role to request and none
should be proposed. The daily extract is the primary discovery path and
costs nothing; the metered API is a fallback that must fit in the pool. A
single pipeline day
can spend 25+ calls, so "SAM is flaky" is usually "SAM is exhausted."

This ledger counts every call locally (data/state/sam_calls.json), resets at
midnight, and is surfaced in healthchecks, the rack test, and search output —
so the budget is visible BEFORE it runs out, not after.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path

# 10 A DAY PER KEY, PERMANENTLY. This defaulted to 1000, which is the
# role-upgrade figure and there is no role. The guard therefore enforced a
# 2000-call pool against a real ceiling of 20 and never once fired: a single
# apexanalytix press pushed 28 calls out and hit HTTP 429 from sam.gov rather
# than from us. A guard that cannot stop anything is not a guard.
PER_KEY_DEFAULT = 10

_LOCK = threading.Lock()
_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "state" / "sam_calls.json"


def _path() -> Path:
    return Path(os.environ.get("LILA_SAM_LEDGER", str(_DEFAULT)))


def _load() -> dict:
    p = _path()
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get("date") != date.today().isoformat():
        data = {"date": date.today().isoformat(), "count": 0, "by": {}}
    return data


def note_call(purpose: str) -> int:
    """Record one SAM API call; returns today's running total."""
    with _LOCK:
        data = _load()
        data["count"] = int(data.get("count", 0)) + 1
        by = data.setdefault("by", {})
        by[purpose] = int(by.get(purpose, 0)) + 1
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return data["count"]


def calls_today() -> int:
    with _LOCK:
        return int(_load().get("count", 0))


def summary() -> str:
    with _LOCK:
        data = _load()
    by = ", ".join(f"{k} {v}" for k, v in sorted((data.get("by") or {}).items()))
    return (f"{data.get('count', 0)} SAM calls today"
            + (f" ({by})" if by else "")
            + f" · pool {daily_quota()}/day across {configured_keys()} key(s), "
              "which is permanent: the extract path is the primary and the "
              "metered API is the fallback")


def configured_keys() -> int:
    """How many SAM keys the environment carries (primary + _2)."""
    return len([key for key in (os.environ.get("SAM_GOV_API_KEY"),
                                os.environ.get("SAM_GOV_API_KEY_2")) if key])


def daily_quota() -> int:
    """The POOL's daily budget: per-key budget times configured keys.
    Per-key default 1,000 (registered role); override the per-key figure
    with LILA_SAM_DAILY_QUOTA (e.g. 10 while a role is pending)."""
    try:
        per_key = int(os.environ.get("LILA_SAM_DAILY_QUOTA", str(PER_KEY_DEFAULT)))
    except ValueError:
        per_key = PER_KEY_DEFAULT
    return per_key * max(1, configured_keys())


def remaining() -> int:
    return max(0, daily_quota() - calls_today())


def guard(purpose: str) -> bool:
    """True if another call fits the budget; warns once at 80%. Callers stop
    paginating (and mark the pull INCOMPLETE) when this returns False —
    stored results are never discarded on budget exhaustion (L17)."""
    with _LOCK:
        data = _load()
        used = int(data.get("count", 0))
        quota = daily_quota()
        if used >= quota:
            return False
        if used >= int(quota * 0.8) and not data.get("_warned80"):
            import sys as _sys
            print(f"[sam-quota] WARNING: {used}/{quota} daily SAM calls used "
                  f"(80% threshold) — {purpose} continuing until exhaustion",
                  file=_sys.stderr)
            data["_warned80"] = True
            _path().write_text(json.dumps(data))
        return True


def press_live_sam_enabled() -> bool:
    """THE single zero-SAM press switch (operator spec, 2026-08-03).

    Default OFF: the report press path never spends the permanent 10/day
    api.data.gov pool — the durable notice store and the daily extract
    serve everything a press reads. The apexanalytix press that pushed 28
    calls out of a 20-call pool is why this exists. ON is an explicit
    operator act (LILA_PRESS_LIVE_SAM=1) for a press that genuinely needs
    a live SAM confirmation pass. This flag governs the PRESS path only;
    the standalone search step keeps its own quota-guarded live behavior.
    """
    return os.environ.get("LILA_PRESS_LIVE_SAM", "").strip().lower() in (
        "1", "true", "on", "yes")
