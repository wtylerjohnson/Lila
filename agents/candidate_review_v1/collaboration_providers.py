"""Hermetic provider adapters for Candidate Review collaboration.
OpenAI keys are per-call and never retained or returned.
Both adapters serialize :class:`CollaborationRequest` through the same helper
so the independent collaborators receive byte-identical evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agents.candidate_review_v1.collaboration import (
    CollaborationRequest,
    CollaboratorProvider,
    CollaboratorState,
    ProviderCallResult,
    TokenUsage,
    challenge_output_model,
    redact_sensitive_material,
    validate_collaboration_request,
    validate_provider_model,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
# A dated snapshot keeps cache identity honest. Operators may select another
# model token, but floating aliases are deliberately not cacheable by the
# collaboration runner.
DEFAULT_OPENAI_MODEL = "gpt-5-2025-08-07"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_PROVIDER_REPLY_BYTES = 1024 * 1024
OPENAI_MAX_OUTPUT_TOKENS = 12_000

_CREDENTIAL_NAME = (
    r"(?:"
    r"AWS_ACCESS_KEY_ID|"
    r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:KEY|TOKEN|SECRET|PASSWORD)|"
    r"api[_ -]?key|key|token|secret|password|passwd|client[_ -]?secret|"
    r"access[_ -]?token|auth(?:entication|orization)?(?:[_ -]?token)?|"
    r"oauth[_ -]?token|"
    r"contact(?:[_ -]?(?:email|phone|name))?"
    r")"
)
_SECRET_PATTERNS = (
    # Remove a naked credential variable/label as well.  Public failures are
    # diagnostic summaries, so retaining the sensitive marker adds no value.
    re.compile(r"(?i)(?<![A-Za-z0-9_])" + _CREDENTIAL_NAME + r"\b"),
    re.compile(r"(?i)traceback"),
)

T = TypeVar("T", bound=BaseModel)
PostJson = Callable[..., Mapping[str, Any]]
ClaudeRunner = Callable[..., str]


class CollaborationProviderError(RuntimeError):
    """A provider response could not be transported or validated."""


class CollaborationProviderRefusal(CollaborationProviderError):
    """The provider explicitly declined to produce the structured output."""


class CollaborationProviderMetadataError(CollaborationProviderError):
    """Provider provenance/accounting metadata was missing or malformed."""


def redact_provider_text(value: object, *, api_key: str | None = None) -> str:
    """Return operator-usable error text with credential shapes removed."""

    safe = redact_sensitive_material(value)
    if api_key:
        safe = safe.replace(api_key, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        safe = pattern.sub("[redacted]", safe)
    return safe


def canonical_request_bytes(request: CollaborationRequest) -> bytes:
    """Serialize only a rebuilt exact collaboration request."""

    validated = validate_collaboration_request(request)
    return validated.canonical_bytes


def _token_usage(payload: Mapping[str, Any] | None):
    if not payload:
        return None
    input_tokens = int(payload.get("input_tokens") or payload.get("prompt_tokens") or 0)
    output_tokens = int(payload.get("output_tokens") or payload.get("completion_tokens") or 0)
    total_tokens = input_tokens + output_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _validated_openai_usage(payload: object) -> Mapping[str, int]:
    if not isinstance(payload, Mapping):
        raise CollaborationProviderMetadataError(
            "OpenAI response omitted token accounting"
        )
    values: dict[str, int] = {}
    for canonical, alternatives in (
        ("input_tokens", ("input_tokens", "prompt_tokens")),
        ("output_tokens", ("output_tokens", "completion_tokens")),
    ):
        value = next(
            (payload[name] for name in alternatives if name in payload),
            None,
        )
        if type(value) is not int or value < 0:
            raise CollaborationProviderMetadataError(
                "OpenAI token accounting is malformed"
            )
        values[canonical] = value
    reported_total = payload.get("total_tokens")
    expected_total = values["input_tokens"] + values["output_tokens"]
    if reported_total is not None and (
        type(reported_total) is not int
        or reported_total < 0
        or reported_total != expected_total
    ):
        raise CollaborationProviderMetadataError(
            "OpenAI token accounting does not reconcile"
        )
    return values


def _merge_usage(rows: list[Mapping[str, Any] | None]):
    present = [row for row in rows if row]
    if not present:
        return None
    return _token_usage({
        "input_tokens": sum(int(row.get("input_tokens") or row.get("prompt_tokens") or 0)
                            for row in present),
        "output_tokens": sum(int(row.get("output_tokens") or row.get("completion_tokens") or 0)
                             for row in present),
    })


def _result(
    *,
    request: Any,
    provider: CollaboratorProvider,
    model: str,
    output: BaseModel | None,
    attempts: int,
    prompt_sha256: str,
    schema_sha256: str,
    started_at: datetime,
    response_id: str | None = None,
    token_usage: TokenUsage | None = None,
    error: str | None = None,
) -> ProviderCallResult:
    """Construct the versioned core result without persisting transport data."""

    safe_error = redact_provider_text(error)[:500] if error else None
    return ProviderCallResult(
        provider=provider,
        state=CollaboratorState.RETURNED if output is not None
        else CollaboratorState.FAILED,
        model=model,
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        input_sha256=request.request_sha256,
        output=output,
        attempts=attempts,
        token_usage=token_usage,
        public_failure=safe_error,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        response_id=_safe_response_id(response_id),
    )


def _not_run_result(*, request: Any, provider: Any, model: str,
                    prompt_sha256: str, schema_sha256: str):
    return ProviderCallResult(
        provider=provider,
        state=CollaboratorState.NOT_RUN,
        model=model,
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        input_sha256=request.request_sha256,
        output=None,
        attempts=0,
        token_usage=None,
        public_failure=None,
        started_at=None,
        completed_at=None,
        response_id=None,
    )


def _safe_response_id(value: object) -> str | None:
    text = str(value or "")
    if redact_provider_text(text) != text:
        return None
    return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text) else None


def _valid_openai_key(value: str) -> bool:
    return bool(re.fullmatch(r"sk-[A-Za-z0-9_-]{17,509}", value))


def _output_model(request: Any) -> type[BaseModel]:
    model = challenge_output_model(request.layer)
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError("challenge_output_model must return a Pydantic model")
    return model


def _system_prompt(request: Any) -> str:
    layer = getattr(getattr(request, "layer", None), "value", request.layer)
    return (
        "You are an independent federal-market analytical challenger. Work only "
        "from the supplied sanitized evidence. Separate evidence from inference, "
        "identify counterevidence and gaps, and never issue a bid/no-bid decision "
        "or mutate an analyst conclusion. Return only the requested structured "
        f"JSON for collaboration layer {layer}."
    )


def _validate_output(text: str, model: type[T]) -> T:
    if not text.strip():
        raise CollaborationProviderError("provider returned empty output")
    if len(text.encode("utf-8")) > MAX_PROVIDER_REPLY_BYTES:
        raise CollaborationProviderError("provider output exceeds its byte budget")
    return model.model_validate_json(text)


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize Pydantic JSON Schema for Responses strict mode.

    OpenAI strict schemas require every object to reject additional properties
    and to list every property as required. Pydantic already represents an
    optional field with a null union, so making its name required preserves the
    value semantics while removing unsupported omission/default semantics.
    """

    normalized: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default":
            continue
        if isinstance(value, Mapping):
            normalized[key] = _strict_json_schema(value)
        elif isinstance(value, list):
            normalized[key] = [
                _strict_json_schema(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            normalized[key] = value
    properties = normalized.get("properties")
    if isinstance(properties, Mapping) or normalized.get("type") == "object":
        properties = properties if isinstance(properties, Mapping) else {}
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized


def provider_contract_hashes(
    request: CollaborationRequest,
) -> tuple[str, str]:
    """Hash the exact prompt and strict output schema sent to either lane."""

    request = validate_collaboration_request(request)
    strict_schema = _strict_json_schema(
        _output_model(request).model_json_schema()
    )
    prompt_sha256 = _sha256_json({
        "prompt_version": request.prompt_version,
        "system": _system_prompt(request),
    })
    return prompt_sha256, _sha256_json(strict_schema)


def _openai_output_text(response: Mapping[str, Any]) -> str:
    refusal = response.get("refusal")
    if refusal:
        raise CollaborationProviderRefusal(str(refusal))
    direct = response.get("output_text")
    parsed = response.get("output_parsed")
    text_parts: list[str] = []
    refusal_parts: list[str] = []
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "refusal" or block.get("refusal"):
                    refusal_parts.append(str(block.get("refusal") or block.get("text") or "refused"))
                elif block_type in {"output_text", "text"}:
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
    if refusal_parts:
        raise CollaborationProviderRefusal(" ".join(refusal_parts))
    if text_parts:
        return "".join(text_parts)
    if isinstance(direct, str) and direct.strip():
        return direct
    if isinstance(parsed, Mapping):
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    raise CollaborationProviderError("OpenAI response contained no output text")


def _openai_body(
    *,
    request_text: str,
    request: Any,
    model: str,
    output_model: type[BaseModel],
    repair: str | None = None,
) -> dict[str, Any]:
    input_rows: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": _system_prompt(request)}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": request_text}],
        },
    ]
    if repair:
        input_rows.append({
            "role": "user",
            "content": [{"type": "input_text", "text": repair}],
        })
    layer = getattr(getattr(request, "layer", None), "value", request.layer)
    schema_name = re.sub(r"[^a-z0-9_]+", "_", f"candidate_review_{layer}_challenge".casefold())
    return {
        "model": model,
        "store": False,
        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
        "input": input_rows,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": _strict_json_schema(output_model.model_json_schema()),
            }
        },
    }


