"""Control-room tests: stage detection, gate guard, file-serving jail.

Offline — temp data dirs, Flask test client. No subprocesses launched.
"""

from __future__ import annotations

import html
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

import ui.server as srv  # noqa: E402


def _bind_run_workstation(monkeypatch, client_name="Testco", search_scope=None):
    """Give a focused /api/run test one unambiguous execution owner."""
    from agents.workstations import canonical_scope, workstation_id

    scope = canonical_scope(search_scope)
    current = SimpleNamespace(
        is_legacy_current=True,
        ref=SimpleNamespace(id=workstation_id(scope), scope=scope),
    )
    catalog = SimpleNamespace(workstations=(current,))
    monkeypatch.setattr(
        srv, "_workstation_catalog_for_slug",
        lambda _slug: (client_name, catalog),
    )


def _mk_client(root, slug="acme_federal", name="Acme Federal", status="approved"):
    review = os.path.join(root, "data", "review")
    os.makedirs(review, exist_ok=True)
    with open(os.path.join(review, f"{slug}.review.json"), "w") as f:
        json.dump({"client_name": name, "status": status, "reviewer_note": ""}, f)
    return review


def test_stage_detection_progression(tmp_path):
    root = str(tmp_path)
    review = _mk_client(root)
    s = srv.client_state("acme_federal", review)
    assert s["stages"]["approved"] is True
    assert s["next_step"] == "searched"

    os.makedirs(os.path.join(root, "data", "cleaned"), exist_ok=True)
    open(os.path.join(root, "data", "cleaned", "searches_acme_federal.json"), "w").write("{}")
    s = srv.client_state("acme_federal", review)
    assert s["stages"]["searched"] is True
    assert s["next_step"] == "qualified"

    open(os.path.join(review, "acme_federal.qualify.json"), "w").write("{}")
    open(os.path.join(review, "acme_federal.contacts.json"), "w").write("{}")
    os.makedirs(os.path.join(root, "data", "reports"), exist_ok=True)
    open(os.path.join(root, "data", "reports", "acme_federal.teaser.md"), "w").write("# t")
    s = srv.client_state("acme_federal", review)
    assert s["next_step"] == "complete"


def test_rejected_strategy_blocks_progress(tmp_path):
    review = _mk_client(str(tmp_path), status="rejected")
    s = srv.client_state("acme_federal", review)
    assert s["next_step"] == "revise Analyst Layer (strategy rejected)"


def test_run_endpoint_refuses_unapproved(monkeypatch, tmp_path):
    # A pending client must not be able to launch spend-incurring steps.
    from agents import review as review_mod
    from agents.decisions.schemas import IntakeStrategy

    review_dir = str(tmp_path)
    monkeypatch.setattr(review_mod, "REVIEW_DIR", review_dir)
    monkeypatch.setattr(srv, "REVIEW_DIR", review_dir)
    strategy = IntakeStrategy(
        client_name="Pending Co", pursuit_strategy="s", confidence=0.5, review_gate="g"
    )
    packet = review_mod.ReviewPacket(client_name="Pending Co", strategy=strategy)
    os.makedirs(review_dir, exist_ok=True)
    with open(os.path.join(review_dir, "pending_co.review.json"), "w") as f:
        f.write(packet.model_dump_json())
    _bind_run_workstation(monkeypatch, "Pending Co")

    client = srv.app.test_client()
    r = client.post("/api/run", json={"client_name": "Pending Co", "step": "searches",
                                      "args": {"posted_from": "01/01/2026", "posted_to": "02/01/2026"}})
    assert r.status_code == 409
    assert "not approved" in r.get_json()["error"]


@pytest.mark.parametrize("step,args", [
    ("report", {"kind": "capture_brief"}),
    ("views", {}),
    ("agency_report", {"agency": "DoD"}),
])
def test_run_endpoint_refuses_release_capable_assess_without_current_approval(
        monkeypatch, step, args):
    _bind_run_workstation(monkeypatch)
    # External-slot law: the capture-brief press is flag-gated off by
    # default; this test's subject is the release gate, so raise the flag
    monkeypatch.setattr(srv, "CAPTURE_BRIEF_ENABLED", True)
    monkeypatch.setattr(
        srv, "load_packet",
        lambda _client, **_kwargs: SimpleNamespace(
            status=srv.ReviewStatus.APPROVED, search_scope=None))
    monkeypatch.setattr(
        srv, "_assess_release_gate",
        lambda _client: (None, "invalid", ["Assess sweep evidence changed"]))
    launched = []
    monkeypatch.setattr(
        srv, "start_job",
        lambda *_args, **_kwargs: launched.append(1) or "should-not-start")

    r = srv.app.test_client().post("/api/run", json={
        "client_name": "Testco", "step": step, "args": args})
    assert r.status_code == 409
    assert not launched
    assert r.get_json()["problems"] == ["Assess sweep evidence changed"]


def test_capture_brief_press_is_retired_by_default():
    """External-slot law (2026-08-23): the capture-brief press refuses
    flag-off before any packet or gate work. LILA_CAPTURE_BRIEF=1 resurrects
    that internal compatibility press, not a competing external product."""
    r = srv.app.test_client().post("/api/run", json={
        "client_name": "Anyco", "step": "report",
        "args": {"kind": "capture_brief"}})
    assert r.status_code == 403
    assert "operator-locked Federal Market Map" in r.get_json()["error"]


def test_legacy_cockpit_is_curtained_and_status_line_is_honest():
    """Legacy workstations stay curtained and the LILA product owns release."""
    html = open(os.path.join(os.path.dirname(srv.__file__),
                             "index.html"), encoding="utf-8").read()
    assert "const SHOW_LEGACY_WORKSTATION = false" in html
    # 2026-07-25: the standalone approve press is retired; the Target
    # door performs the Assess sign-off and the open in one act.
    assert "/api/review/assess-approve" in html
    assert "open Target" in html
    assert "if (SHOW_LEGACY_WORKSTATION) apBar.append(ap, apMsg);" not in html
    assert "chain-status" in html
    assert "row.last_press" in html
    assert "if (d.final_product) renderPreview(d.final_product);" in html
    assert "Release complete LILA bundle" in html
    assert "LILA_LEGACY_WORKSTATION" in open(os.path.join(
        os.path.dirname(srv.__file__), "server.py"), encoding="utf-8").read()


def test_lila_bundle_is_canonical_command_center_deliverable():
    html = open(os.path.join(os.path.dirname(srv.__file__),
                             "index.html"), encoding="utf-8").read()
    assert "const product = d.final_product || null;" in html
    assert "clientReady: !!(product && product.qa_pass)" in html
    assert "if (d.final_product) renderPreview(d.final_product);" in html
    assert "/client/${d.slug}/download/lila.html" in html
    assert "/client/${d.slug}/download/lila-bundle.zip" in html
    assert "Download complete bundle" in html
    assert ("re-sweep → relevance → compose-refresh → award re-pull "
            "→ verification → press → delta") in html
    assert "runJob('refresh_press', {}, card, run)" in html
    assert "Refresh internal Signal Board" in html


def test_command_center_roundtrips_and_previews_presentation_name(monkeypatch):
    from tools import capability

    monkeypatch.setattr(
        capability, "client_display_name",
        lambda client_name: "Mark43" if client_name == "mark43" else client_name,
    )
    assert srv._safe_client_display_name("mark43") == "Mark43"

    def broken_display_name(_client_name):
        raise ValueError("broken optional presentation metadata")

    monkeypatch.setattr(capability, "client_display_name", broken_display_name)
    assert srv._safe_client_display_name("mark43") == "mark43"

    html = open(os.path.join(os.path.dirname(srv.__file__),
                             "index.html"), encoding="utf-8").read()
    assert 'id="ob-display-name"' in html
    assert "q('#ob-display-name').value = p.display_name || '';" in html
    assert "if (displayName) body.profile.display_name = displayName;" in html
    assert "esc(d.display_name || d.client_name)" in html


def test_run_endpoint_allows_current_approval_and_leaves_teaser_unchanged(
        monkeypatch):
    _bind_run_workstation(monkeypatch)
    monkeypatch.setattr(
        srv, "load_packet",
        lambda _client, **_kwargs: SimpleNamespace(
            status=srv.ReviewStatus.APPROVED, search_scope=None))
    status = ["missing"]
    monkeypatch.setattr(
        srv, "_assess_release_gate",
        lambda _client: (None, status[0], ["not approved"]))
    calls = []
    monkeypatch.setattr(
        srv, "start_job",
        lambda step, client, args: calls.append((step, args)) or "job-1")

    client = srv.app.test_client()
    teaser = client.post("/api/run", json={
        "client_name": "Testco", "step": "report",
        "args": {"kind": "teaser"}})
    assert teaser.status_code == 200
    status[0] = "approved"
    blocked = client.post("/api/run", json={
        "client_name": "Testco", "step": "views", "args": {}})
    assert blocked.status_code == 409
    assert "complete Targeting Review" in blocked.get_json()["error"]
    monkeypatch.setattr(
        srv, "_client_targets_payload",
        lambda _slug: ({"targeting_readiness": {"ready": True, "problems": []}}, 200))
    release = client.post("/api/run", json={
        "client_name": "Testco", "step": "views", "args": {}})
    assert release.status_code == 200
    assert calls == [
        ("report", {"kind": "teaser", "workstation_id": "all"}),
        ("views", {"workstation_id": "all"}),
    ]


def test_refresh_endpoint_binds_stateful_adapter_and_one_job_lock(monkeypatch):
    """The UI refresh is the persisted C3 handshake, not a bare runner call."""
    _bind_run_workstation(monkeypatch, client_name="Mark43")
    monkeypatch.setattr(
        srv, "load_packet",
        lambda _client, **_kwargs: SimpleNamespace(
            status=srv.ReviewStatus.APPROVED, search_scope=None))
    monkeypatch.setattr(srv, "JOBS", {})
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            started.append({"target": target, "args": args, "daemon": daemon})

        def start(self):
            started[-1]["started"] = True

    monkeypatch.setattr(srv.threading, "Thread", FakeThread)

    client = srv.app.test_client()
    response = client.post("/api/run", json={
        "client_name": "Mark43", "step": "refresh_press", "args": {}})

    assert response.status_code == 200, response.get_json()
    job_id = response.get_json()["job_id"]
    assert len(started) == 1
    thread_args = started[0]["args"]
    assert started[0]["target"] is srv._run_job
    assert started[0]["daemon"] is True
    assert started[0]["started"] is True
    assert thread_args[0] == job_id
    assert thread_args[1] == [
        srv.sys.executable, "run_assessment.py", "--client", "Mark43",
        "--refresh"]
    assert thread_args[2:4] == ("Mark43", "all")
    assert thread_args[6] is False
    assert srv.JOBS[job_id]["client"] == "Mark43"
    assert srv.JOBS[job_id]["step"] == "refresh_press"

    refused = client.post("/api/run", json={
        "client_name": "Mark43", "step": "refresh_press", "args": {}})
    assert refused.status_code == 409
    assert refused.get_json()["running_job_id"] == job_id
    assert len(started) == 1


def test_assess_approval_endpoint_writes_bound_artifact_atomically_and_revokes(
        tmp_path, monkeypatch):
    import agents.assess.approval as approval

    binding = {
        "version": 1, "scope_designator": "all",
        "sweep_artifact": "searches_testco.json",
        "sweep_sha256": "sweep-a", "profile_sha256": "profile-a",
    }
    monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(
        approval, "current_assess_binding",
        lambda *_args, **_kwargs: binding)
    client = srv.app.test_client()
    approved = client.post("/api/review/assess-approve", json={
        "client_name": "Testco", "note": "reviewed"})
    assert approved.status_code == 200
    payload = json.loads(
        (tmp_path / "testco.assess_approval.json").read_text())
    assert payload["binding"] == binding and payload["note"] == "reviewed"
    assert not list(tmp_path.glob("*.tmp"))
    revoked = client.post("/api/review/assess-approve", json={
        "client_name": "Testco", "revoke": True})
    revoked_body = revoked.get_json()
    assert revoked_body["review_approved"] is False
    assert revoked_body["refresh_note"].startswith("absent")
    revoked_payload = json.loads(
        (tmp_path / "testco.assess_approval.json").read_text())
    assert revoked_payload["revoked_at"]
    assert list((tmp_path / ".history" / "testco.assess_approval.json")
                .glob("*.json"))


