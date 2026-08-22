"""Session-only credential boundary for the optional OpenAI collaborator."""

from __future__ import annotations

import json
import os

import pytest

from agents.candidate_review_v1.collaboration import CollaborationMode
from agents.candidate_review_v1.collaboration_session import (
    CollaborationSessionInputError,
    OpenAICollaborationSession,
)


KEY = "sk-proj-sessiononly_1234567890abcdef"


def test_vault_status_and_repr_never_expose_key_or_mutate_environment():
    before = dict(os.environ)
    vault = OpenAICollaborationSession()
    status = vault.configure(
        api_key=KEY,
        mode="required",
        model="gpt-5-2025-08-07",
    )

    public = status.as_dict()
    assert public == {
        "mode": "required",
        "model": "gpt-5-2025-08-07",
        "key_present": True,
        "active": True,
        "masked": "••••",
        "storage": "process_memory",
        "purpose": "intake_inference_adversarial_collaboration",
    }
    assert KEY not in json.dumps(public)
    assert KEY not in repr(status)
    assert KEY not in repr(vault)
    assert dict(os.environ) == before
    assert vault.api_key_for_provider_call() == KEY

    off = vault.configure(mode="off")
    assert off.mode is CollaborationMode.OFF
    assert off.key_present is True
    assert off.active is False
    assert vault.api_key_for_provider_call() is None

    cleared = vault.clear()
    assert cleared.mode is CollaborationMode.OFF
    assert cleared.key_present is False
    assert vault.api_key_for_provider_call() is None


def test_vault_defaults_new_key_to_advisory_and_supports_all_modes():
    vault = OpenAICollaborationSession()
    assert vault.status().mode is CollaborationMode.OFF
    configured = vault.configure(api_key=KEY)
    assert configured.mode is CollaborationMode.ADVISORY
    assert configured.active is True
    assert vault.configure(mode="required").mode is CollaborationMode.REQUIRED
    assert vault.configure(mode="off").mode is CollaborationMode.OFF


@pytest.mark.parametrize(
    "updates",
    (
        {"api_key": 123},
        {"api_key": "not-a-key"},
        {"api_key": "sk-" + "a" * 510},
        {"mode": "sometimes"},
        {"model": ""},
        {"model": "gpt 5"},
        {"model": "g" * 129},
    ),
)
def test_vault_rejects_unbounded_or_invalid_shapes_atomically(updates):
    vault = OpenAICollaborationSession()
    baseline = vault.status()
    with pytest.raises(CollaborationSessionInputError):
        vault.configure(**updates)
    assert vault.status() == baseline


def test_active_mode_requires_a_session_key():
    vault = OpenAICollaborationSession()
    with pytest.raises(CollaborationSessionInputError, match="key is required"):
        vault.configure(mode="required")
    assert vault.status().mode is CollaborationMode.OFF


@pytest.mark.parametrize(
    "submitted_model",
    (
        "sk-proj-modelsecret_1234567890",
        "bearer-token",
        "BearerCredential",
        "authorization:key",
        "api_key:credential",
        "apiKeyCredential",
        "access-token:credential",
        "client-secret:credential",
        "password:credential",
        "202-555-1212",
        "2025550199",
        "(202)555-0199",
        "operator@example.com",
        "AKIA" + "A" * 16,
        "ghp_" + "a" * 36,
        "xoxb-" + "a" * 24,
        "password is correct horse battery staple",
        "api key is natural language secret",
        "-----BEGIN PRIVATE KEY-----abc-----END PRIVATE KEY-----",
    ),
)
def test_secret_or_contact_shaped_model_is_rejected_without_any_echo(
    submitted_model,
):
    vault = OpenAICollaborationSession()
    baseline = vault.configure(
        api_key=KEY,
        mode="advisory",
        model="gpt-5-2025-08-07",
    )

    with pytest.raises(CollaborationSessionInputError) as exc:
        vault.configure(model=submitted_model)

    current = vault.status()
    ui_safe_json = json.dumps(current.as_dict(), ensure_ascii=False)
    assert current == baseline
    assert current.model == "gpt-5-2025-08-07"
    assert vault.api_key_for_provider_call() == KEY
    assert submitted_model not in str(exc.value)
    assert submitted_model not in ui_safe_json
    assert submitted_model not in repr(current)
    assert submitted_model not in repr(vault)
