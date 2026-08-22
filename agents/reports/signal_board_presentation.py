"""Client-scoped presentation overrides for Signal Board organization marks.

Evidence and composition artifacts never own visual substitutions.  An
operator-uploaded logo is stored beneath the client's assets directory and a
small presentation manifest points at its exact, content-addressed bytes.
Renderers stay offline and deterministic: they either embed the validated
override or continue to the normal cached-mark/text fallback.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Optional
import unicodedata


SCHEMA_VERSION = 1
MAX_LOGO_BYTES = 2 * 1024 * 1024
DEFAULT_LOGO_SIZE_PERCENT = 100
MIN_LOGO_SIZE_PERCENT = 50
MAX_LOGO_SIZE_PERCENT = 200
LOGO_SIZE_STEP = 5
DEFAULT_HEADER_COMPANION_TEXT = "FEDERAL OPPORTUNITY PRE-ASSESSMENT"
MAX_HEADER_COMPANION_CHARS = 64
_ROOT = Path(__file__).resolve().parents[2]
_LOCK = threading.Lock()
_RASTER_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}
_ENTRY_FIELDS = {
    "kind", "label", "asset", "media_type", "sha256", "updated_at",
}


def _repo_root(root: Optional[str | Path]) -> Path:
    return Path(root).resolve() if root is not None else _ROOT


def presentation_path(
    client_name: str, *, root: Optional[str | Path] = None,
) -> Path:
    """The presentation manifest for one canonical client slug."""
    from tools.capability import _slug

    return (_repo_root(root) / "clients" / _slug(client_name)
            / "signal_board_presentation.json")


def logo_identity(client_name: str, kind: str, label: str) -> str:
    """Stable manifest identity for an editable report image.

    ``person`` labels are canonical evidence claims produced by
    :func:`person_identity_label`, not display names.  Hashing the full
    organization + source-record claim prevents two people with the same name
    from sharing an operator portrait by accident.
    """
    from tools.brand_marks import company_slug
    from tools.capability import _slug

    if kind == "client":
        return f"client:{_slug(client_name)}"
    if kind == "company":
        if not str(label or "").strip():
            raise ValueError("company logo label is required")
        return f"company:{company_slug(label)}"
    if kind == "agency":
        if not str(label or "").strip():
            raise ValueError("agency logo label is required")
        # Keep the same curated component -> department resolver used by the
        # renderer; this lazy import avoids a module-import cycle.
        from agents.reports.report_assets import _seal_key

        key = _seal_key(str(label)) or company_slug(str(label))
        return f"agency:{key}"
    if kind == "person":
        claim = _validate_person_identity_label(label)
        digest = hashlib.sha256(claim.encode("utf-8")).hexdigest()[:24]
        return f"person:{digest}"
    raise ValueError("logo kind must be client, company, agency, or person")


def person_identity_label(
    name: str,
    organization: str,
    source_system: str,
    source_record: str,
) -> str:
    """Canonical evidence claim carried in an inert person target.

    A display name alone is never a portrait identity.  The exact official
    record (SAM notice or official biography URL) and organization travel with
    it, so a manual drop cannot silently bleed onto a namesake's card.
    """
    values = []
    for field, value in (
        ("name", name),
        ("organization", organization),
        ("source_system", source_system),
        ("source_record", source_record),
    ):
        if not isinstance(value, str):
            raise ValueError(f"person {field} must be a string")
        normalized = unicodedata.normalize("NFKC", value)
        text = " ".join(normalized.strip().split())
        if field != "organization" and not text:
            raise ValueError(f"person {field} is required")
        if any(unicodedata.category(char).startswith("C") for char in text):
            raise ValueError(f"person {field} contains unsupported characters")
        values.append(text)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _validate_person_identity_label(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("person identity claim is required")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("person identity claim is invalid") from exc
    if not isinstance(decoded, list) or len(decoded) != 4:
        raise ValueError("person identity claim is invalid")
    try:
        canonical = person_identity_label(*decoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("person identity claim is invalid") from exc
    if canonical != value:
        raise ValueError("person identity claim is not canonical")
    return canonical


def _empty(client_name: str) -> dict[str, Any]:
    from tools.capability import _slug

    return {
        "schema_version": SCHEMA_VERSION,
        "client_slug": _slug(client_name),
        "header_companion_text": DEFAULT_HEADER_COMPANION_TEXT,
        "logo_sizes": {},
        "logo_overrides": {},
    }


def validate_header_companion_text(value: Any) -> str:
    """Canonical, single-line public copy for the client-logo lockup."""
    if not isinstance(value, str):
        raise ValueError("header companion text must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    if any(char.isspace() and char != " " for char in normalized):
        raise ValueError("header companion text must be a single line")
    if any(unicodedata.category(char).startswith("C")
           for char in normalized):
        raise ValueError("header companion text contains unsupported characters")
    text = " ".join(normalized.strip().split())
    if not text or not any(char.isalnum() for char in text):
        raise ValueError("header companion text must contain visible words")
    if len(text) > MAX_HEADER_COMPANION_CHARS:
        raise ValueError(
            "header companion text exceeds the 64-character limit")
    if "<" in text or ">" in text:
        raise ValueError("header companion text may not contain markup")
    return text


def validate_logo_size_percent(value: Any) -> int:
    """Canonical operator-selected mark size as a bounded percentage."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("logo size must be an integer percentage")
    if not MIN_LOGO_SIZE_PERCENT <= value <= MAX_LOGO_SIZE_PERCENT:
        raise ValueError(
            "logo size must be between 50 and 200 percent")
    if value % LOGO_SIZE_STEP:
        raise ValueError("logo size must use 5-percent increments")
    return value


