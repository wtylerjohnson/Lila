"""Hermetic tests for Candidate Review collaboration provider adapters."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from pydantic import BaseModel

from agents.candidate_review_v1.collaboration import (
    CollaborationLayer,
    CollaborationRequest,
    CollaboratorState,
    IntakeChallengeOutput,
    IntakeSuggestion,
    SanitizedIntake,
    SuggestionClassification,
    SuggestionKind,
)
from agents.candidate_review_v1.collaboration_providers import (
    ClaudeMaxAdapter,
    DEFAULT_OPENAI_MODEL,
    OPENAI_RESPONSES_URL,
    MAX_PROVIDER_REPLY_BYTES,
    OPENAI_MAX_OUTPUT_TOKENS,
    OpenAIResponsesAdapter,
    _strict_json_schema,
    canonical_request_bytes,
    provider_contract_hashes,
    redact_provider_text,
)
from agents.candidate_review_v1.contracts import ArtifactBinding


KEY = "sk-test-session-key-1234567890"
AUTH_MARKERS = {
    "ANTHROPIC_AUTH_TOKEN": "anthropic-auth-marker",
    "CLAUDE_CODE_OAUTH_TOKEN": "claude-oauth-marker",
    "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-marker",
    "AWS_SESSION_TOKEN": "aws-session-marker",
    "TENANT_PASSWORD": "tenant-password-marker",
    "MYSTERY_KEY": "mystery-key-marker",
}
CONTACT_EMAIL = "operator@example.test"
CONTACT_PHONE = "202-555-0199"
COMMON_TOKEN_MARKERS = (
    "xoxb-" + "a" * 24,
    "xapp-" + "a" * 24,
    "glpat-" + "a" * 24,
    "AIza" + "a" * 32,
    "ya29." + "a" * 24,
    "npm_" + "a" * 32,
    "sk_live_" + "a" * 24,
    "rk_test_" + "a" * 24,
    "eyJhbGciOiJI.eyJzdWIiOiIxMjM0.NiIsInRlc3Qi",
)


def _adversarial_leak_text() -> str:
    assignments = "; ".join(
        f"{name}={value}" for name, value in AUTH_MARKERS.items()
    )
    return (
        f"Traceback {assignments}; contact_email={CONTACT_EMAIL}; "
        f"contact_phone={CONTACT_PHONE}; password is natural-language-marker; "
        "-----BEGIN PRIVATE KEY----- FAKE-PEM-MARKER "
        "-----END PRIVATE KEY-----"
    )


def _assert_no_adversarial_markers(value: str) -> None:
    lowered = value.casefold()
    for name, marker in AUTH_MARKERS.items():
        assert name.casefold() not in lowered
        assert marker.casefold() not in lowered
    for marker in (
        CONTACT_EMAIL,
        CONTACT_PHONE,
        "natural-language-marker",
        "FAKE-PEM-MARKER",
        "private key",
        "traceback",
    ):
        assert marker.casefold() not in lowered


def _binding() -> ArtifactBinding:
    return ArtifactBinding(
        client_id="testco",
        client_name="Testco",
        run_id="run-1",
        scope_designator="government-wide",
        scope_sha256="a" * 64,
        profile_sha256="b" * 64,
        evidence_snapshot_sha256="c" * 64,
    )


def _request() -> CollaborationRequest:
    return CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INTAKE,
        sanitized_intake=SanitizedIntake(
            client_name="Testco",
            primary_services="Secure data labeling",
            known_naics=("541511",),
        ),
    )


def _output(value: str = "data labeling") -> IntakeChallengeOutput:
    return IntakeChallengeOutput(suggestions=(IntakeSuggestion(
        kind=SuggestionKind.KEYWORD,
        classification=SuggestionClassification.CORE,
        value=value,
        rationale="The intake describes secure data labeling services.",
        source_refs=("intake-form",),
    ),))


def _response(
    output=None,
    *,
    response_id="resp_123",
    usage=None,
    model=DEFAULT_OPENAI_MODEL,
):
    output = output or _output()
    return {
        "id": response_id,
        "model": model,
        "output": [{
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": output.model_dump_json(),
            }],
        }],
        "usage": usage or {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
        },
    }


def _walk_objects(value):
    if isinstance(value, dict):
        if value.get("type") == "object" or "properties" in value:
            yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def test_openai_responses_call_is_non_stored_strict_and_credential_free():
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs))
        return _response()

    request = _request()
    adapter = OpenAIResponsesAdapter(
        model="gpt-test",
        post_json=fake_post,
        timeout_s=17,
    )
    result = adapter.call(request, api_key=KEY)

    assert result.state is CollaboratorState.RETURNED
    assert result.output == _output()
    assert result.attempts == 1
    assert result.response_id == "resp_123"
    assert result.token_usage.input_tokens == 11
    assert result.token_usage.output_tokens == 7
    assert result.token_usage.total_tokens == 18
    assert len(seen) == 1
    url, kwargs = seen[0]
    assert url == OPENAI_RESPONSES_URL
    assert kwargs["timeout"] == 17
    assert kwargs["retries"] == 1
    assert kwargs["headers"] == {"Authorization": f"Bearer {KEY}"}
    body = kwargs["json"]
    assert body["store"] is False
    assert body["max_output_tokens"] == OPENAI_MAX_OUTPUT_TOKENS
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["input"][1]["content"][0]["text"] == request.canonical_json()
    persisted_shape = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert KEY not in persisted_shape
    assert "Bearer" not in persisted_shape
    assert (result.prompt_sha256, result.schema_sha256) \
        == provider_contract_hashes(request)


def test_strict_schema_closes_and_requires_every_object_property():
    schema = _strict_json_schema(IntakeChallengeOutput.model_json_schema())
    objects = tuple(_walk_objects(schema))

    assert objects
    for node in objects:
        properties = node.get("properties", {})
        assert node["additionalProperties"] is False
        assert node["required"] == list(properties)
    assert "default" not in json.dumps(schema)


def test_openai_schema_failure_gets_exactly_one_repair_and_sums_usage():
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "id": "resp_bad",
                "model": DEFAULT_OPENAI_MODEL,
                "output_text": "not-json",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
        return _response(
            response_id="resp_fixed",
            usage={"input_tokens": 5, "output_tokens": 4},
        )

    request = _request()
    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        request,
        api_key=KEY,
    )

    assert result.state is CollaboratorState.RETURNED
    assert result.attempts == 2
    assert result.response_id == "resp_fixed"
    assert result.token_usage.input_tokens == 8
    assert result.token_usage.output_tokens == 6
    assert result.token_usage.total_tokens == 14
    assert len(calls) == 2
    for call in calls:
        assert call["json"]["store"] is False
        assert call["retries"] == 1
        assert call["json"]["input"][1]["content"][0]["text"] \
            == request.canonical_json()
    assert len(calls[1]["json"]["input"]) == 3


def test_openai_repair_prompt_redacts_prior_reply_credentials_and_contacts():
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "id": "resp_bad",
                "model": DEFAULT_OPENAI_MODEL,
                "output_text": _adversarial_leak_text(),
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
        return _response(response_id="resp_fixed")

    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )

    assert result.state is CollaboratorState.RETURNED
    assert result.attempts == 2
    repair = calls[1]["json"]["input"][2]["content"][0]["text"]
    assert "[redacted]" in repair
    _assert_no_adversarial_markers(repair)


@pytest.mark.parametrize("token", COMMON_TOKEN_MARKERS)
def test_openai_repair_prompt_never_carries_common_token_shapes(token):
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "id": "resp_bad",
                "model": DEFAULT_OPENAI_MODEL,
                "output_text": f"invalid prior reply {token}",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
        return _response(response_id="resp_fixed")

    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )

    assert result.state is CollaboratorState.RETURNED
    repair = calls[1]["json"]["input"][2]["content"][0]["text"]
    assert "[redacted]" in repair
    assert token not in repair


def test_openai_two_invalid_outputs_fail_without_a_third_call():
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return {
            "id": f"resp_{len(calls)}",
            "model": DEFAULT_OPENAI_MODEL,
            "output_text": "not-json",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }

    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 2
    assert result.output is None
    assert "after one repair" in result.public_failure
    assert len(calls) == 2


def test_openai_transport_error_echoing_key_is_safely_public():
    def fake_post(_url, **_kwargs):
        raise RuntimeError(
            f"Traceback Authorization: Bearer {KEY}; api_key={KEY}; "
            "password=plain-password-marker; "
            "client_secret=plain-client-marker; " + _adversarial_leak_text()
        )

    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )
    serialized = result.model_dump_json()

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 1
    assert KEY not in serialized
    assert "sk-" not in serialized.casefold()
    assert "bearer" not in serialized.casefold()
    assert "api_key" not in serialized.casefold()
    assert "traceback" not in serialized.casefold()
    assert "operator@example.test" not in serialized
    assert "plain-password-marker" not in serialized
    assert "plain-client-marker" not in serialized
    _assert_no_adversarial_markers(serialized)


def test_openai_refusal_fails_without_schema_repair():
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return {
            "id": "resp_refused",
            "model": DEFAULT_OPENAI_MODEL,
            "output": [{
                "type": "message",
                "content": [{"type": "refusal", "refusal": "Cannot comply"}],
            }],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }

    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 1
    assert "refused" in result.public_failure.casefold()
    assert len(calls) == 1


def test_pinned_openai_model_rejects_silent_provider_substitution():
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return {
            **_response(),
            "model": "gpt-5-2099-01-01",
        }

    result = OpenAIResponsesAdapter(
        model="gpt-5-2025-08-07",
        post_json=fake_post,
    ).call(_request(), api_key=KEY)

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 1
    assert result.output is None
    assert "pinned snapshot" in result.public_failure
    assert len(calls) == 1


@pytest.mark.parametrize("reported_model", [None, 123, [], {}])
def test_pinned_openai_model_requires_string_response_provenance(reported_model):
    response = _response()
    if reported_model is None:
        response.pop("model")
    else:
        response["model"] = reported_model

    result = OpenAIResponsesAdapter(
        model=DEFAULT_OPENAI_MODEL,
        post_json=lambda _url, **_kwargs: response,
    ).call(_request(), api_key=KEY)

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 1
    assert result.output is None
    assert "pinned snapshot" in result.public_failure


@pytest.mark.parametrize("response_id", (
    KEY,
    AUTH_MARKERS["AWS_ACCESS_KEY_ID"],
    "AWS_SESSION_TOKEN",
))
def test_credential_shaped_response_id_is_discarded(response_id):
    result = OpenAIResponsesAdapter(
        post_json=lambda _url, **_kwargs: _response(response_id=response_id),
    ).call(_request(), api_key=KEY)

    assert result.state is CollaboratorState.RETURNED
    assert result.response_id is None
    assert response_id not in result.model_dump_json()


def test_missing_openai_key_is_explicit_not_run_and_makes_no_call():
    def forbidden_call(*_args, **_kwargs):
        raise AssertionError("network adapter must remain dormant")

    result = OpenAIResponsesAdapter(post_json=forbidden_call).call(
        _request(),
        api_key="",
    )

    assert result.state is CollaboratorState.NOT_RUN
    assert result.attempts == 0
    assert result.output is None


@pytest.mark.parametrize("bad_key", ("not-a-key", "sk-short", "sk-" + "x" * 600))
def test_malformed_openai_key_is_not_run_and_makes_no_call(bad_key):
    def forbidden_call(*_args, **_kwargs):
        raise AssertionError("malformed credential must not reach transport")

    result = OpenAIResponsesAdapter(post_json=forbidden_call).call(
        _request(),
        api_key=bad_key,
    )

    assert result.state is CollaboratorState.NOT_RUN
    assert result.attempts == 0
    assert bad_key not in result.model_dump_json()


def test_claude_and_openai_receive_the_same_canonical_request_bytes():
    openai_calls = []
    claude_calls = []

    def fake_post(_url, **kwargs):
        openai_calls.append(kwargs)
        return _response()

    def fake_claude(prompt, **kwargs):
        claude_calls.append((prompt, kwargs))
        return _output().model_dump_json()

    request = _request()
    openai = OpenAIResponsesAdapter(post_json=fake_post).call(
        request,
        api_key=KEY,
    )
    claude = ClaudeMaxAdapter(run_claude=fake_claude).call(request)
    canonical = canonical_request_bytes(request).decode("utf-8")

    assert openai.state is CollaboratorState.RETURNED
    assert claude.state is CollaboratorState.RETURNED
    assert openai.input_sha256 == claude.input_sha256 == request.request_sha256
    assert openai.prompt_sha256 == claude.prompt_sha256
    assert openai.schema_sha256 == claude.schema_sha256
    assert openai_calls[0]["json"]["input"][1]["content"][0]["text"] == canonical
    assert claude_calls[0][0].startswith(canonical + "\n\n")
    assert claude_calls[0][1]["isolated"] is True


def test_claude_validation_retry_is_bounded_to_one_repair():
    replies = iter(("not-json", _output("secure annotation").model_dump_json()))
    prompts = []

    def fake_claude(prompt, **_kwargs):
        prompts.append(prompt)
        return next(replies)

    result = ClaudeMaxAdapter(run_claude=fake_claude).call(_request())

    assert result.state is CollaboratorState.RETURNED
    assert result.attempts == 2
    assert result.output == _output("secure annotation")
    assert len(prompts) == 2
    assert prompts[0].startswith(_request().canonical_json())
    assert prompts[1].startswith(_request().canonical_json())


def test_claude_repair_prompt_redacts_prior_reply_credentials_and_contacts():
    replies = iter((
        _adversarial_leak_text(),
        _output("secure annotation").model_dump_json(),
    ))
    prompts = []

    def fake_claude(prompt, **_kwargs):
        prompts.append(prompt)
        return next(replies)

    result = ClaudeMaxAdapter(run_claude=fake_claude).call(_request())

    assert result.state is CollaboratorState.RETURNED
    assert result.attempts == 2
    assert "[redacted]" in prompts[1]
    _assert_no_adversarial_markers(prompts[1])


def test_claude_error_redacts_arbitrary_credentials():
    def fake_claude(*_args, **_kwargs):
        raise RuntimeError(
            f"password=plain-password-marker client_secret=plain-client-marker {KEY} "
            + _adversarial_leak_text()
        )

    result = ClaudeMaxAdapter(run_claude=fake_claude).call(_request())
    serialized = result.model_dump_json()

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 1
    assert "password" not in serialized.casefold()
    assert "client_secret" not in serialized.casefold()
    assert "plain-password-marker" not in serialized
    assert "plain-client-marker" not in serialized
    assert KEY not in serialized
    _assert_no_adversarial_markers(serialized)


def test_redaction_covers_json_natural_language_and_unassigned_contact_shapes():
    raw = (
        '{"CLAUDE_CODE_OAUTH_TOKEN": "oauth value with spaces", '
        '"AWS_SECRET_ACCESS_KEY": "aws value with spaces"}; '
        "api key is natural-api-marker; "
        f"email {CONTACT_EMAIL}; phone +1 (202) 555-0199"
    )

    redacted = redact_provider_text(raw)

    assert "[redacted]" in redacted
    for marker in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "oauth value with spaces",
        "aws value with spaces",
        "natural-api-marker",
        CONTACT_EMAIL,
        "(202) 555-0199",
    ):
        assert marker.casefold() not in redacted.casefold()


@pytest.mark.parametrize("raw", [
    "password is correct horse battery staple",
    "secret is alpha beta gamma delta",
    "password is " + "x" * 600,
    "password is correct horse\nbattery staple",
    "password is correct.horse.battery.staple",
    "api key is alpha!beta?gamma",
])
def test_natural_language_credential_redaction_removes_the_full_clause(raw):
    redacted = redact_provider_text(raw)

    assert redacted == "[redacted]"
    for word in raw.split()[2:]:
        assert word not in redacted


@pytest.mark.parametrize("raw", [
    "ｐａｓｓｗｏｒｄ is fullwidth secret",
    "sk－secret-value-123456",
    "operator＠example.com",
])
def test_provider_redactor_normalizes_unicode_before_sensitive_scan(raw):
    redacted = redact_provider_text(raw)

    assert "[redacted]" in redacted
    assert "secret-value-123456" not in redacted
    assert "operator@example.com" not in redacted


@pytest.mark.parametrize("raw", [
    (
        "-----BEGIN PRIVATE KEY-----\n"
        "ZnVsbC1wcml2YXRlLWtleS1ib2R5\n"
        "-----END PRIVATE KEY-----"
    ),
    "-----BEGIN PRIVATE KEY-----\ndHJ1bmNhdGVkLXByaXZhdGUta2V5LWJvZHk=",
])
def test_provider_redactor_removes_full_and_truncated_private_keys(raw):
    redacted = redact_provider_text(raw)

    assert redacted == "[redacted]"
    assert "PRIVATE KEY" not in redacted
    assert "LXByaXZhdGUta2V5LWJvZHk" not in redacted


@pytest.mark.parametrize("bad_model", (
    "sk-secret-value-123456",
    "AKIA" + "A" * 16,
    "xoxb-" + "a" * 24,
    "operator@example.test",
    "2025550199",
    "password was secret-value",
    "model\N{ZERO WIDTH SPACE}latest",
))
def test_provider_models_fail_before_transport_without_echo(bad_model):
    openai_calls = []
    claude_calls = []

    with pytest.raises(ValueError) as openai_error:
        OpenAIResponsesAdapter(
            model=bad_model,
            post_json=lambda *_args, **_kwargs: openai_calls.append(True),
        )
    with pytest.raises(ValueError) as claude_error:
        ClaudeMaxAdapter(
            model=bad_model,
            run_claude=lambda *_args, **_kwargs: claude_calls.append(True),
        )

    assert bad_model not in str(openai_error.value)
    assert bad_model not in str(claude_error.value)
    assert openai_calls == []
    assert claude_calls == []


def test_adapters_reject_arbitrary_outer_and_nested_request_subclasses():
    request = _request()
    openai_calls = []
    claude_calls = []

    class EvilRequest(CollaborationRequest):
        @property
        def canonical_bytes(self):
            return b'{"private_notes":"sk-secret-value-123456"}'

    evil_outer = EvilRequest.model_construct(**request.__dict__)

    class EvilIntake(SanitizedIntake):
        def model_dump(self, *args, **kwargs):
            return {"private_notes": "sk-secret-value-123456"}

    evil_intake = EvilIntake(
        **BaseModel.model_dump(
            request.sanitized_intake,
            mode="python",
            exclude_none=False,
        )
    )
    evil_nested = CollaborationRequest.model_construct(
        **{**request.__dict__, "sanitized_intake": evil_intake}
    )

    openai = OpenAIResponsesAdapter(
        model="gpt-test",
        post_json=lambda *_args, **_kwargs: openai_calls.append(True),
    )
    claude = ClaudeMaxAdapter(
        model="claude-opus-4-6",
        run_claude=lambda *_args, **_kwargs: claude_calls.append(True),
    )
    for unsafe in (object(), {"partial": True}, evil_outer, evil_nested):
        with pytest.raises((TypeError, ValueError)):
            openai.call(unsafe, api_key=KEY)
        with pytest.raises((TypeError, ValueError)):
            claude.call(unsafe)

    assert openai_calls == []
    assert claude_calls == []


@pytest.mark.parametrize("usage", (
    None,
    {},
    {"input_tokens": -1, "output_tokens": 2},
    {"input_tokens": True, "output_tokens": 2},
    {"input_tokens": 3, "output_tokens": 2, "total_tokens": 99},
))
def test_openai_requires_reconciled_usage_without_schema_retry(usage):
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        response = _response()
        response["usage"] = usage
        return response

    result = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )

    assert result.state is CollaboratorState.FAILED
    assert result.attempts == 1
    assert result.token_usage is None
    assert len(calls) == 1


def test_provider_reply_byte_budget_fails_after_one_bounded_repair():
    openai_calls = []
    claude_calls = []
    oversized = "x" * (MAX_PROVIDER_REPLY_BYTES + 1)

    def fake_post(_url, **kwargs):
        openai_calls.append(kwargs)
        return {
            "id": f"resp_{len(openai_calls)}",
            "model": DEFAULT_OPENAI_MODEL,
            "output_text": oversized,
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }

    def fake_claude(*_args, **_kwargs):
        claude_calls.append(True)
        return oversized

    openai = OpenAIResponsesAdapter(post_json=fake_post).call(
        _request(),
        api_key=KEY,
    )
    claude = ClaudeMaxAdapter(run_claude=fake_claude).call(_request())

    assert openai.state is CollaboratorState.FAILED
    assert claude.state is CollaboratorState.FAILED
    assert openai.attempts == claude.attempts == 2
    assert len(openai_calls) == len(claude_calls) == 2


def test_provider_redactor_fails_closed_on_excessive_encoding_depth():
    encoded = "password is hunter2"
    for _ in range(17):
        encoded = quote(encoded, safe="")

    assert redact_provider_text(encoded) == "[redacted]"
