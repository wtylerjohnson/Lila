"""Frozen, portable inputs for deterministic LILA release compilation."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Optional

from agents.golden_press.external_product_contract import (
    CONTRACT_VERSION,
    OPERATOR_LOCKED_SLOT_SHA256,
)


RELEASE_SNAPSHOT_VERSION = "lila-release-snapshot.v1.2026-08-24"
LIVE_RELEASE_PURPOSE = "release_input"
REPRODUCIBILITY_FIXTURE_PURPOSE = "reproducibility_fixture_only"
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/]+[^\\/]+")
_FILE_URL = re.compile(r"file://", re.IGNORECASE)
_HTTP_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_POSIX_LOCAL_PATH = re.compile(
    r'''(?:^|["'\s(=:\[,])/(?:users|home|private|tmp|volumes)'''
    r'''(?:[\\/]|$|(?=["'\s)>\],;]))''',
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATH = re.compile(
    r'''(?:^|["'\s(=:\[,])[a-z]:[\\/]+''', re.IGNORECASE)
_WINDOWS_UNC_PATH = re.compile(
    r'''(?:^|["'\s(=:\[,])(?:\\{2,}|//)'''
    r'''[^\\/\s"'<>]+[\\/]+[^\\/\s"'<>]+''')
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


class ReleaseSnapshotError(ValueError):
    """A snapshot is incomplete, mutable, or internally inconsistent."""


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_sort_key(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contains_local_path(value: str | bytes) -> bool:
    """Return whether outgoing text exposes a host-local path.

    Public HTTP URLs are removed before inspection so an agency URL such as
    ``https://example.gov/tmp/notice`` is not mistaken for a workstation path.
    File URLs, POSIX workstation roots, Windows drive paths, and UNC shares
    remain local and are rejected by the release boundary.
    """
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    without_http_urls = _HTTP_URL.sub("", text)
    return any(pattern.search(without_http_urls) for pattern in (
        _FILE_URL,
        _POSIX_LOCAL_PATH,
        _WINDOWS_DRIVE_PATH,
        _WINDOWS_UNC_PATH,
    ))


def _portable_path(value: str, *, root: Optional[Path]) -> str:
    folded = value.casefold()
    if folded.startswith("file://"):
        raw = value[7:]
        if (raw and not raw.startswith(("/", "\\"))
                and not _WINDOWS_ABSOLUTE.match(raw)):
            raw = "//" + raw
    elif (value.startswith("/") or _WINDOWS_ABSOLUTE.match(value)
          or _WINDOWS_UNC.match(value)):
        raw = value
    else:
        return value

    if _WINDOWS_ABSOLUTE.match(raw) or _WINDOWS_UNC.match(raw):
        return f"local-artifact:{PureWindowsPath(raw).name or 'available'}"
    path = Path(raw)
    if root is not None:
        try:
            return path.resolve(strict=False).relative_to(
                root.resolve(strict=False)).as_posix()
        except (OSError, ValueError):
            pass
    return f"local-artifact:{path.name or 'available'}"


def canonicalize_release_value(
    value: Any, *, root: Optional[Path] = None,
) -> Any:
    """Return one deterministic, JSON-safe, host-portable representation."""
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name in normalized:
                raise ReleaseSnapshotError(
                    f"canonical mapping key collision: {name!r}")
            normalized[name] = canonicalize_release_value(item, root=root)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (set, frozenset)):
        rows = [canonicalize_release_value(item, root=root) for item in value]
        return sorted(rows, key=_json_sort_key)
    if isinstance(value, (list, tuple)):
        return [canonicalize_release_value(item, root=root) for item in value]
    if isinstance(value, Path):
        return _portable_path(str(value), root=root)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ReleaseSnapshotError("snapshot datetimes must be timezone aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReleaseSnapshotError("snapshot numbers must be finite")
        return value
    if isinstance(value, str):
        return _portable_path(value, root=root)
    if value is None or isinstance(value, (bool, int)):
        return value
    raise ReleaseSnapshotError(
        f"snapshot value is not JSON serializable: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(
        canonicalize_release_value(value), ensure_ascii=False, sort_keys=True,
        indent=2, allow_nan=False,
    ) + "\n").encode("utf-8")


def _aware_utc(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReleaseSnapshotError(f"{field} must be an ISO instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseSnapshotError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_slot_sha256(slots: Any) -> str:
    rows = []
    for raw in slots or ():
        if isinstance(raw, Mapping):
            number = raw.get("number")
            slot_id = raw.get("slot_id")
            heading = raw.get("heading")
        else:
            number = getattr(raw, "number", None)
            slot_id = getattr(raw, "slot_id", None)
            heading = getattr(raw, "heading", None)
        rows.append(f"{int(number)}|{str(slot_id)}|{' '.join(str(heading).split())}")
    return _sha_bytes(("\n".join(rows) + "\n").encode("utf-8"))


@dataclass(frozen=True)
class ReleaseSnapshot:
    """All semantic inputs needed by the clock-free release compiler."""

    schema_version: str
    purpose: str
    client_name: str
    slug: str
    business_as_of: str
    classification_as_of: str
    captured_at: str
    contract_version: str
    operator_locked_slot_sha256: str
    contract_slots: tuple[dict, ...]
    versions: dict
    authorization: dict
    profile: dict
    graph: dict
    market_map_inputs: dict
    render_assets: dict
    render_asset_receipts: dict
    evidence_pack_base64: str
    evidence_pack_sha256: str
    source_evidence_pack_base64: str
    source_evidence_pack_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != RELEASE_SNAPSHOT_VERSION:
            raise ReleaseSnapshotError(
                f"unsupported snapshot version: {self.schema_version}")
        if self.purpose not in {
                LIVE_RELEASE_PURPOSE, REPRODUCIBILITY_FIXTURE_PURPOSE}:
            raise ReleaseSnapshotError(f"unknown snapshot purpose: {self.purpose}")
        if not self.client_name.strip() or not self.slug.strip():
            raise ReleaseSnapshotError("snapshot client identity is incomplete")
        if not _SAFE_SLUG.fullmatch(self.slug):
            raise ReleaseSnapshotError("snapshot slug is not a safe canonical slug")
        if (self.client_name in {".", ".."}
                or any(marker in self.client_name for marker in ("/", "\\", "\x00"))):
            raise ReleaseSnapshotError(
                "snapshot client name cannot contain a filesystem separator")
        try:
            date.fromisoformat(self.business_as_of)
        except ValueError as exc:
            raise ReleaseSnapshotError(
                "business_as_of must be an ISO date") from exc
        object.__setattr__(
            self, "classification_as_of",
            _aware_utc(self.classification_as_of, field="classification_as_of"))
        object.__setattr__(
            self, "captured_at", _aware_utc(self.captured_at, field="captured_at"))
        if not self.contract_version.strip():
            raise ReleaseSnapshotError("snapshot contract version is empty")
        if not _HEX_64.fullmatch(self.operator_locked_slot_sha256):
            raise ReleaseSnapshotError("snapshot slot digest is not a full SHA-256")
        if canonical_slot_sha256(self.contract_slots) != self.operator_locked_slot_sha256:
            raise ReleaseSnapshotError("snapshot slot rows do not match the ratified digest")
        if not _HEX_64.fullmatch(self.evidence_pack_sha256):
            raise ReleaseSnapshotError("evidence pack receipt is not a full SHA-256")
        try:
            evidence_bytes = base64.b64decode(
                self.evidence_pack_base64.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ReleaseSnapshotError(
                "evidence pack base64 is invalid") from exc
        if _sha_bytes(evidence_bytes) != self.evidence_pack_sha256:
            raise ReleaseSnapshotError("evidence pack bytes changed after capture")
        try:
            source_evidence_bytes = base64.b64decode(
                self.source_evidence_pack_base64.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ReleaseSnapshotError(
                "source evidence pack base64 is invalid") from exc
        if _sha_bytes(source_evidence_bytes) != self.source_evidence_pack_sha256:
            raise ReleaseSnapshotError(
                "source evidence pack bytes changed after capture")
        try:
            source_evidence = json.loads(source_evidence_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseSnapshotError(
                "captured source evidence pack is not JSON") from exc
        if not isinstance(source_evidence, dict):
            raise ReleaseSnapshotError(
                "captured source evidence pack is not an object")
        if (self.purpose == LIVE_RELEASE_PURPOSE
                and source_evidence_bytes != evidence_bytes):
            raise ReleaseSnapshotError(
                "a live release cannot substitute its captured evidence pack")
        if (source_evidence_bytes != evidence_bytes
                and not self.versions.get(
                    "fixture_projection_vocabulary_normalization")):
            raise ReleaseSnapshotError(
                "derived fixture evidence has no named normalization receipt")
        try:
            evidence = json.loads(evidence_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseSnapshotError("captured evidence pack is not JSON") from exc
        if not isinstance(evidence, dict):
            raise ReleaseSnapshotError("captured evidence pack is not an object")
        if self.graph != canonicalize_release_value(self.graph):
            raise ReleaseSnapshotError("snapshot graph is not canonical")
        if self.market_map_inputs != canonicalize_release_value(
                self.market_map_inputs):
            raise ReleaseSnapshotError("snapshot market-map inputs are not canonical")
        if (self.market_map_inputs.get("evidence_pack_v2") or {}) != self.graph:
            raise ReleaseSnapshotError(
                "market-map inputs do not carry the canonical shipped graph")
        graph_violations = self.graph.get("graph_contract_violations")
        if self.graph.get("graph_contract_certified") is not True:
            raise ReleaseSnapshotError(
                "snapshot graph contract is not certified")
        if not isinstance(graph_violations, list):
            raise ReleaseSnapshotError(
                "snapshot graph contract violations must be a list")
        if graph_violations:
            raise ReleaseSnapshotError(
                "snapshot graph claims certification with contract violations")
        graph_classification = str(
            (self.graph.get("classification_context") or {}).get("as_of") or "")
        if _aware_utc(
                graph_classification, field="graph classification_as_of") != (
                    self.classification_as_of):
            raise ReleaseSnapshotError(
                "graph and snapshot classification instants disagree")
        for field in ("captured_at", "generated_at"):
            if _aware_utc(str(self.graph.get(field) or ""), field=f"graph {field}") != (
                    self.captured_at):
                raise ReleaseSnapshotError(
                    f"graph {field} does not match the snapshot capture")
        cache_receipt = self.graph.get("incremental_cache_receipt") or {}
        if cache_receipt.get("schema_version") == (
                "incremental-cache-semantic-receipt-v1"):
            from tools.intelligence_graph.cache import stable_hash

            if cache_receipt.get("context_hash") != stable_hash(
                    self.graph.get("classification_context") or {}):
                raise ReleaseSnapshotError(
                    "semantic cache receipt does not match graph context")
            dependency_as_of = str(
                (cache_receipt.get("semantic_dependencies") or {}).get(
                    "as_of") or "")
            if _aware_utc(
                    dependency_as_of,
                    field="semantic cache classification_as_of") != (
                        self.classification_as_of):
                raise ReleaseSnapshotError(
                    "semantic cache receipt does not match classification instant")
        expected_asset_receipts = {
            name: {
                "sha256": _sha_bytes(str(payload).encode("utf-8")),
                "bytes": len(str(payload).encode("utf-8")),
            }
            for name, payload in sorted(self.render_assets.items())
        }
        if self.render_asset_receipts != expected_asset_receipts:
            raise ReleaseSnapshotError("frozen render asset receipt changed")

    @classmethod
    def create(
        cls, *, purpose: str, client_name: str, slug: str,
        business_as_of: str, classification_as_of: str, captured_at: str,
        contract_slots: Any, versions: Mapping[str, Any],
        authorization: Mapping[str, Any], profile: Mapping[str, Any],
        graph: Mapping[str, Any], market_map_inputs: Mapping[str, Any],
        render_assets: Mapping[str, Any], root: Optional[Path],
        evidence_pack_bytes: bytes,
        source_evidence_pack_bytes: Optional[bytes] = None,
    ) -> "ReleaseSnapshot":
        try:
            evidence_pack_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseSnapshotError("evidence pack must be UTF-8 JSON") from exc
        assets = canonicalize_release_value(render_assets, root=root)
        asset_receipts = {
            name: {
                "sha256": _sha_bytes(str(payload).encode("utf-8")),
                "bytes": len(str(payload).encode("utf-8")),
            }
            for name, payload in sorted(assets.items())
        }
        source_bytes = (
            evidence_pack_bytes if source_evidence_pack_bytes is None
            else source_evidence_pack_bytes)
        try:
            source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseSnapshotError(
                "source evidence pack must be UTF-8 JSON") from exc
        return cls(
            schema_version=RELEASE_SNAPSHOT_VERSION,
            purpose=purpose,
            client_name=" ".join(client_name.split()), slug=slug.strip(),
            business_as_of=business_as_of,
            classification_as_of=classification_as_of,
            captured_at=captured_at,
            contract_version=CONTRACT_VERSION,
            operator_locked_slot_sha256=OPERATOR_LOCKED_SLOT_SHA256,
            contract_slots=tuple(canonicalize_release_value(contract_slots)),
            versions=canonicalize_release_value(versions, root=root),
            authorization=canonicalize_release_value(authorization, root=root),
            profile=canonicalize_release_value(profile, root=root),
            graph=canonicalize_release_value(graph, root=root),
            market_map_inputs=canonicalize_release_value(
                market_map_inputs, root=root),
            render_assets=assets,
            render_asset_receipts=asset_receipts,
            evidence_pack_base64=base64.b64encode(
                evidence_pack_bytes).decode("ascii"),
            evidence_pack_sha256=_sha_bytes(evidence_pack_bytes),
            source_evidence_pack_base64=base64.b64encode(
                source_bytes).decode("ascii"),
            source_evidence_pack_sha256=_sha_bytes(source_bytes),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReleaseSnapshot":
        payload = dict(value)
        payload["contract_slots"] = tuple(payload.get("contract_slots") or ())
        missing_source_fields = [
            field for field in (
                "source_evidence_pack_base64", "source_evidence_pack_sha256")
            if field not in payload
        ]
        if missing_source_fields:
            raise ReleaseSnapshotError(
                "snapshot omits exact source evidence fields: "
                + ", ".join(missing_source_fields))
        return cls(**payload)

    def to_dict(self) -> dict:
        return canonicalize_release_value(asdict(self))

    @property
    def evidence_pack_bytes(self) -> bytes:
        return base64.b64decode(
            self.evidence_pack_base64.encode("ascii"), validate=True)

    @property
    def source_evidence_pack_bytes(self) -> bytes:
        return base64.b64decode(
            self.source_evidence_pack_base64.encode("ascii"), validate=True)


__all__ = (
    "LIVE_RELEASE_PURPOSE",
    "RELEASE_SNAPSHOT_VERSION",
    "REPRODUCIBILITY_FIXTURE_PURPOSE",
    "ReleaseSnapshot",
    "ReleaseSnapshotError",
    "canonical_json_bytes",
    "canonical_slot_sha256",
    "canonicalize_release_value",
    "contains_local_path",
)
