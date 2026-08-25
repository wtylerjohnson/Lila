"""ReleaseSnapshot, pure compilation, and atomic bundle promotion."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import inspect
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Optional

import pytest

from agents.golden_press import product_bundle as bundle
from agents.golden_press.external_product_contract import (
    CONTRACT_VERSION,
    OPERATOR_LOCKED_SLOT_SHA256,
    load_external_product_slots,
)
from agents.golden_press.release_compiler import (
    ReleaseCompilationError,
    _content_identity,
    _zip_bytes,
    compile_release_snapshot,
)
from agents.golden_press.release_snapshot import (
    LIVE_RELEASE_PURPOSE,
    RELEASE_SNAPSHOT_VERSION,
    REPRODUCIBILITY_FIXTURE_PURPOSE,
    ReleaseSnapshot,
    ReleaseSnapshotError,
    canonical_json_bytes,
    canonical_slot_sha256,
    canonicalize_release_value,
)
from agents.reports.product_families import (
    FAMILIES,
    external_product_family,
    internal_family_ids,
)


FROZEN = "2026-08-24T12:00:00+00:00"
_HASHES = {
    "target_set_sha256": "1" * 64,
    "plan_sha256": "2" * 64,
    "assess_approval_sha256": "3" * 64,
    "target_unlock_sha256": "4" * 64,
}
APPROVED_AUTHORIZATION = {
    "authorized": True,
    "assess_status": "approved",
    "target_status": "approved",
    "targeting_review_status": "approved",
    "targeting_review": {
        "version": 2,
        "approved_at": "2026-08-24T11:00:00+00:00",
        "approved_by": "operator",
        **_HASHES,
    },
    "problems": [],
}


@pytest.fixture(autouse=True)
def _freeze_release_clock(monkeypatch):
    """Keep shelf freshness assertions independent of the machine calendar."""
    monkeypatch.setattr(bundle, "_utc_now", lambda: FROZEN)


def _mark() -> str:
    raw = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="48">'
        b'<rect width="120" height="48" fill="black"/></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode()


def _graph(
    *, classification_as_of: str = FROZEN, captured_at: str = FROZEN,
) -> dict:
    from tools.intelligence_graph.cache import stable_hash

    classification_context = {"as_of": classification_as_of}
    return {
        "schema_version": "evidence-pack-v2",
        "generated_at": captured_at,
        "captured_at": captured_at,
        "classification_context": classification_context,
        "graph_contract_certified": True,
        "graph_contract_violations": [],
        "records": [],
        "qualified_opportunity_records": [],
        "target_groups": {},
        "held_opportunities": [],
        "workflow_contract": {"certified": True},
        "counts": {"input_records_raw": 0, "canonical_records": 0},
        "record_identity_receipt": {
            "input_records_raw": 0, "canonical_records": 0},
        "research_mesh_receipt": {
            "artifact_name": "searches_acme.json",
            "sha256": "0" * 64,
            "generated_at": classification_as_of,
            "sam_notice_rows_imported": 0,
            "forecast_rows_imported": 0,
        },
        "incremental_cache_receipt": {
            "schema_version": "incremental-cache-semantic-receipt-v1",
            "adapter": "existing-systems-graph-adapter-v2",
            "client_slug": "acme",
            "context_hash": stable_hash(classification_context),
            "semantic_dependencies": {
                "as_of": classification_as_of, "rule_version": "rules-v1"},
            "providers": {},
        },
    }


def _snapshot(
    tmp_path: Path, *, purpose: str = LIVE_RELEASE_PURPOSE,
    evidence_extra: Optional[dict] = None,
    classification_as_of: str = FROZEN,
    captured_at: str = FROZEN,
) -> ReleaseSnapshot:
    from agents.golden_press.market_map_skeleton import load_css, load_runtime

    graph = _graph(
        classification_as_of=classification_as_of,
        captured_at=captured_at)
    evidence = {
        "schema_version": 1,
        "client_name": "Acme",
        "generated_at": classification_as_of,
        "lanes": [],
        "queries": [],
        "records": [],
        "events": [],
        "events_screen": {"screened": 0, "kept": 0},
        **(evidence_extra or {}),
    }
    evidence_bytes = (
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n").encode()
    inputs = {
        "store_candidates": {},
        "store_search_terms": [],
        "store_naics_boundary": [],
        "store_psc_boundary": [],
        "store_excluded_terms": [],
        "targets_payload": {},
        "rival_footprint": {},
        "coverage_contract": {},
        "coverage_report": {},
        "pending_terms": [],
        "record_rulings": {},
        "evidence_pack_v2": graph,
    }
    return ReleaseSnapshot.create(
        purpose=purpose,
        client_name="Acme",
        slug="acme",
        business_as_of="2026-08-24",
        classification_as_of=classification_as_of,
        captured_at=captured_at,
        contract_slots=load_external_product_slots(),
        versions={"test_fixture": "release-snapshot-test.v1"},
        authorization=APPROVED_AUTHORIZATION,
        profile={
            "client_name": "Acme",
            "capability_terms": {"core": ["language services"]},
            "naics_boundary": ["541930"],
            "named_competitors_and_incumbents": [],
        },
        graph=graph,
        market_map_inputs=inputs,
        render_assets={
            "css": load_css(),
            "runtime": load_runtime(),
            "client_mark": _mark(),
            "gtm_mark": _mark(),
        },
        root=tmp_path,
        evidence_pack_bytes=evidence_bytes,
    )


def _promote(
    snapshot: ReleaseSnapshot, root: Path, *, desktop: Optional[Path] = None,
):
    compiled = compile_release_snapshot(snapshot)
    result = bundle._promote_compiled_release(
        compiled, snapshot, root=root,
        deliver_to_desktop=desktop is not None,
        desktop_root=desktop,
    )
    return compiled, result


def _rewrite_zip(
    path: Path, *, replacements: Optional[dict[str, bytes]] = None,
    metadata_change: Optional[str] = None,
) -> bytes:
    """Rewrite a test archive while changing only the requested proof leg."""
    replacements = replacements or {}
    with zipfile.ZipFile(path) as archive:
        rows = [(info, archive.read(info)) for info in archive.infolist()]
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for original, payload in rows:
            date_time = original.date_time
            if metadata_change == "date_time":
                date_time = (2026, 8, 25, 0, 0, 0)
            info = zipfile.ZipInfo(original.filename, date_time=date_time)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = (
                0 if metadata_change == "create_system" else original.create_system)
            info.external_attr = (
                0o100600 << 16
                if metadata_change == "mode" else original.external_attr)
            archive.writestr(
                info, replacements.get(original.filename, payload))
    rewritten = target.getvalue()
    path.write_bytes(rewritten)
    return rewritten


def test_exactly_one_output_family_is_external_and_releasable():
    external = [family for family in FAMILIES if family.role == "external_product"]
    assert external == [external_product_family()]
    assert external[0].family_id == "lila_federal_market_map"
    assert all(not family.releasable for family in FAMILIES
               if family.family_id in internal_family_ids())


def test_direct_promotion_is_private_and_not_exported():
    assert "promote_compiled_release" not in bundle.__all__
    assert not hasattr(bundle, "promote_compiled_release")
    assert hasattr(bundle, "_promote_compiled_release")


def test_release_switch_and_operator_gates_fail_before_capture(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        bundle, "capture_release_snapshot",
        lambda **_kwargs: pytest.fail("capture must not run"))
    with pytest.raises(bundle.ProductReleaseBlocked, match="release switch"):
        bundle.build_complete_bundle(
            client_name="Acme", slug="acme", root=tmp_path,
            as_of="2026-08-24", release_requested=False)
    with pytest.raises(bundle.ProductReleaseBlocked, match="Assess is revoked"):
        bundle.build_complete_bundle(
            client_name="Acme", slug="acme", root=tmp_path,
            as_of="2026-08-24", release_requested=True,
            target_inventory_loader=lambda *_args: [],
            authorization_fn=lambda *_args: {
                "authorized": False, "problems": ["Assess is revoked"]})
    assert not (tmp_path / "data" / "releases").exists()


def test_status_only_authorization_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bundle, "capture_release_snapshot",
        lambda **_kwargs: pytest.fail("capture must not run"))
    with pytest.raises(
            bundle.ProductReleaseBlocked, match="exact Targeting Review"):
        bundle.build_complete_bundle(
            client_name="Acme", slug="acme", root=tmp_path,
            as_of="2026-08-24", release_requested=True,
            target_inventory_loader=lambda *_args: [],
            authorization_fn=lambda *_args: {"authorized": True})


def test_target_inventory_is_rederived_after_compilation(
        tmp_path, monkeypatch):
    from agents.review import targeting_target_set_sha256

    initial_targets = [{
        "id": "target-1",
        "person_name": "First Target",
        "source_url": "https://sam.gov/notice/one",
    }]
    changed_targets = [{
        **initial_targets[0],
        "last_observed": "2026-08-24T12:00:01Z",
    }]
    initial_sha256 = targeting_target_set_sha256(initial_targets)
    authorization = copy.deepcopy(APPROVED_AUTHORIZATION)
    authorization["targeting_review"]["target_set_sha256"] = initial_sha256
    inventories = iter((initial_targets, changed_targets))
    calls = []

    def load_targets(client_name, root):
        calls.append((client_name, root))
        return next(inventories)

    def capture_snapshot(**kwargs):
        payload = _snapshot(tmp_path).to_dict()
        payload["authorization"] = kwargs["authorization"]
        return ReleaseSnapshot.from_dict(payload)

    monkeypatch.setattr(bundle, "capture_release_snapshot", capture_snapshot)
    monkeypatch.setattr(
        bundle, "_promote_compiled_release",
        lambda *_args, **_kwargs: pytest.fail(
            "promotion must not run after target inventory drift"))

    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="target inventory changed during release compilation"):
        bundle.build_complete_bundle(
            client_name="Acme",
            slug="acme",
            root=tmp_path,
            as_of="2026-08-24",
            release_requested=True,
            authorization_fn=lambda *_args: copy.deepcopy(authorization),
            target_inventory_loader=load_targets,
            expected_target_set_sha256=initial_sha256,
            classification_as_of=FROZEN,
            captured_at=FROZEN,
        )

    assert [client_name for client_name, _root in calls] == ["Acme", "Acme"]


def test_snapshot_binds_ratified_contract_without_amendment_gate(tmp_path):
    snapshot = _snapshot(tmp_path)
    assert snapshot.schema_version == RELEASE_SNAPSHOT_VERSION
    assert snapshot.contract_version == CONTRACT_VERSION
    assert snapshot.operator_locked_slot_sha256 == OPERATOR_LOCKED_SLOT_SHA256
    assert [row["heading"] for row in snapshot.contract_slots] == [
        slot.heading for slot in load_external_product_slots()]
    compiled = compile_release_snapshot(snapshot)
    assert compiled.content_receipt["operator_locked_slot_sha256"] == (
        OPERATOR_LOCKED_SLOT_SHA256)


@pytest.mark.parametrize(("field", "value"), (
    ("slug", "../other-client"),
    ("slug", "nested/client"),
    ("client_name", "../Other Client"),
    ("client_name", "Nested/Client"),
))
def test_snapshot_rejects_path_capable_client_identity(tmp_path, field, value):
    payload = _snapshot(tmp_path).to_dict()
    payload[field] = value

    with pytest.raises(ReleaseSnapshotError, match="slug|client name"):
        ReleaseSnapshot.from_dict(payload)


def test_historical_snapshot_replays_but_cannot_be_live_promoted(tmp_path):
    payload = _snapshot(tmp_path).to_dict()
    payload["contract_version"] = "market-map.v3.2026-08-01"
    payload["contract_slots"][0]["heading"] = "Historical priority view"
    payload["operator_locked_slot_sha256"] = canonical_slot_sha256(
        payload["contract_slots"])
    historical = ReleaseSnapshot.from_dict(payload)

    compiled = compile_release_snapshot(historical)

    assert compiled.content_receipt["contract_version"] == (
        "market-map.v3.2026-08-01")
    assert compiled.content_receipt["operator_locked_slot_sha256"] == (
        payload["operator_locked_slot_sha256"])
    with pytest.raises(
            bundle.ProductReleaseBlocked, match="current ratified"):
        bundle._promote_compiled_release(
            compiled, historical, root=tmp_path / "release-root")


def test_current_snapshot_cannot_promote_cross_client_historical_compilation(
        tmp_path):
    current = _snapshot(tmp_path)
    payload = current.to_dict()
    payload["client_name"] = "Other Client"
    payload["slug"] = "other-client"
    payload["contract_version"] = "market-map.v3.2026-08-01"
    payload["contract_slots"][0]["heading"] = "Historical priority view"
    payload["operator_locked_slot_sha256"] = canonical_slot_sha256(
        payload["contract_slots"])
    historical = ReleaseSnapshot.from_dict(payload)
    compiled = compile_release_snapshot(historical)
    release_root = tmp_path / "release-root"

    with pytest.raises(
            bundle.ProductReleaseError,
            match="compiled release does not match supplied snapshot"):
        bundle._promote_compiled_release(
            compiled, current, root=release_root)

    assert not (release_root / "data" / "releases").exists()


def test_stored_historical_contract_is_replayable_but_not_current(
        tmp_path, monkeypatch):
    payload = _snapshot(tmp_path).to_dict()
    payload["contract_version"] = "market-map.v3.2026-08-01"
    payload["contract_slots"][0]["heading"] = "Historical priority view"
    payload["operator_locked_slot_sha256"] = canonical_slot_sha256(
        payload["contract_slots"])
    historical = ReleaseSnapshot.from_dict(payload)
    compiled = compile_release_snapshot(historical)
    release_root = tmp_path / "release-root"
    bundle._materialize_release(compiled, root=release_root, slug="acme")
    bundle._promote_pointer(compiled, root=release_root, slug="acme")
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))

    state = bundle.product_release_state("acme", root=release_root)

    assert state["releasable"] is False
    assert "current ratified product contract" in state["reason"]


def test_fractional_release_clocks_survive_every_owning_surface(tmp_path):
    classification_as_of = "2026-08-24T12:00:00.987654Z"
    captured_at = "2026-08-24T12:05:06.123456Z"
    snapshot = _snapshot(
        tmp_path,
        classification_as_of=classification_as_of,
        captured_at=captured_at,
    )
    replayed = ReleaseSnapshot.from_dict(json.loads(
        canonical_json_bytes(snapshot.to_dict())))

    assert snapshot.classification_as_of == classification_as_of
    assert snapshot.captured_at == captured_at
    assert replayed.classification_as_of == classification_as_of
    assert replayed.captured_at == captured_at
    assert replayed.graph["classification_context"]["as_of"] == (
        classification_as_of)
    assert replayed.graph["captured_at"] == captured_at

    compiled = compile_release_snapshot(replayed)
    product = json.loads(compiled.payloads["product.json"])
    captured_inputs = json.loads(compiled.payloads["captured_inputs.json"])
    validation = json.loads(compiled.payloads["validation.json"])

    assert product["source_receipt"]["classification_as_of"] == (
        classification_as_of)
    assert product["source_receipt"]["graph_captured_at"] == captured_at
    for name in (compiled.primary_html, compiled.studio_html):
        assert classification_as_of in compiled.payloads[name].decode("utf-8")
    assert captured_inputs["classification_as_of"] == classification_as_of
    assert captured_inputs["captured_at"] == captured_at
    assert validation["classification_as_of"] == classification_as_of
    assert validation["captured_at"] == captured_at
    assert compiled.manifest["classification_as_of"] == classification_as_of
    assert compiled.manifest["captured_at"] == captured_at
    assert compiled.content_receipt["classification_as_of"] == (
        classification_as_of)
    assert compiled.content_receipt["captured_at"] == captured_at


@pytest.mark.parametrize(("certified", "violations", "problem"), (
    (False, [], "not certified"),
    (True, [{"rule_id": "fixture-violation"}], "contract violations"),
))
def test_snapshot_refuses_uncertified_or_contradictory_graph(
        tmp_path, certified, violations, problem):
    payload = _snapshot(tmp_path).to_dict()
    graph = copy.deepcopy(payload["graph"])
    graph["graph_contract_certified"] = certified
    graph["graph_contract_violations"] = violations
    payload["graph"] = graph
    payload["market_map_inputs"]["evidence_pack_v2"] = graph

    with pytest.raises(ReleaseSnapshotError, match=problem):
        ReleaseSnapshot.from_dict(payload)


def test_snapshot_requires_exact_source_evidence_fields(tmp_path):
    payload = _snapshot(tmp_path).to_dict()
    payload.pop("source_evidence_pack_base64")
    payload.pop("source_evidence_pack_sha256")

    with pytest.raises(ReleaseSnapshotError, match="exact source evidence"):
        ReleaseSnapshot.from_dict(payload)


def test_canonicalization_is_idempotent_and_converges_graph_paths(tmp_path):
    raw = {
        "cache_root": str(tmp_path / "data" / "cache"),
        "embeddings": {
            "path": str(tmp_path / "data" / "embeddings.db"),
            "receipt": "file:///Users/operator/receipts/dense.json",
        },
        "contact_graph": {"path": "/home/operator/contact.sqlite"},
        "marks": {"path": "/Users/operator/marks/client.svg"},
        "unordered": {"b", "a"},
    }
    once = canonicalize_release_value(raw, root=tmp_path)
    twice = canonicalize_release_value(once, root=tmp_path)
    assert once == twice
    assert once["cache_root"] == "data/cache"
    assert once["embeddings"]["path"] == "data/embeddings.db"
    assert once["embeddings"]["receipt"] == "local-artifact:dense.json"
    assert once["contact_graph"]["path"] == "local-artifact:contact.sqlite"
    assert once["marks"]["path"] == "local-artifact:client.svg"
    assert once["unordered"] == ["a", "b"]


def test_pure_compiler_denies_live_readers_and_is_byte_identical(
        tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path)
    from agents.golden_press import (
        external_product_contract,
        market_map_projection,
        market_map_skeleton,
        report_templates,
        rival_footprint,
    )
    from agents.reports import report_assets

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pure compiler invoked a live reader")

    for name in (
        "capture_inputs", "_pending_terms", "_coverage_contract",
        "_load_record_rulings", "_load_evidence_pack_v2",
    ):
        monkeypatch.setattr(market_map_projection, name, forbidden)
    monkeypatch.setattr(external_product_contract,
                        "load_external_product_slots", forbidden)
    monkeypatch.setattr(market_map_skeleton, "load_css", forbidden)
    monkeypatch.setattr(market_map_skeleton, "load_runtime", forbidden)
    monkeypatch.setattr(report_assets, "client_logo", forbidden)
    monkeypatch.setattr(report_assets, "gtm_logo", forbidden)
    monkeypatch.setattr(report_assets, "agency_seal", forbidden)
    monkeypatch.setattr(report_assets, "company_logo", forbidden)
    monkeypatch.setattr(report_templates, "find_mark", forbidden)
    monkeypatch.setattr(rival_footprint, "load_cached", forbidden)

    payload = snapshot.to_dict()
    payload["market_map_inputs"]["rival_footprint"] = None
    snapshot = ReleaseSnapshot.from_dict(payload)

    first = compile_release_snapshot(snapshot)
    second = compile_release_snapshot(
        ReleaseSnapshot.from_dict(json.loads(
            canonical_json_bytes(snapshot.to_dict()))))
    assert first.payloads == second.payloads
    assert first.manifest_bytes == second.manifest_bytes
    assert first.zip_bytes == second.zip_bytes
    assert first.content_digest == second.content_digest
    source = inspect.getsource(compile_release_snapshot)
    assert "datetime.now" not in source
    assert "date.today" not in source


def test_ambient_next_day_clock_cannot_change_compiled_bytes(
        tmp_path, monkeypatch):
    import time

    snapshot = _snapshot(tmp_path)
    monkeypatch.setattr(time, "time", lambda: 1_776_000_000.0)
    first = compile_release_snapshot(snapshot)
    monkeypatch.setattr(time, "time", lambda: 1_776_086_400.0)
    second = compile_release_snapshot(snapshot)

    assert first.payloads == second.payloads
    assert first.manifest_bytes == second.manifest_bytes
    assert first.zip_bytes == second.zip_bytes


def test_cold_and_warm_cache_compile_to_identical_complete_bundles(tmp_path):
    from tools.intelligence_graph.adapter import ExistingSystemsGraphAdapter

    context = {"as_of": FROZEN}
    kwargs = {
        "root": tmp_path,
        "slug": "acme",
        "client_name": "Acme",
        "classification_context": context,
        "cache_dir": tmp_path / "cache",
    }
    cold = ExistingSystemsGraphAdapter(**kwargs)
    warm = ExistingSystemsGraphAdapter(**kwargs)
    assert cold.receipt()["actions"] != warm.receipt()["actions"]
    assert cold.semantic_receipt() == warm.semantic_receipt()

    base = _snapshot(tmp_path)

    def with_receipt(receipt):
        graph = copy.deepcopy(base.graph)
        graph["incremental_cache_receipt"] = receipt
        inputs = copy.deepcopy(base.market_map_inputs)
        inputs["evidence_pack_v2"] = graph
        return replace(base, graph=graph, market_map_inputs=inputs)

    cold_compilation = compile_release_snapshot(
        with_receipt(cold.semantic_receipt()))
    warm_compilation = compile_release_snapshot(
        with_receipt(warm.semantic_receipt()))
    assert cold_compilation.payloads == warm_compilation.payloads
    assert cold_compilation.manifest_bytes == warm_compilation.manifest_bytes
    assert cold_compilation.zip_bytes == warm_compilation.zip_bytes


def test_shipped_graph_reprojects_to_identical_product(tmp_path):
    snapshot = _snapshot(tmp_path)
    first = compile_release_snapshot(snapshot)
    shipped_graph = json.loads(first.payloads[
        "federal_pursuit_graph.json"].decode())
    replay_inputs = copy.deepcopy(snapshot.market_map_inputs)
    replay_inputs["evidence_pack_v2"] = shipped_graph
    replay = replace(
        snapshot, graph=shipped_graph, market_map_inputs=replay_inputs)
    second = compile_release_snapshot(replay)
    assert first.payloads["product.json"] == second.payloads["product.json"]
    assert first.payloads["federal_pursuit_graph.json"] == second.payloads[
        "federal_pursuit_graph.json"]


def test_content_receipt_covers_every_payload_and_each_mutation_moves_identity(
        tmp_path):
    snapshot = _snapshot(tmp_path)
    compiled = compile_release_snapshot(snapshot)
    receipts = compiled.content_receipt["files"]
    assert set(receipts) == set(compiled.payloads)
    assert len(compiled.content_digest) == 64
    assert len(compiled.release_id.rsplit("-", 1)[1]) == 24
    for name, payload in compiled.payloads.items():
        assert receipts[name] == {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        changed_payloads = dict(compiled.payloads)
        changed_payloads[name] = payload + b"\nidentity mutation\n"
        changed_receipt, changed_digest = _content_identity(
            snapshot, changed_payloads)

        assert changed_receipt["files"][name] == {
            "sha256": hashlib.sha256(changed_payloads[name]).hexdigest(),
            "bytes": len(changed_payloads[name]),
        }
        assert changed_digest != compiled.content_digest
        assert f"{snapshot.business_as_of}-{changed_digest[:24]}" != (
            compiled.release_id)


def test_two_independent_output_roots_and_reuse_are_exact(
        tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path)
    compiled = compile_release_snapshot(snapshot)
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    desktop = tmp_path / "desktop"

    first = bundle._promote_compiled_release(
        compiled, snapshot, root=root_a)
    second = bundle._promote_compiled_release(
        compiled, snapshot, root=root_b)
    assert Path(first.bundle_path).read_bytes() == Path(second.bundle_path).read_bytes()
    assert Path(first.manifest_path).read_bytes() == Path(second.manifest_path).read_bytes()
    for name, payload in compiled.payloads.items():
        assert (Path(first.release_dir) / name).read_bytes() == payload
        assert (Path(second.release_dir) / name).read_bytes() == payload

    pointer = root_a / "data" / "releases" / "acme" / "current.json"
    pointer.unlink()
    reused = bundle._promote_compiled_release(
        compiled, snapshot, root=root_a,
        deliver_to_desktop=True, desktop_root=desktop)
    assert pointer.is_file()
    pointer_payload = json.loads(pointer.read_text())
    assert not Path(pointer_payload["manifest_path"]).is_absolute()
    assert not Path(pointer_payload["bundle_path"]).is_absolute()
    assert Path(reused.desktop_bundle_path).read_bytes() == compiled.zip_bytes
    assert Path(reused.desktop_html_path).read_bytes() == compiled.payloads[
        compiled.primary_html]


def test_exact_reuse_rejects_loose_or_archive_drift(tmp_path):
    snapshot = _snapshot(tmp_path)
    compiled, result = _promote(snapshot, tmp_path / "release-root")
    release_dir = Path(result.release_dir)
    (release_dir / "product.json").write_bytes(b"tampered\n")
    with pytest.raises(bundle.ProductReleaseError, match="incomplete or corrupt"):
        bundle._promote_compiled_release(
            compiled, snapshot, root=tmp_path / "release-root")


def test_exact_reuse_rejects_archive_only_drift_without_repromoting(tmp_path):
    snapshot = _snapshot(tmp_path)
    release_root = tmp_path / "release-root"
    compiled, result = _promote(snapshot, release_root)
    pointer_path = (
        release_root / "data" / "releases" / "acme" / "current.json")
    original_pointer = pointer_path.read_bytes()
    archive_path = Path(result.bundle_path)
    archive_path.write_bytes(archive_path.read_bytes() + b"archive drift")

    with pytest.raises(bundle.ProductReleaseError, match="incomplete or corrupt"):
        bundle._promote_compiled_release(
            compiled, snapshot, root=release_root)

    assert pointer_path.read_bytes() == original_pointer


def test_desktop_copy_failure_cannot_promote_pointer(tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path)
    compiled = compile_release_snapshot(snapshot)
    release_root = tmp_path / "release-root"
    desktop_root = tmp_path / "desktop"
    real_copy2 = bundle.shutil.copy2
    copy_count = 0

    def fail_second_copy(source, destination):
        nonlocal copy_count
        copy_count += 1
        if copy_count == 2:
            raise OSError("Desktop copy failed")
        return real_copy2(source, destination)

    monkeypatch.setattr(bundle.shutil, "copy2", fail_second_copy)

    with pytest.raises(OSError, match="Desktop copy failed"):
        bundle._promote_compiled_release(
            compiled,
            snapshot,
            root=release_root,
            deliver_to_desktop=True,
            desktop_root=desktop_root,
        )

    release_dir = (
        release_root / "data" / "releases" / "acme" / compiled.release_id)
    assert release_dir.is_dir()
    assert not (release_dir.parent / "current.json").exists()


def test_shelf_rejects_product_json_as_pointer_primary_html(
        tmp_path, monkeypatch):
    release_root = tmp_path / "release-root"
    compiled, _result = _promote(_snapshot(tmp_path), release_root)
    pointer_path = release_root / "data" / "releases" / "acme" / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["primary_html"] = f"{compiled.release_id}/product.json"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))

    state = bundle.product_release_state("acme", root=release_root)

    assert state["releasable"] is False
    assert "pointer primary HTML is not the declared primary HTML" in state["reason"]
    assert state["path"] is None


def test_shelf_rejects_manifest_identity_rebound_away_from_snapshot(
        tmp_path, monkeypatch):
    release_root = tmp_path / "release-root"
    _compiled, result = _promote(_snapshot(tmp_path), release_root)
    manifest_path = Path(result.manifest_path)
    bundle_path = Path(result.bundle_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["client_name"] = "Other Client"
    manifest["unexpected_release_claim"] = True
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    bundle_bytes = _rewrite_zip(
        bundle_path, replacements={"manifest.json": manifest_bytes})
    pointer_path = release_root / "data" / "releases" / "acme" / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    pointer["bundle_sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))

    state = bundle.product_release_state("acme", root=release_root)

    assert state["releasable"] is False
    assert "manifest field allowlist" in state["reason"]
    assert "manifest client_name does not match the release snapshot" in state[
        "reason"]


def test_shelf_rejects_self_consistent_payload_forgery(
        tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path)
    compiled = compile_release_snapshot(snapshot)
    forged_payloads = dict(compiled.payloads)
    forged_payloads["product.json"] += b"\n"
    forged_receipt, forged_digest = _content_identity(
        snapshot, forged_payloads)
    forged_release_id = (
        f"{snapshot.business_as_of}-{forged_digest[:24]}")
    forged_bundle_name = (
        f"LILA_{snapshot.slug}_{forged_release_id}_COMPLETE.zip")
    forged_manifest = copy.deepcopy(compiled.manifest)
    forged_manifest.update({
        "release_id": forged_release_id,
        "content_digest": forged_digest,
        "content_receipt": forged_receipt,
        "files": forged_receipt["files"],
        "bundle_name": forged_bundle_name,
    })
    forged_manifest_bytes = canonical_json_bytes(forged_manifest)
    forged_zip = _zip_bytes(
        {**forged_payloads, "manifest.json": forged_manifest_bytes},
        day=snapshot.business_as_of,
    )
    release_root = tmp_path / "release-root"
    release_dir = (
        release_root / "data" / "releases" / "acme" / forged_release_id)
    release_dir.mkdir(parents=True)
    for name, payload in forged_payloads.items():
        (release_dir / name).write_bytes(payload)
    (release_dir / "manifest.json").write_bytes(forged_manifest_bytes)
    (release_dir / forged_bundle_name).write_bytes(forged_zip)
    pointer = {
        "schema_version": bundle.CURRENT_POINTER_VERSION,
        "release_id": forged_release_id,
        "manifest_path": f"{forged_release_id}/manifest.json",
        "manifest_sha256": hashlib.sha256(forged_manifest_bytes).hexdigest(),
        "bundle_path": f"{forged_release_id}/{forged_bundle_name}",
        "bundle_sha256": hashlib.sha256(forged_zip).hexdigest(),
        "primary_html": f"{forged_release_id}/{compiled.primary_html}",
    }
    (release_dir.parent / "current.json").write_text(
        json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))

    state = bundle.product_release_state("acme", root=release_root)

    assert state["releasable"] is False
    assert "fresh snapshot compilation" in state["reason"]


def test_shelf_rejects_loose_member_symlink(tmp_path, monkeypatch):
    release_root = tmp_path / "release-root"
    compiled, result = _promote(_snapshot(tmp_path), release_root)
    release_dir = Path(result.release_dir)
    external = tmp_path / "external-product.json"
    external.write_bytes(compiled.payloads["product.json"])
    product_path = release_dir / "product.json"
    product_path.unlink()
    product_path.symlink_to(external)
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))

    state = bundle.product_release_state("acme", root=release_root)

    assert state["releasable"] is False
    assert "bundle member is a symlink: product.json" in state["reason"]


@pytest.mark.parametrize("metadata_change", [
    "create_system", "mode", "date_time",
])
def test_shelf_rejects_zip_metadata_drift(
        tmp_path, monkeypatch, metadata_change):
    release_root = tmp_path / "release-root"
    _compiled, result = _promote(_snapshot(tmp_path), release_root)
    bundle_path = Path(result.bundle_path)
    bundle_bytes = _rewrite_zip(
        bundle_path, metadata_change=metadata_change)
    pointer_path = release_root / "data" / "releases" / "acme" / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["bundle_sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))

    state = bundle.product_release_state("acme", root=release_root)

    assert state["releasable"] is False
    assert "bundle archive" in state["reason"]


def test_zip_is_stored_sorted_and_exact(tmp_path):
    compiled = compile_release_snapshot(_snapshot(tmp_path))
    archive_path = tmp_path / "compiled.zip"
    archive_path.write_bytes(compiled.zip_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == sorted(
            set(compiled.payloads) | {"manifest.json"})
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            expected = (
                compiled.manifest_bytes if info.filename == "manifest.json"
                else compiled.payloads[info.filename])
            assert archive.read(info) == expected


def test_fixture_compiles_but_cannot_promote(tmp_path):
    snapshot = _snapshot(
        tmp_path, purpose=REPRODUCIBILITY_FIXTURE_PURPOSE)
    compiled = compile_release_snapshot(snapshot)
    assert compiled.manifest["release_eligible"] is False
    assert compiled.manifest["release_state"] == "fixture"
    assert b"reproducibility fixture" in compiled.payloads[
        "README.txt"].lower()
    assert b"complete release bundle" not in compiled.payloads[
        "README.txt"].lower()
    assert b'data-reproducibility-fixture="1"' in compiled.payloads[
        compiled.primary_html]
    assert b'data-reproducibility-fixture="1"' in compiled.payloads[
        compiled.studio_html]
    with pytest.raises(bundle.ProductReleaseBlocked, match="fixture"):
        bundle._promote_compiled_release(
            compiled, snapshot, root=tmp_path / "release-root")
    assert not (tmp_path / "release-root" / "data" / "releases").exists()


def test_state_reauthorization_flips_without_changing_compilation(
        tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path)
    compiled, _result = _promote(snapshot, tmp_path / "release-root")
    monkeypatch.setattr(
        bundle, "_authorization",
        lambda *_args, **_kwargs: copy.deepcopy(APPROVED_AUTHORIZATION))
    assert bundle.product_release_state(
        "acme", root=tmp_path / "release-root")["releasable"] is True
    frozen_again = compile_release_snapshot(snapshot)

    monkeypatch.setattr(bundle, "_authorization", lambda *_args, **_kwargs: {
        **copy.deepcopy(APPROVED_AUTHORIZATION),
        "authorized": False,
        "target_status": "blocked",
        "problems": ["Target approval is revoked"],
    })
    state = bundle.product_release_state(
        "acme", root=tmp_path / "release-root")
    assert state["releasable"] is False
    assert "Target approval is revoked" in state["reason"]
    assert frozen_again.payloads == compiled.payloads
    assert frozen_again.zip_bytes == compiled.zip_bytes


def test_fresh_capture_cannot_hide_a_stale_evidence_pack(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        evidence_extra={"generated_at": "2026-07-01T12:00:00+00:00"},
    )
    with pytest.raises(bundle.ProductReleaseBlocked, match="evidence pack is older"):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_evidence_pack_cannot_postdate_classification_cutoff(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        classification_as_of="2026-08-24T11:00:00Z",
        captured_at=FROZEN,
        evidence_extra={"generated_at": "2026-08-24T11:30:00Z"},
    )

    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="evidence pack is later than classification_as_of"):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_deep_sweep_cannot_postdate_classification_cutoff(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        classification_as_of="2026-08-24T11:00:00Z",
        captured_at=FROZEN,
        evidence_extra={"generated_at": "2026-08-24T11:00:00Z"},
    )
    graph = copy.deepcopy(snapshot.graph)
    graph["deep_sweep_receipt"] = {"at": "2026-08-24T11:30:00Z"}
    inputs = copy.deepcopy(snapshot.market_map_inputs)
    inputs["evidence_pack_v2"] = graph
    snapshot = replace(
        snapshot, graph=graph, market_map_inputs=inputs)

    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="deep sweep is later than classification_as_of"):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_fresh_graph_capture_cannot_hide_a_stale_research_mesh(tmp_path):
    snapshot = _snapshot(tmp_path)
    stale_graph = copy.deepcopy(snapshot.graph)
    stale_graph["research_mesh_receipt"]["generated_at"] = (
        "2026-07-01T12:00:00+00:00")
    stale_inputs = copy.deepcopy(snapshot.market_map_inputs)
    stale_inputs["evidence_pack_v2"] = stale_graph
    snapshot = replace(
        snapshot, graph=stale_graph, market_map_inputs=stale_inputs)

    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="governed research mesh is older"):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_release_fails_closed_without_research_mesh_freshness_receipt(tmp_path):
    snapshot = _snapshot(tmp_path)
    graph = copy.deepcopy(snapshot.graph)
    graph.pop("research_mesh_receipt")
    inputs = copy.deepcopy(snapshot.market_map_inputs)
    inputs["evidence_pack_v2"] = graph
    snapshot = replace(
        snapshot, graph=graph, market_map_inputs=inputs)

    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="governed research mesh generated_at is not an ISO instant"):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_research_mesh_cannot_postdate_classification_cutoff(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        classification_as_of="2026-08-24T11:00:00Z",
        captured_at=FROZEN,
    )
    graph = copy.deepcopy(snapshot.graph)
    graph["research_mesh_receipt"]["generated_at"] = (
        "2026-08-24T11:30:00Z")
    inputs = copy.deepcopy(snapshot.market_map_inputs)
    inputs["evidence_pack_v2"] = graph
    snapshot = replace(
        snapshot, graph=graph, market_map_inputs=inputs)

    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="research mesh is later than classification_as_of"):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_classification_freshness_allows_fourteen_days_but_not_one_second_more(
        tmp_path):
    boundary = _snapshot(
        tmp_path,
        classification_as_of="2026-08-10T12:00:00Z",
        captured_at=FROZEN,
    )
    bundle._check_snapshot_freshness(boundary, now=FROZEN)

    stale = _snapshot(
        tmp_path,
        classification_as_of="2026-08-10T11:59:59Z",
        captured_at=FROZEN,
    )
    with pytest.raises(
            bundle.ProductReleaseBlocked,
            match="classification_as_of is older than fourteen days"):
        bundle._check_snapshot_freshness(stale, now=FROZEN)


@pytest.mark.parametrize(("classification_as_of", "captured_at", "problem"), [
    (
        "2026-08-24T12:01:00Z",
        "2026-08-24T12:00:00Z",
        "later than snapshot capture",
    ),
    (
        "2026-08-24T12:01:00Z",
        "2026-08-24T12:02:00Z",
        "classification_as_of is in the future",
    ),
])
def test_live_freshness_rejects_future_classification_instants(
        tmp_path, classification_as_of, captured_at, problem):
    snapshot = _snapshot(
        tmp_path,
        classification_as_of=classification_as_of,
        captured_at=captured_at,
    )

    with pytest.raises(bundle.ProductReleaseBlocked, match=problem):
        bundle._check_snapshot_freshness(snapshot, now=FROZEN)


def test_build_graph_passes_both_exact_clocks(tmp_path, monkeypatch):
    from agents.golden_press import evidence_pack_v2
    from tools import notice_store

    pressed = tmp_path / "pressed.json"
    pressed.write_text("{}\n")
    sweep = tmp_path / "data" / "cleaned" / "searches_acme.json"
    sweep.parent.mkdir(parents=True)
    sweep.write_text(
        '{"generated_at":"2026-08-24T17:00:00Z","results":{}}\n')
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
        tmp_path, "acme", "Acme", pressed,
        classification_as_of=FROZEN, captured_at=FROZEN)
    assert result[0] == expected_graph
    assert seen["args"] == ("acme", "Acme")
    assert seen["kwargs"]["classification_as_of"] == FROZEN
    assert seen["kwargs"]["captured_at"] == FROZEN
    assert seen["kwargs"]["research_sweep_path"] == sweep
    assert seen["kwargs"]["store_conn"] is connection
    assert connection.closed is True


def test_build_graph_fails_closed_when_governed_sweep_is_missing(
        tmp_path, monkeypatch):
    from tools import notice_store

    pressed = tmp_path / "pressed.json"
    pressed.write_text("{}\n")
    monkeypatch.setattr(notice_store, "connect", lambda: None)

    with pytest.raises(
            bundle.ProductReleaseError,
            match="governed research sweep is missing"):
        bundle._build_graph(
            tmp_path, "acme", "Acme", pressed,
            classification_as_of=FROZEN, captured_at=FROZEN)


def test_compiler_refuses_local_path_in_exact_evidence_bytes(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        evidence_extra={
            "scarcity_note": {
                "operator_source": "/Users/example/private/source.txt"},
        },
    )
    with pytest.raises(ReleaseCompilationError, match="evidence_pack.json"):
        compile_release_snapshot(snapshot)


def test_compiler_refuses_banned_vocabulary_in_any_payload(tmp_path):
    from agents.golden_press.market_map_validate import BANNED_VOCABULARY

    snapshot = _snapshot(
        tmp_path,
        evidence_extra={"unsafe_term": BANNED_VOCABULARY[0]},
    )
    with pytest.raises(ReleaseCompilationError, match="banned vocabulary"):
        compile_release_snapshot(snapshot)


def test_compiler_refuses_disallowed_dash_in_any_payload(tmp_path):
    snapshot = _snapshot(
        tmp_path, evidence_extra={"unsafe_punctuation": "\N{EM DASH}"})

    with pytest.raises(ReleaseCompilationError, match="disallowed dash"):
        compile_release_snapshot(snapshot)


def test_compiler_allows_web_url_with_home_path(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        evidence_extra={
            "scarcity_note": {
                "source_url": "https://agency.gov/home/opportunities"},
        },
    )
    assert compile_release_snapshot(snapshot).zip_bytes
