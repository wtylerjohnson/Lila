"""Release certifier (Chunk 6, Pass 3) tests: binding, schema, DRAFT/RELEASE."""

from __future__ import annotations

import importlib.util
import json
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agents.candidate_review_v1.candidate_engine import (
    CandidateBuildResult,
    RankedCandidate,
)
from agents.candidate_review_v1.composer import compose_candidate_review_document
from agents.candidate_review_v1.document_release import (
    STATE_DRAFT,
    STATE_RELEASE,
    ReleaseError,
    certify_candidate_review_release,
    validate_certificate_schema,
    write_candidate_review_release,
)
from agents.candidate_review_v1.renderer import render_candidate_review

_REF_PATH = Path(__file__).parent / "test_candidate_review_v1_reference_contract.py"


def _ref():
    spec = importlib.util.spec_from_file_location("_crv1_ref_release", _REF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REF = _ref()


def _composed(index: int = 0):
    doc = [_REF._golden_document(r) for r in _REF._reports()][index]
    ordered = sorted(doc.candidates, key=lambda c: c.candidate_id.casefold())
    ranked = tuple(
        RankedCandidate(candidate=c, rank_score=Decimal("0.9") - Decimal("0.01") * i,
                        rank=i + 1, selected_for_report=True, diversity_key=c.candidate_id)
        for i, c in enumerate(ordered))
    build = CandidateBuildResult(
        binding=doc.binding, as_of=doc.as_of, evidence=doc.evidence,
        ranked_inventory=ranked, candidates=tuple(ordered), deferred_candidates=())
    comp = compose_candidate_review_document(
        binding=doc.binding, document_id="DOC-REL", as_of=doc.as_of,
        generated_at=doc.as_of - timedelta(minutes=1), candidate_build=build)
    html = render_candidate_review(comp.document)
    return comp.document, html


def test_draft_is_the_resting_state_and_binds_document_and_bytes():
    document, html = _composed()
    cert = certify_candidate_review_release(
        document, html, certified_at=document.as_of)
    validate_certificate_schema(cert.payload)
    assert cert.state == STATE_DRAFT
    assert cert.release_eligible is False
    assert cert.payload["baseline_document_sha256"] == document.baseline_document_sha256
    assert cert.payload["candidate_count"] == len(document.candidates)
    assert cert.payload["evidence_count"] == len(document.evidence)
    assert cert.payload["section_ids"] == [s.section_id for s in document.sections]


def test_release_is_an_explicit_decision():
    document, html = _composed()
    cert = certify_candidate_review_release(
        document, html, certified_at=document.as_of, released=True)
    assert cert.state == STATE_RELEASE and cert.release_eligible is True


def test_render_from_another_client_refuses_to_certify():
    document, _ = _composed(0)
    _, other_html = _composed(1)
    with pytest.raises(ReleaseError):
        certify_candidate_review_release(document, other_html, certified_at=document.as_of)


def test_naive_timestamp_refuses():
    document, html = _composed()
    naive = document.as_of.astimezone(timezone.utc).replace(tzinfo=None)
    with pytest.raises(ReleaseError):
        certify_candidate_review_release(document, html, certified_at=naive)


def test_write_emits_draft_filename_and_valid_qa_envelope(tmp_path):
    document, html = _composed()
    cert = certify_candidate_review_release(document, html, certified_at=document.as_of)
    qa_path = write_candidate_review_release(document, html, cert, root=tmp_path)
    client_id = document.binding.client_id
    assert qa_path.name == f"{client_id}.candidate_review.qa.json"
    draft = tmp_path / client_id / f"{client_id}.candidate_review.DRAFT.html"
    assert draft.exists() and draft.read_text(encoding="utf-8") == html
    validate_certificate_schema(json.loads(qa_path.read_text(encoding="utf-8")))


def test_release_write_drops_the_draft_suffix(tmp_path):
    document, html = _composed()
    cert = certify_candidate_review_release(
        document, html, certified_at=document.as_of, released=True)
    write_candidate_review_release(document, html, cert, root=tmp_path)
    client_id = document.binding.client_id
    assert (tmp_path / client_id / f"{client_id}.candidate_review.html").exists()


def test_write_refuses_bytes_that_do_not_match_the_certificate(tmp_path):
    document, html = _composed()
    cert = certify_candidate_review_release(document, html, certified_at=document.as_of)
    with pytest.raises(ReleaseError):
        write_candidate_review_release(document, html + "<!-- tampered -->", cert, root=tmp_path)


def test_certificate_schema_rejects_inexact_fields():
    document, html = _composed()
    cert = certify_candidate_review_release(document, html, certified_at=document.as_of)
    extra = {**cert.payload, "unexpected": 1}
    with pytest.raises(ReleaseError):
        validate_certificate_schema(extra)
    missing = {k: v for k, v in cert.payload.items() if k != "html_sha256"}
    with pytest.raises(ReleaseError):
        validate_certificate_schema(missing)
    disagreeing = {**cert.payload, "release_eligible": True}
    with pytest.raises(ReleaseError):
        validate_certificate_schema(disagreeing)


# --------------------------------------------------------------------------- #
# Pass 4 - the one [out:candidate-review-document] marker
# --------------------------------------------------------------------------- #

def _published(tmp_path):
    """Write a real certificate under a tmp repo root; return (root, path)."""
    from agents.candidate_review_v1.document_release import (
        certify_candidate_review_release as certify,
    )
    document, html = _composed()
    cert = certify(document, html, certified_at=document.as_of)
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    qa_path = write_candidate_review_release(document, html, cert, root=state_root)
    return tmp_path, qa_path, document


def test_marker_validates_against_its_certificate(tmp_path):
    from agents.assessment_chain import validate_candidate_review_document_marker
    root, qa_path, document = _published(tmp_path)
    stdout = f"noise\n[out:candidate-review-document] {qa_path}\nmore noise\n"
    resolved = validate_candidate_review_document_marker(
        root, document.binding.client_id, stdout)
    assert resolved == qa_path.resolve()


@pytest.mark.parametrize("stdout", ["", "[out:candidate-review-document] a\n[out:candidate-review-document] b\n"])
def test_marker_must_appear_exactly_once(tmp_path, stdout):
    from agents.assessment_chain import validate_candidate_review_document_marker
    root, _qa, document = _published(tmp_path)
    with pytest.raises(ValueError):
        validate_candidate_review_document_marker(
            root, document.binding.client_id, stdout)


def test_marker_outside_the_state_root_refuses(tmp_path):
    from agents.assessment_chain import validate_candidate_review_document_marker
    root, qa_path, document = _published(tmp_path)
    stray = tmp_path / "elsewhere.candidate_review.qa.json"
    stray.write_text(qa_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_candidate_review_document_marker(
            root, document.binding.client_id,
            f"[out:candidate-review-document] {stray}\n")


def test_marker_for_another_client_refuses(tmp_path):
    from agents.assessment_chain import validate_candidate_review_document_marker
    root, qa_path, _document = _published(tmp_path)
    with pytest.raises(ValueError):
        validate_candidate_review_document_marker(
            root, "someone-else", f"[out:candidate-review-document] {qa_path}\n")
