"""Explicit workstation creation copies strategy, never operator decisions.

These tests pin Tranche 3's creation/storage boundary.  They are deliberately
offline and use only temporary artifact roots: a creation request may write a
registry, a workstation-native strategy packet, and its creation journal, but
it may not run a search, approve evidence, move a pointer, or press a report.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

import agents.review as review
import agents.workstations as workstations
from agents.workstations import WorkstationError, create_workstation
from ui import server


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _tree(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _legacy_packet() -> dict:
    """A decided All packet with provenance worth preserving, not adopting."""
    return {
        "client_name": "NETSCOUT",
        "status": "approved",
        "strategy": {
            "client_name": "NETSCOUT",
            "pursuit_strategy": "Protect mission networks at federal scale.",
            "keywords": [{
                "term": "packet visibility",
                "category": "capability",
                "rationale": "Named in the verified capability source.",
                "origin": "system",
                "edited_from": None,
                "source": "https://www.netscout.com/solutions/government",
                "note": None,
            }],
            "inferred_naics": ["541512"],
            "target_agencies": ["DHS"],
            "set_aside_angles": [],
            "searches": [{
                "source": "sam.gov",
                "query_terms": ["packet visibility"],
                "naics_codes": ["541512"],
                "set_asides": [],
                "rationale": "Screen notices for the approved capability.",
            }],
            "near_misses": [],
            "kept_out": [{
                "term": "generic networking",
                "category": "search_term",
                "rationale": "Too broad for the intended screen.",
                "origin": "edited",
                "edited_from": "networking",
                "source": "consultant review",
                "note": "Preserve for a later boundary discussion.",
            }],
            "naics_meta": [{
                "code": "541512",
                "title": "Computer Systems Design Services",
                "role": "core",
                "origin": "consultant",
                "rationale": "Covers integration of network visibility systems.",
                "note": "Retained after boundary review.",
            }],
            "kept_out_naics": [{
                "code": "541513",
                "title": "Computer Facilities Management Services",
                "role": "boundary",
                "origin": "edited",
                "rationale": "Adjacent managed-service lane.",
                "note": "Out unless the requirement includes operations.",
            }],
            "confidence": 0.86,
            "sources_reviewed": [
                "https://www.netscout.com/solutions/government",
            ],
            "requires_human_review": True,
            "review_gate": "Approve vocabulary, NAICS, and scope.",
        },
        "reviewer_note": "Approved for the old All Federal engagement.",
        "created_at": "2026-07-12T20:00:00Z",
        "decided_at": "2026-07-12T21:00:00Z",
        "revised_at": "2026-07-12T22:00:00Z",
        "revision_count": 7,
        "search_scope": {"all": True},
    }


@pytest.fixture
def creation_roots(tmp_path: Path, monkeypatch):
    review_dir = tmp_path / "review"
    cleaned_dir = tmp_path / "cleaned"
    report_dir = tmp_path / "reports"
    intake_dir = tmp_path / "intake"
    clients_dir = tmp_path / "clients"
    marks_dir = tmp_path / "marks"
    desktop_dir = tmp_path / "desktop"
    for root in (
            review_dir, cleaned_dir, report_dir, intake_dir, clients_dir,
            marks_dir, desktop_dir):
        root.mkdir()

    legacy_path = review_dir / "netscout.review.json"
    _write_json(legacy_path, _legacy_packet())

    # Hostile prior DHS artifacts are deliberately exact-looking.  A filename,
    # old embedded scope, or old approval must not bootstrap the new boundary.
    _write_json(cleaned_dir / "searches_netscout.agency_dhs.json", {
        "client": "NETSCOUT",
        "search_scope": {"agencies": ["DHS"]},
        "strategy_revision": 0,
        "generated_at": "2026-07-11T12:00:00Z",
        "results": {"sam.gov": [{"notice_id": "OLD-DHS"}]},
    })
    (report_dir / "netscout.agency_dhs.assessment.client.html").write_text(
        "OLD DHS REPORT", encoding="utf-8")
    _write_json(review_dir / "netscout.assess_approval.json", {
        "client": "NETSCOUT",
        "scope_designator": "agency_dhs",
        "approved_at": "2026-07-11T13:00:00Z",
        "approved_by": "operator",
    })
    _write_json(review_dir / "netscout.agency_dhs.assess_approval.json", {
        "client": "NETSCOUT",
        "scope_designator": "agency_dhs",
        "approved_at": "2026-07-11T13:00:00Z",
        "approved_by": "operator",
    })

    monkeypatch.setattr(server, "REVIEW_DIR", str(review_dir))
    monkeypatch.setattr(server, "CLEANED_DIR", str(cleaned_dir))
    monkeypatch.setattr(server, "REPORT_DIR", str(report_dir))
    monkeypatch.setattr(server, "INTAKE_DIR", str(intake_dir))
    monkeypatch.setattr(server, "CLIENTS_DIR", str(clients_dir))
    monkeypatch.setenv("LILA_CLIENT_MARKS_DIR", str(marks_dir))
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(desktop_dir))
    return tmp_path, review_dir, cleaned_dir, report_dir, legacy_path


def _create_dhs(review_dir: Path):
    return create_workstation(
        "NETSCOUT",
        {"agencies": ["DHS"]},
        review_dir=review_dir,
        clone_baseline=True,
        created_from="operator",
    )


def test_canonical_creation_clones_only_strategy_and_preserves_provenance(
        creation_roots):
    """2026-07-13: a new scope is a fresh gate, never inherited approval."""
    root, review_dir, cleaned_dir, report_dir, legacy_path = creation_roots
    before = _tree(root)
    legacy_bytes = legacy_path.read_bytes()

    result = _create_dhs(review_dir)

    assert result.created is True
    assert result.ref.id == "agency_dhs"
    assert result.ref.scope.as_dict() == {"agencies": ["DHS"]}
    assert result.ref.created_from == "operator"
    registry = json.loads(
        (review_dir / "netscout.workstations.json").read_text(encoding="utf-8"))
    assert registry["schema_version"] == "1"
    assert registry["client_name"] == "NETSCOUT"
    assert registry["workstations"] == [{
        "id": "agency_dhs",
        "scope": {"agencies": ["DHS"]},
        "created_at": registry["workstations"][0]["created_at"],
        "created_from": "operator",
        "archived_at": None,
    }]

    native_path = review_dir / "netscout.agency_dhs.review.json"
    native = json.loads(native_path.read_text(encoding="utf-8"))
    source = _legacy_packet()
    assert native["client_name"] == "NETSCOUT"
    assert native["search_scope"] == {"agencies": ["DHS"]}
    # Additive-field doctrine: the clone is the complete VALIDATED strategy,
    # so schema fields added after this legacy packet was written surface with
    # their empty defaults (research_entities, GOLDEN_BUILD 2026-07-24) while
    # every authored legacy value survives exactly.
    expected_strategy = dict(source["strategy"])
    expected_strategy.setdefault("research_entities", [])
    assert native["strategy"] == expected_strategy
    assert native["status"] == "pending"
    assert native["reviewer_note"] == ""
    assert native["decided_at"] is None
    assert native["revised_at"] is None
    assert native["revision_count"] == 0

    native_sha256 = hashlib.sha256(native_path.read_bytes()).hexdigest()
    receipt = json.loads((
        review_dir / "netscout.agency_dhs.workstation_receipt.json"
    ).read_text(encoding="utf-8"))
    registry_created_at = registry["workstations"][0]["created_at"]
    assert receipt == {
        "schema_version": "1",
        "client_name": "NETSCOUT",
        "workstation_id": "agency_dhs",
        "scope": {"agencies": ["DHS"]},
        "clone_baseline": True,
        "created_at": receipt["created_at"],
        "created_from": "operator",
        "source_packet_sha256": hashlib.sha256(legacy_bytes).hexdigest(),
        "native_packet_sha256": native_sha256,
    }
    assert datetime.fromisoformat(
        receipt["created_at"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(registry_created_at.replace("Z", "+00:00"))
    native_markdown = (
        review_dir / "netscout.agency_dhs.review.md"
    ).read_text(encoding="utf-8")
    assert "packet visibility" in native_markdown
    assert "541512" in native_markdown
    assert native_markdown.startswith(
        "# Analyst Layer: pursuit strategy for NETSCOUT")
    assert "`agency_dhs`" in native_markdown
    assert "approve.py" not in native_markdown
    journal_rows = [
        json.loads(line)
        for line in (
            review_dir / "netscout.agency_dhs.journal.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(journal_rows) == 1
    assert journal_rows[0]["event"] == "workstation_created"
    assert journal_rows[0]["workstation_id"] == "agency_dhs"
    assert journal_rows[0]["scope"] == {"agencies": ["DHS"]}
    assert journal_rows[0]["source_packet_sha256"] == hashlib.sha256(
        legacy_bytes).hexdigest()
    assert journal_rows[0]["native_packet_sha256"] == native_sha256

    # Creation is additive.  It cannot rewrite the baseline or adopt any old
    # DHS evidence/approval/report, even when those bytes look scope-correct.
    assert legacy_path.read_bytes() == legacy_bytes
    for relative in (
        "cleaned/searches_netscout.agency_dhs.json",
        "reports/netscout.agency_dhs.assessment.client.html",
        "review/netscout.assess_approval.json",
        "review/netscout.agency_dhs.assess_approval.json",
    ):
        assert _tree(root)[relative] == before[relative]

    catalog = workstations.discover_workstations(
        "NETSCOUT",
        review_dir=review_dir,
        cleaned_dir=cleaned_dir,
        report_dir=report_dir,
    )
    by_id = {row.ref.id: row for row in catalog.workstations}
    assert by_id["agency_dhs"].phase == "configure"
    assert by_id["agency_dhs"].boundary_status == "pending"
    assert by_id["agency_dhs"].sweep_exists is False
    assert by_id["agency_dhs"].sweep_status == "stale"
    assert by_id["agency_dhs"].historical_sweep_exists is True
    assert by_id["agency_dhs"].historical_artifacts == 1
    # Tranche 3 creation adds an independent DHS workstation.  It is not the
    # Tranche 5 migration/cutover door, so the exact legacy All boundary keeps
    # its own approved/search state until an explicit migration is performed.
    assert by_id["all"].phase == "search"
    assert by_id["all"].boundary_status == "approved"
    assert by_id["all"].is_legacy_current is True


def test_native_sweep_requires_exact_strategy_packet_bytes(
        creation_roots, monkeypatch):
    """2026-07-13: revision equality cannot adopt pre-workstation evidence."""
    _root, review_dir, cleaned_dir, report_dir, _legacy_path = creation_roots
    _create_dhs(review_dir)
    native_path = review_dir / "netscout.agency_dhs.review.json"
    native = json.loads(native_path.read_text(encoding="utf-8"))
    native["status"] = "approved"
    native["decided_at"] = "2026-07-13T06:00:00Z"
    _write_json(native_path, native)
    # Even a legacy approval/report that happens to validate may not advance a
    # native workstation.  Approval partition belongs to the later tranche.
    monkeypatch.setattr(
        workstations, "_assess_status", lambda *_: ("approved", []))
    monkeypatch.setattr(workstations, "_release_ready", lambda *_: True)

    def selected():
        catalog = workstations.discover_workstations(
            "NETSCOUT",
            review_dir=review_dir,
            cleaned_dir=cleaned_dir,
            report_dir=report_dir,
        )
        return next(
            row for row in catalog.workstations
            if row.ref.id == "agency_dhs")

    # This old sweep has the same scope and revision number.  Native mode still
    # refuses it because it cannot prove the exact packet bytes that produced it.
    held = selected()
    assert held.phase == "search"
    assert held.sweep_exists is False
    assert held.sweep_status == "stale"
    diagnostic = " ".join(held.diagnostics)
    assert "native strategy" in diagnostic
    assert "packet bytes" in diagnostic

    sweep_path = cleaned_dir / "searches_netscout.agency_dhs.json"
    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    sweep["strategy_packet_sha256"] = hashlib.sha256(
        native_path.read_bytes()).hexdigest()
    _write_json(sweep_path, sweep)
    admitted = selected()
    assert admitted.phase == "assess"
    assert admitted.sweep_exists is True
    assert admitted.sweep_status == "current"

    # Semantically equivalent JSON is not the same snapshot.  A byte-level
    # replacement must stale the sweep until a new exact-scope search runs.
    native_path.write_text(
        json.dumps(native, separators=(",", ":")), encoding="utf-8")
    drifted = selected()
    assert drifted.phase == "search"
    assert drifted.sweep_exists is False
    assert drifted.sweep_status == "stale"


def test_equivalent_scope_creation_is_canonical_and_byte_idempotent(
        creation_roots):
    """2026-07-13: one identity, one receipt, no timestamp churn on replay."""
    root, review_dir, _cleaned_dir, _report_dir, _legacy_path = creation_roots
    first = create_workstation(
        "NETSCOUT",
        {"agencies": ["CISA", "DHS", "CISA"]},
        review_dir=review_dir,
        clone_baseline=True,
        created_from="operator",
    )
    assert first.created is True
    assert first.ref.id == "agency_dhs_cisa"
    assert first.ref.scope.as_dict() == {"agencies": ["DHS", "CISA"]}
    before_repeat = _tree(root)

    repeated = create_workstation(
        "NETSCOUT",
        {"agencies": ["DHS", "CISA"]},
        review_dir=review_dir,
        clone_baseline=True,
        created_from="operator",
    )

    assert repeated.created is False
    assert repeated.ref == first.ref
    assert _tree(root) == before_repeat


def test_post_creation_is_explicit_and_never_invokes_pipeline_owners(
        creation_roots, monkeypatch):
    """2026-07-13: + New scope is a creation door, not a pipeline press."""
    _root, review_dir, _cleaned_dir, _report_dir, _legacy_path = creation_roots

    def forbidden(*_args, **_kwargs):
        raise AssertionError("creation crossed into an operator-owned action")

    # Pin the negative authority boundary mechanically.  Exact artifact bytes
    # above separately cover owners that are intentionally not imported here.
    monkeypatch.setattr(review, "set_scope", forbidden)
    monkeypatch.setattr(review, "decide", forbidden)
    monkeypatch.setattr(review, "approve_target", forbidden)
    monkeypatch.setattr(server, "start_job", forbidden)
    monkeypatch.setattr(server, "_step_cmd", forbidden)

    from agents.assess import approval as assess_approval
    from agents.assess import ledger as assess_ledger
    from tools import assess_refresh

    monkeypatch.setattr(assess_approval, "approve_assess_results", forbidden)
    monkeypatch.setattr(assess_ledger, "persist_assess_run", forbidden)
    monkeypatch.setattr(assess_ledger, "materialize_current_assess_run", forbidden)
    monkeypatch.setattr(
        assess_refresh, "refresh_current_assess_run_if_active", forbidden)

    client = server.app.test_client()
    response = client.post("/api/client/netscout/workstations", json={
        "scope": {"agencies": ["DHS"]},
        "clone_baseline": True,
    })

    assert response.status_code == 201
    body = response.get_json()
    assert body["created"] is True
    assert body["workstation"]["ref"]["id"] == "agency_dhs"
    assert body["workstation"]["ref"]["scope"] == {
        "agencies": ["DHS"]}
    assert body["workstation"]["phase"] == "configure"
    assert body["location"] == "/client/netscout/workstation/agency_dhs"
    assert (review_dir / "netscout.agency_dhs.review.json").exists()

    before_repeat = _tree(review_dir.parent)
    repeated = client.post("/api/client/netscout/workstations", json={
        "scope": {"agencies": ["DHS"]},
        "clone_baseline": True,
    })
    assert repeated.status_code == 200
    assert repeated.get_json()["created"] is False
    assert _tree(review_dir.parent) == before_repeat


def test_public_post_rejects_no_clone_without_writing(creation_roots):
    """The public Tranche-3 door cannot create an unusable dormant dead end."""
    root, _review_dir, _cleaned_dir, _report_dir, _legacy_path = creation_roots
    before = _tree(root)

    response = server.app.test_client().post(
        "/api/client/netscout/workstations", json={
            "scope": {"agencies": ["DHS"]},
            "clone_baseline": False,
        })

    assert response.status_code == 409, response.get_json()
    assert "baseline strategy" in response.get_data(as_text=True)
    assert _tree(root) == before


def test_post_existing_legacy_scope_is_a_byte_inert_duplicate(creation_roots):
    """The ordinary All choice cannot double as an unreceipted migration door."""
    root, review_dir, _cleaned_dir, _report_dir, legacy_path = creation_roots
    before = _tree(root)
    legacy_bytes = legacy_path.read_bytes()

    response = server.app.test_client().post(
        "/api/client/netscout/workstations", json={
            "scope": {"all": True},
            "clone_baseline": True,
        })

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["created"] is False
    assert body["workstation"]["ref"]["id"] == "all"
    assert body["workstation"]["is_legacy_current"] is True
    assert body["workstation"]["is_native"] is False
    assert body["location"] == "/client/netscout/workstation/all"
    assert legacy_path.read_bytes() == legacy_bytes
    assert not (review_dir / "netscout.all.review.json").exists()
    assert not (review_dir / "netscout.workstations.json").exists()
    assert _tree(root) == before


def test_created_native_detail_is_configure_only_and_withholds_history(
        creation_roots, monkeypatch):
    """2026-07-13: the new DHS view exposes strategy, never old downstream."""
    _root, _review_dir, _cleaned_dir, _report_dir, _legacy_path = creation_roots
    monkeypatch.setattr(server, "client_state", lambda slug, **_kwargs: {
        "slug": slug,
        "client_name": "NETSCOUT",
        "website": "https://netscout.com",
        "logo_domain": "netscout.com",
    })
    client = server.app.test_client()
    created = client.post("/api/client/netscout/workstations", json={
        "scope": {"agencies": ["DHS"]},
        "clone_baseline": True,
    })
    assert created.status_code == 201

    response = client.get(
        "/api/client/netscout/workstation/agency_dhs")
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["workstation_mode"] == "native-early"
    assert body["workstation"]["phase"] == "configure"
    approve = next(step for step in body["steps"]
                   if step["key"] == "approve")
    assert approve["detail"]["strategy"]["keywords"][0]["term"] == \
        "packet visibility"
    assert body["documents"] == []
    assert body["foundation_documents"] == []
    assert body["sidebar"] == {"targets": [], "artifacts": []}
    for forbidden in (
            "review_approved", "assess_ledger", "final_brief",
            "release_state", "target_problems"):
        assert forbidden not in body


def test_native_packet_never_becomes_a_phantom_client_card(
        creation_roots):
    """Client-global indexes keep one client after native scope creation."""
    root, review_dir, _cleaned_dir, _report_dir, _legacy_path = creation_roots
    client = server.app.test_client()
    assert client.post("/api/client/netscout/workstations", json={
        "scope": {"agencies": ["DHS"]},
        "clone_baseline": True,
    }).status_code == 201
    assert (review_dir / "netscout.agency_dhs.review.json").exists()

    before = _tree(root)
    for url in ("/api/clients", "/api/depository"):
        response = client.get(url)
        assert response.status_code == 200, response.get_data(as_text=True)
        rows = response.get_json()
        assert [(row["slug"], row["client_name"]) for row in rows] == [
            ("netscout", "NETSCOUT"),
        ]
        assert all(row["slug"] != "netscout.agency_dhs" for row in rows)
    assert _tree(root) == before


def test_native_strategy_mutations_require_and_touch_only_exact_workstation(
        creation_roots):
    """2026-07-13: workshop and decision writes cannot fall back to All."""
    _root, review_dir, _cleaned_dir, _report_dir, legacy_path = creation_roots
    client = server.app.test_client()
    assert client.post("/api/client/netscout/workstations", json={
        "scope": {"agencies": ["DHS"]},
        "clone_baseline": True,
    }).status_code == 201
    legacy_bytes = legacy_path.read_bytes()

    missing = client.post("/api/strategy/revise", json={
        "client_name": "NETSCOUT",
        "updates": {"pursuit_strategy": "DHS only."},
    })
    assert missing.status_code == 409
    # All remains a separate, addressable legacy workstation.  It rejects this
    # pre-approval edit because its own boundary is already approved, not
    # because creating DHS silently retired it.
    wrong = client.post("/api/strategy/revise", json={
        "client_name": "NETSCOUT", "workstation_id": "all",
        "updates": {"pursuit_strategy": "Wrong scope."},
    })
    assert wrong.status_code == 409

    detail = client.get(
        "/api/client/netscout/workstation/agency_dhs")
    assert detail.status_code == 200, detail.get_json()
    expected_sha256 = detail.get_json()["strategy_packet_sha256"]
    revised = client.post("/api/strategy/revise", json={
        "client_name": "NETSCOUT", "workstation_id": "agency_dhs",
        "expected_packet_sha256": expected_sha256,
        "updates": {"pursuit_strategy": "DHS only."},
    })
    assert revised.status_code == 200, revised.get_json()
    expected_sha256 = revised.get_json()["packet_sha256"]
    decided = client.post("/api/decide", json={
        "client_name": "NETSCOUT", "workstation_id": "agency_dhs",
        "expected_packet_sha256": expected_sha256,
        "approve": True, "note": "Approved exact DHS boundary.",
    })
    assert decided.status_code == 200, decided.get_json()
    expected_sha256 = decided.get_json()["packet_sha256"]
    amended = client.post("/api/strategy/terms", json={
        "client_name": "NETSCOUT", "workstation_id": "agency_dhs",
        "expected_packet_sha256": expected_sha256,
        "keywords": revised.get_json()["strategy"]["keywords"],
    })
    assert amended.status_code == 200, amended.get_json()

    native = json.loads((
        review_dir / "netscout.agency_dhs.review.json"
    ).read_text(encoding="utf-8"))
    assert native["strategy"]["pursuit_strategy"] == "DHS only."
    assert native["status"] == "approved"
    assert native["revision_count"] == 2
    assert legacy_path.read_bytes() == legacy_bytes
    assert not any(
        json.loads(line)["event"] == "scope_change"
        for line in (
            review_dir / "netscout.agency_dhs.journal.jsonl"
        ).read_text(encoding="utf-8").splitlines())

    before_scope_attempt = _tree(review_dir)
    immutable = client.post("/api/strategy/scope", json={
        "client_name": "NETSCOUT", "workstation_id": "agency_dhs",
        "scope": {"all": True},
    })
    assert immutable.status_code == 409
    assert _tree(review_dir) == before_scope_attempt


@pytest.mark.parametrize("body", [
    {},
    {"scope": {"agencies": ["DHS"]}},
    {"scope": {"agencies": ["DHS"]}, "clone_baseline": "yes"},
    {"scope": {"agencies": ["D"]}, "clone_baseline": True},
    {"scope": {"agencies": ["DHS"]}, "clone_baseline": True, "run": True},
])
def test_post_rejects_implicit_or_ambiguous_creation(creation_roots, body):
    """2026-07-13: scope and cloning choice are explicit request facts."""
    root, _review_dir, _cleaned_dir, _report_dir, _legacy_path = creation_roots
    before = _tree(root)
    response = server.app.test_client().post(
        "/api/client/netscout/workstations", json=body)
    assert response.status_code == 400
    assert _tree(root) == before


def test_creation_refuses_exact_legacy_packet_drift_without_partial_commit(
        creation_roots, monkeypatch):
    """2026-07-13: clone commits only against the exact bytes it inspected."""
    root, review_dir, _cleaned_dir, _report_dir, legacy_path = creation_roots
    before = _tree(root)
    original = workstations._commit_creation

    def drift_then_commit(*args, **kwargs):
        packet = json.loads(legacy_path.read_text(encoding="utf-8"))
        packet["strategy"]["keywords"][0]["term"] = "changed concurrently"
        _write_json(legacy_path, packet)
        return original(*args, **kwargs)

    monkeypatch.setattr(workstations, "_commit_creation", drift_then_commit)
    with pytest.raises(WorkstationError, match="changed|fingerprint|concurrent"):
        _create_dhs(review_dir)

    assert not (review_dir / "netscout.workstations.json").exists()
    assert not (review_dir / "netscout.agency_dhs.review.json").exists()
    after = _tree(root)
    assert set(after) == set(before)
    assert after["review/netscout.review.json"] != before[
        "review/netscout.review.json"]
    for relative in set(before) - {"review/netscout.review.json"}:
        assert after[relative] == before[relative]
