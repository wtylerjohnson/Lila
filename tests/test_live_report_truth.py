"""Cycle 3: strict Live SAM records become report truth per backfilled client."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
from datetime import date, datetime
from pathlib import Path

import pytest

from agents.assess.binding import build_binding
from agents.assess.live_report import (
    LiveReportState,
    project_live_ledger,
    resolve_current_live_report,
)
from agents.assess.ledger import (
    _scope_designator,
    AssessLedgerError,
    assess_projection_input_manifest,
    build_assess_run,
    current_assess_pointer_path,
    materialize_current_assess_run,
    persist_assess_run,
    scope_from_sweep,
)
from agents.assess.parity import build_live_parity_report
from agents.reports.document import build_document
from agents.reports.facts import build_fact_pack
from agents.reports.lint import lint_counts, lint_screen_census
from agents.reports.views import render_assessment
from tests.test_assess_ledger import (
    NOW,
    _depth_record,
    _notice,
    _profile,
    _sweep,
)


def _freeze_strict_clock(monkeypatch) -> None:
    """Calendar-rot guard (2026-08-03): `resolve_current_live_report` and every
    call-time `from agents.assess.live_report import utc_today` consumer
    (approval, facts, document deadline cutoffs) read the wall clock, so this
    fixture family aged in real time: the default `_notice` deadline
    2026-08-01 crossed `utc_today()` at midnight UTC 2026-08-01 and CURRENT
    silently became INVALID with no code change (verified failing on pristine
    HEAD). Freeze the strict-ledger clock at the fixtures' own NOW, exactly
    as tests/test_radar_api.py already freezes the lifecycle clock. Tests
    that probe expiry keep passing an explicit `as_of`/`effective_date`,
    which always wins over this frozen default.

    The approval clock freezes with it: `approve_assess_results` stamps
    `approved_at = datetime.now(utc)`, and `build_assess_run` advances its
    effective `as_of` to max(valid_observation_times), which includes that
    stamp. A real-time approval therefore dragged every re-materialized run
    past the fixture deadline and silently flipped N1 out of BID_NOW."""
    import agents.assess.approval as approval
    import agents.assess.live_report as live_report

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return NOW.replace(tzinfo=None)
            return NOW.astimezone(tz)

    monkeypatch.setattr(live_report, "datetime", _FrozenDateTime)
    monkeypatch.setattr(approval, "datetime", _FrozenDateTime)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    _freeze_strict_clock(monkeypatch)


def _qualify(searches: dict) -> dict:
    candidates = []
    for row in searches["results"]["sam.gov"]:
        if row["source_id"] not in {"N1", "N2"}:
            continue
        candidates.append({
            "opportunity": dict(row),
            "verified": True,
            "fit_rationale": {"verdict": "strong_fit"},
        })
    return {"candidates": candidates}


def _cutover_sweep() -> dict:
    searches = _sweep()
    searches["results"]["triage"]["N2"] = {
        "verdict": "pursue", "reason": "legacy metadata promotion"}
    searches["results"]["sam.gov"].append(
        _notice("N4", "Legacy monitor without requirement depth"))
    searches["results"]["triage"]["N4"] = {
        "verdict": "monitor", "reason": "legacy monitor"}
    searches["results"]["sam_census"].update(matched=4)
    searches["results"]["dossiers"] = {"records": [_depth_record()]}
    searches["results"]["usaspending.gov"] = [{
        "naics_code": "541512",
        "summary": {
            "award_count": 1,
            "total_obligated": 1_000_000.0,
            "median_award": 1_000_000.0,
            "max_award": 1_000_000.0,
            "top_incumbents": ["PrimeCo"],
        },
        "awards": [{
            "award_id": "A1", "recipient": "PrimeCo",
            "amount": 1_000_000.0,
            "awarding_agency": "Department of Homeland Security",
            "start_date": "2026-01-01",
            "url": "https://usaspending.gov/award/A1",
        }],
    }]
    searches["results"]["subawards"] = {
        "edges": {"541512": [{
            "subaward_id": "SA1", "prime": "PrimeCo", "sub": "DeliveryCo",
            "amount": 250_000.0, "prime_award_id": "A1",
            "date": "2026-03-01",
            "source": "https://api.usaspending.gov",
        }]},
        "demand": {"rows_by_naics": {"541512": []}},
        "small_awards": {"541512": []},
    }
    return searches


def _seed_runtime(tmp_path, monkeypatch, searches: dict):
    import agents.review as review
    import tools.capability as capability

    # Offline doctrine (2026-08-04): this seeded world's clock is NOW, not
    # the host's. Two real-clock seams exist and BOTH must freeze or the
    # fixtures rot as the calendar crosses their deadlines (N1's 2026-08-01
    # default went stale on 2026-08-02):
    #   1. utc_today - the ONE shared cutoff (server strict-pointer,
    #      approval can_release, document/facts deadline gates);
    #   2. approval's datetime.now - the approved_at stamp, which the
    #      immutable-run build compares against fixture-epoch stamps.
    # Explicit as_of arguments inside tests still override per call.
    import agents.assess.approval as assess_approval
    import agents.assess.live_report as live_report

    class _SeededNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(live_report, "utc_today", lambda: NOW.date())
    monkeypatch.setattr(assess_approval, "datetime", _SeededNow)

    profile = _profile()
    clients = tmp_path / "clients"
    profile_dir = clients / "testco"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8")
    monkeypatch.setattr(capability, "CLIENTS_DIR", str(clients))

    review_dir = tmp_path / "review"
    review_dir.mkdir()
    scope = scope_from_sweep(searches)
    designator = _scope_designator(scope)
    review_scope = ({"all": True} if designator == "all" else {
        "agencies": [agency.name for agency in scope.agencies],
    })
    (review_dir / "testco.review.json").write_text(json.dumps({
        "client_name": "Testco",
        "search_scope": review_scope,
    }), encoding="utf-8")
    cleaned = tmp_path / "cleaned"
    cleaned.mkdir()
    sweep_name = ("searches_testco.json" if designator == "all"
                  else f"searches_testco.{designator}.json")
    sweep_path = cleaned / sweep_name
    sweep_path.write_text(json.dumps(searches), encoding="utf-8")
    monkeypatch.setattr(review, "REVIEW_DIR", str(review_dir))
    monkeypatch.setenv("LILA_REVIEW_DIR", str(review_dir))
    state_dir = tmp_path / "assess_runs"
    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(state_dir))
    (review_dir / "testco.partnering.json").write_text(json.dumps({
        "client_name": "Testco",
        "size_status_by_naics": {"541512": "other_than_small"},
        "certifications": [],
        "vehicles_held": [],
        "award_band": {"floor_usd": 50_000, "ceiling_usd": 2_000_000},
    }), encoding="utf-8")

    binding = build_binding(
        scope_designator=designator,
        sweep_artifact=sweep_name,
        sweep=searches,
        profile=profile,
    )
    proposed, _, _ = build_assess_run(
        "Testco", searches, profile, binding, as_of=NOW)
    candidate = next(
        row for row in proposed.live.records if row.notice_id == "N1")
    evidence = next(
        row for row in reversed(candidate.authoritative_evidence)
        if candidate.requirement_excerpt in row.excerpt)
    requirements = {
        "schema_version": 1,
        "client": "Testco",
        "binding": binding,
        "reviews": [{
            "notice_id": "N1",
            "evidence_id": evidence.evidence_id,
            "excerpt": candidate.requirement_excerpt,
            "capability_terms": ["packet capture"],
            "decision": "approved",
            "attachment_inventory_count": candidate.attachment_inventory_count,
            "attachment_inventory_hash": candidate.attachment_inventory_hash,
            "attachments_reviewed": False,
            "reviewed_by": "operator",
            "reviewed_at": NOW.isoformat(),
        }],
    }
    (review_dir / "testco.live_requirements.json").write_text(
        json.dumps(requirements), encoding="utf-8")
    approval = {
        "schema_version": 1,
        "client": "Testco",
        "approved_at": NOW.isoformat(),
        "approved_by": "operator",
        "binding": binding,
    }
    (review_dir / "testco.assess_approval.json").write_text(
        json.dumps(approval), encoding="utf-8")
    projection_inputs = assess_projection_input_manifest(
        "Testco", review_dir=review_dir)
    run, diagnostics, posting_index = build_assess_run(
        "Testco",
        searches,
        profile,
        binding,
        approval_payload=approval,
        approval_status="approved",
        requirement_reviews_payload=requirements,
        projection_inputs=projection_inputs,
        as_of=NOW,
    )
    return {
        "profile": profile,
        "binding": binding,
        "requirements": requirements,
        "approval": approval,
        "projection_inputs": projection_inputs,
        "run": run,
        "diagnostics": diagnostics,
        "posting_index": posting_index,
        "state_dir": state_dir,
        "review_dir": review_dir,
        "cleaned_dir": cleaned,
        "sweep_path": sweep_path,
    }


def _persist(seed: dict) -> None:
    persist_assess_run(
        seed["run"],
        seed["binding"],
        seed["diagnostics"],
        seed["posting_index"],
        seed["projection_inputs"],
        state_dir=seed["state_dir"],
    )


def _approve_partial_and_materialize(seed: dict):
    """Exercise the same operator approval and immutable-build path as the UI."""
    from agents.assess.approval import (
        approve_assess_results, required_blocker_manifest,
    )

    # The exception control is offered against the current fully materialized
    # ledger, including its partner projection, rather than a hand-built test
    # approximation of that immutable run.
    displayed, _, _ = materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    displayed_blockers = required_blocker_manifest(displayed.coverage)
    approval = approve_assess_results(
        "Testco",
        review_dir=str(seed["review_dir"]),
        partial_release_approved=True,
        assess_state_dir=str(seed["state_dir"]),
        partial_release_run_id=displayed.run_id,
        partial_release_blockers_sha256=displayed_blockers["sha256"],
    )
    assert approval["partial_release_approved"] is True
    assert approval["partial_release_run_id"] == displayed.run_id
    assert approval["partial_release_blockers"]
    assert approval["partial_release_blockers_sha256"]
    run, _, diagnostics = materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    return run, diagnostics


def test_unstubbed_current_run_cuts_board_factpack_and_monitor_joins_over(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)

    absent = resolve_current_live_report(
        "Testco", searches, seed["profile"],
        state_dir=seed["state_dir"], review_dir=seed["review_dir"])
    assert absent.state == LiveReportState.ABSENT
    assert absent.can_release is None
    legacy = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=NOW.date())
    assert {row.source_id for row in legacy.board.pursuits} == {"N1", "N2"}
    assert legacy.verdict_totals == {"pursue": 2, "monitor": 2}

    _persist(seed)
    current = resolve_current_live_report(
        "Testco", searches, seed["profile"],
        state_dir=seed["state_dir"], review_dir=seed["review_dir"])
    assert current.state == LiveReportState.CURRENT
    assert current.run_id == seed["run"].run_id
    assert current.can_release is False
    assert current.live_coverage_complete is True

    document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=NOW.date())
    assert [row.source_id for row in document.board.pursuits] == ["N1"]
    strict_trace = document.board.pursuits[0].fit_trace
    assert strict_trace["matched_terms"] == ["packet capture"]
    assert "human-reviewed exact SAM requirement span" in \
        strict_trace["screen_inference"]
    assert "legacy metadata promotion" not in json.dumps(strict_trace)
    assert strict_trace["notice_evidence_trace"] \
        == list(current.actionable[0].record.fit_trace)
    assert document.verdict_totals == {
        "monitor": 1, "pursue": 1, "research": 2}
    assert document.counts()["pursue_notices"] == 1
    assert document.counts()["monitor_notices"] == 1
    assert {entry.id for entry in document.watchlist.entries
            if entry.kind == "standing"} == {"N3"}
    assert all(play.source_id != "N4" for play in document.partnering.plays)

    pack = build_fact_pack(
        "Testco", searches=searches, qualify_report=_qualify(searches),
        profile=seed["profile"], as_of=NOW.date())
    live_facts = [pack.get(fact_id) for fact_id in pack.opportunity_fact_ids
                  if pack.get(fact_id).tier == "notice"]
    assert len(live_facts) == 1
    assert live_facts[0].value["opportunity"]["source_id"] == "N1"
    assert live_facts[0].value["fit"]["verdict"] \
        == "human_reviewed_requirement_fit"
    assert "N2" not in live_facts[0].model_dump_json()

    expected_count_keys = {
        "pursuits", "dossiers", "pursue_notices", "monitor_notices",
        "competitors", "watchlist_entries", "agencies", "headline_agencies",
        "news_items", "tam_components", "teaming_plays", "teaming_watch",
    }
    assert set(document.counts()) == expected_count_keys
    for view in ("client", "sales", "internal"):
        html = render_assessment(document, view)
        assert lint_counts(html).ok
        assert lint_screen_census(html).ok


@pytest.mark.parametrize("agency", [
    {"name": "!!!", "abbr": "!!!"},
    {"name": "国土安全", "abbr": "国土"},
])
def test_malformed_scope_designator_respects_pointer_dormancy(
        tmp_path, agency):
    """Item 5: invalid scope labels cannot cut over a pointerless client."""
    searches = _sweep()
    searches["search_scope"] = {"mode": "focus", "agencies": [agency]}

    absent = resolve_current_live_report(
        "Testco", searches, _profile(), state_dir=tmp_path)
    assert absent.state == LiveReportState.ABSENT
    assert absent.problem is None
    assert not list(tmp_path.rglob("*.current.json"))

    pointer = current_assess_pointer_path(
        "Testco", "all", state_dir=tmp_path)
    pointer.parent.mkdir(parents=True)
    pointer.write_text("{}", encoding="utf-8")
    invalid = resolve_current_live_report(
        "Testco", searches, _profile(), state_dir=tmp_path)
    assert invalid.state == LiveReportState.INVALID
    assert "cannot bind this sweep" in (invalid.problem or "")


def test_malformed_scope_fails_closed_when_client_pointer_root_is_not_directory(
        tmp_path):
    searches = _sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{"name": "!!!", "abbr": "!!!"}],
    }
    pointer_root = current_assess_pointer_path(
        "Testco", "all", state_dir=tmp_path).parent
    pointer_root.write_text("corrupt pointer root", encoding="utf-8")

    resolved = resolve_current_live_report(
        "Testco", searches, _profile(), state_dir=tmp_path)

    assert resolved.state == LiveReportState.INVALID
    assert "pointer state is unreadable" in (resolved.problem or "")
    assert "not a directory" in (resolved.problem or "").lower()


def test_malformed_scope_fails_closed_when_pointer_root_cannot_be_read(
        tmp_path, monkeypatch):
    import agents.assess.live_report as live_report

    searches = _sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{"name": "!!!", "abbr": "!!!"}],
    }

    def _denied(_path):
        raise PermissionError("pointer store denied")

    monkeypatch.setattr(live_report.os, "scandir", _denied)
    resolved = resolve_current_live_report(
        "Testco", searches, _profile(), state_dir=tmp_path)

    assert resolved.state == LiveReportState.INVALID
    assert "pointer state is unreadable" in (resolved.problem or "")
    assert "pointer store denied" in (resolved.problem or "")


def test_malformed_scope_fails_closed_on_broken_pointer_root_symlink(
        tmp_path):
    searches = _sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{"name": "!!!", "abbr": "!!!"}],
    }
    pointer_root = current_assess_pointer_path(
        "Testco", "all", state_dir=tmp_path).parent
    pointer_root.symlink_to(tmp_path / "missing-pointer-root", target_is_directory=True)

    resolved = resolve_current_live_report(
        "Testco", searches, _profile(), state_dir=tmp_path)

    assert resolved.state == LiveReportState.INVALID
    assert "pointer state is unreadable" in (resolved.problem or "")


def test_partial_strict_census_reconciles_accepted_and_rejected_rows(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    rejected = copy.deepcopy(searches["results"]["sam.gov"][1])
    rejected.update({
        "source": "web",
        "source_id": "N5",
        "title": "Non-SAM row mixed into the collection",
        "api_url": "https://sam.gov/opp/N5/view",
    })
    rejected["raw_payload"] = dict(
        rejected["raw_payload"], notice_id="N5")
    searches["results"]["sam.gov"].append(rejected)
    searches["results"]["triage"]["N5"] = {
        "verdict": "pursue", "reason": "legacy promotion must not survive",
    }
    searches["results"]["sam_census"].update(
        matched=5, active_screened=5, complete=True)
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)

    current = resolve_current_live_report(
        "Testco", searches, seed["profile"],
        state_dir=seed["state_dir"], review_dir=seed["review_dir"])
    assert current.state == LiveReportState.CURRENT
    assert current.live_coverage_complete is False
    assert len(current.notices) == 4
    document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=NOW.date())
    assert document.board.pursuits == []
    assert sum(document.verdict_totals.values()) == 4
    for view in ("client", "sales", "internal"):
        html = render_assessment(document, view)
        # opportunity-set doctrine: held-census reconciliation is internal/sales
        # audit; the client view stays a clean opportunity set.
        if view == "client":
            assert "live-source evidence gate is held closed" not in html
        else:
            assert "live-source evidence gate is held closed" in html
            assert "4 of 5 census records" in html
        assert "every one accounted for" not in html
        assert "pull returned none" not in html
        assert lint_screen_census(html).ok


def test_partial_coverage_qualifier_preserves_verified_pursuit_cards(
        tmp_path, monkeypatch):
    from dataclasses import replace

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    current = resolve_current_live_report(
        "Testco", searches, seed["profile"],
        state_dir=seed["state_dir"], review_dir=seed["review_dir"])
    partial_projection = replace(current, live_coverage_complete=False)
    document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=NOW.date(), _live_report=partial_projection,
        _cap_profile=seed["profile"])
    assert [row.source_id for row in document.board.pursuits] == ["N1"]
    html = render_assessment(document, "client")
    assert "Packet capture platform" in html
    # opportunity-set doctrine: the client view preserves the verified pursuit
    # cards but drops the held-census reconciliation (that audit rides internal).
    assert "live-source evidence gate is held closed" not in html
    assert lint_screen_census(html).ok
    internal = render_assessment(document, "internal")
    assert "live-source evidence gate is held closed" in internal
    assert "4 of 300 census records" in internal
    assert lint_screen_census(internal).ok


def test_stale_current_pointer_never_supplies_report_truth(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    changed = copy.deepcopy(searches)
    changed["results"]["sam.gov"][0]["title"] = "Changed after ledger backfill"

    resolved = resolve_current_live_report(
        "Testco", changed, seed["profile"],
        state_dir=seed["state_dir"], review_dir=seed["review_dir"])
    assert resolved.state == LiveReportState.INVALID
    assert "sweep evidence has changed" in resolved.problem
    document = build_document(
        "Testco", searches=changed, qualify=_qualify(changed),
        as_of=NOW.date())
    assert document.board.pursuits == []
    assert document.verdict_totals == {}
    assert not [entry for entry in document.watchlist.entries
                if entry.source == "sam.gov"]
    assert not [play for play in document.partnering.plays
                if play.kind == "monitor"]
    assert any("cutover invalid" in gap and "live lane held closed" in gap
               for gap in document.gaps)

    pack = build_fact_pack(
        "Testco", searches=changed, qualify_report=_qualify(changed),
        profile=seed["profile"], as_of=NOW.date())
    assert not [fact for fact in pack.facts if fact.tier == "notice"]
    assert any("live facts held closed" in warning for warning in pack.warnings)


@pytest.mark.parametrize("bad_scope", [
    None,
    {"mode": "focus", "agencies": []},
], ids=["removed", "malformed"])
def test_scoped_pointer_with_unresolvable_sweep_scope_fails_closed(
        tmp_path, monkeypatch, bad_scope):
    searches = _cutover_sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{
            "name": "Department of Homeland Security", "abbr": "DHS",
        }],
    }
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    assert seed["binding"]["scope_designator"] == "agency_dhs"
    _persist(seed)

    changed = copy.deepcopy(searches)
    if bad_scope is None:
        changed.pop("search_scope")
    else:
        changed["search_scope"] = bad_scope
    resolved = resolve_current_live_report(
        "Testco", changed, seed["profile"],
        state_dir=seed["state_dir"], review_dir=seed["review_dir"])
    assert resolved.state == LiveReportState.INVALID
    assert "cannot bind this sweep" in resolved.problem

    document = build_document(
        "Testco", searches=changed, qualify=_qualify(changed),
        as_of=NOW.date())
    assert document.board.pursuits == []
    assert document.verdict_totals == {}
    pack = build_fact_pack(
        "Testco", searches=changed, qualify_report=_qualify(changed),
        profile=seed["profile"], as_of=NOW.date())
    assert not [fact for fact in pack.facts if fact.tier == "notice"]


def test_backfilled_can_release_is_an_unstubbed_release_conjunction(
        tmp_path, monkeypatch):
    from agents.assess.ledger import current_assess_pointer_path
    from agents.assess.approval import assess_approval_for_release
    from agents.reports.release import release_state

    assert "enforce_strict_run" not in \
        inspect.signature(assess_approval_for_release).parameters

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    html = report_dir / "testco.federal_opportunity_assessment.html"
    body = "<html><body>release candidate</body></html>"
    html.write_text(body, encoding="utf-8")
    html.with_suffix(".qa.json").write_text(json.dumps({
        "state": "release",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }), encoding="utf-8")

    blocked = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert blocked["approval_status"] == "invalid"
    assert blocked["releasable"] is False
    assert "not approved for release" in blocked["reason"]
    assert any("required-source coverage gaps" in problem
               for problem in blocked["approval_problems"])

    partial, partial_diagnostics = _approve_partial_and_materialize(seed)
    assert partial.can_release() is True
    assert partial.partial_release_approved is True
    assert not [note for note in partial_diagnostics
                if "blocker manifest changed" in note]

    stale_build = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert stale_build["releasable"] is False
    assert "predates the current strict Assess run" in stale_build["reason"]

    pointer = current_assess_pointer_path(
        "Testco", state_dir=seed["state_dir"])
    pointer_mtime_ns = pointer.stat().st_mtime_ns
    os.utime(html, ns=(pointer_mtime_ns, pointer_mtime_ns))
    os.utime(html.with_suffix(".qa.json"),
             ns=(pointer_mtime_ns, pointer_mtime_ns))
    equal_mtime = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert equal_mtime["releasable"] is False
    assert "predates the current strict Assess run" in equal_mtime["reason"]

    # A post-pointer Produce build clears the artifact-freshness leg only
    # when its QA sidecar also STAMPS the current run id (Cycle 4 run-id
    # pairing): mtime alone cannot prove which run priced a build after
    # restores, copies, or same-instant writes. Nanosecond mtimes make the
    # ordering explicit without sleeping or depending on filesystem
    # timestamp granularity.
    body = "<html><body>strict ledger release candidate</body></html>"
    html.write_text(body, encoding="utf-8")
    html.with_suffix(".qa.json").write_text(json.dumps({
        "state": "release",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }), encoding="utf-8")
    post_pointer_ns = pointer_mtime_ns + 1_000_000_000
    os.utime(html, ns=(post_pointer_ns, post_pointer_ns))
    os.utime(html.with_suffix(".qa.json"),
             ns=(post_pointer_ns, post_pointer_ns))
    unstamped = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert unstamped["releasable"] is False
    assert "no strict run stamp" in unstamped["reason"]

    html.with_suffix(".qa.json").write_text(json.dumps({
        "state": "release",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "assess_run_id": partial.run_id,
    }), encoding="utf-8")
    os.utime(html.with_suffix(".qa.json"),
             ns=(post_pointer_ns, post_pointer_ns))
    released = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert released["releasable"] is True
    assert released["path"] == str(html)

    # A stamp from a SUPERSEDED run can never ride a fresh mtime through
    # the gate; only the exact current pointer run id certifies.
    html.with_suffix(".qa.json").write_text(json.dumps({
        "state": "release",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "assess_run_id": "assess:v1:superseded",
    }), encoding="utf-8")
    os.utime(html.with_suffix(".qa.json"),
             ns=(post_pointer_ns, post_pointer_ns))
    mispaired = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert mispaired["releasable"] is False
    assert "does not certify the current strict Assess run" \
        in mispaired["reason"]

    html.with_suffix(".qa.json").write_text(json.dumps({
        "state": "release",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "assess_run_id": partial.run_id,
    }), encoding="utf-8")
    os.utime(html.with_suffix(".qa.json"),
             ns=(post_pointer_ns, post_pointer_ns))

    # Explicit pointer removal is the reversible rollback. ABSENT returns to
    # the exact pre-ledger approval path; invalid/stale never does.
    current_assess_pointer_path(
        "Testco", state_dir=seed["state_dir"]).unlink()
    rolled_back = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert rolled_back["releasable"] is True


def test_expired_after_backfill_holds_board_facts_and_release_closed(
        tmp_path, monkeypatch):
    import agents.assess.approval as approval_module
    from agents.reports.release import release_state

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    partial, _ = _approve_partial_and_materialize(seed)
    assert partial.can_release() is True
    after_deadline = date(2026, 9, 1)
    document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=after_deadline)
    assert document.board.pursuits == []
    assert document.verdict_totals == {}
    assert any("deadline(s) no longer future" in gap for gap in document.gaps)

    pack = build_fact_pack(
        "Testco", searches=searches, qualify_report=_qualify(searches),
        profile=seed["profile"], as_of=after_deadline)
    assert not [fact for fact in pack.facts if fact.tier == "notice"]
    assert any("deadline(s) no longer future" in warning
               for warning in pack.warnings)

    report_dir = tmp_path / "expired_reports"
    report_dir.mkdir()
    html = report_dir / "testco.federal_opportunity_assessment.html"
    body = "<html><body>expired candidate</body></html>"
    html.write_text(body, encoding="utf-8")
    html.with_suffix(".qa.json").write_text(json.dumps({
        "state": "release",
        "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }), encoding="utf-8")
    monkeypatch.setattr(approval_module, "_utc_today", lambda: after_deadline)
    state = release_state(
        "Testco", report_dir=str(report_dir),
        review_dir=str(seed["review_dir"]))
    assert state["releasable"] is False
    assert any("deadline(s) no longer future" in problem
               for problem in state["approval_problems"])


def _set_evening_clocks(monkeypatch) -> tuple[date, date]:
    """Model 18:30 MDT: the operator is on Aug 31, UTC is Sep 1."""
    import agents.assess.live_report as live_report_module
    import agents.reports.document as document_module
    import agents.reports.facts as facts_module

    local_day = date(2026, 8, 31)
    utc_day = date(2026, 9, 1)

    class EveningLocalDate(date):
        @classmethod
        def today(cls):
            return local_day

    monkeypatch.setattr(document_module, "date", EveningLocalDate)
    monkeypatch.setattr(facts_module, "date", EveningLocalDate)
    monkeypatch.setattr(live_report_module, "utc_today", lambda: utc_day)
    return local_day, utc_day


def test_evening_defaults_keep_local_stamps_and_utc_comparisons_when_absent(
        tmp_path, monkeypatch):
    """Cycle 5: a dormant pointer cannot advance a legacy report's date."""
    import agents.reports.views as views_module
    import tools.snapshots as snapshots_module

    searches = _cutover_sweep()
    next(row for row in searches["results"]["sam.gov"]
         if row["source_id"] == "N1")["response_deadline"] = "2026-09-01"
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    local_day, utc_day = _set_evening_clocks(monkeypatch)
    snapshot_cutoffs = []
    monkeypatch.setattr(
        snapshots_module, "load_latest_snapshot",
        lambda _kind, _slug, before=None: snapshot_cutoffs.append(before))

    document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches))
    pack = build_fact_pack(
        "Testco", searches=searches, qualify_report=_qualify(searches),
        profile=seed["profile"])

    assert document.as_of == local_day
    assert pack.as_of == local_day
    assert snapshot_cutoffs == [local_day]
    pursuit = next(row for row in document.board.pursuits
                   if row.source_id == "N1")
    assert pursuit.grade.as_of == utc_day
    timing = next(row for row in pursuit.grade.dimensions
                  if row.dimension == "timing")
    assert timing.basis.startswith("0d to due")
    candidate_cutoffs = {
        candidate.fit.as_of
        for play in document.partnering.plays
        for candidate in play.candidates
        if candidate.fit is not None
    }
    assert candidate_cutoffs == {utc_day}

    # The sales window is dormant by default, but if enabled it must consume
    # the pursuit's operational cutoff rather than the presentation stamp.
    monkeypatch.setattr(views_module, "SALES_DEADLINE_STYLE", "window")
    gated = views_module.gate_for_sales(document)
    assert next(row for row in gated.board.pursuits
                if row.rank == pursuit.rank).response_deadline == \
        "closes within 0 days"
    assert not seed["state_dir"].exists()


