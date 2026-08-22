"""Cycle 4 T6: recurring, internal-only PROGRAM change digests."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.assess.approval import approve_assess_results
from agents.change_digest import (
    ChangeDigestError,
    build_and_persist_change_digest,
    build_change_digest,
    change_digest_path,
    persist_change_digest,
    validate_change_digest,
)
from agents.reports.horizon import current_horizon_binding
from tests.test_assess_ledger import (
    _horizon_payload,
    _sam_monitor_horizon_payload,
)


CLIENT = "Testco"
T0 = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


class _Profile:
    client_name = CLIENT

    def is_populated(self) -> bool:
        return True

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "client_name": CLIENT,
            "capability_terms": {"core": ["packet capture"]},
            "naics_boundary": ["541512"],
        }


def _sam(source_id: str, title: str, deadline: str = "2026-09-01") -> dict:
    return {
        "source": "sam.gov",
        "source_id": source_id,
        "title": title,
        "agency": "Department of Homeland Security",
        "response_deadline": deadline,
        "naics_code": "541512",
        "api_url": f"https://sam.gov/opp/{source_id}/view",
    }


def _forecast(source_id: str, title: str, timing: str) -> dict:
    return {
        "source": "dhs_apfs",
        "source_id": source_id,
        "title": title,
        "url": f"https://apfs-cloud.dhs.gov/forecast/{source_id}",
        "agency": "Department of Homeland Security",
        "component": "CISA",
        "anticipated_solicitation": timing,
        "estimated_value_range": "$5M to $10M",
    }


def _recompete(award_id: str, recipient: str, end: str, score: int) -> dict:
    return {
        "award_id": award_id,
        "internal_id": f"CONT_{award_id}",
        "recipient": recipient,
        "awarding_agency": "Department of Homeland Security",
        "awarding_office": "CISA",
        "pop_end": end,
        "score": score,
        "url": f"https://www.usaspending.gov/award/CONT_{award_id}",
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    cleaned_dir = tmp_path / "cleaned"
    state_dir = tmp_path / "change_digests"
    recompete_dir = tmp_path / "recompete"
    forecast_store_dir = tmp_path / "forecast_store"
    review_dir.mkdir()
    cleaned_dir.mkdir()
    recompete_dir.mkdir()
    forecast_store_dir.mkdir()
    monkeypatch.setenv("LILA_RECOMPETE_DIR", str(recompete_dir))
    monkeypatch.setenv("LILA_FORECAST_STORE_DIR", str(forecast_store_dir))
    profile = _Profile()
    monkeypatch.setattr(
        "tools.capability.load_profile", lambda _client: profile)
    _write_json(review_dir / "testco.review.json", {
        "client_name": CLIENT,
        "status": "approved",
        "search_scope": {"all": True},
    })
    sweep = {
        "client": CLIENT,
        "generated_at": T0.isoformat(),
        "search_scope": {"all": True},
        "results": {
            "sam.gov": [
                _sam("N1", "Network visibility"),
                _sam("N2", "Sensor refresh"),
                _sam("N4", "Packet capture sustainment"),
            ],
            "triage": {
                "N1": {"verdict": "monitor", "reason": "planning signal"},
                "N2": {"verdict": "discard", "reason": "outside core"},
                "N4": {"verdict": "monitor", "reason": "watch"},
            },
            "forecast_signals": {
                "matched": [
                    _forecast("F1", "CISA visibility", "Q4 FY2027"),
                    _forecast("F2", "CBP sensors", "Q1 FY2028"),
                    _forecast("F4", "CISA telemetry", "Q2 FY2028"),
                ],
                "events": [],
            },
        },
    }
    sweep_path = cleaned_dir / "searches_testco.json"
    _write_json(sweep_path, sweep)
    approve_assess_results(CLIENT, review_dir=str(review_dir))

    horizon = _horizon_payload()
    horizon["binding"] = current_horizon_binding(
        CLIENT,
        sweep_path=str(sweep_path),
        sweep=sweep,
        profile=profile,
    )
    _write_json(review_dir / "testco.horizon.json", horizon)
    _write_json(recompete_dir / "testco.json", {
        "client": CLIENT,
        "generated": T0.date().isoformat(),
        "population": 3,
        "attack": [
            _recompete("R1", "Prime One", "2027-03-01", 70),
            _recompete("R2", "Prime Two", "2027-06-01", 55),
            _recompete("R4", "Prime Four", "2027-09-01", 45),
        ],
        "defend": [],
    })
    _write_json(forecast_store_dir / "dhs_apfs.json", {
        source_id: {
            **_forecast(source_id, title, timing),
            "record_hash": f"hash-{source_id}",
            "first_seen": "2026-07-01",
            "last_seen": "2026-07-12",
        }
        for source_id, title, timing in (
            ("F1", "CISA visibility", "Q4 FY2027"),
            ("F2", "CBP sensors", "Q1 FY2028"),
            ("F4", "CISA telemetry", "Q2 FY2028"),
        )
    })
    return {
        "review_dir": review_dir,
        "state_dir": state_dir,
        "recompete_dir": recompete_dir,
        "forecast_store_dir": forecast_store_dir,
        "sweep": sweep,
        "sweep_path": sweep_path,
        "profile": profile,
        "horizon": horizon,
    }


def test_first_run_is_explicit_baseline_then_all_change_classes_reconcile(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    first, path = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    assert path == change_digest_path(CLIENT, state_dir=seed["state_dir"])
    assert first["baseline"] is True and first["since"] is None
    assert first["summary"].startswith("First-run baseline captured")
    assert all(row["record_role"] == "census_observation"
               and row["verdict_role"] == "review_metadata_only"
               for row in first["baselines"]["sam"])
    cursors = first["source_cursors"]
    assert cursors["scope_designator"] == "all"
    assert cursors["forecast_store"]["dhs_apfs"]["records_found"] == 3
    assert len(cursors["forecast_store"]["dhs_apfs"][
        "selected_records_sha256"]) == 64
    assert cursors["recompete"]["artifact"] == "testco.json"
    assert len(cursors["recompete"]["artifact_sha256"]) == 64
    for lane in first["lanes"].values():
        assert lane["tier"] == "program"
        assert not lane["entered"] and not lane["left"]
        assert not lane["moved"] and not lane["unchanged"]
    before_invalid_write = path.read_bytes()
    invalid = copy.deepcopy(first)
    invalid["visibility"] = "client"
    with pytest.raises(ChangeDigestError, match="internal-only"):
        persist_change_digest(invalid, state_dir=seed["state_dir"])
    assert path.read_bytes() == before_invalid_write

    sweep = seed["sweep"]
    sweep["generated_at"] = T1.isoformat()
    sweep["results"]["sam.gov"] = [
        _sam("N1", "Network visibility", "2026-08-15"),
        _sam("N3", "New telemetry planning"),
        _sam("N4", "Packet capture sustainment"),
    ]
    sweep["results"]["triage"] = {
        "N1": {"verdict": "pursue", "reason": "operator-reviewed depth"},
        "N3": {"verdict": "monitor", "reason": "new planning signal"},
        "N4": {"verdict": "monitor", "reason": "watch"},
    }
    sweep["results"]["forecast_signals"] = {
        "matched": [
            _forecast("F1", "CISA visibility", "Q2 FY2027"),
            _forecast("F3", "CISA packet capture", "Q3 FY2027"),
            _forecast("F4", "CISA telemetry", "Q2 FY2028"),
        ],
        "events": [{
            "kind": "date_moved_closer",
            "id": "F1",
            "title": "CISA visibility",
            "url": "https://apfs-cloud.dhs.gov/forecast/F1",
            "agency": "Department of Homeland Security",
            "component": "CISA",
            "detail": "solicitation timing moved closer: Q4 FY2027 -> Q2 FY2027",
        }],
    }
    _write_json(seed["sweep_path"], sweep)
    _write_json(seed["recompete_dir"] / "testco.json", {
        "client": CLIENT,
        "generated": T1.date().isoformat(),
        "population": 3,
        "attack": [
            _recompete("R1", "Prime One", "2027-01-15", 80),
            _recompete("R3", "Prime Three", "2027-08-01", 60),
            _recompete("R4", "Prime Four", "2027-09-01", 45),
        ],
        "defend": [],
    })

    second, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T1,
    )
    assert second["baseline"] is False
    assert second["since"] == first["generated_at"]

    sam = second["lanes"]["sam_census"]
    assert [row["id"] for row in sam["entered"]] == ["N3"]
    assert [row["id"] for row in sam["left"]] == ["N2"]
    assert {row["label"] for row in sam["moved"]} == {"deadline", "verdict"}
    assert [row["id"] for row in sam["unchanged"]] == ["N4"]
    approval = sam["approval_context"]
    assert approval["role"] == "census_context_only"
    assert approval["tier"] == "program"
    assert [row["id"] for row in approval["entered"]] == ["N3"]
    assert [row["id"] for row in approval["left"]] == ["N2"]
    assert [row["id"] for row in approval["changed"]] == ["N1"]

    forecast = second["lanes"]["forecast_store"]
    assert [row["id"] for row in forecast["entered"]] == ["dhs_apfs:F3"]
    assert [row["id"] for row in forecast["left"]] == ["dhs_apfs:F2"]
    assert [row["id"] for row in forecast["moved"]] == ["dhs_apfs:F1"]
    assert [row["id"] for row in forecast["unchanged"]] == ["dhs_apfs:F4"]
    assert forecast["event_count"] == 1
    assert forecast["events"][0]["tier"] == "program"

    recompete = second["lanes"]["recompete_calendar"]
    assert [row["id"] for row in recompete["entered"]] == ["R3"]
    assert [row["id"] for row in recompete["left"]] == ["R2"]
    assert {row["label"] for row in recompete["moved"]} == {
        "attack value", "period of performance end"}
    assert [row["id"] for row in recompete["unchanged"]] == ["R4"]

    history = path.parent / ".history" / path.name
    assert len(list(history.glob("*.json"))) >= 2


def test_horizon_signals_are_exact_bound_program_rows_and_never_invented(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    seed["horizon"]["fact_bank"].append({
        "id": "H2",
        "text": "GAO identified a CISA telemetry modernization control gap.",
        "source": "https://www.gao.gov/products/gao-26-100001",
        "kind": "oversight",
        "retrieved_at": T0.isoformat(),
        "scope": {
            "kind": "agency",
            "agencies": ["Department of Homeland Security"],
            "component": "CISA",
        },
    })
    _write_json(seed["review_dir"] / "testco.horizon.json", seed["horizon"])
    payload = build_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    horizon_rows = payload["baselines"]["horizon"]
    assert len(horizon_rows) == 2
    row = next(row for row in horizon_rows if row["evidence_id"] == "H1")
    fact = seed["horizon"]["fact_bank"][0]
    assert (row["evidence_id"], row["signal_text"], row["signal_source"],
            row["kind"], row["tier"]) == (
                fact["id"], fact["text"], fact["source"],
                fact["kind"], "program")

    invented = copy.deepcopy(payload)
    invented_row = next(
        row for row in invented["baselines"]["horizon"]
        if row["evidence_id"] == "H1")
    invented_row["signal_text"] = \
        "Invented procurement claim"
    problems = validate_change_digest(
        invented, horizon_payload=seed["horizon"])
    assert any("not an exact PROGRAM fact-bank row" in problem
               for problem in problems)

    client_visible = copy.deepcopy(payload)
    client_visible["visibility"] = "client"
    assert "change digest must remain internal-only" in \
        validate_change_digest(client_visible)

    posting_claim = copy.deepcopy(payload)
    posting_claim["lanes"]["forecast_store"]["framing"] = \
        "The solicitation has definitely posted for immediate pursuit."
    assert any("unverified posting" in problem
               for problem in validate_change_digest(posting_claim))


def test_horizon_recomposition_item_ids_do_not_create_signal_motion(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    first, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    assert len(first["baselines"]["horizon"]) == 1

    horizon = copy.deepcopy(seed["horizon"])
    horizon["set"]["items"][0]["id"] = "reorganized-cisa-thesis"
    horizon["set"]["items"][0]["title"] = "Reorganized analyst framing"
    _write_json(seed["review_dir"] / "testco.horizon.json", horizon)
    second, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T1,
    )
    lane = second["lanes"]["horizon_signals"]
    assert not lane["entered"] and not lane["left"] and not lane["moved"]
    assert len(lane["unchanged"]) == 1
    assert second["baselines"]["horizon"] \
        == first["baselines"]["horizon"]


def test_first_available_lane_is_baselined_without_fabricated_entry_motion(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    forecast = copy.deepcopy(seed["sweep"]["results"]["forecast_signals"])
    seed["sweep"]["results"]["forecast_signals"] = {
        "disabled": True,
        "note": "collector intentionally unavailable for fixture",
    }
    _write_json(seed["sweep_path"], seed["sweep"])
    first, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    assert first["lanes"]["forecast_store"]["state"] == "unavailable"
    assert first["source_cursors"]["lane_observed"]["forecast"] is False
    assert first["baselines"]["forecast"] == []

    seed["sweep"]["results"]["forecast_signals"] = forecast
    _write_json(seed["sweep_path"], seed["sweep"])
    second, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T1,
    )
    lane = second["lanes"]["forecast_store"]
    assert second["baseline"] is False
    assert lane["state"] == "baseline"
    assert lane["population"] == 3
    assert not lane["entered"] and not lane["left"] and not lane["moved"]
    assert second["source_cursors"]["lane_observed"]["forecast"] is True


def test_scope_change_rebaselines_every_lane_without_cross_scope_motion(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    first, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )

    _write_json(seed["review_dir"] / "testco.review.json", {
        "client_name": CLIENT,
        "status": "approved",
        "search_scope": {"agencies": ["DHS"]},
    })
    scoped = copy.deepcopy(seed["sweep"])
    scoped["generated_at"] = T1.isoformat()
    scoped["search_scope"] = {
        "mode": "focus",
        "agencies": [{
            "abbr": "DHS",
            "name": "Department of Homeland Security",
        }],
    }
    scoped["results"]["sam.gov"] = [
        _sam("N9", "DHS scoped planning record")]
    scoped["results"]["triage"] = {
        "N9": {"verdict": "monitor", "reason": "scope fixture"}}
    scoped_path = seed["sweep_path"].parent / \
        "searches_testco.agency_dhs.json"
    _write_json(scoped_path, scoped)
    horizon = copy.deepcopy(seed["horizon"])
    horizon["binding"] = current_horizon_binding(
        CLIENT,
        sweep_path=str(scoped_path),
        sweep=scoped,
        profile=seed["profile"],
    )
    _write_json(seed["review_dir"] / "testco.horizon.json", horizon)

    second, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=scoped_path,
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T1,
    )
    assert second["baseline"] is True
    assert second["baseline_reason"] == "scope_changed"
    assert second["since"] == first["generated_at"]
    assert second["source_cursors"]["scope_designator"] == "agency_dhs"
    assert "no cross-scope movement is asserted" in second["summary"]
    for lane in second["lanes"].values():
        assert lane["state"] == "baseline"
        assert not lane["entered"] and not lane["left"]
        assert not lane["moved"] and not lane["unchanged"]
        assert not lane.get("events")
    approval = second["lanes"]["sam_census"]["approval_context"]
    assert not approval["entered"] and not approval["left"]
    assert "not mixed" in approval["summary"]


def test_notice_tier_horizon_signal_is_excluded_from_program_digest(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    horizon = _sam_monitor_horizon_payload()
    horizon["binding"] = current_horizon_binding(
        CLIENT,
        sweep_path=str(seed["sweep_path"]),
        sweep=seed["sweep"],
        profile=seed["profile"],
    )
    _write_json(seed["review_dir"] / "testco.horizon.json", horizon)
    payload = build_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    assert payload["baselines"]["horizon"] == []
    assert payload["lanes"]["horizon_signals"]["population"] == 0
    assert "sam.gov/opp/N3" not in json.dumps(payload)


@pytest.mark.parametrize("bad_client", [None, "Other Client"])
def test_digest_requires_exact_sweep_client(
        tmp_path, monkeypatch, bad_client):
    seed = _seed(tmp_path, monkeypatch)
    if bad_client is None:
        seed["sweep"].pop("client")
    else:
        seed["sweep"]["client"] = bad_client
    _write_json(seed["sweep_path"], seed["sweep"])
    with pytest.raises(ChangeDigestError, match="another client"):
        build_change_digest(
            CLIENT,
            sweep_path=seed["sweep_path"],
            review_dir=seed["review_dir"],
            state_dir=seed["state_dir"],
            generated_at=T0,
        )


def test_explicit_sweep_scope_must_match_operator_gate(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    _write_json(seed["review_dir"] / "testco.review.json", {
        "client_name": CLIENT,
        "status": "approved",
        "search_scope": {"agencies": ["DHS"]},
    })
    with pytest.raises(
            ChangeDigestError,
            match="sweep scope all does not match operator gate agency_dhs"):
        build_change_digest(
            CLIENT,
            sweep_path=seed["sweep_path"],
            review_dir=seed["review_dir"],
            state_dir=seed["state_dir"],
            generated_at=T0,
        )


def test_cross_client_recompete_calendar_is_unavailable_not_compared(
        tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    calendar_path = seed["recompete_dir"] / "testco.json"
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
    calendar["client"] = "Other Client"
    _write_json(calendar_path, calendar)
    payload = build_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    lane = payload["lanes"]["recompete_calendar"]
    assert lane["state"] == "unavailable"
    assert "another client" in lane["summary"]
    assert payload["baselines"]["recompete"] == []
    assert payload["source_cursors"]["lane_observed"]["recompete"] is False


def test_unchanged_reconciliation_uses_source_and_source_id_identity():
    from agents.change_digest import _lane_delta
    from tools.snapshots import diff_snapshots

    previous = [
        {"source": "source-a", "source_id": "SAME", "title": "Alpha",
         "url": "https://a.gov/record", "window": "Q4"},
        {"source": "source-b", "source_id": "SAME", "title": "Bravo",
         "url": "https://b.gov/record", "window": "Q4"},
    ]
    current = copy.deepcopy(previous)
    current[0]["window"] = "Q2"
    delta = _lane_delta(
        previous,
        current,
        engine=diff_snapshots,
        compare={"window": "window"},
    )
    assert [(row["source"], row["id"]) for row in delta["moved"]] \
        == [("source-a", "SAME")]
    assert [(row["source"], row["id"]) for row in delta["unchanged"]] \
        == [("source-b", "SAME")]


def test_official_titles_do_not_trigger_authored_claim_lint_and_store_is_read_only(
        tmp_path, monkeypatch):
    import tools.api.forecasts.store as forecast_store

    seed = _seed(tmp_path, monkeypatch)
    seed["sweep"]["results"]["sam.gov"][0]["title"] = \
        "Solicitation released by the source system"
    _write_json(seed["sweep_path"], seed["sweep"])

    store_dir = tmp_path / "forecast_store"
    store_dir.mkdir(exist_ok=True)
    store_file = store_dir / "dhs_apfs.json"
    store_file.write_text('{"F1":{"record_hash":"unchanged"}}\n',
                          encoding="utf-8")
    before = store_file.read_bytes()
    monkeypatch.setenv("LILA_FORECAST_STORE_DIR", str(store_dir))
    calls = []
    original_load = forecast_store.load_store
    monkeypatch.setattr(
        forecast_store,
        "load_store",
        lambda source: calls.append(source) or original_load(source),
    )
    monkeypatch.setattr(
        forecast_store,
        "upsert",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("change digest must never mutate forecast store")),
    )

    payload = build_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    sam_titles = [row["title"] for row in payload["baselines"]["sam"]]
    assert "Solicitation released by the source system" in sam_titles
    assert calls == ["dhs_apfs"]
    assert store_file.read_bytes() == before
    assert list(store_dir.iterdir()) == [store_file]


def test_control_room_surface_is_internal_and_run_contract_is_unchanged(
        tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    del flask
    import ui.server as server
    from agents.reports.views import render_assessment
    from tests.test_views import _doc

    seed = _seed(tmp_path, monkeypatch)
    payload, _ = build_and_persist_change_digest(
        CLIENT,
        sweep_path=seed["sweep_path"],
        review_dir=seed["review_dir"],
        state_dir=seed["state_dir"],
        generated_at=T0,
    )
    monkeypatch.setattr(server, "REVIEW_DIR", str(seed["review_dir"]))
    monkeypatch.setattr(server, "CHANGE_DIGEST_DIR", str(seed["state_dir"]))
    client = server.app.test_client()
    read = client.get("/api/client/testco/change-digest")
    assert read.status_code == 200
    body = read.get_json()
    assert body["built"] is True
    assert body["visibility"] == "internal_only"
    assert "baselines" not in body
    assert body["summary"] == payload["summary"]

    monkeypatch.setattr(
        server, "load_packet",
        lambda _client, **_kwargs: SimpleNamespace(
            status=server.ReviewStatus.APPROVED,
            search_scope={"all": True},
        ),
    )
    launched = []
    monkeypatch.setattr(
        server,
        "start_job",
        lambda step, name, args: launched.append((step, name, args)) or "job-1",
    )
    run = client.post("/api/run", json={
        "client_name": CLIENT,
        "step": "change_digest",
        "args": {},
    })
    assert run.status_code == 200
    assert run.get_json() == {"job_id": "job-1"}
    assert launched == [(
        "change_digest", CLIENT, {"workstation_id": "all"})]
    command = server._step_cmd("change_digest", CLIENT, {})
    assert command[1:] == ["-m", "agents.change_digest", "--client", CLIENT]

    source = Path(server.ROOT) / "ui" / "index.html"
    ui_text = source.read_text(encoding="utf-8")
    assert "Intelligence change digest · internal" in ui_text
    assert "runJob('change_digest', {}, card, run)" in ui_text
    assert "].slice(0, 5)" not in ui_text

    monkeypatch.setattr("tools.capability.load_profile", lambda _client: None)
    document = _doc()
    for view in ("client", "sales"):
        rendered = render_assessment(document, view)
        assert "Intelligence change digest" not in rendered
        assert "change-digest" not in rendered