def test_assess_run_endpoint_returns_validated_review_ledger(tmp_path, monkeypatch):
    import agents.assess.approval as approval
    import agents.assess.ledger as ledger
    from agents.assess.ledger import persist_assess_run
    from tests.test_assess_ledger import BINDING, _build, _sweep

    run, diagnostics, posting_index = _build(_sweep())
    persist_assess_run(
        run, BINDING, diagnostics, posting_index, state_dir=tmp_path)
    monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(tmp_path))
    monkeypatch.setattr(
        approval, "current_assess_binding",
        lambda *_args, **_kwargs: BINDING)
    monkeypatch.setattr(
        ledger, "assess_projection_input_manifest",
        lambda *_args, **_kwargs: {})

    # The client-detail contract stays compact; only the dedicated endpoint
    # pays the cost of returning every evidence record.
    summary = srv._assess_ledger_snapshot("Testco")
    assert summary["state"] == "current"
    assert summary["as_of"] == run.as_of.isoformat()
    assert "run" not in summary
    assert summary["live_records"] == 3
    assert summary["live_actionable"] == 0
    assert summary["requirement_review_candidates"] == 0
    assert summary["requirement_reviews_approved"] == 0
    assert summary["coverage_gaps"] > 0
    assert summary["coverage_warnings"] == len(summary["coverage_issues"])
    assert summary["coverage_warnings"] >= 9

    response = srv.app.test_client().get(
        "/api/assess/run?client_name=Testco")
    assert response.status_code == 200
    body = response.get_json()
    assert body["exists"] is True
    assert body["run_id"] == run.run_id
    assert body["live_records"] == 3
    assert body["live_classifications"]["unscreened"] == 2
    assert body["horizon_theses"] == 0
    assert body["partner_paths"] == 0
    assert body["run"]["requires_human_review"] is True

    monkeypatch.setattr(
        approval, "current_assess_binding",
        lambda *_args, **_kwargs: {**BINDING, "profile_sha256": "new-profile"})
    stale = srv._assess_ledger_snapshot("Testco")
    assert stale["exists"] is False and stale["state"] == "stale"
    assert stale["stale"] is True and "run" not in stale
    assert "not shown as current" in stale["diagnostics"][0]

    missing = srv.app.test_client().get(
        "/api/assess/run?client_name=Nobody")
    assert missing.status_code == 404
    missing_body = missing.get_json()
    assert missing_body["exists"] is False
    assert missing_body["state"] == "missing"
    assert "Assessment generation remains available" in \
        missing_body["diagnostics"][0]