def test_evening_defaults_keep_current_cutoff_utc_and_explicit_date_replays(
        tmp_path, monkeypatch):
    import agents.assess.approval as approval_module
    import ui.server as server

    searches = _cutover_sweep()
    next(row for row in searches["results"]["sam.gov"]
         if row["source_id"] == "N1")["response_deadline"] = "2026-09-01"
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    local_day, utc_day = _set_evening_clocks(monkeypatch)

    document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches))
    pack = build_fact_pack(
        "Testco", searches=searches, qualify_report=_qualify(searches),
        profile=seed["profile"])
    assert document.as_of == local_day
    assert pack.as_of == local_day
    assert document.board.pursuits == []
    assert not [fact for fact in pack.facts if fact.tier == "notice"]
    assert any("deadline(s) no longer future" in gap for gap in document.gaps)
    assert approval_module._utc_today() == utc_day
    assert server._utc_today() == utc_day

    # An explicit as_of remains the deterministic replay seam and therefore
    # controls both the displayed date and strict deadline eligibility.
    replay_document = build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=local_day)
    replay_pack = build_fact_pack(
        "Testco", searches=searches, qualify_report=_qualify(searches),
        profile=seed["profile"], as_of=local_day)
    replay_pursuit = next(row for row in replay_document.board.pursuits
                          if row.source_id == "N1")
    assert replay_document.as_of == local_day
    assert replay_pack.as_of == local_day
    assert replay_pursuit.grade.as_of == local_day
    assert [fact for fact in replay_pack.facts if fact.tier == "notice"]


