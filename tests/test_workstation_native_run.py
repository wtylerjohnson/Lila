"""Native workstation search authorization and immutable run binding.

These tests are deliberately offline.  A fake executor returns inert source
results without invoking a source function, and the API job launcher is always
mocked.  They pin the Tranche-3 rule that an approved workstation-native packet
is the exact authority for its own search while the independent legacy-current
workstation remains available until the explicit Tranche-5 migration/cutover.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents.review as review
import agents.workstations as workstations
from agents.decisions.schemas import IntakeStrategy, Keyword, KeywordCategory
from ui import server


CLIENT = "NETSCOUT"
WORKSTATION_ID = "agency_dhs"
DHS_SCOPE = {"agencies": ["DHS"]}
ALL_SCOPE = {"all": True}


def _strategy(client_name: str = CLIENT, *, marker: str = "native") -> IntakeStrategy:
    return IntakeStrategy(
        client_name=client_name,
        pursuit_strategy=f"Protect the {marker} workstation boundary.",
        keywords=[Keyword(
            term=f"{marker} observability",
            category=KeywordCategory.CAPABILITY,
            rationale="Operator-reviewed capability term.",
        )],
        inferred_naics=["541512"],
        confidence=0.9,
        review_gate="Approve the exact workstation boundary.",
    )


def _write_packet(
    path: Path,
    *,
    scope: dict,
    revision: int,
    status: review.ReviewStatus = review.ReviewStatus.APPROVED,
    marker: str,
    client_name: str = CLIENT,
) -> review.ReviewPacket:
    packet = review.ReviewPacket(
        client_name=client_name,
        status=status,
        strategy=_strategy(client_name, marker=marker),
        reviewer_note=f"{marker} decision",
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        decided_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc),
        revision_count=revision,
        search_scope=scope,
    )
    path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


def _write_registry(review_dir: Path) -> None:
    (review_dir / "netscout.workstations.json").write_text(json.dumps({
        "schema_version": "1",
        "client_name": CLIENT,
        "workstations": [
            {"id": WORKSTATION_ID, "scope": DHS_SCOPE,
             "created_at": "2026-07-13T00:00:00+00:00",
             "created_from": "operator"},
            {"id": "all", "scope": ALL_SCOPE,
             "created_from": "legacy-current"},
        ],
    }), encoding="utf-8")


def _seed_native_coexistence(tmp_path: Path, monkeypatch):
    review_dir = tmp_path / "review"
    cleaned_dir = tmp_path / "cleaned"
    report_dir = tmp_path / "reports"
    for root in (review_dir, cleaned_dir, report_dir):
        root.mkdir()

    monkeypatch.setattr(review, "REVIEW_DIR", str(review_dir))
    monkeypatch.setattr(server, "REVIEW_DIR", str(review_dir))
    monkeypatch.setattr(server, "CLEANED_DIR", str(cleaned_dir))
    monkeypatch.setattr(server, "REPORT_DIR", str(report_dir))

    legacy = review_dir / "netscout.review.json"
    native = review_dir / "netscout.agency_dhs.review.json"
    _write_packet(
        legacy, scope=ALL_SCOPE, revision=91, marker="legacy-all")
    _write_packet(
        native, scope=DHS_SCOPE, revision=7, marker="native-dhs")
    _write_registry(review_dir)
    (review_dir / "netscout.agency_dhs.workstation_receipt.json").write_text(
        json.dumps({
            "schema_version": "1",
            "client_name": CLIENT,
            "workstation_id": WORKSTATION_ID,
            "scope": DHS_SCOPE,
            "clone_baseline": True,
            "created_at": "2026-07-13T00:00:00+00:00",
            "created_from": "operator",
            "source_packet_sha256": hashlib.sha256(
                legacy.read_bytes()).hexdigest(),
            "native_packet_sha256": hashlib.sha256(
                native.read_bytes()).hexdigest(),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return review_dir, cleaned_dir, report_dir, legacy, native


def _bind_native_owner(monkeypatch, review_dir: Path) -> None:
    ownership = workstations.native_workstation_ownership(
        CLIENT, WORKSTATION_ID, review_dir=review_dir)
    packet_path = review_dir / "netscout.agency_dhs.review.json"
    packet = review.ReviewPacket.model_validate_json(packet_path.read_bytes())
    monkeypatch.setenv("LILA_EXPECT_NATIVE_WORKSTATION", "1")
    monkeypatch.setenv(
        "LILA_EXPECT_PACKET_SHA256",
        hashlib.sha256(packet_path.read_bytes()).hexdigest())
    monkeypatch.setenv(
        "LILA_EXPECT_STRATEGY_REVISION", str(packet.revision_count))
    monkeypatch.setenv(
        "LILA_EXPECT_WORKSTATION_REGISTRY_SHA256",
        ownership.registry_sha256)
    monkeypatch.setenv(
        "LILA_EXPECT_WORKSTATION_RECEIPT_SHA256",
        ownership.receipt_sha256)


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        capability_terms=SimpleNamespace(core=[], adjacent=[]),
        named_competitors_and_incumbents=[],
        mission_components=[],
    )


class _InertFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _InertExecutor:
    """Record fan-out names but never call the submitted source function."""

    submitted: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def submit(self, _runner, name, _source_fn):
        self.submitted.append(name)
        return _InertFuture((name, {}, "mocked offline", 0.0, None))


def test_command_center_search_preserves_subject_timeout_and_resumes(tmp_path, monkeypatch):
    """Real route authorization + search main; source/model I/O is injected."""
    from agents.assess import investigations as worker
    from tools.capability import ClientProfile, CapabilityTerms
    import tools.capability as capability
    import tools.relevance.taxonomy as taxonomy
    import agents.decisions.research_picture as picture
    import run_searches
    review_dir, *_ = _seed_native_coexistence(tmp_path, monkeypatch)
    monkeypatch.setenv('LILA_EXPECT_CLIENT_NAME', CLIENT)
    monkeypatch.setenv('LILA_EXPECT_WORKSTATION_ID', WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)
    captured = {}
    _patch_offline_success(monkeypatch, captured)
    monkeypatch.setattr(capability, 'require_profile', lambda _: ClientProfile(
        client_name=CLIENT, naics_boundary=['541519'],
        capability_terms=CapabilityTerms(core=['data recovery'])))
    monkeypatch.setattr(taxonomy, 'load_taxonomy', lambda _: None)
    rows = [dict(source='agency_program_documents', tier='program', kind='budget',
        record_id=identifier, title='Data recovery', description='IT data recovery replacement is planned.',
        agency='Department of Homeland Security', component='U.S. Customs and Border Protection',
        canonical_url='https://www.dhs.gov/' + identifier,
        retrieved_at='2026-01-01T00:00:00Z', data_as_of='2026-01-01') for identifier in ('one', 'two')]
    class Sources(_InertExecutor):
        def submit(self, runner, name, source_fn):
            return _InertFuture((name, {'records': rows} if name == 'agency_program_documents' else {}, 'fixture', 0.0, None))
    monkeypatch.setattr(concurrent.futures, 'ThreadPoolExecutor', Sources)
    calls = []
    def synth(bundle):
        calls.append(bundle['source']['record_id'])
        if calls == ['one', 'two']:
            raise TimeoutError('forced one-subject timeout')
        return dict(decision='refresh_hold', buyer_need='Published IT recovery replacement plan',
            fit_hypothesis='Product fit remains unknown', why_now='Confirm current plan',
            route='Routing owner not established', rationale='Refresh current work status',
            first_question='Who owns this plan?', next_action='Research the technical owner',
            unknowns=['Current owner'], evidence=[{'evidence_id': bundle['evidence'][0]['evidence_id'],
                'quote': 'IT data recovery replacement is planned.'}])
    real_worker = worker.run_investigations
    monkeypatch.setattr(worker, 'run_investigations', lambda data, profile: real_worker(data, profile, synth=synth))
    monkeypatch.setattr(picture, 'compose_research_picture', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError('whole picture timeout')))
    monkeypatch.setattr(sys, 'argv', ['run_searches.py', '--client', CLIENT, '--skip', 'triage'])
    def launch(step, client, args):
        assert step == 'searches' and args['_lila_native_workstation']
        assert run_searches.main() == 0
        return 'fixture-command-center-job'
    monkeypatch.setattr(server, 'start_job', launch)
    request = {'client_name': CLIENT, 'step': 'searches', 'args': {'workstation_id': WORKSTATION_ID}}
    api = server.app.test_client()
    assert api.post('/api/run', json=request).status_code == 200
    first = captured['payload']['results']['upstream_investigations']
    assert [item['state'] for item in first['items']] == ['complete', 'failed']
    assert captured['payload']['results']['research_picture']['error'] == 'whole picture timeout'
    assert api.post('/api/run', json=request).status_code == 200
    resumed = captured['payload']['results']['upstream_investigations']
    assert calls == ['one', 'two', 'two']
    assert all(item['state'] == 'complete' for item in resumed['items'])


def _patch_offline_success(monkeypatch, captured: dict) -> None:
    """Keep a successful runner test entirely off network and off disk."""
    import tools.artifacts as artifacts
    import tools.assess_refresh as assess_refresh
    import tools.capability as capability
    import tools.contact_graph.harvest as contact_harvest
    import tools.snapshots as snapshots
    from tools.api.forecasts import coverage

    _InertExecutor.submitted = []
    monkeypatch.setattr(capability, "require_profile", lambda _client: _profile())
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _InertExecutor)
    monkeypatch.setattr(
        concurrent.futures, "as_completed", lambda futures: iter(futures))
    monkeypatch.setattr(coverage, "record_pull", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        contact_harvest, "harvest_from_results",
        lambda _results: SimpleNamespace(enabled=False),
    )
    monkeypatch.setattr(contact_harvest, "contact_graph_enabled", lambda: False)
    monkeypatch.setattr(
        snapshots, "save_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(snapshots, "sweep_slim_records", lambda _results: [])
    monkeypatch.setattr(
        assess_refresh, "refresh_current_assess_run_if_active",
        lambda *_args, **_kwargs: "no active pointer",
    )

    def capture(path, payload, *_args, **_kwargs):
        captured["path"] = str(path)
        captured["payload"] = payload

    monkeypatch.setattr(artifacts, "atomic_write_json", capture)


def test_native_packet_hash_and_child_binding_use_exact_packet_bytes(
        tmp_path, monkeypatch):
    review_dir, _cleaned, _reports, _legacy, native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)

    loaded = review.load_packet(CLIENT, review_dir=str(review_dir))
    assert loaded.revision_count == 7
    assert loaded.search_scope == DHS_SCOPE
    assert review.load_packet(
        CLIENT, workstation_id=WORKSTATION_ID,
        review_dir=str(review_dir)).revision_count == 7

    expected = hashlib.sha256(native.read_bytes()).hexdigest()
    assert review.packet_sha256(
        CLIENT, workstation_id=WORKSTATION_ID,
        review_dir=str(review_dir)) == expected

    # Even a semantically inert byte replacement is a different immutable
    # packet snapshot.  The runner must not stamp or spend against it.
    native.write_bytes(native.read_bytes() + b"\n")
    assert review.packet_sha256(
        CLIENT, workstation_id=WORKSTATION_ID,
        review_dir=str(review_dir)) != expected

    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "all")
    with pytest.raises(
            review.WorkstationBindingError,
            match="workstation|scope|binding"):
        review.load_packet(
            CLIENT, workstation_id=WORKSTATION_ID,
            review_dir=str(review_dir))


def test_api_run_authorizes_only_the_approved_native_workstation(
        tmp_path, monkeypatch):
    _review_dir, _cleaned, _reports, _legacy, native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(
        server, "start_job",
        lambda step, client, args: (
            launched.append((step, client, dict(args))) or "native-job"),
    )

    client = server.app.test_client()
    response = client.post("/api/run", json={
        "client_name": CLIENT,
        "step": "searches",
        "args": {"workstation_id": WORKSTATION_ID},
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["job_id"] == "native-job"
    assert len(launched) == 1
    step, launched_client, launched_args = launched[0]
    assert (step, launched_client) == ("searches", CLIENT)
    assert launched_args["workstation_id"] == WORKSTATION_ID
    assert launched_args["_lila_expected_strategy_revision"] == 7
    assert launched_args["_lila_native_workstation"] is True
    for key in (
        "_lila_expected_packet_sha256",
        "_lila_expected_workstation_registry_sha256",
        "_lila_expected_workstation_receipt_sha256",
    ):
        assert len(launched_args[key]) == 64

    _write_packet(
        native,
        scope=DHS_SCOPE,
        revision=8,
        status=review.ReviewStatus.PENDING,
        marker="native-pending",
    )
    response = client.post("/api/run", json={
        "client_name": CLIENT,
        "step": "searches",
        "args": {"workstation_id": WORKSTATION_ID},
    })
    assert response.status_code == 409, response.get_json()

    # Omission is ambiguous once both workstations exist, but an explicit All
    # request continues to address its own approved legacy-current boundary.
    response = client.post("/api/run", json={
        "client_name": CLIENT, "step": "searches", "args": {},
    })
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["error"] == (
        "workstation_id is required once a native workstation exists")
    response = client.post("/api/run", json={
        "client_name": CLIENT, "step": "searches",
        "args": {"workstation_id": "all"},
    })
    assert response.status_code == 200, response.get_json()
    assert launched[-1] == (
        "searches", CLIENT, {"workstation_id": "all"})
    assert len(launched) == 2


def test_legacy_all_coexists_after_native_dhs_creation(tmp_path, monkeypatch):
    review_dir, cleaned_dir, report_dir, _legacy, _native = (
        _seed_native_coexistence(tmp_path, monkeypatch))
    # A matching All sweep advances only the independent All workstation.  It
    # neither advances DHS nor turns Tranche-3 creation into migration.
    (cleaned_dir / "searches_netscout.json").write_text(json.dumps({
        "client": CLIENT,
        "strategy_revision": 91,
        "search_scope": ALL_SCOPE,
        "results": {},
    }), encoding="utf-8")

    catalog = workstations.discover_workstations(
        CLIENT,
        review_dir=review_dir,
        cleaned_dir=cleaned_dir,
        report_dir=report_dir,
    )
    rows = {row.ref.id: row for row in catalog.workstations}

    assert rows[WORKSTATION_ID].boundary_status == "approved"
    assert rows[WORKSTATION_ID].phase == "search"
    assert rows["all"].is_legacy_current is True
    assert rows["all"].boundary_status == "approved"
    assert rows["all"].phase == "assess"
    assert rows["all"].sweep_exists is True
    assert rows["all"].historical_sweep_exists is True
    assert rows["all"].historical_artifacts == 0


@pytest.mark.parametrize("packet_sha", [None, "0" * 64])
def test_native_sweep_requires_exact_packet_sha_without_timestamp_fallback(
        tmp_path, monkeypatch, packet_sha):
    review_dir, cleaned_dir, report_dir, _legacy, native = (
        _seed_native_coexistence(tmp_path, monkeypatch))
    monkeypatch.setattr(
        workstations, "_assess_status", lambda *_args: ("pending", []))
    sweep = {
        "client": CLIENT,
        "strategy_revision": 7,
        "search_scope": DHS_SCOPE,
        # A future timestamp would satisfy the legacy freshness adapter.  It
        # cannot substitute for the exact native packet fingerprint.
        "generated_at": "2099-01-01T00:00:00+00:00",
        "results": {},
    }
    if packet_sha is not None:
        sweep["strategy_packet_sha256"] = packet_sha
    sweep_path = cleaned_dir / "searches_netscout.agency_dhs.json"
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")

    def row():
        catalog = workstations.discover_workstations(
            CLIENT,
            review_dir=review_dir,
            cleaned_dir=cleaned_dir,
            report_dir=report_dir,
        )
        return next(
            item for item in catalog.workstations
            if item.ref.id == WORKSTATION_ID)

    held = row()
    assert held.phase == "search"
    assert held.sweep_status == "stale"
    assert "packet" in " ".join(held.diagnostics).lower()

    sweep["strategy_packet_sha256"] = hashlib.sha256(
        native.read_bytes()).hexdigest()
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")
    current = row()
    assert current.sweep_status == "current"
    assert current.phase == "assess"


def test_run_searches_emits_native_packet_sha_and_revision_without_real_fanout(
        tmp_path, monkeypatch):
    review_dir, _cleaned, _reports, _legacy, native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)
    captured = {}
    _patch_offline_success(monkeypatch, captured)

    import run_searches

    monkeypatch.setattr(
        sys, "argv",
        ["run_searches.py", "--client", CLIENT, "--skip", "triage", "picture"],
    )
    assert run_searches.main() == 0

    payload = captured["payload"]
    assert payload["strategy_revision"] == 7
    assert payload["strategy_packet_sha256"] == hashlib.sha256(
        native.read_bytes()).hexdigest()
    assert payload["search_scope"]["agencies"][0]["abbr"] == "DHS"
    assert captured["path"].endswith(
        "searches_netscout.agency_dhs.json")
    assert _InertExecutor.submitted  # source names were scheduled, never called


def test_run_searches_rejects_native_packet_replacement_before_fanout(
        tmp_path, monkeypatch):
    review_dir, _cleaned, _reports, _legacy, native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)

    import run_searches
    import tools.capability as capability

    def replace_packet_after_initial_snapshot(_client):
        # Same valid JSON, same scope, same revision.  Only the exact bytes
        # changed, which proves the runner rechecks the packet fingerprint and
        # does not rely on revision/scope alone.
        native.write_bytes(native.read_bytes() + b"\n")
        return _profile()

    monkeypatch.setattr(
        capability, "require_profile", replace_packet_after_initial_snapshot)

    def forbidden_fanout(*_args, **_kwargs):
        raise AssertionError("source fan-out reached after native packet drift")

    monkeypatch.setattr(
        concurrent.futures, "ThreadPoolExecutor", forbidden_fanout)
    monkeypatch.setattr(
        sys, "argv", ["run_searches.py", "--client", CLIENT])

    with pytest.raises(
            review.WorkstationBindingError,
            match="packet|fingerprint|strategy|boundary|changed"):
        run_searches.main()


def test_run_searches_rejects_workstation_id_without_owner_mode_flag(
        tmp_path, monkeypatch):
    _seed_native_coexistence(tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    monkeypatch.delenv("LILA_EXPECT_NATIVE_WORKSTATION", raising=False)

    import run_searches
    import tools.capability as capability

    monkeypatch.setattr(
        capability, "require_profile",
        lambda _client: (_ for _ in ()).throw(
            AssertionError("profile read crossed partial child binding")),
    )
    monkeypatch.setattr(
        concurrent.futures, "ThreadPoolExecutor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source fan-out crossed partial child binding")),
    )
    monkeypatch.setattr(
        sys, "argv", ["run_searches.py", "--client", CLIENT])

    with pytest.raises(review.WorkstationBindingError, match="owner-mode"):
        run_searches.main()


def test_run_searches_rejects_native_scope_mismatch_before_profile_or_fanout(
        tmp_path, monkeypatch):
    review_dir, _cleaned, _reports, _legacy, native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    # A packet found at the DHS-native path cannot claim All Federal.
    _write_packet(
        native, scope=ALL_SCOPE, revision=7, marker="wrong-scope")
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)

    import run_searches
    import tools.capability as capability

    monkeypatch.setattr(
        capability, "require_profile",
        lambda _client: (_ for _ in ()).throw(
            AssertionError("profile read reached after scope mismatch")),
    )
    monkeypatch.setattr(
        concurrent.futures, "ThreadPoolExecutor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source fan-out reached after scope mismatch")),
    )
    monkeypatch.setattr(
        sys, "argv", ["run_searches.py", "--client", CLIENT])

    with pytest.raises(
            review.WorkstationBindingError,
            match="workstation|scope|binding"):
        run_searches.main()


def test_native_search_writes_only_scope_owned_early_artifacts(
        tmp_path, monkeypatch):
    """Tranche 3 cannot mutate shared intelligence, Assess, or Target state."""
    review_dir, _cleaned, _reports, _legacy, _native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)

    captured = {}
    _patch_offline_success(monkeypatch, captured)

    import run_searches
    import agents.decisions.research_picture as research_picture
    import tools.assess_refresh as assess_refresh
    import tools.contact_graph.harvest as contact_harvest
    import tools.snapshots as snapshots

    # Redirect the runner-owned output roots to the test tree. The primary
    # sweep writer remains captured by _patch_offline_success.
    monkeypatch.setattr(run_searches, "__file__", str(tmp_path / "run_searches.py"))
    picture = research_picture.ResearchPicture(
        client_name=CLIENT,
        headline="DHS scope picture",
        top_opportunities=[],
        demand_signals=[],
        market_structure="No shared state required.",
        watchlist=[],
        next_action="Review this exact sweep.",
        gaps=[],
    )
    monkeypatch.setattr(
        research_picture, "compose_research_picture",
        lambda *_args, **_kwargs: picture,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("native search reached shared downstream state")

    monkeypatch.setattr(contact_harvest, "harvest_from_results", forbidden)
    monkeypatch.setattr(contact_harvest, "contact_graph_enabled", forbidden)
    monkeypatch.setattr(
        assess_refresh, "refresh_current_assess_run_if_active", forbidden)
    snapshot_keys = []
    monkeypatch.setattr(
        snapshots, "save_snapshot",
        lambda kind, key, rows: snapshot_keys.append((kind, key, rows)),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["run_searches.py", "--client", CLIENT, "--skip", "triage"],
    )

    assert run_searches.main() == 0
    review_root = tmp_path / "data" / "review"
    assert (review_root / "netscout.agency_dhs.research_picture.md").is_file()
    assert not (review_root / "netscout.research_picture.md").exists()
    assert snapshot_keys == [("sweep", "netscout.agency_dhs", [])]
    assert captured["path"].endswith("searches_netscout.agency_dhs.json")


def test_native_forecast_search_never_mutates_shared_forecast_state(
        tmp_path, monkeypatch):
    review_dir, _cleaned, _reports, _legacy, _native = _seed_native_coexistence(
        tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", WORKSTATION_ID)
    _bind_native_owner(monkeypatch, review_dir)

    import run_searches
    import tools.api.forecasts as forecasts
    import tools.assess_refresh as assess_refresh
    import tools.capability as capability
    import tools.contact_graph.harvest as contact_harvest
    import tools.snapshots as snapshots
    from agents.schemas import ForecastRecord

    monkeypatch.setattr(run_searches, "__file__", str(tmp_path / "run_searches.py"))
    monkeypatch.setattr(capability, "require_profile", lambda _client: _profile())

    record = ForecastRecord(
        source="fake_forecast",
        source_id="DHS-1",
        agency="Department of Homeland Security",
        component="CISA",
        title="Native network observability requirement",
        description="native network observability for the DHS boundary",
        naics_code="541512",
        url="https://example.gov/forecast/1",
        retrieved_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    detail_calls = []

    def enrich_matched(rows):
        detail_calls.append([row.source_id for row in rows])
        return [row.model_copy(update={
            "small_business_poc": "Official specialist <poc@example.gov>",
        }) for row in rows]

    source = SimpleNamespace(
        name="fake_forecast",
        forecast_agency="DHS",
        enabled=True,
        offline_safe=True,
        forecasts=lambda: [record],
        enrich_matched=enrich_matched,
    )
    monkeypatch.setattr(forecasts, "forecast_sources", lambda: [source])

    shared_calls: list[str] = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            shared_calls.append(name)
            raise AssertionError(f"native forecast reached shared {name}")
        return fail

    for name in (
        "store_upsert",
        "record_pull",
        "gaps_for",
        "load_latest_snapshot",
        "save_snapshot",
        "diff_snapshots",
    ):
        monkeypatch.setattr(forecasts, name, forbidden(name))

    monkeypatch.setattr(contact_harvest, "harvest_from_results", forbidden(
        "contact harvest"))
    monkeypatch.setattr(contact_harvest, "contact_graph_enabled", forbidden(
        "contact graph"))
    monkeypatch.setattr(
        assess_refresh, "refresh_current_assess_run_if_active",
        forbidden("assess pointer refresh"),
    )
    snapshot_keys: list[tuple[str, str, list]] = []
    monkeypatch.setattr(
        snapshots, "save_snapshot",
        lambda kind, key, rows: snapshot_keys.append((kind, key, rows)),
    )
    monkeypatch.setattr(snapshots, "sweep_slim_records", lambda _results: [])

    skipped = [
        "sam.gov", "usaspending.gov", "web", "federal_register", "news",
        "subawards", "contract_awards", "dod_contracts", "budget_pressure",
        "watchdogs", "congress", "kev", "regulations", "govinfo", "grants",
        "sbir", "dsip_topics", "gdelt", "treasury", "gao", "calc", "edgar", "fedramp",
        "hierarchy", "buyer_map", "funded_demand", "triage", "picture",
        "reginfo_unified_agenda", "darpa_opportunities",
        "dod_budget_exhibits", "foreign_assistance",
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_searches.py", "--client", CLIENT, "--skip", *skipped],
    )

    assert run_searches.main() == 0
    assert shared_calls == []
    assert snapshot_keys == [("sweep", "netscout.agency_dhs", [])]

    path = (tmp_path / "data" / "cleaned" /
            "searches_netscout.agency_dhs.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    signals = payload["results"]["forecast_signals"]
    assert signals["delta"] is None
    assert signals["events"] == []
    assert len(signals["matched"]) == 1
    assert signals["matched"][0]["source_id"] == "DHS-1"
    assert signals["matched"][0]["small_business_poc"] == (
        "Official specialist <poc@example.gov>")
    assert detail_calls == [["DHS-1"]]