class OpenAIResponsesAdapter:
    """One-call-scope OpenAI Responses API adapter."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        post_json: PostJson | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        validated_model = validate_provider_model(model)
        if timeout_s <= 0 or timeout_s > 300:
            raise ValueError("provider timeout must be between 0 and 300 seconds")
        if post_json is None:
            from tools.api._http import post_json as default_post_json

            post_json = default_post_json
        self.model = validated_model
        self._post_json = post_json
        self.timeout_s = float(timeout_s)

    def call(self, request: CollaborationRequest, *, api_key: str):
        request = validate_collaboration_request(request)
        started_at = datetime.now(timezone.utc)
        provider = CollaboratorProvider.OPENAI
        output_model = _output_model(request)
        prompt_sha256, schema_sha256 = provider_contract_hashes(request)
        key = api_key.strip()
        if not _valid_openai_key(key):
            return _not_run_result(
                request=request,
                provider=provider,
                model=self.model,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
            )

        request_text = canonical_request_bytes(request).decode("utf-8")
        response_id: str | None = None
        usage_rows: list[Mapping[str, Any] | None] = []
        first_reply: str | None = None
        first_error: Exception | None = None

        for attempt in range(1, 3):
            reply = None
            repair = None
            if attempt == 2:
                assert first_error is not None
                repair = (
                    "Your prior structured response failed validation. Return only a "
                    "corrected JSON object. Prior response:\n"
                    f"{redact_provider_text(first_reply or '', api_key=key)[:4000]}"
                    "\nValidation error:\n"
                    f"{redact_provider_text(first_error, api_key=key)[:1200]}"
                )
            body = _openai_body(
                request_text=request_text,
                request=request,
                model=self.model,
                output_model=output_model,
                repair=repair,
            )
            try:
                response = self._post_json(
                    OPENAI_RESPONSES_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=self.timeout_s,
                    retries=1,
                )
                if not isinstance(response, Mapping):
                    raise CollaborationProviderError("OpenAI response root is not an object")
                if response.get("error"):
                    raise RuntimeError("OpenAI response reported a provider error")
                resolved_model = response.get("model")
                if re.fullmatch(
                    r".+-[0-9]{4}-[0-9]{2}-[0-9]{2}", self.model
                ) and (
                    not isinstance(resolved_model, str)
                    or resolved_model != self.model
                ):
                    return _result(
                        request=request,
                        provider=provider,
                        model=self.model,
                        output=None,
                        attempts=attempt,
                        prompt_sha256=prompt_sha256,
                        schema_sha256=schema_sha256,
                        started_at=started_at,
                        error=(
                            "OpenAI resolved a different model than the "
                            "pinned snapshot."
                        ),
                    )
                if response.get("id"):
                    response_id = _safe_response_id(response["id"])
                usage_rows.append(_validated_openai_usage(response.get("usage")))
                reply = _openai_output_text(response)
                output = _validate_output(reply, output_model)
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=output,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    response_id=response_id,
                    token_usage=_merge_usage(usage_rows),
                )
            except CollaborationProviderRefusal as exc:
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=None,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    response_id=response_id,
                    token_usage=_merge_usage(usage_rows),
                    error=f"OpenAI refused the structured challenge: {redact_provider_text(exc, api_key=key)[:420]}",
                )
            except CollaborationProviderMetadataError as exc:
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=None,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    response_id=response_id,
                    token_usage=_merge_usage(usage_rows),
                    error=(
                        "OpenAI response accounting failed: "
                        f"{redact_provider_text(exc, api_key=key)[:420]}"
                    ),
                )
            except (json.JSONDecodeError, ValidationError, CollaborationProviderError) as exc:
                if attempt == 1:
                    first_reply = reply
                    first_error = exc
                    continue
                error = f"OpenAI structured output failed after one repair: {exc}"
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=None,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    response_id=response_id,
                    token_usage=_merge_usage(usage_rows),
                    error=redact_provider_text(error, api_key=key)[:500],
                )
            except Exception as exc:  # noqa: BLE001 - transport boundary is typed
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=None,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    response_id=response_id,
                    token_usage=_merge_usage(usage_rows),
                    error=("OpenAI provider call failed: "
                           + redact_provider_text(exc, api_key=key)[:470]),
                )

        raise AssertionError("bounded provider loop exhausted")


class ClaudeMaxAdapter:
    """Claude Code Max-plan adapter with the same strict request boundary."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_CLAUDE_MODEL,
        run_claude: ClaudeRunner | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        validated_model = validate_provider_model(model)
        if timeout_s <= 0 or timeout_s > 300:
            raise ValueError("provider timeout must be between 0 and 300 seconds")
        if run_claude is None:
            from agents.decisions.maxplan_cli import run_claude as default_runner

            run_claude = default_runner
        self.model = validated_model
        self._run_claude = run_claude
        self.timeout_s = float(timeout_s)

    def call(self, request: CollaborationRequest):
        request = validate_collaboration_request(request)
        started_at = datetime.now(timezone.utc)
        provider = CollaboratorProvider.CLAUDE
        request_text = canonical_request_bytes(request).decode("utf-8")
        output_model = _output_model(request)
        strict_schema = _strict_json_schema(output_model.model_json_schema())
        prompt_sha256, schema_sha256 = provider_contract_hashes(request)
        schema = json.dumps(
            strict_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        instruction = (
            f"{request_text}\n\nReturn ONLY one JSON object validating against "
            f"this schema:\n{schema}"
        )
        first_reply: str | None = None
        first_error: Exception | None = None
        for attempt in range(1, 3):
            prompt = instruction
            if attempt == 2:
                assert first_error is not None
                prompt += (
                    "\n\nYour prior response failed validation. Return only the "
                    "corrected JSON object. Prior response:\n"
                    f"{redact_provider_text(first_reply or '')[:4000]}"
                    f"\nValidation error:\n{redact_provider_text(first_error)[:1200]}"
                )
            try:
                reply = self._run_claude(
                    prompt,
                    system=_system_prompt(request),
                    model=self.model,
                    timeout_s=self.timeout_s,
                    isolated=True,
                )
                output = _validate_output(reply, output_model)
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=output,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                )
            except (json.JSONDecodeError, ValidationError, CollaborationProviderError) as exc:
                if attempt == 1:
                    first_reply = reply
                    first_error = exc
                    continue
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=None,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    error=("Claude structured output failed after one repair: "
                           + redact_provider_text(exc)[:420]),
                )
            except Exception as exc:  # noqa: BLE001 - CLI boundary is typed
                return _result(
                    request=request,
                    provider=provider,
                    model=self.model,
                    output=None,
                    attempts=attempt,
                    prompt_sha256=prompt_sha256,
                    schema_sha256=schema_sha256,
                    started_at=started_at,
                    error="Claude Max provider call failed: " + redact_provider_text(exc)[:460],
                )
        raise AssertionError("bounded provider loop exhausted")


__all__ = [
    "ClaudeMaxAdapter",
    "CollaborationProviderError",
    "CollaborationProviderMetadataError",
    "CollaborationProviderRefusal",
    "DEFAULT_CLAUDE_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_PROVIDER_REPLY_BYTES",
    "OPENAI_MAX_OUTPUT_TOKENS",
    "OPENAI_RESPONSES_URL",
    "OpenAIResponsesAdapter",
    "canonical_request_bytes",
    "provider_contract_hashes",
    "redact_provider_text",
]