def test_assess_ledger_becomes_stale_when_actionable_deadline_arrives(
        tmp_path, monkeypatch):
    from datetime import date

    import agents.assess.approval as approval
    import agents.assess.ledger as ledger
    from agents.assess.ledger import persist_assess_run
    from tests.test_assess_ledger import (
        BINDING, _build_with_requirement_review, _depth_record, _sweep,
    )

    searches = _sweep()
    searches["results"]["dossiers"] = {"records": [_depth_record()]}
    run, diagnostics, posting_index = _build_with_requirement_review(searches)
    actionable = next(
        record for record in run.live.records if record.notice_id == "N1")
    assert actionable.response_deadline == date(2026, 8, 1)
    persist_assess_run(
        run, BINDING, diagnostics, posting_index, state_dir=tmp_path)
    monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(tmp_path))
    monkeypatch.setattr(
        approval, "current_assess_binding",
        lambda *_args, **_kwargs: BINDING)
    monkeypatch.setattr(
        ledger, "assess_projection_input_manifest",
        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(srv, "_utc_today", lambda: date(2026, 8, 1))

    snapshot = srv._assess_ledger_snapshot("Testco")
    assert snapshot["exists"] is False
    assert snapshot["state"] == "stale" and snapshot["stale"] is True
    assert "N1" in snapshot["diagnostics"][0]
    assert "Assessment generation remains available" in snapshot["diagnostics"][0]


def test_assess_ledger_unavailable_is_visible_without_raising(monkeypatch):
    import agents.assess.ledger as ledger

    def fail_load(*_args, **_kwargs):
        raise PermissionError("simulated unreadable sidecar")

    monkeypatch.setattr(ledger, "load_current_assess_run", fail_load)
    response = srv.app.test_client().get(
        "/api/assess/run?client_name=Testco")
    assert response.status_code == 404
    body = response.get_json()
    assert body["exists"] is False and body["state"] == "unavailable"
    assert body["unavailable"] is True
    assert "Assessment generation remains available" in body["diagnostics"][0]


def test_requirement_review_endpoint_returns_exact_current_source_span(monkeypatch):
    excerpt = (
        "The contractor shall provide an enterprise packet capture platform.")
    snapshot = {
        "exists": True,
        "run_id": "assess:v1:exact-span",
        "run": {"live": {"records": [{
            "notice_id": "N-EXACT", "title": "Packet capture platform",
            "agency": "DHS / CISA", "response_deadline": "2026-08-21",
            "classification": "unscreened", "recommendation": "research",
            "requirement_excerpt": excerpt,
            "authoritative_evidence": [{
                "evidence_id": "ev-exact", "supports": ["requirement"],
                "excerpt": "Context. " + excerpt + " End context.",
                "source_url": "https://sam.gov/opp/N-EXACT/view",
            }],
            "requirement_reviewed_by": None,
            "requirement_reviewed_at": None,
            "attachments": [{
                "name": "Performance Work Statement.pdf",
                "media_type": "application/pdf",
                "resource_id": "resource-1",
                "source_url": (
                    "https://sam.gov/api/prod/opps/v3/opportunities/"
                    "N-EXACT/resources/resource-1"),
            }],
            "attachment_inventory_count": 1,
            "attachment_inventory_hash": "attachments-sha",
            "attachment_inventory_valid": True,
            "attachments_checked": False,
            "attachment_gap": "Human requirement review is required",
        }]}}
    }
    monkeypatch.setattr(
        srv, "_assess_ledger_snapshot",
        lambda _client, *, include_run=False: snapshot)
    response = srv.app.test_client().get(
        "/api/assess/requirements?client_name=Testco")
    assert response.status_code == 200
    body = response.get_json()
    assert body["run_id"] == "assess:v1:exact-span"
    assert body["items"] == [{
        "notice_id": "N-EXACT", "title": "Packet capture platform",
        "agency": "DHS / CISA", "response_deadline": "2026-08-21",
        "classification": "unscreened", "recommendation": "research",
        "excerpt": excerpt, "evidence_id": "ev-exact",
        "source_url": "https://sam.gov/opp/N-EXACT/view",
        "reviewed_by": None, "reviewed_at": None,
        "attachments": [{
            "name": "Performance Work Statement.pdf",
            "media_type": "application/pdf",
            "resource_id": "resource-1",
            "source_url": (
                "https://sam.gov/api/prod/opps/v3/opportunities/"
                "N-EXACT/resources/resource-1"),
        }],
        "attachment_inventory_count": 1,
        "attachment_inventory_hash": "attachments-sha",
        "attachment_inventory_valid": True,
        "attachments_checked": False,
        "gap": "Human requirement review is required",
    }]

    monkeypatch.setattr(
        srv, "_assess_ledger_snapshot",
        lambda _client, *, include_run=False: {
            "exists": False, "state": "stale", "stale": True,
            "diagnostics": ["refresh required"],
        })
    stale = srv.app.test_client().get(
        "/api/assess/requirements?client_name=Testco")
    assert stale.status_code == 409
    assert stale.get_json()["state"] == "stale"


def test_requirement_review_post_enforces_inventory_binding_and_attachment_check(
        monkeypatch):
    import agents.assess.live_review as live_review
    import tools.capability as capability
    from tests.test_assess_ledger import BINDING, _profile

    excerpt = "The contractor shall provide packet capture services."
    snapshot = {
        "exists": True, "state": "current", "run_id": "assess:v1:shown",
        "binding": BINDING,
        "run": {"live": {"records": [{
            "notice_id": "N1", "title": "Packet capture", "agency": "CISA",
            "classification": "unscreened", "recommendation": "research",
            "requirement_excerpt": excerpt,
            "authoritative_evidence": [{
                "evidence_id": "EV1", "supports": ["requirement"],
                "excerpt": excerpt,
                "source_url": "https://sam.gov/opp/N1/view",
            }],
            "attachments": [{
                "name": "PWS.pdf", "media_type": "application/pdf",
                "source_url": "https://sam.gov/api/resources/PWS.pdf",
            }],
            "attachment_inventory_count": 1,
            "attachment_inventory_hash": "inventory-v1",
            "attachment_inventory_valid": True,
            "attachments_checked": False,
            "attachment_gap": "review required",
        }]}}}
    monkeypatch.setattr(
        srv, "_assess_ledger_snapshot",
        lambda _client, *, include_run=False: snapshot)
    monkeypatch.setattr(capability, "require_profile", lambda _client: _profile())
    saved = []
    monkeypatch.setattr(
        live_review, "record_requirement_review",
        lambda *args, **kwargs: saved.append(kwargs) or {"decision": "approved"})
    monkeypatch.setattr(srv, "_refresh_assess_ledger", lambda _client: None)
    client = srv.app.test_client()
    base = {
        "client_name": "Testco", "notice_id": "N1", "approve": True,
        "run_id": "assess:v1:shown", "evidence_id": "EV1",
        "excerpt": excerpt, "attachment_inventory_count": 1,
        "attachment_inventory_hash": "inventory-v1",
    }

    changed = client.post(
        "/api/assess/requirements", json={**base, "run_id": "old-run"})
    assert changed.status_code == 409 and not saved

    wrong_count = client.post(
        "/api/assess/requirements",
        json={**base, "attachment_inventory_count": 0,
              "attachments_reviewed": True})
    assert wrong_count.status_code == 409 and not saved

    unchecked = client.post("/api/assess/requirements", json=base)
    assert unchecked.status_code == 400 and not saved

    approved = client.post(
        "/api/assess/requirements",
        json={**base, "attachments_reviewed": True})
    assert approved.status_code == 200
    assert saved[0]["attachment_inventory_hash"] == "inventory-v1"
    assert saved[0]["attachment_inventory_count"] == 1
    assert saved[0]["attachments_reviewed"] is True

    # Confirmed zero is still exact evidence: count=0 and the empty manifest
    # hash must round-trip even though no checkbox is required.
    saved.clear()
    record = snapshot["run"]["live"]["records"][0]
    record.update(
        attachments=[], attachment_inventory_count=0,
        attachment_inventory_hash="inventory-empty",
        attachment_inventory_valid=True, attachments_checked=True)
    zero = {
        **base, "attachment_inventory_count": 0,
        "attachment_inventory_hash": "inventory-empty",
    }
    missing_zero_hash = dict(zero)
    missing_zero_hash.pop("attachment_inventory_hash")
    refused_zero = client.post(
        "/api/assess/requirements", json=missing_zero_hash)
    assert refused_zero.status_code == 409 and not saved
    approved_zero = client.post("/api/assess/requirements", json=zero)
    assert approved_zero.status_code == 200
    assert saved[0]["attachment_inventory_count"] == 0
    assert saved[0]["attachment_inventory_hash"] == "inventory-empty"
    assert saved[0]["attachments_reviewed"] is True

    # A malformed collection can be rejected, but never approved, even if its
    # serialized flag claims validity and the submitted count/hash match.
    saved.clear()
    record.update(
        attachments={"not": "a list"}, attachment_inventory_count=0,
        attachment_inventory_hash="inventory-malformed",
        attachment_inventory_valid=True, attachments_checked=False)
    malformed = {
        **base, "attachment_inventory_count": 0,
        "attachment_inventory_hash": "inventory-malformed",
    }
    malformed_approval = client.post(
        "/api/assess/requirements", json=malformed)
    assert malformed_approval.status_code == 409 and not saved
    malformed_rejection = client.post(
        "/api/assess/requirements", json={**malformed, "approve": False})
    assert malformed_rejection.status_code == 200
    assert saved[0]["approved"] is False
    assert saved[0]["attachments_reviewed"] is False

    saved.clear()
    record.update(
        attachments=[{
            "name": "PWS.pdf", "media_type": "application/pdf",
            "source_url": "https://sam.gov/api/resources/PWS.pdf",
        }],
        attachment_inventory_count=2,
        attachment_inventory_hash="inventory-count-mismatch",
        attachment_inventory_valid=True, attachments_checked=False)
    count_mismatch = {
        **base, "attachment_inventory_count": 2,
        "attachment_inventory_hash": "inventory-count-mismatch",
        "attachments_reviewed": True,
    }
    mismatched_approval = client.post(
        "/api/assess/requirements", json=count_mismatch)
    assert mismatched_approval.status_code == 409 and not saved


def test_client_endpoint_requests_lightweight_ledger_summary(monkeypatch):
    state = {
        "client_name": "Testco", "status": "approved",
        "stages": {"searched": True},
    }
    monkeypatch.setattr(srv, "client_state", lambda _slug: dict(state))
    monkeypatch.setattr(srv, "build_steps", lambda _slug: [])
    monkeypatch.setattr(
        srv, "client_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(srv, "_doc_prefs", lambda _slug: {"hidden": []})
    monkeypatch.setattr(
        srv, "_final_brief", lambda _slug, **_kwargs: None)
    monkeypatch.setattr(
        srv, "_assess_release_gate",
        lambda _client: (None, "missing", []))
    monkeypatch.setattr(srv, "_assessment_doc", lambda _slug: None)
    calls = []

    def snapshot(client_name, *, include_run=False):
        calls.append((client_name, include_run))
        return {"exists": True, "state": "current", "run_id": "assess:v1:test"}

    monkeypatch.setattr(srv, "_assess_ledger_snapshot", snapshot)
    response = srv.app.test_client().get("/api/client/testco")
    assert response.status_code == 200
    body = response.get_json()
    assert calls == [("Testco", False)]
    assert "run" not in body["assess_ledger"]


def test_client_state_reports_effective_stale_approval_problems(
        tmp_path, monkeypatch):
    root = str(tmp_path)
    review = _mk_client(root)
    cleaned = os.path.join(root, "data", "cleaned")
    reports = os.path.join(root, "data", "reports")
    os.makedirs(cleaned, exist_ok=True)
    os.makedirs(reports, exist_ok=True)
    monkeypatch.setattr(srv, "REVIEW_DIR", review)
    monkeypatch.setattr(srv, "CLEANED_DIR", cleaned)
    monkeypatch.setattr(srv, "REPORT_DIR", reports)
    monkeypatch.setattr(
        srv, "_assess_release_gate",
        lambda _client: (None, "invalid", ["Assess capability profile changed"]))

    body = srv.app.test_client().get("/api/client/acme_federal").get_json()
    assert body["review_approved"] is False
    assert body["review_approval_status"] == "invalid"
    assert body["review_approval_problems"] == [
        "Assess capability profile changed"]


def test_file_api_is_jailed_to_data_dir():
    client = srv.app.test_client()
    r = client.get("/api/file?path=../.env")
    assert r.status_code == 404
    r = client.get("/api/file?path=/etc/passwd")
    assert r.status_code == 404
    r = client.get("/api/file?path=run_intake.py")  # real file, but outside data/
    assert r.status_code == 404


def test_step_cmd_builds_expected_commands():
    cmd = srv._step_cmd("report", "Acme", {"kind": "pre-assessment"})
    assert cmd[1:] == ["run_report.py", "--client", "Acme", "--kind", "pre-assessment"]
    with pytest.raises(ValueError):
        srv._step_cmd("rm_rf", "Acme", {})


def test_horizon_approval_endpoint_rejects_invalid_draft(monkeypatch, tmp_path):
    """The UI is an explicit human gate, not a bypass around validation."""
    import agents.reports.horizon as hz

    monkeypatch.setattr(hz, "_REVIEW_DIR", tmp_path)
    binding = {
        "version": 1, "scope_designator": "all",
        "sweep_artifact": "searches_acme.json",
        "sweep_sha256": "sweep-a", "profile_sha256": "profile-a",
    }
    monkeypatch.setattr(hz, "current_horizon_binding",
                        lambda _client, **_kwargs: binding)
    source = "https://sam.gov/opp/horizon-test/view"
    text = "Canonical monitor signal from the scoped sweep."
    bank = [{"id": "H1", "text": text, "source": source,
             "kind": "sam_monitor", "retrieved_at":
             "2026-07-10T12:00:00+00:00"}]
    item = hz.HorizonItem(
        id="forming-window", title="Forming window", where="Test Agency",
        verified=[hz.HorizonSignal(evidence_id="H1", text=text,
                                   source=source)],
        pattern="The verified signal establishes buyer activity.",
        projection="We judge a positioning window may form.",
        window="next 6-12 months", watching="review the buyer feed weekly",
        confidence="early", lifecycle_stage=hz.LifecycleStage.EARLY_SIGNAL,
        falsifier="No related buyer activity appears before review.",
        watch_trigger="new buyer planning activity",
        monitoring_cadence="reviewed weekly")
    hset = hz.HorizonSet(client_name="Acme", items=[item],
                         method_note="Scoped monitor records screened.")
    payload = hz.new_draft("Acme", hset, bank, binding=binding)
    payload["set"]["items"][0]["verified"][0]["text"] = "Invented claim."
    hz.save_horizon(payload)

    client = srv.app.test_client()
    rejected = client.post("/api/horizon/approve", json={"client_name": "Acme"})
    assert rejected.status_code == 409
    body = rejected.get_json()
    assert body["status"] == "draft" and body["problems"]
    assert hz.load_horizon("Acme")["status"] == "draft"

    payload["set"]["items"][0]["verified"][0]["text"] = text
    hz.save_horizon(payload)
    approved = client.post("/api/horizon/approve", json={"client_name": "Acme"})
    assert approved.status_code == 200
    assert approved.get_json() == {"status": "approved", "items": 1}
    assert hz.load_horizon("Acme")["status"] == "approved"

    # A later scope/profile/sweep change makes the stored approval ineligible
    # without deleting it; the UI shows the operator why it must be recomposed.
    changed = {**binding, "scope_designator": "agency_dhs"}
    monkeypatch.setattr(hz, "current_horizon_binding",
                        lambda _client, **_kwargs: changed)
    read = client.get("/api/horizon?client_name=Acme").get_json()
    assert read["status"] == "approved"
    assert read["effective_status"] == "invalid"
    assert any("search scope changed" in p
               for p in read["eligibility_problems"])
    stale_approve = client.post("/api/horizon/approve",
                                json={"client_name": "Acme"})
    assert stale_approve.status_code == 409


def test_horizon_approval_refresh_failure_cannot_change_success(monkeypatch):
    """The durable human approval wins even when its additive projection fails."""
    import agents.reports.horizon as hz

    draft = {"status": "draft", "set": {"items": [{"id": "H1"}]}}
    approved_payload = {
        "status": "approved", "set": {"items": [{"id": "H1"}]}}
    saved = []
    attempts = []
    monkeypatch.setattr(hz, "load_horizon", lambda _client: draft)
    monkeypatch.setattr(hz, "approve_horizon", lambda _payload: approved_payload)
    monkeypatch.setattr(hz, "save_horizon", lambda payload: saved.append(payload))

    def fail_refresh(client_name):
        attempts.append(client_name)
        raise RuntimeError("simulated ledger refresh failure")

    monkeypatch.setattr(srv, "_refresh_assess_ledger", fail_refresh)
    response = srv.app.test_client().post(
        "/api/horizon/approve", json={"client_name": "Acme"})
    assert response.status_code == 200
    assert response.get_json() == {"status": "approved", "items": 1}
    assert saved == [approved_payload]
    assert attempts == ["Acme"]


def test_review_ledger_panel_shows_census_actionability_and_all_states():
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "LIVE CENSUS" in html
    assert "ACTIONABLE" in html
    assert "REQUIRED COVERAGE GAP" in html
    assert "COVERAGE WARNING" in html
    assert "ledger.coverage_issues || ledger.blocking_sources" in html
    assert "STALE — REFRESH REQUIRED" in html
    assert "TEMPORARILY UNAVAILABLE" in html
    assert "if (DETAIL.assess_ledger)" in html


def test_since_your_last_approval_panel_is_retired():
    # 2026-07-24 operator ruling (2026-07-23): the "Since your last approval"
    # drift panel is retired from the page. The server endpoint and the
    # binding gate stay; only the dashboard surface is gone.
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "Since your last approval" not in html
    assert "const diffSlot" not in html
    assert "'/api/client/' + DETAIL.slug + '/approval-diff'" not in html


def test_review_panel_loads_exact_live_requirement_fields_and_controls():
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "buildRequirementSpanReview(DETAIL.assess_ledger, card)" in html
    assert "requirement_review_candidates" in html
    assert "requirement_reviews_approved" in html
    assert "Review live requirement spans" in html
    assert "Exact language from SAM" in html
    assert "item.title" in html
    assert "item.agency" in html
    assert "item.response_deadline" in html
    assert "item.excerpt" in html
    assert "item.source_url" in html
    assert "Open the SAM source" in html
    assert "Approve as requirement" in html
    assert ">Reject</button>" in html
    assert "safeRequirementSourceUrl" in html
    assert "item.attachments" in html
    assert "attachment.name" in html
    assert "attachment.media_type" in html
    assert "(attachment || {}).source_url" in html
    assert "SAM attachments" in html
    assert "Download from SAM" in html
    assert "I reviewed all listed attachments" in html
    assert "item.attachment_inventory_count" in html
    assert "item.attachment_inventory_valid === true" in html
    assert "attachmentInventoryCount === attachments.length" in html


def test_requirement_review_ui_fails_closed_and_refreshes_after_save():
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "!ledger.exists || ledger.state !== 'current'" in html
    assert "data.run_id !== expectedRun" in html
    assert "latest.run_id !== expectedRun" in html
    assert "current.evidence_id !== item.evidence_id" in html
    assert "current.excerpt !== item.excerpt" in html
    assert "current.attachment_inventory_hash ?? null" in html
    assert "current.attachment_inventory_count" in html
    assert "approve && current.attachment_inventory_valid !== true" in html
    assert "'/api/assess/requirements?client_name='" in html
    assert "await api('/api/assess/requirements'" in html
    assert "notice_id: item.notice_id, approve" in html
    assert "attachment_inventory_hash: attachmentInventoryHash" in html
    assert "attachment_inventory_count: attachmentInventoryCount" in html
    assert "attachments_reviewed: approve" in html
    assert "if (approve && !attachmentInventoryValid)" in html
    assert "if (approve && hasAttachments" in html
    assert "!attachmentConfirm.checked" in html
    assert "Review every listed attachment and check the confirmation" in html
    assert "rejectBtn.onclick = () => decide(false)" in html
    assert "await select(slugAtOpen)" in html
    assert "This span is no longer current" in html


def test_company_suggest_backoff_recovers_overshoot_typo(monkeypatch):
    import httpx as _httpx

    calls = []

    class FakeResp:
        def __init__(self, data): self._d = data
        def raise_for_status(self): pass
        def json(self): return self._d

    def fake_get(url, params=None, timeout=None):
        q = params["query"]
        calls.append(q)
        # exact overshoot typo returns nothing; trimmed query hits
        return FakeResp([] if q == "recorded futures" else
                        [{"name": "Recorded Future", "domain": "recordedfuture.com", "logo": None}])

    monkeypatch.setattr(_httpx, "get", fake_get)
    client = srv.app.test_client()
    r = client.get("/api/company_suggest?q=recorded futures")
    data = r.get_json()
    assert data[0]["name"] == "Recorded Future"
    assert len(calls) >= 2  # backoff actually retried


def test_company_suggest_respects_toggle(monkeypatch):
    monkeypatch.setenv("LILA_ENABLE_COMPANY_SUGGEST", "off")
    client = srv.app.test_client()
    assert client.get("/api/company_suggest?q=recorded").get_json() == []


def test_assessment_loader_accepts_old_and_new_filenames(tmp_path, monkeypatch):
    """Writer emits federal_opportunity_assessment; legacy capture_brief
    artifacts on disk keep loading (dashboard shelf + FINAL PRODUCT block)."""
    monkeypatch.setattr(srv, "REPORT_DIR", str(tmp_path))
    # the badge folds the approval axis (release resolver, 2026-07-11)
    monkeypatch.setattr("agents.reports.release.assess_approval_for_release",
                        lambda *a, **k: (None, "approved", []))

    # legacy artifact only
    old = os.path.join(str(tmp_path), "acme.capture_brief.html")
    open(old, "w").write("<html>legacy</html>")
    fb = srv._final_brief("acme")
    assert fb and fb["qa_pass"] and "capture_brief" in fb["path"]
    row = srv._report_rows("acme")[0]
    assert row["exists"] and row["qa_pass"] and row["label"] == "Opportunity Assessment"

    # New stable artifact appears and is preferred, but fails closed until its
    # same-family QA sidecar explicitly releases it.
    new = os.path.join(str(tmp_path), "acme.federal_opportunity_assessment.html")
    open(new, "w").write("<html>new</html>")
    os.utime(new, None)
    row = srv._report_rows("acme")[0]
    assert not row["qa_pass"] and "federal_opportunity_assessment" in row["path"]
    fb = srv._final_brief("acme")
    assert fb and not fb["qa_pass"] and "federal_opportunity_assessment" in fb["path"]

    qa = os.path.join(str(tmp_path),
                      "acme.federal_opportunity_assessment.qa.json")
    with open(qa, "w") as f:
        json.dump({"state": "release"}, f)
    row = srv._report_rows("acme")[0]
    assert row["qa_pass"]
    assert srv._final_brief("acme")["qa_pass"]

    # Explicit legacy DO-NOT-SEND stamps are still detected.
    bad = os.path.join(str(tmp_path), "zeta.federal_opportunity_assessment.DO-NOT-SEND.html")
    open(bad, "w").write("<html>stamped</html>")
    fb = srv._final_brief("zeta")
    assert fb and not fb["qa_pass"]

    # docs shelf labels: old file renders under the new client-facing label
    docs = srv.client_documents("acme")
    labels = {d["label"] for d in docs}
    assert "Opportunity Assessment" in labels
    assert "Capture Brief" not in labels


def test_stable_foa_sidecar_fails_closed_until_fresh_release(
        tmp_path, monkeypatch):
    """2026-07-10: a clean stable filename never bypasses release state."""
    monkeypatch.setattr(srv, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        srv, "_assess_release_gate_for_slug",
        lambda _slug: ({"client": "Acme"}, "approved", []),
    )
    html = tmp_path / "acme.federal_opportunity_assessment.html"
    qa = tmp_path / "acme.federal_opportunity_assessment.qa.json"
    html.write_text("<html>DRAFT PREVIEW</html>", encoding="utf-8")
    os.utime(html, (200, 200))
    client = srv.app.test_client()

    # Missing sidecar: visible preview, blocked shelf state and download.
    assert srv._final_brief("acme")["qa_pass"] is False
    assert srv._report_rows("acme")[0]["qa_pass"] is False
    shelf = next(d for d in srv.client_documents("acme")
                 if d["label"] == "Opportunity Assessment")
    assert shelf["qa_pass"] is False
    assert client.get("/client/acme/download/foa.html").status_code == 409

    # Unreadable, stale, and explicitly release-ineligible sidecars all block.
    qa.write_text("{not json", encoding="utf-8")
    os.utime(qa, (300, 300))
    assert srv._stable_foa_status(str(html))["reason"] == "unreadable QA sidecar"

    qa.write_text(json.dumps({"state": "release"}), encoding="utf-8")
    os.utime(qa, (100, 100))
    assert srv._stable_foa_status(str(html))["reason"] == "stale QA sidecar"

    qa.write_text(json.dumps({"state": "release",
                              "release_eligible": False}), encoding="utf-8")
    os.utime(qa, (300, 300))
    assert srv._stable_foa_status(str(html))["reason"] == "release ineligible"
    assert client.post("/api/export/pdf", json={
        "slug": "acme", "kind": "foa"}).status_code == 409

    # A fresh RELEASE sidecar unlocks the FAMILY; the badge additionally
    # requires the Assess approval (single-truth resolver, 2026-07-11)
    qa.write_text(json.dumps({"state": "release"}), encoding="utf-8")
    os.utime(qa, (400, 400))
    unapproved = srv._final_brief("acme")
    assert unapproved["qa_pass"] is False
    assert "not approved" in unapproved["reason"]
    monkeypatch.setattr("agents.reports.release.assess_approval_for_release",
                        lambda *a, **k: (None, "approved", []))
    assert srv._final_brief("acme")["qa_pass"] is True
    assert srv._report_rows("acme")[0]["qa_pass"] is True
    shelf = next(d for d in srv.client_documents("acme")
                 if d["label"] == "Opportunity Assessment")
    assert shelf["qa_pass"] is True
    # Assessment release alone is no longer sufficient: the separate,
    # current Targeting Review receipt also protects the attachment boundary.
    download = client.get("/client/acme/download/foa.html")
    assert download.status_code == 409
    assert b"complete Targeting Review" in download.data
    monkeypatch.setattr(
        srv, "_client_targets_payload",
        lambda _slug: ({"targeting_readiness": {"ready": True,
                                                 "problems": []}}, 200),
    )
    download = client.get("/client/acme/download/foa.html")
    assert download.status_code == 200
    assert b"DRAFT PREVIEW" in download.data


def test_attention_label_uses_client_facing_names(tmp_path, monkeypatch):
    """A failed QA gate surfaces the client-facing deliverable name, not the
    raw filename kind ('teaser' -> 'Federal Snapshot')."""
    monkeypatch.setattr(srv, "REPORT_DIR", str(tmp_path))
    with open(os.path.join(str(tmp_path), "acme.teaser.qa.json"), "w") as f:
        json.dump({"lint_ok": False, "audit": {"passed": False}}, f)
    a = srv._attention({"status": "approved", "slug": "acme", "next_step": "reports"})
    assert a["label"] == "Federal Snapshot failed its QA gates"
    pending = srv._attention({"status": "pending", "slug": "acme"})
    rejected = srv._attention({"status": "rejected", "slug": "acme"})
    assert pending["label"] == "Analyst Layer waiting for your approval"
    assert rejected["label"] == "Analyst Layer rejected: rebuild and re-review"


def test_docs_shelf_supersedes_legacy_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "REPORT_DIR", str(tmp_path))
    open(os.path.join(str(tmp_path), "acme.capture_brief.html"), "w").write("<html>old</html>")
    # legacy only: still shelved (history readable)
    assert any(".capture_brief" in d["path"] for d in srv.client_documents("acme"))
    # new artifact arrives: the stale legacy card is superseded, one card remains
    open(os.path.join(str(tmp_path), "acme.federal_opportunity_assessment.html"), "w").write("<html>new</html>")
    docs = srv.client_documents("acme")
    paths = [d["path"] for d in docs if d["label"] == "Opportunity Assessment"]
    assert len(paths) == 1 and "federal_opportunity_assessment" in paths[0]


def test_depository_lists_every_client_with_deliverables(tmp_path, monkeypatch):
    """The front page is the main depository: all clients, pipeline status,
    and deliverables in one payload."""
    root = str(tmp_path)
    review = _mk_client(root)
    monkeypatch.setattr(srv, "REVIEW_DIR", review)
    reports = os.path.join(root, "data", "reports")
    os.makedirs(reports, exist_ok=True)
    open(os.path.join(reports, "acme_federal.federal_opportunity_assessment.html"),
         "w").write("<html>foa</html>")
    monkeypatch.setattr(srv, "REPORT_DIR", reports)
    monkeypatch.setenv("LILA_DESKTOP_ROOT", os.path.join(root, "nodesk"))

    r = srv.app.test_client().get("/api/depository")
    assert r.status_code == 200
    rows = r.get_json()
    row = next(c for c in rows if c["slug"] == "acme_federal")
    assert row["stages"]["approved"] is True                    # pipeline status
    labels = {d["label"] for d in row["documents"]}
    assert "Opportunity Assessment" in labels                   # deliverables
    assert row["doc_total"] >= 1


def test_sidebar_is_scoped_to_the_open_client(tmp_path, monkeypatch):
    """Inside a client view the sidebar carries ONLY that client's targets
    (graded pursuits) and artifacts — never another client's."""
    import json as _json
    from tests.test_assessment_document import _searches

    root = str(tmp_path)
    review = _mk_client(root)
    _mk_client(root, slug="other_co", name="Other Co")
    monkeypatch.setattr(srv, "REVIEW_DIR", review)
    reports = os.path.join(root, "data", "reports")
    cleaned = os.path.join(root, "data", "cleaned")
    os.makedirs(reports, exist_ok=True)
    os.makedirs(cleaned, exist_ok=True)
    monkeypatch.setattr(srv, "REPORT_DIR", reports)
    monkeypatch.setattr(srv, "CLEANED_DIR", cleaned)
    monkeypatch.setenv("LILA_DESKTOP_ROOT", os.path.join(root, "nodesk"))

    with open(os.path.join(cleaned, "searches_acme_federal.json"), "w") as f:
        _json.dump(_searches(3), f)
    open(os.path.join(reports, "acme_federal.federal_opportunity_assessment.html"),
         "w").write("<html>acme</html>")
    open(os.path.join(reports, "other_co.federal_opportunity_assessment.html"),
         "w").write("<html>other</html>")

    r = srv.app.test_client().get("/api/client/acme_federal")
    assert r.status_code == 200
    sb = r.get_json()["sidebar"]
    assert len(sb["targets"]) == 3                              # graded pursuits
    assert all(t["grade"] in "ABCDF" for t in sb["targets"])
    assert sb["artifacts"], "artifacts present"
    assert all("other_co" not in a["path"] for a in sb["artifacts"])
    assert all("acme_federal" in a["path"] or "review" in a["path"]
               for a in sb["artifacts"])


def test_qualify_and_assess_report_are_internal_not_dashboard_steps(tmp_path, monkeypatch):
    """Qualify & verify and the Assess report are INTERNAL — not dashboard
    steps at all. A 'Review opportunities' step sits after search instead.
    The internal code paths stay fully runnable via /api/run."""
    root = str(tmp_path)
    review = _mk_client(root)
    monkeypatch.setattr(srv, "REVIEW_DIR", review)
    os.makedirs(os.path.join(root, "data", "cleaned"), exist_ok=True)
    open(os.path.join(root, "data", "cleaned", "searches_acme_federal.json"),
         "w").write("{}")
    monkeypatch.setattr(srv, "CLEANED_DIR", os.path.join(root, "data", "cleaned"))
    monkeypatch.setattr(srv, "REPORT_DIR", os.path.join(root, "data", "reports"))

    steps = {s["key"]: s for s in srv.build_steps("acme_federal")}
    assert "qualify" not in steps and "reports" not in steps   # gone from the dashboard
    assert "review" in steps                                   # the new checkpoint
    assert steps["review"]["state"] in ("ready", "waiting")
    # the review step sits right after the opportunity search
    order = [s["key"] for s in srv.build_steps("acme_federal")]
    assert order.index("review") == order.index("search") + 1

    # non-UI entry points untouched and runnable
    assert srv._step_cmd("qualify", "Acme", {})[1] == "run_qualify.py"
    assert srv._step_cmd("report", "Acme", {"kind": "capture_brief"})[1] == "run_capture_brief.py"
    # the review loop re-triages via run_picture, forwarding operator guidance
    cmd = srv._step_cmd("picture", "Acme", {"guidance": "favor aviation buyers"})
    assert "run_picture.py" in cmd and "--guidance" in cmd
    assert "favor aviation buyers" in cmd


def test_views_job_kind_and_shelf_labels(tmp_path, monkeypatch):
    """One-click refresh: the 'views' job maps to run_views.py, and the three
    per-view artifacts shelve under distinct labels."""
    cmd = srv._step_cmd("views", "Thinklogical", {})
    assert cmd[-3:] == ["run_views.py", "--client", "Thinklogical"]

    monkeypatch.setattr(srv, "REPORT_DIR", str(tmp_path))
    for view in ("client", "sales", "internal"):
        open(os.path.join(str(tmp_path), f"acme.assessment.{view}.html"), "w").write("<html>")
    labels = {d["label"] for d in srv.client_documents("acme")}
    assert {"Assessment · Client View", "Assessment · Sales View",
            "Assessment · Internal View"} <= labels


def _job_record(*, status="running", started=100.0, heartbeat=128.0,
                last_output=110.0, finished=None):
    return {
        "id": "job-1", "step": "views", "client": "Acme Federal",
        "cmd": "python run_views.py", "status": status,
        "lines": ["[compose] filling the locked schema ..."],
        "returncode": 0 if status == "done" else None,
        "started_at": "2026-07-11T12:00:00+00:00",
        "finished_at": (
            "2026-07-11T12:01:00+00:00" if finished is not None else None),
        "_started_monotonic": started,
        "_heartbeat_monotonic": heartbeat,
        "_last_output_monotonic": last_output,
        "_finished_monotonic": finished,
    }


def test_job_snapshot_uses_server_clock_and_hides_private_fields():
    job = _job_record()
    job["lines"].extend([
        "[out:client] /tmp/acme.assessment.client.DO-NOT-SEND.html",
        "[out] /tmp/acme.federal_opportunity_assessment.html (DRAFT)",
        "[out] atomic write FAILED (simulated)",
    ])
    snapshot = srv._job_snapshot(job, now=130.0)
    assert snapshot["elapsed_seconds"] == 30.0
    assert snapshot["quiet_seconds"] == 20.0
    assert snapshot["heartbeat_age_seconds"] == 2.0
    assert snapshot["runner_state"] == "process_alive"
    assert snapshot["artifacts_written"] == 2
    assert not any(key.startswith("_") for key in snapshot)
    snapshot["lines"].append("client mutation")
    assert job["lines"][-1] == "[out] atomic write FAILED (simulated)"


def test_finished_job_timing_freezes_and_stale_heartbeat_is_diagnostic():
    finished = _job_record(
        status="done", heartbeat=139.0, last_output=135.0, finished=140.0)
    snapshot = srv._job_snapshot(finished, now=500.0)
    assert snapshot["elapsed_seconds"] == 40.0
    assert snapshot["quiet_seconds"] == 5.0
    assert snapshot["heartbeat_age_seconds"] == 1.0
    assert snapshot["runner_state"] == "finished"

    stale = _job_record(heartbeat=119.0)
    stale_snapshot = srv._job_snapshot(stale, now=130.0)
    assert stale_snapshot["runner_state"] == "heartbeat_stale"
    assert stale_snapshot["status"] == "running"


def test_stale_heartbeat_never_unlocks_same_client(monkeypatch):
    stale = _job_record(heartbeat=100.0)
    monkeypatch.setattr(srv, "JOBS", {"job-1": stale})
    monkeypatch.setattr(
        srv, "_step_cmd", lambda *_args: [sys.executable, "-c", "pass"])
    assert srv._job_snapshot(stale, now=130.0)["runner_state"] == \
        "heartbeat_stale"
    with pytest.raises(srv.JobRunning) as exc:
        srv.start_job("views", "Acme Federal", {})
    assert exc.value.job_id == "job-1"


def test_job_endpoint_returns_public_telemetry_only(monkeypatch):
    monkeypatch.setattr(srv, "JOBS", {"job-1": _job_record()})
    body = srv.app.test_client().get("/api/job/job-1").get_json()
    assert body["runner_state"] in {"process_alive", "heartbeat_stale"}
    assert "elapsed_seconds" in body and "quiet_seconds" in body
    assert not any(key.startswith("_") for key in body)


def test_job_runner_records_output_heartbeat_and_terminal_state(
        tmp_path, monkeypatch):
    child = {}

    class FakeProcess:
        def __init__(self, *_args, stdout, env, **_kwargs):
            self.stdout = stdout
            self.returncode = None
            self.polls = 0
            child["env"] = env

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                self.stdout.write("[compose] working\n")
                self.stdout.flush()
                return None
            self.returncode = 0
            return 0

    started = srv._time.monotonic()
    job = _job_record(
        started=started, heartbeat=None, last_output=started, finished=None)
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "JOBS", {"job-1": job})
    monkeypatch.setattr(srv.subprocess, "Popen", FakeProcess)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient_must_not_reach_child")
    srv._run_job("job-1", ["fake"])

    snapshot = srv._job_snapshot(job)
    assert snapshot["status"] == "done"
    assert snapshot["runner_state"] == "finished"
    assert "[compose] working" in snapshot["lines"]
    assert job["_heartbeat_monotonic"] is not None
    assert job["_last_output_monotonic"] >= started
    assert "OPENAI_API_KEY" not in child["env"]
    assert os.environ["OPENAI_API_KEY"] == "sk-ambient_must_not_reach_child"


