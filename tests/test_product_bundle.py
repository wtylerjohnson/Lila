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
    pressed = tmp_path / "pressed.json"
    pressed.write_text('{"client_name":"Acme"}\n', encoding="utf-8")
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{}\n", encoding="utf-8")
    pack = SimpleNamespace(client_name="Acme", generated_at="2026-08-23",
                           records=[], lanes=[], queries=[], events=[],
                           events_screen={})
    graph = {
        "schema_version": "evidence-pack-v2", "graph_contract_certified": True,
        "graph_contract_violations": [], "records": [],
        "qualified_opportunity_records": [], "target_groups": {},
        "held_opportunities": [], "workflow_contract": {"certified": True},
        "incremental_cache_receipt": {},
    }
    product = SimpleNamespace(
        slots=[SimpleNamespace(slot_id=f"slot-{index}") for index in range(1, 9)],
        to_dict=lambda: {"slots": [f"slot-{index}" for index in range(1, 9)]})
    monkeypatch.setattr(bundle, "_load_pack", lambda *_args: (pressed, pack))
    monkeypatch.setattr(bundle, "_load_profile", lambda *_args: {})
    monkeypatch.setattr(bundle, "_build_graph", lambda *_args: (graph_path, graph))
    monkeypatch.setattr(bundle, "build_external_product_document",
                        lambda **_kwargs: product)
    monkeypatch.setattr(bundle, "render_external_product",
                        lambda _product: ("<html>studio</html>", "<html>client</html>"))
    monkeypatch.setattr(bundle, "validate_external_product_html",
                        lambda *_args: {"ok": True, "violations": []})
    from agents.golden_press import market_map_projection
    monkeypatch.setattr(market_map_projection, "capture_inputs", lambda **_kwargs: {})
    monkeypatch.setattr(market_map_projection, "build_market_map",
                        lambda *_args, **_kwargs: SimpleNamespace())

    result = bundle.build_complete_bundle(
        client_name="Acme", slug="acme", root=tmp_path,
        as_of="2026-08-23", release_requested=True,
        authorization_fn=lambda *_args: {
            "authorized": True, "assess_status": "approved",
            "target_status": "approved", "problems": []})
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
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is True
    again = bundle.build_complete_bundle(
        client_name="Acme", slug="acme", root=tmp_path,
        as_of="2026-08-23", release_requested=True,
        authorization_fn=lambda *_args: {
            "authorized": True, "assess_status": "approved",
            "target_status": "approved", "problems": []})
    assert again.release_id == result.release_id
    assert again.bundle_sha256 == result.bundle_sha256
    (release_dir / "product.json").write_text("tampered\n", encoding="utf-8")
    state = bundle.product_release_state("acme", root=tmp_path)
    assert state["releasable"] is False
    assert "bundle member hash changed: product.json" in state["reason"]
