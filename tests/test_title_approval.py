"""The title-set approval (build step 4) · CONTRACT-SURFACE CHANGE.

This adds a durable operator decision adjacent to the documented
"Assess -> Target operator door" in docs/CONTRACT_SURFACES.md. It is
presented as its own diff per CLAUDE.md session protocol rule 3.

The tests below prove the three properties that make it safe to add:
  1. it does NOT weaken target_gate_status, which is untouched
  2. it does NOT unlock outreach from a new path: a closed Target door
     still closes everything
  3. it fails closed on every drift direction, so an approval cannot
     outlive the evidence it was given
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import title_approval as tap  # noqa: E402

CLIENT = "Testco"
MOTION = "displacement-buying-component-defense"
FP = "a" * 64


def _approve(tmp_path, *, fingerprint=FP, titles=("Contracting Officer",),
             motion_id=MOTION):
    return tap.record_title_approval(
        CLIENT, motion_id=motion_id, fingerprint=fingerprint, titles=titles,
        approved_at="2026-08-06T12:00:00+00:00", review_dir=str(tmp_path))


# ===== fails closed in every direction ==================================== #
def test_no_approval_means_supply_may_not_run(tmp_path):
    unlocked, problems = tap.title_gate_status(
        CLIENT, fingerprint=FP, motion_id=MOTION, review_dir=str(tmp_path))
    assert unlocked is False
    assert "no title set has been approved" in problems[0]


def test_an_approved_set_unlocks_only_its_own_motion(tmp_path):
    _approve(tmp_path)
    ok, _ = tap.title_gate_status(CLIENT, fingerprint=FP, motion_id=MOTION,
                                  review_dir=str(tmp_path))
    assert ok is True
    other, problems = tap.title_gate_status(
        CLIENT, fingerprint=FP, motion_id="engage-paper-holder-fcn",
        review_dir=str(tmp_path))
    assert other is False and "no approved title set for motion" in problems[0]


def test_drift_in_the_fingerprint_relocks_with_no_new_click(tmp_path):
    """THE POINT OF BINDING THE PAYLOAD HASH. An operator approved a
    vocabulary derived from specific evidence; when that evidence, the
    prompt, or the capability terms change, what they approved no longer
    exists."""
    _approve(tmp_path)
    unlocked, problems = tap.title_gate_status(
        CLIENT, fingerprint="b" * 64, motion_id=MOTION,
        review_dir=str(tmp_path))
    assert unlocked is False
    assert "changed since it was approved" in problems[0]


def test_an_empty_approved_set_is_refused(tmp_path):
    _approve(tmp_path, titles=())
    unlocked, problems = tap.title_gate_status(
        CLIENT, fingerprint=FP, motion_id=MOTION, review_dir=str(tmp_path))
    assert unlocked is False
    assert "empty" in problems[0]


def test_a_stored_approval_without_a_fingerprint_is_refused(tmp_path):
    _approve(tmp_path)
    path = tap.approval_path(CLIENT, str(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["motions"][MOTION].pop("fingerprint")
    path.write_text(json.dumps(payload), encoding="utf-8")
    unlocked, problems = tap.title_gate_status(
        CLIENT, fingerprint=FP, motion_id=MOTION, review_dir=str(tmp_path))
    assert unlocked is False and "no fingerprint" in problems[0]


def test_an_unreadable_approval_is_absent_not_open(tmp_path):
    tap.approval_path(CLIENT, str(tmp_path)).parent.mkdir(
        parents=True, exist_ok=True)
    tap.approval_path(CLIENT, str(tmp_path)).write_text("{not json",
                                                        encoding="utf-8")
    assert tap.load_title_approval(CLIENT, str(tmp_path)) is None
    assert tap.title_gate_status(CLIENT, fingerprint=FP, motion_id=MOTION,
                                 review_dir=str(tmp_path))[0] is False


# ===== revocation is stamped, never deleted =============================== #
def test_revocation_stamps_and_preserves_the_audit_event(tmp_path):
    _approve(tmp_path)
    tap.revoke_title_approval(CLIENT, revoked_at="2026-08-07T00:00:00+00:00",
                              review_dir=str(tmp_path))
    payload = tap.load_title_approval(CLIENT, str(tmp_path))
    assert payload["revoked_at"]
    assert payload["motions"][MOTION]["titles"] == ["Contracting Officer"]
    assert tap.title_gate_status(CLIENT, fingerprint=FP, motion_id=MOTION,
                                 review_dir=str(tmp_path))[0] is False


def test_one_motion_can_be_revoked_without_the_others(tmp_path):
    _approve(tmp_path, motion_id="m1")
    _approve(tmp_path, motion_id="m2")
    tap.revoke_title_approval(CLIENT, motion_id="m1",
                              revoked_at="2026-08-07T00:00:00+00:00",
                              review_dir=str(tmp_path))
    assert tap.title_gate_status(CLIENT, fingerprint=FP, motion_id="m1",
                                 review_dir=str(tmp_path))[0] is False
    assert tap.title_gate_status(CLIENT, fingerprint=FP, motion_id="m2",
                                 review_dir=str(tmp_path))[0] is True


# ===== THE CONTRACT PROPERTIES ============================================ #
def test_target_gate_status_is_not_modified_by_this_change():
    """PROPERTY 1. The documented "one gate derivation" keeps exactly its
    two legs. Adding a third would re-lock the two clients holding valid
    Target approvals today, and every future client, until a title set
    exists."""
    import inspect

    from agents import review

    source = inspect.getsource(review.target_gate_status)
    assert "title" not in source.casefold()
    assert "load_target_approval" in source
    assert "assess_approval_for_release" in source


def test_a_closed_target_door_still_closes_everything(tmp_path, monkeypatch):
    """PROPERTY 2. This gate never unlocks outreach from a new path. Even a
    perfectly approved title set cannot open a closed Target door."""
    _approve(tmp_path)
    monkeypatch.setattr("agents.review.target_gate_status",
                        lambda *a, **k: (False, ["Target is locked"]))
    unlocked, problems = tap.supply_may_run(
        CLIENT, motion_id=MOTION, fingerprint=FP, review_dir=str(tmp_path))
    assert unlocked is False
    assert problems == ["Target is locked"]


def test_both_gates_must_hold_for_supply_to_run(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.review.target_gate_status",
                        lambda *a, **k: (True, []))
    assert tap.supply_may_run(CLIENT, motion_id=MOTION, fingerprint=FP,
                              review_dir=str(tmp_path))[0] is False
    _approve(tmp_path)
    assert tap.supply_may_run(CLIENT, motion_id=MOTION, fingerprint=FP,
                              review_dir=str(tmp_path))[0] is True


def test_no_agent_path_records_an_approval_on_its_own_initiative():
    """PROPERTY 3. record_title_approval is a persistence owner for a human
    act. Nothing in the targeting lane may call it, exactly as nothing calls
    the Assess or Target approval writers."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "agents" / "golden_press"
    for module in root.glob("*.py"):
        if module.name == "title_approval.py":
            continue
        source = module.read_text(encoding="utf-8")
        assert "record_title_approval" not in source, module.name
