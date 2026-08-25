"""Real JTG replay proof for the non-champion reproducibility fixture."""

from __future__ import annotations

import builtins
import hashlib
import inspect
import json
import re
import socket
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from agents.golden_press import release_compiler
from agents.golden_press.external_product_contract import (
    CONTRACT_VERSION,
    OPERATOR_LOCKED_SLOT_SHA256,
)
from agents.golden_press.release_compiler import compile_release_snapshot
from agents.golden_press.market_map_validate import BANNED_VOCABULARY
from agents.golden_press.release_snapshot import (
    REPRODUCIBILITY_FIXTURE_PURPOSE,
    ReleaseSnapshot,
    canonical_json_bytes,
)
from tools.build_release_snapshot_fixture import _replace_vocabulary
from tools.intelligence_graph.cache import stable_hash


FIXTURE = (
    Path(__file__).parent / "fixtures" / "release_snapshot" /
    "jtg_reproducibility_snapshot.json")
_LOCAL_PATH = re.compile(
    rb"(?:file://|(?:^|[\"'\s(=:])/(?:users|home)/)", re.I | re.M)
_SOURCE_EVIDENCE_SHA256 = (
    "714426566bb115d2cd7f4e4c7a317f315ad45e092c2c85ecbb0dcc4ed5eeab58")
_CAPTURE_RECEIPT_SHA256 = (
    "c6971d9d2ecded25f1baa3f39503d690981b9fad557807e7270b5e4da16ce4cb")
_FIXTURE_CLASSIFIER_VERSION = "evidence-route-v7-utc-window-truth"


def _write_compilation(root: Path, compiled) -> None:
    root.mkdir(parents=True)
    for name, payload in compiled.payloads.items():
        (root / name).write_bytes(payload)
    (root / "manifest.json").write_bytes(compiled.manifest_bytes)
    (root / compiled.bundle_name).write_bytes(compiled.zip_bytes)