def _validate_payload(client_name: str, payload: Any) -> dict[str, Any]:
    """Validate one already-decoded presentation manifest."""
    expected = _empty(client_name)
    if not isinstance(payload, dict):
        raise ValueError("presentation manifest root is not an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("presentation manifest schema is unsupported")
    if payload.get("client_slug") != expected["client_slug"]:
        raise ValueError("presentation manifest belongs to another client")
    if not isinstance(payload.get("logo_overrides"), dict):
        raise ValueError("presentation logo_overrides is not an object")
    validated = dict(payload)
    sizes = payload.get("logo_sizes", {})
    if not isinstance(sizes, dict):
        raise ValueError("presentation logo_sizes is not an object")
    validated_sizes = {}
    for identity, percent in sizes.items():
        if (not isinstance(identity, str)
                or re.fullmatch(
                    r"(?:client|company|agency|person):[\w]+", identity)
                is None):
            raise ValueError("presentation logo size identity is invalid")
        validated_sizes[identity] = validate_logo_size_percent(percent)
    validated["logo_sizes"] = validated_sizes
    validated["header_companion_text"] = validate_header_companion_text(
        payload.get(
            "header_companion_text", DEFAULT_HEADER_COMPANION_TEXT))
    return validated


def load_presentation(
    client_name: str, *, root: Optional[str | Path] = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Load one exact manifest; malformed presentation data never renders."""
    path = presentation_path(client_name, root=root)
    if not path.exists():
        return _empty(client_name)
    try:
        return _validate_payload(
            client_name, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        if strict:
            raise
        return _empty(client_name)


def _validate_raster(raw: bytes) -> tuple[str, str]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except Exception as exc:  # noqa: BLE001 - invalid images share one refusal
        raise ValueError("logo is not a valid supported image") from exc
    if image_format not in _RASTER_FORMATS:
        raise ValueError("logo must be PNG, JPEG, or WebP")
    if min(width, height) < 8 or max(width, height) > 8192:
        raise ValueError("logo dimensions must be between 8 and 8192 pixels")
    return _RASTER_FORMATS[image_format]


def validate_logo_bytes(raw: bytes) -> tuple[str, str]:
    """Return ``(media_type, canonical_extension)`` for safe logo bytes."""
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("logo file is empty")
    if len(raw) > MAX_LOGO_BYTES:
        raise ValueError("logo file exceeds the 2 MiB limit")
    probe = raw.lstrip()[:256].lower()
    if probe.startswith(b"<svg") or b"<svg" in probe:
        raise ValueError(
            "SVG uploads are not accepted; export the mark as PNG or WebP")
    return _validate_raster(raw)


def _assert_no_symlink_components(path: Path, root: Path) -> None:
    """Reject any presentation path that traverses a symlink below root."""
    root = root.resolve()
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as exc:
        raise ValueError("presentation path escapes the repository root") from exc
    current = root
    for part in relative.parts:
        if part in {".", ".."}:
            raise ValueError("presentation path contains an unsafe segment")
        current = current / part
        if current.is_symlink():
            raise ValueError("presentation paths may not contain symlinks")


@contextmanager
def _presentation_lock(path: Path, root: Path):
    """Serialize manifest read-modify-write across Command Center workers."""
    _assert_no_symlink_components(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(path, root)
    lock_path = path.parent / ".signal_board_presentation.lock"
    _assert_no_symlink_components(lock_path, root)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _entry_bytes(
    client_name: str,
    identity: str,
    entry: Any,
    *,
    root: Optional[str | Path] = None,
) -> tuple[bytes, str]:
    """Validate and read one exact manifest entry inside its client jail."""
    if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
        raise ValueError("presentation logo entry fields are invalid")
    kind = entry.get("kind")
    label = entry.get("label")
    if (not isinstance(kind, str) or not isinstance(label, str)
            or logo_identity(client_name, kind, label) != identity):
        raise ValueError("presentation logo entry identity is invalid")
    repo = _repo_root(root)
    manifest = presentation_path(client_name, root=repo)
    base = manifest.parent
    allowed = base / "assets" / "presentation"
    asset_value = entry.get("asset")
    if not isinstance(asset_value, str) or not asset_value:
        raise ValueError("presentation logo asset path is invalid")
    asset = base / asset_value
    try:
        asset.absolute().relative_to(allowed.absolute())
    except ValueError as exc:
        raise ValueError("presentation logo asset escapes its client jail") from exc
    _assert_no_symlink_components(manifest, repo)
    _assert_no_symlink_components(asset, repo)
    base_resolved = base.resolve()
    allowed_resolved = allowed.resolve()
    asset_resolved = asset.resolve(strict=True)
    try:
        asset_resolved.relative_to(base_resolved)
        asset_resolved.relative_to(allowed_resolved)
    except ValueError as exc:
        raise ValueError("presentation logo asset escapes its client jail") from exc
    raw = asset_resolved.read_bytes()
    media_type, _extension = validate_logo_bytes(raw)
    if (media_type != entry.get("media_type")
            or hashlib.sha256(raw).hexdigest() != entry.get("sha256")):
        raise ValueError("presentation logo asset hash is invalid")
    return raw, media_type


def presentation_digest(
    client_name: str, *, root: Optional[str | Path] = None,
) -> str:
    """Hash the exact manifest and referenced bytes used by a board press."""
    repo = _repo_root(root)
    path = presentation_path(client_name, root=repo)
    _assert_no_symlink_components(path, repo)
    if not path.exists():
        from tools.capability import _slug

        marker = f"signal-board-presentation:none:v1:{_slug(client_name)}\n"
        return hashlib.sha256(marker.encode("utf-8")).hexdigest()
    raw_manifest = path.read_bytes()
    # Parse the exact bytes entering the digest. Re-reading the manifest here
    # would permit a concurrent atomic publish to combine manifest A's bytes
    # with manifest B's asset references: a snapshot that never existed.
    payload = _validate_payload(
        client_name, json.loads(raw_manifest.decode("utf-8")))
    digest = hashlib.sha256()
    digest.update(b"signal-board-presentation:v1\0")
    digest.update(raw_manifest)
    for identity, entry in sorted(payload["logo_overrides"].items()):
        if not isinstance(identity, str) or not identity:
            raise ValueError("presentation logo identity is invalid")
        raw, _media_type = _entry_bytes(
            client_name, identity, entry, root=repo)
        digest.update(b"\0asset\0")
        digest.update(identity.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    from tools.atomic_io import atomic_write_text

    atomic_write_text(
        str(path),
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
    )


def store_logo_override(
    client_name: str,
    *,
    kind: str,
    label: str,
    raw: bytes,
    root: Optional[str | Path] = None,
    updated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Atomically publish one content-addressed presentation override.

    The asset lands before the manifest pointer.  An interrupted publication
    can therefore leave only an unreachable asset, never a torn active logo.
    """
    media_type, extension = validate_logo_bytes(raw)
    identity = logo_identity(client_name, kind, label)
    entity = identity.split(":", 1)[1]
    digest = hashlib.sha256(raw).hexdigest()
    repo = _repo_root(root)
    manifest_path = presentation_path(client_name, root=repo)
    base = manifest_path.parent
    asset = (base / "assets" / "presentation" / kind / entity
             / f"{digest}{extension}")
    rel_asset = asset.relative_to(base).as_posix()
    moment = updated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("updated_at must be timezone-aware")
    entry = {
        "kind": kind,
        "label": str(label),
        "asset": rel_asset,
        "media_type": media_type,
        "sha256": digest,
        "updated_at": moment.isoformat().replace("+00:00", "Z"),
    }
    with _LOCK:
        with _presentation_lock(manifest_path, repo):
            _assert_no_symlink_components(asset, repo)
            payload = load_presentation(client_name, root=repo, strict=True)
            _atomic_write_bytes(asset, raw)
            overrides = dict(payload["logo_overrides"])
            overrides[identity] = entry
            payload["logo_overrides"] = overrides
            _write_manifest(manifest_path, payload)
    return {"identity": identity, **entry}


def store_header_companion_text(
    client_name: str,
    text: str,
    *,
    root: Optional[str | Path] = None,
) -> str:
    """Atomically publish client-scoped header companion copy."""
    value = validate_header_companion_text(text)
    repo = _repo_root(root)
    manifest_path = presentation_path(client_name, root=repo)
    with _LOCK:
        with _presentation_lock(manifest_path, repo):
            payload = load_presentation(client_name, root=repo, strict=True)
            payload["header_companion_text"] = value
            _write_manifest(manifest_path, payload)
    return value


def store_logo_size(
    client_name: str,
    *,
    kind: str,
    label: str,
    percent: int,
    root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Persist one identity-wide mark size without touching its image bytes."""
    value = validate_logo_size_percent(percent)
    identity = logo_identity(client_name, kind, label)
    repo = _repo_root(root)
    manifest_path = presentation_path(client_name, root=repo)
    with _LOCK:
        with _presentation_lock(manifest_path, repo):
            payload = load_presentation(client_name, root=repo, strict=True)
            sizes = dict(payload["logo_sizes"])
            if value == DEFAULT_LOGO_SIZE_PERCENT:
                sizes.pop(identity, None)
            else:
                sizes[identity] = value
            payload["logo_sizes"] = sizes
            _write_manifest(manifest_path, payload)
    return {"identity": identity, "percent": value}


def resolve_logo_sizes(
    client_name: Optional[str], *, root: Optional[str | Path] = None,
) -> dict[str, int]:
    """Validated identity-to-size map, or an empty map for normal sizing."""
    if not client_name:
        return {}
    try:
        payload = load_presentation(str(client_name), root=root)
        return dict(payload["logo_sizes"])
    except (OSError, TypeError, ValueError, KeyError):
        return {}


def resolve_header_companion_text(
    client_name: Optional[str], *, root: Optional[str | Path] = None,
) -> str:
    """Validated client-scoped header copy, or the canonical default."""
    if not client_name:
        return DEFAULT_HEADER_COMPANION_TEXT
    try:
        payload = load_presentation(str(client_name), root=root)
        return validate_header_companion_text(
            payload.get("header_companion_text"))
    except (OSError, TypeError, ValueError, KeyError):
        return DEFAULT_HEADER_COMPANION_TEXT


def resolve_logo_override(
    client_name: Optional[str],
    *,
    kind: str,
    label: str,
    root: Optional[str | Path] = None,
) -> str:
    """Validated data URI for one override, or ``''`` for normal fallback."""
    if not client_name:
        return ""
    try:
        identity = logo_identity(str(client_name), kind, label)
        payload = load_presentation(str(client_name), root=root)
        entry = payload["logo_overrides"].get(identity)
        if not isinstance(entry, dict):
            return ""
        if entry.get("kind") != kind:
            return ""
        raw, media_type = _entry_bytes(
            str(client_name), identity, entry, root=root)
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{media_type};base64,{encoded}"
    except (OSError, TypeError, ValueError, KeyError):
        return ""
