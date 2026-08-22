"""Thin execution boundary for Candidate Review collaboration.

The runner deliberately owns no credentials and no analyst packet.  It sends
the same immutable :class:`CollaborationRequest` to each enabled provider,
uses only exact provider-specific cache identities, and persists the two
provider artifacts and their deterministic comparison separately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.candidate_review_v1.collaboration import (
    CollaborationLayer,
    CollaborationMode,
    CollaborationRequest,
    CollaborationRun,
    CollaboratorProvider,
    CollaboratorState,
    ProviderCallResult,
    RunDisposition,
    challenge_output_model,
    validate_collaboration_request,
    validate_provider_model,
)
from agents.candidate_review_v1.collaboration_store import (
    CollaborationCacheIdentity,
    CollaborationStoreReceipt,
)
from agents.candidate_review_v1.collaboration_providers import (
    provider_contract_hashes,
)


class CollaborationRunnerError(RuntimeError):
    """The collaboration execution boundary could not preserve its contract."""


_DATED_MODEL_RE = re.compile(r".+-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CLAUDE_SNAPSHOT_RE = re.compile(
    r"claude-[a-z0-9.-]+(?:-[0-9]{8}|-[0-9]+-[0-9]+)$",
    re.I,
)
_FLOATING_MODEL_SEGMENTS = frozenset({"current", "latest"})


def _model_cache_is_stable(
    provider: CollaboratorProvider,
    model: str,
) -> bool:
    """Never reuse conclusions across a floating provider alias."""

    segments = frozenset(re.findall(r"[a-z0-9]+", model.casefold()))
    if segments & _FLOATING_MODEL_SEGMENTS:
        return False
    if provider is CollaboratorProvider.OPENAI:
        return _DATED_MODEL_RE.fullmatch(model) is not None
    return _CLAUDE_SNAPSHOT_RE.fullmatch(model) is not None


def _internal_request_fingerprint(request: CollaborationRequest) -> str:
    """Bind internal provenance omitted from the provider-facing payload."""

    validated = validate_collaboration_request(request)
    payload = BaseModel.model_dump(
        validated,
        mode="json",
        exclude_none=False,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProviderExecutionSource(str, Enum):
    CACHE = "cache"
    PROVIDER = "provider"
    MODE_OFF = "mode_off"
    MISSING_KEY = "missing_key"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        strict=True,
        str_strip_whitespace=True,
    )


class CollaborationExecutionReceipt(_FrozenModel):
    """Credential-free proof of how the two collaboration lanes resolved."""

    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layer: CollaborationLayer
    mode: CollaborationMode
    disposition: RunDisposition
    claude_state: CollaboratorState
    openai_state: CollaboratorState
    claude_source: ProviderExecutionSource
    openai_source: ProviderExecutionSource
    store: CollaborationStoreReceipt
    analyst_packet_mutated: Literal[False] = False

    @model_validator(mode="after")
    def _receipt_is_closed(self) -> "CollaborationExecutionReceipt":
        if self.store.request_sha256 != self.request_sha256:
            raise ValueError("store receipt belongs to another request")
        if self.store.layer != self.layer.value:
            raise ValueError("store receipt belongs to another collaboration layer")
        if self.store.mode != self.mode.value:
            raise ValueError("store receipt belongs to another collaboration mode")
        if self.store.disposition != self.disposition.value:
            raise ValueError("store receipt has another run disposition")
        if self.claude_source is ProviderExecutionSource.CACHE:
            if self.claude_state is not CollaboratorState.CACHED:
                raise ValueError("Claude cache source requires cached state")
        elif self.claude_source is ProviderExecutionSource.PROVIDER:
            if self.claude_state is CollaboratorState.CACHED:
                raise ValueError("Claude provider source cannot claim cached state")
        else:
            raise ValueError("Claude must resolve from cache or its provider")
        if self.openai_source is ProviderExecutionSource.CACHE:
            if self.openai_state is not CollaboratorState.CACHED:
                raise ValueError("OpenAI cache source requires cached state")
        elif self.openai_source is ProviderExecutionSource.MODE_OFF:
            if self.mode is not CollaborationMode.OFF \
                    or self.openai_state is not CollaboratorState.NOT_RUN:
                raise ValueError("mode-off source requires off mode and not-run state")
        elif self.openai_source is ProviderExecutionSource.MISSING_KEY:
            if self.mode is CollaborationMode.OFF \
                    or self.openai_state is not CollaboratorState.NOT_RUN:
                raise ValueError("missing-key source requires enabled, not-run OpenAI")
        elif self.openai_source is ProviderExecutionSource.PROVIDER:
            if self.mode is CollaborationMode.OFF:
                raise ValueError("off mode cannot use the OpenAI provider")
            if self.openai_state is CollaboratorState.CACHED:
                raise ValueError("OpenAI provider source cannot claim cached state")
        else:  # pragma: no cover - strict enum construction already prevents this
            raise ValueError("unknown OpenAI execution source")
        return self


class CollaborationExecutionResult(_FrozenModel):
    run: CollaborationRun
    receipt: CollaborationExecutionReceipt

    @model_validator(mode="after")
    def _result_matches_receipt(self) -> "CollaborationExecutionResult":
        receipt = self.receipt
        run = self.run
        expected = (
            (receipt.request_sha256, run.request.request_sha256, "request"),
            (receipt.layer, run.request.layer, "layer"),
            (receipt.mode, run.mode, "mode"),
            (receipt.disposition, run.disposition, "disposition"),
            (receipt.claude_state, run.claude.state, "Claude state"),
            (receipt.openai_state, run.openai.state, "OpenAI state"),
            (receipt.analyst_packet_mutated, run.analyst_packet_mutated,
             "analyst mutation flag"),
        )
        for actual, bound, label in expected:
            if actual != bound:
                raise ValueError(f"execution receipt {label} does not match run")
        return self


class ClaudeAdapter(Protocol):
    model: str

    def call(self, request: CollaborationRequest) -> ProviderCallResult:
        ...


class OpenAIAdapter(Protocol):
    model: str

    def call(
        self,
        request: CollaborationRequest,
        *,
        api_key: str,
    ) -> ProviderCallResult:
        ...


class CollaborationStoreLike(Protocol):
    def load_cache(
        self,
        identity: CollaborationCacheIdentity,
    ) -> ProviderCallResult | None:
        ...

    def save_cache(
        self,
        identity: CollaborationCacheIdentity,
        result: ProviderCallResult,
    ) -> object:
        ...

    def persist(
        self,
        run: CollaborationRun,
        *,
        committed_at: datetime | None = None,
    ) -> CollaborationStoreReceipt:
        ...


def _not_run(
    request: CollaborationRequest,
    *,
    provider: CollaboratorProvider,
    model: str,
    prompt_sha256: str,
    schema_sha256: str,
) -> ProviderCallResult:
    return ProviderCallResult(
        provider=provider,
        state=CollaboratorState.NOT_RUN,
        model=model,
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        input_sha256=request.request_sha256,
        output=None,
        attempts=0,
    )


def _failed(
    request: CollaborationRequest,
    *,
    provider: CollaboratorProvider,
    model: str,
    prompt_sha256: str,
    schema_sha256: str,
) -> ProviderCallResult:
    now = datetime.now(timezone.utc)
    provider_label = (
        "OpenAI" if provider is CollaboratorProvider.OPENAI else "Claude")
    return ProviderCallResult(
        provider=provider,
        state=CollaboratorState.FAILED,
        model=model,
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        input_sha256=request.request_sha256,
        output=None,
        attempts=1,
        public_failure=f"{provider_label} provider adapter failed.",
        started_at=now,
        completed_at=now,
    )


def _validate_lane(
    request: CollaborationRequest,
    result: ProviderCallResult,
    *,
    provider: CollaboratorProvider,
    model: str,
    prompt_sha256: str,
    schema_sha256: str,
) -> ProviderCallResult:
    if type(result) is not ProviderCallResult:
        raise CollaborationRunnerError(
            "provider artifact must use its exact result contract"
        )
    if result.output is not None and type(result.output) is not (
        challenge_output_model(request.layer)
    ):
        raise CollaborationRunnerError(
            "provider output must use its exact challenge contract"
        )
    result = ProviderCallResult.model_validate(
        BaseModel.model_dump(result, mode="python", exclude_none=False)
    )
    if result.provider is not provider:
        raise CollaborationRunnerError("provider artifact arrived in the wrong lane")
    if result.model != model:
        raise CollaborationRunnerError("provider artifact model binding changed")
    if result.input_sha256 != request.request_sha256:
        raise CollaborationRunnerError("provider artifact belongs to another request")
    if result.prompt_sha256 != prompt_sha256:
        raise CollaborationRunnerError("provider artifact prompt binding changed")
    if result.schema_sha256 != schema_sha256:
        raise CollaborationRunnerError("provider artifact schema binding changed")
    if result.output is not None and result.output.layer != request.layer:
        raise CollaborationRunnerError("provider output belongs to another layer")
    return result


class CollaborationRunner:
    """Execute Claude baseline plus the optional independent OpenAI challenger."""

    def __init__(
        self,
        *,
        claude_adapter: ClaudeAdapter,
        openai_adapter: OpenAIAdapter,
        store: CollaborationStoreLike,
    ) -> None:
        try:
            claude_model = validate_provider_model(claude_adapter.model)
            openai_model = validate_provider_model(openai_adapter.model)
        except (TypeError, ValueError):
            raise ValueError("collaboration provider model is invalid") from None
        self._claude_adapter = claude_adapter
        self._openai_adapter = openai_adapter
        self._store = store
        self._claude_model = claude_model
        self._openai_model = openai_model

    def __repr__(self) -> str:
        return (
            "CollaborationRunner("
            f"claude_model={self._claude_model!r}, "
            f"openai_model={self._openai_model!r})"
        )

    def _cache_or_call_claude(
        self,
        request: CollaborationRequest,
        *,
        prompt_sha256: str,
        schema_sha256: str,
    ) -> tuple[ProviderCallResult, ProviderExecutionSource]:
        safe_request = validate_collaboration_request(request)
        expected_fingerprint = _internal_request_fingerprint(safe_request)
        cacheable = _model_cache_is_stable(
            CollaboratorProvider.CLAUDE, self._claude_model)
        identity = None
        if cacheable:
            identity = CollaborationCacheIdentity.from_request(
                safe_request,
                provider=CollaboratorProvider.CLAUDE,
                model=self._claude_model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )
            cached = self._store.load_cache(identity)
            if cached is not None:
                try:
                    return (
                        _validate_lane(
                            safe_request,
                            cached,
                            provider=CollaboratorProvider.CLAUDE,
                            model=self._claude_model,
                            prompt_sha256=prompt_sha256,
                            schema_sha256=schema_sha256,
                        ),
                        ProviderExecutionSource.CACHE,
                    )
                except (TypeError, ValueError, CollaborationRunnerError):
                    pass
        try:
            raw_result = self._claude_adapter.call(request)
            if _internal_request_fingerprint(request) != expected_fingerprint:
                raise CollaborationRunnerError(
                    "Claude adapter mutated its isolated request"
                )
            result = _validate_lane(
                safe_request,
                raw_result,
                provider=CollaboratorProvider.CLAUDE,
                model=self._claude_model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )
        except Exception:  # noqa: BLE001 - provider boundary becomes safe artifact
            result = _failed(
                safe_request,
                provider=CollaboratorProvider.CLAUDE,
                model=self._claude_model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )
        if identity is not None and result.state is CollaboratorState.RETURNED:
            self._store.save_cache(identity, result)
        return result, ProviderExecutionSource.PROVIDER

    def _cache_or_call_openai(
        self,
        request: CollaborationRequest,
        *,
        mode: CollaborationMode,
        api_key: str | None,
        prompt_sha256: str,
        schema_sha256: str,
    ) -> tuple[ProviderCallResult, ProviderExecutionSource]:
        safe_request = validate_collaboration_request(request)
        expected_fingerprint = _internal_request_fingerprint(safe_request)
        if mode is CollaborationMode.OFF:
            return (
                _not_run(
                    safe_request,
                    provider=CollaboratorProvider.OPENAI,
                    model=self._openai_model,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                ),
                ProviderExecutionSource.MODE_OFF,
            )
        cacheable = _model_cache_is_stable(
            CollaboratorProvider.OPENAI, self._openai_model)
        identity = None
        if cacheable:
            identity = CollaborationCacheIdentity.from_request(
                safe_request,
                provider=CollaboratorProvider.OPENAI,
                model=self._openai_model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )
            cached = self._store.load_cache(identity)
            if cached is not None:
                try:
                    return (
                        _validate_lane(
                            safe_request,
                            cached,
                            provider=CollaboratorProvider.OPENAI,
                            model=self._openai_model,
                            prompt_sha256=prompt_sha256,
                            schema_sha256=schema_sha256,
                        ),
                        ProviderExecutionSource.CACHE,
                    )
                except (TypeError, ValueError, CollaborationRunnerError):
                    pass
        if not (api_key or "").strip():
            return (
                _not_run(
                    safe_request,
                    provider=CollaboratorProvider.OPENAI,
                    model=self._openai_model,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                ),
                ProviderExecutionSource.MISSING_KEY,
            )
        try:
            raw_result = self._openai_adapter.call(
                request,
                api_key=api_key,
            )
            if _internal_request_fingerprint(request) != expected_fingerprint:
                raise CollaborationRunnerError(
                    "OpenAI adapter mutated its isolated request"
                )
            result = _validate_lane(
                safe_request,
                raw_result,
                provider=CollaboratorProvider.OPENAI,
                model=self._openai_model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )
        except Exception:  # noqa: BLE001 - provider boundary becomes safe artifact
            result = _failed(
                safe_request,
                provider=CollaboratorProvider.OPENAI,
                model=self._openai_model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )
        if identity is not None and result.state is CollaboratorState.RETURNED:
            self._store.save_cache(identity, result)
        return result, ProviderExecutionSource.PROVIDER

    def execute(
        self,
        request: CollaborationRequest,
        *,
        mode: CollaborationMode,
        openai_api_key: str | None = None,
        committed_at: datetime | None = None,
    ) -> CollaborationExecutionResult:
        """Run enabled lanes; ``openai_api_key`` exists only for this call."""

        try:
            baseline_request = validate_collaboration_request(request)
        except (TypeError, ValueError):
            raise CollaborationRunnerError(
                "collaboration request failed its exact contract"
            ) from None
        mode = CollaborationMode(mode)
        baseline_fingerprint = _internal_request_fingerprint(baseline_request)
        prompt_sha256, schema_sha256 = provider_contract_hashes(
            baseline_request
        )
        claude_request = validate_collaboration_request(baseline_request)
        claude, claude_source = self._cache_or_call_claude(
            claude_request,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
        )
        if _internal_request_fingerprint(
            baseline_request
        ) != baseline_fingerprint:
            raise CollaborationRunnerError(
                "Claude lane changed the baseline collaboration request"
            )
        openai_request = validate_collaboration_request(baseline_request)
        openai, openai_source = self._cache_or_call_openai(
            openai_request,
            mode=mode,
            api_key=openai_api_key,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
        )
        if _internal_request_fingerprint(
            baseline_request
        ) != baseline_fingerprint:
            raise CollaborationRunnerError(
                "OpenAI lane changed the baseline collaboration request"
            )
        run = CollaborationRun.create(
            request=baseline_request,
            mode=mode,
            claude=claude,
            openai=openai,
        )
        store_receipt = self._store.persist(run, committed_at=committed_at)
        receipt = CollaborationExecutionReceipt(
            request_sha256=baseline_request.request_sha256,
            layer=baseline_request.layer,
            mode=mode,
            disposition=run.disposition,
            claude_state=claude.state,
            openai_state=openai.state,
            claude_source=claude_source,
            openai_source=openai_source,
            store=store_receipt,
        )
        return CollaborationExecutionResult(run=run, receipt=receipt)


__all__ = (
    "ClaudeAdapter",
    "CollaborationExecutionReceipt",
    "CollaborationExecutionResult",
    "CollaborationRunner",
    "CollaborationRunnerError",
    "CollaborationStoreLike",
    "OpenAIAdapter",
    "ProviderExecutionSource",
)
