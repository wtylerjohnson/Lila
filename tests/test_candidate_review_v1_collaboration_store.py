"""Fail-closed persistence tests for Candidate Review collaboration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from pydantic import BaseModel

from agents.candidate_review_v1.collaboration import (
    CollaborationLayer,
    CollaborationMode,
    CollaborationRequest,
    CollaborationRun,
    CollaboratorProvider,
    CollaboratorState,
    InferenceChallengeOutput,
    IntakeChallengeOutput,
    IntakeSuggestion,
    ProviderCallResult,
    SanitizedEvidence,
    SanitizedIntake,
    SuggestionClassification,
    SuggestionKind,
    TokenUsage,
)
from agents.candidate_review_v1.collaboration_store import (
    RECEIPT_FILENAME,
    CollaborationCacheIdentity,
    CollaborationStore,
    CollaborationStoreError,
    CollaborationStoreReceipt,
    _canonical_bytes,
)
from agents.candidate_review_v1.collaboration_providers import (
    provider_contract_hashes,
)
from agents.candidate_review_v1.contracts import ArtifactBinding


NOW = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)
KEY = "sk-test-secret-that-must-never-persist"


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


def _intake_request() -> CollaborationRequest:
    return CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INTAKE,
        sanitized_intake=SanitizedIntake(
            client_name="Testco",
            primary_services="Secure data labeling",
        ),
    )


def _inference_request() -> CollaborationRequest:
    evidence = SanitizedEvidence(
        evidence_id="evidence-1",
        source_url="https://sam.gov/opp/evidence-1/view",
        source_kind="notice",
        title="Agency secure annotation market research",
        excerpt="The agency requests information about a secure annotation platform.",
        official_source=True,
        primary_source=True,
        record_sha256="d" * 64,
    )
    return CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INFERENCE,
        sanitized_intake=SanitizedIntake(
            client_name="Testco",
            primary_services="Secure data labeling",
        ),
        evidence_snapshot=(evidence,),
    )


def _intake_output(value: str) -> IntakeChallengeOutput:
    return IntakeChallengeOutput(suggestions=(IntakeSuggestion(
        kind=SuggestionKind.KEYWORD,
        classification=SuggestionClassification.CORE,
        value=value,
        rationale="The intake supports this search term.",
        source_refs=("intake-form",),
    ),))


def _result(request, provider, output, *, model):
    prompt_sha256, schema_sha256 = provider_contract_hashes(request)
    return ProviderCallResult(
        provider=provider,
        state=CollaboratorState.RETURNED,
        model=model,
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        input_sha256=request.request_sha256,
        output=output,
        attempts=1,
        token_usage=TokenUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        ),
        started_at=NOW,
        completed_at=NOW,
        response_id=f"response-{provider.value}",
    )


def _openai_result(request, *, state, value="annotation"):
    if state is CollaboratorState.RETURNED:
        return _result(
            request,
            CollaboratorProvider.OPENAI,
            _intake_output(value),
            model="gpt-test",
        )
    prompt_sha256, schema_sha256 = provider_contract_hashes(request)
    values = dict(
        provider=CollaboratorProvider.OPENAI,
        state=state,
        model="gpt-test",
        prompt_sha256=prompt_sha256,
        schema_sha256=schema_sha256,
        input_sha256=request.request_sha256,
        output=None,
        attempts=0 if state is CollaboratorState.NOT_RUN else 1,
    )
    if state is CollaboratorState.FAILED:
        values.update(
            public_failure="OpenAI provider unavailable.",
            started_at=NOW,
            completed_at=NOW,
        )
    return ProviderCallResult(**values)


def _intake_run(
    *,
    openai_value="annotation",
    mode=CollaborationMode.ADVISORY,
    openai_state=CollaboratorState.RETURNED,
) -> CollaborationRun:
    request = _intake_request()
    return CollaborationRun.create(
        request=request,
        mode=mode,
        claude=_result(
            request,
            CollaboratorProvider.CLAUDE,
            _intake_output("data labeling"),
            model="opus",
        ),
        openai=_openai_result(
            request,
            state=openai_state,
            value=openai_value,
        ),
    )


def _inference_run() -> CollaborationRun:
    request = _inference_request()
    output = InferenceChallengeOutput()
    return CollaborationRun.create(
        request=request,
        mode=CollaborationMode.ADVISORY,
        claude=_result(
            request,
            CollaboratorProvider.CLAUDE,
            output,
            model="opus",
        ),
        openai=_result(
            request,
            CollaboratorProvider.OPENAI,
            output,
            model="gpt-test",
        ),
    )


def test_persist_writes_separate_exact_artifacts_and_receipt_last(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()
    receipt = store.persist(run, committed_at=NOW)
    directory = tmp_path / receipt.relative_path

    assert isinstance(receipt, CollaborationStoreReceipt)
    assert receipt.mode == "advisory"
    assert receipt.disposition == "continue"
    assert receipt.execution_sha256 in receipt.relative_path
    assert tuple(row.file_name for row in receipt.files) == (
        "claude.json",
        "comparison.json",
        "openai.json",
    )
    assert (directory / RECEIPT_FILENAME).exists()
    for binding in receipt.files:
        raw = (directory / binding.file_name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == binding.sha256
        assert len(raw) == binding.byte_size
        assert KEY.encode() not in raw
    stored_receipt = CollaborationStoreReceipt.model_validate_json(
        (directory / RECEIPT_FILENAME).read_bytes()
    )
    assert stored_receipt == receipt


def test_same_run_persists_intake_and_inference_in_distinct_directories(tmp_path):
    store = CollaborationStore(tmp_path)

    receipts = (
        store.persist(_intake_run(), committed_at=NOW),
        store.persist(_inference_run(), committed_at=NOW),
    )

    for receipt in receipts:
        directory = tmp_path / receipt.relative_path
        assert (directory / "claude.json").exists()
        assert (directory / "openai.json").exists()
        assert (directory / "comparison.json").exists()
        assert (directory / RECEIPT_FILENAME).exists()


def test_persist_is_idempotent_and_changed_output_creates_new_execution(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()

    first = store.persist(run, committed_at=NOW)
    second = store.persist(run, committed_at=NOW)
    assert second == first

    changed = store.persist(
        _intake_run(openai_value="secure annotation"),
        committed_at=NOW,
    )
    assert changed.execution_sha256 != first.execution_sha256
    assert (tmp_path / first.relative_path / RECEIPT_FILENAME).exists()
    assert (tmp_path / changed.relative_path / RECEIPT_FILENAME).exists()


@pytest.mark.parametrize(("mode", "state"), (
    (CollaborationMode.OFF, CollaboratorState.NOT_RUN),
    (CollaborationMode.ADVISORY, CollaboratorState.NOT_RUN),
))
def test_not_run_then_success_preserves_both_executions(tmp_path, mode, state):
    store = CollaborationStore(tmp_path)
    before = store.persist(
        _intake_run(mode=mode, openai_state=state),
        committed_at=NOW,
    )
    after = store.persist(_intake_run(), committed_at=NOW)

    assert before.execution_sha256 != after.execution_sha256
    assert before.mode == mode.value
    assert after.mode == CollaborationMode.ADVISORY.value
    assert (tmp_path / before.relative_path / RECEIPT_FILENAME).exists()
    assert (tmp_path / after.relative_path / RECEIPT_FILENAME).exists()


def test_failed_then_success_preserves_both_executions(tmp_path):
    store = CollaborationStore(tmp_path)
    failed = store.persist(
        _intake_run(openai_state=CollaboratorState.FAILED),
        committed_at=NOW,
    )
    succeeded = store.persist(_intake_run(), committed_at=NOW)

    assert failed.execution_sha256 != succeeded.execution_sha256
    assert failed.disposition == "continue_degraded"
    assert succeeded.disposition == "continue"
    failed_openai = json.loads(
        (tmp_path / failed.relative_path / "openai.json").read_text()
    )
    success_openai = json.loads(
        (tmp_path / succeeded.relative_path / "openai.json").read_text()
    )
    assert failed_openai["state"] == "failed"
    assert success_openai["state"] == "returned"


def test_persist_replay_without_fixed_timestamp_returns_closed_receipt(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()

    first = store.persist(run)
    second = store.persist(run)

    assert second == first


def test_cache_reuses_only_validated_success_and_marks_hit(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )

    path = store.save_cache(identity, run.openai)
    cached = store.load_cache(identity)

    assert path.exists()
    assert cached.state is CollaboratorState.CACHED
    assert cached.attempts == 0
    assert cached.output == run.openai.output
    assert cached.token_usage == run.openai.token_usage
    assert cached.response_id == run.openai.response_id
    assert ProviderCallResult.model_validate(cached) == cached
    assert KEY not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("field", "value"), (
    ("input_sha256", "3" * 64),
    ("evidence_sha256", "4" * 64),
    ("request_sha256", "5" * 64),
    ("provider", "claude"),
    ("model", "gpt-other"),
    ("prompt_version", "prompt-v2"),
    ("schema_version", "schema-v2"),
))
def test_changed_cache_dimension_is_a_miss(tmp_path, field, value):
    store = CollaborationStore(tmp_path)
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )
    store.save_cache(identity, run.openai)

    changed = identity.model_copy(update={field: value})
    assert changed.cache_key != identity.cache_key
    assert store.load_cache(changed) is None


def test_corrupt_cache_is_a_miss(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )
    path = store.save_cache(identity, run.openai)
    path.write_text("{broken", encoding="utf-8")

    assert store.load_cache(identity) is None


def test_failed_or_unvalidated_result_is_not_cached(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )
    failed = ProviderCallResult(
        provider=CollaboratorProvider.OPENAI,
        state=CollaboratorState.FAILED,
        model="gpt-test",
        prompt_sha256="1" * 64,
        schema_sha256="2" * 64,
        input_sha256=run.request.request_sha256,
        attempts=1,
        public_failure="Provider unavailable",
        started_at=NOW,
        completed_at=NOW,
    )

    with pytest.raises(CollaborationStoreError, match="successful"):
        store.save_cache(identity, failed)


def test_secret_shaped_payload_and_destination_override_are_rejected(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()

    class UnsafeRun(BaseModel):
        request: object
        claude: object
        openai: object
        comparison: object

    unsafe = UnsafeRun(
        request=run.request,
        claude={"api_key": KEY},
        openai=run.openai,
        comparison=run.comparison,
    )

    with pytest.raises(CollaborationStoreError, match="exact run contract"):
        store.persist(unsafe, committed_at=NOW)
    with pytest.raises(CollaborationStoreError, match="does not match"):
        store.persist(run, client_id="other-client", committed_at=NOW)
    assert not any(KEY in path.read_text(encoding="utf-8", errors="ignore")
                   for path in tmp_path.rglob("*.json"))


@pytest.mark.parametrize("unsafe_payload", (
    {"ANTHROPIC_AUTH_TOKEN": "anthropic-auth-marker"},
    {"CLAUDE_CODE_OAUTH_TOKEN": "claude-oauth-marker"},
    {"AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE"},
    {"TENANT_CREDENTIAL": "tenant-credential-marker"},
    {"payload": "AWS_SESSION_TOKEN=aws-session-marker"},
    {"payload": "AKIAIOSFODNN7EXAMPLE"},
    {"payload": "ghp_abcdefghijklmnopqrstuvwxyz123456"},
    {
        "payload": (
            "-----BEGIN PRIVATE KEY-----\n"
            "FAKE-PEM-MARKER\n"
            "-----END PRIVATE KEY-----"
        )
    },
    {"payload": "operator@example.test"},
    {"payload": "Call 202-555-0199"},
    {"contact_name": "Jane Operator"},
))
def test_store_rejects_adversarial_credentials_and_contact_pii_before_disk(
    tmp_path,
    unsafe_payload,
):
    with pytest.raises(CollaborationStoreError, match="credential-shaped"):
        _canonical_bytes(unsafe_payload)
    assert not tuple(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("unsafe_payload", (
    {"payload": "ｐａｓｓｗｏｒｄ is fullwidth secret"},
    {"payload": "sk－secret-value-123456"},
    {"payload": "operator＠example.com"},
    {"payload": "xapp-" + "a" * 24},
    {"payload": "npm_" + "a" * 32},
    {"payload": "sk_live_" + "a" * 24},
    {"payload": "eyJhbGciOiJI.eyJzdWIiOiIxMjM0.NiIsInRlc3Qi"},
    {"ａｐｉ＿ｋｅｙ": "opaque-value"},
))
def test_store_scanner_normalizes_unicode_and_rejects_common_tokens(
    unsafe_payload,
):
    with pytest.raises(CollaborationStoreError, match="credential-shaped"):
        _canonical_bytes(unsafe_payload)


def test_store_secret_scan_allows_analytical_keys_and_security_vocabulary():
    payload = {
        "agency_key": "Department of State",
        "buyer_key": "Enterprise security office",
        "program_key": "Zero trust modernization",
        "access_route_key": "OASIS Plus",
        "item_key": "hypothesis:zero-trust",
        "analysis": (
            "Authentication, tokenization, API key management, and the Secret "
            "Service mission are legitimate analytical vocabulary."
        ),
    }

    encoded = _canonical_bytes(payload)

    assert json.loads(encoded) == payload


def test_cache_envelope_contains_no_unbound_raw_request_or_key(tmp_path):
    store = CollaborationStore(tmp_path)
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )
    path = store.save_cache(identity, run.openai)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["identity"]["request_sha256"] == run.request.request_sha256
    assert "sanitized_intake" not in payload
    assert "evidence_snapshot" not in payload
    assert KEY not in json.dumps(payload)


def test_cache_rejects_provider_result_subclass_before_any_write(tmp_path):
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )

    class EvilResult(ProviderCallResult):
        def model_dump(self, *args, **kwargs):
            return {"private_notes": KEY}

    evil = EvilResult.model_construct(**run.openai.__dict__)
    store = CollaborationStore(tmp_path)

    with pytest.raises(CollaborationStoreError, match="exact provider result"):
        store.save_cache(identity, evil)
    assert not tuple(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("payload", (
    {"payload": "password-is-hunter2"},
    {"payload": "OPENAI_API_KEY hunter2value"},
    {"payload": "名前@例え.テスト"},
    {"payload": "+44 20 7946 0958"},
    {"payload": "pass\N{COMBINING ACUTE ACCENT}word"},
    {"payload": "password%2520is%2520hunter"},
))
def test_store_uses_shared_privacy_classifier_for_new_bypass_shapes(payload):
    with pytest.raises(CollaborationStoreError, match="credential-shaped"):
        _canonical_bytes(payload)


def test_store_allows_digest_only_in_an_explicit_sha256_field():
    digest = "1234567890abcdef" * 4

    assert json.loads(_canonical_bytes({"execution_sha256": digest})) \
        == {"execution_sha256": digest}
    with pytest.raises(CollaborationStoreError, match="credential-shaped"):
        _canonical_bytes({"analysis": digest})


def test_cache_load_rejects_identity_subclass_without_reading_disk(
    tmp_path,
    monkeypatch,
):
    run = _intake_run()
    identity = CollaborationCacheIdentity.from_request(
        run.request,
        provider=CollaboratorProvider.OPENAI,
        model=run.openai.model,
    )
    store = CollaborationStore(tmp_path)
    store.save_cache(identity, run.openai)

    class EvilIdentity(CollaborationCacheIdentity):
        @property
        def cache_key(self):
            return identity.cache_key

    evil = EvilIdentity.model_construct(**identity.__dict__)

    def forbidden_read(_path):
        raise AssertionError("identity subclass reached an unrelated cache file")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    assert store.load_cache(evil) is None


def test_store_scanner_fails_closed_on_excessive_encoding_depth():
    encoded = "password is hunter2"
    for _ in range(17):
        encoded = quote(encoded, safe="")

    with pytest.raises(CollaborationStoreError, match="credential-shaped"):
        _canonical_bytes({"payload": encoded})
