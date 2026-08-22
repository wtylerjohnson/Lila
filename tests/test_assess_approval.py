"""Operational Assess approval is immutable-run-bound and fail-closed."""

from __future__ import annotations

import json

import pytest

from agents.assess.approval import (
    AssessApprovalError, approve_assess_results, assess_approval_for_release,
    current_assess_binding,
)
from tools.capability import CapabilityTerms, ClientProfile


CLIENT = "Testco"


def _seed(tmp_path, monkeypatch):
    import agents.review as review
    import tools.capability as capability

    review_dir = tmp_path / "data" / "review"
    cleaned = tmp_path / "data" / "cleaned"
    clients = tmp_path / "clients"
    review_dir.mkdir(parents=True)
    cleaned.mkdir(parents=True)
    (clients / "testco").mkdir(parents=True)
    (review_dir / "testco.review.json").write_text(json.dumps({
        "client_name": CLIENT, "status": "approved", "search_scope": {"all": True},
    }), encoding="utf-8")
    sweep = cleaned / "searches_testco.json"
    sweep.write_text(json.dumps({"client": CLIENT, "results": {"sam.gov": []}}),
                     encoding="utf-8")
    profile = ClientProfile(
        client_name=CLIENT,
        capability_terms=CapabilityTerms(
            core=["secure video"], adjacent=["command center"], excluded=[]),
        named_competitors_and_incumbents=["Prime A"],
        mission_components=["operations"], naics_boundary=["334310"],
    )
    profile_path = clients / "testco" / "profile.json"
    profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    monkeypatch.setattr(review, "REVIEW_DIR", str(review_dir))
    monkeypatch.setattr(capability, "CLIENTS_DIR", str(clients))
    return review_dir, sweep, profile_path


def test_current_approval_survives_json_formatting_only(tmp_path, monkeypatch):
    review_dir, sweep, _profile = _seed(tmp_path, monkeypatch)
    approved = approve_assess_results(CLIENT, review_dir=str(review_dir))
    assert set(approved["binding"]) == {
        "version", "scope_designator", "sweep_artifact", "sweep_sha256",
        "profile_sha256",
    }
    payload = json.loads(sweep.read_text(encoding="utf-8"))
    sweep.write_text(json.dumps(payload, indent=4, sort_keys=True), encoding="utf-8")
    got, status, problems = assess_approval_for_release(
        CLIENT, review_dir=str(review_dir))
    assert status == "approved" and not problems and got == approved
    assert not list(review_dir.glob("*.tmp"))


@pytest.mark.parametrize("changed", ["scope", "sweep", "profile"])
def test_scope_sweep_or_complete_profile_change_invalidates(
        tmp_path, monkeypatch, changed):
    review_dir, sweep, profile_path = _seed(tmp_path, monkeypatch)
    approve_assess_results(CLIENT, review_dir=str(review_dir))
    if changed == "scope":
        packet = json.loads((review_dir / "testco.review.json").read_text())
        packet["search_scope"] = {"agencies": ["DoD"]}
        (review_dir / "testco.review.json").write_text(json.dumps(packet))
        (sweep.parent / "searches_testco.agency_dod.json").write_text(
            sweep.read_text(encoding="utf-8"), encoding="utf-8")
    elif changed == "sweep":
        payload = json.loads(sweep.read_text())
        payload["results"]["new_evidence"] = [{"id": "N1"}]
        sweep.write_text(json.dumps(payload))
    else:
        payload = json.loads(profile_path.read_text())
        payload["named_competitors_and_incumbents"].append("Prime B")
        profile_path.write_text(json.dumps(payload))

    approval, status, problems = assess_approval_for_release(
        CLIENT, review_dir=str(review_dir))
    assert approval is None and status == "invalid" and problems
    assert any(changed in " ".join(problems).lower()
               for changed in ([changed] if changed != "scope" else ["scope"]))


def test_missing_legacy_and_corrupt_approval_fail_closed(tmp_path, monkeypatch):
    review_dir, _sweep, _profile = _seed(tmp_path, monkeypatch)
    assert assess_approval_for_release(
        CLIENT, review_dir=str(review_dir))[1] == "missing"
    path = review_dir / "testco.assess_approval.json"
    path.write_text(json.dumps({"client": CLIENT, "approved_at": "now"}))
    _, status, problems = assess_approval_for_release(
        CLIENT, review_dir=str(review_dir))
    assert status == "invalid" and any("legacy/unbound" in p for p in problems)
    path.write_text("{")
    assert assess_approval_for_release(
        CLIENT, review_dir=str(review_dir))[1] == "invalid"


