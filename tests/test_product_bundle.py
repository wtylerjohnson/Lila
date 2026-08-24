"""Atomic complete-bundle release and legacy family classification."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.golden_press import product_bundle as bundle
from agents.reports.product_families import (
    FAMILIES, external_product_family, internal_family_ids,
)


APPROVED_AUTHORIZATION = {
    "authorized": True,
    "assess_status": "approved",
    "target_status": "approved",
    "problems": [],
}


def _prepare_stub_bundle(tmp_path, monkeypatch, *, graph=None,
                         captured_inputs=None, pressed_payload=None):
    pressed = tmp_path / "pressed.json"
    pressed.write_text(json.dumps(
        pressed_payload or {"client_name": "Acme"}) + "\n", encoding="utf-8")
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{}\n", encoding="utf-8")
    pack = SimpleNamespace(client_name="Acme", generated_at="2026-08-23",
                           records=[], lanes=[], queries=[], events=[],
                           events_screen={})
    graph = graph or {
        "schema_version": "evidence-pack-v2", "graph_contract_certified": True,
        "graph_contract_violations": [], "records": [],
        "qualified_opportunity_records": [], "target_groups": {},
        "held_opportunities": [], "workflow_contract": {"certified": True},
        "incremental_cache_receipt": {},
    }
    captured_inputs = captured_inputs or {}
    product = SimpleNamespace(
        slots=[SimpleNamespace(slot_id=f"slot-{index}") for index in range(1, 9)],
        to_dict=lambda: {"slots": [f"slot-{index}" for index in range(1, 9)]})
    monkeypatch.setattr(bundle, "_load_pack", lambda *_args: (pressed, pack))
    monkeypatch.setattr(bundle, "_load_profile", lambda *_args: {})
    monkeypatch.setattr(
        bundle, "_build_graph",
        lambda *_args, **_kwargs: (graph_path, graph))
    monkeypatch.setattr(bundle, "build_external_product_document",
                        lambda **_kwargs: product)
    monkeypatch.setattr(bundle, "render_external_product",
                        lambda _product: (
                            "<html>studio</html>", "<html>client</html>"))
    monkeypatch.setattr(bundle, "validate_external_product_html",
                        lambda *_args: {"ok": True, "violations": []})
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args: dict(APPROVED_AUTHORIZATION))
    from agents.golden_press import market_map_projection
    monkeypatch.setattr(
        market_map_projection, "capture_inputs",
        lambda **_kwargs: captured_inputs)
    monkeypatch.setattr(market_map_projection, "build_market_map",
                        lambda *_args, **_kwargs: SimpleNamespace())

    def build():
        return bundle.build_complete_bundle(
            client_name="Acme", slug="acme", root=tmp_path,
            as_of="2026-08-23", release_requested=True,
            authorization_fn=lambda *_args: dict(APPROVED_AUTHORIZATION))

    return build, graph, captured_inputs


def _refresh_current_pointer(tmp_path, result):
    pointer_path = (
        tmp_path / "data" / "releases" / "acme" / "current.json")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["manifest_sha256"] = bundle._sha_file(Path(result.manifest_path))
    pointer["bundle_sha256"] = bundle._sha_file(Path(result.bundle_path))
    pointer_path.write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def test_exactly_one_output_family_is_external_and_releasable():
    external = [family for family in FAMILIES if family.role == "external_product"]
    assert external == [external_product_family()]
    assert external[0].family_id == "lila_federal_market_map"
    assert all(not family.releasable for family in FAMILIES
               if family.family_id in internal_family_ids())


def test_release_switch_and_operator_gates_fail_closed(tmp_path):
    with pytest.raises(bundle.ProductReleaseBlocked):
        bundle.build_complete_bundle(
            client_name="Acme", slug="acme", root=tmp_path,
            as_of="2026-08-23", release_requested=False,
            authorization_fn=lambda *_args: {"authorized": True})
    with pytest.raises(bundle.ProductReleaseBlocked):
        bundle.build_complete_bundle(
            client_name="Acme", slug="acme", root=tmp_path,
            as_of="2026-08-23", release_requested=True,
            authorization_fn=lambda *_args: {
                "authorized": False, "problems": ["Assess approval is stale"]})
    assert not (tmp_path / "data" / "releases").exists()


def test_one_release_action_writes_complete_hash_bound_zip(
        tmp_path, monkeypatch):
    graph = {
        "schema_version": "evidence-pack-v2", "graph_contract_certified": True,
        "graph_contract_violations": [], "records": [],
        "qualified_opportunity_records": [], "target_groups": {},
        "held_opportunities": [], "workflow_contract": {"certified": True},
        "incremental_cache_receipt": {
            "cache_root": "/Users/example/private/cache",
            "providers": {"dense": {"path": "/home/example/dense.db"}},
        },
    }
    captured_inputs = {
        "operator_cache": "file:///Users/example/private/operator.json",
    }
    build, internal_graph, internal_inputs = _prepare_stub_bundle(
        tmp_path, monkeypatch, graph=graph, captured_inputs=captured_inputs)
    result = build()
    release_dir = Path(result.release_dir)
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["release_eligible"] is True
    assert manifest["product_family"] == "lila_federal_market_map"
    assert set(manifest["files"]) == {
        "LILA_acme_2026-08-23.html", "LILA_acme_2026-08-23.studio.html",
        "product.json", "federal_pursuit_graph.json", "evidence_pack.json",
        "captured_inputs.json", "validation.json", "README.txt",
    }
    for name, receipt in manifest["files"].items():
        assert bundle._sha_file(release_dir / name) == receipt["sha256"]
    with zipfile.ZipFile(result.bundle_path) as archive:
        assert set(archive.namelist()) == set(manifest["files"]) | {"manifest.json"}
        for name in archive.namelist():
            payload = archive.read(name).lower()
            assert b"/users/" not in payload, name
            assert b"/home/" not in payload, name
            assert b"file://" not in payload, name
    portable_graph = json.loads(
        (release_dir / "federal_pursuit_graph.json").read_text(encoding="utf-8"))
    portable_inputs = json.loads(
        (release_dir / "captured_inputs.json").read_text(encoding="utf-8"))
    assert portable_graph["incremental_cache_receipt"]["cache_root"] == \
        "local-artifact:cache"
    dense = portable_graph["incremental_cache_receipt"]["providers"]["dense"]
    assert dense["path"] == "local-artifact:dense.db"
    assert portable_inputs["operator_cache"] == "local-artifact:operator.json"
    internal_cache = internal_graph["incremental_cache_receipt"]["cache_root"]
    assert internal_cache.startswith("/Users/")
    assert internal_inputs["operator_cache"].startswith("file://")
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is True
    again = build()
    assert again.release_id == result.release_id
    assert again.bundle_sha256 == result.bundle_sha256
    (release_dir / "product.json").write_text("tampered\n", encoding="utf-8")
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is False
    assert "bundle member hash changed: product.json" in state["reason"]


def test_product_release_state_revalidates_current_authorization(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(tmp_path, monkeypatch)
    build()
    monkeypatch.setattr(bundle, "_authorization", lambda *_args: {
        "authorized": False,
        "problems": ["Assess approval is revoked"],
    })

    state = bundle.product_release_state("acme", root=tmp_path)

    assert state["releasable"] is False
    assert "Assess approval is revoked" in state["reason"]


def test_product_release_state_fails_closed_when_authorization_is_unavailable(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(tmp_path, monkeypatch)
    build()

    def unavailable(*_args):
        raise RuntimeError("gate offline")

    monkeypatch.setattr(bundle, "_authorization", unavailable)
    state = bundle.product_release_state("acme", root=tmp_path)

    assert state["releasable"] is False
    assert "operator authorization is unavailable" in state["reason"]
    assert "gate offline" in state["reason"]


def test_build_graph_wires_governed_deep_sweep_and_release_date(
        tmp_path, monkeypatch):
    from agents.golden_press import evidence_pack_v2
    from tools import notice_store

    pressed = tmp_path / "pressed.json"
    pressed.write_text("{}\n", encoding="utf-8")
    expected_graph = tmp_path / "graph.json"
    seen = {}

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()

    def build_corrected_pack(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return expected_graph, {"graph_contract_certified": True}

    monkeypatch.setattr(notice_store, "connect", lambda: connection)
    monkeypatch.setattr(
        evidence_pack_v2, "build_corrected_pack", build_corrected_pack)

    result = bundle._build_graph(
        tmp_path, "acme", "Acme", pressed, as_of="2026-08-24")

    assert result[0] == expected_graph
    assert seen["args"] == ("acme", "Acme")
    assert seen["kwargs"]["deep_sweep_path"] == (
        tmp_path / "data" / "state" / "retrieval" / "deep_sweep_acme.json")
    assert seen["kwargs"]["classification_as_of"] == "2026-08-24"
    assert seen["kwargs"]["pressed_pack_path"] == pressed
    assert seen["kwargs"]["store_conn"] is connection
    assert connection.closed is True


def test_release_refuses_local_path_in_unsanitized_evidence_member(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(
        tmp_path, monkeypatch,
        pressed_payload={
            "client_name": "Acme",
            "note": "operator receipt at /Users/example/private/source.txt",
        })

    with pytest.raises(bundle.ProductReleaseBlocked, match="evidence_pack.json"):
        build()

    assert not (
        tmp_path / "data" / "releases" / "acme" / "current.json").exists()


def test_release_allows_absolute_web_urls_with_home_path(tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(
        tmp_path, monkeypatch,
        pressed_payload={
            "client_name": "Acme",
            "landing_page": "https://agency.gov/home/opportunities",
        })

    result = build()

    assert Path(result.bundle_path).is_file()


def test_legacy_hash_valid_local_path_member_is_rejected_not_reused(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(tmp_path, monkeypatch)
    result = build()
    release_dir = Path(result.release_dir)
    member = release_dir / "evidence_pack.json"
    member.write_text(
        json.dumps({
            "client_name": "Acme",
            "operator_source": "/Users/example/private/source.json",
        }) + "\n",
        encoding="utf-8",
    )
    manifest_path = Path(result.manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "lila-complete-bundle.v1.2026-08-23"
    manifest["files"][member.name]["sha256"] = bundle._sha_file(member)
    manifest["files"][member.name]["bytes"] = member.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _refresh_current_pointer(tmp_path, result)

    assert bundle._validate_existing_release(release_dir) is None
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is False
    assert "manifest schema version is not current" in state["reason"]
    assert (
        "local filesystem path appears in bundle member: evidence_pack.json"
        in state["reason"]
    )
    with pytest.raises(
            bundle.ProductReleaseError,
            match="existing release directory is incomplete or corrupt"):
        build()


def test_current_manifest_with_local_path_is_not_reusable_or_releasable(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(tmp_path, monkeypatch)
    result = build()
    manifest_path = Path(result.manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["operator_debug_path"] = "file:///home/example/private/replay.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _refresh_current_pointer(tmp_path, result)

    assert bundle._validate_existing_release(Path(result.release_dir)) is None
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is False
    assert "local filesystem path appears in manifest.json" in state["reason"]


def test_current_archive_with_unlisted_local_path_is_not_reusable_or_releasable(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(tmp_path, monkeypatch)
    result = build()
    bundle_path = Path(result.bundle_path)
    with zipfile.ZipFile(bundle_path) as archive:
        members = [
            (info.filename, archive.read(info))
            for info in archive.infolist()
            if not info.is_dir()
        ]
    members.append((
        "legacy-debug.txt",
        b"replay source: /Users/example/private/replay.json\n",
    ))
    with zipfile.ZipFile(bundle_path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    _refresh_current_pointer(tmp_path, result)

    assert bundle._validate_existing_release(Path(result.release_dir)) is None
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is False
    assert (
        "local filesystem path appears in archive member: legacy-debug.txt"
        in state["reason"]
    )


def test_current_archive_with_local_path_member_name_is_not_reusable_or_releasable(
        tmp_path, monkeypatch):
    build, _graph, _inputs = _prepare_stub_bundle(tmp_path, monkeypatch)
    result = build()
    bundle_path = Path(result.bundle_path)
    with zipfile.ZipFile(bundle_path) as archive:
        members = [
            (info.filename, archive.read(info))
            for info in archive.infolist()
            if not info.is_dir()
        ]
    members.append(("/Users/example/private/replay.txt", b"redacted\n"))
    with zipfile.ZipFile(bundle_path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    _refresh_current_pointer(tmp_path, result)

    assert bundle._validate_existing_release(Path(result.release_dir)) is None
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is False
    assert (
        "local filesystem path appears in archive member: "
        "/Users/example/private/replay.txt" in state["reason"]
    )