def test_job_runner_start_failure_becomes_visible_failure(
        tmp_path, monkeypatch):
    started = srv._time.monotonic()
    job = _job_record(
        started=started, heartbeat=None, last_output=started, finished=None)
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "JOBS", {"job-1": job})

    def fail_start(*_args, **_kwargs):
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(srv.subprocess, "Popen", fail_start)
    srv._run_job("job-1", ["missing"])
    snapshot = srv._job_snapshot(job)
    assert snapshot["status"] == "failed"
    assert snapshot["runner_state"] == "finished"
    assert any("process failed to start" in line for line in snapshot["lines"])
    assert snapshot["finished_at"]


def test_all_job_consoles_use_server_authoritative_telemetry():
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    # 2026-07-25: the locked gate gains THE Run report console (operator
    # ruling after the rehearsal found no run control on approved
    # clients); four consoles total.
    assert html.count("= renderJobConsole(j)") == 4
    assert "j.output" not in html
    assert "Date.now() - t0" not in html
    assert "[PROCESS ALIVE]" in html
    assert "[HEARTBEAT STALE]" in html
    assert "job remains locked" in html
    assert "j.artifacts_written" in html
    assert "[DRAFT AVAILABLE] release blocked" in html


def test_legacy_finale_does_not_render_inert_openai_collaborator():
    html = Path(srv.__file__).with_name("index.html").read_text(
        encoding="utf-8"
    )
    start = html.index("function buildFinale(d) {")
    end = html.index("\n  return wrap;", start)
    legacy_finale = html[start:end]

    assert "collaboration-openai" not in legacy_finale
    assert "buildOpenAICollaborator" not in legacy_finale
    assert "Adds an independent challenge" not in html
    assert "the current legacy Finale does not invoke OpenAI" in html
    assert "Saving it now does not run a model" in html


