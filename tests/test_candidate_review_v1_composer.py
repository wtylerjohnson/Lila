"""Composer (Chunk 6 core) tests: validity, determinism, sparse-valid, drops.

The composer is a pure assembler.  These tests drive it from CandidateBuildResult
values wrapped around the known-valid golden documents, plus hostile inputs, and
assert it either returns a re-validated document or raises ComposerError.
"""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from agents.candidate_review_v1.candidate_engine import (
    CandidateBuildResult,
    RankedCandidate,
)
from agents.candidate_review_v1.composer import (
    ComposerError,
    _baseline_digest,
    compose_candidate_review_document,
)
from agents.candidate_review_v1.contracts import (
    SECTION_ORDER,
    CandidateReviewDocument,
    CoverageRecord,
    CoverageState,
    EvidenceBoundClaim,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    SourceIdentity,
    SourceTier,
)
from agents.candidate_review_v1.renderer import render_candidate_review

_REF_PATH = Path(__file__).parent / "test_candidate_review_v1_reference_contract.py"


def _ref():
    spec = importlib.util.spec_from_file_location("_crv1_ref_composer", _REF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REF = _ref()


def _build_result(doc: CandidateReviewDocument) -> CandidateBuildResult:
    """Wrap a golden document's candidates + evidence as a CandidateBuildResult.

    Strictly-descending rank_score avoids the engine's tie-break ordering.
    """
    ordered = sorted(doc.candidates, key=lambda c: c.candidate_id.casefold())
    ranked = tuple(
        RankedCandidate(
            candidate=candidate,
            rank_score=Decimal("0.9") - Decimal("0.01") * index,
            rank=index + 1,
            selected_for_report=True,
            diversity_key=candidate.candidate_id,
        )
        for index, candidate in enumerate(ordered)
    )
    return CandidateBuildResult(
        binding=doc.binding,
        as_of=doc.as_of,
        evidence=doc.evidence,
        ranked_inventory=ranked,
        candidates=tuple(ordered),
        deferred_candidates=(),
    )


def _compose(doc: CandidateReviewDocument, **kw):
    build = _build_result(doc)
    return compose_candidate_review_document(
        binding=doc.binding,
        document_id="DOC-COMPOSE",
        as_of=doc.as_of,
        generated_at=doc.as_of - timedelta(minutes=1),
        candidate_build=build,
        **kw,
    )


def _documents():
    return [_REF._golden_document(report) for report in _REF._reports()]


# --------------------------------------------------------------------------- #

def test_composes_a_revalidated_document_for_each_golden():
    for doc in _documents():
        comp = _compose(doc)
        # returned document round-trips the full validator
        again = CandidateReviewDocument.model_validate(comp.document.model_dump(mode="python"))
        assert again == comp.document
        assert comp.document.sections == SECTION_ORDER
        assert comp.document.sections[5].section_id == "calendar"
        order = comp.document.editor_state.candidate_order
        assert set(order) == {c.candidate_id for c in comp.document.candidates}
        assert len(order) == len(comp.document.candidates)
        # evidence is the referenced closure, sorted, unique
        ev_ids = [e.evidence_id for e in comp.document.evidence]
        assert ev_ids == sorted(ev_ids)
        assert len(ev_ids) == len(set(ev_ids))
        assert comp.document.calendar_events == ()


def test_baseline_hash_is_a_fixed_point_and_binds_editor():
    for doc in _documents():
        comp = _compose(doc)
        d = comp.document
        assert d.baseline_document_sha256 == comp.baseline_document_sha256
        assert d.editor_state.baseline_document_sha256 == d.baseline_document_sha256
        assert _baseline_digest(d) == d.baseline_document_sha256
        assert len(d.baseline_document_sha256) == 64
        assert d.baseline_document_sha256 == d.baseline_document_sha256.lower()


def test_composition_is_deterministic():
    for doc in _documents():
        a = _compose(doc).document.model_dump(mode="json")
        b = _compose(doc).document.model_dump(mode="json")
        assert a == b
        assert _compose(doc).baseline_document_sha256 == _compose(doc).baseline_document_sha256


def test_rendered_document_carries_the_locked_surfaces():
    doc = _documents()[0]
    html = render_candidate_review(_compose(doc).document)
    for spec in SECTION_ORDER:
        assert f'id="section-{spec.section_id}"' in html
    for act in ("toggle-edit", "save-draft", "reset", "download-html", "print", "undo-soft-delete"):
        assert f'data-action="{act}"' in html
    assert html.count('data-workflow-action="refresh-analyst-layer"') == 1
    assert "Priority federal routes" not in html and "—" not in html


def test_sparse_candidate_build_is_valid_and_counts_zero():
    doc = _documents()[0]
    empty = CandidateBuildResult(
        binding=doc.binding, as_of=doc.as_of, evidence=(),
        ranked_inventory=(), candidates=(), deferred_candidates=())
    comp = compose_candidate_review_document(
        binding=doc.binding, document_id="DOC-EMPTY", as_of=doc.as_of,
        generated_at=doc.as_of - timedelta(minutes=1), candidate_build=empty)
    d = comp.document
    assert d.candidates == ()
    count_tile = next(t for t in d.kpi_tiles if t.kind.value == "candidate_count")
    assert count_tile.value == "0" and count_tile.computed is True
    CandidateReviewDocument.model_validate(d.model_dump(mode="python"))


def test_derived_money_kpi_matches_typed_money_display():
    # golden 0's first candidate carries OBLIGATED_TO_DATE + AGGREGATE_HISTORY money
    doc = _documents()[0]
    comp = _compose(doc)
    money_tiles = [t for t in comp.document.kpi_tiles if t.money_id]
    assert money_tiles, "expected at least one derived money KPI"
    money_by_id = {m.money_id: m for c in comp.document.candidates for m in c.money}
    for tile in money_tiles:
        assert tile.value == money_by_id[tile.money_id].display_value
        assert set(money_by_id[tile.money_id].evidence_ids).issubset(set(tile.evidence_ids))


def test_claim_with_unresolved_evidence_drops_or_raises():
    doc = _documents()[0]
    bad = EvidenceBoundClaim(
        block_id="mkt-bad", client_id=doc.binding.client_id,
        heading="Signal heading", records_show="Records show a bounded pattern.",
        may_suggest="It may indicate a corridor.", validate_next="Validate the next record.",
        evidence_ids=("E-DOES-NOT-EXIST",))
    # strict=False: dropped, document still valid
    comp = _compose(doc, market_signals=(bad,))
    assert comp.document.market_signals == ()
    assert any(d.target == "claim" and d.entity_id == "mkt-bad" for d in comp.dropped)
    # strict=True: raises
    with pytest.raises(ComposerError):
        _compose(doc, market_signals=(bad,), strict=True)


def test_two_evidence_records_sharing_a_source_identity_raise():
    doc = _documents()[0]
    cid = doc.binding.client_id
    shared = SourceIdentity(source_system="usaspending.gov", record_id="DUPKEY")
    common = dict(
        client_id=cid, run_id=doc.binding.run_id, scope_sha256=doc.binding.scope_sha256,
        source_identity=shared, source_tier=SourceTier.MARKET, source_kind=EvidenceKind.AWARD,
        source_name="USAspending.gov", source_url="https://www.usaspending.gov/award/DUPKEY",
        retrieved_at=doc.as_of - timedelta(hours=1), title="Golden dup", excerpt="A duplicate identity probe.",
        official_source=True, primary_source=True, supports=(EvidenceUse.BUYER,))
    e1 = EvidenceRecord(evidence_id="E-DUP-1", **common)
    e2 = EvidenceRecord(evidence_id="E-DUP-2", **common)
    claim = EvidenceBoundClaim(
        block_id="mkt-dup", client_id=cid, heading="Dup signal",
        records_show="Records show two records.", may_suggest="It may indicate a collision.",
        validate_next="Validate the identity.", evidence_ids=("E-DUP-1", "E-DUP-2"))
    with pytest.raises(ComposerError):
        _compose(doc, evidence=(e1, e2), market_signals=(claim,))


def _coverage(doc, *, source="sam.gov", query_family="notice",
              query_id="Q1", watch_target_id=None, attempted_delta=timedelta(hours=-1)):
    from datetime import date
    return CoverageRecord(
        client_id=doc.binding.client_id, run_id=doc.binding.run_id,
        scope_sha256=doc.binding.scope_sha256, source=source, query_family=query_family,
        state=CoverageState.RETURNED, window_start=date(2026, 1, 1), window_end=date(2027, 1, 1),
        attempted_at=doc.as_of + attempted_delta, records_returned=0, records_accepted=0,
        accepted_evidence_ids=(), query_manifest_id="m1", query_id=query_id,
        watch_target_id=watch_target_id)


def test_future_attempt_coverage_row_is_a_clean_drop_not_a_raw_validation_error():
    doc = _documents()[0]
    future = _coverage(doc, attempted_delta=timedelta(hours=1))
    comp = _compose(doc, coverage=(future,))
    assert comp.document.coverage == ()
    assert any(d.target == "coverage" for d in comp.dropped)
    with pytest.raises(ComposerError):
        _compose(doc, coverage=(future,), strict=True)


def test_duplicate_coverage_query_id_is_a_clean_drop_not_a_raw_validation_error():
    doc = _documents()[0]
    a = _coverage(doc, query_id="Q1", watch_target_id=None)
    b = _coverage(doc, query_id="Q1", watch_target_id="T1")  # distinct identity_key, same query_id
    comp = _compose(doc, coverage=(a, b))
    assert len(comp.document.coverage) == 1
    assert any(d.reason == "duplicate coverage query id" for d in comp.dropped)
    with pytest.raises(ComposerError):
        _compose(doc, coverage=(a, b), strict=True)


def test_evidence_count_kpi_reports_the_closure_not_the_raw_pool():
    doc = _documents()[0]
    # add an unreferenced evidence record to the pool via the evidence arg
    orphan = EvidenceRecord(
        evidence_id="E-ORPHAN", client_id=doc.binding.client_id, run_id=doc.binding.run_id,
        scope_sha256=doc.binding.scope_sha256,
        source_identity=SourceIdentity(source_system="usaspending.gov", record_id="ORPHAN"),
        source_tier=SourceTier.MARKET, source_kind=EvidenceKind.AWARD, source_name="USAspending.gov",
        source_url="https://www.usaspending.gov/award/ORPHAN",
        retrieved_at=doc.as_of - timedelta(hours=1), title="Orphan", excerpt="Unreferenced record.",
        official_source=True, primary_source=True, supports=(EvidenceUse.BUYER,))
    comp = _compose(doc, evidence=(orphan,))
    assert "E-ORPHAN" not in {e.evidence_id for e in comp.document.evidence}  # not referenced -> not in closure
    evidence_tiles = [t for t in comp.document.kpi_tiles
                      if t.kind.value == "source_coverage" and t.kpi_id == "kpi-evidence"]
    assert evidence_tiles, "expected an evidence-count KPI"
    assert evidence_tiles[0].value == str(len(comp.document.evidence))