def test_real_jtg_snapshot_double_replay_is_byte_identical_and_non_promotable(
        tmp_path, monkeypatch):
    fixture_payload = json.loads(FIXTURE.read_text())
    snapshot = ReleaseSnapshot.from_dict(fixture_payload)
    replay_snapshot = ReleaseSnapshot.from_dict(fixture_payload)
    assert snapshot.purpose == REPRODUCIBILITY_FIXTURE_PURPOSE
    assert snapshot.authorization["authorized"] is False
    assert "not approved" in snapshot.authorization["fixture_status"]
    assert snapshot.contract_version == CONTRACT_VERSION
    assert snapshot.operator_locked_slot_sha256 == OPERATOR_LOCKED_SLOT_SHA256
    assert snapshot.source_evidence_pack_sha256 == _SOURCE_EVIDENCE_SHA256
    assert hashlib.sha256(snapshot.source_evidence_pack_bytes).hexdigest() == (
        _SOURCE_EVIDENCE_SHA256)
    assert snapshot.versions["fixture_source_evidence_sha256"] == (
        _SOURCE_EVIDENCE_SHA256)
    assert snapshot.versions["fixture_projection_vocabulary_normalization"] == (
        "contract-L7.v1")
    assert snapshot.versions["fixture_capture_receipt_sha256"] == (
        _CAPTURE_RECEIPT_SHA256)
    assert snapshot.versions["fixture_capture_clock_source"] == (
        "captured_graph.generated_at")

    from agents.golden_press import (
        external_product_contract,
        external_product_projection,
        external_product_render,
        market_map_projection,
        market_map_skeleton,
        product_bundle,
        rival_footprint,
    )
    from agents.reports import report_assets
    from tools import notice_store
    from tools.intelligence_graph import adapter

    def forbidden(*_args, **_kwargs):
        raise AssertionError("real replay invoked a live reader")

    for name in (
        "capture_inputs", "_pending_terms", "_coverage_contract",
        "_load_record_rulings", "_load_evidence_pack_v2",
    ):
        monkeypatch.setattr(market_map_projection, name, forbidden)
    monkeypatch.setattr(
        external_product_contract, "load_external_product_slots", forbidden)
    monkeypatch.setattr(
        external_product_projection, "load_external_product_slots", forbidden)
    monkeypatch.setattr(
        external_product_render, "load_external_product_slots", forbidden)
    monkeypatch.setattr(market_map_skeleton, "load_css", forbidden)
    monkeypatch.setattr(market_map_skeleton, "load_runtime", forbidden)
    monkeypatch.setattr(report_assets, "client_logo", forbidden)
    monkeypatch.setattr(report_assets, "gtm_logo", forbidden)
    monkeypatch.setattr(rival_footprint, "load_cached", forbidden)
    monkeypatch.setattr(adapter, "load_target_observations", forbidden)
    monkeypatch.setattr(notice_store, "connect", forbidden)
    monkeypatch.setattr(product_bundle, "_load_profile", forbidden)
    monkeypatch.setattr(product_bundle, "_load_pack", forbidden)
    monkeypatch.setattr(product_bundle, "_utc_now", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    with monkeypatch.context() as denied:
        denied.setattr(Path, "read_text", forbidden)
        denied.setattr(Path, "read_bytes", forbidden)
        denied.setattr(Path, "exists", forbidden)
        denied.setattr(builtins, "open", forbidden)
        denied.setattr(time, "time", lambda: 1_776_000_000.0)
        first = compile_release_snapshot(snapshot)
        denied.setattr(time, "time", lambda: 1_776_086_400.0)
        second = compile_release_snapshot(replay_snapshot)
    _write_compilation(tmp_path / "first", first)
    _write_compilation(tmp_path / "second", second)

    assert first.payloads == second.payloads
    assert first.manifest_bytes == second.manifest_bytes
    assert first.zip_bytes == second.zip_bytes
    assert first.content_digest == second.content_digest
    assert first.manifest["release_eligible"] is False
    assert first.manifest["release_state"] == "fixture"
    assert first.manifest["purpose"] == REPRODUCIBILITY_FIXTURE_PURPOSE
    assert b"reproducibility fixture" in first.payloads["README.txt"].lower()
    assert b'data-reproducibility-fixture="1"' in first.payloads[
        first.primary_html]
    assert b'data-reproducibility-fixture="1"' in first.payloads[
        first.studio_html]
    cache_receipt = snapshot.graph["incremental_cache_receipt"]
    assert cache_receipt["semantic_dependencies"]["rule_version"] == (
        _FIXTURE_CLASSIFIER_VERSION)
    assert cache_receipt["semantic_dependencies"]["as_of"] == (
        snapshot.classification_as_of)
    assert cache_receipt["context_hash"] == stable_hash(
        snapshot.graph["classification_context"])

    graph_count = len(snapshot.graph.get("records") or [])
    source_evidence = json.loads(snapshot.source_evidence_pack_bytes)
    projection_evidence = json.loads(snapshot.evidence_pack_bytes)
    assert snapshot.evidence_pack_bytes == canonical_json_bytes(
        _replace_vocabulary(source_evidence))
    source_records = source_evidence.get("records") or []
    assert source_evidence["generated_at"] == snapshot.classification_as_of
    source_record_ids = [
        str(row.get("record_id") or "") for row in source_records]
    repeated_source_rows = sum(
        count - 1 for record_id, count in Counter(source_record_ids).items()
        if record_id and count > 1)
    identity_receipt = snapshot.graph["record_identity_receipt"]
    assert len(projection_evidence.get("records") or []) == len(source_records)
    assert identity_receipt["input_records_raw"] == len(source_records)
    assert identity_receipt["canonical_records"] == graph_count
    assert identity_receipt["record_duplicates_collapsed"] == (
        repeated_source_rows)
    assert graph_count == len(source_records) - repeated_source_rows
    current_count = sum(
        row.get("evidence_class") == "current_opportunity"
        for row in snapshot.graph.get("records") or [])
    pursuit_count = len(
        snapshot.graph.get("qualified_opportunity_records") or [])
    fit_review_count = len(snapshot.graph.get("held_opportunities") or [])
    product = json.loads(first.payloads["product.json"])
    slot_five = next(
        row for row in product["slots"]
        if row["slot_id"] == "federal-opportunities")
    assert product["source_receipt"]["graph_final_records"] == graph_count
    assert len(product["graph_receipt"]["current_opportunity_ids"]) == (
        current_count)
    assert len(slot_five["records"]) == pursuit_count
    assert len(slot_five["review_records"]) == fit_review_count

    validation = json.loads(first.payloads["validation.json"])
    assert validation["ok"] is True
    assert validation["violations"] == []
    card_receipt = validation["opportunity_card_contract"]
    assert card_receipt["agency_seal_policy"] == "recorded_only"
    assert card_receipt["rendered_agency_seals"] == 0
    assert card_receipt["cards_without_agency_seal"] == (
        len(slot_five["records"]) + len(slot_five["review_records"]))
    assert card_receipt["rendered_official_source_actions"] == (
        card_receipt["expected_cards"] * 2)
    assert validation["classification_as_of"] == snapshot.classification_as_of
    assert snapshot.classification_as_of in first.payloads[
        first.primary_html].decode("utf-8")
    assert snapshot.classification_as_of in first.payloads[
        first.studio_html].decode("utf-8")
    assert not [
        name for name, payload in first.payloads.items()
        if _LOCAL_PATH.search(payload)
    ]
    assert not _LOCAL_PATH.search(first.manifest_bytes)
    for payload in [*first.payloads.values(), first.manifest_bytes]:
        text = payload.decode("utf-8", errors="replace").casefold()
        assert not any(re.search(
            r"(?<![a-z0-9])" + re.escape(term.casefold())
            + r"(?:s|es|'s)?(?![a-z0-9])", text)
            for term in BANNED_VOCABULARY)

    with zipfile.ZipFile(tmp_path / "first" / first.bundle_name) as archive:
        assert archive.namelist() == sorted(
            set(first.payloads) | {"manifest.json"})
        assert all(not _LOCAL_PATH.search(archive.read(info))
                   for info in archive.infolist())

    compiler_source = inspect.getsource(release_compiler)
    assert "datetime.now" not in compiler_source
    assert "date.today" not in compiler_source
    assert "Path(" not in compiler_source