def test_target_buckets_classify_by_reason_and_prime():
    """2026-07-10: the client rail buckets targets — solicitation (pursuit
    POCs), candidate (watchlist + manual), prime (teaming-prime contacts)."""
    from ui.server import _target_bucket
    primes = {"hii mission technologies"}
    assert _target_bucket({"reason_kind": "pursuit"}, primes) == "solicitation"
    assert _target_bucket({"reason_kind": "watchlist"}, primes) == "candidate"
    assert _target_bucket({"reason_kind": "manual"}, primes) == "candidate"
    assert _target_bucket({"reason_kind": "pursuit",
                           "org": "HII Mission Technologies Corp"},
                          primes) == "prime"
    assert _target_bucket({}, set()) == "candidate"


def test_one_running_job_per_client(monkeypatch):
    """2026-07-11 (review WS1): two concurrent jobs for one client race on the
    same artifact files. The second press is refused with the running job id;
    other clients are unaffected."""
    monkeypatch.setattr(srv, "_step_cmd",
                        lambda step, client, args: [sys.executable, "-c",
                                                    "import time; time.sleep(3)"])
    jid = srv.start_job("searches", "Acme Federal", {})
    try:
        with pytest.raises(srv.JobRunning) as exc:
            srv.start_job("report", "Acme Federal", {})
        assert exc.value.job_id == jid
        # a different client starts fine
        other = srv.start_job("searches", "Other Co", {})
        assert other and other != jid
    finally:
        for j in (jid, locals().get("other")):
            pass  # daemon threads; sleep subprocesses exit on their own


def test_qa_sidecar_certifies_exact_html(tmp_path, monkeypatch):
    """2026-07-11 build pairing: a RELEASE sidecar whose html_sha256 does not
    match the html on disk (concurrent-build mispair) fails closed; a matching
    pair releases; legacy sidecars without the hash keep the mtime rule."""
    import hashlib
    import json as _json
    import time
    html_path = str(tmp_path / "acme.federal_opportunity_assessment.html")
    qa_path = html_path[:-len(".html")] + ".qa.json"
    draft_html = "<html>DRAFT BUILD A</html>"
    with open(html_path, "w") as f:
        f.write(draft_html)
    release_of_other_build = {"state": "release",
                              "html_sha256": hashlib.sha256(
                                  b"<html>RELEASE BUILD B</html>").hexdigest()}
    with open(qa_path, "w") as f:
        _json.dump(release_of_other_build, f)
    st = srv._stable_foa_status(html_path)
    assert st["qa_pass"] is False
    assert "different build" in st["reason"]

    # the certified pair releases
    good = {"state": "release",
            "html_sha256": hashlib.sha256(draft_html.encode()).hexdigest()}
    with open(qa_path, "w") as f:
        _json.dump(good, f)
    srv._HTML_SHA_MEMO.clear()
    assert srv._stable_foa_status(html_path)["qa_pass"] is True

    # legacy sidecar (no hash): mtime freshness still governs
    with open(qa_path, "w") as f:
        _json.dump({"state": "release"}, f)
    os.utime(qa_path, (time.time() + 5, time.time() + 5))
    assert srv._stable_foa_status(html_path)["qa_pass"] is True


def test_artifact_state_survives_mid_request_removal(tmp_path, monkeypatch):
    """2026-07-11: _mtime_or_none treats a vanished file as absent; the
    endpoint path never 500s on the exists/getmtime race."""
    assert srv._mtime_or_none(str(tmp_path / "gone.html")) is None
    monkeypatch.setattr(srv, "REPORT_DIR", str(tmp_path))
    state = srv._foa_artifact_state("acme_federal")
    assert state["kind"] == "missing" and state["qa_pass"] is False


def test_plain_approve_endpoint_preserves_a_standing_partial_grant(
        tmp_path, monkeypatch):
    """Cycle 3 review finding (2026-07-12): the plain Approve button sends NO
    partial_release_approved key, and the endpoint must treat absence as
    "leave the standing grant alone" (explicit false still revokes it)."""
    import agents.assess.approval as approval

    binding = {
        "version": 1, "scope_designator": "all",
        "sweep_artifact": "searches_testco.json",
        "sweep_sha256": "sweep-a", "profile_sha256": "profile-a",
    }
    monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(
        approval, "current_assess_binding",
        lambda *_args, **_kwargs: binding)
    client = srv.app.test_client()
    first = client.post("/api/review/assess-approve", json={
        "client_name": "Testco"})
    assert first.status_code == 200
    artifact = tmp_path / "testco.assess_approval.json"
    stored = json.loads(artifact.read_text())
    stored.update({
        "partial_release_approved": True,
        "partial_release_run_id": "assess:v1:granted",
        "partial_release_blockers": [{"source": "sam_census"}],
        "partial_release_blockers_sha256": "abc123",
    })
    artifact.write_text(json.dumps(stored))

    plain = client.post("/api/review/assess-approve", json={
        "client_name": "Testco", "note": "re-reviewed"})
    assert plain.status_code == 200
    assert plain.get_json()["partial_release_approved"] is True
    preserved = json.loads(artifact.read_text())
    assert preserved["partial_release_approved"] is True
    assert preserved["partial_release_run_id"] == "assess:v1:granted"

    explicit = client.post("/api/review/assess-approve", json={
        "client_name": "Testco", "partial_release_approved": False})
    assert explicit.status_code == 200
    assert explicit.get_json()["partial_release_approved"] is False
    dropped = json.loads(artifact.read_text())
    assert dropped["partial_release_approved"] is False
    assert "partial_release_run_id" not in dropped


