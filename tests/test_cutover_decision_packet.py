"""Cycle 4 T4: offline per-row strict Live SAM cutover explanations."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agents.assess.binding import build_binding
from agents.assess.live_report import project_sweep_live_report
from agents.assess.parity import (
    build_live_parity_report,
    main,
    render_cutover_decision_packet,
)
from tests.test_assess_ledger import _depth_record, _notice, _profile, _sweep
from tests.test_live_report_truth import _persist, _seed_runtime


AS_OF = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _packet_sweep() -> dict:
    searches = _sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{
            "abbr": "DHS",
            "name": "Department of Homeland Security",
        }],
    }
    searches["results"]["sam.gov"].extend([
        _notice("N4", "Unreviewed active monitor"),
        _notice("N5", "Past-deadline legacy pursuit", deadline="2026-07-01"),
        _notice("N6", "Out-of-scope legacy pursuit"),
    ])
    searches["results"]["sam.gov"][-1]["agency"] = (
        "National Aeronautics and Space Administration")
    searches["results"]["sam.gov"][-1]["raw_payload"]["agency"] = "NASA"
    searches["results"]["triage"].update({
        "N4": {"verdict": "monitor", "reason": "legacy monitor"},
        "N5": {"verdict": "pursue", "reason": "legacy deadline handling"},
        "N6": {"verdict": "pursue", "reason": "legacy scope handling"},
    })
    searches["results"]["sam_census"].update(
        matched=6, active_screened=6, complete=True)
    return searches


def _amendment_identity_sweep() -> dict:
    searches = _sweep()
    old = searches["results"]["sam.gov"][0]
    old["raw_payload"].update({
        "solicitation": "SOL-PCAP-1",
        "office": "CISA-ACQ",
        "modifiedDate": "2026-07-10T10:00:00+00:00",
    })
    new = copy.deepcopy(old)
    new.update({
        "source_id": "N1-NEW",
        "title": "Packet capture platform amendment",
        "posted_date": "2026-07-09",
        "api_url": "https://sam.gov/opp/N1-NEW/view",
    })
    new["raw_payload"].update(notice_id="N1-NEW")
    new["raw_payload"].pop("modifiedDate")

    monitor_old = _notice(
        "M-OLD",
        "Network telemetry market research",
        notice_type="Sources Sought",
        solicitation="MON-1",
        office="CISA-ACQ",
        deadline=None,
    )
    monitor_old["raw_payload"]["modifiedDate"] = (
        "2026-07-10T09:00:00+00:00")
    monitor_new = copy.deepcopy(monitor_old)
    monitor_new.update({
        "source_id": "M-NEW",
        "title": "Network telemetry market research amendment",
        "posted_date": "2026-07-08",
        "api_url": "https://sam.gov/opp/M-NEW/view",
    })
    monitor_new["raw_payload"].update(notice_id="M-NEW")
    monitor_new["raw_payload"].pop("modifiedDate")

    searches["results"]["sam.gov"].extend([
        new, monitor_old, monitor_new])
    searches["results"]["triage"].update({
        "N1-NEW": {"verdict": "pursue", "reason": "same solicitation"},
        "M-OLD": {"verdict": "monitor", "reason": "forming requirement"},
        "M-NEW": {"verdict": "monitor", "reason": "forming requirement"},
    })
    searches["results"]["sam_census"].update(
        matched=6, active_screened=300, complete=True)
    searches["results"]["dossiers"] = {"records": [_depth_record("N1")]}
    return searches


def _requirements_for_current_old_posting(searches: dict) -> dict:
    profile = _profile()
    binding = build_binding(
        scope_designator="all",
        sweep_artifact="searches_testco.json",
        sweep=searches,
        profile=profile,
    )
    projection, _ = project_sweep_live_report(
        "Testco", searches, profile, binding, as_of=AS_OF)
    record = next(row.record for row in projection.notices
                  if row.record.notice_id == "N1")
    evidence = next(
        row for row in reversed(record.authoritative_evidence)
        if record.requirement_excerpt in row.excerpt)
    return {
        "schema_version": 1,
        "client": "Testco",
        "binding": binding,
        "reviews": [{
            "notice_id": "N1",
            "evidence_id": evidence.evidence_id,
            "excerpt": record.requirement_excerpt,
            "capability_terms": ["packet capture"],
            "decision": "approved",
            "attachment_inventory_count": record.attachment_inventory_count,
            "attachment_inventory_hash": record.attachment_inventory_hash,
            "attachments_reviewed": False,
            "reviewed_by": "operator",
            "reviewed_at": AS_OF.isoformat(),
        }],
    }


def test_packet_explains_and_reconciles_every_changed_live_row(tmp_path):
    searches = _packet_sweep()
    state_dir = tmp_path / "assess_runs"
    review_dir = tmp_path / "review"
    review_dir.mkdir()

    report = build_live_parity_report(
        "Testco",
        searches,
        _profile(),
        sweep_artifact="searches_testco.agency_dhs.json",
        as_of=AS_OF,
        state_dir=state_dir,
        review_dir=review_dir,
    )

    packet = report["cutover_decision_packet"]
    summary = packet["summary"]
    assert report["current_pointer"]["state"] == "absent"
    assert not list(state_dir.rglob("*.current.json"))
    assert summary["every_changed_row_explained"] is True
    assert summary["all_parity_deltas_reconciled"] is True
    assert summary["unexplained_rows"] == 0
    assert {row["source_id"] for row in packet["board"]} \
        == set(report["delta"]["board_exited"])
    assert {row["source_id"] for row in packet["watchlist"]} == {"N4"}
    assert {row["source_id"] for row in packet["monitors"]} == {"N4"}
    assert packet["reconciliation"]["verdict_transitions"] == {
        "discard->research": 1,
        "monitor->research": 1,
        "pursue->discard": 1,
        "pursue->not_in_strict_ledger": 1,
        "pursue->research": 1,
    }
    assert all(row["title"] and row["agency"] and row["deadline"]
               for row in packet["rows"])
    assert all(row["failed_eligibility_leg"] and row["ledger_reason"]
               for row in packet["rows"])
    assert "\u2014" not in json.dumps(packet, ensure_ascii=False)
    outside = next(row for row in packet["triage_reclassifications"]
                   if row["source_id"] == "N6")
    assert outside["failed_eligibility_leg"] == "operator_scope_boundary"
    expired = next(row for row in packet["board"]
                   if row["source_id"] == "N5")
    assert expired["failed_eligibility_leg"] == "future_deadline"
    discard = next(row for row in packet["triage_reclassifications"]
                   if row["source_id"] == "N2")
    assert discard["failed_eligibility_leg"] == "notice_requirement_evidence"

    markdown = render_cutover_decision_packet(report)
    assert "offline projection only; no pointer was written or activated" in markdown
    assert "| board | dropped | N5 | pursue -> discard | future_deadline |" in markdown
    assert "| triage_verdict | reclassified | N2 | discard -> research |" in markdown
    assert "\u2014" not in markdown


def test_actionable_amendment_rekeys_board_by_family_without_false_drop(
        tmp_path):
    searches = _amendment_identity_sweep()
    review_dir = tmp_path / "review"
    review_dir.mkdir()

    report = build_live_parity_report(
        "Testco",
        searches,
        _profile(),
        sweep_artifact="searches_testco.json",
        requirement_reviews=_requirements_for_current_old_posting(searches),
        as_of=AS_OF,
        state_dir=tmp_path / "assess_runs",
        review_dir=review_dir,
    )

    assert report["delta"]["board_exited"] == ["N1-NEW"]
    assert report["delta"]["board_entered"] == ["N1"]
    packet = report["cutover_decision_packet"]
    assert packet["board"] == []
    assert packet["summary"]["board_rows_explained"] == 0
    assert packet["summary"]["board_current_posting_transitions"] == 1
    transition = packet["board_current_posting_transitions"][0]
    assert transition["source_id"] == "N1-NEW"
    assert transition["strict_current_notice_id"] == "N1"
    assert transition["outcome"] == "current_posting_changed"
    assert transition["legacy_state"] == transition["strict_state"] == "pursue"
    assert transition["failed_eligibility_leg"] \
        == "superseded_by_current_family_posting"
    assert transition in packet["rows"]
    assert packet["summary"]["board_delta_reconciled"] is True

    watch_drop = next(
        row for row in packet["watchlist"] if row["source_id"] == "M-NEW")
    assert watch_drop["strict_state"] == "monitor"
    assert watch_drop["strict_current_notice_id"] == "M-OLD"
    assert watch_drop["failed_eligibility_leg"] \
        == "superseded_by_current_family_posting"
    assert "uses current posting M-OLD" in watch_drop["ledger_reason"]
    assert packet["summary"]["all_parity_deltas_reconciled"] is True
    assert report["cutover_eligible"] is True

    markdown = render_cutover_decision_packet(report)
    assert "| board_current_posting | current_posting_changed | N1-NEW |" \
        in markdown
    assert "superseded_by_current_family_posting" in markdown
    assert "| board | dropped | N1-NEW |" not in markdown


def test_parity_cli_writes_json_sidecar_and_markdown_without_cutover(tmp_path):
    searches = _packet_sweep()
    searches.pop("search_scope")
    sweep_path = tmp_path / "searches_testco.agency_dhs.json"
    profile_path = tmp_path / "profile.json"
    review_dir = tmp_path / "review"
    state_dir = tmp_path / "assess_runs"
    output = tmp_path / "testco.parity.json"
    review_dir.mkdir()
    sweep_path.write_text(json.dumps(searches), encoding="utf-8")
    profile_path.write_text(_profile().model_dump_json(indent=2), encoding="utf-8")

    result = main([
        "--client", "Testco",
        "--sweep-path", str(sweep_path),
        "--profile-path", str(profile_path),
        "--review-dir", str(review_dir),
        "--state-dir", str(state_dir),
        "--as-of", AS_OF.isoformat(),
        "--scope-override", "agency:DHS:Department of Homeland Security",
        "--output", str(output),
    ])

    assert result == 0
    assert output.exists()
    packet_path = output.with_suffix(".md")
    assert packet_path.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["diagnostic_scope_override"] \
        == "agency:DHS:Department of Homeland Security"
    assert payload["cutover_eligible"] is False
    assert payload["cutover_decision_packet"]["summary"][
        "all_parity_deltas_reconciled"] is True
    assert "this packet cannot authorize cutover" in packet_path.read_text(
        encoding="utf-8")
    assert not list(state_dir.rglob("*.current.json"))


def test_parity_cli_restores_real_entities_before_current_pointer_validation(
        tmp_path, monkeypatch):
    """Item 22: hermetic projection must not invalidate a CURRENT pointer."""
    import tools.entity_lineage as entity_lineage

    repository_root = tmp_path / "repository"
    entities_dir = repository_root / "data" / "entities"
    entities_dir.mkdir(parents=True)
    (entities_dir / "crosswalk.json").write_text(
        json.dumps({"schema_version": 1, "entities": []}), encoding="utf-8")
    monkeypatch.setattr(entity_lineage, "ROOT", str(repository_root))
    monkeypatch.delenv("LILA_ENTITIES_DIR")

    searches = _sweep()
    searches["results"]["dossiers"] = {"records": [_depth_record()]}
    runtime = tmp_path / "runtime"
    seed = _seed_runtime(runtime, monkeypatch, searches)
    _persist(seed)
    pointer = next(Path(seed["state_dir"]).rglob("*.current.json"))
    pointer_before = pointer.read_bytes()
    pointer_mtime_before = pointer.stat().st_mtime_ns

    output = tmp_path / "testco.current.parity.json"
    result = main([
        "--client", "Testco",
        "--sweep-path", str(seed["sweep_path"]),
        "--profile-path", str(runtime / "clients" / "testco" / "profile.json"),
        "--review-dir", str(seed["review_dir"]),
        "--state-dir", str(seed["state_dir"]),
        "--as-of", AS_OF.isoformat(),
        "--output", str(output),
    ])

    assert result == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["current_pointer"]["state"] == "current"
    assert report["current_pointer"]["problem"] is None
    assert not any("review or reference inputs have changed" in problem
                   for problem in report["cutover_problems"])
    assert pointer.read_bytes() == pointer_before
    assert pointer.stat().st_mtime_ns == pointer_mtime_before


def test_projection_sandbox_restores_and_never_writes_preexisting_override(
        tmp_path, monkeypatch):
    from agents.assess.parity import _isolated_projection_entities

    operator_entities = tmp_path / "operator-entities"
    operator_entities.mkdir()
    monkeypatch.setenv("LILA_ENTITIES_DIR", str(operator_entities))

    with _isolated_projection_entities():
        isolated = Path(os.environ["LILA_ENTITIES_DIR"])
        assert isolated != operator_entities
        (isolated / "unresolved.log").write_text(
            "projection traffic", encoding="utf-8")

    assert os.environ["LILA_ENTITIES_DIR"] == str(operator_entities)
    assert not (operator_entities / "unresolved.log").exists()


def test_collection_only_rejection_does_not_claim_row_is_explained(tmp_path):
    searches = _packet_sweep()
    malformed = _notice("N7", "Legacy row with no buyer identity")
    malformed["agency"] = ""
    malformed["raw_payload"].pop("agency")
    searches["results"]["sam.gov"].append(malformed)
    searches["results"]["triage"]["N7"] = {
        "verdict": "pursue",
        "reason": "legacy row did not enforce required identity fields",
    }
    searches["results"]["sam_census"].update(
        matched=7, active_screened=7, complete=True)
    review_dir = tmp_path / "review"
    review_dir.mkdir()

    report = build_live_parity_report(
        "Testco",
        searches,
        _profile(),
        sweep_artifact="searches_testco.agency_dhs.json",
        as_of=AS_OF,
        state_dir=tmp_path / "assess_runs",
        review_dir=review_dir,
    )

    packet = report["cutover_decision_packet"]
    row = next(row for row in packet["board"] if row["source_id"] == "N7")
    assert row["failed_eligibility_leg"] == "strict_ledger_eligibility"
    assert packet["summary"]["unexplained_rows"] >= 1
    assert packet["summary"]["every_changed_row_explained"] is False
    assert packet["summary"]["board_delta_reconciled"] is True
    assert packet["summary"]["watchlist_delta_reconciled"] is True
    assert packet["summary"]["monitor_delta_reconciled"] is True
    assert packet["summary"]["verdict_deltas_reconciled"] is True
    assert packet["summary"]["all_parity_deltas_reconciled"] is False
    assert report["cutover_eligible"] is False
    assert "cutover decision packet has unexplained or unreconciled changed rows" \
        in report["cutover_problems"]
    assert "Cutover status: **NOT ELIGIBLE**" in \
        render_cutover_decision_packet(report)
