"""Hermetic acceptance tests for Candidate Review adversarial collaboration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from urllib.parse import quote

import pytest
from pydantic import BaseModel, HttpUrl, ValidationError

from agents.candidate_review_v1.collaboration import (
    ChallengerHypothesis,
    CollaborationLayer,
    CollaborationMode,
    CollaborationRequest,
    CollaborationRun,
    CollaboratorProvider,
    CollaboratorState,
    EvidenceCitation,
    InferenceChallengeOutput,
    IntakeChallengeOutput,
    IntakeSuggestion,
    MAX_CITATIONS_PER_HYPOTHESIS,
    MAX_EVIDENCE_ROWS,
    MAX_INTAKE_GROUP_ITEMS,
    MAX_OUTPUT_HYPOTHESES,
    MAX_OUTPUT_SUGGESTIONS,
    MAX_PROVIDER_INPUT_BYTES,
    MAX_PUBLIC_MATERIALS,
    ProviderCallResult,
    RunDisposition,
    SanitizedEvidence,
    SanitizedIntake,
    SanitizedPublicMaterial,
    IntakeSourceKind,
    SuggestionClassification,
    SuggestionKind,
    TokenUsage,
    challenge_output_model,
    compare_collaboration,
    contains_sensitive_material,
    redact_sensitive_material,
    sanitize_evidence_snapshot,
    sanitize_intake,
)
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateKind,
    EvidenceKind,
    EvidenceRecord,
    LifecycleKind,
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
    SourceTier,
)


NOW = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)
HASH = "a" * 64
AWS_ACCESS_KEY = "AKIA" + "A" * 16
GITHUB_TOKEN = "ghp_" + "a" * 36
SLACK_TOKEN = "xoxb-" + "a" * 24
PEM_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\n"
    "aGVybWV0aWMtdGVzdC1rZXk=\n"
    "-----END PRIVATE KEY-----"
)
LONG_CREDENTIAL = "password is " + "x" * 600
SENSITIVE_SAMPLES = (
    "password is hunter2",
    "password is correct horse battery staple",
    "password is correct.horse.battery.staple",
    "api key is alpha!beta?gamma",
    "api key is abcdefghijk",
    "auth=opaque-value",
    "ANTHROPIC_AUTH_TOKEN",
    "SERVICE_PASSWORD",
    AWS_ACCESS_KEY,
    GITHUB_TOKEN,
    SLACK_TOKEN,
    PEM_PRIVATE_KEY,
    "2025550199",
    "(202)555-0199",
)


def _binding(*, evidence_hash: str = "d" * 64) -> ArtifactBinding:
    return ArtifactBinding(
        client_id="testco",
        client_name="Testco",
        run_id="run-1",
        scope_designator="all-federal",
        scope_sha256="b" * 64,
        profile_sha256="c" * 64,
        evidence_snapshot_sha256=evidence_hash,
    )


def _intake() -> SanitizedIntake:
    return sanitize_intake({
        "client_name": "Testco",
        "website": "https://example.com/products?token=SECRET#team",
        "primary_services": "Secure workflow automation",
        "differentiators": "FedRAMP-ready orchestration",
        "past_performance": "Modernized public records for a state agency",
        "certifications": ["ISO 27001", "SDVOSB"],
        "known_naics": ["541512"],
        "target_agencies": ["Department of State"],
        "geographic_focus": "United States",
        "contact_email": "owner@testco.example",
        "uei": "ABCDEFGH1234",
        "private_notes": "Do not disclose this note",
        "api_key": "sk-this-must-never-appear",
    }, public_materials=[{
        "kind": "official_website",
        "source_url": "https://example.com/platform?api_key=SECRET",
        "text": "Platform details; contact Jane at jane@example.com or 202-555-0199.",
    }])


def _evidence_record() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev-1",
        client_id="testco",
        run_id="run-1",
        scope_sha256="b" * 64,
        source_identity=SourceIdentity(
            source_system="sam.gov",
            record_id="notice-1",
        ),
        source_tier=SourceTier.NOTICE,
        source_kind=EvidenceKind.NOTICE,
        source_name="SAM.gov",
        source_url="https://sam.gov/opp/notice-1/view?token=SECRET",
        retrieved_at=NOW,
        title="Workflow automation market research",
        excerpt=(
            "The Department seeks secure workflow automation. "
            "Email jane@example.gov or call 202-555-0199."
        ),
        official_source=True,
        primary_source=True,
        record_sha256="e" * 64,
        notice_status=NoticeStatus.ACTIVE,
        notice_role=NoticeRole.END_USER_REQUIREMENT,
        verified_at=NOW,
    )


def _evidence() -> tuple[SanitizedEvidence, ...]:
    return sanitize_evidence_snapshot([_evidence_record()])


def _request(
    layer: CollaborationLayer = CollaborationLayer.INTAKE,
) -> CollaborationRequest:
    return CollaborationRequest.create(
        binding=_binding(),
        layer=layer,
        sanitized_intake=_intake(),
        evidence_snapshot=_evidence() if layer is CollaborationLayer.INFERENCE else (),
    )


def _suggestion(
    value: str,
    classification: SuggestionClassification,
    *,
    kind: SuggestionKind = SuggestionKind.KEYWORD,
    refs: tuple[str, ...] = ("intake-form",),
) -> IntakeSuggestion:
    return IntakeSuggestion(
        kind=kind,
        classification=classification,
        value=value,
        rationale=f"The supplied public material supports {value} as a search concept.",
        source_refs=refs,
    )


def _intake_output(*rows: IntakeSuggestion) -> IntakeChallengeOutput:
    return IntakeChallengeOutput(suggestions=tuple(rows))


def _hypothesis(
    *,
    title: str = "State workflow modernization corridor",
    evidence_id: str = "ev-1",
    source_url: str = "https://sam.gov/opp/notice-1/view",
    quote: str = "secure workflow automation",
) -> ChallengerHypothesis:
    return ChallengerHypothesis(
        kind=CandidateKind.RESEARCH_CORRIDOR,
        lifecycle=LifecycleKind.EARLY_SIGNAL,
        title=title,
        agency_key="Department of State",
        buyer_key="enterprise applications",
        program_key="workflow modernization",
        access_route_key="access to validate",
        anchor_evidence_id=evidence_id,
        citations=(EvidenceCitation(
            evidence_id=evidence_id,
            source_url=source_url,
            quote=quote,
        ),),
        inference_chain="The stated requirement may indicate a broader modernization need.",
        counterevidence="No contrary evidence appears in this bounded snapshot.",
        falsifier="A later official record limits the work to unrelated hardware.",
        trigger="A forecast or notice names the same workflow requirement.",
        validate_next="Confirm program ownership and acquisition timing.",
    )


def _artifact(
    provider: CollaboratorProvider,
    request: CollaborationRequest,
    output=None,
    *,
    state: CollaboratorState = CollaboratorState.RETURNED,
) -> ProviderCallResult:
    values = dict(
        provider=provider,
        state=state,
        model="fixture-model",
        prompt_sha256="f" * 64,
        schema_sha256="9" * 64,
        input_sha256=request.request_sha256,
        output=output,
        attempts=1,
        completed_at=NOW,
        response_id="response-1",
    )
    if state is CollaboratorState.NOT_RUN:
        values.update(
            output=None,
            attempts=0,
            completed_at=None,
            response_id=None,
        )
    elif state is CollaboratorState.CACHED:
        values.update(attempts=0)
    elif state is CollaboratorState.FAILED:
        values.update(
            output=None,
            public_failure="provider unavailable",
            response_id=None,
        )
    return ProviderCallResult(**values)


def test_sanitized_intake_is_positive_allowlist_and_strips_contacts_and_url_secrets():
    intake = _intake()
    dumped = intake.model_dump_json()

    assert intake.website == "https://example.com/products"
    assert intake.public_materials[0].source_url == "https://example.com/platform"
    assert "[redacted]" in intake.public_materials[0].text
    for forbidden in (
        "owner@testco.example",
        "jane@example.com",
        "202-555-0199",
        "ABCDEFGH1234",
        "Do not disclose",
        "sk-this-must-never-appear",
        "SECRET",
    ):
        assert forbidden not in dumped


def test_direct_unsanitized_intake_and_evidence_are_rejected():
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedIntake(
            client_name="Testco",
            primary_services="Contact jane@example.com",
        )
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedEvidence(
            evidence_id="ev",
            source_url="https://example.com/record",
            source_kind="notice",
            title="Title",
            excerpt="Call 202-555-0199",
        )


@pytest.mark.parametrize("malicious", SENSITIVE_SAMPLES)
def test_intake_sanitizer_redacts_extended_contact_and_credential_shapes(
    malicious,
):
    intake = sanitize_intake({
        "client_name": "Testco",
        "primary_services": f"Workflow support {malicious}",
    })

    assert "[redacted]" in intake.primary_services
    assert malicious not in intake.model_dump_json()


@pytest.mark.parametrize("malicious", SENSITIVE_SAMPLES)
def test_direct_sanitized_contracts_reject_extended_sensitive_shapes(malicious):
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedIntake(
            client_name="Testco",
            primary_services=f"Workflow support {malicious}",
        )
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedEvidence(
            evidence_id="ev",
            source_url="https://example.com/record",
            source_kind="notice",
            title="Title",
            excerpt=f"Workflow support {malicious}",
        )


def test_long_natural_language_credential_clause_is_fully_redacted_and_rejected():
    intake = sanitize_intake({
        "client_name": "Testco",
        "primary_services": LONG_CREDENTIAL,
    })
    assert intake.primary_services == "[redacted]"

    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedIntake(
            client_name="Testco",
            primary_services=LONG_CREDENTIAL,
        )


@pytest.mark.parametrize("malicious", [
    "ｐａｓｓｗｏｒｄ is fullwidth secret",
    "sk－secret-value-123456",
    "operator＠example.com",
])
def test_core_normalizes_unicode_before_redaction_and_direct_validation(malicious):
    intake = sanitize_intake({
        "client_name": "Testco",
        "primary_services": malicious,
    })

    assert "[redacted]" in intake.primary_services
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedIntake(
            client_name="Testco",
            primary_services=malicious,
        )


def test_sensitive_identifiers_fail_closed_in_direct_contract_construction():
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedPublicMaterial(
            material_id="2025550199",
            kind=IntakeSourceKind.PUBLIC_RESEARCH,
            source_url="https://example.com/research",
            text="Public research summary",
        )
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedEvidence(
            evidence_id="sk-secret-value-123456",
            source_url="https://example.com/record",
            source_kind="notice",
            title="Title",
            excerpt="Public excerpt",
        )
    with pytest.raises(ValidationError, match="contact or credential"):
        SanitizedEvidence(
            evidence_id="ev-1",
            source_url="https://example.com/record",
            source_kind="api_key",
            title="Title",
            excerpt="Public excerpt",
        )


def test_sensitive_identifiers_fail_closed_in_sanitizer_entry_points():
    with pytest.raises(ValidationError, match="contact or credential"):
        sanitize_intake(
            {"client_name": "Testco"},
            public_materials=({
                "material_id": "2025550199",
                "kind": "public_research",
                "source_url": "https://example.com/research",
                "text": "Public research summary",
            },),
        )
    with pytest.raises(ValidationError, match="contact or credential"):
        sanitize_evidence_snapshot(({
            "evidence_id": "ev-1",
            "source_url": "https://example.com/record",
            "source_kind": "password",
            "title": "Title",
            "excerpt": "Public excerpt",
        },))


@pytest.mark.parametrize("path", [
    "sk-secret-value-123456",
    "password/is/hunter2",
    "password-is-hunter2",
    "api-key-is-alpha-secret",
    "client-secret-is-alpha",
    "access-token/is/alpha",
    "private-key-equals-alpha",
    "aws-secret-access-key-is-alpha",
    "openai-api-key-is-alpha",
    "client-secret%2Fis%2Falpha",
    AWS_ACCESS_KEY,
    GITHUB_TOKEN,
    SLACK_TOKEN,
    "2025550199",
    "%73%6b-secret-value-123456",
])
def test_credential_or_contact_material_in_url_paths_is_rejected(path):
    with pytest.raises(ValueError, match="URL path"):
        sanitize_intake({
            "client_name": "Testco",
            "website": f"https://example.com/{path}",
        })


def test_ordinary_public_url_paths_remain_allowed():
    intake = sanitize_intake({
        "client_name": "Testco",
        "website": "https://example.com/docs/security-guidance/account-reset",
    })

    assert intake.website == (
        "https://example.com/docs/security-guidance/account-reset"
    )


@pytest.mark.parametrize("authority", [
    "sk-secret-value-123456.example.com",
    "2025550199.example.com",
    "%73%6b-secret-value-123456.example.com",
    "example.com:sk-secret-value-123456",
    "ANTHROPIC_AUTH_TOKEN.example.com",
])
def test_credential_or_contact_material_in_url_authority_is_rejected(authority):
    with pytest.raises(ValueError, match="URL authority"):
        sanitize_intake({
            "client_name": "Testco",
            "website": f"https://{authority}/research",
        })


def test_multiply_encoded_url_secrets_are_decoded_to_a_fixed_point_and_rejected():
    encoded = "sk%2Dsecret%2Dvalue%2D123456"
    for _ in range(3):
        encoded = quote(encoded, safe="")

    with pytest.raises(ValueError, match="credential material"):
        sanitize_intake({
            "client_name": "Testco",
            "website": f"https://example.com/{encoded}",
        })


def test_percent_encoded_fullwidth_url_secret_is_normalized_and_rejected():
    with pytest.raises(ValueError, match="credential material"):
        sanitize_intake({
            "client_name": "Testco",
            "website": (
                "https://example.com/"
                "sk%EF%BC%8Dsecret%EF%BC%8Dvalue%EF%BC%8D123456"
            ),
        })


def test_malformed_url_fails_closed_without_parser_detail_echo():
    with pytest.raises(ValueError, match="malformed"):
        sanitize_intake({
            "client_name": "Testco",
            "website": "https://[invalid-host/research",
        })


@pytest.mark.parametrize("host", [
    "localhost",
    "intranet",
    "service.internal.corp",
    "host.local",
    "host.lan",
    "host.home",
    "host.test",
    "host.invalid",
    "host.localhost",
    "internal.example",
    "127.0.0.1",
    "127.1",
    "0177.0.0.1",
    "0x7f.0.0.1",
    "2130706433",
    "10.0.0.1",
    "192.168.1.10",
    "169.254.169.254",
    "[::1]",
])
def test_nonpublic_hosts_cannot_enter_collaboration_provider_payloads(host):
    with pytest.raises(ValueError, match="public"):
        sanitize_intake({
            "client_name": "Testco",
            "website": f"https://{host}/research",
        })
    with pytest.raises(ValueError, match="public"):
        sanitize_evidence_snapshot(({
            "evidence_id": "ev-public-boundary",
            "source_url": f"https://{host}/record",
            "source_kind": "award",
            "title": "Public evidence title",
            "excerpt": "Public evidence excerpt",
        },))


def test_public_domain_and_global_ip_are_canonical_provider_urls():
    intake = sanitize_intake({
        "client_name": "Testco",
        "website": "https://Example.COM./research?token=removed",
    })
    global_ipv4 = sanitize_evidence_snapshot(({
        "evidence_id": "ev-global-ip",
        "source_url": "https://8.8.8.8/record",
        "source_kind": "award",
        "title": "Public evidence title",
        "excerpt": "Public evidence excerpt",
    },))[0]
    global_ipv6 = sanitize_evidence_snapshot(({
        "evidence_id": "ev-global-ipv6",
        "source_url": "https://[2606:4700:4700::1111]/record",
        "source_kind": "award",
        "title": "Public IPv6 evidence title",
        "excerpt": "Public IPv6 evidence excerpt",
    },))[0]

    assert intake.website == "https://example.com/research"
    assert global_ipv4.source_url == "https://8.8.8.8/record"
    assert global_ipv6.source_url == "https://[2606:4700:4700::1111]/record"


def test_excessive_url_encoding_fails_closed():
    encoded = "public%20path"
    for _ in range(16):
        encoded = quote(encoded, safe="")

    with pytest.raises(ValueError, match="excessively encoded"):
        sanitize_intake({
            "client_name": "Testco",
            "website": f"https://example.com/{encoded}",
        })


def test_ordinary_public_hostname_and_port_remain_allowed():
    intake = sanitize_intake({
        "client_name": "Testco",
        "website": "https://security-management.example.com:443/research",
    })

    assert intake.website == (
        "https://security-management.example.com:443/research"
    )


def test_sanitized_evidence_excludes_unnecessary_fields_and_query_secrets():
    row = _evidence()[0]
    dumped = row.model_dump_json()
    assert row.source_url == "https://sam.gov/opp/notice-1/view"
    assert "[redacted]" in row.excerpt
    assert "contacts" not in dumped and "private_notes" not in dumped
    assert "jane@example.gov" not in dumped and "202-555-0199" not in dumped


@pytest.mark.parametrize("flag", ["official_source", "primary_source"])
@pytest.mark.parametrize("value", ["false", "0", "no", 0, 1, None])
def test_evidence_trust_flags_require_exact_booleans(flag, value):
    row = {
        "evidence_id": "ev-1",
        "source_url": "https://example.com/record",
        "source_kind": "notice",
        "title": "Title",
        "excerpt": "Public excerpt",
        flag: value,
    }

    with pytest.raises(ValueError, match="exact boolean"):
        sanitize_evidence_snapshot((row,))


def test_untrusted_mapping_cannot_self_assert_official_primary_notice_status():
    with pytest.raises(ValueError, match="notice evidence"):
        sanitize_evidence_snapshot(({
            "evidence_id": "ev-1",
            "source_url": "https://arbitrary.example.com/record",
            "source_kind": "notice",
            "title": "Untrusted notice claim",
            "excerpt": "The source claims a current requirement.",
            "official_source": True,
            "primary_source": True,
        },))

    assert _evidence()[0].official_source is True
    assert _evidence()[0].primary_source is True


def test_evidence_record_subclass_serializer_is_rejected_before_invocation():
    serializer_calls = []

    class FabricatedEvidenceRecord(EvidenceRecord):
        def model_dump(self, *args, **kwargs):
            serializer_calls.append(True)
            return {
                "evidence_id": "fabricated-notice",
                "source_url": "https://sam.gov/opp/fabricated-notice/view",
                "source_kind": "notice",
                "title": "Fabricated official notice",
                "excerpt": "A fabricated requirement must never become trusted.",
                "official_source": True,
                "primary_source": True,
            }

    malicious = FabricatedEvidenceRecord(
        **_evidence_record().model_dump(mode="python")
    )

    with pytest.raises(TypeError, match="subclasses"):
        sanitize_evidence_snapshot((malicious,))
    assert serializer_calls == []


def test_exact_model_construct_evidence_is_revalidated_before_trust_copy():
    valid = _evidence_record()
    forged = EvidenceRecord.model_construct(**valid.__dict__)
    object.__setattr__(forged, "source_url", HttpUrl(
        "https://arbitrary.example.com/fabricated-notice"
    ))

    with pytest.raises(ValueError, match="exact trusted contract"):
        sanitize_evidence_snapshot((forged,))


def test_arbitrary_pydantic_evidence_row_is_rejected_not_serialized():
    class ArbitraryRow(BaseModel):
        official_source: bool = True
        primary_source: bool = True

    with pytest.raises(TypeError, match="exact EvidenceRecord"):
        sanitize_evidence_snapshot((ArbitraryRow(),))


def test_direct_sanitized_notice_requires_sam_official_primary_provenance():
    base = {
        "evidence_id": "ev-1",
        "source_kind": "notice",
        "title": "Notice title",
        "excerpt": "Public notice excerpt",
        "official_source": True,
        "primary_source": True,
    }
    with pytest.raises(ValidationError, match="SAM.gov"):
        SanitizedEvidence(
            **base,
            source_url="https://arbitrary.example.com/record",
        )
    with pytest.raises(ValidationError, match="official primary"):
        SanitizedEvidence(
            **{**base, "primary_source": False},
            source_url="https://sam.gov/opp/ev-1/view",
        )


def test_request_is_one_canonical_payload_for_both_providers_and_hash_bound():
    request = _request()
    received = {}

    def provider(name):
        received[name] = request.canonical_json()

    provider("claude")
    provider("openai")
    assert received["claude"] == received["openai"]
    assert request.request_sha256 not in request.canonical_json()
    assert request.request_sha256 == __import__("hashlib").sha256(
        request.canonical_bytes
    ).hexdigest()


def test_provider_payload_excludes_internal_binding_and_evidence_record_hashes():
    request = _request(CollaborationLayer.INFERENCE)
    payload = json.loads(request.canonical_json())

    assert set(payload) == {
        "schema_version",
        "layer",
        "sanitized_intake",
        "evidence_snapshot",
        "prompt_version",
        "output_schema_version",
    }
    assert "binding" not in payload
    assert "record_sha256" not in payload["evidence_snapshot"][0]
    assert request.binding.run_id == "run-1"
    assert request.binding.client_id == "testco"
    assert request.evidence_snapshot[0].record_sha256 == "e" * 64
    for internal_value in (
        request.binding.run_id,
        request.binding.client_id,
        request.binding.scope_sha256,
        request.binding.profile_sha256,
        request.binding.evidence_snapshot_sha256,
        request.evidence_snapshot[0].record_sha256,
    ):
        assert internal_value not in request.canonical_json()

    rebound = CollaborationRequest.create(
        binding=ArtifactBinding(
            client_id="testco",
            client_name="Testco",
            run_id="run-99",
            scope_designator="changed-internal-scope",
            scope_sha256="1" * 64,
            profile_sha256="2" * 64,
            evidence_snapshot_sha256="3" * 64,
        ),
        layer=request.layer,
        sanitized_intake=request.sanitized_intake,
        evidence_snapshot=request.evidence_snapshot,
    )
    assert rebound.binding != request.binding
    assert rebound.canonical_bytes == request.canonical_bytes
    assert rebound.request_sha256 == request.request_sha256


def test_request_hash_changes_with_input_prompt_schema_and_evidence():
    base = _request()
    changed_intake = CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INTAKE,
        sanitized_intake=sanitize_intake({
            "client_name": "Testco",
            "primary_services": "Different capability",
        }),
    )
    changed_prompt = CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INTAKE,
        sanitized_intake=_intake(),
        prompt_version="candidate-review-collaboration-v2",
    )
    inference = _request(CollaborationLayer.INFERENCE)
    assert len({
        base.request_sha256,
        changed_intake.request_sha256,
        changed_prompt.request_sha256,
        inference.request_sha256,
    }) == 4


def test_request_rejects_tampered_hash_and_wrong_layer_evidence_shape():
    request = _request()
    payload = request.model_dump(mode="python")
    payload["request_sha256"] = HASH
    with pytest.raises(ValidationError, match="does not match"):
        CollaborationRequest(**payload)
    with pytest.raises(ValidationError, match="cannot receive"):
        CollaborationRequest.create(
            binding=_binding(),
            layer=CollaborationLayer.INTAKE,
            sanitized_intake=_intake(),
            evidence_snapshot=_evidence(),
        )
    with pytest.raises(ValidationError, match="requires an evidence"):
        CollaborationRequest.create(
            binding=_binding(),
            layer=CollaborationLayer.INFERENCE,
            sanitized_intake=_intake(),
        )


def test_contracts_are_strict_frozen_and_forbid_extra_fields():
    intake = _intake()
    with pytest.raises(ValidationError):
        SanitizedIntake(**{**intake.model_dump(mode="python"), "private_notes": "x"})
    with pytest.raises(ValidationError):
        SanitizedIntake(client_name="Testco", certifications=["ISO"])
    with pytest.raises(ValidationError):
        intake.primary_services = "changed"


def test_challenge_output_model_is_layer_exact():
    assert challenge_output_model(CollaborationLayer.INTAKE) is IntakeChallengeOutput
    assert challenge_output_model(CollaborationLayer.INFERENCE) is InferenceChallengeOutput


@pytest.mark.parametrize("phrase", [
    "pursue this",
    "no-bid this",
    "automatically disqualify this",
])
def test_suggestions_and_hypotheses_reject_business_disposition_language(phrase):
    with pytest.raises(ValidationError, match="forbidden language"):
        IntakeSuggestion(
            kind=SuggestionKind.KEYWORD,
            classification=SuggestionClassification.CORE,
            value="workflow",
            rationale=phrase,
            source_refs=("intake-form",),
        )
    values = _hypothesis().model_dump(mode="python")
    values["validate_next"] = phrase
    with pytest.raises(ValidationError, match="forbidden language"):
        ChallengerHypothesis(**values)


@pytest.mark.parametrize(("target", "field", "malicious"), [
    ("suggestion", "value", "Email owner@example.com"),
    ("suggestion", "rationale", "api_key=super-secret-value"),
    ("suggestion", "source_refs", ("Bearer abcdefghijklmnop",)),
    ("hypothesis", "agency_key", "owner@example.com"),
    ("hypothesis", "inference_chain", "password=do-not-store"),
    ("hypothesis", "validate_next", "Call 202-555-0199"),
    ("hypothesis", "anchor_evidence_id", "sk-secret-value-123456"),
])
def test_model_generated_free_text_rejects_contacts_and_credentials(
    target,
    field,
    malicious,
):
    if target == "suggestion":
        values = _suggestion(
            "workflow",
            SuggestionClassification.CORE,
        ).model_dump(mode="python")
        values[field] = malicious
        with pytest.raises(ValidationError, match="contact or credential"):
            IntakeSuggestion(**values)
        return

    values = _hypothesis().model_dump(mode="python")
    values[field] = malicious
    if field == "anchor_evidence_id":
        citation = values["citations"][0]
        values["citations"] = ({**citation, "evidence_id": malicious},)
    with pytest.raises(ValidationError, match="contact or credential"):
        ChallengerHypothesis(**values)


def test_output_schemas_have_no_treatment_recommendation_or_decision_fields():
    schema = str(IntakeChallengeOutput.model_json_schema()).casefold()
    inference_schema = str(InferenceChallengeOutput.model_json_schema()).casefold()
    for forbidden in ("treatment", "win_probability", "bid_no_bid"):
        assert forbidden not in schema
        assert forbidden not in inference_schema


def test_intake_output_rejects_same_item_with_conflicting_classifications():
    with pytest.raises(ValidationError, match="duplicate suggestions"):
        IntakeChallengeOutput(suggestions=(
            _suggestion("workflow", SuggestionClassification.CORE),
            _suggestion("workflow", SuggestionClassification.ADJACENT),
        ))


def test_intake_comparator_is_deterministic_and_separates_all_buckets():
    request = _request()
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        _intake_output(
            _suggestion("Zero trust", SuggestionClassification.CORE),
            _suggestion("Automation", SuggestionClassification.CORE),
            _suggestion("State", SuggestionClassification.TARGET,
                        kind=SuggestionKind.AGENCY),
            _suggestion("Unproven buyer intent", SuggestionClassification.ASSUMPTION,
                        kind=SuggestionKind.ARGUMENT, refs=()),
        ),
    )
    openai = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        _intake_output(
            _suggestion("zero-trust", SuggestionClassification.CORE),
            _suggestion("Automation", SuggestionClassification.ADJACENT),
            _suggestion("Records management", SuggestionClassification.CORE),
        ),
    )

    first = compare_collaboration(request, claude, openai)
    second = compare_collaboration(request, claude, openai)
    assert first == second
    assert [row.label for row in first.agreements] == ["Zero trust"]
    assert [row.label for row in first.classification_conflicts] == ["Automation"]
    assert {row.label for row in first.claude_only} == {
        "State", "Unproven buyer intent"}
    assert [row.label for row in first.openai_only] == ["Records management"]
    assert [row.label for row in first.unsupported_assumptions] == [
        "Unproven buyer intent"]
    assert first.analyst_questions


def test_false_intake_source_reference_is_visible_as_unsupported():
    request = _request()
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        _intake_output(_suggestion(
            "Workflow",
            SuggestionClassification.CORE,
            refs=("not-in-request",),
        )),
    )
    openai = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        _intake_output(),
    )
    compared = compare_collaboration(request, claude, openai)
    assert [row.label for row in compared.unsupported_assumptions] == ["Workflow"]


def test_openai_only_inference_surfaces_only_with_exact_evidence_and_link_refs():
    request = _request(CollaborationLayer.INFERENCE)
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        InferenceChallengeOutput(),
    )
    valid = _hypothesis()
    openai = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        InferenceChallengeOutput(hypotheses=(valid,)),
    )
    compared = compare_collaboration(request, claude, openai)
    assert compared.surfaced_openai_hypotheses == (valid,)
    assert not compared.unsupported_assumptions


def test_generic_token_quote_is_held_but_specific_single_token_is_supported():
    generic_request = _request(CollaborationLayer.INFERENCE)
    for invalid_quote in ("the", "workflo"):
        generic = _hypothesis(quote=invalid_quote)
        generic_comparison = compare_collaboration(
            generic_request,
            _artifact(
                CollaboratorProvider.CLAUDE,
                generic_request,
                InferenceChallengeOutput(),
            ),
            _artifact(
                CollaboratorProvider.OPENAI,
                generic_request,
                InferenceChallengeOutput(hypotheses=(generic,)),
            ),
        )
        assert generic_comparison.surfaced_openai_hypotheses == ()
        assert [
            row.label for row in generic_comparison.unsupported_assumptions
        ] == [generic.title]

    fedramp_evidence = _evidence()[0].model_copy(update={
        "excerpt": "The agency requires FedRAMP authorization for the platform.",
    })
    fedramp_request = CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INFERENCE,
        sanitized_intake=_intake(),
        evidence_snapshot=(fedramp_evidence,),
    )
    specific = _hypothesis(quote="FedRAMP")
    specific_comparison = compare_collaboration(
        fedramp_request,
        _artifact(
            CollaboratorProvider.CLAUDE,
            fedramp_request,
            InferenceChallengeOutput(),
        ),
        _artifact(
            CollaboratorProvider.OPENAI,
            fedramp_request,
            InferenceChallengeOutput(hypotheses=(specific,)),
        ),
    )
    assert specific_comparison.surfaced_openai_hypotheses == (specific,)
    assert specific_comparison.unsupported_assumptions == ()


@pytest.mark.parametrize(("evidence_change", "surfaces"), [
    ({}, True),
    ({"official_source": False}, False),
    ({"primary_source": False}, False),
    ({"source_kind": "award"}, False),
])
def test_openai_only_current_notice_requires_official_primary_notice_anchor(
    evidence_change,
    surfaces,
):
    evidence = _evidence()[0].model_copy(update=evidence_change)
    if evidence_change in (
        {"official_source": False},
        {"primary_source": False},
    ):
        with pytest.raises(ValidationError, match="official primary"):
            CollaborationRequest.create(
                binding=_binding(),
                layer=CollaborationLayer.INFERENCE,
                sanitized_intake=_intake(),
                evidence_snapshot=(evidence,),
            )
        return
    request = CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INFERENCE,
        sanitized_intake=_intake(),
        evidence_snapshot=(evidence,),
    )
    hypothesis_values = _hypothesis().model_dump(mode="python")
    hypothesis_values.update({
        "kind": CandidateKind.CURRENT_NOTICE,
        "lifecycle": LifecycleKind.MARKET_RESEARCH,
    })
    hypothesis = ChallengerHypothesis(**hypothesis_values)
    compared = compare_collaboration(
        request,
        _artifact(
            CollaboratorProvider.CLAUDE,
            request,
            InferenceChallengeOutput(),
        ),
        _artifact(
            CollaboratorProvider.OPENAI,
            request,
            InferenceChallengeOutput(hypotheses=(hypothesis,)),
        ),
    )

    assert bool(compared.surfaced_openai_hypotheses) is surfaces
    if surfaces:
        assert compared.unsupported_assumptions == ()
    else:
        assert compared.surfaced_openai_hypotheses == ()
        assert [row.label for row in compared.unsupported_assumptions] == [
            hypothesis.title]
        assert any(
            "validate the cited support before any change" in question.question
            for question in compared.analyst_questions
        )


@pytest.mark.parametrize(("change", "value"), [
    ("evidence_id", "not-in-snapshot"),
    ("source_url", "https://sam.gov/opp/another/view"),
    ("quote", "words not present in evidence"),
])
def test_openai_only_inference_false_reference_is_held_for_analyst_validation(
    change,
    value,
):
    request = _request(CollaborationLayer.INFERENCE)
    kwargs = {change: value}
    invalid = _hypothesis(**kwargs)
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        InferenceChallengeOutput(),
    )
    openai = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        InferenceChallengeOutput(hypotheses=(invalid,)),
    )
    compared = compare_collaboration(request, claude, openai)
    assert compared.surfaced_openai_hypotheses == ()
    assert [row.label for row in compared.unsupported_assumptions] == [invalid.title]
    assert any("validate the cited support" in q.question
               for q in compared.analyst_questions)


def test_provider_artifacts_are_request_provider_and_layer_bound():
    request = _request()
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        _intake_output(),
    )
    wrong_hash = claude.model_copy(update={"input_sha256": "1" * 64})
    with pytest.raises(ValueError, match="another request"):
        compare_collaboration(
            request,
            wrong_hash,
            _artifact(CollaboratorProvider.OPENAI, request, _intake_output()),
        )
    wrong_layer = claude.model_copy(update={"output": InferenceChallengeOutput()})
    with pytest.raises(ValueError, match="another challenge layer"):
        compare_collaboration(
            request,
            wrong_layer,
            _artifact(CollaboratorProvider.OPENAI, request, _intake_output()),
        )


def test_provider_result_state_attempt_usage_and_failure_safety_are_strict():
    request = _request()
    output = _intake_output()
    usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    returned = _artifact(CollaboratorProvider.OPENAI, request, output)
    assert returned.state is CollaboratorState.RETURNED
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=99)
    with pytest.raises(ValidationError, match="less than or equal to 2"):
        ProviderCallResult(**{
            **returned.model_dump(mode="python"),
            "attempts": 3,
        })
    with pytest.raises(ValidationError, match="sensitive"):
        ProviderCallResult(
            provider=CollaboratorProvider.OPENAI,
            state=CollaboratorState.FAILED,
            model="fixture-model",
            prompt_sha256="f" * 64,
            schema_sha256="9" * 64,
            input_sha256=request.request_sha256,
            attempts=1,
            public_failure="Authorization: Bearer sk-supersecret123",
            completed_at=NOW,
        )
    cached = ProviderCallResult(
        provider=CollaboratorProvider.OPENAI,
        state=CollaboratorState.CACHED,
        model="fixture-model",
        prompt_sha256="f" * 64,
        schema_sha256="9" * 64,
        input_sha256=request.request_sha256,
        output=output,
        attempts=0,
        token_usage=usage,
    )
    assert cached.token_usage == usage


@pytest.mark.parametrize("malicious", SENSITIVE_SAMPLES)
def test_direct_provider_failure_artifacts_reject_sensitive_material(malicious):
    request = _request()

    with pytest.raises(ValidationError, match="sensitive"):
        ProviderCallResult(
            provider=CollaboratorProvider.OPENAI,
            state=CollaboratorState.FAILED,
            model="fixture-model",
            prompt_sha256="f" * 64,
            schema_sha256="9" * 64,
            input_sha256=request.request_sha256,
            attempts=1,
            public_failure=f"provider unavailable: {malicious}",
            completed_at=NOW,
        )


@pytest.mark.parametrize("field", ["model", "response_id"])
def test_provider_metadata_cannot_carry_credentials(field):
    request = _request()
    values = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        _intake_output(),
    ).model_dump(mode="python")
    values[field] = "sk-secret-value-123456"

    with pytest.raises(ValidationError, match="contact or credential"):
        ProviderCallResult(**values)


def test_off_advisory_and_required_mode_semantics():
    request = _request()
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        _intake_output(),
    )
    not_run = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        state=CollaboratorState.NOT_RUN,
    )
    failed = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        state=CollaboratorState.FAILED,
    )
    returned = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        _intake_output(),
    )

    assert CollaborationRun.create(
        request=request,
        mode=CollaborationMode.OFF,
        claude=claude,
        openai=not_run,
    ).disposition is RunDisposition.OFF_CONTINUE
    assert CollaborationRun.create(
        request=request,
        mode=CollaborationMode.ADVISORY,
        claude=claude,
        openai=failed,
    ).disposition is RunDisposition.CONTINUE_DEGRADED
    assert CollaborationRun.create(
        request=request,
        mode=CollaborationMode.REQUIRED,
        claude=claude,
        openai=failed,
    ).disposition is RunDisposition.PAUSED
    assert CollaborationRun.create(
        request=request,
        mode=CollaborationMode.REQUIRED,
        claude=claude,
        openai=returned,
    ).disposition is RunDisposition.CONTINUE


def test_required_mode_requires_response_not_model_agreement():
    request = _request()
    claude = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        _intake_output(_suggestion(
            "Workflow", SuggestionClassification.CORE)),
    )
    openai = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        _intake_output(_suggestion(
            "Workflow", SuggestionClassification.ADJACENT)),
    )
    run = CollaborationRun.create(
        request=request,
        mode=CollaborationMode.REQUIRED,
        claude=claude,
        openai=openai,
    )
    assert run.disposition is RunDisposition.CONTINUE
    assert len(run.comparison.classification_conflicts) == 1


@pytest.mark.parametrize("mode", list(CollaborationMode))
def test_claude_failure_pauses_every_mode(mode):
    request = _request()
    claude_failed = _artifact(
        CollaboratorProvider.CLAUDE,
        request,
        state=CollaboratorState.FAILED,
    )
    not_run = _artifact(
        CollaboratorProvider.OPENAI,
        request,
        state=CollaboratorState.NOT_RUN,
    )
    run = CollaborationRun.create(
        request=request,
        mode=mode,
        claude=claude_failed,
        openai=not_run,
    )
    assert run.disposition is RunDisposition.PAUSED


def test_off_rejects_openai_execution_even_when_claude_failed():
    request = _request()
    with pytest.raises(ValidationError, match="off mode cannot execute"):
        CollaborationRun.create(
            request=request,
            mode=CollaborationMode.OFF,
            claude=_artifact(
                CollaboratorProvider.CLAUDE,
                request,
                state=CollaboratorState.FAILED,
            ),
            openai=_artifact(
                CollaboratorProvider.OPENAI, request, _intake_output()),
        )


def test_run_can_never_claim_it_mutated_the_analyst_packet():
    request = _request()
    run = CollaborationRun.create(
        request=request,
        mode=CollaborationMode.OFF,
        claude=_artifact(
            CollaboratorProvider.CLAUDE, request, _intake_output()),
        openai=_artifact(
            CollaboratorProvider.OPENAI,
            request,
            state=CollaboratorState.NOT_RUN,
        ),
    )
    with pytest.raises(ValidationError):
        CollaborationRun(**{
            **run.model_dump(mode="python"),
            "analyst_packet_mutated": True,
        })


@pytest.mark.parametrize("raw", (
    "password-is-hunter2",
    "client-secret-is-alpha",
    "access-token/is/alpha",
    "aws-secret-access-key-is-alpha",
    "password was correct.horse",
    "password equals alpha!beta",
    "password -> alpha",
    "OPENAI_API_KEY hunter2value",
    "AWS_SECRET_ACCESS_KEY hunter2value",
    "AUTH_TOKEN hunter2value",
    "password\nis hunter",
    "password\tis hunter",
    "client secret\nwas hunter",
    "OPENAI_API_KEY\nhunter",
    "名前@例え.テスト",
    "+44 20 7946 0958",
    "+49 30 901820",
    "pass\N{COMBINING ACUTE ACCENT}word",
    "api ke\N{COMBINING ACUTE ACCENT}y",
    "tok\N{COMBINING ACUTE ACCENT}en-is.alpha",
    "sk%2Dsecretvalue123456",
    "password%2520is%2520hunter",
    "password&#32;is&#32;hunter",
))
def test_shared_privacy_boundary_closes_separator_unicode_and_encoding_bypasses(
    raw,
):
    assert contains_sensitive_material(raw) is True
    assert redact_sensitive_material(raw) == "[redacted]"


@pytest.mark.parametrize(("raw", "expected"), (
    ("key issues include FedRAMP", "key issues include FedRAMP"),
    ("token issuance service", "token issuance service"),
    ("hello\nworld", "hello world"),
    ("café résumé", "café résumé"),
))
def test_shared_privacy_boundary_preserves_benign_analysis_and_diacritics(
    raw,
    expected,
):
    assert contains_sensitive_material(raw) is False
    assert redact_sensitive_material(raw) == expected


def test_shared_privacy_scan_is_linear_enough_for_maximum_safe_text():
    safe = "x" * 50_000
    started = time.perf_counter()

    for _ in range(5):
        assert contains_sensitive_material(safe) is False
        assert redact_sensitive_material(safe) == safe

    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize("raw", (
    "password\N{ZERO WIDTH SPACE}is hunter",
    "password\x00is hunter",
    "private\ue000note",
    "unassigned\u0378value",
))
def test_shared_privacy_boundary_fails_closed_on_hidden_controls(raw):
    assert contains_sensitive_material(raw) is True
    assert redact_sensitive_material(raw) == "[redacted]"


def test_free_text_decode_budget_fails_closed_instead_of_partial_decoding():
    encoded = "password is hunter2"
    for _ in range(17):
        encoded = quote(encoded, safe="")

    assert contains_sensitive_material(encoded) is True
    assert redact_sensitive_material(encoded) == "[redacted]"


def _budget_materials(count: int, *, text_size: int = 8):
    return tuple(
        SanitizedPublicMaterial(
            material_id=f"material-{index:03d}",
            kind=IntakeSourceKind.PUBLIC_RESEARCH,
            source_url=f"https://research.example.org/{index}",
            text="x" * text_size,
        )
        for index in range(count)
    )


def test_request_row_caps_allow_the_boundary_and_reject_one_more():
    intake = SanitizedIntake(
        client_name="Testco",
        certifications=tuple(
            f"certification-{index}" for index in range(MAX_INTAKE_GROUP_ITEMS)
        ),
        public_materials=_budget_materials(MAX_PUBLIC_MATERIALS),
    )
    request = CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INTAKE,
        sanitized_intake=intake,
    )
    assert len(request.sanitized_intake.public_materials) == MAX_PUBLIC_MATERIALS
    with pytest.raises(ValidationError):
        SanitizedIntake(
            client_name="Testco",
            public_materials=_budget_materials(MAX_PUBLIC_MATERIALS + 1),
        )


def test_request_byte_budget_allows_bounded_input_and_rejects_oversize():
    allowed = SanitizedIntake(
        client_name="Testco",
        public_materials=_budget_materials(15, text_size=49_000),
    )
    request = CollaborationRequest.create(
        binding=_binding(),
        layer=CollaborationLayer.INTAKE,
        sanitized_intake=allowed,
    )
    assert len(request.canonical_bytes) <= MAX_PROVIDER_INPUT_BYTES
    oversized = SanitizedIntake(
        client_name="Testco",
        public_materials=_budget_materials(17, text_size=49_000),
    )
    with pytest.raises(ValidationError, match="byte budget"):
        CollaborationRequest.create(
            binding=_binding(),
            layer=CollaborationLayer.INTAKE,
            sanitized_intake=oversized,
        )


def test_output_collection_caps_are_explicit_and_fail_closed():
    suggestion = _suggestion("workflow", SuggestionClassification.CORE)
    with pytest.raises(ValidationError):
        IntakeChallengeOutput(
            suggestions=(suggestion,) * (MAX_OUTPUT_SUGGESTIONS + 1)
        )
    hypothesis = _hypothesis()
    with pytest.raises(ValidationError):
        InferenceChallengeOutput(
            hypotheses=(hypothesis,) * (MAX_OUTPUT_HYPOTHESES + 1)
        )
    citation = hypothesis.citations[0]
    values = hypothesis.model_dump(mode="python")
    values["citations"] = tuple(
        citation.model_copy(update={"evidence_id": f"ev-{index}"})
        for index in range(MAX_CITATIONS_PER_HYPOTHESIS + 1)
    )
    with pytest.raises(ValidationError):
        ChallengerHypothesis(**values)


def test_evidence_row_cap_rejects_oversize_snapshot():
    rows = tuple(
        SanitizedEvidence(
            evidence_id=f"evidence-{index:03d}",
            source_url=f"https://agency.example.org/evidence/{index}",
            source_kind="award",
            title=f"Award evidence {index}",
            excerpt="Bounded official award text.",
        )
        for index in range(MAX_EVIDENCE_ROWS + 1)
    )
    with pytest.raises(ValidationError):
        CollaborationRequest.create(
            binding=_binding(),
            layer=CollaborationLayer.INFERENCE,
            sanitized_intake=SanitizedIntake(client_name="Testco"),
            evidence_snapshot=rows,
        )


@pytest.mark.parametrize("field", ("prompt_version", "output_schema_version"))
@pytest.mark.parametrize("malicious", (
    "password-v1",
    "sk-secret-value-123456",
    "openai-api-key",
    "token\N{COMBINING ACUTE ACCENT}-is.alpha",
))
def test_request_version_identifiers_reject_sensitive_material_without_echo(
    field,
    malicious,
):
    kwargs = {field: malicious}
    with pytest.raises(ValidationError) as exc_info:
        CollaborationRequest.create(
            binding=_binding(),
            layer=CollaborationLayer.INTAKE,
            sanitized_intake=SanitizedIntake(client_name="Testco"),
            **kwargs,
        )
    assert malicious not in str(exc_info.value)
