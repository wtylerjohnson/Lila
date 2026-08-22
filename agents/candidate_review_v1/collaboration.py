"""Strict, provider-neutral contracts for the optional adversarial collaborator.

This module is deliberately free of network, filesystem, and model calls.  It
creates the one sanitized request both model providers must receive, validates
their separately-bound artifacts, and compares them deterministically.  Model
disagreement is review material only: it cannot alter an Analyst Layer packet,
remove a candidate, or create a business disposition.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import html
import ipaddress
import json
import re
from typing import Iterable, Literal, Mapping, Optional, Union
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateKind,
    EvidenceRecord,
    LifecycleKind,
    assert_candidate_review_language,
)


COLLABORATION_REQUEST_SCHEMA_VERSION = (
    "candidate_review_v1.collaboration_request.v1"
)
COLLABORATION_OUTPUT_SCHEMA_VERSION = (
    "candidate_review_v1.collaboration_output.v1"
)
MAX_PUBLIC_MATERIALS = 32
MAX_EVIDENCE_ROWS = 200
MAX_INTAKE_GROUP_ITEMS = 100
MAX_PROVIDER_INPUT_BYTES = 768 * 1024
MAX_OUTPUT_SUGGESTIONS = 100
MAX_OUTPUT_HYPOTHESES = 100
MAX_CITATIONS_PER_HYPOTHESIS = 20
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_PROVIDER_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-]?)"
    r"\d{3}[ .-]?\d{4}(?!\d)"
)
_INTERNATIONAL_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])\+[1-9](?:[ .()/-]*\d){7,14}(?!\d)"
)
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_AWS_ACCESS_KEY_RE = re.compile(
    r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"
)
_COMMON_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"sk-[A-Za-z0-9._~-]{6,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"glpat-[A-Za-z0-9_-]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"xapp-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"ya29\.[A-Za-z0-9_-]{10,}|"
    r"npm_[A-Za-z0-9]{20,}|"
    r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}|"
    r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"
    r")"
)
_CREDENTIAL_CLAUSE_RE = re.compile(
    r"(?isx)"
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"AWS_ACCESS_KEY_ID|GOOGLE_APPLICATION_CREDENTIALS|authorization|"
    r"authentication|"
    r"(?:(?:[A-Za-z][A-Za-z0-9]*)[ _./-]+){0,4}"
    r"(?:key|token|secret|password|passwd|auth|credentials?)"
    r")"
    r"(?:[\s._/-]+(?:is|was|equals?|equal)(?=[\s._/-]|$)[\s._/-]*|"
    r"[\s._/-]*(?::|=>|=|->|→)[\s._/-]*)"
    r".*$"
)
_CREDENTIAL_MARKER_RE = re.compile(
    r"(?ix)"
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"(?:[A-Za-z][A-Za-z0-9]*[_-])+(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH)|"
    r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r")"
    r"(?![A-Za-z0-9])"
)
_CREDENTIAL_MARKER_CLAUSE_RE = re.compile(
    r"(?is)(?<![A-Za-z0-9])"
    r"(?:"
    r"(?:[A-Za-z][A-Za-z0-9]*[_-])+(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH)|"
    r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r")(?![A-Za-z0-9]).*$"
)
_SENSITIVE_FAILURE_RE = re.compile(
    r"(?i)(?:traceback|authorization\s*:|authentication\s*:|"
    r"credential(?:s)?\s*(?::|=|\bis\b))"
)
_CREDENTIAL_IDENTIFIER_RE = re.compile(
    r"(?ix)"
    r"(?:^|[^A-Za-z0-9])"
    r"(?:api[^A-Za-z0-9]*key|key|token|secret|password|passwd|auth|"
    r"authentication|authorization|credentials?)"
    r"(?:$|[^A-Za-z0-9])"
)
_OBFUSCATED_CREDENTIAL_IDENTIFIER_RE = re.compile(
    r"(?ix)"
    r"(?:^|[^A-Za-z0-9])"
    r"(?:api[ _./-]*key|client[ _./-]*secret|access[ _./-]*token|"
    r"password|passwd|credentials?)"
    r"(?:$|[^A-Za-z0-9])"
)
_URL_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(?:^|[^A-Za-z0-9])"
    r"(?:[a-z][a-z0-9]*[-_./]+){0,4}"
    r"(?:key|token|secret|password|passwd|auth|credentials?)"
    r"(?:[-_./]*(?:is|equals?)|[:=])[-_./]+[^/.\s]{2,}"
)
_URL_CREDENTIAL_MARKER_RE = re.compile(
    r"(?ix)(?:^|[./])"
    r"(?:[A-Za-z][A-Za-z0-9]*_)+(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH)"
    r"(?:$|[./:])"
)
_PRIVATE_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".corp",
    ".lan",
    ".home",
    ".test",
    ".invalid",
    ".localhost",
    ".example",
)
_LEGACY_NUMERIC_HOST_RE = re.compile(
    r"(?i)^(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)"
    r"(?:\.(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)){0,3}$"
)


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        strict=True,
        str_strip_whitespace=True,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _has_hidden_controls(value: object) -> bool:
    return any(
        not character.isspace()
        and unicodedata.category(character).startswith("C")
        for character in str(value or "")
    )


def _security_normalize(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    without_controls = "".join(
        " "
        if character.isspace()
        else ""
        if unicodedata.category(character).startswith("C")
        else character
        for character in normalized
    )
    return " ".join(without_controls.split())


def _security_detection_skeleton(value: object) -> tuple[str, bool]:
    """Remove combining marks only for privacy-shape detection.

    The returned value must never replace operator-visible prose.  It exists
    solely to catch credential/contact shapes split with combining marks.
    """

    decoded = str(value or "")
    unsafe_controls = False
    for _ in range(16):
        decoded_again = unquote(html.unescape(decoded))
        if _has_hidden_controls(decoded_again):
            unsafe_controls = True
        if decoded_again == decoded:
            break
        decoded = decoded_again
    else:
        # A value that still changes after the fixed decode budget is unsafe;
        # never pass a partially decoded credential/contact shape onward.
        unsafe_controls = True
    decomposed = unicodedata.normalize("NFKD", decoded)
    without_marks = "".join(
        character
        for character in decomposed
        if not unicodedata.category(character).startswith("M")
    )
    return (
        _security_normalize(unicodedata.normalize("NFKC", without_marks)),
        unsafe_controls,
    )


def _normalized_text(value: object) -> str:
    return " ".join(_security_normalize(value).split())


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def _matches_sensitive_shape(
    value: str,
    *,
    include_markers: bool,
) -> bool:
    patterns = (
        _EMAIL_RE,
        _PHONE_RE,
        _INTERNATIONAL_PHONE_RE,
        _PEM_PRIVATE_KEY_RE,
        _AWS_ACCESS_KEY_RE,
        _COMMON_TOKEN_RE,
        _CREDENTIAL_CLAUSE_RE,
        _CREDENTIAL_MARKER_CLAUSE_RE,
    )
    if _has_unicode_email_shape(value):
        return True
    if any(pattern.search(value) for pattern in patterns):
        return True
    return include_markers and _CREDENTIAL_MARKER_RE.search(value) is not None


def _has_unicode_email_shape(value: str) -> bool:
    """Detect non-ASCII email-like tokens in one linear tokenization pass."""

    if "@" not in value:
        return False
    for token in value.split():
        if "@" not in token:
            continue
        # An unbroken token this large is not useful public analysis and is
        # unsafe to feed to a permissive contact matcher.
        if len(token) > 512:
            return True
        if token.count("@") != 1:
            continue
        local, domain = token.split("@", 1)
        if local and "." in domain:
            before_dot, _separator, after_dot = domain.rpartition(".")
            if before_dot and after_dot:
                return True
    return False


def _skeleton_exposes_sensitive_shape(
    normalized: str,
    *,
    include_markers: bool,
) -> bool:
    skeleton, unsafe_controls = _security_detection_skeleton(normalized)
    if unsafe_controls:
        return True
    if skeleton == normalized:
        return False
    if _matches_sensitive_shape(skeleton, include_markers=include_markers):
        return True
    return (
        include_markers
        and _OBFUSCATED_CREDENTIAL_IDENTIFIER_RE.search(skeleton) is not None
        and _OBFUSCATED_CREDENTIAL_IDENTIFIER_RE.search(normalized) is None
    )


def _contains_sensitive_material(
    value: str,
    *,
    include_markers: bool = True,
) -> bool:
    if _has_hidden_controls(value):
        return True
    normalized = _security_normalize(value)
    return _matches_sensitive_shape(
        normalized,
        include_markers=include_markers,
    ) or _skeleton_exposes_sensitive_shape(
        normalized,
        include_markers=include_markers,
    )


def _scrub_text(value: object) -> str:
    if _has_hidden_controls(value):
        return "[redacted]"
    text = _normalized_text(value)
    if _skeleton_exposes_sensitive_shape(text, include_markers=True):
        # Combining-mark removal is detection-only, so offsets cannot be
        # mapped back safely.  Fail closed instead of risking a partial leak.
        return "[redacted]"
    for pattern in (
        _PEM_PRIVATE_KEY_RE,
        _CREDENTIAL_CLAUSE_RE,
        _CREDENTIAL_MARKER_CLAUSE_RE,
        _COMMON_TOKEN_RE,
        _AWS_ACCESS_KEY_RE,
        _CREDENTIAL_MARKER_RE,
        _EMAIL_RE,
        _PHONE_RE,
        _INTERNATIONAL_PHONE_RE,
    ):
        text = pattern.sub("[redacted]", text)
    if _has_unicode_email_shape(text):
        return "[redacted]"
    return text


def contains_sensitive_material(value: object) -> bool:
    """Shared privacy classifier for every collaboration boundary."""

    return _contains_sensitive_material(str(value or ""))


def redact_sensitive_material(value: object) -> str:
    """Shared fail-closed redactor for provider and persistence boundaries."""

    return _scrub_text(value)


def _assert_sanitized(value: str, label: str) -> None:
    if _contains_sensitive_material(value):
        raise ValueError(f"{label} contains contact or credential material")


def _assert_identifier_sanitized(value: str, label: str) -> None:
    normalized = _security_normalize(value)
    if (
        _has_hidden_controls(value)
        or _contains_sensitive_material(normalized)
        or _CREDENTIAL_IDENTIFIER_RE.search(normalized) is not None
    ):
        raise ValueError(f"{label} contains contact or credential material")


def _fully_unquote(value: str) -> str:
    decoded = _security_normalize(value)
    for _ in range(16):
        decoded_raw = unquote(decoded)
        if _has_hidden_controls(decoded_raw):
            raise ValueError("collaboration source URL contains hidden controls")
        decoded_again = _security_normalize(decoded_raw)
        if decoded_again == decoded:
            return decoded
        decoded = decoded_again
    raise ValueError("collaboration source URL is excessively encoded")


def _url_component_contains_sensitive_material(value: str) -> bool:
    value = _security_normalize(value)
    return (
        _contains_sensitive_material(value, include_markers=False)
        or _CREDENTIAL_IDENTIFIER_RE.search(value) is not None
        or _URL_CREDENTIAL_ASSIGNMENT_RE.search(value) is not None
        or _URL_CREDENTIAL_MARKER_RE.search(value) is not None
    )


def validate_provider_model(value: object) -> str:
    """Return a canonical provider model token or fail without echoing input."""

    if type(value) is not str:
        raise ValueError("provider model must be canonical text")
    normalized = _security_normalize(value).strip()
    if (
        _has_hidden_controls(value)
        or _PROVIDER_MODEL_RE.fullmatch(normalized) is None
        or _contains_sensitive_material(normalized)
        or _CREDENTIAL_IDENTIFIER_RE.search(normalized) is not None
    ):
        raise ValueError("provider model contains contact or credential material")
    return normalized


def _canonical_public_host(value: str) -> tuple[str, bool]:
    host = _fully_unquote(value).casefold().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if _LEGACY_NUMERIC_HOST_RE.fullmatch(host):
            raise ValueError(
                "collaboration source URL must use a public hostname"
            ) from None
        if "." not in host or host == "localhost":
            raise ValueError(
                "collaboration source URL must use a public hostname"
            ) from None
        if any(host.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES):
            raise ValueError(
                "collaboration source URL must use a public hostname"
            )
        return host, False
    if not address.is_global:
        raise ValueError("collaboration source URL must use a public IP address")
    return address.compressed, address.version == 6


def _safe_url(value: object) -> Optional[str]:
    if _has_hidden_controls(value):
        raise ValueError(
            "collaboration source URL contains contact or credential material"
        )
    raw = _normalized_text(value)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("collaboration source URL is malformed") from exc
    if parsed.scheme.casefold() != "https" or not hostname:
        raise ValueError("collaboration source URLs must use public HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("collaboration source URLs cannot contain user info")
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if authority.startswith("["):
        closing_bracket = authority.find("]")
        host_text = authority[:closing_bracket + 1]
        port_text = (
            authority[closing_bracket + 2:]
            if authority[closing_bracket + 1:].startswith(":")
            else ""
        )
    else:
        host_text, separator, port_text = authority.rpartition(":")
        if not separator:
            host_text, port_text = authority, ""
    decoded_host = _fully_unquote(host_text)
    decoded_port = _fully_unquote(port_text)
    canonical_host, host_is_ipv6 = _canonical_public_host(hostname)
    if (
        _url_component_contains_sensitive_material(decoded_host)
        or _url_component_contains_sensitive_material(decoded_port)
    ):
        raise ValueError(
            "collaboration source URL authority contains contact or credential material"
        )
    decoded_path = _fully_unquote(parsed.path)
    if _url_component_contains_sensitive_material(decoded_path):
        raise ValueError(
            "collaboration source URL path contains contact or credential material"
        )
    host = f"[{canonical_host}]" if host_is_ipv6 else canonical_host
    try:
        port_number = parsed.port
    except ValueError as exc:
        raise ValueError("collaboration source URL has an invalid port") from exc
    port = f":{port_number}" if port_number is not None else ""
    # Queries and fragments are never needed for model reasoning and are a
    # common credential/token leak surface.
    return urlunsplit(("https", host + port, parsed.path or "/", "", ""))


def _unique_strings(values: Iterable[object]) -> tuple[str, ...]:
    cleaned = {_scrub_text(value) for value in values}
    cleaned.discard("")
    return tuple(sorted(cleaned, key=lambda item: item.casefold()))


class CollaborationMode(str, Enum):
    OFF = "off"
    ADVISORY = "advisory"
    REQUIRED = "required"


class CollaborationLayer(str, Enum):
    INTAKE = "intake"
    INFERENCE = "inference"


class CollaboratorProvider(str, Enum):
    CLAUDE = "claude"
    OPENAI = "openai"


class CollaboratorState(str, Enum):
    RETURNED = "returned"
    FAILED = "failed"
    NOT_RUN = "not_run"
    CACHED = "cached"


class RunDisposition(str, Enum):
    OFF_CONTINUE = "off_continue"
    CONTINUE = "continue"
    CONTINUE_DEGRADED = "continue_degraded"
    PAUSED = "paused"


class IntakeSourceKind(str, Enum):
    OFFICIAL_WEBSITE = "official_website"
    PUBLIC_RESEARCH = "public_research"
    PUBLIC_DOCUMENT = "public_document"


class SuggestionKind(str, Enum):
    CAPABILITY = "capability"
    PROBLEM_TERM = "problem_term"
    KEYWORD = "keyword"
    NAICS = "naics"
    PSC = "psc"
    AGENCY = "agency"
    ENTITY = "entity"
    CONCEPT = "concept"
    ARGUMENT = "argument"


class SuggestionClassification(str, Enum):
    CORE = "core"
    BOUNDARY = "boundary"
    ADJACENT = "adjacent"
    EXCLUDE = "exclude"
    TARGET = "target"
    COMPETITOR = "competitor"
    INCUMBENT = "incumbent"
    NEAR_MISS = "near_miss"
    ASSUMPTION = "assumption"
    COUNTERARGUMENT = "counterargument"
    EVIDENCE_GAP = "evidence_gap"


class SanitizedPublicMaterial(_Contract):
    material_id: str = Field(min_length=1, max_length=128)
    kind: IntakeSourceKind
    source_url: str
    text: str = Field(min_length=1, max_length=50_000)

    @model_validator(mode="after")
    def _material_is_public_and_safe(self) -> "SanitizedPublicMaterial":
        _assert_identifier_sanitized(self.material_id, "material_id")
        if _TOKEN_RE.fullmatch(self.material_id) is None:
            raise ValueError("material_id must be a canonical token")
        if _safe_url(self.source_url) != self.source_url:
            raise ValueError("material source_url is not canonical")
        _assert_sanitized(self.text, "material text")
        return self


class SanitizedIntake(_Contract):
    """Positive allowlist of client input that either model may receive."""

    client_name: str = Field(min_length=1)
    website: Optional[str] = None
    primary_services: str = ""
    differentiators: str = ""
    past_performance: str = ""
    certifications: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_INTAKE_GROUP_ITEMS,
    )
    known_naics: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_INTAKE_GROUP_ITEMS,
    )
    target_agencies: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_INTAKE_GROUP_ITEMS,
    )
    geographic_focus: Optional[str] = None
    public_materials: tuple[SanitizedPublicMaterial, ...] = Field(
        default=(),
        max_length=MAX_PUBLIC_MATERIALS,
    )

    @model_validator(mode="after")
    def _intake_is_safe_and_canonical(self) -> "SanitizedIntake":
        if self.website is not None and _safe_url(self.website) != self.website:
            raise ValueError("intake website is not canonical")
        for label, value in (
            ("client_name", self.client_name),
            ("primary_services", self.primary_services),
            ("differentiators", self.differentiators),
            ("past_performance", self.past_performance),
            ("geographic_focus", self.geographic_focus or ""),
            *[("certification", item) for item in self.certifications],
            *[("target agency", item) for item in self.target_agencies],
        ):
            _assert_sanitized(value, label)
        if any(re.fullmatch(r"[0-9]{6}", code) is None for code in self.known_naics):
            raise ValueError("known NAICS codes must contain six digits")
        for label, rows in (
            ("certifications", self.certifications),
            ("known_naics", self.known_naics),
            ("target_agencies", self.target_agencies),
        ):
            if len(rows) != len(set(item.casefold() for item in rows)):
                raise ValueError(f"{label} must be unique")
        material_ids = tuple(row.material_id for row in self.public_materials)
        if material_ids != tuple(sorted(material_ids)):
            raise ValueError("public materials must be ordered by material_id")
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("public material IDs must be unique")
        return self

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset({"intake-form", *(row.material_id for row in self.public_materials)})


class SanitizedEvidence(_Contract):
    evidence_id: str = Field(min_length=1, max_length=256)
    source_url: str
    source_kind: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=2_000)
    excerpt: str = Field(min_length=1, max_length=50_000)
    official_source: bool = False
    primary_source: bool = False
    record_sha256: Optional[str] = None

    @model_validator(mode="after")
    def _evidence_is_safe(self) -> "SanitizedEvidence":
        if _safe_url(self.source_url) != self.source_url:
            raise ValueError("evidence source_url is not canonical")
        _assert_identifier_sanitized(self.evidence_id, "evidence_id")
        _assert_identifier_sanitized(self.source_kind, "source_kind")
        _assert_sanitized(self.title, "evidence title")
        _assert_sanitized(self.excerpt, "evidence excerpt")
        if self.source_kind.casefold() == "notice":
            host = urlsplit(self.source_url).hostname or ""
            if host.casefold() != "sam.gov" and not host.casefold().endswith(
                ".sam.gov"
            ):
                raise ValueError("notice evidence must resolve to SAM.gov")
            if not self.official_source or not self.primary_source:
                raise ValueError(
                    "notice evidence must be official primary-source evidence"
                )
        if self.record_sha256 is not None:
            _require_sha256(self.record_sha256, "evidence record_sha256")
        return self


def _public_material_payload(row: SanitizedPublicMaterial) -> dict:
    return {
        "material_id": row.material_id,
        "kind": row.kind.value,
        "source_url": row.source_url,
        "text": row.text,
    }


def _intake_payload(row: SanitizedIntake) -> dict:
    return {
        "client_name": row.client_name,
        "website": row.website,
        "primary_services": row.primary_services,
        "differentiators": row.differentiators,
        "past_performance": row.past_performance,
        "certifications": list(row.certifications),
        "known_naics": list(row.known_naics),
        "target_agencies": list(row.target_agencies),
        "geographic_focus": row.geographic_focus,
        "public_materials": [
            _public_material_payload(material)
            for material in row.public_materials
        ],
    }


def _evidence_payload(
    row: SanitizedEvidence,
    *,
    include_record_sha256: bool,
) -> dict:
    payload = {
        "evidence_id": row.evidence_id,
        "source_url": row.source_url,
        "source_kind": row.source_kind,
        "title": row.title,
        "excerpt": row.excerpt,
        "official_source": row.official_source,
        "primary_source": row.primary_source,
    }
    if include_record_sha256:
        payload["record_sha256"] = row.record_sha256
    return payload


def _require_exact_request_parts(
    sanitized_intake: SanitizedIntake,
    evidence_snapshot: tuple[SanitizedEvidence, ...],
) -> None:
    if type(sanitized_intake) is not SanitizedIntake:
        raise TypeError("collaboration request intake must use its exact contract")
    if any(
        type(row) is not SanitizedPublicMaterial
        for row in sanitized_intake.public_materials
    ):
        raise TypeError(
            "collaboration request public materials must use exact contracts"
        )
    if any(type(row) is not SanitizedEvidence for row in evidence_snapshot):
        raise TypeError(
            "collaboration request evidence must use exact contracts"
        )


def sanitize_intake(
    raw: Mapping[str, object],
    *,
    public_materials: Iterable[Mapping[str, object]] = (),
) -> SanitizedIntake:
    """Create the allowlisted intake; unknown/private fields are ignored."""

    materials: list[SanitizedPublicMaterial] = []
    for index, row in enumerate(public_materials):
        kind_value = row.get("kind", IntakeSourceKind.PUBLIC_RESEARCH.value)
        kind = kind_value if isinstance(kind_value, IntakeSourceKind) \
            else IntakeSourceKind(str(kind_value))
        url = _safe_url(row.get("source_url") or row.get("url"))
        if url is None:
            raise ValueError("public material requires a source URL")
        text = _scrub_text(row.get("text") or row.get("content"))
        if not text:
            continue
        source_id = _normalized_text(row.get("material_id"))
        if not source_id:
            material_digest = _sha256(
                (url + chr(0) + text).encode("utf-8")
            )[:20]
            source_id = f"material-{material_digest}"
        materials.append(SanitizedPublicMaterial(
            material_id=source_id,
            kind=kind,
            source_url=url,
            text=text,
        ))
    materials.sort(key=lambda row: row.material_id)
    return SanitizedIntake(
        client_name=_scrub_text(raw.get("client_name")),
        website=_safe_url(raw.get("website")),
        primary_services=_scrub_text(raw.get("primary_services")),
        differentiators=_scrub_text(raw.get("differentiators")),
        past_performance=_scrub_text(raw.get("past_performance")),
        certifications=_unique_strings(raw.get("certifications") or ()),
        known_naics=_unique_strings(raw.get("known_naics") or ()),
        target_agencies=_unique_strings(raw.get("target_agencies") or ()),
        geographic_focus=(
            _scrub_text(raw.get("geographic_focus")) or None
        ),
        public_materials=tuple(materials),
    )


def sanitize_evidence_snapshot(
    rows: Iterable[object],
) -> tuple[SanitizedEvidence, ...]:
    """Project evidence through a contact/credential-free positive allowlist."""

    result: list[SanitizedEvidence] = []
    for raw in rows:
        if type(raw) is EvidenceRecord:
            try:
                raw_payload = BaseModel.model_dump(
                    raw,
                    mode="python",
                    exclude_none=False,
                )
                validated_record = EvidenceRecord.model_validate(raw_payload)
            except (TypeError, ValueError):
                raise ValueError(
                    "evidence record failed its exact trusted contract"
                ) from None
            row = BaseModel.model_dump(
                validated_record,
                mode="python",
                exclude_none=False,
            )
            trusted_record = True
        elif isinstance(raw, EvidenceRecord):
            raise TypeError(
                "evidence snapshot rejects EvidenceRecord subclasses"
            )
        elif isinstance(raw, BaseModel):
            raise TypeError(
                "evidence snapshot model rows must be exact EvidenceRecord"
            )
        elif isinstance(raw, Mapping):
            row = raw
            trusted_record = False
        else:
            raise TypeError("evidence snapshot rows must be models or mappings")
        url = _safe_url(row.get("source_url"))
        if url is None:
            raise ValueError("evidence snapshot row requires a source URL")
        kind = row.get("source_kind")
        kind_value = getattr(kind, "value", kind)
        trust_flags: dict[str, bool] = {}
        for flag in ("official_source", "primary_source"):
            flag_value = row.get(flag, False)
            if type(flag_value) is not bool:
                raise ValueError(f"{flag} must be an exact boolean")
            trust_flags[flag] = flag_value if trusted_record else False
        result.append(SanitizedEvidence(
            evidence_id=_normalized_text(row.get("evidence_id")),
            source_url=url,
            source_kind=_normalized_text(kind_value),
            title=_scrub_text(row.get("title")),
            excerpt=_scrub_text(row.get("excerpt")),
            official_source=trust_flags["official_source"],
            primary_source=trust_flags["primary_source"],
            record_sha256=(
                _normalized_text(row.get("record_sha256")) or None
            ),
        ))
    result.sort(key=lambda row: row.evidence_id)
    identifiers = tuple(row.evidence_id for row in result)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("sanitized evidence IDs must be unique")
    return tuple(result)


class CollaborationRequest(_Contract):
    """The single canonical byte payload passed unchanged to both lanes."""

    schema_version: Literal[
        "candidate_review_v1.collaboration_request.v1"
    ] = COLLABORATION_REQUEST_SCHEMA_VERSION
    binding: ArtifactBinding
    layer: CollaborationLayer
    sanitized_intake: SanitizedIntake
    evidence_snapshot: tuple[SanitizedEvidence, ...] = Field(
        default=(),
        max_length=MAX_EVIDENCE_ROWS,
    )
    prompt_version: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=128)
    request_sha256: str

    def canonical_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "layer": self.layer.value,
            "sanitized_intake": _intake_payload(self.sanitized_intake),
            "evidence_snapshot": [
                _evidence_payload(row, include_record_sha256=False)
                for row in self.evidence_snapshot
            ],
            "prompt_version": self.prompt_version,
            "output_schema_version": self.output_schema_version,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload())

    @property
    def canonical_bytes(self) -> bytes:
        return self.canonical_json().encode("utf-8")

    @model_validator(mode="after")
    def _request_is_exact(self) -> "CollaborationRequest":
        if type(self) is not CollaborationRequest:
            raise TypeError("collaboration request must use its exact contract")
        if type(self.binding) is not ArtifactBinding:
            raise TypeError("collaboration request binding must use its exact contract")
        _require_exact_request_parts(
            self.sanitized_intake,
            self.evidence_snapshot,
        )
        _require_sha256(self.request_sha256, "request_sha256")
        if self.sanitized_intake.client_name != self.binding.client_name:
            raise ValueError("sanitized intake belongs to another client")
        if self.layer is CollaborationLayer.INTAKE and self.evidence_snapshot:
            raise ValueError("intake challenge cannot receive inference evidence")
        if self.layer is CollaborationLayer.INFERENCE and not self.evidence_snapshot:
            raise ValueError("inference challenge requires an evidence snapshot")
        evidence_ids = tuple(row.evidence_id for row in self.evidence_snapshot)
        if evidence_ids != tuple(sorted(evidence_ids)):
            raise ValueError("request evidence must be ordered by evidence_id")
        for label, value in (
            ("prompt_version", self.prompt_version),
            ("output_schema_version", self.output_schema_version),
        ):
            _assert_identifier_sanitized(value, label)
            if _TOKEN_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be a canonical token")
        expected = _sha256(self.canonical_json().encode("utf-8"))
        if len(self.canonical_bytes) > MAX_PROVIDER_INPUT_BYTES:
            raise ValueError("collaboration request exceeds its provider byte budget")
        if self.request_sha256 != expected:
            raise ValueError("request_sha256 does not match canonical request bytes")
        return self

    @classmethod
    def create(
        cls,
        *,
        binding: ArtifactBinding,
        layer: CollaborationLayer,
        sanitized_intake: SanitizedIntake,
        evidence_snapshot: tuple[SanitizedEvidence, ...] = (),
        prompt_version: str = "candidate-review-collaboration-v1",
        output_schema_version: str = COLLABORATION_OUTPUT_SCHEMA_VERSION,
    ) -> "CollaborationRequest":
        if type(binding) is not ArtifactBinding:
            raise TypeError("collaboration request binding must use its exact contract")
        _require_exact_request_parts(sanitized_intake, evidence_snapshot)
        values = dict(
            binding=binding,
            layer=layer,
            sanitized_intake=sanitized_intake,
            evidence_snapshot=evidence_snapshot,
            prompt_version=prompt_version,
            output_schema_version=output_schema_version,
        )
        provisional = {
            "schema_version": COLLABORATION_REQUEST_SCHEMA_VERSION,
            "layer": layer.value,
            "sanitized_intake": _intake_payload(sanitized_intake),
            "evidence_snapshot": [
                _evidence_payload(row, include_record_sha256=False)
                for row in evidence_snapshot
            ],
            "prompt_version": prompt_version,
            "output_schema_version": output_schema_version,
        }
        return cls(
            **values,
            request_sha256=_sha256(_canonical_json(provisional).encode("utf-8")),
        )


def validate_collaboration_request(value: object) -> CollaborationRequest:
    """Rebuild one exact request without invoking dynamic serializers."""

    if type(value) is not CollaborationRequest:
        raise TypeError("provider requires an exact collaboration request")
    request = value
    _require_exact_request_parts(
        request.sanitized_intake,
        request.evidence_snapshot,
    )
    if type(request.binding) is not ArtifactBinding:
        raise TypeError("collaboration request binding must use its exact contract")
    binding = ArtifactBinding(
        client_id=request.binding.client_id,
        client_name=request.binding.client_name,
        run_id=request.binding.run_id,
        scope_designator=request.binding.scope_designator,
        scope_sha256=request.binding.scope_sha256,
        profile_sha256=request.binding.profile_sha256,
        evidence_snapshot_sha256=request.binding.evidence_snapshot_sha256,
    )
    materials = tuple(
        SanitizedPublicMaterial(**_public_material_payload(row))
        for row in request.sanitized_intake.public_materials
    )
    intake = SanitizedIntake(
        client_name=request.sanitized_intake.client_name,
        website=request.sanitized_intake.website,
        primary_services=request.sanitized_intake.primary_services,
        differentiators=request.sanitized_intake.differentiators,
        past_performance=request.sanitized_intake.past_performance,
        certifications=request.sanitized_intake.certifications,
        known_naics=request.sanitized_intake.known_naics,
        target_agencies=request.sanitized_intake.target_agencies,
        geographic_focus=request.sanitized_intake.geographic_focus,
        public_materials=materials,
    )
    evidence = tuple(
        SanitizedEvidence(
            **_evidence_payload(row, include_record_sha256=True)
        )
        for row in request.evidence_snapshot
    )
    return CollaborationRequest(
        schema_version=request.schema_version,
        binding=binding,
        layer=request.layer,
        sanitized_intake=intake,
        evidence_snapshot=evidence,
        prompt_version=request.prompt_version,
        output_schema_version=request.output_schema_version,
        request_sha256=request.request_sha256,
    )


class IntakeSuggestion(_Contract):
    kind: SuggestionKind
    classification: SuggestionClassification
    value: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=4_000)
    source_refs: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_INTAKE_GROUP_ITEMS,
    )

    @model_validator(mode="after")
    def _suggestion_is_non_dispositive(self) -> "IntakeSuggestion":
        _assert_sanitized(self.value, "suggestion value")
        _assert_sanitized(self.rationale, "suggestion rationale")
        for source_ref in self.source_refs:
            _assert_sanitized(source_ref, "suggestion source reference")
        assert_candidate_review_language(self.value, self.rationale)
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("suggestion source refs must be unique")
        if self.kind is SuggestionKind.NAICS \
                and re.fullmatch(r"[0-9]{6}", self.value) is None:
            raise ValueError("NAICS suggestion must contain six digits")
        if self.kind is SuggestionKind.PSC \
                and re.fullmatch(r"[A-Z0-9]{4}", self.value.upper()) is None:
            raise ValueError("PSC suggestion must contain four characters")
        allowed = {
            SuggestionKind.AGENCY: {SuggestionClassification.TARGET},
            SuggestionKind.ENTITY: {
                SuggestionClassification.COMPETITOR,
                SuggestionClassification.INCUMBENT,
            },
            SuggestionKind.ARGUMENT: {
                SuggestionClassification.ASSUMPTION,
                SuggestionClassification.COUNTERARGUMENT,
                SuggestionClassification.EVIDENCE_GAP,
            },
        }
        if self.kind in allowed and self.classification not in allowed[self.kind]:
            raise ValueError("suggestion classification does not match its kind")
        if self.classification not in {
            SuggestionClassification.ASSUMPTION,
            SuggestionClassification.EVIDENCE_GAP,
        } and not self.source_refs:
            raise ValueError("supported suggestion requires a source reference")
        return self

    @property
    def comparison_key(self) -> str:
        namespace = {
            SuggestionKind.NAICS: "naics",
            SuggestionKind.PSC: "psc",
            SuggestionKind.AGENCY: "agency",
            SuggestionKind.ENTITY: "entity",
            SuggestionKind.ARGUMENT: "argument",
        }.get(self.kind, "term")
        return f"{namespace}:{_comparison_text(self.value)}"


class IntakeChallengeOutput(_Contract):
    layer: Literal[CollaborationLayer.INTAKE] = CollaborationLayer.INTAKE
    suggestions: tuple[IntakeSuggestion, ...] = Field(
        default=(),
        max_length=MAX_OUTPUT_SUGGESTIONS,
    )

    @model_validator(mode="after")
    def _suggestions_are_unique(self) -> "IntakeChallengeOutput":
        keys = tuple(row.comparison_key for row in self.suggestions)
        if len(keys) != len(set(keys)):
            raise ValueError("intake output contains duplicate suggestions")
        return self


class EvidenceCitation(_Contract):
    evidence_id: str = Field(min_length=1, max_length=256)
    source_url: str
    quote: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def _citation_is_safe(self) -> "EvidenceCitation":
        if _safe_url(self.source_url) != self.source_url:
            raise ValueError("citation source_url is not canonical")
        _assert_sanitized(self.evidence_id, "citation evidence ID")
        _assert_sanitized(self.quote, "citation quote")
        return self


class ChallengerHypothesis(_Contract):
    kind: CandidateKind
    lifecycle: LifecycleKind
    title: str = Field(min_length=1, max_length=1_000)
    agency_key: str = Field(min_length=1, max_length=500)
    buyer_key: str = Field(min_length=1, max_length=500)
    program_key: str = Field(min_length=1, max_length=500)
    access_route_key: str = Field(min_length=1, max_length=500)
    anchor_evidence_id: str = Field(min_length=1, max_length=256)
    citations: tuple[EvidenceCitation, ...] = Field(
        min_length=1,
        max_length=MAX_CITATIONS_PER_HYPOTHESIS,
    )
    inference_chain: str = Field(min_length=1, max_length=8_000)
    counterevidence: str = Field(min_length=1, max_length=4_000)
    falsifier: str = Field(min_length=1, max_length=4_000)
    trigger: str = Field(min_length=1, max_length=4_000)
    validate_next: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def _hypothesis_is_non_dispositive(self) -> "ChallengerHypothesis":
        values = (
            self.title,
            self.agency_key,
            self.buyer_key,
            self.program_key,
            self.access_route_key,
            self.inference_chain,
            self.counterevidence,
            self.falsifier,
            self.trigger,
            self.validate_next,
        )
        for value in values:
            _assert_sanitized(value, "hypothesis text")
        _assert_sanitized(self.anchor_evidence_id, "hypothesis anchor evidence ID")
        assert_candidate_review_language(*values)
        citation_ids = tuple(row.evidence_id for row in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("hypothesis evidence citations must be unique")
        if self.anchor_evidence_id not in citation_ids:
            raise ValueError("hypothesis anchor must be one of its citations")
        notice_lifecycles = {
            LifecycleKind.MARKET_RESEARCH,
            LifecycleKind.PRESOLICITATION,
            LifecycleKind.LIVE_SOLICITATION,
        }
        if self.kind is CandidateKind.CURRENT_NOTICE \
                and self.lifecycle not in notice_lifecycles:
            raise ValueError("current-notice hypothesis uses a non-notice lifecycle")
        if self.kind is CandidateKind.RESEARCH_CORRIDOR \
                and self.lifecycle in notice_lifecycles:
            raise ValueError("corridor hypothesis claims a notice lifecycle")
        return self

    @property
    def comparison_key(self) -> str:
        parts = (
            self.agency_key,
            self.buyer_key,
            self.program_key,
            self.access_route_key,
            self.lifecycle.value,
        )
        return "hypothesis:" + "|".join(_comparison_text(item) for item in parts)


class InferenceChallengeOutput(_Contract):
    layer: Literal[CollaborationLayer.INFERENCE] = CollaborationLayer.INFERENCE
    hypotheses: tuple[ChallengerHypothesis, ...] = Field(
        default=(),
        max_length=MAX_OUTPUT_HYPOTHESES,
    )

    @model_validator(mode="after")
    def _hypotheses_are_unique(self) -> "InferenceChallengeOutput":
        keys = tuple(row.comparison_key for row in self.hypotheses)
        if len(keys) != len(set(keys)):
            raise ValueError("inference output contains duplicate hypotheses")
        return self


ChallengeOutput = Union[IntakeChallengeOutput, InferenceChallengeOutput]


def challenge_output_model(
    layer: CollaborationLayer,
) -> type[IntakeChallengeOutput] | type[InferenceChallengeOutput]:
    return (
        IntakeChallengeOutput
        if layer is CollaborationLayer.INTAKE
        else InferenceChallengeOutput
    )


class TokenUsage(_Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _total_closes(self) -> "TokenUsage":
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total token usage does not reconcile")
        return self


class ProviderCallResult(_Contract):
    """Separately persisted, credential-free output for exactly one lane."""

    provider: CollaboratorProvider
    state: CollaboratorState
    model: str = Field(min_length=1, max_length=128)
    prompt_sha256: str
    schema_sha256: str
    input_sha256: str
    output: Optional[ChallengeOutput] = None
    attempts: int = Field(ge=0, le=2)
    token_usage: Optional[TokenUsage] = None
    public_failure: Optional[str] = Field(default=None, max_length=500)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    response_id: Optional[str] = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _artifact_matches_state(self) -> "ProviderCallResult":
        for label, value in (
            ("prompt_sha256", self.prompt_sha256),
            ("schema_sha256", self.schema_sha256),
            ("input_sha256", self.input_sha256),
        ):
            _require_sha256(value, label)
        for label, value in (
            ("started_at", self.started_at),
            ("completed_at", self.completed_at),
        ):
            if value is not None:
                _require_aware(value, label)
        if self.started_at and self.completed_at \
                and self.completed_at < self.started_at:
            raise ValueError("provider completion predates its start")
        if validate_provider_model(self.model) != self.model:
            raise ValueError("provider model must be canonical")
        if self.public_failure and (
            _SENSITIVE_FAILURE_RE.search(self.public_failure)
            or _contains_sensitive_material(self.public_failure)
        ):
            raise ValueError("public failure contains sensitive internals")
        if self.response_id and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]*", self.response_id
        ):
            raise ValueError("response_id is not a safe opaque identifier")
        if self.response_id:
            _assert_sanitized(self.response_id, "response_id")
        if self.state is CollaboratorState.NOT_RUN:
            if self.attempts or self.output is not None or self.public_failure:
                raise ValueError("not-run provider cannot carry attempts or output")
            if self.token_usage or self.started_at or self.completed_at or self.response_id:
                raise ValueError("not-run provider cannot carry response metadata")
        elif self.state is CollaboratorState.FAILED:
            if not 1 <= self.attempts <= 2 or self.output is not None:
                raise ValueError("failed provider requires attempts and no output")
            if not self.public_failure or self.completed_at is None:
                raise ValueError("failed provider requires a safe public failure")
        elif self.state is CollaboratorState.RETURNED:
            if not 1 <= self.attempts <= 2 or self.output is None:
                raise ValueError("returned provider requires attempts and output")
            if self.public_failure or self.completed_at is None:
                raise ValueError("returned provider cannot carry a failure")
        elif self.state is CollaboratorState.CACHED:
            if self.attempts or self.output is None or self.public_failure:
                raise ValueError("cached provider requires output and zero attempts")
        return self


class ComparisonItem(_Contract):
    item_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    claude_classification: Optional[str] = None
    openai_classification: Optional[str] = None

    @model_validator(mode="after")
    def _comparison_item_is_safe(self) -> "ComparisonItem":
        for value in (
            self.item_key,
            self.label,
            self.claude_classification or "",
            self.openai_classification or "",
        ):
            _assert_sanitized(value, "comparison item")
        return self


class AnalystQuestion(_Contract):
    question_id: str = Field(min_length=1)
    item_key: str = Field(min_length=1)
    question: str = Field(min_length=1)

    @model_validator(mode="after")
    def _question_is_not_a_disposition(self) -> "AnalystQuestion":
        for value in (self.question_id, self.item_key, self.question):
            _assert_sanitized(value, "analyst question")
        assert_candidate_review_language(self.question)
        return self


class CollaborationComparison(_Contract):
    request_sha256: str
    layer: CollaborationLayer
    agreements: tuple[ComparisonItem, ...] = ()
    claude_only: tuple[ComparisonItem, ...] = ()
    openai_only: tuple[ComparisonItem, ...] = ()
    classification_conflicts: tuple[ComparisonItem, ...] = ()
    unsupported_assumptions: tuple[ComparisonItem, ...] = ()
    analyst_questions: tuple[AnalystQuestion, ...] = ()
    surfaced_openai_hypotheses: tuple[ChallengerHypothesis, ...] = ()

    @model_validator(mode="after")
    def _comparison_is_ordered(self) -> "CollaborationComparison":
        _require_sha256(self.request_sha256, "comparison request_sha256")
        for label, rows in (
            ("agreements", self.agreements),
            ("claude_only", self.claude_only),
            ("openai_only", self.openai_only),
            ("classification_conflicts", self.classification_conflicts),
            ("unsupported_assumptions", self.unsupported_assumptions),
        ):
            keys = tuple(row.item_key for row in rows)
            if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
                raise ValueError(f"comparison {label} must be ordered and unique")
        question_ids = tuple(row.question_id for row in self.analyst_questions)
        if question_ids != tuple(sorted(question_ids)):
            raise ValueError("analyst questions must be ordered")
        if self.layer is CollaborationLayer.INTAKE \
                and self.surfaced_openai_hypotheses:
            raise ValueError("intake comparison cannot surface hypotheses")
        return self


def _artifact_output(
    request: CollaborationRequest,
    artifact: ProviderCallResult,
    provider: CollaboratorProvider,
) -> Optional[ChallengeOutput]:
    if artifact.provider is not provider:
        raise ValueError("provider artifact is in the wrong lane")
    if artifact.input_sha256 != request.request_sha256:
        raise ValueError("provider artifact is bound to another request")
    output = artifact.output
    if output is not None and output.layer != request.layer:
        raise ValueError("provider output belongs to another challenge layer")
    return output


def _quote_is_substantive(value: str) -> bool:
    """Reject generic token matches while retaining specific short terms.

    A single normalized token needs seven characters, which admits terms such
    as ``FedRAMP`` and ``workflow`` but rejects glue words such as ``the``.
    Multi-token spans need at least eight alphanumeric characters in total.
    Exact support is still checked against the normalized source text below.
    """

    tokens = _comparison_text(value).split()
    if len(tokens) == 1:
        return len(tokens[0]) >= 7
    return len(tokens) >= 2 and sum(len(token) for token in tokens) >= 8


def _hypothesis_has_valid_evidence(
    request: CollaborationRequest,
    hypothesis: ChallengerHypothesis,
) -> bool:
    evidence = {row.evidence_id: row for row in request.evidence_snapshot}
    anchor = evidence.get(hypothesis.anchor_evidence_id)
    if anchor is None:
        return False
    if hypothesis.kind is CandidateKind.CURRENT_NOTICE and not (
        anchor.official_source
        and anchor.primary_source
        and anchor.source_kind.casefold() == "notice"
    ):
        return False
    for citation in hypothesis.citations:
        source = evidence.get(citation.evidence_id)
        if source is None or citation.source_url != source.source_url:
            return False
        quote = _comparison_text(citation.quote)
        if not quote or not _quote_is_substantive(citation.quote):
            return False
        exact_span = f" {quote} "
        if not any(
            exact_span in f" {_comparison_text(source_text)} "
            for source_text in (source.title, source.excerpt)
        ):
            return False
    return True


def _question(item: ComparisonItem, reason: str) -> AnalystQuestion:
    digest = _sha256((reason + "\0" + item.item_key).encode())[:20]
    return AnalystQuestion(
        question_id=f"question-{digest}",
        item_key=item.item_key,
        question=f"Analyst review: {reason} — {item.label}.",
    )


def compare_collaboration(
    request: CollaborationRequest,
    claude: ProviderCallResult,
    openai: ProviderCallResult,
) -> CollaborationComparison:
    """Compare exact normalized keys; never use fuzzy or model arbitration."""

    claude_output = _artifact_output(
        request, claude, CollaboratorProvider.CLAUDE)
    openai_output = _artifact_output(
        request, openai, CollaboratorProvider.OPENAI)

    labels: dict[str, str] = {}
    classes: dict[CollaboratorProvider, dict[str, str]] = {
        CollaboratorProvider.CLAUDE: {},
        CollaboratorProvider.OPENAI: {},
    }
    invalid: dict[CollaboratorProvider, set[str]] = {
        CollaboratorProvider.CLAUDE: set(),
        CollaboratorProvider.OPENAI: set(),
    }
    openai_hypotheses: dict[str, ChallengerHypothesis] = {}

    def add_intake(provider: CollaboratorProvider, output: ChallengeOutput) -> None:
        if not isinstance(output, IntakeChallengeOutput):
            return
        for row in output.suggestions:
            key = row.comparison_key
            labels.setdefault(key, row.value)
            classes[provider][key] = row.classification.value
            if row.classification is SuggestionClassification.ASSUMPTION \
                    or not set(row.source_refs) <= request.sanitized_intake.source_ids:
                invalid[provider].add(key)

    def add_inference(provider: CollaboratorProvider, output: ChallengeOutput) -> None:
        if not isinstance(output, InferenceChallengeOutput):
            return
        for row in output.hypotheses:
            key = row.comparison_key
            labels.setdefault(key, row.title)
            classes[provider][key] = row.kind.value
            if not _hypothesis_has_valid_evidence(request, row):
                invalid[provider].add(key)
            if provider is CollaboratorProvider.OPENAI:
                openai_hypotheses[key] = row

    if claude_output is not None:
        (add_intake if request.layer is CollaborationLayer.INTAKE else add_inference)(
            CollaboratorProvider.CLAUDE, claude_output)
    if openai_output is not None:
        (add_intake if request.layer is CollaborationLayer.INTAKE else add_inference)(
            CollaboratorProvider.OPENAI, openai_output)

    ckeys, okeys = set(classes[CollaboratorProvider.CLAUDE]), set(
        classes[CollaboratorProvider.OPENAI])

    def item(key: str) -> ComparisonItem:
        return ComparisonItem(
            item_key=key,
            label=labels[key],
            claude_classification=classes[CollaboratorProvider.CLAUDE].get(key),
            openai_classification=classes[CollaboratorProvider.OPENAI].get(key),
        )

    agreements = tuple(item(key) for key in sorted(
        key for key in ckeys & okeys
        if classes[CollaboratorProvider.CLAUDE][key]
        == classes[CollaboratorProvider.OPENAI][key]
    ))
    conflicts = tuple(item(key) for key in sorted(
        key for key in ckeys & okeys
        if classes[CollaboratorProvider.CLAUDE][key]
        != classes[CollaboratorProvider.OPENAI][key]
    ))
    claude_only = tuple(item(key) for key in sorted(ckeys - okeys))
    openai_only = tuple(item(key) for key in sorted(okeys - ckeys))
    unsupported = tuple(item(key) for key in sorted(
        invalid[CollaboratorProvider.CLAUDE]
        | invalid[CollaboratorProvider.OPENAI]
    ))

    questions: dict[str, AnalystQuestion] = {}
    for row in conflicts:
        q = _question(row, "resolve the classification difference")
        questions[q.question_id] = q
    for row in claude_only:
        q = _question(row, "consider the Claude-only recommendation")
        questions[q.question_id] = q
    for row in openai_only:
        q = _question(row, "consider the OpenAI-only recommendation")
        questions[q.question_id] = q
    for row in unsupported:
        q = _question(row, "validate the cited support before any change")
        questions[q.question_id] = q

    surfaced = ()
    if request.layer is CollaborationLayer.INFERENCE:
        surfaced = tuple(
            openai_hypotheses[key]
            for key in sorted(okeys - ckeys)
            if key not in invalid[CollaboratorProvider.OPENAI]
        )
    return CollaborationComparison(
        request_sha256=request.request_sha256,
        layer=request.layer,
        agreements=agreements,
        claude_only=claude_only,
        openai_only=openai_only,
        classification_conflicts=conflicts,
        unsupported_assumptions=unsupported,
        analyst_questions=tuple(
            questions[key] for key in sorted(questions)
        ),
        surfaced_openai_hypotheses=surfaced,
    )


class CollaborationRun(_Contract):
    request: CollaborationRequest
    mode: CollaborationMode
    claude: ProviderCallResult
    openai: ProviderCallResult
    comparison: CollaborationComparison
    disposition: RunDisposition
    analyst_packet_mutated: Literal[False] = False

    @model_validator(mode="after")
    def _run_semantics_are_exact(self) -> "CollaborationRun":
        expected_comparison = compare_collaboration(
            self.request, self.claude, self.openai)
        if self.comparison != expected_comparison:
            raise ValueError("comparison is not the deterministic provider comparison")
        good = {CollaboratorState.RETURNED, CollaboratorState.CACHED}
        if self.mode is CollaborationMode.OFF \
                and self.openai.state is not CollaboratorState.NOT_RUN:
            raise ValueError("off mode cannot execute OpenAI")
        if self.claude.state not in good:
            expected = RunDisposition.PAUSED
        elif self.mode is CollaborationMode.OFF:
            expected = RunDisposition.OFF_CONTINUE
        elif self.openai.state in good:
            expected = RunDisposition.CONTINUE
        elif self.mode is CollaborationMode.ADVISORY:
            expected = RunDisposition.CONTINUE_DEGRADED
        else:
            expected = RunDisposition.PAUSED
        if self.disposition is not expected:
            raise ValueError("run disposition does not match collaboration mode")
        return self

    @classmethod
    def create(
        cls,
        *,
        request: CollaborationRequest,
        mode: CollaborationMode,
        claude: ProviderCallResult,
        openai: ProviderCallResult,
    ) -> "CollaborationRun":
        good = {CollaboratorState.RETURNED, CollaboratorState.CACHED}
        if claude.state not in good:
            disposition = RunDisposition.PAUSED
        elif mode is CollaborationMode.OFF:
            disposition = RunDisposition.OFF_CONTINUE
        elif openai.state in good:
            disposition = RunDisposition.CONTINUE
        elif mode is CollaborationMode.ADVISORY:
            disposition = RunDisposition.CONTINUE_DEGRADED
        else:
            disposition = RunDisposition.PAUSED
        return cls(
            request=request,
            mode=mode,
            claude=claude,
            openai=openai,
            comparison=compare_collaboration(request, claude, openai),
            disposition=disposition,
        )


__all__ = (
    "COLLABORATION_OUTPUT_SCHEMA_VERSION",
    "COLLABORATION_REQUEST_SCHEMA_VERSION",
    "MAX_CITATIONS_PER_HYPOTHESIS",
    "MAX_EVIDENCE_ROWS",
    "MAX_INTAKE_GROUP_ITEMS",
    "MAX_OUTPUT_HYPOTHESES",
    "MAX_OUTPUT_SUGGESTIONS",
    "MAX_PROVIDER_INPUT_BYTES",
    "MAX_PUBLIC_MATERIALS",
    "AnalystQuestion",
    "ChallengeOutput",
    "ChallengerHypothesis",
    "CollaborationComparison",
    "CollaborationLayer",
    "CollaborationMode",
    "CollaborationRequest",
    "CollaborationRun",
    "CollaboratorProvider",
    "CollaboratorState",
    "ComparisonItem",
    "EvidenceCitation",
    "InferenceChallengeOutput",
    "IntakeChallengeOutput",
    "IntakeSourceKind",
    "IntakeSuggestion",
    "ProviderCallResult",
    "RunDisposition",
    "SanitizedEvidence",
    "SanitizedIntake",
    "SanitizedPublicMaterial",
    "SuggestionClassification",
    "SuggestionKind",
    "TokenUsage",
    "challenge_output_model",
    "compare_collaboration",
    "contains_sensitive_material",
    "redact_sensitive_material",
    "sanitize_evidence_snapshot",
    "sanitize_intake",
    "validate_collaboration_request",
    "validate_provider_model",
)
