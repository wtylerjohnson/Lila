"""Offline execution tests for the Candidate Review collaboration runner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.candidate_review_v1.collaboration import (
    CollaborationLayer,
    CollaborationMode,
    CollaborationRequest,
    CollaboratorProvider,
    CollaboratorState,
    InferenceChallengeOutput,
    IntakeChallengeOutput,
    IntakeSuggestion,
    ProviderCallResult,
    RunDisposition,
    SanitizedEvidence,
    SanitizedIntake,
    SuggestionClassification,
    SuggestionKind,
)
from agents.candidate_review_v1.collaboration_runner import (
    CollaborationExecutionReceipt,
    CollaborationExecutionResult,
    CollaborationRunner,
    ProviderExecutionSource,
)
from agents.candidate_review_v1 import collaboration_runner as runner_module
from agents.candidate_review_v1.collaboration_store import (
    RECEIPT_FILENAME,
    CollaborationCacheIdentity,
    CollaborationStore,
)
from agents.candidate_review_v1.collaboration_providers import (
    provider_contract_hashes,
)
from agents.candidate_review_v1.contracts import ArtifactBinding


NOW = datetime(2026, 7, 22, 21, 0, tzinfo=timezone.utc)
KEY = "sk-session-only-key-1234567890"


def _binding(*, run_id: str = "run-1") -> ArtifactBinding:
    return ArtifactBinding(
        client_id="testco",
        client_name="Testco",
        run_id=run_id,
        scope_designator="government-wide",
        scope_sha256="a" * 64,
        profile_sha256="b" * 64,
        evidence_snapshot_sha256="c" * 64,
    )


def _request(
    *,
    run_id: str = "run-1",
    prompt_version: str = "candidate-review-collaboration-v1",
    layer: CollaborationLayer = CollaborationLayer.INTAKE,
) -> CollaborationRequest:
    evidence = ()
    if layer is CollaborationLayer.INFERENCE:
        evidence = (SanitizedEvidence(
            evidence_id="evidence-1",
            source_url="https://agency.example.org/award/evidence-1",
            source_kind="award",
            title="Secure data services award",
            excerpt="The agency bought secure data services.",
            record_sha256="d" * 64,
        ),)
    return CollaborationRequest.create(
        binding=_binding(run_id=run_id),
        layer=layer,
        sanitized_intake=SanitizedIntake(
            client_name="Testco",
            primary_services="Secure data labeling",
            known_naics=("541511",),
        ),
        evidence_snapshot=evidence,
        prompt_version=prompt_version,
    )


def _output(value: str) -> IntakeChallengeOutput:
    return IntakeChallengeOutput(suggestions=(IntakeSuggestion(
        kind=SuggestionKind.KEYWORD,
        classification=SuggestionClassification.CORE,
        value=value,
        rationale="The sanitized intake supports this search concept.",
        source_refs=("intake-form",),
    ),))


def _result(
    request: CollaborationRequest,
    *,
    provider: CollaboratorProvider,
    model: str,
    state: CollaboratorState = CollaboratorState.RETURNED,
) -> ProviderCallResult:
    prompt_sha256, schema_sha256 = provider_contract_hashes(request)
    if state is CollaboratorState.RETURNED:
        output = (
            _output(f"{provider.value} concept")
            if request.layer is CollaborationLayer.INTAKE
            else InferenceChallengeOutput()
        )
        return ProviderCallResult(
            provider=provider,
            state=state,
            model=model,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
            input_sha256=request.request_sha256,
            output=output,
            attempts=1,
            started_at=NOW,
            completed_at=NOW,
            response_id=f"response-{provider.value}",
        )
    if state is CollaboratorState.FAILED:
        return ProviderCallResult(
            provider=provider,
            state=state,
            model=model,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
            input_sha256=request.request_sha256,
            output=None,
            attempts=1,
            public_failure=f"{provider.value.title()} provider unavailable.",
            started_at=NOW,
            completed_at=NOW,
        )
    raise AssertionError("fixture supports returned and failed results")


class FakeClaude:
    def __init__(
        self,
        *,
        model: str = "claude-opus-4-6",
        state: CollaboratorState = CollaboratorState.RETURNED,
        raises: bool = False,
    ) -> None:
        self.model = model
        self.state = state
        self.raises = raises
        self.calls: list[CollaborationRequest] = []

    def call(self, request: CollaborationRequest) -> ProviderCallResult:
        self.calls.append(request)
        if self.raises:
            raise RuntimeError(f"transport failed with {KEY}")
        return _result(
            request,
            provider=CollaboratorProvider.CLAUDE,
            model=self.model,
            state=self.state,
        )


class FakeOpenAI:
    def __init__(
        self,
        *,
        model: str = "gpt-5-2025-08-07",
        state: CollaboratorState = CollaboratorState.RETURNED,
        raises: bool = False,
    ) -> None:
        self.model = model
        self.state = state
        self.raises = raises
        self.calls: list[CollaborationRequest] = []
        self.received_key = False

    def call(
        self,
        request: CollaborationRequest,
        *,
        api_key: str,
    ) -> ProviderCallResult:
        self.calls.append(request)
        self.received_key = bool(api_key)
        if self.raises:
            raise RuntimeError(f"transport failed with {api_key}")
        return _result(
            request,
            provider=CollaboratorProvider.OPENAI,
            model=self.model,
            state=self.state,
        )


class RecordingStore(CollaborationStore):
    def __init__(self, state_root: Path) -> None:
        super().__init__(state_root)
        self.saved_providers: list[CollaboratorProvider] = []
        self.loaded_identities: list[CollaborationCacheIdentity] = []

    def load_cache(self, identity):
        self.loaded_identities.append(identity)
        return super().load_cache(identity)

    def save_cache(self, identity, result):
        self.saved_providers.append(result.provider)
        return super().save_cache(identity, result)


def _runner(tmp_path, *, claude=None, openai=None, store=None):
    claude = claude or FakeClaude()
    openai = openai or FakeOpenAI()
    store = store or RecordingStore(tmp_path)
    return CollaborationRunner(
        claude_adapter=claude,
        openai_adapter=openai,
        store=store,
    ), claude, openai, store


def test_advisory_executes_same_immutable_request_and_persists_closed_artifacts(
    tmp_path,
):
    runner, claude, openai, _store = _runner(tmp_path)
    request = _request()
    canonical_before = request.canonical_bytes

    result = runner.execute(
        request,
        mode=CollaborationMode.ADVISORY,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert isinstance(result, CollaborationExecutionResult)
    assert isinstance(result.receipt, CollaborationExecutionReceipt)
    assert claude.calls == [request]
    assert openai.calls == [request]
    assert claude.calls[0] is not openai.calls[0]
    assert claude.calls[0] is not request
    assert openai.calls[0] is not request
    assert claude.calls[0] == openai.calls[0] == request
    assert request.canonical_bytes == canonical_before
    assert result.run.disposition is RunDisposition.CONTINUE
    assert result.run.analyst_packet_mutated is False
    assert result.receipt.analyst_packet_mutated is False
    assert result.receipt.claude_source is ProviderExecutionSource.PROVIDER
    assert result.receipt.openai_source is ProviderExecutionSource.PROVIDER

    directory = tmp_path / result.receipt.store.relative_path
    expected = {"claude.json", "openai.json", "comparison.json", RECEIPT_FILENAME}
    assert {path.name for path in directory.iterdir()} == expected
    assert tuple(row.file_name for row in result.receipt.store.files) == (
        "claude.json",
        "comparison.json",
        "openai.json",
    )


@pytest.mark.parametrize(("target", "replacement", "layer"), (
    ("run_id", "other-run", CollaborationLayer.INTAKE),
    ("scope_sha256", "f" * 64, CollaborationLayer.INTAKE),
    ("evidence_snapshot_sha256", "e" * 64, CollaborationLayer.INTAKE),
    ("record_sha256", "f" * 64, CollaborationLayer.INFERENCE),
))
def test_lane_mutation_cannot_change_peer_or_persistence_provenance(
    tmp_path,
    target,
    replacement,
    layer,
):
    request = _request(layer=layer)

    class MutatingClaude(FakeClaude):
        def call(self, received):
            self.calls.append(received)
            if target == "record_sha256":
                object.__setattr__(
                    received.evidence_snapshot[0],
                    target,
                    replacement,
                )
            else:
                object.__setattr__(received.binding, target, replacement)
            return _result(
                received,
                provider=CollaboratorProvider.CLAUDE,
                model=self.model,
            )

    runner, claude, openai, _store = _runner(
        tmp_path,
        claude=MutatingClaude(),
    )
    result = runner.execute(
        request,
        mode=CollaborationMode.ADVISORY,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert result.run.claude.state is CollaboratorState.FAILED
    assert result.run.openai.state is CollaboratorState.RETURNED
    assert result.run.disposition is RunDisposition.PAUSED
    assert len(claude.calls) == len(openai.calls) == 1
    assert openai.calls[0] == request
    assert result.run.request == request
    assert result.receipt.store.relative_path.startswith("testco/run-1/")
    assert not (tmp_path / "testco" / "other-run").exists()
    if target == "record_sha256":
        assert openai.calls[0].evidence_snapshot[0].record_sha256 == "d" * 64
        assert result.run.request.evidence_snapshot[0].record_sha256 == "d" * 64
    else:
        assert getattr(openai.calls[0].binding, target) \
            == getattr(request.binding, target)
        assert getattr(result.run.request.binding, target) \
            == getattr(request.binding, target)


def test_off_never_reads_openai_cache_or_hits_transport_even_with_key(tmp_path):
    runner, claude, openai, store = _runner(tmp_path)
    result = runner.execute(
        _request(),
        mode=CollaborationMode.OFF,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert len(claude.calls) == 1
    assert openai.calls == []
    assert all(identity.provider != CollaboratorProvider.OPENAI.value
               for identity in store.loaded_identities)
    assert result.run.openai.state is CollaboratorState.NOT_RUN
    assert result.receipt.openai_source is ProviderExecutionSource.MODE_OFF
    assert result.run.disposition is RunDisposition.OFF_CONTINUE


@pytest.mark.parametrize(("mode", "expected"), [
    (CollaborationMode.ADVISORY, RunDisposition.CONTINUE_DEGRADED),
    (CollaborationMode.REQUIRED, RunDisposition.PAUSED),
])
def test_missing_key_is_explicit_not_run_without_openai_call(
    tmp_path,
    mode,
    expected,
):
    runner, _claude, openai, _store = _runner(tmp_path)
    result = runner.execute(
        _request(),
        mode=mode,
        committed_at=NOW,
    )

    assert openai.calls == []
    assert result.run.openai.state is CollaboratorState.NOT_RUN
    assert result.receipt.openai_source is ProviderExecutionSource.MISSING_KEY
    assert result.run.disposition is expected


@pytest.mark.parametrize(("mode", "expected"), [
    (CollaborationMode.ADVISORY, RunDisposition.CONTINUE_DEGRADED),
    (CollaborationMode.REQUIRED, RunDisposition.PAUSED),
])
def test_openai_outage_degrades_advisory_and_pauses_required(
    tmp_path,
    mode,
    expected,
):
    openai = FakeOpenAI(raises=True)
    runner, _claude, openai, store = _runner(tmp_path, openai=openai)
    result = runner.execute(
        _request(),
        mode=mode,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert len(openai.calls) == 1
    assert result.run.openai.state is CollaboratorState.FAILED
    assert result.run.openai.public_failure == "OpenAI provider adapter failed."
    assert result.run.disposition is expected
    assert store.saved_providers == [CollaboratorProvider.CLAUDE]


@pytest.mark.parametrize("mode", tuple(CollaborationMode))
def test_claude_outage_always_pauses_but_other_enabled_lane_remains_independent(
    tmp_path,
    mode,
):
    claude = FakeClaude(raises=True)
    runner, claude, openai, store = _runner(tmp_path, claude=claude)
    result = runner.execute(
        _request(),
        mode=mode,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert len(claude.calls) == 1
    assert result.run.claude.state is CollaboratorState.FAILED
    assert result.run.disposition is RunDisposition.PAUSED
    assert len(openai.calls) == (0 if mode is CollaborationMode.OFF else 1)
    expected_saved = [] if mode is CollaborationMode.OFF else [
        CollaboratorProvider.OPENAI]
    assert store.saved_providers == expected_saved


def test_successful_provider_cache_is_used_before_either_call(tmp_path):
    request = _request()
    claude = FakeClaude()
    openai = FakeOpenAI()
    store = RecordingStore(tmp_path)
    for provider, adapter in (
        (CollaboratorProvider.CLAUDE, claude),
        (CollaboratorProvider.OPENAI, openai),
    ):
        identity = CollaborationCacheIdentity.from_request(
            request,
            provider=provider,
            model=adapter.model,
        )
        CollaborationStore.save_cache(
            store,
            identity,
            _result(request, provider=provider, model=adapter.model),
        )
    runner, _, _, _ = _runner(
        tmp_path,
        claude=claude,
        openai=openai,
        store=store,
    )

    result = runner.execute(
        request,
        mode=CollaborationMode.REQUIRED,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert claude.calls == []
    assert openai.calls == []
    assert result.run.claude.state is CollaboratorState.CACHED
    assert result.run.openai.state is CollaboratorState.CACHED
    assert result.receipt.claude_source is ProviderExecutionSource.CACHE
    assert result.receipt.openai_source is ProviderExecutionSource.CACHE
    assert store.saved_providers == []


def test_enabled_mode_reuses_exact_openai_cache_without_session_key(tmp_path):
    request = _request()
    claude = FakeClaude()
    openai = FakeOpenAI()
    store = RecordingStore(tmp_path)
    identity = CollaborationCacheIdentity.from_request(
        request,
        provider=CollaboratorProvider.OPENAI,
        model=openai.model,
    )
    CollaborationStore.save_cache(
        store,
        identity,
        _result(
            request,
            provider=CollaboratorProvider.OPENAI,
            model=openai.model,
        ),
    )
    runner, _, _, _ = _runner(
        tmp_path,
        claude=claude,
        openai=openai,
        store=store,
    )

    result = runner.execute(
        request,
        mode=CollaborationMode.REQUIRED,
        committed_at=NOW,
    )

    assert openai.calls == []
    assert result.run.openai.state is CollaboratorState.CACHED
    assert result.receipt.openai_source is ProviderExecutionSource.CACHE
    assert result.run.disposition is RunDisposition.CONTINUE


@pytest.mark.parametrize("floating_model", (
    "opus",
    "claude-opus",
    "claude-opus-latest",
    "claude-opus-latest-4-6",
    "claude-opus-current-4-6",
))
def test_floating_claude_alias_is_never_cached_or_reused(
    tmp_path,
    floating_model,
):
    claude = FakeClaude(model=floating_model)
    runner, claude, openai, store = _runner(tmp_path, claude=claude)
    request = _request()

    first = runner.execute(
        request,
        mode=CollaborationMode.OFF,
        committed_at=NOW,
    )
    second = runner.execute(
        request,
        mode=CollaborationMode.OFF,
        committed_at=NOW,
    )

    assert len(claude.calls) == 2
    assert openai.calls == []
    assert first.run.claude.state is CollaboratorState.RETURNED
    assert second.run.claude.state is CollaboratorState.RETURNED
    assert CollaboratorProvider.CLAUDE not in store.saved_providers
    assert all(identity.provider != CollaboratorProvider.CLAUDE.value
               for identity in store.loaded_identities)


@pytest.mark.parametrize("floating_model", (
    "gpt-5",
    "gpt-5-latest-2025-08-07",
    "gpt-5-current-2025-08-07",
))
def test_floating_openai_alias_is_never_cached_or_reused(
    tmp_path,
    floating_model,
):
    openai = FakeOpenAI(model=floating_model)
    runner, claude, openai, store = _runner(tmp_path, openai=openai)
    request = _request()

    first = runner.execute(
        request,
        mode=CollaborationMode.ADVISORY,
        openai_api_key=KEY,
        committed_at=NOW,
    )
    second = runner.execute(
        request,
        mode=CollaborationMode.ADVISORY,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert len(openai.calls) == 2
    assert len(claude.calls) == 1
    assert first.run.openai.state is CollaboratorState.RETURNED
    assert second.run.openai.state is CollaboratorState.RETURNED
    assert CollaboratorProvider.OPENAI not in store.saved_providers
    assert all(identity.provider != CollaboratorProvider.OPENAI.value
               for identity in store.loaded_identities)


def test_changed_request_identity_misses_stale_cache_and_calls_provider(tmp_path):
    old_request = _request(prompt_version="candidate-review-collaboration-v1")
    new_request = _request(prompt_version="candidate-review-collaboration-v2")
    claude = FakeClaude()
    openai = FakeOpenAI()
    store = RecordingStore(tmp_path)
    old_identity = CollaborationCacheIdentity.from_request(
        old_request,
        provider=CollaboratorProvider.CLAUDE,
        model=claude.model,
    )
    CollaborationStore.save_cache(
        store,
        old_identity,
        _result(
            old_request,
            provider=CollaboratorProvider.CLAUDE,
            model=claude.model,
        ),
    )
    runner, _, _, _ = _runner(
        tmp_path,
        claude=claude,
        openai=openai,
        store=store,
    )

    result = runner.execute(
        new_request,
        mode=CollaborationMode.OFF,
        committed_at=NOW,
    )

    assert claude.calls == [new_request]
    assert result.run.claude.state is CollaboratorState.RETURNED
    assert result.receipt.claude_source is ProviderExecutionSource.PROVIDER
    assert store.loaded_identities[0].cache_key != old_identity.cache_key


def test_only_new_returned_results_are_cached(tmp_path):
    openai = FakeOpenAI(state=CollaboratorState.FAILED)
    runner, _claude, _openai, store = _runner(tmp_path, openai=openai)

    result = runner.execute(
        _request(),
        mode=CollaborationMode.ADVISORY,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert result.run.openai.state is CollaboratorState.FAILED
    assert store.saved_providers == [CollaboratorProvider.CLAUDE]


@pytest.mark.parametrize("bad_model", (
    "sk-secret-value-123456",
    "AKIA" + "A" * 16,
    "operator@example.test",
    "2025550199",
    "password was secret-value",
    "model\N{ZERO WIDTH SPACE}latest",
))
@pytest.mark.parametrize("lane", ("claude", "openai"))
def test_runner_rejects_malicious_adapter_model_before_cache_or_calls(
    tmp_path,
    bad_model,
    lane,
):
    claude = FakeClaude(model=bad_model if lane == "claude" else "claude-opus-4-6")
    openai = FakeOpenAI(model=bad_model if lane == "openai" else "gpt-5-2025-08-07")
    store = RecordingStore(tmp_path)

    with pytest.raises(ValueError) as exc_info:
        CollaborationRunner(
            claude_adapter=claude,
            openai_adapter=openai,
            store=store,
        )

    assert bad_model not in str(exc_info.value)
    assert claude.calls == openai.calls == []
    assert store.loaded_identities == []
    assert store.saved_providers == []


def test_runner_rejects_provider_result_subclass_before_cache_write(tmp_path):
    request = _request()
    safe = _result(
        request,
        provider=CollaboratorProvider.CLAUDE,
        model="claude-opus-4-6",
    )

    class EvilResult(ProviderCallResult):
        def model_dump(self, *args, **kwargs):
            return {"private_notes": "sk-secret-value-123456"}

    evil = EvilResult.model_construct(**safe.__dict__)

    class EvilClaude(FakeClaude):
        def call(self, received):
            self.calls.append(received)
            return evil

    runner, claude, openai, store = _runner(
        tmp_path,
        claude=EvilClaude(),
    )
    result = runner.execute(
        request,
        mode=CollaborationMode.OFF,
        committed_at=NOW,
    )

    assert result.run.claude.state is CollaboratorState.FAILED
    assert len(claude.calls) == 1
    assert openai.calls == []
    assert store.saved_providers == []
    assert "sk-secret-value-123456" not in "".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*.json")
    )


def test_changed_contract_hash_misses_stale_cache_without_version_change(
    tmp_path,
    monkeypatch,
):
    request = _request()
    claude = FakeClaude()
    store = RecordingStore(tmp_path)
    identity = CollaborationCacheIdentity.from_request(
        request,
        provider=CollaboratorProvider.CLAUDE,
        model=claude.model,
    )
    CollaborationStore.save_cache(
        store,
        identity,
        _result(
            request,
            provider=CollaboratorProvider.CLAUDE,
            model=claude.model,
        ),
    )
    changed_hashes = ("a" * 64, identity.schema_sha256)
    monkeypatch.setattr(
        runner_module,
        "provider_contract_hashes",
        lambda _request: changed_hashes,
    )
    runner = CollaborationRunner(
        claude_adapter=claude,
        openai_adapter=FakeOpenAI(),
        store=store,
    )

    result = runner.execute(
        request,
        mode=CollaborationMode.OFF,
        committed_at=NOW,
    )

    assert len(claude.calls) == 1
    assert store.loaded_identities[-1].prompt_sha256 == "a" * 64
    assert result.receipt.claude_source is ProviderExecutionSource.PROVIDER
    assert result.run.claude.state is CollaboratorState.FAILED


def test_openai_key_is_call_scoped_and_absent_from_executor_receipts_and_disk(
    tmp_path,
):
    runner, _claude, openai, _store = _runner(tmp_path)
    result = runner.execute(
        _request(),
        mode=CollaborationMode.ADVISORY,
        openai_api_key=KEY,
        committed_at=NOW,
    )

    assert openai.received_key is True
    assert KEY not in repr(runner)
    assert KEY not in repr(result)
    assert KEY not in result.model_dump_json()
    assert all(KEY not in repr(value) for value in runner.__dict__.values())
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()
