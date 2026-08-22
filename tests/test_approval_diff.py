"""Diff-aware Assess reapproval over real approvals and evidence files."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.approval import (  # noqa: E402
    approval_evidence_diff,
    approve_assess_results,
    load_assess_approval,
)


class _Profile:
    def __init__(self, terms=("network monitoring",)):
        self.terms = list(terms)

    def is_populated(self) -> bool:
        return True

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "client_name": "Acme Federal",
            "capability_terms": {"core": self.terms},
            "naics_boundary": ["541512"],
        }


def _notice(source_id, title, deadline="2026-09-01", agency="DHS"):
    return {
        "source": "sam.gov",
        "source_id": source_id,
        "title": title,
        "agency": agency,
        "response_deadline": deadline,
        "naics_code": "541512",
    }


def _seed(tmp_path, monkeypatch, profile=None):
    review = tmp_path / "review"
    cleaned = tmp_path / "cleaned"
    review.mkdir()
    cleaned.mkdir()
    (review / "acme_federal.review.json").write_text(json.dumps({
        "client_name": "Acme Federal",
        "status": "approved",
        "search_scope": {"all": True},
    }))
    sweep = {
        "client": "Acme Federal",
        "generated_at": "2026-07-11T06:00:00",
        "results": {
            "sam.gov": [
                _notice("P1", "Network visibility"),
                _notice("P2", "SOC tooling"),
            ],
            "triage": {
                "P1": {"verdict": "monitor", "reason": "early fit"},
                "P2": {"verdict": "discard", "reason": "weak fit"},
            },
        },
    }
    path = cleaned / "searches_acme_federal.json"
    path.write_text(json.dumps(sweep, indent=2))
    monkeypatch.setattr(
        "tools.capability.load_profile", lambda _name: profile or _Profile())
    return str(review), str(path), sweep


def test_approval_stamps_manifest_and_clean_diff(tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    stored = load_assess_approval("Acme Federal", review_dir=review_dir)
    manifest = stored["evidence_manifest"]
    assert set(manifest["notices"]) == {"P1", "P2"}
    assert manifest["notices"]["P1"]["verdict"] == "monitor"
    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"]
    assert diff["summary"] == "no evidence changes detected"
    assert diff["unchanged_count"] == 2
    assert not diff["entered"] and not diff["changed"]


def test_diff_names_entered_left_changed_and_verdict_flips(
        tmp_path, monkeypatch):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)

    sweep["results"]["sam.gov"] = [
        _notice("P1", "Network visibility", deadline="2026-08-15"),
        _notice("P3", "Packet capture refresh", agency="CBP"),
    ]
    sweep["results"]["triage"] = {
        "P1": {"verdict": "pursue", "reason": "early fit"},
        "P3": {"verdict": "monitor", "reason": "new lead"},
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"]
    assert [item["id"] for item in diff["entered"]] == ["P3"]
    assert diff["entered"][0]["title"] == "Packet capture refresh"
    assert [item["id"] for item in diff["left"]] == ["P2"]
    changed = diff["changed"][0]
    assert changed["id"] == "P1"
    assert set(changed["fields"]) == {"response_deadline", "verdict"}
    assert changed["was"]["verdict"] == "monitor"
    assert changed["verdict"] == "pursue"
    assert "1 notice(s) entered" in diff["summary"]
    assert "1 notice(s) left" in diff["summary"]
    assert "1 notice(s) changed" in diff["summary"]


def test_verdict_only_change_is_never_counted_as_unchanged(
        tmp_path, monkeypatch):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    sweep["results"]["triage"]["P1"]["verdict"] = "pursue"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert [item["id"] for item in diff["changed"]] == ["P1"]
    assert diff["changed"][0]["fields"] == ["verdict"]
    assert diff["changed"][0]["was"]["verdict"] == "monitor"
    assert diff["changed"][0]["verdict"] == "pursue"
    assert diff["unchanged_count"] == 1


def test_triage_rationale_only_change_is_reported_as_content(
        tmp_path, monkeypatch):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    sweep["results"]["triage"]["P1"]["reason"] = "new operator evidence"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert [item["id"] for item in diff["changed"]] == ["P1"]
    assert diff["changed"][0]["fields"] == ["triage_reason"]
    assert diff["changed"][0]["was"]["triage_reason"] == "early fit"
    assert diff["changed"][0]["triage_reason"] == "new operator evidence"


def test_profile_change_surfaces_as_dotted_paths(tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    monkeypatch.setattr(
        "tools.capability.load_profile",
        lambda _name: _Profile(terms=["packet capture"]),
    )
    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"]
    assert any(
        path.startswith("capability_terms.core")
        for path in diff["profile_changed"]
    )
    assert "profile field(s) changed" in diff["summary"]


def test_legacy_approval_reports_unavailable_and_never_blocks(
        tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    from agents.assess.approval import (
        approval_path,
        assess_approval_for_release,
        current_assess_binding,
    )

    binding = current_assess_binding(
        "Acme Federal", review_dir=review_dir)
    with open(
        approval_path("Acme Federal", review_dir=review_dir),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump({
            "client": "Acme Federal",
            "approved_at": "2026-07-10T20:00:00+00:00",
            "binding": binding,
        }, handle)
    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"] is False
    assert "predates evidence manifests" in diff["why"]

    _approval, status, problems = assess_approval_for_release(
        "Acme Federal", review_dir=review_dir)
    assert status == "approved" and problems == []


def test_manifest_failure_never_blocks_approval(tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "agents.assess.approval.evidence_manifest",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("manifest failed")),
    )
    stored = approve_assess_results(
        "Acme Federal", review_dir=review_dir)
    assert "evidence_manifest" not in stored

    from agents.assess.approval import assess_approval_for_release
    _approval, status, problems = assess_approval_for_release(
        "Acme Federal", review_dir=review_dir)
    assert status == "approved" and problems == []


@pytest.mark.parametrize("broken", [
    {"version": 1, "notices": {"P1": ["not", "an", "object"]},
     "profile": {}},
    {"version": 1, "notices": {}, "profile": ["not", "an", "object"]},
    {"version": 1, "notices": {}},
])
def test_malformed_stored_manifest_degrades_to_unavailable(
        tmp_path, monkeypatch, broken):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    stored = approve_assess_results(
        "Acme Federal", review_dir=review_dir)
    stored["evidence_manifest"] = broken
    from agents.assess.approval import approval_path
    with open(
        approval_path("Acme Federal", review_dir=review_dir),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(stored, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"] is False
    assert "malformed" in diff["why"]


def test_wrong_client_approval_never_exposes_a_diff(tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    stored = approve_assess_results(
        "Acme Federal", review_dir=review_dir)
    stored["client"] = "Acme-Federal"
    from agents.assess.approval import approval_path
    with open(
        approval_path("Acme Federal", review_dir=review_dir),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(stored, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"] is False
    assert "different client" in diff["why"]


@pytest.mark.parametrize("sam_rows", [
    {},
    {"P1": _notice("P1", "wrong collection shape")},
    [_notice("P1", "first"), _notice("P1", "duplicate")],
])
def test_malformed_current_sam_population_never_produces_a_misleading_diff(
        tmp_path, monkeypatch, sam_rows):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    sweep["results"]["sam.gov"] = sam_rows
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"] is False
    assert "current evidence unavailable" in diff["why"]


def test_falsey_malformed_results_never_look_like_an_empty_sweep(
        tmp_path, monkeypatch):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    sweep["results"] = []
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"] is False
    assert "current evidence unavailable" in diff["why"]


def test_malformed_current_triage_never_produces_a_misleading_diff(
        tmp_path, monkeypatch):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    sweep["results"]["triage"]["P1"] = ["not", "an", "object"]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    diff = approval_evidence_diff("Acme Federal", review_dir=review_dir)
    assert diff["available"] is False
    assert "current evidence unavailable" in diff["why"]


def test_approval_builds_binding_and_manifest_from_one_scope_snapshot(
        tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    import agents.assess.approval as approval_module
    original = approval_module._scope_and_sweep
    calls = []

    def one_read(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) > 1:
            raise AssertionError("approval re-read scope during one snapshot")
        return original(*args, **kwargs)

    monkeypatch.setattr(approval_module, "_scope_and_sweep", one_read)
    stored = approve_assess_results(
        "Acme Federal", review_dir=review_dir)
    assert len(calls) == 1
    assert stored["binding"]["scope_designator"] == "all"
    assert stored["evidence_manifest"]["scope_designator"] == "all"


def test_endpoint_serves_diff_using_canonical_client_identity(
        tmp_path, monkeypatch):
    review_dir, path, sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    sweep["results"]["sam.gov"].append(
        _notice("P9", "New RFI", agency="TSA"))
    sweep["results"]["triage"]["P9"] = {
        "verdict": "monitor", "reason": "new lead"}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sweep, handle)

    pytest.importorskip("flask")
    import ui.server as server
    monkeypatch.setattr(server, "REVIEW_DIR", review_dir)
    response = server.app.test_client().get(
        "/api/client/acme_federal/approval-diff")
    assert response.status_code == 200
    diff = response.get_json()
    assert diff["available"]
    assert [item["id"] for item in diff["entered"]] == ["P9"]


def test_endpoint_rejects_cross_client_review_packet(tmp_path, monkeypatch):
    review_dir, _path, _sweep = _seed(tmp_path, monkeypatch)
    approve_assess_results("Acme Federal", review_dir=review_dir)
    packet = os.path.join(review_dir, "acme_federal.review.json")
    with open(packet, "w", encoding="utf-8") as handle:
        json.dump({
            "client_name": "Different Company",
            "status": "approved",
            "search_scope": {"all": True},
        }, handle)

    pytest.importorskip("flask")
    import ui.server as server
    monkeypatch.setattr(server, "REVIEW_DIR", review_dir)
    response = server.app.test_client().get(
        "/api/client/acme_federal/approval-diff")
    assert response.status_code == 200
    diff = response.get_json()
    assert diff["available"] is False
    assert "identity" in diff["why"]
