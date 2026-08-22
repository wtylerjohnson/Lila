"""Immutable artifacts and validated cache for Candidate Review collaboration."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.candidate_review_v1.collaboration import (
    CollaborationComparison,
    CollaborationRequest,
    CollaborationRun,
    ProviderCallResult,
    _security_normalize,
    challenge_output_model,
    contains_sensitive_material,
    validate_collaboration_request,
    validate_provider_model,
)


STORE_SCHEMA_VERSION = "candidate_review_v1.collaboration_store.v2"
CACHE_SCHEMA_VERSION = "candidate_review_v1.collaboration_cache.v1"
RECEIPT_FILENAME = "collaboration.receipt.json"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_ROOT = _ROOT / "data" / "state" / "candidate_review_v1" / "collaboration"
_SAFE_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ANALYTICAL_KEY_FIELDS = frozenset({
    "access_route_key",
    "agency_key",
    "buyer_key",
    "item_key",
    "program_key",
})
_SECRET_KEY = re.compile(
    r"(?i)^(?:"
    r"api[_-]?key|authorization|credential|password|passwd|private[_-]?key|"
    r"secret|aws_access_key_id|email|phone|"
    r"contact(?:[_-]?(?:email|phone|name))?|"
    r"[a-z0-9]+(?:[_-][a-z0-9]+)*[_-]"
    r"(?:key|token|secret|password|auth|credential)"
    r")$"
)
class CollaborationStoreError(RuntimeError):
    """Collaboration data is unsafe, stale, corrupt, or not immutable."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