def test_assess_run_dir_honors_the_env_override(tmp_path, monkeypatch):
    """Cycle 3 review finding (2026-07-12): the dashboard must resolve the
    assess-run store exactly like the gate legs (LILA_ASSESS_RUN_DIR), or an
    operator env override desyncs the two."""
    import importlib.util

    override = tmp_path / "override_store"
    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(override))
    spec = importlib.util.spec_from_file_location(
        "ui_server_env_probe",
        os.path.join(os.path.dirname(srv.__file__), "server.py"))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.ASSESS_RUN_DIR == str(override)
    monkeypatch.delenv("LILA_ASSESS_RUN_DIR")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh.ASSESS_RUN_DIR.endswith(
        os.path.join("data", "state", "assess_runs"))


def test_review_pdf_exports_draft_reports_with_forced_internal_banner(
        tmp_path, monkeypatch):
    """Operator-directed (2026-07-12): a report still in consideration must be
    downloadable as a PDF for review. NOT a release door: output is force-
    stamped INTERNAL REVIEW, named .INTERNAL-REVIEW.pdf, written only to the
    review-exports dir, and the gated /api/export/pdf is untouched."""
    import tools.export_pdf as xp

    desk = tmp_path / "desk"
    desk.mkdir()
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desk))
    out_dir = tmp_path / "exports"
    monkeypatch.setenv("LILA_REVIEW_EXPORT_DIR", str(out_dir))
    draft = desk / "acme.assessment.client.DO-NOT-SEND.html"
    draft.write_text("<html><body><h1>Draft findings</h1></body></html>",
                     encoding="utf-8")
    captured = {}

    def _fake_export(html_path, pdf_path=None, **_kw):
        captured["src"] = open(html_path, encoding="utf-8").read()
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-fake")
        return pdf_path

    monkeypatch.setattr(xp, "export_pdf", _fake_export)
    client = srv.app.test_client()
    r = client.post("/api/export/review-pdf",
                    json={"path": str(draft)})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["internal_only"] is True
    assert body["pdf"].endswith(".INTERNAL-REVIEW.pdf")
    # the banner is FORCED into the exported copy; original file untouched
    assert "INTERNAL REVIEW COPY" in captured["src"]
    assert "Draft findings" in captured["src"]
    assert "INTERNAL REVIEW" not in draft.read_text(encoding="utf-8")
    # output landed in the review-exports dir, nowhere near data/reports
    produced = list(out_dir.glob("*.INTERNAL-REVIEW.pdf"))
    assert len(produced) == 1
    # the temp stamped html is cleaned up
    assert not list(out_dir.glob(".*review-src.html"))


def test_review_pdf_refuses_paths_outside_the_allowed_roots(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(tmp_path / "desk"))
    client = srv.app.test_client()
    outside = tmp_path / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")
    assert client.post("/api/export/review-pdf",
                       json={"path": str(outside)}).status_code == 404
    assert client.post("/api/export/review-pdf",
                       json={"path": "../../etc/hosts"}).status_code == 404


def test_review_pdf_is_html_only(tmp_path, monkeypatch):
    desk = tmp_path / "desk"
    desk.mkdir()
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desk))
    md = desk / "notes.md"
    md.write_text("internal", encoding="utf-8")
    r = srv.app.test_client().post("/api/export/review-pdf",
                                   json={"path": str(md)})
    assert r.status_code == 400


def test_openai_collaborator_key_is_session_only_and_never_leaks(
        monkeypatch):
    """The UI vault is independent intake/inference collaboration state."""
    from agents.candidate_review_v1.collaboration_session import (
        clear_openai_collaboration,
        openai_api_key_for_provider_call,
    )

    clear_openai_collaboration()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LILA_ENABLE_ARBITER_OPENAI", raising=False)
    client = srv.app.test_client()
    key = "sk-proj-abcdef_1234567890TESTKEY9999"

    # off by default
    status = client.get("/api/collaboration/openai")
    assert status.headers["Cache-Control"] == "no-store"
    assert status.get_json()["active"] is False

    r = client.post("/api/collaboration/openai", json={
        "api_key": key,
        "mode": "required",
        "model": "gpt-5-2025-08-07",
    })
    body = r.get_json()
    assert r.status_code == 200 and body["configured"] is True
    assert body["active"] is True
    assert body["mode"] == "required"
    assert body["model"] == "gpt-5-2025-08-07"
    assert body["masked"] == "••••"
    assert key not in json.dumps(body)
    assert "OPENAI_API_KEY" not in os.environ
    assert "LILA_ENABLE_ARBITER_OPENAI" not in os.environ
    assert openai_api_key_for_provider_call() == key
    # Compatibility path reads the same non-secret vault status.
    assert client.get("/api/arbiter/openai").get_json() == {
        field: body[field]
        for field in (
            "active", "key_present", "masked", "mode", "model",
            "purpose", "storage",
        )
    }

    # Legacy deactivate remains a clear alias but never touches the environment.
    d = client.post(
        "/api/arbiter/openai", json={"deactivate": True}
    ).get_json()
    assert d["active"] is False and d["cleared"] is True
    assert "OPENAI_API_KEY" not in os.environ
    assert openai_api_key_for_provider_call() is None


def test_openai_collaborator_rejects_bad_shapes_without_storing(monkeypatch):
    from agents.candidate_review_v1.collaboration_session import (
        clear_openai_collaboration,
    )

    clear_openai_collaboration()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = srv.app.test_client()
    r = client.post(
        "/api/collaboration/openai", json={"api_key": "not-a-key"}
    )
    assert r.status_code == 400
    assert "OPENAI_API_KEY" not in os.environ
    assert client.get(
        "/api/collaboration/openai"
    ).get_json()["active"] is False
    assert client.post(
        "/api/collaboration/openai", json={"mode": "required"}
    ).status_code == 400
    assert client.post(
        "/api/collaboration/openai", json={"api_key": 123}
    ).status_code == 400
    assert client.post(
        "/api/collaboration/openai", json=["not", "an", "object"]
    ).status_code == 400
    assert client.post(
        "/api/collaboration/openai", json={"unexpected": True}
    ).status_code == 400


def test_openai_collaborator_api_never_echoes_secret_shaped_model(monkeypatch):
    from agents.candidate_review_v1.collaboration_session import (
        OPENAI_COLLABORATION_SESSION,
        clear_openai_collaboration,
    )

    clear_openai_collaboration()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = srv.app.test_client()
    key = "sk-proj-validsession_1234567890abcdef"
    configured = client.post("/api/collaboration/openai", json={
        "api_key": key,
        "mode": "advisory",
        "model": "gpt-5-2025-08-07",
    })
    assert configured.status_code == 200

    submitted_model = "sk-proj-modelsecret_9876543210"
    rejected = client.post("/api/collaboration/openai", json={
        "model": submitted_model,
    })
    rejected_json = json.dumps(rejected.get_json(), ensure_ascii=False)
    assert rejected.status_code == 400
    assert submitted_model not in rejected_json
    assert key not in rejected_json

    status = client.get("/api/collaboration/openai").get_json()
    ui_safe_json = json.dumps(status, ensure_ascii=False)
    assert status["model"] == "gpt-5-2025-08-07"
    assert status["mode"] == "advisory"
    assert status["active"] is True
    assert submitted_model not in ui_safe_json
    assert key not in ui_safe_json
    assert submitted_model not in repr(OPENAI_COLLABORATION_SESSION)
    assert key not in repr(OPENAI_COLLABORATION_SESSION)
    clear_openai_collaboration()


def test_server_startup_scrubs_ambient_openai_legacy_state(monkeypatch):
    from agents.candidate_review_v1.collaboration_session import (
        clear_openai_collaboration,
    )

    clear_openai_collaboration()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient_legacy_1234567890")
    monkeypatch.setenv("LILA_ENABLE_ARBITER_OPENAI", "on")
    monkeypatch.setenv("OPENAI_ARBITER_MODEL", "legacy-model")

    srv._scrub_legacy_openai_environment()

    assert "OPENAI_API_KEY" not in os.environ
    assert "LILA_ENABLE_ARBITER_OPENAI" not in os.environ
    assert "OPENAI_ARBITER_MODEL" not in os.environ
    status = srv.app.test_client().get(
        "/api/collaboration/openai"
    ).get_json()
    assert status["active"] is False
    assert status["key_present"] is False
    assert status["mode"] == "off"


def test_main_scrubs_openai_values_loaded_from_env_before_app_run(monkeypatch):
    import tools.env

    observed = {}

    def load_legacy_env():
        os.environ["OPENAI_API_KEY"] = "sk-loaded_legacy_1234567890"
        os.environ["LILA_ENABLE_ARBITER_OPENAI"] = "on"
        os.environ["OPENAI_ARBITER_MODEL"] = "legacy-model"

    def run_app(**kwargs):
        observed["kwargs"] = kwargs
        observed["openai"] = {
            name: os.environ.get(name)
            for name in (
                "OPENAI_API_KEY",
                "LILA_ENABLE_ARBITER_OPENAI",
                "OPENAI_ARBITER_MODEL",
            )
        }

    monkeypatch.setattr(tools.env, "load_env", load_legacy_env)
    monkeypatch.setattr(srv.app, "run", run_app)
    monkeypatch.setenv("LILA_UI_PORT", "9137")

    assert srv.main() == 0
    assert observed["kwargs"] == {
        "host": "127.0.0.1",
        "port": 9137,
        "debug": False,
    }
    assert observed["openai"] == {
        "OPENAI_API_KEY": None,
        "LILA_ENABLE_ARBITER_OPENAI": None,
        "OPENAI_ARBITER_MODEL": None,
    }


def test_openai_arbiter_default_off_keeps_anthropic_consensus(monkeypatch):
    """With no key, only the Anthropic arbiter is active — the report still
    reaches consensus (the cross-check is additive, never required)."""
    from agents.candidate_review_v1.collaboration_session import (
        clear_openai_collaboration,
    )

    clear_openai_collaboration()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LILA_ENABLE_ARBITER_OPENAI", raising=False)
    from agents.decisions.arbiters import ARBITERS
    names = {a.name for a in ARBITERS.active()}
    assert "arbiter-openai" not in names


def _upload_png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (24, 16), (14, 92, 154, 255)).save(
        out, format="PNG")
    return out.getvalue()


def _editor_headers(**extra):
    return {"X-LILA-Editor-Token": srv._SIGNAL_BOARD_EDITOR_TOKEN, **extra}


def _allow_targeting_preview(monkeypatch, slug="testco"):
    monkeypatch.setattr(srv, "_report_client_slug", lambda _path: slug)
    monkeypatch.setattr(
        srv, "_client_targets_payload",
        lambda _slug: ({"targeting_readiness": {"ready": True,
                                                 "problems": []}}, 200),
    )


def test_signal_board_preview_injects_editor_but_artifact_stays_script_free(
        tmp_path, monkeypatch):
    root = str(tmp_path)
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    source = (
        '<html><body><div data-brand-kind="company" '
        'data-brand-key="company:booz_allen_hamilton" '
        'data-brand-label="Booz Allen Hamilton">Booz Allen Hamilton</div>'
        '</body></html>'
    )
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", root)
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(tmp_path / "desktop"))
    _allow_targeting_preview(monkeypatch)

    response = srv.app.test_client().get(
        "/report?path=" + str(path) + "&logo_editor=1")

    assert response.status_code == 200
    assert "/ui/signal-board-logo-editor.js" in response.get_data(as_text=True)
    assert 'data-client="testco"' in response.get_data(as_text=True)
    assert 'data-editor-token="' in response.get_data(as_text=True)
    assert ('data-download-url="/client/testco/download/'
            'signal-board.html"' in response.get_data(as_text=True))
    assert "<script" not in path.read_text(encoding="utf-8")