def test_partial_release_authorization_fails_closed_when_blockers_drift(
        tmp_path, monkeypatch):
    from agents.assess.approval import (
        approve_assess_results, required_blocker_manifest,
    )
    from tests.test_assess_ledger import _horizon_payload

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    displayed, _, _ = materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    displayed_blockers = required_blocker_manifest(displayed.coverage)
    approved = approve_assess_results(
        "Testco",
        review_dir=str(seed["review_dir"]),
        partial_release_approved=True,
        assess_state_dir=str(seed["state_dir"]),
        partial_release_run_id=displayed.run_id,
        partial_release_blockers_sha256=displayed_blockers["sha256"],
    )
    assert approved["partial_release_approved"] is True

    # The operator approved the then-current missing Horizon gate. Completing
    # that source changes the exact blocker rows; even an improvement cannot
    # silently carry a prior exception into a new immutable run.
    horizon = _horizon_payload()
    horizon["binding"] = seed["binding"]
    (seed["review_dir"] / "testco.horizon.json").write_text(
        json.dumps(horizon), encoding="utf-8")
    run, _, diagnostics = materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    assert run.partial_release_approved is False
    assert run.can_release() is False
    assert any("blocker manifest changed" in note for note in diagnostics)


def test_partial_release_api_and_ui_require_an_explicit_boolean_confirmation(
        tmp_path, monkeypatch):
    import ui.server as server
    from agents.assess.approval import (
        AssessApprovalError, approve_assess_results,
    )

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    with pytest.raises(AssessApprovalError, match="must be a boolean"):
        approve_assess_results(
            "Testco",
            review_dir=str(seed["review_dir"]),
            partial_release_approved="yes",  # type: ignore[arg-type]
            assess_state_dir=str(seed["state_dir"]),
        )

    monkeypatch.setattr(server, "REVIEW_DIR", str(seed["review_dir"]))
    monkeypatch.setattr(server, "CLEANED_DIR", str(seed["cleaned_dir"]))
    monkeypatch.setattr(server, "ASSESS_RUN_DIR", str(seed["state_dir"]))
    client = server.app.test_client()
    malformed = client.post("/api/review/assess-approve", json={
        "client_name": "Testco", "partial_release_approved": "yes"})
    assert malformed.status_code == 400

    displayed = server._assess_ledger_snapshot("Testco")
    assert displayed["run_id"]
    assert displayed["blocking_sources_sha256"]
    missing_snapshot = client.post("/api/review/assess-approve", json={
        "client_name": "Testco", "partial_release_approved": True})
    assert missing_snapshot.status_code == 409

    response = client.post("/api/review/assess-approve", json={
        "client_name": "Testco",
        "partial_release_approved": True,
        "partial_release_run_id": displayed["run_id"],
        "partial_release_blockers_sha256":
            displayed["blocking_sources_sha256"],
    })
    assert response.status_code == 200
    assert response.get_json()["partial_release_approved"] is True
    snapshot = server._assess_ledger_snapshot("Testco")
    assert snapshot["partial_release_approved"] is True
    assert snapshot["can_release"] is True

    ui_source = (Path(server.ROOT) / "ui" / "index.html").read_text(
        encoding="utf-8")
    assert "Approve release with the ${disclosedGaps} listed required " \
        "source-coverage gap" in ui_source
    assert 'class="assess-partial-release-confirm" type="checkbox"' \
        in ui_source
    assert "partial_release_approved: true" in ui_source
    # Cycle 3 review finding (2026-07-12): the PLAIN approve button sends NO
    # partial key (absent = preserve a standing grant); only the panel's
    # explicit true and a deliberate explicit false touch that axis.
    assert "partial_release_approved: false" not in ui_source
    assert "JSON.stringify({ client_name: DETAIL.client_name })" in ui_source
    assert "partial_release_run_id: strictLedger.run_id" in ui_source
    assert "strictLedger.blocking_sources_sha256" in ui_source
    # 2026-07-25: the standalone approve-results press is retired; the
    # explicit disclosed-gaps panel remains the only partial-release UI.
    assert "Approve with the disclosed gaps" in ui_source


