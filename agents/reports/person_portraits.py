"""Offline, evidence-bound portrait resolution for Signal Board people.

The report renderer never searches the web.  It may embed only:

* a client-scoped presentation override bound to an exact person/source claim;
* an explicit local asset whose bytes, portrait source URL, and SHA256 travel
  with the stored person record; or
* the same shape from ``data/reference/portraits/index.json``.

Anything else becomes an initials placeholder.  In particular, a name match
alone is never enough to select a face.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from agents.reports.signal_board_presentation import (
    logo_identity,
    person_identity_label,
    resolve_logo_override,
    validate_logo_bytes,
)

_ROOT = Path(__file__).resolve().parents[2]
_CATALOG = Path("data/reference/portraits/index.json")
_ALLOWED_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class PortraitResolution:
    """One exact render decision, including why no image was used."""

    identity_label: str
    identity: str
    data_uri: str = ""
    portrait_source_url: str = ""
    provenance: str = "PORTRAIT NOT SOURCED"
    issue: str = ""


def _https_url(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.lower().startswith("https://") else ""


def _identity_parts(row: dict) -> tuple[str, str, str, str]:
    name = str(row.get("name") or "").strip()
    organization = str(
        row.get("organization") or row.get("agency") or "").strip()
    source_system = str(row.get("source_system") or "").strip()
    source_record = str(
        row.get("source_record_id")
        or row.get("opp_notice_guid")
        or row.get("bio_url")
        or row.get("opp_url")
        or ""
    ).strip()
    return name, organization, source_system, source_record


def identity_label_for(row: dict) -> str:
    """Return the exact, namesake-safe claim for a rendered person row."""
    return person_identity_label(*_identity_parts(row))


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _asset_data(
    asset_value: Any,
    expected_sha: Any,
    *,
    client_name: str,
    root: Path,
) -> tuple[str, str]:
    """Read one hash-pinned local portrait from the two allowed asset jails."""
    if not isinstance(asset_value, str) or not asset_value.strip():
        return "", "portrait asset is missing"
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        return "", "portrait asset SHA256 is missing or invalid"
    relative = Path(asset_value)
    if relative.is_absolute() or ".." in relative.parts:
        return "", "portrait asset path is unsafe"
    candidate = root / relative
    from tools.capability import _slug

    allowed = (
        root / "data" / "reference" / "portraits",
        root / "clients" / _slug(client_name) / "assets" / "portraits",
    )
    try:
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return "", "portrait asset path may not contain symlinks"
        resolved = candidate.resolve(strict=True)
        if not any(_within(resolved, base.resolve()) for base in allowed):
            return "", "portrait asset escapes its allowed directory"
        raw = resolved.read_bytes()
    except OSError:
        return "", "portrait asset is unreadable"
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != expected_sha.lower():
        return "", "portrait asset SHA256 does not match"
    try:
        media_type, _extension = validate_logo_bytes(raw)
    except ValueError as exc:
        return "", str(exc).replace("logo", "portrait")
    expected_media = _ALLOWED_MEDIA.get(resolved.suffix.lower())
    if expected_media != media_type:
        return "", "portrait asset extension does not match its bytes"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{media_type};base64,{encoded}", ""


def _catalog_rows(root: Path) -> list[dict]:
    path = root / _CATALOG
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return []
    rows = payload.get("portraits")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _matching_catalog_rows(row: dict, root: Path) -> list[dict]:
    claim = identity_label_for(row)
    matches = []
    for candidate in _catalog_rows(root):
        try:
            candidate_claim = person_identity_label(
                str(candidate.get("name") or ""),
                str(candidate.get("organization") or ""),
                str(candidate.get("source_system") or ""),
                str(candidate.get("source_record_id") or ""),
            )
        except ValueError:
            continue
        if candidate_claim == claim:
            matches.append(candidate)
    return sorted(
        matches,
        key=lambda item: json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def resolve_portrait(
    client_name: str,
    row: dict,
    *,
    root: Optional[str | Path] = None,
) -> PortraitResolution:
    """Resolve one person portrait without a network call.

    Invalid or ambiguous automatic evidence fails to the editable initials
    placeholder and carries an internal issue; it never guesses.
    """
    repo = Path(root).resolve() if root is not None else _ROOT
    try:
        claim = identity_label_for(row)
        identity = logo_identity(client_name, "person", claim)
    except ValueError as exc:
        return PortraitResolution(
            identity_label="", identity="", issue=str(exc))

    override = resolve_logo_override(
        client_name, kind="person", label=claim, root=repo)
    if override:
        return PortraitResolution(
            identity_label=claim,
            identity=identity,
            data_uri=override,
            provenance="OPERATOR-SUPPLIED PORTRAIT",
        )

    explicit_asset = row.get("portrait_asset")
    if explicit_asset:
        source_url = _https_url(row.get("portrait_source_url"))
        if not source_url:
            return PortraitResolution(
                identity_label=claim,
                identity=identity,
                issue="portrait source URL is missing or is not HTTPS",
            )
        data_uri, issue = _asset_data(
            explicit_asset,
            row.get("portrait_sha256"),
            client_name=client_name,
            root=repo,
        )
        return PortraitResolution(
            identity_label=claim,
            identity=identity,
            data_uri=data_uri,
            portrait_source_url=source_url if data_uri else "",
            provenance="OFFICIAL PORTRAIT" if data_uri else "PORTRAIT NOT SOURCED",
            issue=issue,
        )

    matches = _matching_catalog_rows(row, repo)
    if len(matches) > 1:
        distinct = {
            (item.get("asset"), item.get("sha256"),
             item.get("portrait_source_url"))
            for item in matches
        }
        if len(distinct) > 1:
            return PortraitResolution(
                identity_label=claim,
                identity=identity,
                issue="portrait catalog has conflicting exact-match rows",
            )
    if matches:
        matched = matches[0]
        source_url = _https_url(matched.get("portrait_source_url"))
        if not source_url:
            return PortraitResolution(
                identity_label=claim,
                identity=identity,
                issue="portrait catalog source URL is missing or is not HTTPS",
            )
        data_uri, issue = _asset_data(
            matched.get("asset"),
            matched.get("sha256"),
            client_name=client_name,
            root=repo,
        )
        return PortraitResolution(
            identity_label=claim,
            identity=identity,
            data_uri=data_uri,
            portrait_source_url=source_url if data_uri else "",
            provenance="OFFICIAL PORTRAIT" if data_uri else "PORTRAIT NOT SOURCED",
            issue=issue,
        )

    return PortraitResolution(identity_label=claim, identity=identity)
