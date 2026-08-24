"""Assess -> Target is an explicit operator door (2026-07-12, UX plan A2).

The outreach lane (contact plan, target report, Target rail) stays physically
locked until the operator, holding a CURRENT Assess approval, clicks
proceed-to-Target. Evidence drift stales the Assess approval and re-locks the
lane with no new click; revocation is journaled, never deleted.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.review as review  # noqa: E402
from agents.review import (  # noqa: E402
    approve_targeting_review,
    approve_target,
    load_target_approval,
    save_targeting_plan,
    targeting_review_binding_receipt,
    targeting_review_status,
    targeting_target_set_sha256,
    validate_targeting_plan,
    revoke_target,
    target_gate_status,
)


def _approved_assess(monkeypatch, status="approved", problems=None):
    monkeypatch.setattr(
        "agents.assess.approval.assess_approval_for_release",
        lambda *a, **k: ({"client": a[0] if a else "Testco",
                          "approval_id": "assess-v1"} if status == "approved" else None,
                         status, list(problems or [])))


class TestTargetGateStatus:
    def test_locked_until_the_explicit_click(self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        ok, problems = target_gate_status("Testco", review_dir=str(tmp_path))
        assert ok is False
        assert any("proceed to Target" in p for p in problems)

    def test_click_plus_current_assess_approval_unlocks(
            self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        payload = approve_target("Testco", note="ship it",
                                 review_dir=str(tmp_path))
        assert payload["approved_at"]
        ok, problems = target_gate_status("Testco", review_dir=str(tmp_path))
        assert ok is True and problems == []
        on_disk = load_target_approval("Testco", review_dir=str(tmp_path))
        assert on_disk["note"] == "ship it"

    def test_assess_drift_relocks_without_any_click(
            self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        approve_target("Testco", review_dir=str(tmp_path))
        _approved_assess(monkeypatch, status="stale",
                         problems=["sweep evidence changed"])
        ok, problems = target_gate_status("Testco", review_dir=str(tmp_path))
        assert ok is False
        assert any("not approved" in p for p in problems)

    def test_revocation_relocks_and_is_never_deleted(
            self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        approve_target("Testco", review_dir=str(tmp_path))
        assert revoke_target("Testco", review_dir=str(tmp_path)) is True
        ok, _problems = target_gate_status("Testco", review_dir=str(tmp_path))
        assert ok is False
        on_disk = load_target_approval("Testco", review_dir=str(tmp_path))
        assert on_disk["revoked_at"]  # audit event retained
        assert revoke_target("NeverApproved", review_dir=str(tmp_path)) is False


def _target(target_id="target-1", reason="POC on pursuit #1 · Test need"):
    return {
        "id": target_id, "reason": reason, "reason_rank": 1,
        "title": "Contract Specialist", "last_observed": "2026-08-01",
        "email": {"source_url": "https://sam.gov/example", "grade": "B"},
    }


def _complete_action(lane="acquisition"):
    return {
        "lane": lane, "disposition": "ready_to_contact",
        "route": "Validate the published acquisition route.",
        "proposed_role": "Support payment-integrity modernization.",
        "first_ask": "Confirm the current requirement owner and acquisition path.",
        "message": "Test fit against the documented requirement without implying a relationship.",
        "call_to_action": "Schedule a short validation conversation.",
        "action_window": "By 2026-08-21", "owner": "A. Executive",
        "learn": "Current owner, timing, funding, and vehicle.",
        "desired_outcome": "A verified next step and responsible role.",
        "qualification_question": "Is the requirement active and funded?",
        "stop_condition": "Stop if the requirement or role cannot be verified.",
        "promotion_criteria": "Promote after role, route, timing, and source are current.",
        "reject_reason": "",
    }


def _play_lane_dispositions(reason="POC on pursuit #1 · Test need",
                            covered="acquisition"):
    from agents.review import targeting_play_key
    lanes = {}
    for lane in ("buyer", "acquisition", "partner", "incumbent", "positioning", "event"):
        lanes[lane] = ({"status": "covered", "note": "Complete action is bound."}
                       if lane == covered else {
                           "status": "no_qualified_target",
                           "evidence_checked": "Official notice and award sources",
                           "missing": "Verified role and access route",
                           "next_action": "Check the named forecast and award record",
                           "owner": "A. Operator",
                           "deadline": "By 2026-08-21",
                       })
    return {targeting_play_key(reason): lanes}


class TestTargetingReviewCompletion:
    def test_target_unlock_is_not_targeting_completion(self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        approve_target("Testco", review_dir=str(tmp_path))
        ready, problems = targeting_review_status(
            "Testco", [_target()], review_dir=str(tmp_path))
        assert ready is False
        assert any("top play has no complete target action" in p for p in problems)
        assert any("has not been approved" in p for p in problems)

    def test_complete_plan_receipt_is_bound_and_drift_fails_closed(
            self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        approve_target("Testco", review_dir=str(tmp_path))
        targets = [_target()]
        plan = save_targeting_plan(
            "Testco", actions={"target-1": _complete_action()},
            play_lane_dispositions=_play_lane_dispositions(),
            review_dir=str(tmp_path))
        assert validate_targeting_plan(plan, targets) == []
        approve_targeting_review("Testco", targets, review_dir=str(tmp_path))
        receipt, bound, binding_problems = targeting_review_binding_receipt(
            "Testco", targets=targets, review_dir=str(tmp_path))
        assert bound is True and binding_problems == []
        assert set(receipt) >= {
            "target_set_sha256", "plan_sha256",
            "assess_approval_sha256", "target_unlock_sha256",
        }
        target_set_sha256 = targeting_target_set_sha256(targets)
        receipt_by_hash, bound_by_hash, hash_problems = (
            targeting_review_binding_receipt(
                "Testco", expected_target_set_sha256=target_set_sha256,
                review_dir=str(tmp_path)))
        assert bound_by_hash is True and hash_problems == []
        assert receipt_by_hash == receipt
        _receipt, bound_without_inventory, missing_problems = (
            targeting_review_binding_receipt(
                "Testco", review_dir=str(tmp_path)))
        assert bound_without_inventory is False
        assert any("inventory binding is required" in problem
                   for problem in missing_problems)
        _receipt, stale_hash_bound, stale_hash_problems = (
            targeting_review_binding_receipt(
                "Testco", expected_target_set_sha256="f" * 64,
                review_dir=str(tmp_path)))
        assert stale_hash_bound is False
        assert any("inventory changed" in problem
                   for problem in stale_hash_problems)
        ready, problems = targeting_review_status(
            "Testco", targets, review_dir=str(tmp_path))
        assert ready is True and problems == []
        drifted = [{**targets[0], "last_observed": "2026-08-02"}]
        ready, problems = targeting_review_status(
            "Testco", drifted, review_dir=str(tmp_path))
        assert ready is False
        assert any("inventory changed" in p for p in problems)

        revoke_target("Testco", review_dir=str(tmp_path))
        _receipt, bound, binding_problems = targeting_review_binding_receipt(
            "Testco", targets=targets, review_dir=str(tmp_path))
        assert bound is False
        assert any("Target is locked" in p for p in binding_problems)
        approve_target("Testco", note="reopened after review",
                       review_dir=str(tmp_path))
        ready, problems = targeting_review_status(
            "Testco", targets, review_dir=str(tmp_path))
        assert ready is False
        assert any("Target unlock binding changed" in p for p in problems)

        contact_drift = [{**targets[0], "email": {
            "source_url": "https://sam.gov/example", "grade": "D",
            "value": "changed@example.gov",
        }}]
        ready, problems = targeting_review_status(
            "Testco", contact_drift, review_dir=str(tmp_path))
        assert ready is False
        assert any("inventory changed" in p for p in problems)

    def test_gap_requires_structured_owner_action_and_deadline(
            self, tmp_path, monkeypatch):
        _approved_assess(monkeypatch)
        approve_target("Testco", review_dir=str(tmp_path))
        incomplete = _play_lane_dispositions()
        play_id = next(iter(incomplete))
        incomplete[play_id]["buyer"] = {
            "status": "no_qualified_target",
            "evidence_checked": "SAM.gov notice",
            "missing": "Mission owner",
            "next_action": "Find the program office source",
            "owner": "",
            "deadline": "",
        }
        plan = save_targeting_plan(
            "Testco", actions={"target-1": _complete_action()},
            play_lane_dispositions=incomplete, review_dir=str(tmp_path))
        problems = validate_targeting_plan(plan, [_target()])
        assert any("buyer gap is missing: owner, deadline" in p
                   for p in problems)


class TestTargetGateServerSurfaces:
    def _seed(self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")  # noqa: F841
        import ui.server as srv
        from tests.test_ui import _mk_client
        review_dir = _mk_client(str(tmp_path))
        monkeypatch.setattr(srv, "REVIEW_DIR", review_dir)
        monkeypatch.setattr(review, "REVIEW_DIR", review_dir)
        return srv, review_dir

    def test_endpoint_refuses_without_current_assess_approval(
            self, tmp_path, monkeypatch):
        srv, _ = self._seed(tmp_path, monkeypatch)
        _approved_assess(monkeypatch, status="missing",
                         problems=["no Assess approval on file"])
        r = srv.app.test_client().post("/api/review/target-approve", json={
            "client_name": "Acme Federal"})
        assert r.status_code == 409
        assert r.get_json()["target_approved"] is False

    def test_endpoint_approve_and_revoke_roundtrip(
            self, tmp_path, monkeypatch):
        srv, review_dir = self._seed(tmp_path, monkeypatch)
        _approved_assess(monkeypatch)
        client = srv.app.test_client()
        r = client.post("/api/review/target-approve", json={
            "client_name": "Acme Federal"})
        assert r.status_code == 200 and r.get_json()["target_approved"]
        assert load_target_approval("Acme Federal", review_dir=review_dir)
        r = client.post("/api/review/target-approve", json={
            "client_name": "Acme Federal", "revoke": True})
        assert r.status_code == 200
        assert r.get_json()["target_approved"] is False
        ok, _ = target_gate_status("Acme Federal", review_dir=review_dir)
        assert ok is False

    def test_run_api_holds_the_target_lane_closed(
            self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        srv, _ = self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr(
            srv, "load_packet",
            lambda _c, **_kwargs: SimpleNamespace(
                status=srv.ReviewStatus.APPROVED,
                search_scope=None,
            ))
        _approved_assess(monkeypatch, status="missing", problems=["none"])
        launched = []
        monkeypatch.setattr(
            srv, "start_job",
            lambda *_a, **_k: launched.append(1) or "should-not-start")
        for step in ("contacts", "target_report"):
            r = srv.app.test_client().post("/api/run", json={
                "client_name": "Acme Federal", "step": step, "args": {}})
            assert r.status_code == 409, step
            assert ("Target is locked" in r.get_json()["error"]
                    or "complete Targeting Review" in r.get_json()["error"])
        assert not launched


class TestIntakeIdentityGuard:
    """A4 (2026-07-12): duplicate or colliding client names can never
    silently overwrite an existing review packet through intake."""

    def test_intake_refuses_an_existing_identity(self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")  # noqa: F841
        import ui.server as srv
        from tests.test_ui import _mk_client
        review_dir = _mk_client(str(tmp_path))  # seeds acme_federal packet
        monkeypatch.setattr(srv, "REVIEW_DIR", review_dir)
        started = []
        monkeypatch.setattr(srv, "start_job",
                            lambda *a, **k: started.append(1) or "nope")
        client = srv.app.test_client()
        for colliding in ("Acme Federal", "Acme, Federal", "acme federal"):
            r = client.post("/api/intake", json={"client_name": colliding})
            assert r.status_code == 409, colliding
            assert "already exists" in r.get_json()["error"]
        assert started == []

    def test_intake_proceeds_for_a_new_identity(self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")  # noqa: F841
        import ui.server as srv
        from tests.test_ui import _mk_client
        review_dir = _mk_client(str(tmp_path))
        monkeypatch.setattr(srv, "REVIEW_DIR", review_dir)
        monkeypatch.setattr(srv, "INTAKE_DIR", str(tmp_path / "intake"))
        monkeypatch.setattr(srv, "start_job", lambda *a, **k: "job-1")
        monkeypatch.setattr("tools.brand_marks.prefetch_client_logo",
                            lambda name: name == "Brand New Co")
        r = srv.app.test_client().post("/api/intake", json={
            "client_name": "Brand New Co"})
        assert r.status_code == 200
        assert r.get_json()["slug"] == "brand_new_co"
        assert r.get_json()["logo_imported"] is True
