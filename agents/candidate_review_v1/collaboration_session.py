"""Process-memory credential boundary for the optional OpenAI collaborator.

The Command Center may accept an operator-supplied OpenAI API key for the
current server process.  This module is the only owner of that session value:
it never writes the key to ``os.environ``, disk, logs, status payloads, or an
object representation.  A later in-process orchestration adapter may request
the key explicitly for one provider call; ordinary status consumers receive
only non-secret mode and model metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from threading import RLock
from typing import Final, Literal

from agents.candidate_review_v1.collaboration import (
    CollaborationMode,
    _contains_sensitive_material,
    validate_provider_model,
)
from agents.candidate_review_v1.collaboration_providers import (
    DEFAULT_OPENAI_MODEL,
)


_MIN_API_KEY_LENGTH: Final = 20
_MAX_API_KEY_LENGTH: Final = 512
_MAX_MODEL_LENGTH: Final = 128
_API_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_-]+$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_EMAIL_RE = re.compile(
    r"(?i)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[.-]?)?\d{3}[.-]\d{3}[.-]\d{4}(?!\d)"
)
_CREDENTIAL_NAMES = frozenset({
    "apikey",
    "auth",
    "authentication",
    "authorization",
    "bearer",
    "clientsecret",
    "key",
    "password",
    "passwd",
    "secret",
    "sk",
    "token",
    "accesstoken",
})
_CREDENTIAL_PREFIXES = (
    "apikey",
    "authorization",
    "bearer",
    "clientsecret",
    "key",
    "password",
    "passwd",
    "secret",
    "sk",
    "token",
    "accesstoken",
)
_UNSET: Final = object()


class CollaborationSessionInputError(ValueError):
    """A session update had an unsupported type or bounded value."""


@dataclass(frozen=True)
class CollaborationSessionStatus:
    """Safe, JSON-ready metadata; this type has no credential field."""

    mode: CollaborationMode
    model: str
    key_present: bool
    active: bool
    masked: str
    storage: Literal["process_memory"] = "process_memory"
    purpose: Literal["intake_inference_adversarial_collaboration"] = (
        "intake_inference_adversarial_collaboration"
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "model": self.model,
            "key_present": self.key_present,
            "active": self.active,
            # Constant masking confirms configuration without returning even a
            # credential suffix.
            "masked": self.masked,
            "storage": self.storage,
            "purpose": self.purpose,
        }


def _validated_api_key(value: object) -> str:
    if type(value) is not str:
        raise CollaborationSessionInputError("api_key must be text")
    if not _MIN_API_KEY_LENGTH <= len(value) <= _MAX_API_KEY_LENGTH:
        raise CollaborationSessionInputError(
            "api_key does not have a supported OpenAI key length"
        )
    if _API_KEY_RE.fullmatch(value) is None:
        raise CollaborationSessionInputError(
            "api_key does not have a supported OpenAI key shape"
        )
    return value


def _validated_model(value: object) -> str:
    try:
        normalized = validate_provider_model(value)
    except ValueError:
        raise CollaborationSessionInputError(
            "model cannot contain credential or contact-shaped material"
        ) from None
    if not 1 <= len(normalized) <= _MAX_MODEL_LENGTH:
        raise CollaborationSessionInputError("model length is unsupported")
    folded = normalized.casefold()
    segments = tuple(
        segment for segment in re.split(r"[._:-]+", folded) if segment
    )
    joined_pairs = {
        "".join(segments[index:index + 2])
        for index in range(max(0, len(segments) - 1))
    }
    credential_shaped = (
        _contains_sensitive_material(value)
        or any(
            segment.startswith(_CREDENTIAL_PREFIXES)
            for segment in segments
        )
        or any(segment in _CREDENTIAL_NAMES for segment in segments)
        or bool(joined_pairs & _CREDENTIAL_NAMES)
        or _EMAIL_RE.search(normalized) is not None
        or _PHONE_RE.search(normalized) is not None
    )
    if credential_shaped:
        raise CollaborationSessionInputError(
            "model cannot contain credential or contact-shaped material"
        )
    if _MODEL_RE.fullmatch(normalized) is None:
        raise CollaborationSessionInputError(
            "model must be a canonical provider model token"
        )
    return normalized


def _validated_mode(value: object) -> CollaborationMode:
    if isinstance(value, CollaborationMode):
        return value
    if type(value) is not str:
        raise CollaborationSessionInputError("mode must be text")
    try:
        return CollaborationMode(value)
    except ValueError as exc:
        raise CollaborationSessionInputError(
            "mode must be off, advisory, or required"
        ) from exc


class OpenAICollaborationSession:
    """Thread-safe vault whose secret cannot appear in default representations."""

    __slots__ = ("__api_key", "__lock", "__mode", "__model")

    def __init__(self) -> None:
        self.__lock = RLock()
        self.__api_key: str | None = None
        self.__mode = CollaborationMode.OFF
        self.__model = DEFAULT_OPENAI_MODEL

    def __repr__(self) -> str:
        status = self.status()
        return (
            "<OpenAICollaborationSession "
            f"mode={status.mode.value!r} model={status.model!r} "
            f"key_present={status.key_present}>"
        )

    def status(self) -> CollaborationSessionStatus:
        with self.__lock:
            present = self.__api_key is not None
            try:
                public_model = _validated_model(self.__model)
            except CollaborationSessionInputError:
                # Defensive migration boundary: a vault object retained by a
                # hot-reloaded development server must not expose a model value
                # accepted by an older, weaker validator.
                public_model = DEFAULT_OPENAI_MODEL
            return CollaborationSessionStatus(
                mode=self.__mode,
                model=public_model,
                key_present=present,
                active=present and self.__mode is not CollaborationMode.OFF,
                masked="••••" if present else "",
            )

    def configure(
        self,
        *,
        api_key: object = _UNSET,
        mode: object = _UNSET,
        model: object = _UNSET,
    ) -> CollaborationSessionStatus:
        """Atomically validate and apply one bounded session update.

        Supplying a new key without an explicit mode selects ``advisory``.
        ``required`` is available for a later orchestration step that should
        pause when the independent OpenAI response is unavailable.  No active
        mode can be selected until a valid key is present.
        """

        with self.__lock:
            next_key = self.__api_key
            next_mode = self.__mode
            next_model = _validated_model(self.__model)
            if api_key is not _UNSET:
                next_key = _validated_api_key(api_key)
                if mode is _UNSET and next_mode is CollaborationMode.OFF:
                    next_mode = CollaborationMode.ADVISORY
            if mode is not _UNSET:
                next_mode = _validated_mode(mode)
            if model is not _UNSET:
                next_model = _validated_model(model)
            if next_mode is not CollaborationMode.OFF and next_key is None:
                raise CollaborationSessionInputError(
                    "an OpenAI API key is required for an active mode"
                )
            self.__api_key = next_key
            self.__mode = next_mode
            self.__model = next_model
            return self.status()

    def clear(self) -> CollaborationSessionStatus:
        """Forget the credential and return to the explicit off mode."""

        with self.__lock:
            self.__api_key = None
            self.__mode = CollaborationMode.OFF
            return self.status()

    def api_key_for_provider_call(self) -> str | None:
        """Return the credential only to later in-process orchestration.

        The caller must not persist, log, serialize, or pass this value through
        a subprocess environment.  Off mode deliberately returns ``None`` even
        when a configured key is retained for a later session toggle.
        """

        with self.__lock:
            if self.__mode is CollaborationMode.OFF:
                return None
            return self.__api_key


OPENAI_COLLABORATION_SESSION = OpenAICollaborationSession()


def openai_collaboration_status() -> CollaborationSessionStatus:
    return OPENAI_COLLABORATION_SESSION.status()


def configure_openai_collaboration(
    *,
    api_key: object = _UNSET,
    mode: object = _UNSET,
    model: object = _UNSET,
) -> CollaborationSessionStatus:
    return OPENAI_COLLABORATION_SESSION.configure(
        api_key=api_key,
        mode=mode,
        model=model,
    )


def clear_openai_collaboration() -> CollaborationSessionStatus:
    return OPENAI_COLLABORATION_SESSION.clear()


def openai_api_key_for_provider_call() -> str | None:
    return OPENAI_COLLABORATION_SESSION.api_key_for_provider_call()


__all__ = (
    "CollaborationSessionInputError",
    "CollaborationSessionStatus",
    "OPENAI_COLLABORATION_SESSION",
    "OpenAICollaborationSession",
    "clear_openai_collaboration",
    "configure_openai_collaboration",
    "openai_api_key_for_provider_call",
    "openai_collaboration_status",
)
