"""Fail-closed persistence for one Candidate Review v1 generation.

The persistence layer is deliberately ignorant of research and rendering.  It
commits already-produced JSON objects into an immutable run directory, binds
their exact bytes in a receipt, and promotes that receipt with one atomic
``current.json`` pointer.  A directory without both the receipt and pointer is
never treated as current work.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Iterator, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.candidate_review_v1.contracts import ArtifactBinding
from tools.artifacts import atomic_write_json


GENERATION_RECEIPT_SCHEMA_VERSION = (
    "candidate_review_v1.generation_receipt.v1"
)
CURRENT_POINTER_SCHEMA_VERSION = "candidate_review_v1.current_pointer.v1"
GENERATION_RECEIPT_FILENAME = "generation.receipt.json"
CURRENT_POINTER_FILENAME = "current.json"

MAX_FILES_PER_GROUP = 128
MAX_JSON_FILE_BYTES = 32 * 1024 * 1024
MAX_GENERATION_BYTES = 128 * 1024 * 1024

_ROOT = Path(__file__).resolve().parents[2]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLIENT_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_FILE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")


class GenerationPersistenceError(RuntimeError):
    """A generation is incomplete, corrupt, stale, or ambiguously bound."""


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class GenerationFileRole(str, Enum):
    REGISTRY = "registry"
    MANIFEST = "manifest"
    ARTIFACT = "artifact"

    @property
    def directory(self) -> str:
        return {
            GenerationFileRole.REGISTRY: "registries",
            GenerationFileRole.MANIFEST: "manifests",
            GenerationFileRole.ARTIFACT: "artifacts",
        }[self]


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_relative_path(value: str, label: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise ValueError(f"{label} must be a normalized POSIX relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or str(parsed) != value:
        raise ValueError(f"{label} must be a normalized POSIX relative path")
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError(f"{label} cannot traverse directories")
    return parsed


class GenerationFileBinding(_FrozenContract):
    """Exact byte binding for one file beneath a client state root."""

    file_id: str = Field(min_length=1, max_length=128)
    role: GenerationFileRole
    relative_path: str = Field(min_length=1)
    sha256: str
    byte_size: int = Field(gt=0, le=MAX_JSON_FILE_BYTES)

    @model_validator(mode="after")
    def _binding_is_safe(self) -> "GenerationFileBinding":
        if _FILE_ID_RE.fullmatch(self.file_id) is None:
            raise ValueError("file_id must be a canonical lowercase token")
        _require_relative_path(self.relative_path, "relative_path")
        _require_sha256(self.sha256, "file sha256")
        return self


def _binding_payload(binding: ArtifactBinding) -> dict:
    return binding.model_dump(mode="json")


def _basis_payload(
    binding: ArtifactBinding,
    registry_files: tuple[GenerationFileBinding, ...],
    manifest_files: tuple[GenerationFileBinding, ...],
) -> dict:
    return {
        "schema_version": GENERATION_RECEIPT_SCHEMA_VERSION,
        "binding": _binding_payload(binding),
        "registry_files": [
            {
                "file_id": row.file_id,
                "role": row.role.value,
                "sha256": row.sha256,
                "byte_size": row.byte_size,
            }
            for row in registry_files
        ],
        "manifest_files": [
            {
                "file_id": row.file_id,
                "role": row.role.value,
                "sha256": row.sha256,
                "byte_size": row.byte_size,
            }
            for row in manifest_files
        ],
    }


def _stable_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _basis_from_bindings(
    binding: ArtifactBinding,
    registry_files: tuple[GenerationFileBinding, ...],
    manifest_files: tuple[GenerationFileBinding, ...],
) -> str:
    return _stable_digest(_basis_payload(
        binding,
        registry_files,
        manifest_files,
    ))


class GenerationReceipt(_FrozenContract):
    """Commit record for one exact, immutable run generation."""

    schema_version: Literal[
        "candidate_review_v1.generation_receipt.v1"
    ] = GENERATION_RECEIPT_SCHEMA_VERSION
    binding: ArtifactBinding
    basis_sha256: str
    registry_files: tuple[GenerationFileBinding, ...] = Field(min_length=1)
    manifest_files: tuple[GenerationFileBinding, ...] = Field(min_length=1)
    artifact_files: tuple[GenerationFileBinding, ...] = Field(min_length=1)
    committed_at: datetime

    @model_validator(mode="after")
    def _receipt_is_closed(self) -> "GenerationReceipt":
        _require_sha256(self.basis_sha256, "basis_sha256")
        _require_aware(self.committed_at, "committed_at")
        if _RUN_ID_RE.fullmatch(self.binding.run_id) is None:
            raise ValueError("run_id is not safe for generation storage")

        groups = (
            (GenerationFileRole.REGISTRY, self.registry_files),
            (GenerationFileRole.MANIFEST, self.manifest_files),
            (GenerationFileRole.ARTIFACT, self.artifact_files),
        )
        all_ids: list[str] = []
        all_paths: list[str] = []
        total_bytes = 0
        for role, rows in groups:
            if tuple(sorted(rows, key=lambda row: row.file_id)) != rows:
                raise ValueError(f"{role.value} files must be sorted by file_id")
            expected_prefix = (
                f"generations/{self.binding.run_id}/{role.directory}/"
            )
            for row in rows:
                if row.role is not role:
                    raise ValueError(f"{role.value} file has the wrong role")
                expected_path = f"{expected_prefix}{row.file_id}.json"
                if row.relative_path != expected_path:
                    raise ValueError(
                        f"{role.value} file path is not generation-bound"
                    )
                all_ids.append(row.file_id)
                all_paths.append(row.relative_path)
                total_bytes += row.byte_size
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("generation file_ids must be globally unique")
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("generation file paths must be globally unique")
        if total_bytes > MAX_GENERATION_BYTES:
            raise ValueError("generation exceeds its byte budget")
        expected_basis = _basis_from_bindings(
            self.binding,
            self.registry_files,
            self.manifest_files,
        )
        if self.basis_sha256 != expected_basis:
            raise ValueError("basis_sha256 does not match registry/manifests")
        return self


class CurrentGenerationPointer(_FrozenContract):
    """The sole mutable reference to the current committed generation."""

    schema_version: Literal[
        "candidate_review_v1.current_pointer.v1"
    ] = CURRENT_POINTER_SCHEMA_VERSION
    client_id: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_designator: str = Field(min_length=1)
    scope_sha256: str
    profile_sha256: str
    evidence_snapshot_sha256: str
    basis_sha256: str
    receipt_path: str = Field(min_length=1)
    receipt_sha256: str
    committed_at: datetime

    @model_validator(mode="after")
    def _pointer_is_exact(self) -> "CurrentGenerationPointer":
        if _CLIENT_ID_RE.fullmatch(self.client_id) is None:
            raise ValueError("client_id must be a canonical lowercase slug")
        if _RUN_ID_RE.fullmatch(self.run_id) is None:
            raise ValueError("run_id is not safe for generation storage")
        for label, value in (
            ("scope_sha256", self.scope_sha256),
            ("profile_sha256", self.profile_sha256),
            ("evidence_snapshot_sha256", self.evidence_snapshot_sha256),
            ("basis_sha256", self.basis_sha256),
            ("receipt_sha256", self.receipt_sha256),
        ):
            _require_sha256(value, label)
        expected = f"generations/{self.run_id}/{GENERATION_RECEIPT_FILENAME}"
        if self.receipt_path != expected:
            raise ValueError("receipt_path is not bound to pointer run_id")
        _require_relative_path(self.receipt_path, "receipt_path")
        _require_aware(self.committed_at, "committed_at")
        return self

    @classmethod
    def from_receipt(
        cls,
        receipt: GenerationReceipt,
        *,
        receipt_path: str,
        receipt_sha256: str,
    ) -> "CurrentGenerationPointer":
        binding = receipt.binding
        return cls(
            client_id=binding.client_id,
            client_name=binding.client_name,
            run_id=binding.run_id,
            scope_designator=binding.scope_designator,
            scope_sha256=binding.scope_sha256,
            profile_sha256=binding.profile_sha256,
            evidence_snapshot_sha256=binding.evidence_snapshot_sha256,
            basis_sha256=receipt.basis_sha256,
            receipt_path=receipt_path,
            receipt_sha256=receipt_sha256,
            committed_at=receipt.committed_at,
        )


@dataclass(frozen=True)
class LoadedGeneration:
    """Validated payloads read from exactly one current pointer snapshot."""

    pointer: CurrentGenerationPointer
    receipt: GenerationReceipt
    registries: Mapping[str, dict]
    manifests: Mapping[str, dict]
    artifacts: Mapping[str, dict]


@dataclass(frozen=True)
class GenerationCommit:
    generation: LoadedGeneration
    reused: bool


def default_generation_state_root(root: Path | str = _ROOT) -> Path:
    return Path(root) / "data" / "state" / "candidate_review_v1"


def _state_root(state_root: Path | str | None) -> Path:
    return Path(state_root) if state_root is not None \
        else default_generation_state_root()


def _validate_client_id(client_id: str) -> None:
    if _CLIENT_ID_RE.fullmatch(client_id) is None:
        raise ValueError("client_id must be a canonical lowercase slug")


def _safe_client_root(
    state_root: Path | str | None,
    client_id: str,
    *,
    create: bool,
) -> Path:
    _validate_client_id(client_id)
    root = _state_root(state_root)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve(strict=False)
    candidate = root / client_id
    if candidate.exists() and candidate.is_symlink():
        raise GenerationPersistenceError("client state root cannot be a symlink")
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise GenerationPersistenceError("client state root escapes state root")
    if create:
        candidate.mkdir(parents=False, exist_ok=True)
    return candidate


def _safe_bound_path(client_root: Path, relative_path: str) -> Path:
    parsed = _require_relative_path(relative_path, "bound path")
    root = client_root.resolve(strict=False)
    candidate = client_root.joinpath(*parsed.parts)
    current = client_root
    for part in parsed.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise GenerationPersistenceError("bound path contains a symlink")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise GenerationPersistenceError("bound path escapes client root")
    return candidate


@contextmanager
def _client_lock(client_root: Path) -> Iterator[None]:
    lock_path = _safe_bound_path(client_root, ".generation.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_json_value(value: object, *, label: str, depth: int = 0) -> None:
    if depth > 100:
        raise ValueError(f"{label} exceeds the JSON nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, label=label, depth=depth + 1)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError(f"{label} JSON object keys must be strings")
        for nested in value.values():
            _validate_json_value(nested, label=label, depth=depth + 1)
        return
    raise TypeError(f"{label} contains a non-JSON value")


def _canonical_object(value: dict, *, label: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"{label} payload must be a JSON object")
    _validate_json_value(value, label=label)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    result = json.loads(encoded)
    if not isinstance(result, dict):  # pragma: no cover - guarded above
        raise TypeError(f"{label} payload must be a JSON object")
    return result


def _atomic_json_bytes(payload: dict) -> bytes:
    return (
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
    ).encode("utf-8")


def _normalize_group(
    payloads: Mapping[str, dict],
    *,
    role: GenerationFileRole,
    binding: ArtifactBinding,
) -> tuple[tuple[str, dict, GenerationFileBinding], ...]:
    if not isinstance(payloads, Mapping) or not payloads:
        raise ValueError(f"{role.value} payloads must be a nonempty mapping")
    if len(payloads) > MAX_FILES_PER_GROUP:
        raise ValueError(f"{role.value} payloads exceed the file-count budget")
    rows = []
    for file_id, raw_payload in sorted(payloads.items()):
        if not isinstance(file_id, str) or _FILE_ID_RE.fullmatch(file_id) is None:
            raise ValueError(
                f"{role.value} file_id must be a canonical lowercase token"
            )
        payload = _canonical_object(raw_payload, label=f"{role.value}:{file_id}")
        encoded = _atomic_json_bytes(payload)
        if len(encoded) > MAX_JSON_FILE_BYTES:
            raise ValueError(f"{role.value}:{file_id} exceeds its byte budget")
        relative = (
            f"generations/{binding.run_id}/{role.directory}/{file_id}.json"
        )
        file_binding = GenerationFileBinding(
            file_id=file_id,
            role=role,
            relative_path=relative,
            sha256=hashlib.sha256(encoded).hexdigest(),
            byte_size=len(encoded),
        )
        rows.append((file_id, payload, file_binding))
    return tuple(rows)


def _normalized_inputs(
    binding: ArtifactBinding,
    *,
    registries: Mapping[str, dict],
    manifests: Mapping[str, dict],
    artifacts: Mapping[str, dict],
) -> tuple[
    tuple[tuple[str, dict, GenerationFileBinding], ...],
    tuple[tuple[str, dict, GenerationFileBinding], ...],
    tuple[tuple[str, dict, GenerationFileBinding], ...],
]:
    if _RUN_ID_RE.fullmatch(binding.run_id) is None:
        raise ValueError("run_id is not safe for generation storage")
    groups = (
        _normalize_group(
            registries,
            role=GenerationFileRole.REGISTRY,
            binding=binding,
        ),
        _normalize_group(
            manifests,
            role=GenerationFileRole.MANIFEST,
            binding=binding,
        ),
        _normalize_group(
            artifacts,
            role=GenerationFileRole.ARTIFACT,
            binding=binding,
        ),
    )
    all_ids = [row[0] for group in groups for row in group]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("generation file_ids must be globally unique")
    total = sum(row[2].byte_size for group in groups for row in group)
    if total > MAX_GENERATION_BYTES:
        raise ValueError("generation exceeds its byte budget")
    return groups


def generation_basis_sha256(
    binding: ArtifactBinding,
    *,
    registries: Mapping[str, dict],
    manifests: Mapping[str, dict],
) -> str:
    """Hash the exact run-bound registry and query-manifest basis.

    The run id is intentionally part of ``ArtifactBinding`` and therefore the
    basis.  Persistence can reuse retries of the same run, but never relabel an
    older generation as a newer run.
    """

    registry_rows = _normalize_group(
        registries,
        role=GenerationFileRole.REGISTRY,
        binding=binding,
    )
    manifest_rows = _normalize_group(
        manifests,
        role=GenerationFileRole.MANIFEST,
        binding=binding,
    )
    return _basis_from_bindings(
        binding,
        tuple(row[2] for row in registry_rows),
        tuple(row[2] for row in manifest_rows),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_bounded(path: Path, *, label: str) -> bytes:
    try:
        stat = path.stat()
    except OSError as exc:
        raise GenerationPersistenceError(f"{label} is unavailable") from exc
    if not path.is_file() or path.is_symlink():
        raise GenerationPersistenceError(f"{label} must be a regular file")
    if stat.st_size <= 0 or stat.st_size > MAX_JSON_FILE_BYTES:
        raise GenerationPersistenceError(f"{label} violates its byte budget")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GenerationPersistenceError(f"{label} cannot be read") from exc


def _parse_object(value: bytes, *, label: str) -> dict:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationPersistenceError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise GenerationPersistenceError(f"{label} root must be an object")
    return parsed


def _load_model(path: Path, model, *, label: str):
    raw = _read_bounded(path, label=label)
    payload = _parse_object(raw, label=label)
    try:
        return model.model_validate(payload), raw
    except (ValidationError, ValueError) as exc:
        raise GenerationPersistenceError(f"{label} validation failed") from exc


def _pointer_matches_receipt(
    pointer: CurrentGenerationPointer,
    receipt: GenerationReceipt,
) -> bool:
    binding = receipt.binding
    return (
        pointer.client_id == binding.client_id
        and pointer.client_name == binding.client_name
        and pointer.run_id == binding.run_id
        and pointer.scope_designator == binding.scope_designator
        and pointer.scope_sha256 == binding.scope_sha256
        and pointer.profile_sha256 == binding.profile_sha256
        and pointer.evidence_snapshot_sha256 == binding.evidence_snapshot_sha256
        and pointer.basis_sha256 == receipt.basis_sha256
        and pointer.committed_at == receipt.committed_at
    )


def _load_file_group(
    client_root: Path,
    rows: tuple[GenerationFileBinding, ...],
) -> Mapping[str, dict]:
    loaded: dict[str, dict] = {}
    total = 0
    for row in rows:
        path = _safe_bound_path(client_root, row.relative_path)
        raw = _read_bounded(path, label=f"generation file {row.file_id}")
        total += len(raw)
        if len(raw) != row.byte_size or _sha256_bytes(raw) != row.sha256:
            raise GenerationPersistenceError(
                f"generation file {row.file_id} failed exact SHA validation"
            )
        loaded[row.file_id] = _parse_object(
            raw,
            label=f"generation file {row.file_id}",
        )
    if total > MAX_GENERATION_BYTES:
        raise GenerationPersistenceError("generation exceeds its byte budget")
    return MappingProxyType(loaded)


def load_current_generation(
    client_id: str,
    *,
    state_root: Path | str | None = None,
    expected_binding: Optional[ArtifactBinding] = None,
    expected_basis_sha256: Optional[str] = None,
) -> Optional[LoadedGeneration]:
    """Load the current generation only after complete receipt/SHA validation.

    Absence is returned as ``None``.  Any present-but-invalid state raises
    :class:`GenerationPersistenceError`; corrupt work is never confused with
    an empty first run.
    """

    _validate_client_id(client_id)
    if expected_binding is not None and expected_binding.client_id != client_id:
        raise ValueError("expected binding belongs to another client")
    if expected_basis_sha256 is not None:
        _require_sha256(expected_basis_sha256, "expected_basis_sha256")
    client_root = _safe_client_root(state_root, client_id, create=False)
    pointer_path = _safe_bound_path(client_root, CURRENT_POINTER_FILENAME)
    if not pointer_path.exists():
        return None

    pointer, pointer_raw = _load_model(
        pointer_path,
        CurrentGenerationPointer,
        label="current generation pointer",
    )
    if pointer.client_id != client_id:
        raise GenerationPersistenceError(
            "current generation pointer belongs to another client"
        )
    receipt_path = _safe_bound_path(client_root, pointer.receipt_path)
    receipt, receipt_raw = _load_model(
        receipt_path,
        GenerationReceipt,
        label="generation receipt",
    )
    if _sha256_bytes(receipt_raw) != pointer.receipt_sha256:
        raise GenerationPersistenceError(
            "generation receipt failed exact SHA validation"
        )
    if not _pointer_matches_receipt(pointer, receipt):
        raise GenerationPersistenceError(
            "current pointer and generation receipt bindings differ"
        )
    if expected_binding is not None and receipt.binding != expected_binding:
        raise GenerationPersistenceError(
            "current generation does not match the expected binding"
        )
    if expected_basis_sha256 is not None \
            and receipt.basis_sha256 != expected_basis_sha256:
        raise GenerationPersistenceError(
            "current generation does not match the expected basis"
        )

    registries = _load_file_group(client_root, receipt.registry_files)
    manifests = _load_file_group(client_root, receipt.manifest_files)
    artifacts = _load_file_group(client_root, receipt.artifact_files)
    if sum(
        row.byte_size
        for rows in (
            receipt.registry_files,
            receipt.manifest_files,
            receipt.artifact_files,
        )
        for row in rows
    ) > MAX_GENERATION_BYTES:
        raise GenerationPersistenceError("generation exceeds its byte budget")

    # Reject an atomic pointer replacement that occurred during validation.
    try:
        final_pointer_raw = pointer_path.read_bytes()
    except OSError as exc:
        raise GenerationPersistenceError(
            "current generation pointer changed during validation"
        ) from exc
    if final_pointer_raw != pointer_raw:
        raise GenerationPersistenceError(
            "current generation pointer changed during validation"
        )
    return LoadedGeneration(
        pointer=pointer,
        receipt=receipt,
        registries=registries,
        manifests=manifests,
        artifacts=artifacts,
    )


def find_reusable_generation(
    binding: ArtifactBinding,
    basis_sha256: str,
    *,
    state_root: Path | str | None = None,
) -> Optional[LoadedGeneration]:
    """Return a validated retry of this exact run and basis, if current."""

    _require_sha256(basis_sha256, "basis_sha256")
    current = load_current_generation(
        binding.client_id,
        state_root=state_root,
    )
    if current is None:
        return None
    if current.receipt.binding != binding:
        return None
    if current.receipt.basis_sha256 != basis_sha256:
        return None
    return current


def _prospective_artifacts_match(
    current: LoadedGeneration,
    artifact_rows: tuple[tuple[str, dict, GenerationFileBinding], ...],
) -> bool:
    expected = {
        row.file_id: (row.sha256, row.byte_size)
        for row in current.receipt.artifact_files
    }
    prospective = {
        row[2].file_id: (row[2].sha256, row[2].byte_size)
        for row in artifact_rows
    }
    return expected == prospective


def _write_group(
    client_root: Path,
    rows: tuple[tuple[str, dict, GenerationFileBinding], ...],
) -> None:
    for _file_id, payload, binding in rows:
        path = _safe_bound_path(client_root, binding.relative_path)
        if path.exists():
            raw = _read_bounded(
                path,
                label=f"partial generation file {binding.file_id}",
            )
            if len(raw) != binding.byte_size \
                    or _sha256_bytes(raw) != binding.sha256:
                raise GenerationPersistenceError(
                    f"partial generation file {binding.file_id} does not "
                    "match the expected bytes"
                )
            continue
        atomic_write_json(path, payload)
        raw = _read_bounded(path, label=f"generation file {binding.file_id}")
        if len(raw) != binding.byte_size or _sha256_bytes(raw) != binding.sha256:
            raise GenerationPersistenceError(
                f"generation file {binding.file_id} changed during commit"
            )


def _generation_expected_paths(
    rows: tuple[
        tuple[tuple[str, dict, GenerationFileBinding], ...],
        tuple[tuple[str, dict, GenerationFileBinding], ...],
        tuple[tuple[str, dict, GenerationFileBinding], ...],
    ],
    *,
    include_receipt: bool,
) -> tuple[set[str], set[str]]:
    files = {
        "/".join(binding.relative_path.split("/")[2:])
        for group in rows
        for _file_id, _payload, binding in group
    }
    if include_receipt:
        files.add(GENERATION_RECEIPT_FILENAME)
    directories = {
        str(parent)
        for path in files
        for parent in PurePosixPath(path).parents
        if str(parent) != "."
    }
    return files, directories


def _validate_partial_generation_tree(
    generation_root: Path,
    rows: tuple[
        tuple[tuple[str, dict, GenerationFileBinding], ...],
        tuple[tuple[str, dict, GenerationFileBinding], ...],
        tuple[tuple[str, dict, GenerationFileBinding], ...],
    ],
    *,
    include_receipt: bool,
) -> None:
    """Reject ambiguous partial state while permitting atomic-write history."""

    expected_files, expected_directories = _generation_expected_paths(
        rows,
        include_receipt=include_receipt,
    )
    for current_root, directory_names, file_names in os.walk(
        generation_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        relative_root = current.relative_to(generation_root)
        under_history = ".history" in relative_root.parts
        for name in tuple(directory_names):
            path = current / name
            relative = path.relative_to(generation_root)
            if path.is_symlink():
                raise GenerationPersistenceError(
                    "partial generation contains a symlink"
                )
            if under_history or ".history" in relative.parts:
                continue
            if relative.as_posix() not in expected_directories:
                raise GenerationPersistenceError(
                    "partial generation contains an unknown directory"
                )
        for name in file_names:
            path = current / name
            relative = path.relative_to(generation_root)
            if path.is_symlink():
                raise GenerationPersistenceError(
                    "partial generation contains a symlink"
                )
            if under_history or ".history" in relative.parts:
                continue
            if relative.as_posix() not in expected_files:
                raise GenerationPersistenceError(
                    "partial generation contains an unknown file"
                )


def _load_partial_receipt(
    receipt_path: Path,
    *,
    binding: ArtifactBinding,
    basis_sha256: str,
    registry_rows: tuple[tuple[str, dict, GenerationFileBinding], ...],
    manifest_rows: tuple[tuple[str, dict, GenerationFileBinding], ...],
    artifact_rows: tuple[tuple[str, dict, GenerationFileBinding], ...],
) -> tuple[GenerationReceipt, bytes]:
    receipt, raw = _load_model(
        receipt_path,
        GenerationReceipt,
        label="partial generation receipt",
    )
    if (
        receipt.binding != binding
        or receipt.basis_sha256 != basis_sha256
        or receipt.registry_files != tuple(row[2] for row in registry_rows)
        or receipt.manifest_files != tuple(row[2] for row in manifest_rows)
        or receipt.artifact_files != tuple(row[2] for row in artifact_rows)
    ):
        raise GenerationPersistenceError(
            "partial generation receipt does not match expected bytes"
        )
    return receipt, raw


def persist_generation(
    binding: ArtifactBinding,
    *,
    registries: Mapping[str, dict],
    manifests: Mapping[str, dict],
    artifacts: Mapping[str, dict],
    state_root: Path | str | None = None,
    committed_at: Optional[datetime] = None,
) -> GenerationCommit:
    """Commit one generation, writing receipt and current pointer last.

    The same client/run/scope/basis and byte-identical output is an idempotent
    reuse.  The same basis with different output is rejected as nondeterminism,
    and an existing run directory is never overwritten.
    """

    registry_rows, manifest_rows, artifact_rows = _normalized_inputs(
        binding,
        registries=registries,
        manifests=manifests,
        artifacts=artifacts,
    )
    basis_sha256 = _basis_from_bindings(
        binding,
        tuple(row[2] for row in registry_rows),
        tuple(row[2] for row in manifest_rows),
    )
    when = committed_at or datetime.now(timezone.utc)
    _require_aware(when, "committed_at")
    when = when.astimezone(timezone.utc)

    client_root = _safe_client_root(
        state_root,
        binding.client_id,
        create=True,
    )
    with _client_lock(client_root):
        current = load_current_generation(
            binding.client_id,
            state_root=state_root,
        )
        if current is not None \
                and current.receipt.binding == binding \
                and current.receipt.basis_sha256 == basis_sha256:
            if not _prospective_artifacts_match(current, artifact_rows):
                raise GenerationPersistenceError(
                    "same generation basis produced different artifact bytes"
                )
            return GenerationCommit(generation=current, reused=True)
        if current is not None and when <= current.receipt.committed_at:
            raise GenerationPersistenceError(
                "a new current generation must be committed after the "
                "existing current generation"
            )

        generation_relative = f"generations/{binding.run_id}"
        generation_root = _safe_bound_path(
            client_root,
            generation_relative,
        )
        generation_root.parent.mkdir(parents=False, exist_ok=True)
        if generation_root.exists():
            if generation_root.is_symlink() or not generation_root.is_dir():
                raise GenerationPersistenceError(
                    "partial generation root must be a regular directory"
                )
            _validate_partial_generation_tree(
                generation_root,
                (registry_rows, manifest_rows, artifact_rows),
                include_receipt=True,
            )
        else:
            generation_root.mkdir(parents=False, exist_ok=False)

        _write_group(client_root, registry_rows)
        _write_group(client_root, manifest_rows)
        _write_group(client_root, artifact_rows)

        _validate_partial_generation_tree(
            generation_root,
            (registry_rows, manifest_rows, artifact_rows),
            include_receipt=True,
        )

        receipt_relative = (
            f"{generation_relative}/{GENERATION_RECEIPT_FILENAME}"
        )
        receipt_path = _safe_bound_path(client_root, receipt_relative)
        if receipt_path.exists():
            receipt, receipt_raw = _load_partial_receipt(
                receipt_path,
                binding=binding,
                basis_sha256=basis_sha256,
                registry_rows=registry_rows,
                manifest_rows=manifest_rows,
                artifact_rows=artifact_rows,
            )
        else:
            receipt = GenerationReceipt(
                binding=binding,
                basis_sha256=basis_sha256,
                registry_files=tuple(row[2] for row in registry_rows),
                manifest_files=tuple(row[2] for row in manifest_rows),
                artifact_files=tuple(row[2] for row in artifact_rows),
                committed_at=when,
            )
            atomic_write_json(receipt_path, receipt.model_dump(mode="json"))
            receipt_raw = _read_bounded(
                receipt_path,
                label="generation receipt",
            )

        if current is not None \
                and receipt.committed_at <= current.receipt.committed_at:
            raise GenerationPersistenceError(
                "cannot promote an older generation over the current "
                "generation"
            )

        pointer = CurrentGenerationPointer.from_receipt(
            receipt,
            receipt_path=receipt_relative,
            receipt_sha256=_sha256_bytes(receipt_raw),
        )
        pointer_path = _safe_bound_path(client_root, CURRENT_POINTER_FILENAME)
        if pointer_path.exists() and pointer_path.is_symlink():
            raise GenerationPersistenceError(
                "current generation pointer cannot be a symlink"
            )
        atomic_write_json(pointer_path, pointer.model_dump(mode="json"))

        loaded = load_current_generation(
            binding.client_id,
            state_root=state_root,
            expected_binding=binding,
            expected_basis_sha256=basis_sha256,
        )
        if loaded is None:  # pragma: no cover - pointer was just committed
            raise GenerationPersistenceError(
                "committed generation did not become current"
            )
        return GenerationCommit(generation=loaded, reused=False)


__all__ = [
    "CURRENT_POINTER_FILENAME",
    "CURRENT_POINTER_SCHEMA_VERSION",
    "GENERATION_RECEIPT_FILENAME",
    "GENERATION_RECEIPT_SCHEMA_VERSION",
    "CurrentGenerationPointer",
    "GenerationCommit",
    "GenerationFileBinding",
    "GenerationFileRole",
    "GenerationPersistenceError",
    "GenerationReceipt",
    "LoadedGeneration",
    "default_generation_state_root",
    "find_reusable_generation",
    "generation_basis_sha256",
    "load_current_generation",
    "persist_generation",
]