def _enum_text(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _require_sha(value: str, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_safe_id(value: str, label: str) -> None:
    if _SAFE_ID.fullmatch(value) is None or value in {".", ".."}:
        raise ValueError(f"{label} is not a safe path identifier")


def _canonical_bytes(payload: Any) -> bytes:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json", exclude_none=False)
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CollaborationStoreError(f"artifact is not canonical JSON: {exc}") from exc
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise CollaborationStoreError("collaboration artifact exceeds its byte budget")
    _assert_secret_free(payload)
    return encoded


def _assert_secret_free(
    value: Any,
    *,
    path: str = "$",
    field_name: str | None = None,
) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = _security_normalize(key).casefold()
            if normalized_key not in _SAFE_ANALYTICAL_KEY_FIELDS \
                    and _SECRET_KEY.fullmatch(normalized_key):
                raise CollaborationStoreError(f"credential-shaped field rejected at {path}.{key}")
            _assert_secret_free(
                child,
                path=f"{path}.{key}",
                field_name=str(key),
            )
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        normalized_field = _security_normalize(field_name).casefold()
        if (
            (normalized_field == "sha256" or normalized_field.endswith("_sha256"))
            and _SHA256.fullmatch(value) is not None
        ):
            return
        if contains_sensitive_material(value):
            raise CollaborationStoreError(f"credential-shaped value rejected at {path}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_child(root: Path, *parts: str) -> Path:
    for index, part in enumerate(parts):
        _require_safe_id(part, f"path part {index + 1}")
    resolved_root = root.resolve()
    target = resolved_root.joinpath(*parts).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise CollaborationStoreError("collaboration path escapes its state root") from exc
    return target


def _write_immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == data:
            return
        raise CollaborationStoreError(f"immutable collaboration artifact changed: {path.name}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp.read_bytes() != data:
            raise CollaborationStoreError("temporary collaboration artifact failed verification")
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise CollaborationStoreError(
                    f"immutable collaboration artifact changed: {path.name}"
                )
            return
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()


class CollaborationCacheIdentity(_FrozenModel):
    """Every dimension that can change a collaborator conclusion."""

    layer: str = Field(min_length=1)
    input_sha256: str
    evidence_sha256: str
    request_sha256: str
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    prompt_sha256: str
    schema_sha256: str

    @model_validator(mode="after")
    def _identity_is_bound(self) -> "CollaborationCacheIdentity":
        for label, value in (
            ("input_sha256", self.input_sha256),
            ("evidence_sha256", self.evidence_sha256),
            ("request_sha256", self.request_sha256),
            ("prompt_sha256", self.prompt_sha256),
            ("schema_sha256", self.schema_sha256),
        ):
            _require_sha(value, label)
        if validate_provider_model(self.model) != self.model:
            raise ValueError("cache model must be a canonical provider token")
        return self

    @property
    def cache_key(self) -> str:
        return _sha256(_canonical_bytes(self.model_dump(mode="json")))

    @classmethod
    def from_request(
        cls,
        request: Any,
        *,
        provider: Any,
        model: str,
        prompt_sha256: str | None = None,
        schema_sha256: str | None = None,
    ) -> "CollaborationCacheIdentity":
        if prompt_sha256 is None or schema_sha256 is None:
            from agents.candidate_review_v1.collaboration_providers import (
                provider_contract_hashes,
            )

            prompt_sha256, schema_sha256 = provider_contract_hashes(request)
        input_sha = _sha256(_canonical_bytes(request.sanitized_intake))
        evidence_sha = _sha256(_canonical_bytes([
            BaseModel.model_dump(
                row,
                mode="json",
                exclude_none=False,
            )
            for row in request.evidence_snapshot
        ]))
        return cls(
            layer=_enum_text(request.layer),
            input_sha256=input_sha,
            evidence_sha256=evidence_sha,
            request_sha256=request.request_sha256,
            provider=_enum_text(provider),
            model=model,
            prompt_version=request.prompt_version,
            schema_version=request.output_schema_version,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
        )


class CollaborationFileBinding(_FrozenModel):
    file_name: Literal["claude.json", "openai.json", "comparison.json"]
    sha256: str
    byte_size: int = Field(gt=0, le=MAX_ARTIFACT_BYTES)

    @model_validator(mode="after")
    def _file_is_bound(self) -> "CollaborationFileBinding":
        _require_sha(self.sha256, "artifact sha256")
        return self


def _execution_digest(
    *,
    request_sha256: str,
    mode: str,
    disposition: str,
    files: tuple[CollaborationFileBinding, ...],
) -> str:
    return _sha256(_canonical_bytes({
        "request_sha256": request_sha256,
        "mode": mode,
        "disposition": disposition,
        "files": [row.model_dump(mode="json") for row in files],
    }))


class CollaborationStoreReceipt(_FrozenModel):
    schema_version: Literal[
        "candidate_review_v1.collaboration_store.v2"
    ] = STORE_SCHEMA_VERSION
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    request_sha256: str
    layer: str = Field(min_length=1)
    mode: Literal["off", "advisory", "required"]
    disposition: Literal[
        "off_continue", "continue", "continue_degraded", "paused"
    ]
    execution_sha256: str
    files: tuple[CollaborationFileBinding, ...] = Field(min_length=3, max_length=3)
    committed_at: datetime

    @model_validator(mode="after")
    def _receipt_is_closed(self) -> "CollaborationStoreReceipt":
        _require_safe_id(self.client_id, "client_id")
        _require_safe_id(self.run_id, "run_id")
        _require_safe_id(self.layer, "layer")
        _require_sha(self.request_sha256, "request_sha256")
        _require_sha(self.execution_sha256, "execution_sha256")
        if self.committed_at.tzinfo is None or self.committed_at.utcoffset() is None:
            raise ValueError("committed_at must be timezone-aware")
        expected = ("claude.json", "comparison.json", "openai.json")
        if tuple(row.file_name for row in self.files) != expected:
            raise ValueError("receipt files must be complete and sorted")
        expected_execution = _execution_digest(
            request_sha256=self.request_sha256,
            mode=self.mode,
            disposition=self.disposition,
            files=self.files,
        )
        if self.execution_sha256 != expected_execution:
            raise ValueError("execution_sha256 does not match the exact run artifacts")
        return self

    @property
    def relative_path(self) -> str:
        return "/".join((
            self.client_id,
            self.run_id,
            self.layer,
            self.execution_sha256,
        ))


class CollaborationCacheEnvelope(_FrozenModel):
    schema_version: Literal[
        "candidate_review_v1.collaboration_cache.v1"
    ] = CACHE_SCHEMA_VERSION
    identity: CollaborationCacheIdentity
    result_sha256: str
    result: dict[str, Any]

    @model_validator(mode="after")
    def _result_is_bound(self) -> "CollaborationCacheEnvelope":
        _require_sha(self.result_sha256, "result_sha256")
        if self.result_sha256 != _sha256(_canonical_bytes(self.result)):
            raise ValueError("cached result hash does not match its exact bytes")
        return self


def _first_attr(value: Any, *names: str) -> Any:
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    raise CollaborationStoreError(f"missing required collaboration field: {names[0]}")


def _run_part(run: Any, *names: str) -> Any:
    for name in names:
        value = getattr(run, name, None)
        if value is not None:
            return value
    raise CollaborationStoreError(f"collaboration run has no {names[0]}")


def _is_success(result: Any) -> bool:
    state = _enum_text(result.state).casefold()
    return state in {
        "returned", "cached", "success", "succeeded", "complete", "completed",
    }


def _validate_cached_result(identity: CollaborationCacheIdentity, result: Any) -> None:
    from agents.candidate_review_v1 import collaboration

    if not _is_success(result):
        raise CollaborationStoreError("only successful provider results may be cached")
    for label, actual, expected in (
        ("provider", _enum_text(result.provider), identity.provider),
        ("model", result.model, identity.model),
        ("request", result.input_sha256, identity.request_sha256),
        ("prompt", result.prompt_sha256, identity.prompt_sha256),
        ("schema", result.schema_sha256, identity.schema_sha256),
    ):
        if actual != expected:
            raise CollaborationStoreError(f"cached result {label} binding does not match")
    output = _first_attr(result, "output", "challenge", "result")
    layer = collaboration.CollaborationLayer(identity.layer)
    collaboration.challenge_output_model(layer).model_validate(output)


def _require_exact_result(result: Any, *, label: str) -> None:
    if type(result) is not ProviderCallResult:
        raise CollaborationStoreError(
            f"{label} must use the exact provider result contract"
        )
    if result.output is not None and type(result.output) is not (
        challenge_output_model(result.output.layer)
    ):
        raise CollaborationStoreError(
            f"{label} output must use the exact challenge contract"
        )


def _require_exact_run(run: Any) -> None:
    if type(run) is not CollaborationRun:
        raise CollaborationStoreError(
            "collaboration persistence requires the exact run contract"
        )
    if type(run.request) is not CollaborationRequest:
        raise CollaborationStoreError("run request must use its exact contract")
    try:
        validate_collaboration_request(run.request)
    except (TypeError, ValueError):
        raise CollaborationStoreError(
            "run request failed its exact contract"
        ) from None
    _require_exact_result(run.claude, label="Claude artifact")
    _require_exact_result(run.openai, label="OpenAI artifact")
    if type(run.comparison) is not CollaborationComparison:
        raise CollaborationStoreError("comparison must use its exact contract")


class CollaborationStore:
    """Persist run artifacts and successful provider caches without credentials."""

    def __init__(self, state_root: str | Path = DEFAULT_STATE_ROOT) -> None:
        self.state_root = Path(state_root)

    def persist(
        self,
        run: Any,
        *,
        client_id: str | None = None,
        run_id: str | None = None,
        committed_at: datetime | None = None,
    ) -> CollaborationStoreReceipt:
        from agents.candidate_review_v1 import collaboration

        _require_exact_run(run)
        run_bytes = _canonical_bytes(
            BaseModel.model_dump(run, mode="json", exclude_none=False)
        )
        try:
            run = collaboration.CollaborationRun.model_validate_json(run_bytes)
        except ValidationError as exc:
            raise CollaborationStoreError(
                "collaboration run failed its deterministic contract"
            ) from exc
        request = _run_part(run, "request")
        binding = request.binding
        resolved_client = client_id or binding.client_id
        resolved_run = run_id or binding.run_id
        if resolved_client != binding.client_id or resolved_run != binding.run_id:
            raise CollaborationStoreError(
                "collaboration destination does not match request binding"
            )
        layer = _enum_text(request.layer)
        mode = _enum_text(run.mode)
        disposition = _enum_text(run.disposition)
        payloads = {
            "claude.json": _run_part(run, "claude", "claude_result"),
            "openai.json": _run_part(run, "openai", "openai_result"),
            "comparison.json": _run_part(run, "comparison"),
        }
        encoded = {
            file_name: _canonical_bytes(payload)
            for file_name, payload in payloads.items()
        }
        bindings = tuple(
            CollaborationFileBinding(
                file_name=file_name,
                sha256=_sha256(encoded[file_name]),
                byte_size=len(encoded[file_name]),
            )
            for file_name in sorted(encoded)
        )
        execution_sha256 = _execution_digest(
            request_sha256=request.request_sha256,
            mode=mode,
            disposition=disposition,
            files=bindings,
        )
        destination = _safe_child(
            self.state_root,
            resolved_client,
            resolved_run,
            layer,
            execution_sha256,
        )
        for file_name, data in encoded.items():
            _write_immutable(destination / file_name, data)
        receipt = CollaborationStoreReceipt(
            client_id=resolved_client,
            run_id=resolved_run,
            request_sha256=request.request_sha256,
            layer=layer,
            mode=mode,
            disposition=disposition,
            execution_sha256=execution_sha256,
            files=bindings,
            committed_at=committed_at or datetime.now(timezone.utc),
        )
        receipt_path = destination / RECEIPT_FILENAME
        if receipt_path.exists():
            try:
                existing = CollaborationStoreReceipt.model_validate_json(
                    receipt_path.read_bytes()
                )
            except (OSError, ValidationError) as exc:
                raise CollaborationStoreError(
                    "existing collaboration receipt is corrupt"
                ) from exc
            replay = receipt.model_copy(update={"committed_at": existing.committed_at})
            if replay != existing:
                raise CollaborationStoreError("immutable collaboration receipt changed")
            return existing
        _write_immutable(receipt_path, _canonical_bytes(receipt))
        return receipt

    def save_cache(self, identity: CollaborationCacheIdentity, result: Any) -> Path:
        if type(identity) is not CollaborationCacheIdentity:
            raise CollaborationStoreError(
                "cache identity must use its exact contract"
            )
        _require_exact_result(result, label="cached artifact")
        _validate_cached_result(identity, result)
        result_payload = BaseModel.model_dump(
            result,
            mode="json",
            exclude_none=False,
        )
        result_bytes = _canonical_bytes(result_payload)
        envelope = CollaborationCacheEnvelope(
            identity=identity,
            result_sha256=_sha256(result_bytes),
            result=result_payload,
        )
        provider = re.sub(r"[^a-z0-9_-]+", "-", identity.provider.casefold()).strip("-")
        _require_safe_id(provider, "provider")
        target = _safe_child(self.state_root, "cache", provider, identity.cache_key)
        target = target.with_suffix(".json")
        _write_immutable(target, _canonical_bytes(envelope))
        return target

    def load_cache(self, identity: CollaborationCacheIdentity):
        from agents.candidate_review_v1 import collaboration

        if type(identity) is not CollaborationCacheIdentity:
            return None
        provider = re.sub(r"[^a-z0-9_-]+", "-", identity.provider.casefold()).strip("-")
        try:
            target = _safe_child(self.state_root, "cache", provider, identity.cache_key).with_suffix(".json")
            raw = target.read_bytes()
            if len(raw) > MAX_ARTIFACT_BYTES:
                return None
            envelope = CollaborationCacheEnvelope.model_validate_json(raw)
            if envelope.identity != identity:
                return None
            result = collaboration.ProviderCallResult.model_validate_json(
                _canonical_bytes(envelope.result)
            )
            _validate_cached_result(identity, result)
            updates = {
                "state": collaboration.CollaboratorState.CACHED,
                "attempts": 0,
            }
            if "cache_hit" in type(result).model_fields:
                updates["cache_hit"] = True
            payload = result.model_dump(mode="python")
            payload.update(updates)
            return collaboration.ProviderCallResult.model_validate(payload)
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
            CollaborationStoreError,
        ):
            return None


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "DEFAULT_STATE_ROOT",
    "RECEIPT_FILENAME",
    "STORE_SCHEMA_VERSION",
    "CollaborationCacheEnvelope",
    "CollaborationCacheIdentity",
    "CollaborationFileBinding",
    "CollaborationStore",
    "CollaborationStoreError",
    "CollaborationStoreReceipt",
]
