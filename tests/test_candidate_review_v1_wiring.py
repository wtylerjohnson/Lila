"""Production caller and assessment/refresh wiring for Candidate Review v1."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from agents.assessment_chain import (
    SubprocessRunnerAdapter,
    validate_candidate_review_watch_marker,
)
from agents.candidate_review_v1.persistence import load_current_generation
from agents.decisions.schemas import (
    IntakeStrategy,
    Keyword,
    KeywordCategory,
    SearchSpec,
)
from agents.review import ReviewPacket, ReviewStatus
from tools.capability import CapabilityTerms, ClientProfile

import run_candidate_review_watch as watch_cli


ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 7, 22, 18, tzinfo=timezone.utc)
APPROVED_AT = datetime(2026, 7, 22, 16, tzinfo=timezone.utc)


def _approved_world(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("LILA_EXPECT_WORKSTATION_ID", raising=False)
    monkeypatch.delenv("LILA_EXPECT_NATIVE_WORKSTATION", raising=False)
    client_dir = tmp_path / "clients" / "riverbed"
    client_dir.mkdir(parents=True)
    profile = ClientProfile(
        client_name="Riverbed",
        capability_terms=CapabilityTerms(
            core=["artificial intelligence", "data analytics"],
            adjacent=["zero trust"],
            excluded=["river bank"],
        ),
        named_competitors_and_incumbents=["Example Incumbent"],
        mission_components=["Department of Defense", "Air Force"],
        naics_boundary=["541512"],
    )
    (client_dir / "profile.json").write_text(
        profile.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    strategy = IntakeStrategy(
        client_name="Riverbed",
        pursuit_strategy="Find evidence-bound federal requirements.",
        keywords=[
            Keyword(
                term="machine learning",
                category=KeywordCategory.TECHNOLOGY,
                rationale="Approved capability adjacency.",
            ),
            Keyword(
                term="Department of Defense",
                category=KeywordCategory.AGENCY,
                rationale="Approved buyer scope.",
            ),
        ],
        inferred_naics=["541512"],
        target_agencies=["Department of Defense"],
        searches=[
            SearchSpec(
                source="sam.gov",
                query_terms=["artificial intelligence"],
                naics_codes=["541512"],
                rationale="Approved live-opportunity search.",
            )
        ],
        confidence=0.8,
        sources_reviewed=["https://example.gov/source"],
        review_gate="Analyst approval required.",
    )
    packet = ReviewPacket(
        client_name="Riverbed",
        status=ReviewStatus.APPROVED,
        strategy=strategy,
        decided_at=APPROVED_AT,
        search_scope={"all": True},
    )
    review_dir = tmp_path / "data" / "review"
    review_dir.mkdir(parents=True)
    (review_dir / "riverbed.review.json").write_text(
        packet.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    cleaned = tmp_path / "data" / "cleaned"
    cleaned.mkdir(parents=True)
    (cleaned / "searches_riverbed.json").write_text(json.dumps({
        "client": "Riverbed",
        "generated_at": AS_OF.isoformat(),
        "search_scope": {"all": True},
        "results": {},
    }, indent=2) + "\n", encoding="utf-8")
    import agents.assessment_chain as assessment_chain

    monkeypatch.setattr(
        assessment_chain,
        "relevance_receipt_valid",
        lambda _slug, *, root, allow_board_content_change,
        allow_sweep_change: (
            bool(root)
            and allow_board_content_change
            and allow_sweep_change
        ),
    )
    registry = tmp_path / "data" / "reference" / "event_watch_universe.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(
        (ROOT / "data" / "reference" / "event_watch_universe.json").read_bytes()
    )
    return registry


def _write_vehicle_source_bundle(tmp_path: Path) -> Path:
    """Enable exact-current-sweep replay through the client-owned seam."""

    from agents.candidate_review_v1.replay_providers import (
        CurrentSweepSource,
        ReplaySourceBundleConfig,
    )

    path = (
        tmp_path
        / "clients"
        / "riverbed"
        / "candidate_review_replay_source.json"
    )
    path.write_text(
        ReplaySourceBundleConfig(
            client_id="riverbed",
            client_name="Riverbed",
            vehicle=CurrentSweepSource(),
        ).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_cli_constructs_frames_from_approved_existing_artifacts(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    receipt = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )

    assert receipt.is_file()
    marker = f"[out:candidate-review-watch] {receipt}\n"
    assert validate_candidate_review_watch_marker(
        tmp_path,
        "riverbed",
        marker,
    ) == receipt
    current = load_current_generation(
        "riverbed",
        state_root=tmp_path / "data" / "state" / "candidate_review_v1",
    )
    assert current is not None
    inputs = current.manifests["pipeline-inputs"]
    assert inputs["event_research_frame"]["capabilities_missions"] == [
        "artificial intelligence",
        "data analytics",
        "machine learning",
        "zero trust",
    ]
    tags = current.registries["event-watch-selection"]["frame_tags"]
    assert "artificial-intelligence" in tags["capability_tags"]
    assert "defense" in tags["agency_tags"]
    providers = current.manifests["provider-bindings"]["providers"]
    assert providers["event"]["mode"] == "unavailable"
    assert providers["vehicle"]["mode"] == "unavailable"
    event_attempts = current.artifacts["event-discovery-result"]["attempts"]
    vehicle_attempts = current.artifacts["vehicle-collection-result"]["attempts"]
    assert any(row["state"] == "not_run" for row in event_attempts)
    assert any(row["state"] == "not_run" for row in vehicle_attempts)


def test_assessment_repull_exposes_exact_parent_ids_before_watch(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    _write_vehicle_source_bundle(tmp_path)
    content = tmp_path / "clients" / "riverbed" / "signal_board_content.json"
    content.write_text(json.dumps({
        "client_name": "Riverbed",
        "figures": [{
            "text": "$1",
            "raw": 1,
            "source_system": "usaspending",
            "generated_internal_id": "CONT_AWD_TEST123_7003_-NONE-_-NONE-",
        }],
    }) + "\n", encoding="utf-8")
    (tmp_path / "agents" / "candidate_review_v1").mkdir(parents=True)
    (tmp_path / "run_candidate_review_watch.py").write_text(
        "# watch stub\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter,
        "_client_name",
        lambda _slug: "Riverbed",
    )
    sweep_path = tmp_path / "data" / "cleaned" / "searches_riverbed.json"
    import agents.review as review

    monkeypatch.setattr(
        review,
        "sweep_artifact_path",
        lambda _client, **_kwargs: sweep_path,
    )

    def invoke(argv):
        calls.append(argv)
        if argv[1] == "-m":
            sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
            sweep["results"]["award_repulls"] = [{
                "generated_id": "CONT_AWD_TEST123_7003_-NONE-_-NONE-",
                "parent_award_id": "OASIS-PLUS-UR",
                "parent_generated_id": "IDV_PARENT_EXACT_001",
                "source_system": "usaspending",
                "retrieved_at": AS_OF.isoformat(),
            }]
            sweep_path.write_text(
                json.dumps(sweep, indent=2) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(argv, 0, "repulled\n", "")
        if argv[1].endswith("run_candidate_review_watch.py"):
            rows = json.loads(sweep_path.read_text(encoding="utf-8"))[
                "results"
            ]["award_repulls"]
            assert [row["parent_generated_id"] for row in rows] == [
                "IDV_PARENT_EXACT_001"
            ]
            receipt = watch_cli.run_watch_generation(
                "riverbed",
                root=tmp_path,
                registry_path=registry,
            )
            return subprocess.CompletedProcess(
                argv,
                0,
                f"[out:candidate-review-watch] {receipt}\n",
                "",
            )
        raise AssertionError(argv)

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("REPULLING", slug="riverbed", mode="assessment")

    assert outcome.success is True
    assert [argv[1] if argv[1] == "-m" else Path(argv[1]).name
            for argv in calls] == [
        "-m",
        "run_candidate_review_watch.py",
    ]
    assert calls[0][2] == "tools.api.award_repull"
    assert "[out:candidate-review-watch]" in outcome.stdout
    current = load_current_generation(
        "riverbed",
        state_root=tmp_path / "data" / "state" / "candidate_review_v1",
    )
    assert current is not None
    providers = current.manifests["provider-bindings"]["providers"]
    assert providers["event"]["mode"] == "unavailable"
    assert providers["vehicle"]["mode"] == "replay"
    first_receipt = (
        tmp_path
        / "data"
        / "state"
        / "candidate_review_v1"
        / "riverbed"
        / current.pointer.receipt_path
    )
    first_bytes = first_receipt.read_bytes()
    retry = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )
    assert retry == first_receipt.resolve()
    assert retry.read_bytes() == first_bytes
    assert "ticker" not in current.artifacts
    parent_rows = current.manifests["pipeline-inputs"][
        "vehicle_watch_frame"
    ]["known_parent_idvs"]
    assert parent_rows == [{
        "source_system": "usaspending.gov",
        "record_id": "IDV_PARENT_EXACT_001",
        "revision_id": None,
    }]


def test_refresh_path_rebinds_client_source_after_fresh_repull(
        tmp_path, monkeypatch):
    """The one-click refresh resolves replay only after its re-pull bytes."""

    import run_refresh_press as refresh

    registry = _approved_world(tmp_path, monkeypatch)
    _write_vehicle_source_bundle(tmp_path)
    (tmp_path / "agents" / "candidate_review_v1").mkdir(
        parents=True,
        exist_ok=True,
    )
    watch_script = tmp_path / "run_candidate_review_watch.py"
    watch_script.write_text("# production watch boundary\n", encoding="utf-8")
    compose_script = tmp_path / "run_compose.py"
    compose_script.write_text("# compose boundary\n", encoding="utf-8")
    report_dir = tmp_path / "data" / "reports"
    monkeypatch.setattr(refresh, "ROOT", str(tmp_path))
    monkeypatch.setattr(refresh, "WATCH_SCRIPT", str(watch_script))
    monkeypatch.setattr(refresh, "COMPOSE_SCRIPT", str(compose_script))
    monkeypatch.setattr(refresh, "REPORT_DIR", str(report_dir))
    monkeypatch.setattr(
        refresh,
        "_profile_owned_identity",
        lambda _value: ("riverbed", "Riverbed"),
    )
    monkeypatch.setattr(
        refresh,
        "_legacy_all_federal_error",
        lambda _client: None,
    )
    monkeypatch.setattr(
        refresh,
        "_relevance_step",
        lambda _name, _slug, _path, _moment, execute, command: (
            execute(command, cwd=refresh.ROOT, check=False).returncode
        ),
    )
    monkeypatch.setattr(
        refresh,
        "_compose_step",
        lambda execute, command, _slug: (
            execute(command, cwd=refresh.ROOT, check=False).returncode,
            "",
            None,
        ),
    )

    sweep_path = tmp_path / "data" / "cleaned" / "searches_riverbed.json"
    before_repull = hashlib.sha256(sweep_path.read_bytes()).hexdigest()
    after_repull: list[str] = []
    commands: list[list[str]] = []

    def execute(command, **_kwargs):
        commands.append(command)
        if any("award_repull" in part for part in command):
            sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
            sweep["results"]["award_repulls"] = [{
                "kind": "award_repull",
                "generated_id": "CONT_AWD_REFRESH_EXACT_001",
                "award_id": "REFRESH-ORDER-001",
                "description": "Post-re-pull analytics order",
                "parent_award_id": "OASIS-PLUS-UR",
                "parent_generated_id": "CONT_IDV_REFRESH_PARENT_001",
                "source_system": "usaspending",
                "retrieved_at": (
                    AS_OF.replace(minute=AS_OF.minute + 1).isoformat()
                ),
            }]
            sweep_path.write_text(
                json.dumps(sweep, indent=2) + "\n",
                encoding="utf-8",
            )
            after_repull.append(
                hashlib.sha256(sweep_path.read_bytes()).hexdigest()
            )
            return subprocess.CompletedProcess(command, 0, "repulled\n", "")
        if any("run_candidate_review_watch.py" in part for part in command):
            assert after_repull and after_repull[-1] != before_repull
            receipt = watch_cli.run_watch_generation(
                "riverbed",
                root=tmp_path,
                registry_path=registry,
            )
            return subprocess.CompletedProcess(
                command,
                0,
                f"[out:candidate-review-watch] {receipt}\n",
                "",
            )
        if any("run_signal_board.py" in part for part in command):
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "riverbed.federal_opportunity_signals.internal.md") \
                .write_text("# fresh\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    def persist(*_args, **_kwargs):
        state = tmp_path / "data" / "state" / "deltas" / "riverbed"
        state.mkdir(parents=True, exist_ok=True)
        paths = {
            "snapshot_path": state / "snapshot.json",
            "delta_path": state / "delta.json",
            "summary_path": state / "delta.md",
        }
        for path in paths.values():
            path.write_text("{}\n", encoding="utf-8")
        return paths

    def certify(*_args, **_kwargs):
        path = report_dir / "riverbed.federal_opportunity_signals.qa.json"
        path.write_text("{}\n", encoding="utf-8")
        return path

    assert refresh.run_refresh_press(
        "Riverbed",
        now=AS_OF,
        executor=execute,
        persister=persist,
        certifier=certify,
    ) == 0
    repull_index = next(
        index for index, command in enumerate(commands)
        if any("award_repull" in part for part in command)
    )
    watch_index = next(
        index for index, command in enumerate(commands)
        if any("run_candidate_review_watch.py" in part for part in command)
    )
    assert repull_index < watch_index
    current = load_current_generation(
        "riverbed",
        state_root=tmp_path / "data" / "state" / "candidate_review_v1",
    )
    assert current is not None
    assert current.receipt.binding.evidence_snapshot_sha256 == after_repull[-1]
    providers = current.manifests["provider-bindings"]["providers"]
    assert providers["event"]["mode"] == "unavailable"
    assert providers["vehicle"]["mode"] == "replay"
    assert "ticker" not in current.artifacts
    assert all(
        row["creates_candidate"] is False
        for row in current.artifacts["vehicle-collection-result"]["leads"]
    )


def test_assessment_compose_stage_does_not_run_watch(tmp_path, monkeypatch):
    (tmp_path / "agents" / "candidate_review_v1").mkdir(parents=True)
    (tmp_path / "run_candidate_review_watch.py").write_text(
        "# watch stub\n",
        encoding="utf-8",
    )
    (tmp_path / "run_compose.py").write_text(
        "# compose stub\n",
        encoding="utf-8",
    )
    content = tmp_path / "clients" / "riverbed" / "signal_board_content.json"
    content.parent.mkdir(parents=True)
    calls: list[list[str]] = []
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter,
        "_require_client_name",
        lambda _slug: "Riverbed",
    )
    trail = tmp_path / "compose.internal.md"
    monkeypatch.setattr(
        "agents.assessment_chain.validate_compose_pair",
        lambda *_args, **_kwargs: trail,
    )

    def invoke(argv):
        calls.append(argv)
        content.write_text(
            '{"client_name":"Riverbed"}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, "compose\n", "")

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("COMPOSING", slug="riverbed", mode="assessment")

    assert outcome.success is True
    assert [Path(argv[1]).name for argv in calls] == ["run_compose.py"]


def test_assessment_repull_stops_on_named_watch_failure(
        tmp_path, monkeypatch):
    (tmp_path / "agents" / "candidate_review_v1").mkdir(parents=True)
    (tmp_path / "run_candidate_review_watch.py").write_text(
        "# watch stub\n",
        encoding="utf-8",
    )
    content = tmp_path / "clients" / "riverbed" / "signal_board_content.json"
    content.parent.mkdir(parents=True)
    content.write_text(json.dumps({
        "client_name": "Riverbed",
        "figures": [],
    }) + "\n", encoding="utf-8")
    sweep_path = tmp_path / "data" / "cleaned" / "searches_riverbed.json"
    sweep_path.parent.mkdir(parents=True)
    sweep_path.write_text(json.dumps({
        "client": "Riverbed",
        "results": {},
    }) + "\n", encoding="utf-8")
    import agents.review as review

    monkeypatch.setattr(
        review,
        "sweep_artifact_path",
        lambda _client, **_kwargs: sweep_path,
    )
    calls: list[list[str]] = []
    adapter = SubprocessRunnerAdapter(root=tmp_path, python="PYTHON")
    monkeypatch.setattr(
        adapter,
        "_client_name",
        lambda _slug: "Riverbed",
    )
    failure = {
        "stage": "candidate-review-watch",
        "reason": "approved frame is unavailable",
        "fix_surface": "approve the Candidate Review frame",
    }

    def invoke(argv):
        calls.append(argv)
        if argv[1] == "-m":
            sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
            sweep["results"]["award_repulls"] = []
            sweep_path.write_text(
                json.dumps(sweep, indent=2) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(argv, 0, "repulled\n", "")
        return subprocess.CompletedProcess(
            argv,
            2,
            "",
            json.dumps(failure) + "\n",
        )

    monkeypatch.setattr(adapter, "_invoke", invoke)
    outcome = adapter.run("REPULLING", slug="riverbed", mode="assessment")

    assert outcome.success is False
    assert outcome.reason == failure["reason"]
    assert outcome.fix_surface == failure["fix_surface"]
    assert [argv[1] if argv[1] == "-m" else Path(argv[1]).name
            for argv in calls] == [
        "-m",
        "run_candidate_review_watch.py",
    ]


def test_watch_marker_rejects_duplicates(tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    receipt = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )
    marker = f"[out:candidate-review-watch] {receipt}\n"
    with pytest.raises(ValueError, match="exactly one"):
        validate_candidate_review_watch_marker(
            tmp_path,
            "riverbed",
            marker + marker,
        )


def test_registry_byte_change_mints_a_new_generation(tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    first = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )
    registry.write_bytes(registry.read_bytes() + b"\n")
    second = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )

    assert first != second
    assert first.is_file()
    assert second.is_file()


def test_next_run_checks_do_not_advance_a_partly_failed_multi_query_target(
        tmp_path, monkeypatch):
    from agents.candidate_review_v1.pipeline import (
        ProviderBinding,
        ProviderMode,
        ResearchProviderBindings,
        execute_candidate_review_generation,
        plan_candidate_review_generation,
    )

    registry = _approved_world(tmp_path, monkeypatch)
    registry_sha256 = hashlib.sha256(registry.read_bytes()).hexdigest()
    binding, event_frame, tags, vehicle_frame, as_of = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=registry_sha256,
    )
    provider = ProviderBinding(
        adapter_name="fixture-replay",
        adapter_version="1",
        config_sha256="a" * 64,
        mode=ProviderMode.REPLAY,
    )
    plan = plan_candidate_review_generation(
        binding,
        event_frame,
        tags,
        vehicle_frame,
        as_of=as_of,
        event_last_checked_at={},
        registry_path=registry,
        providers=ResearchProviderBindings(event=provider, vehicle=provider),
    )
    grouped: dict[str, list] = {}
    for query in plan.event_query_manifest.queries:
        if query.required and query.watch_target_id is not None:
            grouped.setdefault(query.watch_target_id, []).append(query)
    target_id, target_queries = next(
        (target_id, queries)
        for target_id, queries in grouped.items()
        if len(queries) > 1
    )
    failed_query_id = target_queries[0].query_id

    def event_searcher(query):
        if query.query_id == failed_query_id:
            raise RuntimeError("one source class is unavailable")
        return ()

    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    result = execute_candidate_review_generation(
        plan,
        event_searcher=event_searcher,
        vehicle_searcher=lambda _query: (),
        state_root=state_root,
    )
    target_attempts = [
        attempt for attempt in result.event_result.attempts
        if attempt.query_id in {query.query_id for query in target_queries}
    ]
    assert {row.state.value for row in target_attempts} == {
        "failed",
        "returned",
    }
    assert target_id not in result.next_event_watch_checks.advanced_target_ids
    assert target_id not in watch_cli._prior_event_checks(
        binding.client_id,
        state_root=state_root,
        registry_path=registry,
    )


def _write_dynamic_sweep(
    tmp_path: Path,
    *,
    rows: list[dict],
    buyers: list[dict] | None = None,
    award_repulls: list[dict] | None = None,
    search_scope: dict | None = None,
    filename: str = "searches_riverbed.json",
) -> Path:
    path = tmp_path / "data" / "cleaned" / filename
    path.write_text(json.dumps({
        "client": "Riverbed",
        "generated_at": AS_OF.isoformat(),
        "search_scope": search_scope or {"all": True},
        "results": {
            "sam.gov": rows,
            "triage": {
                row["source_id"]: {
                    "verdict": (
                        "pursue" if index % 3 == 0
                        else "monitor" if index % 3 == 1
                        else "discard"
                    ),
                    "reason": "analyst-visible outcome",
                }
                for index, row in enumerate(rows)
                if row.get("source_id")
            },
            "incumbent_buyer_map": {"buyers": buyers or []},
            "award_repulls": [
                {"retrieved_at": AS_OF.isoformat(), **row}
                for row in (award_repulls or [])
            ],
        },
    }, indent=2) + "\n", encoding="utf-8")
    return path


def _write_taxonomy(tmp_path: Path, *, updated: str = "2026-07-22") -> Path:
    path = tmp_path / "clients" / "riverbed" / "capability_taxonomy.json"
    path.write_text(json.dumps({
        "client_name": "Riverbed",
        "version": 3,
        "updated": updated,
        "core": [{"term": "network observability", "mode": "stemmed"}],
        "adjacent": [{"term": "application performance", "mode": "stemmed"}],
        "exclude": [],
        "code_universe": {
            "naics": ["541513"],
            "psc": ["D302", "R408"],
        },
    }, indent=2) + "\n", encoding="utf-8")
    return path


def test_production_frames_bind_taxonomy_and_every_structured_sam_identity(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    _write_taxonomy(tmp_path)
    rows = [
        {
            "source": "sam.gov",
            "source_id": f"NOTICE-{index:04d}",
            "agency": "DEPT OF DEFENSE.US AIR FORCE",
            "title": "Title is presentation only",
            "api_url": f"https://sam.gov/opp/ignored-{index}/view",
        }
        for index in range(130)
    ]
    _write_dynamic_sweep(
        tmp_path,
        rows=rows,
        buyers=[{
            "buyer": "PROGRAM EXECUTIVE OFFICE DIGITAL",
            "agency": "DEPT OF DEFENSE",
        }],
        award_repulls=[{
            "kind": "award_repull",
            "recipient": "Exact Award Recipient LLC",
            "parent_award_id": "PIID-MUST-NOT-BE-USED",
            "parent_generated_id": "CONT_IDV_EXACT_PARENT_001",
        }],
    )

    registry_sha256 = hashlib.sha256(registry.read_bytes()).hexdigest()
    _, event_frame, tags, vehicle_frame, _ = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=registry_sha256,
    )

    assert len(vehicle_frame.candidate_source_identities) == 130
    assert {
        row.record_id for row in vehicle_frame.candidate_source_identities
    } == {f"NOTICE-{index:04d}" for index in range(130)}
    assert event_frame.candidate_accounts == (
        "DEPT OF DEFENSE.US AIR FORCE",
    )
    assert event_frame.buyer_communities == (
        "PROGRAM EXECUTIVE OFFICE DIGITAL",
    )
    assert event_frame.vehicle_partner_competitor_ecosystem == (
        "Exact Award Recipient LLC",
        "Example Incumbent",
    )
    assert vehicle_frame.psc_codes == ("D302", "R408")
    assert {"D302", "R408"} <= set(event_frame.naics_psc)
    assert len(vehicle_frame.known_parent_idvs) == 1
    parent = vehicle_frame.known_parent_idvs[0]
    assert parent.source_system == "usaspending.gov"
    assert parent.record_id == "CONT_IDV_EXACT_PARENT_001"
    assert parent.record_id != "PIID-MUST-NOT-BE-USED"
    assert "program-executive-office" in tags.buyer_tags
    assert "department-of-defense" not in tags.buyer_tags
    assert "department-of-defense" in tags.agency_tags


def test_focused_frame_uses_only_approved_scope_and_rejects_scope_drift(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    review_path = tmp_path / "data" / "review" / "riverbed.review.json"
    packet = json.loads(review_path.read_text(encoding="utf-8"))
    packet["search_scope"] = {"agencies": ["DoD"]}
    review_path.write_text(
        json.dumps(packet, indent=2) + "\n",
        encoding="utf-8",
    )
    focused_scope = {
        "mode": "focus",
        "agencies": [{
            "abbr": "DoD",
            "name": "Department of Defense",
        }],
    }
    path = _write_dynamic_sweep(
        tmp_path,
        rows=[{
            "source": "sam.gov",
            "source_id": "DOD-1",
            "agency": "DEPT OF DEFENSE.US AIR FORCE",
        }],
        buyers=[{
            "buyer": "AIR FORCE LIFE CYCLE MANAGEMENT CENTER",
            "agency": "DEPT OF DEFENSE",
        }],
        search_scope=focused_scope,
        filename="searches_riverbed.agency_dod.json",
    )
    registry_sha256 = hashlib.sha256(registry.read_bytes()).hexdigest()
    _, event_frame, _, vehicle_frame, _ = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=registry_sha256,
    )
    assert event_frame.priority_agencies_components == (
        "Department of Defense",
    )
    assert vehicle_frame.agencies == ("Department of Defense",)
    assert "Air Force" not in vehicle_frame.agencies

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["search_scope"] = {"all": True}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="search_scope differs"):
        watch_cli._build_inputs(
            tmp_path,
            "riverbed",
            registry_sha256=registry_sha256,
        )

    payload["search_scope"] = focused_scope
    payload["results"]["sam.gov"][0]["agency"] = (
        "General Services Administration"
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside approved focus scope"):
        watch_cli._build_inputs(
            tmp_path,
            "riverbed",
            registry_sha256=registry_sha256,
        )


def test_dynamic_frame_revision_is_order_independent_but_taxonomy_bound(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    taxonomy = _write_taxonomy(tmp_path)
    rows = [
        {
            "source": "sam.gov",
            "source_id": "NOTICE-2",
            "agency": "General Services Administration",
        },
        {
            "source": "sam.gov",
            "source_id": "NOTICE-1",
            "agency": "Department of Defense",
        },
    ]
    buyers = [
        {"buyer": "Buyer Z", "agency": "GSA"},
        {"buyer": "Buyer A", "agency": "Department of Defense"},
    ]
    awards = [
        {
            "kind": "award_repull",
            "recipient": "Recipient Z",
            "parent_generated_id": "PARENT-Z",
        },
        {
            "kind": "award_repull",
            "recipient": "Recipient A",
            "parent_generated_id": "PARENT-A",
        },
    ]
    _write_dynamic_sweep(
        tmp_path,
        rows=rows,
        buyers=buyers,
        award_repulls=awards,
    )
    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    first = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=digest,
    )
    _write_dynamic_sweep(
        tmp_path,
        rows=list(reversed(rows)),
        buyers=list(reversed(buyers)),
        award_repulls=list(reversed(awards)),
    )
    second = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=digest,
    )
    assert first[1].revision_sha256 == second[1].revision_sha256
    assert first[1].model_dump(exclude={"binding"}) \
        == second[1].model_dump(exclude={"binding"})
    assert first[3].model_dump(exclude={"binding"}) \
        == second[3].model_dump(exclude={"binding"})

    changed = json.loads(taxonomy.read_text(encoding="utf-8"))
    changed["updated"] = "2026-07-23"
    taxonomy.write_text(
        json.dumps(changed, indent=2) + "\n",
        encoding="utf-8",
    )
    third = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=digest,
    )
    assert third[1].revision_sha256 != second[1].revision_sha256


def test_dynamic_frame_rejects_identity_overflow_and_url_only_records(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    _write_dynamic_sweep(tmp_path, rows=[{
        "source": "sam.gov",
        "agency": "Department of Defense",
        "api_url": "https://sam.gov/opp/URL-IS-NOT-IDENTITY/view",
    }])
    with pytest.raises(ValueError, match="source_id"):
        watch_cli._build_inputs(
            tmp_path,
            "riverbed",
            registry_sha256=digest,
        )

    _write_dynamic_sweep(tmp_path, rows=[{
        "source": "sam.gov",
        "source_id": f"N-{index:04d}",
        "agency": "Department of Defense",
    } for index in range(1025)])
    with pytest.raises(ValueError, match="maximum is 1024"):
        watch_cli._build_inputs(
            tmp_path,
            "riverbed",
            registry_sha256=digest,
        )


def test_registry_digest_boundary_resets_persisted_event_cadence(
        tmp_path, monkeypatch):
    from agents.candidate_review_v1.pipeline import (
        ProviderBinding,
        ProviderMode,
        ResearchProviderBindings,
        execute_candidate_review_generation,
        plan_candidate_review_generation,
    )

    registry = _approved_world(tmp_path, monkeypatch)
    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    binding, event_frame, tags, vehicle_frame, as_of = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=digest,
    )
    replay = ProviderBinding(
        adapter_name="fixture-replay",
        adapter_version="1",
        config_sha256="b" * 64,
        mode=ProviderMode.REPLAY,
    )
    plan = plan_candidate_review_generation(
        binding,
        event_frame,
        tags,
        vehicle_frame,
        as_of=as_of,
        event_last_checked_at={},
        registry_path=registry,
        providers=ResearchProviderBindings(event=replay, vehicle=replay),
    )
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    result = execute_candidate_review_generation(
        plan,
        event_searcher=lambda _query: (),
        vehicle_searcher=lambda _query: (),
        state_root=state_root,
    )
    assert result.next_event_watch_checks.advanced_target_ids
    unchanged = watch_cli._prior_event_checks(
        binding.client_id,
        state_root=state_root,
        registry_path=registry,
    )
    assert unchanged

    registry.write_bytes(registry.read_bytes() + b"\n")
    assert watch_cli._prior_event_checks(
        binding.client_id,
        state_root=state_root,
        registry_path=registry,
    ) == {}


def test_watch_as_of_includes_post_sweep_repull_retrieval(
        tmp_path, monkeypatch):
    registry = _approved_world(tmp_path, monkeypatch)
    retrieved_at = datetime(2026, 7, 22, 20, tzinfo=timezone.utc)
    path = _write_dynamic_sweep(
        tmp_path,
        rows=[],
        award_repulls=[{
            "kind": "award_repull",
            "recipient": "Recipient",
            "parent_generated_id": "PARENT-1",
            "retrieved_at": retrieved_at.isoformat(),
        }],
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["results"]["agency_docs"] = [{
        "source_record_id": "DOC-1",
        "retrieved_at": datetime(
            2026, 7, 22, 19, tzinfo=timezone.utc
        ).isoformat(),
    }]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    _, _, _, vehicle_frame, as_of = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=digest,
    )
    assert as_of == retrieved_at
    assert vehicle_frame.window_start == retrieved_at.date()


def test_run_watch_generation_uses_bound_offline_replay_and_reuses_bytes(
        tmp_path, monkeypatch):
    """The production seam executes replay without a hidden live fallback."""

    from agents.candidate_review_v1.contracts import CoverageState
    from agents.candidate_review_v1.event_research import (
        EventLead,
        EventSearchResponse,
    )
    from agents.candidate_review_v1.pipeline import (
        plan_candidate_review_generation,
    )
    from agents.candidate_review_v1.replay_providers import (
        CurrentSweepReplay,
        EventReplayArtifact,
        ReplayFileReference,
        ReplayProviderConfig,
    )

    registry = _approved_world(tmp_path, monkeypatch)
    sweep_path = _write_dynamic_sweep(
        tmp_path,
        rows=[{
            "source": "sam.gov",
            "source_id": "SAM-EXACT-1",
            "agency": "Department of Defense",
            "title": "Artificial intelligence network observability IDIQ",
            "naics_code": "541512",
            "api_url": "https://sam.gov/opp/SAM-EXACT-1/view",
        }],
        award_repulls=[{
            "kind": "award_repull",
            "generated_id": "CONT_AWD_ORDER_EXACT_001",
            "award_id": "ORDER-EXACT-001",
            "recipient": "Example Incumbent",
            "description": "Artificial intelligence analytics task order",
            "naics": "541512",
            "parent_award_id": "PARENT-PIID-001",
            "parent_generated_id": "CONT_IDV_PARENT_EXACT_001",
            "source_system": "usaspending",
            "url": (
                "https://www.usaspending.gov/award/"
                "CONT_AWD_ORDER_EXACT_001"
            ),
        }],
    )
    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    sweep["results"]["sam_census"] = {
        "matched": 1,
        "active_screened": 1,
        "complete": True,
        "source": "test snapshot",
    }
    sweep_path.write_text(
        json.dumps(sweep, indent=2) + "\n",
        encoding="utf-8",
    )

    registry_sha256 = hashlib.sha256(registry.read_bytes()).hexdigest()
    binding, event_frame, tags, vehicle_frame, as_of = watch_cli._build_inputs(
        tmp_path,
        "riverbed",
        registry_sha256=registry_sha256,
    )
    preview = plan_candidate_review_generation(
        binding,
        event_frame,
        tags,
        vehicle_frame,
        as_of=as_of,
        event_last_checked_at={},
        registry_path=registry,
        providers=watch_cli._provider_bindings(),
    )
    event_queries = tuple(
        row for row in preview.event_query_manifest.queries if row.required
    )
    assert len(event_queries) >= 6
    event_query = event_queries[0]
    lead_query = event_queries[5]
    event_artifact = EventReplayArtifact(
        binding=binding,
        manifest_id=preview.event_query_manifest.manifest_id,
        responses={
            event_query.query_id: EventSearchResponse(
                state=CoverageState.RETURNED,
                records_returned=0,
                public_detail="Offline fixture completed with no leads.",
            ),
            event_queries[1].query_id: EventSearchResponse(
                state=CoverageState.PARTIAL,
                records_returned=1,
                public_detail="Offline fixture contains a partial page.",
            ),
            event_queries[2].query_id: EventSearchResponse(
                state=CoverageState.STALE_SNAPSHOT,
                records_returned=0,
                public_detail="Offline fixture is outside source freshness.",
            ),
            event_queries[3].query_id: EventSearchResponse(
                state=CoverageState.FAILED,
                public_detail="Offline fixture records a failed query.",
            ),
            event_queries[4].query_id: EventSearchResponse(
                state=CoverageState.NOT_RUN,
                public_detail="Offline fixture records an unrun query.",
            ),
            lead_query.query_id: EventSearchResponse(
                state=CoverageState.RETURNED,
                records_returned=1,
                leads=(EventLead(
                    lead_id="event-replay-lead-1",
                    query_id=lead_query.query_id,
                    source_class=lead_query.source_class,
                    discovered_at=as_of,
                    title="Federal technology forum",
                    url="https://events.example.gov/federal-forum",
                    claimed_date="2026-10-01",
                ),),
                public_detail="Offline fixture returned one unverified lead.",
            ),
        },
    )
    event_path = tmp_path / "event-replay.json"
    event_path.write_text(
        event_artifact.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    config = ReplayProviderConfig(
        binding=binding,
        event_manifest_id=preview.event_query_manifest.manifest_id,
        vehicle_manifest_id=preview.vehicle_query_manifest.manifest_id,
        event=ReplayFileReference(
            path=event_path.name,
            sha256=hashlib.sha256(event_path.read_bytes()).hexdigest(),
        ),
        vehicle=CurrentSweepReplay(
            sweep_sha256=hashlib.sha256(sweep_path.read_bytes()).hexdigest(),
        ),
    )
    config_path = tmp_path / "candidate-review-replay.json"
    config_path.write_text(
        config.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    def no_live_calls(*_args, **_kwargs):
        raise AssertionError("offline Candidate Review replay used the network")

    import socket
    import urllib.request

    monkeypatch.setattr(socket, "create_connection", no_live_calls)
    monkeypatch.setattr(urllib.request, "urlopen", no_live_calls)
    monkeypatch.setenv(
        "LILA_CANDIDATE_REVIEW_REPLAY_CONFIG",
        str(config_path),
    )
    receipt = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )
    first_receipt_bytes = receipt.read_bytes()
    retry = watch_cli.run_watch_generation(
        "riverbed",
        root=tmp_path,
        registry_path=registry,
    )

    assert retry == receipt
    assert retry.read_bytes() == first_receipt_bytes
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    current = load_current_generation("riverbed", state_root=state_root)
    assert current is not None
    providers = current.manifests["provider-bindings"]["providers"]
    assert providers["event"]["mode"] == "replay"
    assert providers["vehicle"]["mode"] == "replay"
    assert providers["event"]["config_sha256"] != providers["vehicle"][
        "config_sha256"
    ]
    assert all(len(row["config_sha256"]) == 64 for row in providers.values())

    event_attempts = current.artifacts["event-discovery-result"]["attempts"]
    assert {
        "returned",
        "partial",
        "stale_snapshot",
        "failed",
        "not_run",
    } <= {row["state"] for row in event_attempts}
    event_leads = current.artifacts["event-discovery-result"]["leads"]
    assert len(event_leads) == 1
    assert event_leads[0]["lead_id"] == "event-replay-lead-1"
    assert event_leads[0]["query_id"] == lead_query.query_id
    assert event_leads[0]["source_class"] == lead_query.source_class.value
    assert datetime.fromisoformat(
        event_leads[0]["discovered_at"].replace("Z", "+00:00")
    ) == as_of
    vehicle = current.artifacts["vehicle-collection-result"]
    assert any(row["state"] == "returned" for row in vehicle["attempts"])
    assert any(row["state"] == "not_run" for row in vehicle["attempts"])
    assert {
        row["source_identity"]["record_id"] for row in vehicle["leads"]
    } >= {
        "SAM-EXACT-1",
        "CONT_IDV_PARENT_EXACT_001",
        "CONT_AWD_ORDER_EXACT_001",
    }
    assert all(row["creates_candidate"] is False for row in vehicle["leads"])
    assert current.artifacts["research-snapshot-diff"]["changes"] == []
    assert "ticker" not in current.artifacts
