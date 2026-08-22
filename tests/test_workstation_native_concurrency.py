"""Adversarial CAS and child-authorization contracts for native workstations.

These tests are intentionally offline and mutation-focused.  A native packet
is a shared operator-owned strategy surface: browser writes must compare the
exact packet bytes the operator reviewed, and a launched child must retain the
exact registry, creation-receipt, packet, revision, and native-mode authority
captured by the server.  Stale actors lose; they never append even a journal
row and never reach a metered or profile-backed child stage.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agents.review as review
import agents.workstations as workstations
from agents.decisions.schemas import IntakeStrategy
from ui import server


CLIENT = "NETSCOUT"
SLUG = "netscout"
WORKSTATION_ID = "agency_dhs"
DHS_SCOPE = {"agencies": ["DHS"]}
ALL_SCOPE = {"all": True}


@dataclass(frozen=True)
class NativeRuntime:
    root: Path
    review_dir: Path
    native_packet: Path
    native_markdown: Path
    native_journal: Path
    registry: Path
    receipt: Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _strategy(marker: str = "baseline") -> IntakeStrategy:
    return IntakeStrategy(
        client_name=CLIENT,
        pursuit_strategy=f"Protect the {marker} federal boundary.",
        keywords=[{
            "term": f"{marker} packet observability",
            "category": "capability",
            "rationale": "Operator-reviewed capability term.",
        }],
        inferred_naics=["541512"],
        confidence=0.9,
        review_gate="Approve the exact workstation boundary.",
    )


@pytest.fixture
def native_runtime(tmp_path: Path, monkeypatch) -> NativeRuntime:
    review_dir = tmp_path / "review"
    cleaned_dir = tmp_path / "cleaned"
    report_dir = tmp_path / "reports"
    intake_dir = tmp_path / "intake"
    clients_dir = tmp_path / "clients"
    marks_dir = tmp_path / "marks"
    for path in (
            review_dir, cleaned_dir, report_dir, intake_dir, clients_dir,
            marks_dir):
        path.mkdir()

    legacy = review.ReviewPacket(
        client_name=CLIENT,
        status=review.ReviewStatus.APPROVED,
        strategy=_strategy(),
        reviewer_note="Approved historical All boundary.",
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        decided_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc),
        revision_count=4,
        search_scope=ALL_SCOPE,
    )
    (review_dir / "netscout.review.json").write_text(
        legacy.model_dump_json(indent=2), encoding="utf-8")
    (review_dir / "netscout.review.md").write_text(
        review.render_markdown(legacy.strategy), encoding="utf-8")

    created = workstations.create_workstation(
        CLIENT,
        DHS_SCOPE,
        review_dir=review_dir,
        clone_baseline=True,
        created_from="operator",
    )
    assert created.created is True
    assert created.ref.id == WORKSTATION_ID

    monkeypatch.setattr(review, "REVIEW_DIR", str(review_dir))
    monkeypatch.setattr(server, "REVIEW_DIR", str(review_dir))
    monkeypatch.setattr(server, "CLEANED_DIR", str(cleaned_dir))
    monkeypatch.setattr(server, "REPORT_DIR", str(report_dir))
    monkeypatch.setattr(server, "INTAKE_DIR", str(intake_dir))
    monkeypatch.setattr(server, "CLIENTS_DIR", str(clients_dir))
    monkeypatch.setenv("LILA_CLIENT_MARKS_DIR", str(marks_dir))

    return NativeRuntime(
        root=tmp_path,
        review_dir=review_dir,
        native_packet=review_dir / "netscout.agency_dhs.review.json",
        native_markdown=review_dir / "netscout.agency_dhs.review.md",
        native_journal=review_dir / "netscout.agency_dhs.journal.jsonl",
        registry=review_dir / "netscout.workstations.json",
        receipt=(review_dir /
                 "netscout.agency_dhs.workstation_receipt.json"),
    )


def _family_bytes(runtime: NativeRuntime) -> tuple[bytes, bytes, bytes]:
    return (
        runtime.native_packet.read_bytes(),
        runtime.native_markdown.read_bytes(),
        runtime.native_journal.read_bytes(),
    )


def _detail(client) -> dict:
    response = client.get(
        f"/api/client/{SLUG}/workstation/{WORKSTATION_ID}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _mutation_request(operation: str, marker: str) -> tuple[str, dict]:
    common = {
        "client_name": CLIENT,
        "workstation_id": WORKSTATION_ID,
    }
    if operation == "revise":
        return "/api/strategy/revise", {
            **common,
            "updates": {
                "pursuit_strategy": f"{marker} DHS pursuit strategy",
            },
        }
    if operation == "terms":
        return "/api/strategy/terms", {
            **common,
            "keywords": [{
                "term": f"{marker} continuous diagnostics",
                "category": "capability",
                "rationale": "Exact DHS capability boundary.",
            }],
        }
    if operation == "decide":
        return "/api/decide", {
            **common,
            "approve": marker == "first",
            "note": f"{marker} exact DHS decision",
        }
    raise AssertionError(f"unknown mutation operation: {operation}")


def _approve_native(runtime: NativeRuntime) -> review.PacketSnapshot:
    snapshot = review.load_packet_snapshot(
        CLIENT,
        workstation_id=WORKSTATION_ID,
        review_dir=str(runtime.review_dir),
        require_native_owner=True,
    )
    review.decide(
        CLIENT,
        True,
        note="Approved exact DHS search boundary.",
        workstation_id=WORKSTATION_ID,
        review_dir=str(runtime.review_dir),
        expected_sha256=snapshot.sha256,
    )
    return review.load_packet_snapshot(
        CLIENT,
        workstation_id=WORKSTATION_ID,
        review_dir=str(runtime.review_dir),
        require_native_owner=True,
    )


def test_native_detail_exposes_sha_of_the_exact_packet_bytes(
        native_runtime):
    client = server.app.test_client()
    before = _tree(native_runtime.root)

    body = _detail(client)

    assert body["workstation"]["ref"]["id"] == WORKSTATION_ID
    assert body["workstation"]["is_native"] is True
    assert body["strategy_packet_sha256"] == _sha(
        native_runtime.native_packet)
    assert _tree(native_runtime.root) == before


def test_native_detail_revalidates_inner_strategy_after_catalog(
        native_runtime, monkeypatch):
    original = server._workstation_route_error

    def replace_after_catalog(slug):
        resolved, error = original(slug)
        packet = json.loads(native_runtime.native_packet.read_text())
        packet["strategy"]["client_name"] = "ANOTHER CLIENT"
        native_runtime.native_packet.write_text(
            json.dumps(packet, indent=2), encoding="utf-8")
        return resolved, error

    monkeypatch.setattr(
        server, "_workstation_route_error", replace_after_catalog)
    response = server.app.test_client().get(
        f"/api/client/{SLUG}/workstation/{WORKSTATION_ID}")

    assert response.status_code == 409, response.get_json()
    assert "strategy identity changed" in response.get_data(
        as_text=True).lower()


@pytest.mark.parametrize("operation", ["revise", "terms", "decide"])
def test_native_mutations_require_fresh_sha_and_stale_writes_are_byte_inert(
        native_runtime, operation):
    client = server.app.test_client()
    endpoint, first = _mutation_request(operation, "first")
    initial_sha = _detail(client)["strategy_packet_sha256"]
    assert initial_sha == _sha(native_runtime.native_packet)

    # Native callers never receive a compatibility escape hatch: omission is
    # rejected before packet, rendered Markdown, or judgment journal changes.
    before_missing = _family_bytes(native_runtime)
    missing = client.post(endpoint, json=first)
    assert missing.status_code == 409, missing.get_json()
    assert "expected_packet_sha256" in missing.get_data(as_text=True)
    assert _family_bytes(native_runtime) == before_missing

    accepted = client.post(endpoint, json={
        **first,
        "expected_packet_sha256": initial_sha,
    })
    assert accepted.status_code == 200, accepted.get_json()
    accepted_sha = accepted.get_json()["packet_sha256"]
    assert accepted_sha == _sha(native_runtime.native_packet)
    assert accepted_sha != initial_sha

    # A second browser still holding the first detail response cannot overwrite
    # the accepted writer or leave a misleading failed-attempt journal row.
    endpoint, stale_body = _mutation_request(operation, "second")
    before_stale = _family_bytes(native_runtime)
    stale = client.post(endpoint, json={
        **stale_body,
        "expected_packet_sha256": initial_sha,
    })
    assert stale.status_code == 409, stale.get_json()
    assert "changed" in stale.get_data(as_text=True).lower()
    assert _family_bytes(native_runtime) == before_stale


def test_two_concurrent_native_writers_cannot_last_write_win(native_runtime):
    initial_sha = _sha(native_runtime.native_packet)
    barrier = threading.Barrier(3)

    def write(marker: str) -> tuple[int, dict]:
        with server.app.test_client() as client:
            barrier.wait(timeout=5)
            response = client.post("/api/strategy/revise", json={
                "client_name": CLIENT,
                "workstation_id": WORKSTATION_ID,
                "expected_packet_sha256": initial_sha,
                "updates": {
                    "pursuit_strategy": f"{marker} concurrent DHS writer",
                },
            })
            return response.status_code, response.get_json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, marker)
                   for marker in ("alpha", "bravo")]
        barrier.wait(timeout=5)
        results = [future.result(timeout=10) for future in futures]

    assert sorted(status for status, _body in results) == [200, 409], results
    packet = review.load_packet(
        CLIENT,
        workstation_id=WORKSTATION_ID,
        review_dir=str(native_runtime.review_dir),
    )
    winners = {
        "alpha concurrent DHS writer", "bravo concurrent DHS writer",
    }
    assert packet.strategy.pursuit_strategy in winners
    assert packet.revision_count == 1

    journal = [
        json.loads(line)
        for line in native_runtime.native_journal.read_text(
            encoding="utf-8").splitlines()
    ]
    assert sum(row.get("event") == "gate_revision" for row in journal) == 1
    markdown = native_runtime.native_markdown.read_text(encoding="utf-8")
    assert packet.strategy.pursuit_strategy in markdown
    loser = next(value for value in winners
                 if value != packet.strategy.pursuit_strategy)
    assert loser not in markdown


def test_api_run_rejects_client_binding_fields_and_captures_server_tokens(
        native_runtime, monkeypatch):
    snapshot = _approve_native(native_runtime)
    ownership = workstations.native_workstation_ownership(
        CLIENT,
        WORKSTATION_ID,
        review_dir=native_runtime.review_dir,
    )
    launched: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        server,
        "start_job",
        lambda step, client, args: (
            launched.append((step, client, dict(args))) or "job-native-cas"),
    )
    client = server.app.test_client()

    for key, value in (
        ("_lila_expected_packet_sha256", "0" * 64),
        ("_lila_expected_strategy_revision", 999),
        ("_lila_native_workstation", False),
        ("_lila_expected_workstation_registry_sha256", "0" * 64),
        ("_lila_expected_workstation_receipt_sha256", "0" * 64),
    ):
        response = client.post("/api/run", json={
            "client_name": CLIENT,
            "step": "searches",
            "args": {"workstation_id": WORKSTATION_ID, key: value},
        })
        assert response.status_code == 400, (key, response.get_json())
        assert launched == []

    response = client.post("/api/run", json={
        "client_name": CLIENT,
        "step": "searches",
        "args": {"workstation_id": WORKSTATION_ID},
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["job_id"] == "job-native-cas"
    assert len(launched) == 1
    step, launched_client, args = launched[0]
    assert (step, launched_client) == ("searches", CLIENT)
    assert args["workstation_id"] == WORKSTATION_ID
    assert args["_lila_expected_packet_sha256"] == snapshot.sha256
    assert args["_lila_expected_strategy_revision"] == snapshot.revision
    assert args["_lila_native_workstation"] is True
    assert args["_lila_expected_workstation_registry_sha256"] == (
        ownership.registry_sha256)
    assert args["_lila_expected_workstation_receipt_sha256"] == (
        ownership.receipt_sha256)


@pytest.mark.parametrize(
    "drift_path, expected_fragment",
    [("registry", "registry"), ("receipt", "receipt")],
)
def test_child_ownership_drift_aborts_before_profile_or_source_fanout(
        native_runtime, monkeypatch, drift_path, expected_fragment):
    snapshot = _approve_native(native_runtime)
    ownership = workstations.native_workstation_ownership(
        CLIENT,
        WORKSTATION_ID,
        review_dir=native_runtime.review_dir,
    )
    # The public packet snapshot is the single server/child handoff envelope.
    assert snapshot.registry_sha256 == ownership.registry_sha256
    assert snapshot.receipt_sha256 == ownership.receipt_sha256

    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    monkeypatch.setenv("LILA_EXPECT_NATIVE_WORKSTATION", "1")
    monkeypatch.setenv("LILA_EXPECT_PACKET_SHA256", snapshot.sha256)
    monkeypatch.setenv("LILA_EXPECT_STRATEGY_REVISION", str(snapshot.revision))
    monkeypatch.setenv(
        "LILA_EXPECT_WORKSTATION_REGISTRY_SHA256",
        ownership.registry_sha256,
    )
    monkeypatch.setenv(
        "LILA_EXPECT_WORKSTATION_RECEIPT_SHA256",
        ownership.receipt_sha256,
    )

    changed = getattr(native_runtime, drift_path)
    changed.write_bytes(changed.read_bytes() + b"\n")

    calls = {"profile": 0, "fanout": 0}
    import tools.capability as capability

    def forbidden_profile(_client):
        calls["profile"] += 1
        raise AssertionError("profile read crossed stale child authorization")

    class ForbiddenExecutor:
        def __init__(self, *_args, **_kwargs):
            calls["fanout"] += 1
            raise AssertionError("source fanout crossed stale child authorization")

    monkeypatch.setattr(capability, "require_profile", forbidden_profile)
    monkeypatch.setattr(
        concurrent.futures, "ThreadPoolExecutor", ForbiddenExecutor)
    monkeypatch.setattr(
        sys, "argv", ["run_searches.py", "--client", CLIENT])

    import run_searches

    with pytest.raises(
            review.WorkstationBindingError,
            match=f"{expected_fragment}|authorization|changed|workstation"):
        run_searches.main()
    assert calls == {"profile": 0, "fanout": 0}


@pytest.mark.parametrize("owner_file", ["registry", "receipt"])
def test_native_mutation_rechecks_owner_generation_before_commit(
        native_runtime, monkeypatch, owner_file):
    """A route-captured owner generation is CAS authority, not decoration."""
    client = server.app.test_client()
    initial_sha = _detail(client)["strategy_packet_sha256"]
    before_family = _family_bytes(native_runtime)
    original_revise = review.revise
    drift_path = getattr(native_runtime, owner_file)

    def drift_then_revise(*args, **kwargs):
        # Preserve valid JSON while changing the exact generation captured by
        # the route.  The writer must notice before packet/journal persistence.
        drift_path.write_bytes(drift_path.read_bytes() + b"\n")
        return original_revise(*args, **kwargs)

    monkeypatch.setattr(review, "revise", drift_then_revise)
    response = client.post("/api/strategy/revise", json={
        "client_name": CLIENT,
        "workstation_id": WORKSTATION_ID,
        "expected_packet_sha256": initial_sha,
        "updates": {"pursuit_strategy": "stale owner generation"},
    })

    assert response.status_code == 409, response.get_json()
    assert "ownership changed" in response.get_data(as_text=True).lower()
    assert _family_bytes(native_runtime) == before_family


def test_native_mutation_omission_describes_coexistence_not_cutover(
        native_runtime):
    response = server.app.test_client().post("/api/strategy/revise", json={
        "client_name": CLIENT,
        "updates": {"pursuit_strategy": "ambiguous owner"},
    })

    assert response.status_code == 409, response.get_json()
    assert response.get_json()["error"] == (
        "workstation_id is required once a native workstation exists")


def test_low_level_native_job_requires_packet_and_revision_binding(
        native_runtime):
    ownership = workstations.native_workstation_ownership(
        CLIENT, WORKSTATION_ID, review_dir=native_runtime.review_dir)

    with pytest.raises(ValueError, match="authoritative packet"):
        server.start_job("searches", CLIENT, {
            "workstation_id": WORKSTATION_ID,
            "_lila_native_workstation": True,
            "_lila_expected_workstation_registry_sha256": (
                ownership.registry_sha256),
            "_lila_expected_workstation_receipt_sha256": (
                ownership.receipt_sha256),
        })


def test_low_level_native_job_requires_exact_workstation_id(native_runtime):
    ownership = workstations.native_workstation_ownership(
        CLIENT, WORKSTATION_ID, review_dir=native_runtime.review_dir)

    with pytest.raises(ValueError, match="workstation id"):
        server.start_job("searches", CLIENT, {
            "_lila_native_workstation": True,
            "_lila_expected_packet_sha256": "a" * 64,
            "_lila_expected_strategy_revision": 0,
            "_lila_expected_workstation_registry_sha256": (
                ownership.registry_sha256),
            "_lila_expected_workstation_receipt_sha256": (
                ownership.receipt_sha256),
        })


def test_low_level_native_job_rejects_downstream_steps(native_runtime):
    ownership = workstations.native_workstation_ownership(
        CLIENT, WORKSTATION_ID, review_dir=native_runtime.review_dir)

    with pytest.raises(ValueError, match="only searches"):
        server.start_job("report", CLIENT, {
            "workstation_id": WORKSTATION_ID,
            "_lila_native_workstation": True,
            "_lila_expected_packet_sha256": "a" * 64,
            "_lila_expected_strategy_revision": 0,
            "_lila_expected_workstation_registry_sha256": (
                ownership.registry_sha256),
            "_lila_expected_workstation_receipt_sha256": (
                ownership.receipt_sha256),
        })


def test_final_packet_binding_lock_blocks_writer_until_fanout_boundary(
        native_runtime):
    """The runner's final-check lock is the native writer's same lock."""
    snapshot = review.load_packet_snapshot(
        CLIENT,
        workstation_id=WORKSTATION_ID,
        review_dir=str(native_runtime.review_dir),
        require_native_owner=True,
    )
    ownership = workstations.native_workstation_ownership(
        CLIENT, WORKSTATION_ID, review_dir=native_runtime.review_dir)
    attempted = threading.Event()
    finished = threading.Event()
    failures: list[BaseException] = []

    def writer() -> None:
        attempted.set()
        try:
            review.revise(
                CLIENT,
                {"pursuit_strategy": "writer after fanout boundary"},
                workstation_id=WORKSTATION_ID,
                review_dir=str(native_runtime.review_dir),
                expected_sha256=snapshot.sha256,
                native_owner=True,
                expected_registry_sha256=ownership.registry_sha256,
                expected_receipt_sha256=ownership.receipt_sha256,
            )
        except BaseException as exc:  # surfaced in the main assertion thread
            failures.append(exc)
        finally:
            finished.set()

    with review.packet_binding_lock(
            CLIENT,
            workstation_id=WORKSTATION_ID,
            review_dir=str(native_runtime.review_dir),
            native_owner=True):
        thread = threading.Thread(target=writer)
        thread.start()
        assert attempted.wait(1)
        assert not finished.wait(0.1)

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert finished.is_set()
    assert failures == []
    packet = review.load_packet(
        CLIENT,
        workstation_id=WORKSTATION_ID,
        review_dir=str(native_runtime.review_dir),
        native_owner=True,
    )
    assert packet.strategy.pursuit_strategy == "writer after fanout boundary"
