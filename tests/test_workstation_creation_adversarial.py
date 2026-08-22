"""Adversarial proofs for native workstation creation and ownership.

These tests are offline and temporary-root only. They pin receipt-first crash
recovery, cross-process registry serialization, exact-id coexistence, and the
read-only ownership proof used by later native mutation/runner contracts.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agents.workstations as workstations
from agents.workstations import (
    WorkstationError,
    WorkstationRef,
    create_workstation,
    discover_workstations,
    native_workstation_ownership,
)


class _HardCrash(BaseException):
    """Simulate process death after an atomic promotion."""


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _legacy_packet() -> dict:
    return {
        "client_name": "NETSCOUT",
        "status": "approved",
        "strategy": {
            "client_name": "NETSCOUT",
            "pursuit_strategy": "Protect mission networks.",
            "keywords": [
                {
                    "term": "541512",
                    "category": "naics",
                    "rationale": "Exact authored systems-integration rationale.",
                },
                {
                    "term": "packet visibility",
                    "category": "capability",
                    "rationale": "Exact authored capability rationale.",
                },
            ],
            "inferred_naics": ["541512", "334290", "541519"],
            "target_agencies": ["DHS"],
            "set_aside_angles": [],
            "searches": [],
            "near_misses": [{
                "value": "334290",
                "kind": "naics",
                "category": None,
                "reason": "Exact authored adjacent-equipment reason.",
                "confidence": 0.52,
            }],
            "kept_out": [],
            "naics_meta": [],
            "kept_out_naics": [],
            "confidence": 0.82,
            "sources_reviewed": ["https://www.netscout.com/"],
            "requires_human_review": True,
            "review_gate": "Approve the exact boundary.",
        },
        "reviewer_note": "Legacy All approval.",
        "created_at": "2026-07-12T20:00:00Z",
        "decided_at": "2026-07-12T21:00:00Z",
        "revised_at": None,
        "revision_count": 0,
        "search_scope": {"all": True},
    }


@pytest.fixture
def roots(tmp_path: Path):
    review = tmp_path / "review"
    cleaned = tmp_path / "cleaned"
    reports = tmp_path / "reports"
    for root in (review, cleaned, reports):
        root.mkdir()
    _write_json(review / "netscout.review.json", _legacy_packet())
    return review, cleaned, reports


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _create(review: Path, scope: dict, *, clone: bool = True):
    return create_workstation(
        "NETSCOUT", scope, review_dir=review,
        clone_baseline=clone, created_from="operator")


def _catalog(review: Path, cleaned: Path, reports: Path):
    return {
        row.ref.id: row
        for row in discover_workstations(
            "NETSCOUT", review_dir=review,
            cleaned_dir=cleaned, report_dir=reports).workstations
    }


@pytest.mark.parametrize("promotion", [
    "receipt", "packet", "markdown", "journal", "registry",
])
def test_creation_recovers_after_every_atomic_promotion(
        roots, monkeypatch, promotion):
    """A killed creator leaves a deterministic prefix the same request finishes."""
    review, _cleaned, _reports = roots
    from tools import artifacts, atomic_io

    if promotion == "registry":
        real_registry_write = artifacts.atomic_write_json

        def crash_after_registry(path, payload):
            real_registry_write(path, payload)
            raise _HardCrash()

        monkeypatch.setattr(artifacts, "atomic_write_json", crash_after_registry)
    else:
        target_index = {
            "receipt": 1, "packet": 2, "markdown": 3, "journal": 4,
        }[promotion]
        real_text_write = atomic_io.atomic_write_text
        calls = 0

        def crash_after_text(path, text):
            nonlocal calls
            real_text_write(path, text)
            calls += 1
            if calls == target_index:
                raise _HardCrash()

        monkeypatch.setattr(atomic_io, "atomic_write_text", crash_after_text)

    with pytest.raises(_HardCrash):
        _create(review, {"agencies": ["DHS"]})

    monkeypatch.undo()
    recovered = _create(review, {"agencies": ["DHS"]})
    assert recovered.ref.id == "agency_dhs"
    assert recovered.created is (promotion != "registry")
    assert recovered.cloned_baseline is True
    journal = (
        review / "netscout.agency_dhs.journal.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(journal) == 1
    assert json.loads(journal[0])["event"] == "workstation_created"
    assert len(workstations.load_registry(
        "NETSCOUT", review_dir=review)) == 1

    before_replay = _tree(review)
    replay = _create(review, {"agencies": ["DHS"]})
    assert replay.created is False
    assert _tree(review) == before_replay


def _concurrent_create_worker(
    review_dir: str, scope: dict, start, results,
) -> None:
    try:
        start.wait(10)
        result = create_workstation(
            "NETSCOUT", scope, review_dir=review_dir,
            clone_baseline=True, created_from="operator")
        results.put(("ok", result.ref.id))
    except BaseException as exc:  # child result must reach parent
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def test_cross_process_creators_do_not_lose_registry_rows(roots):
    """Two scopes racing from separate processes serialize per exact client."""
    review, _cleaned, _reports = roots
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_create_worker,
            args=(str(review), scope, start, results),
        )
        for scope in (
            {"agencies": ["DHS"]},
            {"agencies": ["GSA"]},
        )
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=2) for _ in processes]
    assert all(kind == "ok" for kind, _value in outcomes), outcomes
    assert {ref.id for ref in workstations.load_registry(
        "NETSCOUT", review_dir=review)} == {"agency_dhs", "agency_gsa"}


def test_no_clone_creation_is_receipted_journaled_and_byte_idempotent(roots):
    review, cleaned, reports = roots
    created = _create(review, {"agencies": ["DHS"]}, clone=False)
    assert created.created is True
    assert created.cloned_baseline is False
    receipt_path = (
        review / "netscout.agency_dhs.workstation_receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["clone_baseline"] is False
    assert receipt["native_packet_sha256"] is None
    assert not (review / "netscout.agency_dhs.review.json").exists()
    assert not (review / "netscout.agency_dhs.review.md").exists()
    event = json.loads((
        review / "netscout.agency_dhs.journal.jsonl"
    ).read_text(encoding="utf-8"))
    assert event["clone_baseline"] is False
    assert _catalog(review, cleaned, reports)["agency_dhs"].phase == "dormant"

    ownership = native_workstation_ownership(
        "NETSCOUT", "agency_dhs", review_dir=review)
    assert ownership.clone_baseline is False
    before = _tree(review)
    replay = _create(review, {"agencies": ["DHS"]}, clone=False)
    assert replay.created is False
    assert replay.cloned_baseline is False
    assert _tree(review) == before


def test_alternate_native_dhs_coexists_with_legacy_all_and_damage_is_local(roots):
    review, cleaned, reports = roots
    _create(review, {"agencies": ["DHS"]})
    rows = _catalog(review, cleaned, reports)
    assert rows["agency_dhs"].is_native is True
    assert rows["agency_dhs"].phase == "configure"
    assert rows["all"].is_legacy_current is True
    assert rows["all"].is_native is False

    (review / "netscout.agency_dhs.workstation_receipt.json").write_text(
        "not json", encoding="utf-8")
    damaged = _catalog(review, cleaned, reports)
    assert damaged["agency_dhs"].is_native is False
    assert damaged["agency_dhs"].phase == "dormant"
    assert damaged["agency_dhs"].diagnostics
    assert damaged["all"].is_legacy_current is True


@pytest.mark.parametrize("identity_registered", [False, True])
def test_legacy_current_same_id_creation_is_byte_inert_duplicate(
        roots, identity_registered):
    """Tranche 3 cannot turn the existing All identity into a native cutover."""
    review, cleaned, reports = roots
    if identity_registered:
        _write_json(review / "netscout.workstations.json", {
            "schema_version": "1",
            "client_name": "NETSCOUT",
            "workstations": [{
                "id": "all",
                "scope": {"all": True},
                "created_at": "2026-07-12T23:00:00Z",
                "created_from": "operator",
                "archived_at": None,
            }],
        })
    before = _tree(review)

    duplicate = _create(review, {"all": True})

    assert duplicate.created is False
    assert duplicate.cloned_baseline is False
    assert duplicate.ref.id == "all"
    assert _tree(review) == before
    assert not (review / "netscout.all.review.json").exists()
    assert not (
        review / "netscout.all.workstation_receipt.json").exists()
    row = _catalog(review, cleaned, reports)["all"]
    assert row.is_legacy_current is True
    assert row.is_native is False
    assert row.phase == "search"


def _prepared_all_creation(review: Path):
    """Build exact old/native bytes without calling the guarded creation door."""
    from agents.review import ReviewPacket

    source_path = review / "netscout.review.json"
    source_bytes = source_path.read_bytes()
    source_packet = ReviewPacket.model_validate_json(source_bytes)
    ref = WorkstationRef(
        id="all",
        scope={"all": True},
        label="All Federal",
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        created_from="operator",
    )
    packet_target = review / "netscout.all.review.json"
    _receipt, prepared = workstations._build_creation_files(
        "NETSCOUT",
        ref,
        source_packet=source_packet,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        packet_target=packet_target,
        clone_baseline=True,
    )
    return ref, prepared


@pytest.mark.parametrize("orphan_kind", [
    "receipt", "packet", "markdown", "journal",
])
def test_legacy_current_same_id_never_adopts_orphan_native_prefix(
        roots, orphan_kind):
    review, _cleaned, _reports = roots
    _ref, prepared = _prepared_all_creation(review)
    suffix = {
        "receipt": ".workstation_receipt.json",
        "packet": ".review.json",
        "markdown": ".review.md",
        "journal": ".journal.jsonl",
    }[orphan_kind]
    target = review / f"netscout.all{suffix}"
    target.write_text(prepared[target], encoding="utf-8")
    before = _tree(review)

    with pytest.raises(WorkstationError, match="migration|required"):
        _create(review, {"all": True})

    assert _tree(review) == before
    assert not (review / "netscout.workstations.json").exists()


def test_completed_legacy_id_native_owner_remains_replayable(roots):
    """Do not reinterpret an already-complete historical native transaction."""
    review, cleaned, reports = roots
    ref, prepared = _prepared_all_creation(review)
    for path, text in prepared.items():
        path.write_text(text, encoding="utf-8")
    _write_json(
        review / "netscout.workstations.json",
        workstations._registry_payload("NETSCOUT", (ref,)),
    )
    before = _tree(review)

    replay = _create(review, {"all": True})

    assert replay.created is False
    assert replay.cloned_baseline is True
    assert replay.ref == ref
    assert _tree(review) == before
    row = _catalog(review, cleaned, reports)["all"]
    assert row.is_native is True
    assert row.is_legacy_current is False


@pytest.mark.parametrize("mutation,diagnostic", [
    (lambda packet: packet.update(
        {"search_scope": {"agencies": ["DHS", "DHS"]}}), "scope"),
    (lambda packet: packet["strategy"].update(
        {"client_name": "OTHER"}), "strategy identity"),
])
def test_native_packet_requires_canonical_scope_and_inner_identity(
        roots, mutation, diagnostic):
    review, cleaned, reports = roots
    _create(review, {"agencies": ["DHS"]})
    packet_path = review / "netscout.agency_dhs.review.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    mutation(packet)
    _write_json(packet_path, packet)

    rows = _catalog(review, cleaned, reports)
    assert rows["agency_dhs"].is_native is False
    assert rows["agency_dhs"].phase == "dormant"
    assert diagnostic in " ".join(rows["agency_dhs"].diagnostics)
    assert rows["all"].is_legacy_current is True


def test_ownership_snapshot_reads_only_registry_and_receipt_once(roots, monkeypatch):
    review, _cleaned, _reports = roots
    _create(review, {"agencies": ["DHS"]})
    registry_path = review / "netscout.workstations.json"
    receipt_path = review / "netscout.agency_dhs.workstation_receipt.json"
    expected_registry = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    expected_receipt = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    (review / "netscout.agency_dhs.review.json").unlink()

    original = Path.read_bytes
    reads: list[Path] = []

    def counted(path: Path) -> bytes:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    proof = native_workstation_ownership(
        "NETSCOUT", "agency_dhs", review_dir=review)
    assert reads == [registry_path, receipt_path]
    assert proof.registry_sha256 == expected_registry
    assert proof.receipt_sha256 == expected_receipt
    assert proof.ref.id == "agency_dhs"


def test_clone_hydrates_naics_only_from_affirmative_authored_evidence(roots):
    review, _cleaned, _reports = roots
    baseline_path = review / "netscout.review.json"
    baseline_bytes = baseline_path.read_bytes()
    _create(review, {"agencies": ["DHS"]})
    native = json.loads((
        review / "netscout.agency_dhs.review.json"
    ).read_text(encoding="utf-8"))

    meta = native["strategy"]["naics_meta"]
    assert [row["code"] for row in meta] == ["541512", "334290", "541519"]
    assert [row["rationale"] for row in meta] == [
        "Exact authored systems-integration rationale.",
        "",
        "",
    ]
    assert all(row["origin"] == "system" for row in meta)
    assert all(row["role"] == "boundary" for row in meta)
    assert all(row["title"] == "" and row["note"] == "" for row in meta)
    assert native["strategy"]["near_misses"][0]["reason"] == (
        "Exact authored adjacent-equipment reason.")
    assert baseline_path.read_bytes() == baseline_bytes


def test_clone_appends_missing_naics_metadata_without_rewriting_existing(roots):
    review, _cleaned, _reports = roots
    baseline_path = review / "netscout.review.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    existing = [
        {
            "code": "334290",
            "title": "Existing equipment title",
            "role": "core",
            "origin": "edited",
            "rationale": "Existing affirmative operator rationale.",
            "note": "Preserve this exact authored record.",
        },
        {
            "code": "999999",
            "title": "Existing out-of-boundary history",
            "role": "boundary",
            "origin": "consultant",
            "rationale": "Existing historical rationale.",
            "note": "Do not prune or reorder this record during cloning.",
        },
    ]
    baseline["strategy"]["naics_meta"] = existing
    _write_json(baseline_path, baseline)
    baseline_bytes = baseline_path.read_bytes()

    _create(review, {"agencies": ["DHS"]})

    native = json.loads((
        review / "netscout.agency_dhs.review.json"
    ).read_text(encoding="utf-8"))
    meta = native["strategy"]["naics_meta"]
    assert meta[:2] == existing
    assert [entry["code"] for entry in meta] == [
        "334290", "999999", "541512", "541519",
    ]
    assert meta[2]["rationale"] == (
        "Exact authored systems-integration rationale.")
    assert meta[3]["rationale"] == ""
    assert native["strategy"]["near_misses"][0]["reason"] == (
        "Exact authored adjacent-equipment reason.")
    assert baseline_path.read_bytes() == baseline_bytes


@pytest.mark.parametrize("sidecar", ["review.md", "journal.jsonl"])
def test_completed_creation_replay_requires_creation_sidecars(roots, sidecar):
    review, _cleaned, _reports = roots
    _create(review, {"agencies": ["DHS"]})
    target = review / f"netscout.agency_dhs.{sidecar}"
    target.unlink()
    before = _tree(review)

    with pytest.raises(WorkstationError, match="missing"):
        _create(review, {"agencies": ["DHS"]})

    assert _tree(review) == before


def test_ownership_rejects_archived_or_corrupt_binding(roots):
    review, _cleaned, _reports = roots
    _create(review, {"agencies": ["DHS"]})
    receipt_path = review / "netscout.agency_dhs.workstation_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["scope"] = {"all": True}
    _write_json(receipt_path, receipt)
    with pytest.raises(WorkstationError, match="binding|scope|receipt"):
        native_workstation_ownership(
            "NETSCOUT", "agency_dhs", review_dir=review)
