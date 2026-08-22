"""Additive workstation GET contract and byte-invariance proofs."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents.review as review_mod
import agents.workstations as workstations
from ui import server


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _tree(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*")) if path.is_file()
    }


@pytest.fixture
def workstation_api(tmp_path, monkeypatch):
    review = tmp_path / "review"
    cleaned = tmp_path / "cleaned"
    reports = tmp_path / "reports"
    intake = tmp_path / "intake"
    clients = tmp_path / "clients"
    marks = tmp_path / "marks"
    for root in (review, cleaned, reports, intake, clients, marks):
        root.mkdir()
    (clients / "netscout").mkdir()
    monkeypatch.setattr(server, "REVIEW_DIR", str(review))
    monkeypatch.setattr(server, "CLEANED_DIR", str(cleaned))
    monkeypatch.setattr(server, "REPORT_DIR", str(reports))
    monkeypatch.setattr(server, "INTAKE_DIR", str(intake))
    monkeypatch.setattr(server, "CLIENTS_DIR", str(clients))
    monkeypatch.setenv("LILA_CLIENT_MARKS_DIR", str(marks))
    _write_json(intake / "netscout.submission.json", {
        "client_name": "NETSCOUT",
        "website": "https://netscout.com",
    })
    _write_json(clients / "netscout" / "profile.json", {
        "client_name": "NETSCOUT",
        "capabilities": ["packet visibility"],
    })
    (review / "netscout.review.md").write_text(
        "# NETSCOUT baseline strategy\n", encoding="utf-8")
    (marks / "netscout.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        encoding="utf-8")
    _write_json(review / "netscout.review.json", {
        "client_name": "NETSCOUT",
        "status": "approved",
        "search_scope": {"agencies": ["DHS"]},
        "strategy": {},
    })
    _write_json(cleaned / "searches_netscout.agency_dhs.json", {
        "client": "NETSCOUT",
        "search_scope": {"agencies": ["DHS"]},
        "results": {},
    })
    _write_json(review / "netscout.workstations.json", {
        "schema_version": "1",
        "client_name": "NETSCOUT",
        "workstations": [
            {"id": "agency_dhs", "scope": {"agencies": ["DHS"]}},
            {"id": "agency_dod", "scope": {"agencies": ["DoD"]}},
        ],
    })
    monkeypatch.setattr(workstations, "_assess_status", lambda *_: ("pending", []))
    def client_payload(slug, **kwargs):
        assert kwargs["workstation_id"] == "agency_dhs"
        assert kwargs["packet"]["search_scope"] == {"agencies": ["DHS"]}
        assert kwargs["searches"]["search_scope"] == {"agencies": ["DHS"]}
        return {
            "slug": slug,
            "client_name": "NETSCOUT",
            "website": "https://netscout.com",
            "logo_domain": "netscout.com",
            "status": "approved",
            "steps": [{"key": "gate", "detail": {
                "strategy_md": "DHS SENTINEL"}}],
            "documents": [
                {"label": "DHS report"},
                {"label": "Duplicate foundation",
                 "path": server._rel(str(
                     intake / "netscout.submission.json"))},
            ],
            "sidebar": {"targets": [{"title": "DHS target"}],
                        "artifacts": []},
            "target_approved": True,
        }

    monkeypatch.setattr(server, "_client_payload", client_payload)
    monkeypatch.setattr(server, "client_state", lambda slug, **_kwargs: {
        "slug": slug,
        "client_name": "NETSCOUT",
        "website": "https://netscout.com",
        "logo_domain": "netscout.com",
    })
    return server.app.test_client(), tmp_path, review, cleaned, reports


def test_catalog_and_exact_detail_shapes(workstation_api):
    client, *_ = workstation_api
    response = client.get("/api/client/netscout/workstations")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["client_name"] == "NETSCOUT"
    assert [row["ref"]["id"] for row in body["workstations"]] == [
        "agency_dhs", "agency_dod", "all"]
    assert body["workstations"][0]["ref"]["scope"] == {"agencies": ["DHS"]}
    assert body["workstations"][0]["phase"] == "assess"

    current = client.get(
        "/api/client/netscout/workstation/agency_dhs").get_json()
    assert current["workstation_mode"] == "legacy-current"
    assert current["workstation"]["ref"]["id"] == "agency_dhs"
    assert current["steps"][0]["detail"]["strategy_md"] == "DHS SENTINEL"
    assert {row["kind"] for row in current["foundation_documents"]} == {
        "intake", "capability_profile", "baseline_strategy", "client_mark",
    }
    assert current["documents"] == [{"label": "DHS report"}]

    dormant = client.get(
        "/api/client/netscout/workstation/all").get_json()
    assert dormant == {
        "slug": "netscout",
        "client_name": "NETSCOUT",
        "website": "https://netscout.com",
        "logo_domain": "netscout.com",
        "workstation_mode": "dormant",
        "workstation": next(
            row for row in dormant["workstations"] if row["ref"]["id"] == "all"),
        "workstations": dormant["workstations"],
        "foundation_documents": dormant["foundation_documents"],
        "documents": [],
        "sidebar": {"targets": [], "artifacts": []},
        "target_approved": False,
    }
    text = json.dumps(dormant)
    assert "DHS SENTINEL" not in text
    assert "DHS target" not in text
    assert "DHS report" not in text
    assert dormant["workstation"]["phase"] == "dormant"


def test_foundation_documents_are_identical_strict_and_read_only_across_scopes(
        workstation_api, monkeypatch):
    client, root, review, cleaned, reports = workstation_api

    # Each excluded family gets an attractive-looking sentinel.  The shared
    # foundation contract is an allowlist, so none may be promoted merely by
    # filename, scope, recency, or proximity to the client packet.
    (review / "netscout.agency_dhs.review.md").write_text(
        "SCOPED STRATEGY SENTINEL", encoding="utf-8")
    _write_json(review / "netscout.assess_approval.json", {
        "client_name": "NETSCOUT", "status": "approved",
    })
    (review / "netscout.contacts.md").write_text(
        "OUTREACH SENTINEL", encoding="utf-8")
    (reports / "netscout.agency_dhs.federal_opportunity_assessment.html").write_text(
        "REPORT SENTINEL", encoding="utf-8")
    _write_json(cleaned / "searches_netscout.all.json", {
        "client": "NETSCOUT", "search_scope": {"all": True},
    })
    desktop = root / "desktop" / "NETSCOUT"
    desktop.mkdir(parents=True)
    (desktop / "keith-ready.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(server, "_desktop_root", lambda: str(root / "desktop"))

    before = _tree(root)
    payloads = [
        client.get(f"/api/client/netscout/workstation/{workstation_id}")
        for workstation_id in ("agency_dhs", "agency_dod", "all")
    ]
    assert all(response.status_code == 200 for response in payloads)
    bodies = [response.get_json() for response in payloads]
    assert _tree(root) == before

    foundations = [body["foundation_documents"] for body in bodies]
    assert foundations[0] == foundations[1] == foundations[2]
    assert [Path(row["path"]).name for row in foundations[0]] == [
        "netscout.submission.json", "profile.json", "netscout.review.md",
        "netscout.svg",
    ]
    assert all(row["stage"] == "Shared foundation" for row in foundations[0])
    assert all(row["source"] == "client-foundation" for row in foundations[0])
    assert len({row["path"] for row in foundations[0]}) == len(foundations[0])

    foundation_text = json.dumps(foundations[0])
    for excluded in (
            "agency_dhs.review.md", "assess_approval", "contacts.md",
            "federal_opportunity_assessment", "searches_netscout",
            "keith-ready.pdf", "OUTREACH SENTINEL", "REPORT SENTINEL"):
        assert excluded not in foundation_text
    for body in bodies:
        foundation_paths = {row["path"] for row in body["foundation_documents"]}
        document_paths = {
            row.get("path") for row in body["documents"] if row.get("path")}
        assert foundation_paths.isdisjoint(document_paths)


def test_file_api_returns_allowlisted_client_mark_as_safe_data_url(
        tmp_path, monkeypatch):
    png_bytes = b"\x89PNG\r\n\x1a\nFOUNDATION-MARK"
    mark = tmp_path / "data" / "reference" / "marks" / "client" / "acme.png"
    mark.parent.mkdir(parents=True)
    mark.write_bytes(png_bytes)
    monkeypatch.setattr(server, "ROOT", str(tmp_path))

    response = server.app.test_client().get(
        "/api/file?path=data/reference/marks/client/acme.png")

    assert response.status_code == 200
    body = response.get_json()
    assert body["encoding"] == "data-url"
    assert body["media_type"] == "image/png"
    prefix, encoded = body["content"].split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == png_bytes


def test_current_scope_binding_failure_withholds_legacy_downstream_payload(
        workstation_api, monkeypatch):
    client, _root, _review, cleaned, _reports = workstation_api
    _write_json(cleaned / "searches_netscout.agency_dhs.json", {
        "client": "NETSCOUT", "search_scope": {"all": True}, "results": {},
    })

    def forbidden(_slug, **_kwargs):
        raise AssertionError("unproven scope reached the legacy payload")

    monkeypatch.setattr(server, "_client_payload", forbidden)
    response = client.get(
        "/api/client/netscout/workstation/agency_dhs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["workstation_mode"] == "legacy-current-held"
    assert body["workstation"]["phase"] == "search"
    assert body["foundation_documents"] == server.client_foundation_documents(
        "netscout")
    assert body["documents"] == []
    assert body["sidebar"] == {"targets": [], "artifacts": []}
    assert "scope does not match" in " ".join(
        body["workstation"]["diagnostics"])


def test_pre_workstation_all_detail_uses_real_rich_assembler(
        tmp_path, monkeypatch):
    """Exercise the production assembler behind the exact detail endpoint."""
    data = tmp_path / "data"
    review = data / "review"
    cleaned = data / "cleaned"
    reports = data / "reports"
    intake = data / "intake"
    clients = tmp_path / "clients"
    for root in (review, cleaned, reports, intake, clients):
        root.mkdir(parents=True)
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    monkeypatch.setattr(server, "REVIEW_DIR", str(review))
    monkeypatch.setattr(server, "CLEANED_DIR", str(cleaned))
    monkeypatch.setattr(server, "REPORT_DIR", str(reports))
    monkeypatch.setattr(server, "INTAKE_DIR", str(intake))
    monkeypatch.setattr(server, "CLIENTS_DIR", str(clients))
    _write_json(review / "netscout.review.json", {
        "client_name": "NETSCOUT",
        "status": "approved",
        "strategy": {
            "keywords": [], "inferred_naics": [], "confidence": 0.5,
        },
    })
    _write_json(cleaned / "searches_netscout.json", {
        "client": "NETSCOUT", "results": {},
    })
    preview = reports / "netscout.capture_brief.html"
    preview.write_text("<html>EXACT ALL REPORT</html>", encoding="utf-8")
    monkeypatch.setattr(workstations, "_assess_status", lambda *_: ("pending", []))
    monkeypatch.setattr(server, "_assess_release_gate", lambda *_: (
        None, "pending", ["review required"]))
    monkeypatch.setattr(server, "_assess_ledger_snapshot", lambda *_a, **_k: {
        "exists": False, "state": "missing",
    })
    monkeypatch.setattr(server, "release_state", lambda *_a, **_k: {
        "preview_path": str(preview),
        "family": "legacy",
        "releasable": False,
        "reason": "review required",
        "approval_status": "pending",
        "updated": preview.stat().st_mtime,
    })
    monkeypatch.setattr(
        review_mod, "target_gate_status", lambda *_a, **_k: (False, []))

    response = server.app.test_client().get(
        "/api/client/netscout/workstation/all")

    assert response.status_code == 200
    body = response.get_json()
    assert body["workstation_mode"] == "legacy-current"
    assert body["workstation"]["ref"]["id"] == "all"
    assert len(body["steps"]) == 7
    assert [step["key"] for step in body["steps"][:4]] == [
        "intake", "approve", "search", "review"]
    assert body["final_brief"]["path"].endswith(
        "netscout.capture_brief.html")
    assert [row["label"] for row in body["documents"]] == [
        "Opportunity Assessment"]
    assert body["assess_ledger"]["state"] == "missing"
    assert body["sidebar"] == {"targets": [], "artifacts": [{
        "label": "Opportunity Assessment",
        "path": body["documents"][0]["path"],
        "fmt": "html",
        "qa_pass": True,
    }]}


def test_pre_workstation_all_detail_is_rich_and_scoreboard_stays_exact(
        workstation_api, monkeypatch):
    """The exact route, not a client-global browser fallback, owns the UI.

    A healthy pre-workstation All Federal client must receive its operating
    pipeline and qualified scoreboard from the exact workstation APIs.  The
    fixture deliberately omits sweep ``search_scope`` to exercise the legacy
    compatibility seam used by Recorded Future, Osprey, and Thinklogical.
    """
    client, _root, review, cleaned, _reports = workstation_api
    packet = json.loads((review / "netscout.review.json").read_text())
    packet.pop("search_scope")
    _write_json(review / "netscout.review.json", packet)
    (cleaned / "searches_netscout.agency_dhs.json").unlink()
    _write_json(cleaned / "searches_netscout.json", {
        "client": "NETSCOUT", "results": {},
    })
    calls = []

    def rich_payload(slug, **kwargs):
        calls.append(kwargs)
        return {
            "slug": slug,
            "client_name": "NETSCOUT",
            "website": "https://netscout.com",
            "logo_domain": "netscout.com",
            "status": "approved",
            "stages": {"searched": True},
            "steps": [{"key": "review", "detail": {
                "sentinel": "EXACT OPERATING PIPELINE"}}],
            "final_brief": {"path": "exact-report.html", "qa_pass": True},
            "documents": [{"label": "Exact All report"}],
            "assess_ledger": {"state": "current"},
            "review_approved": True,
            "sidebar": {"targets": [{"title": "Exact All target"}],
                        "artifacts": []},
            "target_approved": False,
        }

    monkeypatch.setattr(server, "_client_payload", rich_payload)
    response = client.get("/api/client/netscout/workstation/all")

    assert response.status_code == 200
    body = response.get_json()
    assert body["workstation_mode"] == "legacy-current"
    assert body["workstation"]["ref"]["id"] == "all"
    assert body["steps"][0]["detail"]["sentinel"] == \
        "EXACT OPERATING PIPELINE"
    assert body["final_brief"]["path"] == "exact-report.html"
    assert body["documents"] == [{"label": "Exact All report"}]
    assert body["assess_ledger"]["state"] == "current"
    assert calls[0]["workstation_id"] == "all"
    assert "search_scope" not in calls[0]["searches"]

    monkeypatch.setattr(
        server, "_assessment_doc",
        lambda slug, *, searches, workstation_id: SimpleNamespace(
            sentinel=(slug, searches, workstation_id)))
    import agents.reports.views as views
    import agents.reports.lint as lint
    monkeypatch.setattr(
        views, "render_scoreboard",
        lambda doc, view: (
            f"<div id='exact-scoreboard'>{doc.sentinel[2]}:{view}</div>"))
    monkeypatch.setattr(
        lint, "lint_scoreboard_populations",
        lambda _html: SimpleNamespace(violations=[]))
    banner = client.get(
        "/api/client/netscout/banner?workstation_id=all")
    assert banner.status_code == 200
    assert "exact-scoreboard" in banner.get_json()["html"]
    assert "all:internal" in banner.get_json()["html"]


@pytest.mark.parametrize("status, remove_sweep, phase", [
    ("pending", False, "configure"),
    ("approved", True, "search"),
])
def test_early_phases_physically_exclude_downstream_state(
        workstation_api, monkeypatch, status, remove_sweep, phase):
    client, _root, review, cleaned, reports = workstation_api
    packet = json.loads((review / "netscout.review.json").read_text())
    packet["status"] = status
    packet["strategy"] = {
        "keywords": [{"term": "packet visibility"}],
        "inferred_naics": ["541512"],
    }
    _write_json(review / "netscout.review.json", packet)
    if remove_sweep:
        (cleaned / "searches_netscout.agency_dhs.json").unlink()
    (reports / "netscout.agency_dhs.federal_opportunity_assessment.html").write_text(
        "OLD REPORT SENTINEL")
    (review / "netscout.research_picture.md").write_text(
        "OLD WORKING SENTINEL")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("early phase reached the downstream payload")

    monkeypatch.setattr(server, "_client_payload", forbidden)
    response = client.get("/api/client/netscout/workstation/agency_dhs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["workstation_mode"] == "legacy-current-early"
    assert body["workstation"]["phase"] == phase
    assert body["foundation_documents"] == server.client_foundation_documents(
        "netscout")
    assert [step["key"] for step in body["steps"]] == [
        "intake", "approve", "search"]
    analyst = next(step for step in body["steps"] if step["key"] == "approve")
    assert analyst["title"] == "Analyst Layer · strategy & search boundary"
    assert body["documents"] == []
    assert body["sidebar"] == {"targets": [], "artifacts": []}
    assert body["target_approved"] is False
    text = json.dumps(body)
    assert "OLD REPORT SENTINEL" not in text
    assert "OLD WORKING SENTINEL" not in text
    for forbidden_key in (
            "final_brief", "assess_ledger", "review_approved",
            "review_approval_status", "target_problems"):
        assert forbidden_key not in body


def test_packet_change_after_catalog_is_refused_without_payload(
        workstation_api, monkeypatch):
    client, _root, review, _cleaned, _reports = workstation_api
    original = server._workstation_route_error

    def change_after_catalog(slug):
        resolved, error = original(slug)
        packet = json.loads((review / "netscout.review.json").read_text())
        packet["search_scope"] = {"all": True}
        _write_json(review / "netscout.review.json", packet)
        return resolved, error

    monkeypatch.setattr(server, "_workstation_route_error", change_after_catalog)
    response = client.get("/api/client/netscout/workstation/agency_dhs")
    assert response.status_code == 409
    text = response.get_data(as_text=True)
    assert "scope changed" in text
    assert "DHS SENTINEL" not in text
    assert "DHS target" not in text


def test_packet_status_change_after_catalog_is_refused_without_downstream_payload(
        workstation_api, monkeypatch):
    """Same-scope status drift must not retain the catalog's downstream phase.

    Scope equality alone is insufficient: an approved/Target catalog row can
    become pending before the packet is rebound.  That response belongs in the
    configure adapter, never behind the stale Target row.
    """
    client, _root, review, _cleaned, _reports = workstation_api
    original = server._workstation_route_error

    def change_after_catalog(slug):
        resolved, error = original(slug)
        packet = json.loads((review / "netscout.review.json").read_text())
        packet["status"] = "pending"
        _write_json(review / "netscout.review.json", packet)
        return resolved, error

    def forbidden(_slug, **_kwargs):
        raise AssertionError("status-drifted packet reached downstream payload")

    monkeypatch.setattr(server, "_workstation_route_error", change_after_catalog)
    monkeypatch.setattr(server, "_client_payload", forbidden)
    response = client.get("/api/client/netscout/workstation/agency_dhs")
    assert response.status_code == 409
    text = response.get_data(as_text=True)
    assert "changed" in text
    assert "DHS SENTINEL" not in text
    assert "DHS target" not in text


@pytest.mark.parametrize("artifact", ["packet", "sweep"])
def test_binding_replacement_during_payload_is_refused(
        workstation_api, monkeypatch, artifact):
    client, _root, review, cleaned, _reports = workstation_api

    def replace_then_return(slug, **_kwargs):
        if artifact == "packet":
            packet = json.loads((review / "netscout.review.json").read_text())
            packet["search_scope"] = {"all": True}
            _write_json(review / "netscout.review.json", packet)
        else:
            _write_json(cleaned / "searches_netscout.agency_dhs.json", {
                "client": "NETSCOUT", "search_scope": {"all": True},
                "results": {"sentinel": "FOREIGN SWEEP"},
            })
        return {
            "slug": slug, "client_name": "NETSCOUT",
            "sidebar": {"targets": [{"title": "LEAK"}], "artifacts": []},
            "documents": [{"label": "LEAK"}], "target_approved": True,
        }

    monkeypatch.setattr(server, "_client_payload", replace_then_return)
    response = client.get("/api/client/netscout/workstation/agency_dhs")
    assert response.status_code == 409
    assert "LEAK" not in response.get_data(as_text=True)


def test_unknown_client_id_and_malformed_identity_fail_closed(workstation_api):
    client, _root, review, _cleaned, _reports = workstation_api
    assert client.get("/api/client/missing/workstations").status_code == 404
    assert client.get(
        "/api/client/netscout/workstation/agency_fbi").status_code == 404
    assert client.get(
        "/api/client/netscout/workstation/Agency_DHS").status_code == 400
    assert client.get("/api/client/NETSCOUT/workstations").status_code == 400

    _write_json(review / "netscout.workstations.json", {
        "schema_version": "1", "client_name": "OTHER", "workstations": [],
    })
    response = client.get("/api/client/netscout/workstations")
    assert response.status_code == 409
    assert response.headers["Cache-Control"] == "no-store"


def test_archived_workstation_is_omitted_and_cannot_be_opened(workstation_api):
    client, _root, review, _cleaned, _reports = workstation_api
    registry = json.loads((review / "netscout.workstations.json").read_text())
    registry["workstations"][1]["archived_at"] = "2026-07-13T00:00:00Z"
    _write_json(review / "netscout.workstations.json", registry)
    ids = [row["ref"]["id"] for row in client.get(
        "/api/client/netscout/workstations").get_json()["workstations"]]
    assert "agency_dod" not in ids
    assert client.get(
        "/api/client/netscout/workstation/agency_dod").status_code == 404


def test_gets_and_deep_link_are_non_mutating_and_never_call_gate_owners(
        workstation_api, monkeypatch):
    client, root, *_ = workstation_api

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a read-only workstation route called a mutation owner")

    monkeypatch.setattr(review_mod, "set_scope", forbidden)
    monkeypatch.setattr(review_mod, "revise", forbidden)
    monkeypatch.setattr(review_mod, "decide", forbidden)
    monkeypatch.setattr(server, "index", lambda: "SPA")
    before = _tree(root)
    for url in (
        "/api/client/netscout/workstation/agency_dhs",
        "/api/client/netscout/workstation/all",
        "/api/client/netscout/workstation/agency_dhs",
        "/client/netscout/workstation/agency_dhs",
    ):
        response = client.get(url)
        assert response.status_code == 200
    assert _tree(root) == before


def test_complete_bound_auxiliary_navigation_graph_is_byte_invariant(
        workstation_api, monkeypatch):
    """Execute the same qualified GET graph the full browser renderer emits.

    The Chrome test proves every automatic URL carries ``workstation_id``;
    this server-backed half proves those exact routes remain observational.
    """
    client, root, *_ = workstation_api
    monkeypatch.setattr(server, "_assessment_doc", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_contacts_snapshot", lambda: ([], [], []))
    monkeypatch.setattr(server, "_outreach", lambda: SimpleNamespace(
        render=lambda **_kwargs: []))
    import agents.change_digest as change_digest
    monkeypatch.setattr(
        change_digest, "load_change_digest", lambda *_a, **_k: None)

    before = _tree(root)
    base = "/api/client/netscout"
    urls = (
        f"{base}/workstation/agency_dhs",
        f"{base}/banner?workstation_id=agency_dhs",
        f"{base}/competitors?workstation_id=agency_dhs",
        f"{base}/recompetes?workstation_id=agency_dhs",
        f"{base}/change-digest?workstation_id=agency_dhs",
        f"{base}/targets?workstation_id=agency_dhs",
    )
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, (url, response.get_data(as_text=True))
    assert _tree(root) == before


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_workstation_read_surfaces_reject_mutating_verbs(workstation_api, method):
    client, *_ = workstation_api
    call = getattr(client, method)
    assert call("/api/client/netscout/workstations").status_code == 405
    assert call(
        "/api/client/netscout/workstation/agency_dhs").status_code == 405


def test_document_preferences_are_partitioned_by_workstation(workstation_api):
    client, root, *_ = workstation_api
    assert client.post("/api/docs/prefs", json={
        "slug": "netscout", "workstation_id": "agency_fbi",
        "hide": "wrong.html",
    }).status_code == 404
    assert client.post("/api/docs/prefs", json={
        "slug": "netscout", "workstation_id": "agency_dhs",
        "hide": "dhs.html",
    }).status_code == 200
    assert client.post("/api/docs/prefs", json={
        "slug": "netscout", "workstation_id": "all",
        "hide": "all.html",
    }).status_code == 200
    dhs = json.loads((root / "state" /
                      "doc_prefs_netscout.agency_dhs.json").read_text())
    all_scope = json.loads((root / "state" /
                            "doc_prefs_netscout.all.json").read_text())
    assert dhs["hidden"] == ["dhs.html"]
    assert all_scope["hidden"] == ["all.html"]


def test_run_binding_rejects_dormant_or_stale_id_and_resolves_legacy_omission(
        workstation_api, monkeypatch):
    """A job is owned by the posted/derived current workstation, never a tab.

    Compatibility permits omission only by resolving the sole legacy-current
    row.  A dormant or unknown id must fail before ``start_job``.
    """
    client, *_ = workstation_api
    packet = SimpleNamespace(
        client_name="NETSCOUT",
        status=server.ReviewStatus.APPROVED,
        is_approved=True,
        search_scope={"agencies": ["DHS"]},
        designator=lambda: "agency_dhs",
    )
    packet_loads = []

    def load_exact_legacy(_client, **kwargs):
        packet_loads.append(kwargs)
        return packet

    monkeypatch.setattr(server, "load_packet", load_exact_legacy)
    launched = []
    monkeypatch.setattr(
        server, "start_job",
        lambda step, name, args: launched.append((step, name, dict(args))) or "job-1")

    for workstation_id in ("all", "agency_dod", "agency_fbi"):
        response = client.post("/api/run", json={
            "client_name": "NETSCOUT",
            "step": "searches",
            "args": {"workstation_id": workstation_id},
        })
        assert response.status_code in {404, 409}, workstation_id
        assert launched == []

    response = client.post("/api/run", json={
        "client_name": "NETSCOUT", "step": "searches", "args": {},
    })
    assert response.status_code == 200
    assert launched == [(
        "searches", "NETSCOUT", {"workstation_id": "agency_dhs"})]
    assert packet_loads == [{
        "workstation_id": "agency_dhs",
        "review_dir": server.REVIEW_DIR,
        "native_owner": False,
    }]


def test_roots_are_independent_and_missing_registry_is_not_created(
        workstation_api):
    client, _root, review, _cleaned, reports = workstation_api
    (review / "netscout.workstations.json").unlink()
    # A report-dir decoy named like a registry must never be read.
    (reports / "netscout.workstations.json").write_text("{")
    before = _tree(review)
    response = client.get("/api/client/netscout/workstations")
    assert response.status_code == 200
    assert _tree(review) == before
    assert not (review / "netscout.workstations.json").exists()


def test_current_document_shelf_admits_only_exact_report_family(
        workstation_api, monkeypatch):
    _client, _root, review, _cleaned, reports = workstation_api
    for name in (
        "netscout.federal_opportunity_assessment.html",
        "netscout.agency_dhs.federal_opportunity_assessment.html",
        "netscout.agency_dhs_cisa.federal_opportunity_assessment.html",
    ):
        (reports / name).write_text("<html></html>")
    for name in ("netscout.research_picture.md", "netscout.dossiers.md",
                 "netscout.review.md", "netscout.contacts.md"):
        (review / name).write_text("UNQUALIFIED WORKING SENTINEL")
    desktop = reports / "desktop" / "NETSCOUT"
    desktop.mkdir(parents=True)
    (desktop / "operator-sentinel.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(server, "_desktop_root", lambda: str(reports / "desktop"))
    monkeypatch.setattr(server, "_doc_prefs", lambda *_args: {
        "hidden": [], "order": []})

    all_names = {Path(row["path"]).name for row in server.client_documents(
        "netscout", "NETSCOUT", workstation_id="all")}
    dhs_names = {Path(row["path"]).name for row in server.client_documents(
        "netscout", "NETSCOUT", workstation_id="agency_dhs")}
    assert "netscout.federal_opportunity_assessment.html" in all_names
    assert not any("agency_" in name for name in all_names)
    assert dhs_names == {
        "netscout.agency_dhs.federal_opportunity_assessment.html"}
    assert not ({"netscout.research_picture.md", "netscout.dossiers.md",
                 "netscout.review.md", "netscout.contacts.md",
                 "operator-sentinel.pdf"} & (all_names | dhs_names))

    global_names = {Path(row["path"]).name for row in server.client_documents(
        "netscout", "NETSCOUT", workstation_id=None)}
    assert {"netscout.research_picture.md", "netscout.dossiers.md",
            "netscout.review.md", "netscout.contacts.md",
            "operator-sentinel.pdf"} <= global_names


def test_dashboard_assessment_assembly_disables_unresolved_entity_writes(
        workstation_api, monkeypatch):
    _client, _root, _review, cleaned, _reports = workstation_api
    _write_json(cleaned / "searches_netscout.agency_dhs.json", {
        "client": "NETSCOUT",
        "search_scope": {"agencies": ["DHS"]},
        "results": {"usaspending.gov": []},
    })
    monkeypatch.setattr(server, "_sweep_name", lambda _slug, **_kwargs: (
        "searches_netscout.agency_dhs.json"))
    import agents.reports.document as document
    calls = []
    sentinel = object()

    def build(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(document, "build_document", build)
    assert server._assessment_doc("netscout") is sentinel
    assert calls[0][1]["_log_unresolved_entities"] is False


def test_scoped_change_digest_rejects_requested_workstation_mismatch(
        workstation_api, monkeypatch):
    client, *_ = workstation_api
    imported = pytest.importorskip("agents.change_digest")
    monkeypatch.setattr(imported, "load_change_digest", lambda *_a, **_k: {
        "client_name": "NETSCOUT",
        "source_cursors": {"scope_designator": "agency_dhs"},
        "summary": "DHS DIGEST SENTINEL",
    })
    monkeypatch.setattr(
        imported, "digest_for_display",
        lambda payload: {"summary": payload["summary"]})

    response = client.get(
        "/api/client/netscout/change-digest?workstation_id=all")
    assert response.status_code == 409
    assert "DHS DIGEST SENTINEL" not in response.get_data(as_text=True)


def test_scoped_change_digest_withholds_stored_other_scope(
        workstation_api, monkeypatch):
    client, *_ = workstation_api
    imported = pytest.importorskip("agents.change_digest")
    monkeypatch.setattr(imported, "load_change_digest", lambda *_a, **_k: {
        "client_name": "NETSCOUT",
        "source_cursors": {"scope_designator": "all"},
        "summary": "ALL DIGEST SENTINEL",
    })
    monkeypatch.setattr(
        imported, "digest_for_display",
        lambda payload: {"summary": payload["summary"]})

    response = client.get(
        "/api/client/netscout/change-digest?workstation_id=agency_dhs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["built"] is False
    assert "ALL DIGEST SENTINEL" not in response.get_data(as_text=True)


def test_scoped_recompetes_withholds_legacy_unbound_calendar(
        workstation_api, monkeypatch):
    client, *_ = workstation_api
    imported = pytest.importorskip("tools.api.recompete")
    monkeypatch.setattr(imported, "load_calendar", lambda _name: {
        "client": "NETSCOUT",
        "generated": "2026-07-13",
        "attack": [{"award_id": "UNBOUND RECOMPETE SENTINEL"}],
        "defend": [],
    })

    response = client.get(
        "/api/client/netscout/recompetes?workstation_id=agency_dhs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["built"] is False
    assert "UNBOUND RECOMPETE SENTINEL" not in response.get_data(as_text=True)