def test_signal_board_editor_keeps_client_safe_source_coverage_collapsed(
        tmp_path, monkeypatch):
    root = str(tmp_path)
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    source = (
        '<html><body><div data-brand-kind="client">Testco</div>'
        '<section class="band" id="research-sources">'
        '<details class="sb-source-coverage-details">'
        '<summary><span class="sb-source-client-only">33 connected</span>'
        '<span class="sb-source-operator-only">3 failed</span></summary>'
        '</details></section></body></html>'
    )
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", root)
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(tmp_path / "desktop"))
    _allow_targeting_preview(monkeypatch)
    client = srv.app.test_client()

    public = client.get("/report?path=" + str(path)).get_data(as_text=True)
    operator = client.get(
        "/report?path=" + str(path) + "&logo_editor=1"
    ).get_data(as_text=True)

    assert 'id="research-sources" data-source-view=' not in public
    assert '<details class="sb-source-coverage-details">' in public
    assert 'id="research-sources" data-source-view=' not in operator
    assert '<details class="sb-source-coverage-details">' in operator
    assert '<details class="sb-source-coverage-details" open>' not in operator
    assert '<span class="sb-source-client-only">33 connected</span>' in operator
    assert '<span class="sb-source-operator-only">3 failed</span>' in operator
    assert path.read_text(encoding="utf-8") == source


