"""Live provider tests (Phase 3, 2026-07-24). Offline: every seam stubbed.

The live vehicle searcher translates query specs into official API calls,
stashes collection-time FetchedRecords quantized to the run as_of, and the
press replays them so current notices survive the 24h window.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_candidate_review as driver
import run_candidate_review_watch as watch_cli
import agents.candidate_review_v1.live_providers as live_mod
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateReviewDocument,
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
)
from agents.candidate_review_v1.live_providers import (
    load_live_provider_runtime,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleQuerySpec,
    VehicleTargetKind,
    VehicleWatchLane,
)
from agents.candidate_review_v1.verification import (
    _mint_vehicle_evidence,
    _mint_vehicle_signal,
)

_NOW = datetime(2026, 7, 24, 16, 0, tzinfo=timezone.utc)
_H = "a" * 64
_BINDING = ArtifactBinding(
    client_id="testco", client_name="TestCo", run_id="run-1",
    scope_designator="all_federal", scope_sha256=_H,
    profile_sha256="b" * 64, evidence_snapshot_sha256="c" * 64)
_WIRING_PATH = Path(__file__).parent / "test_candidate_review_v1_wiring.py"


def _query(lane, kind, value=None, identity=None, query_id="vq-1"):
    return SimpleNamespace(
        lane=lane, target_kind=kind, target_value=value,
        target_source_identity=identity, query_id=query_id, required=True)


def _sam_row(notice_id="SAMLIVE1", title="Enterprise Network Observability",
             deadline="2026-08-21"):
    return SimpleNamespace(
        source_id=notice_id, title=title,
        agency="US Army Corps of Engineers",
        response_deadline=deadline,
        api_url=f"https://sam.gov/opp/{notice_id}/view",
        raw_payload={
            "active": "Yes", "solicitationNumber": "W912DY26R0055",
            "description": "The Government requires network monitoring."})


class _FakeSam:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def search(self, source_query):
        self.queries.append(source_query)
        return self.rows


def _runtime(sam=None, award=None, guard=None):
    return load_live_provider_runtime(
        binding=_BINDING, as_of=_NOW,
        sam_source=sam,
        award_fetch=award or (lambda url: (_ for _ in ()).throw(RuntimeError())),
        quota_guard=guard or (lambda purpose: True))


def test_sam_lane_translates_terms_and_stashes_verified_notices():
    sam = _FakeSam([_sam_row()])
    runtime = _runtime(sam=sam)
    response = runtime.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="network observability"))
    assert response.state.value == "returned"
    assert len(response.leads) == 1
    assert sam.queries[0].keywords == ["network observability"]
    assert sam.queries[0].deadline_from == _NOW.date()
    key = response.leads[0].source_identity.canonical_key
    record = runtime.records[key]
    assert record.fetched_at == _NOW and record.verified_at == _NOW
    assert record.notice_status == NoticeStatus.ACTIVE
    assert record.notice_role == NoticeRole.END_USER_REQUIREMENT
    assert "Agency: US Army Corps of Engineers." in record.text
    assert "Responses are due August 21, 2026." in record.text
    assert runtime.extra_artifacts()


def test_on_ramp_title_gets_vehicle_role_and_labeled_line():
    sam = _FakeSam([_sam_row(notice_id="ONRAMPLIVE",
                             title="Polaris GWAC On-Ramp")])
    runtime = _runtime(sam=sam)
    response = runtime.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="polaris"))
    record = runtime.records[response.leads[0].source_identity.canonical_key]
    assert record.notice_role == NoticeRole.VEHICLE_ON_RAMP
    assert "Vehicle: Polaris." in record.text


def test_quota_exhaustion_and_missing_key_are_typed_not_run(monkeypatch):
    # Hermetic against the operator's real .env (2026-07-24): the keyless
    # branch must see no ambient SAM key, or main's environment turns this
    # case into a live-source build (which is correct product behavior).
    monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
    sam = _FakeSam([_sam_row()])
    exhausted = _runtime(sam=sam, guard=lambda purpose: False)
    r1 = exhausted.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="x"))
    assert r1.state.value == "not_run" and "budget" in r1.public_detail
    assert sam.queries == []
    keyless = _runtime(sam=None)
    r2 = keyless.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="x"))
    # 2026-07-25: keyless routes to the sweep screen; with no sweep either,
    # the lane is a typed not-run naming both absences.
    assert r2.state.value == "not_run" and "sweep" in r2.public_detail
    r3 = _runtime(sam=sam).vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CANDIDATE_SOURCE,
        identity=SourceIdentity(source_system="sam.gov", record_id="N1")))
    assert r3.state.value == "not_run"


_AWARD_PAYLOAD = {
    "piid": "TO-77",
    "description": "Network performance management services.",
    "period_of_performance": {"end_date": "2027-03-31"},
    "parent_award": {
        "piid": "IDV-9000",
        "generated_unique_award_id": "CONT_IDV_9000",
    },
    "awarding_agency": {"toptier_agency": {"name": "Department of the Navy"}},
}


def test_usaspending_lineage_lead_survives_the_c1_signal_handshake():
    runtime = _runtime(award=lambda url: dict(_AWARD_PAYLOAD))
    response = runtime.vehicle_searcher(_query(
        VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
        VehicleTargetKind.CANDIDATE_SOURCE,
        identity=SourceIdentity(
            source_system="usaspending.gov", record_id="CONT_AWD_77")))
    assert response.state.value == "returned"
    lead = response.leads[0]
    assert lead.order_identity == lead.source_identity
    assert lead.parent_idv_identity.record_id == "CONT_IDV_9000"
    record = runtime.records[lead.source_identity.canonical_key]
    assert "Agency: Department of the Navy." in record.text
    assert ("Task order TO-77 on award record CONT_AWD_77 uses "
            "parent IDV IDV-9000") in record.text
    assert "The period of performance ends March 31, 2027." in record.text
    minted = _mint_vehicle_evidence(_BINDING, _NOW, lead, record, {})
    signal, skip = _mint_vehicle_signal(_BINDING, _NOW, lead, minted)
    assert skip is None and signal is not None
    assert signal.kind.value == "task_order_activity"


def test_award_endpoint_failure_is_typed_failed():
    runtime = _runtime()
    response = runtime.vehicle_searcher(_query(
        VehicleWatchLane.USASPENDING_IDV,
        VehicleTargetKind.KNOWN_PARENT_IDV,
        identity=SourceIdentity(
            source_system="usaspending.gov", record_id="CONT_IDV_X")))
    assert response.state.value == "failed"
    assert "award endpoint" in response.public_detail


def test_unwired_lanes_are_typed_not_run():
    runtime = _runtime(sam=_FakeSam([]))
    for lane in (VehicleWatchLane.GSA_PROGRAM_RECORDS,
                 VehicleWatchLane.OFFICIAL_AGENCY_PROGRAM_FORECAST,
                 VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT):
        response = runtime.vehicle_searcher(_query(
            lane, VehicleTargetKind.CLIENT_TERM, value="x"))
        assert response.state.value == "not_run"
        assert "not" in response.public_detail


def _wiring():
    spec = importlib.util.spec_from_file_location("_crv1_wiring", _WIRING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_watch_persists_records_and_the_press_replays_current_notices(
        tmp_path, monkeypatch):
    # 2026-07-24 THE 24h TRAP, closed end to end: a live watch stashes the
    # verified notice with the run as_of; the press auto-replays it and the
    # notice becomes a CURRENT candidate without any press-time network.
    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    sam = _FakeSam([_sam_row()])
    real_loader = live_mod.load_live_provider_runtime

    def _stubbed(**kwargs):
        return real_loader(
            **kwargs, sam_source=sam,
            award_fetch=lambda url: dict(_AWARD_PAYLOAD),
            quota_guard=lambda purpose: True)

    monkeypatch.setattr(live_mod, "load_live_provider_runtime", _stubbed)
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    watch_cli.run_watch_generation(
        "riverbed", root=tmp_path, registry_path=registry,
        state_root=state_root, live=True)
    assert sam.queries, "the live SAM lane never ran"

    # 2026-07-25 (the operator's wall): a second live pull the same day on
    # the same sweep is a NEW observation and must persist a NEW generation
    # instead of colliding with the snapshot-diff basis guard.
    import time as _time
    _time.sleep(1.1)
    watch_cli.run_watch_generation(
        "riverbed", root=tmp_path, registry_path=registry,
        state_root=state_root, live=True)

    qa_path = driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root,
        certified_at=datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc))
    assert qa_path.is_file()
    document = CandidateReviewDocument.model_validate_json(
        (state_root / "riverbed" / "riverbed.candidate_review.document.json")
        .read_text(encoding="utf-8"))
    kinds = [item.kind.value for item in document.candidates]
    assert "current_notice" in kinds

    # 2026-07-24: the Command Center calendar serves the same projection.
    flask = pytest.importorskip("flask")
    _ = flask
    import ui.server as srv
    monkeypatch.setattr(srv, "ROOT", str(tmp_path))
    monkeypatch.setitem(srv.app.config, "TESTING", True)
    response = srv.app.test_client().get("/api/client/riverbed/calendar")
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert items, "the pressed document carries dated items"
    assert any(item["date"] == "2026-08-21" for item in items)
    assert all(
        {"date", "title", "agency", "status", "kind", "origin"}
        <= set(item) for item in items)

    # The pinned book-of-business views aggregate the same press.
    everything = srv.app.test_client().get("/api/calendar").get_json()
    assert any(item["client_name"] == "Riverbed"
               for item in everything["items"])
    targets = srv.app.test_client().get("/api/targets").get_json()
    riverbed = next(client for client in targets["clients"]
                    if client["client_id"] == "riverbed")
    assert riverbed["candidates"], "the pressed candidates are the targets"


def test_award_agency_extraction_tries_every_published_shape():
    # 2026-07-25: real /awards/ payloads nest the agency name differently by
    # award type; the labeled Agency line must survive every shape so the
    # author never falls back to the record id as an agency.
    base = {key: value for key, value in _AWARD_PAYLOAD.items()
            if key != "awarding_agency"}
    shapes = [
        {"awarding_agency": {"toptier_agency": {"name": "General Services Administration"}}},
        {"awarding_agency": {"subtier_agency": {"name": "Federal Acquisition Service"}}},
        {"awarding_agency": {"name": "General Services Administration"}},
        {"funding_agency": {"toptier_agency": {"name": "NASA"}}},
    ]
    for index, shape in enumerate(shapes):
        payload = {**base, **shape}
        runtime = _runtime(award=lambda url, p=payload: dict(p))
        response = runtime.vehicle_searcher(_query(
            VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
            VehicleTargetKind.CANDIDATE_SOURCE,
            identity=SourceIdentity(
                source_system="usaspending.gov",
                record_id=f"CONT_AWD_SHAPE{index}"),
            query_id=f"vq-shape-{index}"))
        lead = response.leads[0]
        record = runtime.records[lead.source_identity.canonical_key]
        assert "Agency: " in record.text, shape


def _sweep_row(notice_id="EXTRACT1", title="Enterprise Network Observability",
               deadline="2026-08-21", naics="541512"):
    return {
        "source": "sam.gov", "source_id": notice_id, "title": title,
        "agency": "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION",
        "naics_code": naics, "psc_code": "DA10",
        "response_deadline": deadline,
        "api_url": f"https://sam.gov/workspace/contract/opp/{notice_id}/view",
        "raw_payload": {"active": "Yes", "solicitationNumber": "80NSSC1",
                        "description": "NASA requires network monitoring."}}


def test_sam_lane_screens_the_sweep_when_the_metered_lane_is_unavailable():
    # 2026-07-25 the operator's spine: the daily-extract rows in the sweep
    # are official SAM records; with no key or budget the lane screens them
    # quota-free, and the snapshot instant is the verification time.
    snapshot = _NOW - timedelta(hours=3)
    runtime = load_live_provider_runtime(
        binding=_BINDING, as_of=_NOW, sam_source=None,
        award_fetch=lambda url: {}, quota_guard=lambda purpose: True,
        sweep_loader=lambda: ((_sweep_row(),), snapshot))
    response = runtime.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="network monitoring"))
    assert response.state.value == "returned"
    assert response.records_returned == 1
    assert "quota-free" in response.public_detail
    lead = response.leads[0]
    record = runtime.records[lead.source_identity.canonical_key]
    assert record.verified_at == snapshot
    assert record.notice_status == NoticeStatus.ACTIVE
    assert "Agency: NATIONAL AERONAUTICS" in record.text
    assert "Responses are due August 21, 2026." in record.text

    minted = _mint_vehicle_evidence(_BINDING, _NOW, lead, record, {})
    assert minted.confirms_open_notice is True

    naics_hit = runtime.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.NAICS, value="541512", query_id="vq-naics"))
    assert naics_hit.records_returned == 1
    miss = runtime.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="quantum radar",
        query_id="vq-miss"))
    assert miss.records_returned == 0


def test_sweep_screen_skips_dead_inventory_and_absent_sweeps():
    runtime = load_live_provider_runtime(
        binding=_BINDING, as_of=_NOW, sam_source=None,
        award_fetch=lambda url: {}, quota_guard=lambda purpose: True,
        sweep_loader=lambda: ((_sweep_row(deadline="2020-01-01"),),
                              _NOW - timedelta(hours=3)))
    stale = runtime.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="network monitoring"))
    assert stale.records_returned == 0
    absent = load_live_provider_runtime(
        binding=_BINDING, as_of=_NOW, sam_source=None,
        award_fetch=lambda url: {}, quota_guard=lambda purpose: True,
        sweep_loader=lambda: ((), None))
    response = absent.vehicle_searcher(_query(
        VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
        VehicleTargetKind.CLIENT_TERM, value="anything"))
    assert response.state.value == "not_run"