def test_partial_release_api_rejects_stale_displayed_run_and_blockers(
        tmp_path, monkeypatch):
    import ui.server as server
    from tests.test_assess_ledger import _horizon_payload

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    _persist(seed)
    materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    monkeypatch.setattr(server, "REVIEW_DIR", str(seed["review_dir"]))
    monkeypatch.setattr(server, "CLEANED_DIR", str(seed["cleaned_dir"]))
    monkeypatch.setattr(server, "ASSESS_RUN_DIR", str(seed["state_dir"]))
    client = server.app.test_client()
    displayed = server._assess_ledger_snapshot("Testco")
    approval_path = seed["review_dir"] / "testco.assess_approval.json"
    approval_before = approval_path.read_bytes()

    stale_hash = client.post("/api/review/assess-approve", json={
        "client_name": "Testco",
        "partial_release_approved": True,
        "partial_release_run_id": displayed["run_id"],
        "partial_release_blockers_sha256": "0" * 64,
    })
    assert stale_hash.status_code == 409
    assert any("coverage gaps changed" in problem
               for problem in stale_hash.get_json()["problems"])
    assert approval_path.read_bytes() == approval_before

    horizon = _horizon_payload()
    horizon["binding"] = seed["binding"]
    (seed["review_dir"] / "testco.horizon.json").write_text(
        json.dumps(horizon), encoding="utf-8")
    materialize_current_assess_run(
        "Testco",
        sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"],
        review_dir=str(seed["review_dir"]),
    )
    refreshed = server._assess_ledger_snapshot("Testco")
    assert refreshed["run_id"] != displayed["run_id"]

    stale_run = client.post("/api/review/assess-approve", json={
        "client_name": "Testco",
        "partial_release_approved": True,
        "partial_release_run_id": displayed["run_id"],
        "partial_release_blockers_sha256":
            displayed["blocking_sources_sha256"],
    })
    assert stale_run.status_code == 409
    assert any("run changed" in problem
               for problem in stale_run.get_json()["problems"])
    assert approval_path.read_bytes() == approval_before


