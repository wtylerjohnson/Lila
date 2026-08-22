"""Read-only assessment status on lifecycle cards, radar API, and ticker."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

flask = pytest.importorskip("flask")

import ui.lifecycle as lc  # noqa: E402
import ui.server as srv  # noqa: E402
from agents.assessment_chain import (  # noqa: E402
    _sweep_basis_sha,
    relevance_receipt_valid,
)
from test_lifecycle_stages import (  # noqa: E402
    TODAY,
    _mk_profile,
    _mk_sweep,
    book,  # noqa: F401
)


def _write_waiting(root):
    state_dir = root / "data" / "state" / "assessment"
    state_dir.mkdir(parents=True, exist_ok=True)
    at = "2026-07-18T12:00:00+00:00"
    (state_dir / "testco.state.json").write_text(json.dumps({
        "version": 1,
        "slug": "testco",
        "mode": "assessment",
        "state": "AWAITING_TAXONOMY_GO",
        "failure": None,
        "transitions": [
            {"state": "INTAKE_DONE", "at": "2026-07-18T11:58:00+00:00",
             "by": "intake-owner", "note": "intake complete"},
            {"state": "SWEEPING", "at": "2026-07-18T11:58:01+00:00",
             "by": "assessment-chain", "note": "sweep"},
            {"state": "RELEVANCE", "at": "2026-07-18T11:59:00+00:00",
             "by": "assessment-chain", "note": "relevance"},
            {"state": "AWAITING_TAXONOMY_GO", "at": at,
             "by": "assessment-chain", "note": "GO required"},
        ],
    }), encoding="utf-8")
    return at


def test_lifecycle_stage_and_chain_status_remain_separate(book):  # noqa: F811
    _mk_profile(book)
    _mk_sweep(book)
    since = _write_waiting(book)

    card = lc.derive_stage("testco", today=TODAY)

    assert card["stage"] == "swept"
    assert card["assessment"] == {
        "slug": "testco",
        "state": "AWAITING_TAXONOMY_GO",
        "since": since,
        "alert_line": "testco awaiting your keyword review",
    }


def test_radar_api_and_ticker_expose_one_checkpoint(book):  # noqa: F811
    _mk_profile(book)
    _mk_sweep(book)
    _write_waiting(book)

    payload = srv.app.test_client().get("/api/radar").get_json()

    assessment = payload["cards"][0]["assessment"]
    assert set(assessment) == {"slug", "state", "since", "alert_line"}
    checkpoints = [row for row in payload["ticker"]["items"]
                   if row["kind"] == "assessment_checkpoint"]
    assert checkpoints == [{
        "kind": "assessment_checkpoint",
        "slug": "testco",
        "client_name": "Testco",
        "text": "awaiting your keyword review",
        "when": "2026-07-18",
    }]


def test_in_progress_state_is_visible_without_becoming_a_ticker_alert(
        book):  # noqa: F811
    _mk_profile(book)
    _write_waiting(book)
    path = book / "data" / "state" / "assessment" / "testco.state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["state"] = "SWEEPING"
    state["transitions"] = state["transitions"][:2]
    path.write_text(json.dumps(state), encoding="utf-8")

    payload = srv.app.test_client().get("/api/radar").get_json()

    assert payload["cards"][0]["assessment"] == {
        "slug": "testco",
        "state": "SWEEPING",
        "since": "2026-07-18T11:58:01+00:00",
        "alert_line": None,
    }
    assert all(row["kind"] != "assessment_checkpoint"
               for row in payload["ticker"]["items"])


def test_draft_ready_remains_visible_without_permanent_ticker_alert(
        book):  # noqa: F811
    _mk_profile(book)
    _write_waiting(book)
    path = book / "data" / "state" / "assessment" / "testco.state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["transitions"].extend([
        {"state": "AWAITING_TAXONOMY_GO",
         "at": "2026-07-18T12:00:01+00:00", "by": "reviewer",
         "note": "taxonomy GO recorded"},
        {"state": "COMPOSING", "at": "2026-07-18T12:00:02+00:00",
         "by": "assessment-chain",
         "note": "taxonomy GO accepted from reviewer at "
                 "2026-07-18T12:00:01+00:00; C1 composer started"},
        {"state": "REPULLING", "at": "2026-07-18T12:00:03+00:00",
         "by": "assessment-chain", "note": "composition complete"},
        {"state": "GATING_PRESS", "at": "2026-07-18T12:00:04+00:00",
         "by": "assessment-chain", "note": "repull complete"},
        {"state": "DRAFT_READY", "at": "2026-07-18T12:00:05+00:00",
         "by": "assessment-chain", "note": "press complete"},
    ])
    state["state"] = "DRAFT_READY"
    path.write_text(json.dumps(state), encoding="utf-8")

    payload = srv.app.test_client().get("/api/radar").get_json()

    assert payload["cards"][0]["assessment"] == {
        "slug": "testco", "state": "DRAFT_READY",
        "since": "2026-07-18T12:00:05+00:00", "alert_line": None,
    }
    assert all(row["kind"] != "assessment_checkpoint"
               for row in payload["ticker"]["items"])


def test_clean_relevance_receipt_counts_as_output(book):  # noqa: F811
    _mk_profile(book)
    _mk_sweep(book)
    relevance = book / "data" / "state" / "relevance"
    calibration = relevance / "testco.calibration.csv"
    calibration.write_text(
        "client,record_ref,kind,title,pipeline_status,engine_score,relevant,"
        "off_code,core_terms,adjacent_terms,matched_spans,taxonomy,scope_basis,"
        "disagreement,would_be_score\n",
        encoding="utf-8",
    )
    sweep = book / "data" / "cleaned" / "searches_testco.json"
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    input_paths = {
        "taxonomy": book / "clients" / "testco" / "capability_taxonomy.json",
        "profile": book / "clients" / "testco" / "profile.json",
        "engagement_scope": book / "clients" / "testco" /
                            "engagement_scope.json",
        "signal_board_content": book / "clients" / "testco" /
                                "signal_board_content.json",
        "scope_presets": book / "data" / "reference" / "scope_presets.json",
    }
    inputs = {
        name: {"path": str(path),
               "sha256": digest(path) if path.exists() else None}
        for name, path in input_paths.items()
    }
    (relevance / "testco.run.json").write_text(json.dumps({
        "version": 1,
        "slug": "testco",
        "client_name": "Testco",
        "started_at": "2026-07-18T11:59:00+00:00",
        "completed_at": "2026-07-18T12:00:00+00:00",
        "command": [
            "-m", "tools.relevance.calibrate", "--client", "Testco",
            "--out", str(calibration),
        ],
        "sweep": {
            "path": str(sweep),
            "sha256": digest(sweep),
            "stable_sha256": _sweep_basis_sha(sweep),
        },
        "inputs": inputs,
        "calibration": {
            "path": str(calibration), "sha256": digest(calibration),
            "row_count": 0,
        },
        "summary": "[calibrate] Testco: 0 records",
    }), encoding="utf-8")

    assert lc.derive_stage("testco", today=TODAY)["stage"] == "shortlisted"

    profile = book / "clients" / "testco" / "profile.json"
    prior_profile = profile.read_bytes()
    profile.write_text(profile.read_text(encoding="utf-8") + "\n",
                       encoding="utf-8")
    assert lc.derive_stage("testco", today=TODAY)["stage"] == "swept"
    profile.write_bytes(prior_profile)
    assert lc.derive_stage("testco", today=TODAY)["stage"] == "shortlisted"

    content = book / "clients" / "testco" / "signal_board_content.json"
    content.write_text('{"client_name":"Testco"}\n', encoding="utf-8")
    assert relevance_receipt_valid("testco", root=book) is False
    assert relevance_receipt_valid(
        "testco", root=book, allow_board_content_change=True) is True
    content_sha = digest(content)
    assert relevance_receipt_valid(
        "testco", root=book,
        expected_board_sha256=content_sha) is True
    content.write_text(
        '{"client_name":"Testco","changed":true}\n', encoding="utf-8")
    assert relevance_receipt_valid(
        "testco", root=book,
        expected_board_sha256=content_sha) is False
    content.unlink()

    sweep_payload = json.loads(sweep.read_text(encoding="utf-8"))
    sweep_payload["results"]["award_repulls"] = [{"generated_id": "A"}]
    sweep.write_text(json.dumps(sweep_payload), encoding="utf-8")
    assert relevance_receipt_valid("testco", root=book) is False
    assert relevance_receipt_valid(
        "testco", root=book, allow_sweep_change=True) is True
    sweep_sha = digest(sweep)
    assert relevance_receipt_valid(
        "testco", root=book,
        expected_sweep_sha256=sweep_sha) is True
    sweep_payload["results"]["later"] = []
    sweep.write_text(json.dumps(sweep_payload), encoding="utf-8")
    assert relevance_receipt_valid(
        "testco", root=book,
        expected_sweep_sha256=sweep_sha) is False
    assert relevance_receipt_valid(
        "testco", root=book, allow_sweep_change=True) is False
    assert lc.derive_stage("testco", today=TODAY)["stage"] == "swept"
    sweep.unlink()
    assert relevance_receipt_valid(
        "testco", root=book, allow_sweep_change=True) is False


def test_relevance_receipt_cannot_borrow_another_client_identity(
        book):  # noqa: F811
    _mk_profile(book)
    _mk_sweep(book)
    profile = book / "clients" / "testco" / "profile.json"
    relevance = book / "data" / "state" / "relevance"
    calibration = relevance / "testco.calibration.csv"
    calibration.write_text(
        "client,record_ref,engine_score,disagreement,scope_basis\n",
        encoding="utf-8")
    sweep = book / "data" / "cleaned" / "searches_testco.json"

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    inputs = {
        name: {"path": str(path), "sha256": digest(path) if path.exists() else None}
        for name, path in {
            "taxonomy": book / "clients" / "testco" / "capability_taxonomy.json",
            "profile": profile,
            "engagement_scope": book / "clients" / "testco" /
                                "engagement_scope.json",
            "signal_board_content": book / "clients" / "testco" /
                                    "signal_board_content.json",
            "scope_presets": book / "data" / "reference" /
                             "scope_presets.json",
        }.items()
    }
    (relevance / "testco.run.json").write_text(json.dumps({
        "version": 1, "slug": "testco", "client_name": "Otherco",
        "started_at": "2026-07-18T11:59:00+00:00",
        "completed_at": "2026-07-18T12:00:00+00:00",
        "command": ["-m", "tools.relevance.calibrate", "--client", "Otherco",
                    "--out", str(calibration)],
        "sweep": {"path": str(sweep), "sha256": digest(sweep)},
        "inputs": inputs,
        "calibration": {"path": str(calibration),
                        "sha256": digest(calibration), "row_count": 0},
        "summary": "[calibrate] Otherco: 0 records",
    }), encoding="utf-8")

    assert relevance_receipt_valid("testco", root=book) is False

    profile_payload = json.loads(profile.read_text(encoding="utf-8"))
    profile_payload["client_name"] = "Otherco"
    profile.write_text(json.dumps(profile_payload), encoding="utf-8")
    receipt_path = relevance / "testco.run.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["inputs"]["profile"]["sha256"] = digest(profile)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert relevance_receipt_valid("testco", root=book) is False


def test_invalid_authoritative_receipt_cannot_fall_back_to_legacy_output(
        book):  # noqa: F811
    _mk_profile(book)
    _mk_sweep(book)
    relevance = book / "data" / "state" / "relevance"
    (relevance / "testco.run.json").write_text("{broken", encoding="utf-8")
    (relevance / "testco.adjudications.jsonl").write_text(
        '{"legacy": true}\n', encoding="utf-8")
    (relevance / "calibration.csv").write_text(
        "client,record_ref\nTestco,legacy-row\n", encoding="utf-8")

    assert lc.derive_stage("testco", today=TODAY)["stage"] == "swept"


def test_assessment_ticker_normalizes_space_separated_iso_timestamp(
        book):  # noqa: F811
    _mk_profile(book)
    _write_waiting(book)
    path = book / "data" / "state" / "assessment" / "testco.state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["transitions"][-1]["at"] = "2026-07-18 12:00:00-06:00"
    path.write_text(json.dumps(state), encoding="utf-8")

    payload = srv.app.test_client().get("/api/radar").get_json()
    checkpoints = [row for row in payload["ticker"]["items"]
                   if row["kind"] == "assessment_checkpoint"]

    assert checkpoints[0]["when"] == "2026-07-18"


def test_corrupt_chain_state_is_alerted_without_mutation(book):  # noqa: F811
    _mk_profile(book)
    state_dir = book / "data" / "state" / "assessment"
    state_dir.mkdir(parents=True)
    path = state_dir / "testco.state.json"
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()

    assessment = lc.derive_stage("testco", today=TODAY)["assessment"]

    assert assessment["state"] is None
    assert "status unavailable" in assessment["alert_line"]
    assert path.read_bytes() == before


def test_radar_route_survives_a_corrupt_state_file(book):  # noqa: F811
    """Adjusted from an index.html source grep after the operator's home
    revert (2026-07-18): the seam is pinned at the API instead. A corrupt
    assessment state file must not break the radar route; the card still
    serves with the honest status-unavailable alert."""
    _mk_profile(book)
    _mk_sweep(book)
    state_dir = book / "data" / "state" / "assessment"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "testco.state.json").write_text("{broken", encoding="utf-8")

    r = srv.app.test_client().get("/api/radar")

    assert r.status_code == 200
    card = next(c for c in r.get_json()["cards"] if c["slug"] == "testco")
    assert card["assessment"]["state"] is None
    assert "status unavailable" in card["assessment"]["alert_line"]