def test_signal_board_editor_token_and_upload_stay_on_loopback_host(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    path.write_text(
        '<html><body><div data-brand-kind="agency" '
        'data-brand-key="agency:dhs" data-brand-label="USCIS">'
        'USCIS</div></body></html>', encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    _allow_targeting_preview(monkeypatch)
    client = srv.app.test_client()

    preview = client.get(
        "/report?path=" + str(path) + "&logo_editor=1",
        base_url="http://attacker.example")
    upload = client.post(
        "/api/client/testco/signal-board/logo",
        base_url="http://attacker.example",
        data={"kind": "agency", "key": "agency:dhs", "label": "USCIS",
              "file": (io.BytesIO(_upload_png()), "seal.png")},
        headers=_editor_headers(), content_type="multipart/form-data")

    assert preview.status_code == 200
    assert "/ui/signal-board-logo-editor.js" not in preview.get_data(
        as_text=True)
    assert srv._SIGNAL_BOARD_EDITOR_TOKEN not in preview.get_data(as_text=True)
    assert upload.status_code == 403
    assert upload.get_json()["error"] == "mark editor is local-only"


def test_report_preview_fails_closed_without_current_targeting_review(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    path.write_text("<html><body>client-ready</body></html>", encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    monkeypatch.setattr(srv, "_report_client_slug", lambda _path: "testco")
    monkeypatch.setattr(
        srv, "_client_targets_payload",
        lambda _slug: ({"targeting_readiness": {
            "ready": False, "problems": ["Targeting Review has not been approved"],
        }}, 200),
    )
    response = srv.app.test_client().get("/report?path=" + str(path))
    assert response.status_code == 409
    assert response.get_json()["do_not_send"] is True
    assert "complete Targeting Review" in response.get_json()["error"]
    assert b"<html>" not in response.data


def test_logo_drop_persists_override_without_mutating_certified_draft(
        tmp_path, monkeypatch):
    root = str(tmp_path)
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    source = (
        '<html><body><div data-brand-kind="company" '
        'data-brand-key="company:booz_allen_hamilton" '
        'data-brand-label="Booz Allen Hamilton">Booz Allen Hamilton</div>'
        '</body></html>'
    )
    path.write_text(source, encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(srv, "ROOT", root)
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")

    response = srv.app.test_client().post(
        "/api/client/testco/signal-board/logo",
        data={
            "kind": "company",
            "key": "company:booz_allen_hamilton",
            "label": "Booz Allen Hamilton",
            "file": (io.BytesIO(_upload_png()), "booz.png"),
        },
        headers=_editor_headers(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    assert response.get_json()["identity"] == "company:booz_allen_hamilton"
    assert path.read_bytes() == before
    manifest = (tmp_path / "clients" / "testco"
                / "signal_board_presentation.json")
    assert manifest.is_file()
    assert "company:booz_allen_hamilton" in manifest.read_text()


def test_portrait_drop_and_size_are_bound_to_exact_rendered_person(
        tmp_path, monkeypatch):
    from agents.reports.signal_board_presentation import (
        logo_identity,
        person_identity_label,
    )

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    claim = person_identity_label(
        "Renee Leaman", "U.S. Marshals Service", "SAM.gov", "notice-a")
    key = logo_identity("Testco", "person", claim)
    path.write_text(
        '<div class="sb-person-portrait-target" data-brand-kind="person" '
        f'data-brand-key="{key}" data-brand-label="'
        f'{html.escape(claim, quote=True)}" '
        'data-brand-display="Renee Leaman">RL</div>',
        encoding="utf-8",
    )
    before = path.read_bytes()
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    client = srv.app.test_client()

    upload = client.post(
        "/api/client/testco/signal-board/logo",
        data={
            "kind": "person", "key": key, "label": claim,
            "file": (io.BytesIO(_upload_png()), "renee.png"),
        },
        headers=_editor_headers(),
        content_type="multipart/form-data",
    )
    resized = client.post(
        "/api/client/testco/signal-board/logo-size",
        json={
            "kind": "person", "key": key, "label": claim,
            "percent": 135,
        },
        headers=_editor_headers(),
    )

    assert upload.status_code == 201
    assert resized.status_code == 201
    assert upload.get_json()["identity"] == key
    assert resized.get_json()["identity"] == key
    assert path.read_bytes() == before
    manifest = json.loads((
        tmp_path / "clients" / "testco"
        / "signal_board_presentation.json").read_text())
    assert key in manifest["logo_overrides"]
    assert manifest["logo_sizes"] == {key: 135}


def test_logo_size_persists_without_mutating_certified_draft(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    source = (
        '<html><body><div data-brand-kind="agency" '
        'data-brand-key="agency:treas" '
        'data-brand-label="Department of the Treasury">Treasury seal'
        '</div></body></html>'
    )
    path.write_text(source, encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")

    response = srv.app.test_client().post(
        "/api/client/testco/signal-board/logo-size",
        json={
            "kind": "agency",
            "key": "agency:treas",
            "label": "Department of the Treasury",
            "percent": 145,
        },
        headers=_editor_headers(),
    )

    assert response.status_code == 201
    assert response.get_json()["identity"] == "agency:treas"
    assert response.get_json()["percent"] == 145
    assert response.headers["Cache-Control"] == "no-store"
    assert path.read_bytes() == before
    manifest = json.loads((
        tmp_path / "clients" / "testco"
        / "signal_board_presentation.json").read_text())
    assert manifest["logo_sizes"] == {"agency:treas": 145}


def test_logo_size_refuses_invalid_or_unrendered_identity(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "testco.federal_opportunity_signals.html").write_text(
        '<div data-brand-kind="agency" data-brand-key="agency:treas" '
        'data-brand-label="Department of the Treasury">Treasury</div>',
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    client = srv.app.test_client()
    endpoint = "/api/client/testco/signal-board/logo-size"

    invalid = client.post(endpoint, json={
        "kind": "agency", "key": "agency:treas",
        "label": "Department of the Treasury", "percent": 207,
    }, headers=_editor_headers())
    unrendered = client.post(endpoint, json={
        "kind": "company", "key": "company:example",
        "label": "Example", "percent": 125,
    }, headers=_editor_headers())

    assert invalid.status_code == 400
    assert "between 50 and 200" in invalid.get_json()["error"]
    assert unrendered.status_code == 409
    assert not (tmp_path / "clients" / "testco"
                / "signal_board_presentation.json").exists()


@pytest.mark.parametrize(("field", "value"), [
    ("kind", 7),
    ("kind", " "),
    ("key", ["client:testco"]),
    ("key", ""),
    ("label", {"name": "Testco"}),
    ("label", "\t"),
])
def test_logo_size_rejects_non_string_or_empty_identity_fields(
        tmp_path, monkeypatch, field, value):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "testco.federal_opportunity_signals.html").write_text(
        '<div data-brand-kind="client" data-brand-key="client:testco" '
        'data-brand-label="Testco">Testco</div>',
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    payload = {
        "kind": "client", "key": "client:testco",
        "label": "Testco", "percent": 125,
    }
    payload[field] = value

    response = srv.app.test_client().post(
        "/api/client/testco/signal-board/logo-size",
        json=payload,
        headers=_editor_headers(),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "kind, key, and label must be non-empty strings")
    assert not (tmp_path / "clients" / "testco"
                / "signal_board_presentation.json").exists()


def test_header_companion_edit_persists_without_mutating_certified_draft(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    path = reports / "testco.federal_opportunity_signals.html"
    source = (
        '<html><body><div class="sb-brand" data-brand-kind="client" '
        'data-brand-key="client:testco" data-brand-label="Testco">'
        '<span class="sb-brand-sub">FEDERAL OPPORTUNITY PRE-ASSESSMENT'
        '</span></div></body></html>'
    )
    path.write_text(source, encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")

    response = srv.app.test_client().post(
        "/api/client/testco/signal-board/header-companion",
        json={"text": "FEDERAL CAPTURE PRIORITIES"},
        headers=_editor_headers(),
    )

    assert response.status_code == 201
    assert response.get_json()["text"] == "FEDERAL CAPTURE PRIORITIES"
    assert response.headers["Cache-Control"] == "no-store"
    assert path.read_bytes() == before
    manifest = json.loads((
        tmp_path / "clients" / "testco"
        / "signal_board_presentation.json").read_text())
    assert manifest["client_slug"] == "testco"
    assert manifest["header_companion_text"] == "FEDERAL CAPTURE PRIORITIES"


def test_header_companion_edit_requires_loopback_token_and_same_origin(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "testco.federal_opportunity_signals.html").write_text(
        '<div class="sb-brand" data-brand-kind="client" '
        'data-brand-key="client:testco" data-brand-label="Testco">'
        '<span class="sb-brand-sub">CURRENT</span></div>',
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    client = srv.app.test_client()
    endpoint = "/api/client/testco/signal-board/header-companion"

    remote = client.post(
        endpoint, base_url="http://attacker.example",
        json={"text": "REMOTE"}, headers=_editor_headers())
    missing_token = client.post(endpoint, json={"text": "NO TOKEN"})
    foreign_origin = client.post(
        endpoint, json={"text": "FOREIGN"},
        headers=_editor_headers(Origin="https://example.invalid"))
    cross_site = client.post(
        endpoint, json={"text": "CROSS SITE"},
        headers=_editor_headers(**{"Sec-Fetch-Site": "cross-site"}))
    oversized = client.post(
        endpoint, json={"text": "X" * (srv._MAX_HEADER_COMPANION_BODY + 1)},
        headers=_editor_headers())

    assert remote.status_code == 403
    assert remote.get_json()["error"] == "mark editor is local-only"
    assert missing_token.status_code == 403
    assert foreign_origin.status_code == 403
    assert cross_site.status_code == 403
    assert oversized.status_code == 413
    assert not (tmp_path / "clients" / "testco"
                / "signal_board_presentation.json").exists()


@pytest.mark.parametrize("text", [
    "line one\nline two",
    "<script>alert(1)</script>",
    "X" * 65,
])
def test_header_companion_edit_rejects_invalid_public_copy(
        tmp_path, monkeypatch, text):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "testco.federal_opportunity_signals.html").write_text(
        '<div data-brand-kind="client" data-brand-key="client:testco" '
        'data-brand-label="Testco"><span class="sb-brand-sub">CURRENT'
        '</span></div>', encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")

    response = srv.app.test_client().post(
        "/api/client/testco/signal-board/header-companion",
        json={"text": text}, headers=_editor_headers())

    assert response.status_code == 400
    assert not (tmp_path / "clients" / "testco"
                / "signal_board_presentation.json").exists()


def test_logo_drop_refuses_arbitrary_or_unrendered_identity(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "testco.federal_opportunity_signals.html").write_text(
        '<div data-brand-kind="agency" data-brand-key="agency:dhs" '
        'data-brand-label="USCIS">USCIS</div>', encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    client = srv.app.test_client()

    mismatch = client.post(
        "/api/client/testco/signal-board/logo",
        data={"kind": "agency", "key": "agency:doj", "label": "USCIS",
              "file": (io.BytesIO(_upload_png()), "seal.png")},
        headers=_editor_headers(),
        content_type="multipart/form-data")
    absent = client.post(
        "/api/client/testco/signal-board/logo",
        data={"kind": "company", "key": "company:unrendered",
              "label": "Unrendered",
              "file": (io.BytesIO(_upload_png()), "logo.png")},
        headers=_editor_headers(),
        content_type="multipart/form-data")

    assert mismatch.status_code == 409
    assert absent.status_code == 409


def test_logo_drop_requires_editor_session_and_rejects_body_before_parse(
        tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "testco.federal_opportunity_signals.html").write_text(
        '<div data-brand-kind="agency" data-brand-key="agency:dhs" '
        'data-brand-label="USCIS">USCIS</div>', encoding="utf-8")
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "REPORT_DIR", str(reports))
    _bind_run_workstation(monkeypatch, "Testco")
    client = srv.app.test_client()
    form = {"kind": "agency", "key": "agency:dhs", "label": "USCIS",
            "file": (io.BytesIO(_upload_png()), "seal.png")}

    missing = client.post(
        "/api/client/testco/signal-board/logo", data=form,
        content_type="multipart/form-data")
    foreign = client.post(
        "/api/client/testco/signal-board/logo",
        data={**form, "file": (io.BytesIO(_upload_png()), "seal.png")},
        headers=_editor_headers(Origin="https://example.invalid"),
        content_type="multipart/form-data")
    oversized = client.post(
        "/api/client/testco/signal-board/logo",
        data={**form, "file": (io.BytesIO(b"x" * (
            srv._MAX_LOGO_UPLOAD_BODY + 1)), "huge.png")},
        headers=_editor_headers(), content_type="multipart/form-data")

    assert missing.status_code == 403
    assert foreign.status_code == 403
    assert oversized.status_code == 413
    assert not (tmp_path / "clients" / "testco"
                / "signal_board_presentation.json").exists()


def test_logo_editor_supports_file_and_browser_search_image_drops():
    source = (Path(srv.__file__).parent
              / "signal_board_logo_editor.js").read_text()
    assert "dataTransfer.files" in source
    assert "text/uri-list" in source
    assert "text/html" in source
    assert source.index("getData('text/html')") < source.index(
        "getData('text/uri-list')")
    assert "getData('text/plain')" in source
    assert "credentials: 'omit'" in source
    assert "Save the image, then drop the file here" in source
    assert "target.classList.add('has-image')" in source
    assert "contains('sb-horizon-mark')" in source
    assert "return 'sb-horizon-logo'" in source
    assert "contains('sb-person-portrait-target')" in source
    assert "return 'sb-person-portrait'" in source
    assert "target.dataset.brandKind === 'person'" in source
    assert "target.dataset.brandDisplay" in source
    assert "Portrait slots are bound to the exact sourced person record" in source
    assert "ResizeObserver(syncEditorOffset).observe(bar)" in source
    assert "--sb-editor-offset" in source
    assert "top: var(--sb-editor-offset) !important" in source
    assert "body.sb-mark-editor-active .sb-utility {\n        top: 0 !important;" in source
    assert "--sb-editor-offset: 0px !important" in source
    assert "'padding-top', `${Math.ceil" not in source
    assert "The GTM certification mark stays locked" in source
    assert "header-companion" in source
    assert "Edit label" in source
    assert "JSON.stringify({ text: value })" in source
    assert "sb-copy-edit-button" in source
    assert "window.prompt" not in source
    assert "sb-copy-editor" in source
    assert "input.maxLength = 64" in source
    assert "saveButton.textContent = 'Save'" in source
    assert "cancelButton.textContent = 'Cancel'" in source
    assert "input.select()" in source
    assert "signal-board/logo-size" in source
    assert "sizeInput.type = 'range'" in source
    assert "sizeInput.min = '50'" in source
    assert "sizeInput.max = '200'" in source
    assert "sizeInput.step = '5'" in source
    assert "target.style.setProperty('--sb-brand-scale'" in source
    assert "percent," in source


def test_analyst_approval_auto_starts_the_pre_assessment_press(monkeypatch, tmp_path):
    # 2026-07-24 one-gate law (operator ruling 2026-07-23): input ->
    # inference -> Analyst approve -> search -> report. The approval click IS
    # the go: /api/decide starts the candidate_review job and reports it
    # additively; a refused start never rolls back the durable approval.
    from agents import review as review_mod
    from agents.decisions.schemas import IntakeStrategy

    review_dir = str(tmp_path)
    monkeypatch.setattr(review_mod, "REVIEW_DIR", review_dir)
    monkeypatch.setattr(srv, "REVIEW_DIR", review_dir)
    strategy = IntakeStrategy(
        client_name="Pending Co", pursuit_strategy="s", confidence=0.5,
        review_gate="g")
    packet = review_mod.ReviewPacket(client_name="Pending Co", strategy=strategy)
    os.makedirs(review_dir, exist_ok=True)

    def _reset_pending():
        with open(os.path.join(review_dir, "pending_co.review.json"), "w") as f:
            f.write(packet.model_dump_json())

    _reset_pending()
    _bind_run_workstation(monkeypatch, "Pending Co")
    started = []
    monkeypatch.setattr(
        srv, "start_job",
        lambda step, client, args: (
            started.append((step, client)) or "job-auto-1"))
    client = srv.app.test_client()
    r = client.post("/api/decide", json={
        "client_name": "Pending Co", "approve": True, "note": ""})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "approved"
    assert body["job_id"] == "job-auto-1"
    assert started == [("candidate_review", "Pending Co")]

    _reset_pending()
    r2 = client.post("/api/decide", json={
        "client_name": "Pending Co", "approve": False, "note": "no"})
    assert r2.status_code == 200
    assert "job_id" not in r2.get_json()

    _reset_pending()

    def _boom(step, client_name, args):
        raise RuntimeError("another job is already running")

    monkeypatch.setattr(srv, "start_job", _boom)
    r3 = client.post("/api/decide", json={
        "client_name": "Pending Co", "approve": True, "note": ""})
    assert r3.status_code == 200
    b3 = r3.get_json()
    assert b3["status"] == "approved"
    assert "auto_run_note" in b3 and "not auto-started" in b3["auto_run_note"]


def test_detail_payload_names_the_missing_capability_profile(monkeypatch, tmp_path):
    # 2026-07-24 fresh-client cliff: the page says when the populated profile
    # is missing (and links the guided forms) instead of letting the sweep
    # gate surface it later inside a job log.
    review = _mk_client(str(tmp_path))
    state = srv.client_state("acme_federal", review)
    assert state["profile_populated"] is False
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "btn-complete-profile" in html
    assert "openOnboardingDialog(d.slug)" in html


def test_clean_pass_retires_action_presses_and_adds_the_calendar():
    # 2026-07-24 operator ruling: no re-screen, no re-search, no dossier
    # presses; the read surfaces (results, raw-opp drawers) stay; the
    # report's Federal opportunity calendar joins the operating screen.
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "Re-screen (no quota)" not in html
    assert "Re-search (spends SAM quota)" not in html
    assert "Generate Pursuit Dossier" not in html
    assert "Build pursuit dossiers" not in html
    assert "buildOppReview" in html          # the drill-in views stay
    assert "buildFederalCalendar" in html
    assert "fedcalSlot" in html


def test_calendar_endpoint_is_empty_without_a_document(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    client = srv.app.test_client()
    response = client.get("/api/client/acme_federal/calendar")
    assert response.status_code == 200
    assert response.get_json() == {"items": []}


def test_step_cmd_maps_the_ranking_workbook():
    cmd = srv._step_cmd("ranking_workbook", "Riverbed", {})
    assert cmd[1:] == ["run_ranking_workbook.py", "--client", "Riverbed"]


def test_step_cmd_maps_the_one_external_release_action():
    cmd = srv._step_cmd("lila_release", "Riverbed", {})
    assert cmd[1:] == ["run_lila_release.py", "--client", "Riverbed", "--release"]


def test_gate_refreshes_research_and_finale_owns_release():
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert ">Refresh research</button>" in html
    assert "Approve + Refresh research" in html
    assert "step: 'candidate_review'" in html
    assert "step: 'lila_release'" in html


def test_home_ticker_route_and_tape_exist(monkeypatch):
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    # 2026-07-25 ruling: the tape lives on client views only, filtered
    # to that client; the standalone approve-results press is retired and
    # the Target door carries the sign-off in one act.
    assert "buildClientTicker" in html and "hometicker" not in html
    assert "Approve results \u2192 activate Produce" not in html
    assert "open Target" in html
    import ui.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "radar", lambda: {"cards": []})
    monkeypatch.setattr(
        lifecycle, "ticker",
        lambda cards: {"items": [
            {"kind": "window_closing", "slug": "mark43",
             "client_name": "Mark43",
             "text": "window closes in 3d", "when": "2026-07-28"}],
            "capped": False})
    response = srv.app.test_client().get("/api/ticker")
    assert response.status_code == 200
    body = response.get_json()
    assert body["items"][0]["slug"] == "mark43"


def test_sidebar_pins_the_calendar_and_key_targets(monkeypatch, tmp_path):
    # 2026-07-25 operator ruling: the calendar and key targets are pinned,
    # clickable, book-of-business views; empty worlds answer honestly.
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert 'onclick="showCalendar()"' in html
    assert 'onclick="showTargets()"' in html
    # 2026-07-25: the calendar is a real month grid with day cells.
    assert 'calgrid' in html and 'renderCalendarMonth' in html
    assert html.count('calcell') >= 2
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    client = srv.app.test_client()
    assert client.get("/api/calendar").get_json() == {"items": []}
    assert client.get("/api/targets").get_json() == {"clients": []}


def test_sam_key_panel_stores_operator_entered_keys(monkeypatch, tmp_path):
    # 2026-07-25: the operator types the keys into the dashboard; the server
    # stores them (.env + live process env) and never echoes a value.
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert "renderSamKeys" in html and 'type="password"' in html
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
    monkeypatch.delenv("SAM_GOV_API_KEY_2", raising=False)
    client = srv.app.test_client()
    state = client.get("/api/keys/sam").get_json()
    assert state["primary_set"] is False
    key_one = "SAM-" + "a" * 32
    key_two = "SAM-" + "b" * 32
    response = client.post("/api/keys/sam", json={
        "primary": key_one, "secondary": key_two})
    body = response.get_json()
    assert response.status_code == 200
    assert body == {"primary_set": True, "secondary_set": True}
    assert key_one not in response.get_data(as_text=True)
    env_text = open(os.path.join(str(tmp_path), ".env")).read()
    assert f"SAM_GOV_API_KEY={key_one}" in env_text
    assert f"SAM_GOV_API_KEY_2={key_two}" in env_text
    assert os.environ["SAM_GOV_API_KEY"] == key_one
    assert client.post("/api/keys/sam", json={}).status_code == 400
    assert client.post(
        "/api/keys/sam", json={"primary": "short"}).status_code == 400


def test_dedicated_keys_page_serves_the_form(monkeypatch):
    monkeypatch.delenv("SAM_GOV_API_KEY_2", raising=False)
    response = srv.app.test_client().get("/keys")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "SAM.gov API keys" in page and "type=password" in page
    assert "/api/keys/sam" in page
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert ">API keys</b></button>" in html.replace("<b", ">API keys</b>" and "<b") or "API keys" in html


def test_assessment_bundle_can_run_before_targets_exist_but_target_report_cannot(monkeypatch):
    _bind_run_workstation(monkeypatch)
    monkeypatch.setattr(srv, "load_packet", lambda *_a, **_k: SimpleNamespace(
        status=srv.ReviewStatus.APPROVED, search_scope=None))
    monkeypatch.setattr(srv, "_assess_release_gate", lambda *_a: (None, "approved", []))
    monkeypatch.setattr(srv, "_client_targets_payload", lambda *_a: (
        {"targeting_readiness": {"ready": False, "problems": ["no promoted targets"]}}, 200))
    import agents.review
    monkeypatch.setattr(agents.review, "target_gate_status", lambda *_a, **_k: (True, []))
    calls = []
    monkeypatch.setattr(srv, "start_job", lambda step, *_a: calls.append(step) or "job-1")
    client = srv.app.test_client()
    assessment = client.post("/api/run", json={"client_name": "Testco", "step": "lila_release", "args": {"no_desktop": True}})
    assert assessment.status_code == 200
    targeting = client.post("/api/run", json={"client_name": "Testco", "step": "target_report", "args": {}})
    assert targeting.status_code == 409
    assert calls == ["lila_release"]
    assert srv._step_cmd("lila_release", "Testco", {"no_desktop": True})[-1] == "--no-desktop"
