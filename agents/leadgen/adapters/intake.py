"""Cite Step 1 CompanyDossier / IdentityResolution without redesigning intake.

The merged intake model validates dossier identity and evidence references when
binding a client release. Legacy cite-only calls remain read compatible.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class IntakeCiteError(ValueError):
    """Fail-closed dossier cite. Not an Assess or lead-tier error."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def cite_profile(client_name: str) -> dict[str, str | None]:
    """Optional capability-profile cite. Missing profile is not fatal here."""

    from tools.capability import load_profile, profile_path

    name = _clean(client_name)
    path = profile_path(name) if name else ""
    owned = load_profile(name) if name else None
    if owned is None:
        return {
            "profile_path": None,
            "profile_client_name": None,
        }
    return {
        "profile_path": path or None,
        "profile_client_name": owned.client_name,
    }


def cite_dossier(path: str | Path | None, *, client_name: str | None = None) -> dict:
    """Load optional string cites from a dossier JSON if present.

    Supplying client_name validates the merged CompanyDossier contract.
    """

    empty = {
        "dossier_path": None,
        "dossier_schema_version": None,
        "identity_status": None,
    }
    if path is None or _clean(path) == "":
        return empty
    dossier = Path(path)
    if not dossier.is_file():
        raise IntakeCiteError(
            f"intake dossier path is not readable ({dossier})")
    try:
        payload: Any = json.loads(dossier.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IntakeCiteError(
            f"intake dossier is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise IntakeCiteError(
            "intake dossier must be a JSON object so it can be cited")
    if client_name is not None:
        from agents.intake.dossier import CompanyDossier
        try:
            model = CompanyDossier.model_validate(payload)
            if model.client_name.casefold() != client_name.casefold():
                raise ValueError("dossier belongs to a different client")
            if model.schema_version != "intake_dossier.v1" or not model.identity.is_bound:
                raise ValueError("dossier identity must be bound under intake_dossier.v1")
            ids = {e.evidence_id for e in model.evidence}
            if not ids or not model.offerings:
                raise ValueError("dossier requires evidence and offerings")
            for collection in (model.offerings, model.customers, model.competitors,
                               model.channels, model.capability_statements):
                for claim in collection:
                    if not set(claim.evidence_ids).issubset(ids):
                        raise ValueError("dossier claim has an unresolved evidence reference")
        except ValueError as exc:
            raise IntakeCiteError(f"invalid company dossier: {exc}") from exc
    return {
        "dossier_path": str(dossier),
        "dossier_schema_version": _first_string(payload, (
            "dossier_schema_version", "schema_version",
        )),
        "identity_status": _identity_status(payload),
    }


def _first_string(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _clean(payload.get(key))
        if value:
            return value
    return None


def _identity_status(payload: Mapping[str, Any]) -> str | None:
    direct = _first_string(payload, ("identity_status",))
    if direct:
        return direct
    identity = payload.get("identity") or payload.get("identity_resolution")
    if isinstance(identity, Mapping):
        return _first_string(identity, ("status", "identity_status"))
    return None
