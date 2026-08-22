"""WS3 (review findings 3, 4, 5, 9): ONE truth for releasable.

agents/reports/release.py is the single derivation point. The matrix here is
the review's acceptance bar: for every combination of sidecar state, approval
state, newer-blocked-view, and legacy artifacts, the badge (_final_brief),
the shelf, and the download boundary give the SAME answer.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

import ui.server as srv  # noqa: E402
from agents.reports.release import release_state, signal_board_status  # noqa: E402
from tests.test_ui import _mk_client  # noqa: E402

SLUG = "acme_federal"
SIGNAL_LINTS = {
    name: {"ok": True, "violations": []}
    for name in (
        "signal_board", "sam_workspace_links",
        "federal_link_construction", "whitelabel",
        "client_bleed", "emdash",
    )
}


def _approve(monkeypatch, status="approved", problems=None):
    monkeypatch.setattr("agents.reports.release.assess_approval_for_release",
                        lambda *a, **k: (None, status, list(problems or [])))


def _write_stable(report_dir, state="release", paired=True, when=None):
    html_path = os.path.join(report_dir,
                             f"{SLUG}.federal_opportunity_assessment.html")
    body = f"<html>build {state} {when}</html>"
    with open(html_path, "w") as f:
        f.write(body)
    sha = hashlib.sha256(body.encode()).hexdigest() if paired else "0" * 64
    with open(html_path[:-len(".html")] + ".qa.json", "w") as f:
        json.dump({"state": state, "html_sha256": sha}, f)
    if when:
        os.utime(html_path, (when, when))
    return html_path


def _write_signal(report_dir, *, overrides=None, when=None):
    html_path = os.path.join(
        report_dir, f"{SLUG}.federal_opportunity_signals.html")
    body = ("<html><title>Acme Federal · "
            "Federal Opportunity Pre-Assessment</title></html>")
    with open(html_path, "w") as f:
        f.write(body)
    root = Path(report_dir)
    if root.name == "reports" and root.parent.name == "data":
        root = root.parents[1]
    from agents.reports.signal_board_presentation import presentation_digest
    payload = {
        "schema_version": 2,
        "lint_contract_version": 1,
        "client_name": "Acme Federal",
        "presentation_name": "Acme Federal",
        "slug": SLUG,
        "state": "release",
        "release_eligible": True,
        "gate_verdict": "- CLEAN (client-final)",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "presentation_sha256": presentation_digest(
            "Acme Federal", root=root),
        "press_timestamp": "2026-07-19T15:00:00Z",
        "static_lints": SIGNAL_LINTS,
        "federal_link_manifest": [],
    }
    payload.update(overrides or {})
    qa_path = html_path[:-len(".html")] + ".qa.json"
    with open(qa_path, "w") as f:
        json.dump(payload, f)
    if when is not None:
        os.utime(html_path, (when, when))
        os.utime(qa_path, (when + 0.01, when + 0.01))
    return html_path, qa_path


def _seed(tmp_path, monkeypatch):
    _mk_client(str(tmp_path))
    monkeypatch.setattr(srv, "REVIEW_DIR",
                        os.path.join(str(tmp_path), "data", "review"))
    report_dir = os.path.join(str(tmp_path), "data", "reports")
    os.makedirs(report_dir, exist_ok=True)
    monkeypatch.setattr(srv, "REPORT_DIR", report_dir)
    # Release-family matrix tests isolate Assess/QA/certificate semantics;
    # Targeting Review release enforcement has separate fail-closed coverage.
    monkeypatch.setattr(
        srv, "_client_targets_payload",
        lambda _slug: ({"targeting_readiness": {"ready": True,
                                                 "problems": []}}, 200),
    )
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(tmp_path / "nodesk"))
    return report_dir


def _verdicts(client):
    """(badge, download_ok) — the two surfaces that must always agree."""
    fb = srv._final_brief(SLUG)
    r = client.get(f"/client/{SLUG}/download/foa.html")
    return (None if fb is None else fb["qa_pass"]), r.status_code == 200


def test_badge_and_download_agree_across_the_matrix(tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    client = srv.app.test_client()

    # nothing on file
    assert _verdicts(client) == (None, False)

    # clean release + approval -> both yes
    _approve(monkeypatch)
    _write_stable(report_dir, when=time.time() - 60)
    srv._HTML_SHA_MEMO.clear()
    assert _verdicts(client) == (True, True)

    # approval lapses -> BOTH refuse (the review's headline disagreement)
    _approve(monkeypatch, "invalid", ["Assess sweep evidence changed"])
    badge, dl = _verdicts(client)
    assert (badge, dl) == (False, False)
    assert "not approved" in srv._final_brief(SLUG)["reason"]

    # approval back, but a NEWER blocked view build supersedes (finding #4/#5)
    _approve(monkeypatch)
    blocked = os.path.join(report_dir,
                           f"{SLUG}.assessment.client.DO-NOT-SEND.html")
    with open(blocked, "w") as f:
        f.write("<html>failed views build</html>")
    now = time.time() + 5
    os.utime(blocked, (now, now))
    badge, dl = _verdicts(client)
    assert (badge, dl) == (False, False)
    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert rs["family"] == "view_bad"

    # the blocked view is cleared and a fresh clean view lands -> both yes
    os.remove(blocked)
    view = os.path.join(report_dir, f"{SLUG}.assessment.client.html")
    with open(view, "w") as f:
        f.write("<html>clean client view</html>")
    os.utime(view, (now + 5, now + 5))
    assert _verdicts(client) == (True, True)


def test_new_presentation_mark_blocks_old_certificate_until_repress(
        tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    html_path, qa_path = _write_signal(
        report_dir, when=time.time() - 60)
    from agents.reports.signal_board_presentation import presentation_path
    manifest = presentation_path("Acme Federal", root=tmp_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "client_slug": SLUG,
        "logo_overrides": {},
    }), encoding="utf-8")
    now = time.time()
    os.utime(manifest, (now, now))

    dirty = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG,
        qa_path=qa_path)

    assert dirty["qa_pass"] is False
    assert dirty["presentation_current"] is False
    assert "presentation marks changed" in dirty["reason"]

    os.utime(qa_path, (now + 1, now + 1))
    still_dirty = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG,
        qa_path=qa_path)
    assert still_dirty["qa_pass"] is False
    assert still_dirty["presentation_current"] is False

def test_real_multiword_approval_releases_through_slug_route_unstubbed(
        tmp_path, monkeypatch):
    """A URL slug must resolve to the exact packet identity before approval.

    This intentionally stubs neither side of the release comparison: the
    approval is written for ``Acme Federal`` while badge and download enter
    through the normal ``acme_federal`` route.
    """
    import agents.review as review
    import tools.capability as capability
    from tools.capability import CapabilityTerms, ClientProfile

    report_dir = _seed(tmp_path, monkeypatch)
    cleaned = tmp_path / "data" / "cleaned"
    clients = tmp_path / "clients"
    assess_runs = tmp_path / "data" / "state" / "assess_runs"
    cleaned.mkdir(parents=True, exist_ok=True)
    (clients / SLUG).mkdir(parents=True)
    (cleaned / f"searches_{SLUG}.json").write_text(json.dumps({
        "client": "Acme Federal",
        "search_scope": {"all": True},
        "results": {"sam.gov": []},
    }), encoding="utf-8")
    profile = ClientProfile(
        client_name="Acme Federal",
        capability_terms=CapabilityTerms(
            core=["network monitoring"], adjacent=[], excluded=[]),
        mission_components=["operations"],
        naics_boundary=["541512"],
    )
    (clients / SLUG / "profile.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8")
    monkeypatch.setattr(review, "REVIEW_DIR", srv.REVIEW_DIR)
    monkeypatch.setattr(capability, "CLIENTS_DIR", str(clients))
    monkeypatch.setattr(srv, "CLEANED_DIR", str(cleaned))
    monkeypatch.setattr(srv, "ASSESS_RUN_DIR", str(assess_runs))

    client = srv.app.test_client()
    approved = client.post("/api/review/assess-approve", json={
        "client_name": "Acme Federal",
    })
    assert approved.status_code == 200
    saved = json.loads((
        tmp_path / "data" / "review" /
        f"{SLUG}.assess_approval.json").read_text(encoding="utf-8"))
    assert saved["client"] == "Acme Federal"
    _write_stable(report_dir)
    srv._HTML_SHA_MEMO.clear()

    state = srv._final_brief(SLUG)
    assert state and state["qa_pass"] is True
    response = client.get(
        f"/client/{SLUG}/download/foa.html")
    assert response.status_code == 200
    resolved = release_state(
        SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert resolved["approval_status"] == "approved"
    assert resolved["releasable"] is True


def test_sidecar_time_basis_never_outranks_a_newer_blocked_build(tmp_path, monkeypatch):
    """Finding #5's exact interleaving: stable html at T1, blocked view at T3,
    stable sidecar at T2 with T1 < T3 < T2. Ordering is html-mtime only, so
    the newer blocked build wins and both surfaces refuse."""
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    t1 = time.time() - 100
    html_path = _write_stable(report_dir, when=t1)
    blocked = os.path.join(report_dir,
                           f"{SLUG}.assessment.client.DO-NOT-SEND.html")
    with open(blocked, "w") as f:
        f.write("<html>newer failed build</html>")
    os.utime(blocked, (t1 + 50, t1 + 50))                  # T3
    qa = html_path[:-len(".html")] + ".qa.json"
    os.utime(qa, (t1 + 90, t1 + 90))                       # T2 > T3
    srv._HTML_SHA_MEMO.clear()
    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert rs["releasable"] is False
    assert rs["family"] == "view_bad"
    client = srv.app.test_client()
    assert client.get(f"/client/{SLUG}/download/foa.html").status_code == 409


def test_scoped_gate_never_reads_all_scope_or_legacy_artifacts(tmp_path, monkeypatch):
    """L19 + finding #9: a scoped gate resolves only its designator family;
    legacy capture_brief is an all-scope artifact and never a scoped
    candidate, and the all-scope shelf card is not suppressed by scoped
    builds (glob reverted to same-family)."""
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    # all-scope legacy + an agency-scoped stable build side by side
    legacy = os.path.join(report_dir, f"{SLUG}.capture_brief.html")
    with open(legacy, "w") as f:
        f.write("<html>legacy all-scope</html>")
    scoped_html = os.path.join(
        report_dir, f"{SLUG}.agency_dhs.federal_opportunity_assessment.html")
    with open(scoped_html, "w") as f:
        f.write("<html>scoped</html>")

    # all-scope gate: legacy resolves; the scoped file is not its candidate
    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert rs["family"] == "legacy" and rs["releasable"] is True

    # shelf: the scoped build must NOT hide the legacy card
    docs = srv.client_documents(SLUG)
    labels = {os.path.basename(d["path"]) for d in docs}
    assert f"{SLUG}.capture_brief.html" in labels

    # scoped gate: only the designator family resolves
    review = os.path.join(srv.REVIEW_DIR, f"{SLUG}.review.json")
    packet = json.load(open(review))
    packet["search_scope"] = {"agencies": ["DHS"], "mode": "focus"}
    with open(review, "w") as f:
        json.dump(packet, f)
    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert rs["family"] == "stable"
    assert "agency_dhs" in (rs["preview_path"] or "")


def test_report_preview_banner_stamps_unreleasable_deliverables(tmp_path, monkeypatch):
    """Finding #4: the /report preview stays available but a deliverable the
    boundary refuses carries a NOT RELEASABLE banner; a releasable one is
    served unstamped."""
    report_dir = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    _approve(monkeypatch, "missing", ["Assess results have not been approved"])
    html_path = _write_stable(report_dir)
    srv._HTML_SHA_MEMO.clear()
    client = srv.app.test_client()
    page = client.get("/report", query_string={"path": html_path}).data.decode()
    assert "NOT RELEASABLE" in page
    _approve(monkeypatch)
    page = client.get("/report", query_string={"path": html_path}).data.decode()
    assert "NOT RELEASABLE" not in page


def test_fresh_certified_signal_board_supersedes_legacy_and_downloads(
        tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    old = time.time() - 100
    _write_stable(report_dir, when=old)
    _write_signal(report_dir, when=old + 50)
    srv._HTML_SHA_MEMO.clear()

    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert rs["family"] == "signal_board"
    assert rs["releasable"] is True
    assert srv._signal_board(SLUG)["qa_pass"] is True
    monkeypatch.setattr(srv, "CLIENT_LINK_CHECKS_ENABLED", False)
    response = srv.app.test_client().get(
        f"/client/{SLUG}/download/signal-board.html")
    assert response.status_code == 200
    assert "Federal_Opportunity_Pre-Assessment" in response.headers[
        "Content-Disposition"]


@pytest.mark.parametrize("overrides,reason", [
    ({"schema_version": 3}, "schema is unsupported"),
    ({"schema_version": None}, "schema is unsupported"),
    ({"client_name": "Wrong"}, "different client"),
    ({"presentation_name": "ACME FEDERAL"}, "current client presentation"),
    ({"html_sha256": "0" * 64}, "different build"),
    ({"state": "draft"}, "state is draft"),
    ({"release_eligible": False}, "release ineligible"),
    ({"gate_verdict": "wrong"}, "gate verdict"),
    ({"static_lints": {"x": {"ok": True}}}, "lint certificate"),
    ({"federal_link_manifest": [{
        "url": "https://sam.gov/opp/x", "builder": None,
        "record_id": None, "reconciled": False,
    }]}, "manifest"),
])
def test_signal_board_certificate_mismatch_fails_closed(
        tmp_path, overrides, reason):
    report_dir = str(tmp_path)
    html_path, _ = _write_signal(report_dir, overrides=overrides)
    status = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG)
    assert status["qa_pass"] is False
    assert reason in status["reason"]


def test_stale_identical_signal_certificate_cannot_reauthorize_new_press(
        tmp_path):
    html_path, qa_path = _write_signal(str(tmp_path))
    now = time.time()
    os.utime(qa_path, (now, now))
    os.utime(html_path, (now + 1, now + 1))
    status = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG)
    assert status["qa_pass"] is False
    assert status["reason"] == "stale Signal Board QA certificate"


@pytest.mark.parametrize("mutation", [
    "missing_timestamp", "missing_presentation", "missing_lint_contract",
    "wrong_lint_contract", "extra_field",
])
def test_signal_board_certificate_schema_fields_are_exact(tmp_path, mutation):
    html_path, qa_path = _write_signal(str(tmp_path))
    with open(qa_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if mutation == "missing_timestamp":
        payload.pop("press_timestamp")
    elif mutation == "missing_presentation":
        payload.pop("presentation_sha256")
    elif mutation == "missing_lint_contract":
        payload.pop("lint_contract_version")
    elif mutation == "wrong_lint_contract":
        payload["lint_contract_version"] = 2
    else:
        payload["unversioned_extension"] = True
    with open(qa_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    status = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG)

    assert status["certificate_valid"] is False
    assert status["reason"] == \
        "Signal Board QA certificate schema is unsupported"


def test_do_not_send_certificate_preserves_unreconciled_manifest_as_evidence(
        tmp_path):
    from agents.reports.signal_board_presentation import presentation_digest

    body = (
        "<html><title>Acme Federal · Federal Opportunity Pre-Assessment</title>"
        '<a href="https://sam.gov/opp/x" data-link-builder="sam_notice" '
        'data-link-record="x" data-link-reconciled="0">SAM</a></html>'
    )
    html_path = tmp_path / (
        f"{SLUG}.federal_opportunity_signals.DO-NOT-SEND.html")
    html_path.write_text(body, encoding="utf-8")
    failed_lints = {
        **SIGNAL_LINTS,
        "federal_link_construction": {
            "ok": False,
            "violations": ["unreconciled federal link"],
        },
    }
    qa_path = tmp_path / f"{SLUG}.federal_opportunity_signals.qa.json"
    qa_path.write_text(json.dumps({
        "schema_version": 2,
        "lint_contract_version": 1,
        "client_name": "Acme Federal",
        "presentation_name": "Acme Federal",
        "slug": SLUG,
        "state": "do_not_send",
        "release_eligible": False,
        "gate_verdict": "- DO-NOT-SEND (violations below)",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "presentation_sha256": presentation_digest(
            "Acme Federal", root=tmp_path),
        "press_timestamp": "2026-07-19T15:00:00Z",
        "static_lints": failed_lints,
        "federal_link_manifest": [{
            "url": "https://sam.gov/opp/x",
            "builder": "sam_notice",
            "record_id": "x",
            "reconciled": False,
        }],
    }), encoding="utf-8")

    status = signal_board_status(
        str(html_path), exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG,
        qa_path=str(qa_path))

    assert status["certificate_valid"] is True
    assert status["qa_pass"] is False
    assert status["reason"] == "state is do_not_send"


def test_newer_do_not_send_signal_board_supersedes_clean_certificate(
        tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    clean, _ = _write_signal(report_dir, when=time.time() - 10)
    blocked = os.path.join(
        report_dir, f"{SLUG}.federal_opportunity_signals.DO-NOT-SEND.html")
    with open(blocked, "w") as f:
        f.write("<html>blocked</html>")
    os.utime(blocked, (time.time(), time.time()))
    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert clean != blocked
    assert rs["family"] == "signal_board_bad"
    assert rs["releasable"] is False


def test_signal_board_family_remains_canonical_when_retired_report_is_newer(
        tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    base = time.time() - 100
    signal_path, _ = _write_signal(report_dir, when=base)
    legacy_path = _write_stable(report_dir, when=base + 50)

    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)

    assert os.path.getmtime(legacy_path) > os.path.getmtime(signal_path)
    assert rs["family"] == "signal_board"
    assert rs["preview_path"] == signal_path
    assert rs["releasable"] is True
    response = srv.app.test_client().get(
        f"/client/{SLUG}/download/foa.html")
    assert response.status_code == 200
    assert "Federal_Opportunity_Pre-Assessment" in response.headers[
        "Content-Disposition"]


def test_current_presentation_drift_blocks_old_certificate(
        tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    _write_signal(report_dir)
    monkeypatch.setattr(
        "tools.capability.client_display_name",
        lambda _client: "ACME FEDERAL",
    )

    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)

    assert rs["family"] == "signal_board"
    assert rs["releasable"] is False
    assert "current client presentation" in rs["reason"]
    assert srv.app.test_client().get(
        f"/client/{SLUG}/download/signal-board.html").status_code == 409


def test_digest_cache_detects_equal_size_same_mtime_atomic_replacement(
        tmp_path):
    html_path, qa_path = _write_signal(str(tmp_path))
    original = open(html_path, encoding="utf-8").read().replace(
        "</html>", "<!--a--></html>")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(original)
    qa = json.load(open(qa_path, encoding="utf-8"))
    qa["html_sha256"] = hashlib.sha256(original.encode()).hexdigest()
    with open(qa_path, "w", encoding="utf-8") as f:
        json.dump(qa, f)
    now = time.time()
    os.utime(html_path, (now, now))
    os.utime(qa_path, (now + 1, now + 1))
    first = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG)
    assert first["qa_pass"] is True

    prior_stat = os.stat(html_path)
    replacement = str(tmp_path / "replacement.html")
    with open(replacement, "w", encoding="utf-8") as f:
        f.write(original.replace("<!--a-->", "<!--b-->"))
    os.utime(replacement, ns=(prior_stat.st_atime_ns, prior_stat.st_mtime_ns))
    os.replace(replacement, html_path)

    second = signal_board_status(
        html_path, exact_client_name="Acme Federal",
        presentation_name="Acme Federal", slug=SLUG)
    assert second["qa_pass"] is False
    assert second["reason"] == "Signal Board QA certifies a different build"


@pytest.mark.parametrize("mutation", [
    "missing_schema", "wrong_schema", "missing_timestamp",
    "missing_lint_contract", "wrong_lint_contract", "extra_field",
])
def test_download_verifier_rejects_invalid_certificate_schema(
        tmp_path, monkeypatch, mutation):
    report_dir = _seed(tmp_path, monkeypatch)
    html_path, qa_path = _write_signal(report_dir)
    qa = json.load(open(qa_path, encoding="utf-8"))
    if mutation == "missing_schema":
        qa.pop("schema_version")
    elif mutation == "wrong_schema":
        qa["schema_version"] = 3
    elif mutation == "missing_timestamp":
        qa.pop("press_timestamp")
    elif mutation == "missing_lint_contract":
        qa.pop("lint_contract_version")
    elif mutation == "wrong_lint_contract":
        qa["lint_contract_version"] = 2
    else:
        qa["unversioned_extension"] = True
    with open(qa_path, "w", encoding="utf-8") as f:
        json.dump(qa, f)
    html = open(html_path, encoding="utf-8").read()

    with pytest.raises(ValueError, match="certificate schema is invalid"):
        srv._signal_board_certificate(
            SLUG, path=html_path, html=html,
            html_digest=hashlib.sha256(html.encode()).hexdigest())


def test_download_verifier_rechecks_current_presentation(
        tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    html_path, _ = _write_signal(report_dir)
    html = open(html_path, encoding="utf-8").read()
    monkeypatch.setattr(
        "tools.capability.client_display_name",
        lambda _client: "ACME FEDERAL",
    )

    with pytest.raises(ValueError, match="does not authorize"):
        srv._signal_board_certificate(
            SLUG, path=html_path, html=html,
            html_digest=hashlib.sha256(html.encode()).hexdigest())


def test_scoped_gate_ignores_all_scope_signal_board(tmp_path, monkeypatch):
    report_dir = _seed(tmp_path, monkeypatch)
    _approve(monkeypatch)
    _write_signal(report_dir)
    review = os.path.join(srv.REVIEW_DIR, f"{SLUG}.review.json")
    packet = json.load(open(review))
    packet["search_scope"] = {"agencies": ["DHS"], "mode": "focus"}
    with open(review, "w") as f:
        json.dump(packet, f)
    rs = release_state(SLUG, report_dir=report_dir, review_dir=srv.REVIEW_DIR)
    assert rs["family"] == "missing"
    assert rs["preview_path"] is None


def test_download_does_not_trust_rendered_federal_attributes_without_manifest(
        tmp_path, monkeypatch):
    from agents.reports.links import build_sam_notice_link

    report_dir = _seed(tmp_path, monkeypatch)
    html_path, qa_path = _write_signal(report_dir)
    link = build_sam_notice_link("0123456789abcdef0123456789abcdef")
    html = (
        "<html><title>Acme Federal · Federal Opportunity Pre-Assessment</title>"
        f'<a href="{link.url}" data-link-builder="{link.builder}" '
        f'data-link-record="{link.record_id}" '
        'data-link-reconciled="1">SAM</a></html>'
    )
    with open(html_path, "w") as f:
        f.write(html)
    qa = json.load(open(qa_path))
    qa["html_sha256"] = hashlib.sha256(html.encode()).hexdigest()
    # Deliberately leave the independent occurrence manifest empty. The HTML
    # attributes look canonical but are never allowed to attest themselves.
    with open(qa_path, "w") as f:
        json.dump(qa, f)
    monkeypatch.setattr(srv, "release_state", lambda *_a, **_k: {
        "family": "signal_board", "releasable": True,
        "path": html_path, "preview_path": html_path, "reason": "release",
    })
    response = srv.app.test_client().get(
        f"/client/{SLUG}/download/signal-board.html")
    assert response.status_code == 409
    assert "does not match rendered occurrences" in response.get_json()["error"]
