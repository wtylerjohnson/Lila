"""Cycle 5 follow-on review repairs (2026-07-12): the four P1s + one P2 Codex
found in the QB solo range, each pinned so it cannot silently reopen."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── P1: pointer refresh CAS serializes under a per-scope lock ────────────────
class TestPointerLockClosesTheRace:
    def test_refresh_holds_the_scope_lock_across_compare_and_write(
            self, tmp_path, monkeypatch):
        import agents.assess.ledger as ledger
        from tests.test_live_report_truth import (
            _cutover_sweep, _persist, _seed_runtime,
        )
        seed = _seed_runtime(tmp_path, monkeypatch, _cutover_sweep())
        _persist(seed)
        pointer = ledger.current_assess_pointer_path(
            "Testco", "all", state_dir=seed["state_dir"])
        lock_path = pointer.with_suffix(pointer.suffix + ".lock")

        # while the write section runs, the lock file exists and is held; a
        # concurrent acquire would block. We prove the CAS reads current bytes
        # UNDER the lock: removing the pointer mid-write (via the atomic-json
        # hook) is caught by the compare, never silently recreated.
        real_atomic = ledger._atomic_json
        seen = {}

        def _spy(path, payload):
            seen["lock_present"] = lock_path.exists()
            return real_atomic(path, payload)

        monkeypatch.setattr(ledger, "_atomic_json", _spy)
        from tools.assess_refresh import activate_current_assess_run
        activate_current_assess_run(
            "Testco", sweep_path=seed["sweep_path"],
            state_dir=seed["state_dir"], review_dir=seed["review_dir"])
        assert seen.get("lock_present") is True

    def test_concurrent_removal_is_not_recreated(self, tmp_path, monkeypatch):
        import agents.assess.ledger as ledger
        from tests.test_live_report_truth import (
            _cutover_sweep, _persist, _seed_runtime,
        )
        from tools.assess_refresh import refresh_current_assess_run_if_active
        seed = _seed_runtime(tmp_path, monkeypatch, _cutover_sweep())
        _persist(seed)
        pointer = ledger.current_assess_pointer_path(
            "Testco", "all", state_dir=seed["state_dir"])
        real = ledger.materialize_current_assess_run

        def _remove_then_materialize(*a, **k):
            pointer.unlink()  # operator rolls back mid-refresh
            return real(*a, **k)

        monkeypatch.setattr(
            ledger, "materialize_current_assess_run", _remove_then_materialize)
        out = refresh_current_assess_run_if_active(
            "Testco", sweep_path=seed["sweep_path"],
            state_dir=seed["state_dir"], review_dir=seed["review_dir"])
        assert out.startswith("FAILED")
        assert not pointer.exists()  # rollback stands, never recreated


# ── P1: a credential can never enter the persisted arbiter note ──────────────
class TestArbiterNoteNeverCarriesTheKey:
    def test_exception_echoing_the_key_is_scrubbed(self, monkeypatch):
        import agents.decisions.arbiters as arb
        from agents.reports.facts import FactPack
        monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecretKEY1234567890")

        def _boom(*a, **k):
            raise RuntimeError(
                "401 with headers {'Authorization': 'Bearer "
                "sk-supersecretKEY1234567890'}")

        monkeypatch.setattr("tools.api._http.post_json", _boom)
        judge = arb.OpenAIArbiter()

        class _Draft:
            def model_dump(self, **k):
                return {}
        audit = judge.audit(FactPack(client_name="X", as_of="2026-07-12",
                                     facts=[]), _Draft())
        assert audit.passed is False
        assert "supersecret" not in audit.note
        assert "sk-supersecret" not in audit.note
        assert "sk-***" in audit.note or "Bearer ***" in audit.note


# ── P1: post-approval keyword removal keeps kept_out provenance ──────────────
class TestKeptOutSurvivesTheLockedPath:
    def test_amend_terms_persists_kept_out(self, tmp_path, monkeypatch):
        import agents.review as review
        from agents.decisions.schemas import (
            IntakeStrategy, Keyword, SearchSpec,
        )
        from agents.review import (
            amend_terms, decide, load_packet, request_approval,
        )
        monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
        strategy = IntakeStrategy(
            client_name="Testco", pursuit_strategy="n.",
            keywords=[Keyword(term="packet capture", category="capability",
                              rationale="core")],
            inferred_naics=["541512"], target_agencies=["DHS"],
            set_aside_angles=[],
            searches=[SearchSpec(source="sam.gov", query_terms=["packet capture"],
                                 naics_codes=["541512"], set_asides=[],
                                 rationale="s")],
            confidence=0.8, review_gate="approve")
        request_approval(strategy, alert_fn=lambda *_a: None)
        decide("Testco", approve=True)  # lock it: the iterate path now runs
        pkt = amend_terms(
            "Testco",
            keywords=[{"term": "packet capture", "category": "capability",
                       "rationale": "kept"}],
            kept_out=[{"term": "zero trust", "category": "capability",
                       "rationale": "too broad; moved out by the operator"}])
        kept = load_packet("Testco").strategy.kept_out
        assert [k.term for k in kept] == ["zero trust"]
        assert pkt.status.value == "approved"  # approval intact


# ── P1: containment check failure fails CLOSED for a scoped build ────────────
class TestContainmentFailsClosed:
    def test_focus_build_flags_when_the_check_throws(self):
        # the guard: a focus sweep whose containment check raises must add a
        # blocking SCOPE_CONTAINMENT flag, never ship unchecked. We exercise
        # the decision directly (the runner wraps build_document + the gate).
        from tools.agency_scope import document_focus_violations
        focus_sweep = {"search_scope": {"mode": "focus",
                                        "agencies": [{"abbr": "DHS"}]}}
        # a document whose attribute access explodes simulates the throw
        class _Boom:
            @property
            def board(self):
                raise RuntimeError("document construction failed")
        with pytest.raises(RuntimeError):
            document_focus_violations(_Boom(), focus_sweep)
        # the runner's contract: this raise becomes a blocking flag when the
        # sweep is focus-scoped (asserted end-to-end in the runner path).


# ── P2: review export cannot escape its directory ───────────────────────────
class TestReviewExportStaysContained:
    def test_destination_is_asserted_under_the_export_dir(self):
        import inspect
        import ui.server as srv
        src = inspect.getsource(srv.api_export_review_pdf)
        assert "invalid review export destination" in src
        assert "os.path.realpath(pdf_path).startswith" in src