def test_approval_refuses_missing_current_inputs(tmp_path, monkeypatch):
    review_dir, sweep, _profile = _seed(tmp_path, monkeypatch)
    sweep.unlink()
    with pytest.raises(AssessApprovalError, match="cannot approve"):
        approve_assess_results(CLIENT, review_dir=str(review_dir))


def test_binding_hashes_entire_validated_profile():
    profile = ClientProfile(
        client_name=CLIENT,
        capability_terms=CapabilityTerms(core=["video"], excluded=[]),
        named_competitors_and_incumbents=["A"], naics_boundary=["334310"],
    )
    first = current_assess_binding(
        CLIENT, review_dir="/does/not/exist", sweep_path="searches_testco.json",
        sweep={"results": {}}, profile=profile)
    second = current_assess_binding(
        CLIENT, review_dir="/does/not/exist", sweep_path="searches_testco.json",
        sweep={"results": {}},
        profile=profile.model_copy(update={
            "named_competitors_and_incumbents": ["A", "B"]}),
    )
    assert first["profile_sha256"] != second["profile_sha256"]


# ── Cycle 3 review finding (2026-07-12): plain re-approval must not silently
# revoke a standing partial-release grant. Absent (None) carries an unrevoked
# grant forward verbatim; explicit False keeps its revoke meaning; a revoked
# approval never resurrects its grant. Downstream run assembly still binds
# the carried rows/digest/run id against the CURRENT strict run, so a
# drifted grant stays inert.

def _graft_grant(review_dir, **extra):
    from agents.assess.approval import approval_path
    path = approval_path(CLIENT, review_dir=str(review_dir))
    stored = json.loads(open(path, encoding="utf-8").read())
    stored.update({
        "partial_release_approved": True,
        "partial_release_run_id": "assess:v1:granted",
        "partial_release_blockers": [{"source": "sam_census"}],
        "partial_release_blockers_sha256": "abc123",
        **extra,
    })
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle)
    return path


def test_absent_partial_axis_carries_a_standing_grant_verbatim(
        tmp_path, monkeypatch):
    review_dir, _sweep, _profile = _seed(tmp_path, monkeypatch)
    approve_assess_results(CLIENT, review_dir=str(review_dir))
    path = _graft_grant(review_dir)
    carried = approve_assess_results(
        CLIENT, review_dir=str(review_dir), partial_release_approved=None)
    assert carried["partial_release_approved"] is True
    assert carried["partial_release_run_id"] == "assess:v1:granted"
    assert carried["partial_release_blockers"] == [{"source": "sam_census"}]
    assert carried["partial_release_blockers_sha256"] == "abc123"
    on_disk = json.loads(open(path, encoding="utf-8").read())
    assert on_disk["partial_release_approved"] is True
    assert on_disk["partial_release_run_id"] == "assess:v1:granted"


def test_explicit_false_still_revokes_the_partial_grant(
        tmp_path, monkeypatch):
    review_dir, _sweep, _profile = _seed(tmp_path, monkeypatch)
    approve_assess_results(CLIENT, review_dir=str(review_dir))
    path = _graft_grant(review_dir)
    dropped = approve_assess_results(
        CLIENT, review_dir=str(review_dir), partial_release_approved=False)
    assert dropped["partial_release_approved"] is False
    assert "partial_release_run_id" not in dropped
    on_disk = json.loads(open(path, encoding="utf-8").read())
    assert on_disk["partial_release_approved"] is False


def test_revoked_approval_never_carries_its_grant(tmp_path, monkeypatch):
    review_dir, _sweep, _profile = _seed(tmp_path, monkeypatch)
    approve_assess_results(CLIENT, review_dir=str(review_dir))
    _graft_grant(review_dir, revoked_at="2026-07-12T00:00:00+00:00")
    fresh = approve_assess_results(
        CLIENT, review_dir=str(review_dir), partial_release_approved=None)
    assert fresh["partial_release_approved"] is False
    assert "partial_release_run_id" not in fresh


def test_default_call_still_drops_the_grant(tmp_path, monkeypatch):
    """Only an explicit None opts into carry; the keyword default stays
    False so non-endpoint callers keep their pre-change behavior."""
    review_dir, _sweep, _profile = _seed(tmp_path, monkeypatch)
    approve_assess_results(CLIENT, review_dir=str(review_dir))
    _graft_grant(review_dir)
    fresh = approve_assess_results(CLIENT, review_dir=str(review_dir))
    assert fresh["partial_release_approved"] is False
