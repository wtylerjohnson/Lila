"""Client onboarding: the Command Center path from a name to sweep-ready.

The three files the pipeline requires (clients/<slug>/profile.json,
capability_taxonomy.json, engagement_scope.json) are written ONLY through
the schemas and loaders the runners themselves use: ClientProfile plus the
require_profile populated-gate, CapabilityTaxonomy, EngagementScope. The
UI never duplicates validation logic; validator error text surfaces
verbatim. A saved client is by definition sweep-ready: every provided
payload validates before any byte lands, writes are atomic, and the state
is re-proven from disk through the true runner loaders afterward.

Concurrency: saves are compare-and-swap over each file's current SHA-256
(the native-mutation pattern); a hand edit between load and save is a 409,
never a silent clobber.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILE_KINDS = ("profile", "taxonomy", "scope")
_FILENAMES = {
    "profile": "profile.json",
    "taxonomy": "capability_taxonomy.json",
    "scope": "engagement_scope.json",
}


class OnboardingValidationError(ValueError):
    """Per-file validator failures, verbatim."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


class OnboardingConflict(RuntimeError):
    """A file changed since the caller loaded it (CAS mismatch)."""


def _slug(name: str) -> str:
    from tools.capability import _slug as slug
    return slug(name)


def file_path(client_name: str, kind: str) -> str:
    return os.path.join(_ROOT, "clients", _slug(client_name),
                        _FILENAMES[kind])


def _sha(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _validate_one(client_name: str, kind: str, payload: dict) -> Optional[str]:
    """One payload through the runner's own schema. Returns the validator's
    error text verbatim, or None when valid."""
    try:
        if kind == "profile":
            from tools.capability import ClientProfile
            profile = ClientProfile(**payload)
            if not profile.is_populated():
                return ("capability_terms.core and naics_boundary are "
                        "required (the require_profile gate: no profile, "
                        "no sweep)")
        elif kind == "taxonomy":
            from tools.relevance.taxonomy import CapabilityTaxonomy
            CapabilityTaxonomy(**payload)
        elif kind == "scope":
            from tools.relevance.scope import EngagementScope
            EngagementScope(**payload)
        else:
            return f"unknown file kind {kind!r}"
    except Exception as e:  # noqa: BLE001 - the validator's text IS the contract
        return str(e)
    return None


def validate_payloads(client_name: str,
                      payloads: dict[str, dict]) -> dict[str, str]:
    """Every provided payload through its runner schema; kind -> error."""
    errors: dict[str, str] = {}
    for kind, payload in payloads.items():
        if kind not in FILE_KINDS:
            errors[kind] = f"unknown file kind {kind!r}"
            continue
        if not isinstance(payload, dict):
            errors[kind] = "payload must be a JSON object"
            continue
        err = _validate_one(client_name, kind, payload)
        if err:
            errors[kind] = err
    return errors


def _disk_state(client_name: str) -> dict:
    """Per-file disk truth plus the runner loaders' verdicts, from disk."""
    out: dict[str, Any] = {"client_name": client_name,
                           "slug": _slug(client_name), "files": {}}
    for kind in FILE_KINDS:
        path = file_path(client_name, kind)
        row: dict[str, Any] = {"exists": os.path.exists(path),
                               "path": os.path.relpath(path, _ROOT),
                               "sha256": _sha(path), "raw": None,
                               "valid": False, "error": None}
        if row["exists"]:
            try:
                with open(path, encoding="utf-8") as f:
                    row["raw"] = json.load(f)
            except ValueError as e:
                row["error"] = f"file is not valid JSON: {e}"
        if isinstance(row["raw"], dict):
            err = _validate_one(client_name, kind, row["raw"])
            row["valid"] = err is None
            row["error"] = err
        out["files"][kind] = row

    # the exact sweep gate, from disk: sweep_ready is the runners' verdict,
    # never this module's opinion
    try:
        from tools.capability import require_profile
        require_profile(client_name)
        out["sweep_ready"] = True
        out["sweep_gate_error"] = None
    except Exception as e:  # noqa: BLE001 - the gate's text IS the contract
        out["sweep_ready"] = False
        out["sweep_gate_error"] = str(e)
    try:
        from tools.relevance.taxonomy import load_taxonomy
        tax = load_taxonomy(client_name)
        out["taxonomy_loaded"] = tax is not None
        out["taxonomy_version"] = tax.version if tax else None
    except Exception as e:  # noqa: BLE001
        out["taxonomy_loaded"] = False
        out["taxonomy_error"] = str(e)
    try:
        from tools.relevance.scope import load_engagement_scope
        scope = load_engagement_scope(client_name)
        out["scope_loaded"] = scope is not None
        out["scope_preset"] = getattr(scope, "preset", None) if scope else None
        out["scope_unscoped"] = scope is None
        # client view law (2026-07-18): the scope line renders read-only
        # resolver truth; the count is never recomputed client-side
        out["scope_basis"] = scope.resolved_basis if scope else None
        out["scope_departments"] = (
            len(scope.resolved_departments)
            if scope is not None and not scope.unbounded else None)
        out["scope_excluded"] = (
            list(scope.excluded_departments) if scope is not None else [])
    except Exception as e:  # noqa: BLE001
        out["scope_loaded"] = False
        out["scope_error"] = str(e)
    return out


def load_state(client_name: str) -> dict:
    return _disk_state(client_name)


def save(client_name: str, payloads: dict[str, dict],
         expected_shas: Optional[dict[str, Optional[str]]] = None) -> dict:
    """Validate everything first; refuse on any error; CAS per file; write
    atomically; re-prove from disk. Partial writes cannot happen: the first
    byte lands only after every provided payload validates."""
    if not payloads:
        raise OnboardingValidationError({"request": "no file payloads provided"})
    errors = validate_payloads(client_name, payloads)
    if errors:
        raise OnboardingValidationError(errors)

    if expected_shas is not None:
        for kind in payloads:
            expected = expected_shas.get(kind)
            current = _sha(file_path(client_name, kind))
            if expected != current:
                raise OnboardingConflict(
                    f"{_FILENAMES[kind]} changed since it was loaded "
                    f"(expected {str(expected)[:12]}, found "
                    f"{str(current)[:12]}); reload before saving")

    from tools.atomic_io import atomic_write_text
    slug_dir = os.path.dirname(file_path(client_name, "profile"))
    os.makedirs(slug_dir, exist_ok=True)
    for kind, payload in payloads.items():
        atomic_write_text(
            file_path(client_name, kind),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return _disk_state(client_name)