def test_strict_verdict_totals_remain_posting_level_while_board_dedupes(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    amended = copy.deepcopy(searches)
    posting_index = dict(seed["posting_index"])
    for current_id, old_id in (("N1", "N1-OLD"), ("N3", "N3-OLD")):
        current = next(
            row for row in amended["results"]["sam.gov"]
            if row["source_id"] == current_id)
        old = copy.deepcopy(current)
        old["source_id"] = old_id
        old["posted_date"] = "2026-06-01"
        old["api_url"] = f"https://sam.gov/opp/{old_id}/view"
        old["raw_payload"]["notice_id"] = old_id
        amended["results"]["sam.gov"].append(old)
        posting_index[old_id] = posting_index[current_id]

    projected = project_live_ledger(
        seed["run"].live,
        posting_index,
        amended,
        can_release=False,
        live_coverage_complete=True,
    )
    assert len(projected.actionable) == 1
    assert projected.verdict_counts() == {
        "monitor": 2, "pursue": 2, "research": 2}
    document = build_document(
        "Testco",
        searches=amended,
        qualify=_qualify(amended),
        as_of=NOW.date(),
        _live_report=projected,
        _cap_profile=seed["profile"],
    )
    assert document.counts()["pursue_notices"] == 1
    assert document.verdict_totals["pursue"] == 2
    assert document.counts()["monitor_notices"] == 2


def test_parity_report_uses_full_counts_and_exposes_cutover_deltas(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    report = build_live_parity_report(
        "Testco",
        searches,
        seed["profile"],
        sweep_artifact="searches_testco.json",
        qualify=_qualify(searches),
        requirement_reviews=seed["requirements"],
        as_of=NOW,
        state_dir=seed["state_dir"],
        review_dir=seed["review_dir"],
    )
    assert report["strict_projection"]["available"] is True
    assert report["cutover_eligible"] is True
    assert report["current_pointer"]["report_truth_active"] is False
    assert report["current_pointer"]["can_release"] is None
    assert report["legacy"]["counts"] == build_document(
        "Testco", searches=searches, qualify=_qualify(searches),
        as_of=NOW.date(), _live_report=False,
        _cap_profile=seed["profile"]).counts()
    assert report["delta"]["board_exited"] == ["N2"]
    assert report["delta"]["counts"]["pursue_notices"] == {
        "legacy": 2, "strict": 1}
    assert report["delta"]["verdict_totals"]["research"] == {
        "legacy": 0, "strict": 2}

    from agents.assess.live_report import project_sweep_live_report
    with pytest.raises(AssessLedgerError, match="different client"):
        project_sweep_live_report(
            "Other Client", searches, seed["profile"], seed["binding"],
            as_of=NOW)
    drifted = {**seed["binding"], "sweep_sha256": "stale"}
    with pytest.raises(AssessLedgerError, match="binding has drifted"):
        project_sweep_live_report(
            "Testco", searches, seed["profile"], drifted, as_of=NOW)


def test_parity_report_marks_pre_scope_sweep_strict_unavailable(
        tmp_path, monkeypatch):
    searches = _cutover_sweep()
    searches.pop("search_scope")
    seed = _seed_runtime(
        tmp_path, monkeypatch, {**searches, "search_scope": {"all": True}})
    report = build_live_parity_report(
        "Testco",
        searches,
        seed["profile"],
        sweep_artifact="searches_testco.json",
        qualify=_qualify(searches),
        as_of=NOW,
        state_dir=seed["state_dir"],
        review_dir=seed["review_dir"],
    )
    assert report["legacy"]["available"] is True
    assert report["strict"]["available"] is False
    assert report["strict"]["counts"] is None
    assert report["delta"]["available"] is False
    assert report["cutover_eligible"] is False
    assert "no explicit, valid search scope" in report["strict"]["problem"]

    diagnostic = build_live_parity_report(
        "Testco",
        searches,
        seed["profile"],
        sweep_artifact="searches_testco.json",
        qualify=_qualify(searches),
        as_of=NOW,
        state_dir=seed["state_dir"],
        review_dir=seed["review_dir"],
        diagnostic_scope_override="all",
    )
    assert diagnostic["strict"]["available"] is True
    assert diagnostic["diagnostic_scope_override"] == "all"
    assert diagnostic["cutover_eligible"] is False
    assert any("cannot activate or persist" in problem
               for problem in diagnostic["cutover_problems"])


def test_review_board_is_strict_under_a_current_pointer(tmp_path, monkeypatch):
    """ONE opportunity truth (2026-07-12, A3): with a CURRENT pointer the
    Review step's board rows come from the immutable ledger the deliverable
    consumes; raw-sweep triage demotes to a labeled diagnostic view. Without
    a pointer the legacy board is unchanged."""
    flask = pytest.importorskip("flask")  # noqa: F841
    import ui.server as server

    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    # client_state derives data/cleaned from the review dir's parent layout;
    # give the flat seed the conventional <root>/data/<kind> shape
    webroot = tmp_path / "webroot" / "data"
    webroot.mkdir(parents=True)
    (webroot / "review").symlink_to(seed["review_dir"])
    (webroot / "cleaned").symlink_to(seed["cleaned_dir"])
    monkeypatch.setattr(server, "REVIEW_DIR", str(webroot / "review"))
    monkeypatch.setattr(server, "CLEANED_DIR", str(webroot / "cleaned"))
    monkeypatch.setattr(server, "ASSESS_RUN_DIR", str(seed["state_dir"]))

    # no pointer yet: legacy board is the truth, strict block absent
    steps = server.build_steps("testco")
    review = next(s for s in steps if s["key"] == "review")
    assert review["detail"]["strict"] is None
    assert "pursue" in review["summary"]

    # materialize the pointer: strict records become the board
    materialize_current_assess_run(
        "Testco", sweep_path=seed["sweep_path"],
        state_dir=seed["state_dir"], review_dir=str(seed["review_dir"]))
    steps = server.build_steps("testco")
    review = next(s for s in steps if s["key"] == "review")
    strict = review["detail"]["strict"]
    assert strict and strict["state"] == "current"
    assert strict["run_id"].startswith("assess:v3:")
    assert len(strict["rows"]) >= 1
    row = strict["rows"][0]
    assert {"notice_id", "title", "agency", "classification",
            "recommendation", "deadline", "requirement_reviewed",
            "url"} <= set(row)
    assert review["summary"].startswith("STRICT:")
    assert "diagnostic only" in review["summary"]
    # legacy rows remain available for the diagnostic drawer
    assert review["detail"]["opportunities"]

    # the dashboard names the demotion and the strict board in source
    ui_source = (Path(server.ROOT) / "ui" / "index.html").read_text(
        encoding="utf-8")
    assert "Diagnostic · legacy raw-sweep triage (not report truth)" in ui_source
    assert "function buildStrictReviewBoard" in ui_source
    assert "strict report truth" in ui_source
    # Phase B (2026-07-12): ONE canonical status derivation feeds the header
    # band; documents live behind the Deliverables tab; the report opens on
    # its own page instead of embedding in the operating screen
    assert "function deriveStatus" in ui_source
    assert "tab-deliverables" in ui_source
    assert "Open full report" in ui_source
    assert '<iframe src="/report?path=${encodeURIComponent(fb.path)}"' not in ui_source
