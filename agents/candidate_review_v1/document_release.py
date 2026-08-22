"""Candidate Review v1 release certifier (Chunk 6, Pass 3).

Binds an exact rendered artifact to the exact composed document.  The
certificate is a versioned, exact-field JSON envelope (the pattern established
by ``agents/reports/signal_board_release.py``), hash-bound to BOTH the
document's ``baseline_document_sha256`` and the rendered HTML bytes, so no
consumer can treat a stale or mismatched render as releasable.

DRAFT is the resting state.  RELEASE is an explicit caller decision, and the
draft artifact is written under a distinct filename so a draft can never be
mistaken for a client-final file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from agents.candidate_review_v1.composer import COMPOSER_SCHEMA_VERSION
from agents.candidate_review_v1.contracts import (
    SECTION_ORDER,
    CandidateReviewDocument,
)
from agents.candidate_review_v1.renderer import RENDERER_VERSION

CERTIFICATE_SCHEMA_VERSION = 1

CERTIFICATE_REQUIRED_FIELDS = frozenset({
    "schema_version", "client_id", "client_name", "document_id",
    "baseline_document_sha256", "html_sha256", "renderer_version",
    "composer_schema_version", "as_of", "certified_at", "state",
    "release_eligible", "section_ids", "candidate_count", "evidence_count",
})

STATE_DRAFT = "DRAFT"
STATE_RELEASE = "RELEASE"


class ReleaseError(ValueError):
    """The artifact does not bind to the document, or the envelope is invalid."""


@dataclass(frozen=True)
class CandidateReviewCertificate:
    payload: dict[str, Any]

    @property
    def state(self) -> str:
        return str(self.payload["state"])

    @property
    def release_eligible(self) -> bool:
        return bool(self.payload["release_eligible"])

    @property
    def html_sha256(self) -> str:
        return str(self.payload["html_sha256"])


def validate_certificate_schema(payload: Any) -> None:
    """Validate the exact, versioned Candidate Review QA envelope."""

    if not isinstance(payload, dict):
        raise ReleaseError("certificate root is not an object")
    keys = set(payload)
    if keys != CERTIFICATE_REQUIRED_FIELDS:
        raise ReleaseError("certificate fields are not exact")
    if payload["schema_version"] != CERTIFICATE_SCHEMA_VERSION:
        raise ReleaseError("certificate schema is unsupported")
    for name in ("client_id", "client_name", "document_id", "renderer_version",
                 "composer_schema_version", "as_of", "certified_at", "state"):
        if not isinstance(payload.get(name), str) or not payload[name]:
            raise ReleaseError(f"certificate {name} is incomplete")
    for name in ("baseline_document_sha256", "html_sha256"):
        digest = payload.get(name)
        if (not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise ReleaseError(f"certificate {name} is not a lowercase SHA-256")
    for name in ("as_of", "certified_at"):
        try:
            stamp = datetime.fromisoformat(str(payload[name]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReleaseError(f"certificate {name} is invalid") from exc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ReleaseError(f"certificate {name} is naive")
    if payload["state"] not in (STATE_DRAFT, STATE_RELEASE):
        raise ReleaseError("certificate state is unsupported")
    if not isinstance(payload.get("release_eligible"), bool):
        raise ReleaseError("certificate release_eligible must be boolean")
    if payload["release_eligible"] != (payload["state"] == STATE_RELEASE):
        raise ReleaseError("certificate state and release_eligible disagree")
    sections = payload.get("section_ids")
    if not isinstance(sections, list) or [str(s) for s in sections] != [
        spec.section_id for spec in SECTION_ORDER
    ]:
        raise ReleaseError("certificate section list is not the locked order")
    for name in ("candidate_count", "evidence_count"):
        if not isinstance(payload.get(name), int) or payload[name] < 0:
            raise ReleaseError(f"certificate {name} is invalid")


def certify_candidate_review_release(
    document: CandidateReviewDocument,
    html: str,
    *,
    certified_at: datetime,
    released: bool = False,
) -> CandidateReviewCertificate:
    """Bind the exact rendered bytes to the exact composed document."""

    if certified_at.tzinfo is None or certified_at.utcoffset() is None:
        raise ReleaseError("certified_at must be timezone-aware")

    # Semantic binding: the render must actually be THIS document's render.
    expected_title = (
        f"<title>{escape(document.client_name + ' Federal Opportunity Pre-Assessment', quote=True)}</title>")
    if html.count(expected_title) != 1:
        raise ReleaseError("rendered title does not match the exact client")
    if f'data-report-id="crv1-{document.binding.client_id}"' not in html:
        raise ReleaseError("rendered report id does not match the document client")
    if f'data-renderer="{RENDERER_VERSION}"' not in html:
        raise ReleaseError("rendered artifact was not produced by this renderer version")

    payload = {
        "schema_version": CERTIFICATE_SCHEMA_VERSION,
        "client_id": document.binding.client_id,
        "client_name": document.client_name,
        "document_id": document.document_id,
        "baseline_document_sha256": document.baseline_document_sha256,
        "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "renderer_version": RENDERER_VERSION,
        "composer_schema_version": COMPOSER_SCHEMA_VERSION,
        "as_of": document.as_of.isoformat(),
        "certified_at": certified_at.isoformat(),
        "state": STATE_RELEASE if released else STATE_DRAFT,
        "release_eligible": bool(released),
        "section_ids": [spec.section_id for spec in document.sections],
        "candidate_count": len(document.candidates),
        "evidence_count": len(document.evidence),
    }
    validate_certificate_schema(payload)
    return CandidateReviewCertificate(payload=payload)


def write_candidate_review_release(
    document: CandidateReviewDocument,
    html: str,
    certificate: CandidateReviewCertificate,
    *,
    root: Path | str,
) -> Path:
    """Write the artifact + QA envelope; return the certificate path."""

    validate_certificate_schema(certificate.payload)
    if certificate.payload["baseline_document_sha256"] != document.baseline_document_sha256:
        raise ReleaseError("certificate is not bound to this document")
    if certificate.html_sha256 != hashlib.sha256(html.encode("utf-8")).hexdigest():
        raise ReleaseError("certificate is not bound to these rendered bytes")

    client_id = document.binding.client_id
    directory = Path(root) / client_id
    directory.mkdir(parents=True, exist_ok=True)
    suffix = "" if certificate.release_eligible else ".DRAFT"
    html_path = directory / f"{client_id}.candidate_review{suffix}.html"
    qa_path = directory / f"{client_id}.candidate_review.qa.json"
    html_path.write_text(html, encoding="utf-8")
    qa_path.write_text(
        json.dumps(certificate.payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return qa_path


__all__ = (
    "CERTIFICATE_REQUIRED_FIELDS",
    "CERTIFICATE_SCHEMA_VERSION",
    "STATE_DRAFT",
    "STATE_RELEASE",
    "CandidateReviewCertificate",
    "ReleaseError",
    "certify_candidate_review_release",
    "validate_certificate_schema",
    "write_candidate_review_release",
)
