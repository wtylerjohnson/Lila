"""ONE binding fingerprint for operator-approved evidence (2026-07-11).

The Assess approval gate and the Horizon gate both bind operator sign-off to
the exact evidence universe: gate scope, sweep artifact + contents, and the
complete capability profile. The review of the gate build found the two
implementations were verbatim copies already diverging on an edge case; this
module is now the single owner. Schema changes (a new bound input, a version
bump, canonicalization) happen HERE or nowhere, so the two gates can never
disagree about what reopens an approval.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

BINDING_VERSION = 1
REQUIRED_KEYS = frozenset({"version", "scope_designator", "sweep_artifact",
                           "sweep_sha256", "profile_sha256"})
KEY_LABELS = {
    "version": "binding version",
    "scope_designator": "search scope",
    "sweep_artifact": "sweep artifact",
    "sweep_sha256": "sweep evidence",
    "profile_sha256": "capability profile",
}


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def digest(value: Any) -> str:
    canonical = json.dumps(jsonable(value), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False,
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_binding(*, scope_designator: str, sweep_artifact: str,
                  sweep: Any, profile: Any) -> dict:
    """The deterministic five-key fingerprint both gates store and compare."""
    return {
        "version": BINDING_VERSION,
        "scope_designator": scope_designator,
        "sweep_artifact": sweep_artifact,
        "sweep_sha256": digest(sweep),
        "profile_sha256": digest(profile),
    }


def binding_drift(stored: Optional[dict], current: dict, *,
                  subject: str, remedy: str) -> list[str]:
    """Shared drift comparison: unbound/incomplete/changed, phrased per gate.

    subject: 'horizon'/'Assess' prefix for messages; remedy: what the operator
    does about it ('recompose it' / 'review and approve again')."""
    if not isinstance(stored, dict):
        return [f"{subject} artifact is legacy/unbound; {remedy}"]
    missing = sorted(REQUIRED_KEYS - set(stored))
    if missing:
        return [f"{subject} binding is incomplete (missing "
                + ", ".join(missing) + f"); {remedy}"]
    problems = []
    for key in sorted(REQUIRED_KEYS):
        if stored.get(key) != current.get(key):
            problems.append(f"{subject} {KEY_LABELS[key]} changed; {remedy}")
    return problems
