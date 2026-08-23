"""V4.2 auto-composer contract battery (fix round, 2026-07-19).

Covers the independent review's nine findings: C1/C3 handshake, slug vs
display identity, machine-screened safety, publication ordering, horizon
calendar math with the real mark43 APFS facts, figure provenance and
reconciliation, determinism under input permutation, prose length limits
with fragment regressions, and the sanctioned relevance binding.
Offline; tmp roots; the LLM transport is stubbed; adapter acceptance
runs the REAL SubprocessRunnerAdapter against the real composer.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import date

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from agents.reports import composer  # noqa: E402
from tools.capability import load_profile  # noqa: E402

TODAY = date(2026, 7, 19)
CORE_NOTICE = {
    "source": "sam.gov", "source_id": "aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1",
    "title": "Computer-Aided Dispatch modernization",
    "agency": "Department of Homeland Security",
    "naics_code": "541512", "response_deadline": "2026-08-20",
    "contacts": [{"name": "Pat Doe", "title": "CO"}],
    "raw_payload": {"notice_id": "aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1", "solicitation": "70RSAT26R0001",
                    "description_snippet": "computer-aided dispatch refresh"},
}


def _sweep() -> dict:
    return {
        "client": "Testco", "generated_at": "2026-07-18",
        "results": {
            "sam.gov": [
                copy.deepcopy(CORE_NOTICE),
                {**copy.deepcopy(CORE_NOTICE),
                 "source_id": "bbb2bbb2bbb2bbb2bbb2bbb2bbb2bbb2",
                 "title": "Records management system support",
                 "response_deadline": "2026-09-01",
                 "raw_payload": {"notice_id": "bbb2bbb2bbb2bbb2bbb2bbb2bbb2bbb2",
                                 "solicitation": "70RSAT26R0002",
                                 "description_snippet":
                                 "records management system upgrade"}},
                {**copy.deepcopy(CORE_NOTICE),
                 "source_id": "ccc3ccc3ccc3ccc3ccc3ccc3ccc3ccc3",
                 "title": "Computer-aided dispatch replacement",
                 "raw_payload": {"notice_id": "ccc3ccc3ccc3ccc3ccc3ccc3ccc3ccc3",
                                 "description_snippet":
                                 "computer-aided dispatch"}},
                {**copy.deepcopy(CORE_NOTICE),
                 "source_id": "ddd4ddd4ddd4ddd4ddd4ddd4ddd4ddd4",
                 "title": "Janitorial services",
                 "raw_payload": {"notice_id": "ddd4ddd4ddd4ddd4ddd4ddd4ddd4ddd4",
                                 "description_snippet": "custodial work"}},
            ],
            "triage": {"ccc3ccc3ccc3ccc3ccc3ccc3ccc3ccc3": {"verdict": "discard", "reason": "dupe"}},
            "incumbent_buyer_map": {
                "retrieved_at": "2026-07-18",
                "buyers": [
                    {"buyer": "US IMMIGRATION AND CUSTOMS ENFORCEMENT",
                     "agency": "Department of Homeland Security",
                     "records": [
                         {"kind": "award", "recipient": "ACTIVE VENDOR ONE",
                          "amount": 5_000_000.0,
                          "amount_basis": "obligated_to_date",
                          "end_date": "2027-01-15",
                          "award_id": "70CDCR25F0001",
                          "generated_internal_id":
                              "CONT_AWD_70CDCR25F0001_7012_IDV1_7012",
                          "matched_terms": ["dispatch system"],
                          "description": "dispatch system platform support"},
                         {"kind": "award", "recipient": "EXPIRED VENDOR",
                          "amount": 90_000_000.0,
                          "amount_basis": "obligated_to_date",
                          "end_date": "2021-11-19",
                          "award_id": "OLD1",
                          "generated_internal_id": "CONT_AWD_OLD1_X_X_X",
                          "matched_terms": ["case management"],
                          "description": "closed award"},
                         {"kind": "award", "recipient": "BIG PRIME",
                          "amount": 1_000_000.0,
                          "amount_basis": "obligated_to_date",
                          "end_date": "2027-02-01",
                          "award_id": "PA-1",
                          "generated_internal_id":
                              "CONT_AWD_PA1_7001_IDV1_7001",
                          "description":
                              "computer-aided dispatch integration prime"},
                     ]},
                    {"buyer": "INTERNAL REVENUE SERVICE",
                     "agency": "Department of the Treasury",
                     "records": [
                         {"kind": "award", "recipient": "ACTIVE VENDOR TWO",
                          "amount": 9_000_000.0,
                          "amount_basis": "obligated_to_date",
                          "end_date": "2026-12-01",
                          "award_id": "2032H822F0002",
                          "generated_internal_id":
                              "CONT_AWD_2032H822F0002_2050_IDV2_2050",
                          "matched_products": ["PRODUCT TWO"],
                          "description": "incident reporting suite"},
                     ]},
                ]},
            "subawards": {
                "primes": [{"name": "BIG PRIME", "total": 9_000_000.0}],
                "edges": {"541512": [
                    {"prime": "BIG PRIME", "prime_award_id": "PA-1",
                     "prime_award_generated_id":
                         "CONT_AWD_PA1_7001_IDV1_7001",
                     "subaward_id": "SUB-1",
                     "source": "https://api.usaspending.gov/api/v2/subawards/SUB-1",
                     "awarding_agency": "Department of Homeland Security",
                     "sub": "S", "amount": 1.0,
                     "description":
                         "computer-aided dispatch integration subcontract"}]},
            },
            "forecast_signals": {"matched": [
                {"source_id": "F-1", "agency": "DHS", "component": "CBP",
                 "title": "Computer-aided dispatch refresh forecast",
                 "anticipated_solicitation": "06/30/2026",
                 "anticipated_award": "08/20/2026",
                 "forecast_status": "planned"}]},
            "contract_awards": {"recompetes": [
                {"awardee": "OLD CO", "piid": "PIID-9",
                 "completion": "2026-08-30", "agency": "DHS",
                 "description": "computer-aided dispatch platform support"}]},
        },
    }


def _zero_best_fit_sweep() -> dict:
    """No relevant SAM row and no award evidence: the genuine safe stop."""
    bare = _sweep()
    bare["results"]["sam.gov"] = [{
        **CORE_NOTICE,
        "source_id": "ddd4ddd4ddd4ddd4ddd4ddd4ddd4ddd4",
        "title": "Janitorial",
        "raw_payload": {
            "notice_id": "ddd4ddd4ddd4ddd4ddd4ddd4ddd4ddd4",
            "description_snippet": "custodial",
        },
    }]
    bare["results"]["incumbent_buyer_map"]["buyers"] = []
    return bare


def _award_only_sweep() -> dict:
    """No relevant SAM notice; one defensible active client corridor."""
    sweep = _zero_best_fit_sweep()
    sweep["results"]["incumbent_buyer_map"]["buyers"] = [{
        "buyer": "NATIONAL TRANSPORTATION SAFETY BOARD",
        "agency": "National Transportation Safety Board",
        "records": [{
            "kind": "award",
            "recipient": "TESTCO, INC.",
            "amount": 194_760.0,
            "amount_basis": "obligated_to_date",
            "end_date": "2026-09-26",
            "award_id": "9531BM23P0059",
            "generated_internal_id":
                "CONT_AWD_9531BM23P0059_9508_NONE_NONE",
            "matched_products": ["Testco"],
            "matched_terms": ["incident reporting"],
            "description": "incident reporting software subscription",
        }],
    }]
    return sweep


def _add_distinct_acquisition_notice(sweep: dict) -> dict:
    """A relevant CSO route that is intentionally not a Best Fit notice."""

    sweep["results"]["sam.gov"].append({
        "source": "sam.gov",
        "source_id": "eee5eee5eee5eee5eee5eee5eee5eee5",
        "title": "Commercial Solutions Opening for dispatch data support",
        "agency": "Department of Homeland Security",
        "naics_code": "541512",
        "response_deadline": "2026-10-01",
        "raw_payload": {
            "notice_id": "eee5eee5eee5eee5eee5eee5eee5eee5",
            "solicitation": "70RSAT26S0005",
            "type": "Special Notice",
            "set_aside": "Small Business",
            "description_snippet": (
                "Commercial Solutions Opening for computer-aided dispatch "
                "data integration and incident reporting support"),
        },
    })
    return sweep


def _route_selections(sweep: dict):
    inputs = composer.load_client_inputs("Testco")
    profile = load_profile("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    competitors = composer.select_competitors(
        sweep, today=TODAY, scope=inputs["scope"],
        client_name="Testco", taxonomy=inputs["taxonomy"],
        naics_boundary=profile.naics_boundary)
    return inputs, profile, best_fit, competitors


CAL_HEADER = "client,record_ref,engine_score,disagreement,scope_basis\n"


def _mint_receipt(root, slug="testco", display="Testco"):
    """Mint a VALID relevance receipt through the chain's own producer:
    one contract, no second format."""
    from pathlib import Path

    from agents.assessment_chain import (
        _relevance_input_binding, write_relevance_receipt)
    cal = root / "data" / "state" / "relevance" / f"{slug}.calibration.csv"
    sweep = root / "data" / "cleaned" / f"searches_{slug}.json"
    write_relevance_receipt(
        Path(root), slug, display,
        command=["-m", "tools.relevance.calibrate", "--client", display,
                 "--out", str(cal)],
        sweep_path=sweep,
        started_at="2026-07-19T00:00:00+00:00",
        completed_at="2026-07-19T00:00:01+00:00",
        inputs=_relevance_input_binding(Path(root), slug),
        calibration_path=cal,
        summary=f"[calibrate] {display}: 4 records · 0 FP · 0 FN")


def _world_client(root, slug, display, sweep=None):
    (root / "clients" / slug).mkdir(parents=True, exist_ok=True)
    (root / "clients" / slug / "profile.json").write_text(json.dumps({
        "client_name": display,
        "capability_terms": {
            "core": ["computer-aided dispatch", "records management system",
                     "dispatch system", "incident reporting"],
            "adjacent": ["case management"], "excluded": []},
        "naics_boundary": ["541512"],
    }), encoding="utf-8")
    payload = sweep or _sweep()
    payload["client"] = display
    sweep_path = root / "data" / "cleaned" / f"searches_{slug}.json"
    sweep_path.parent.mkdir(parents=True, exist_ok=True)
    sweep_path.write_text(json.dumps(payload), encoding="utf-8")
    rel = root / "data" / "state" / "relevance" / f"{slug}.calibration.csv"
    rel.parent.mkdir(parents=True, exist_ok=True)
    rel.write_text(CAL_HEADER
                   + f"{display},aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1,6,,preset:all_federal@v1\n",
                   encoding="utf-8")
    _mint_receipt(root, slug, display)
    return sweep_path


def _riverbed_profile(root) -> None:
    """Small profile for Riverbed-shaped unit cards composed in tmp roots."""
    client_dir = root / "clients" / "riverbed"
    client_dir.mkdir(parents=True, exist_ok=True)
    (client_dir / "profile.json").write_text(json.dumps({
        "client_name": "Riverbed",
        "capability_terms": {
            "core": ["network performance monitoring", "SolarWinds",
                     "Aternity"],
            "adjacent": [],
            "excluded": [],
        },
        "naics_boundary": ["541512"],
    }), encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import agents.reports.board_content as bc
    import agents.review as review
    import run_compose
    import tools.capability as cap
    import tools.relevance.scope as scope_mod
    import tools.relevance.taxonomy as tax_mod
    monkeypatch.setattr(bc, "_ROOT", str(tmp_path))
    monkeypatch.setattr(cap, "ROOT", str(tmp_path))
    monkeypatch.setattr(cap, "CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setattr(tax_mod, "_ROOT", str(tmp_path))
    monkeypatch.setattr(scope_mod, "_ROOT", str(tmp_path))
    monkeypatch.setattr(run_compose, "_ROOT", str(tmp_path))
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path / "cache"))

    def _sweep_for(client, **_kwargs):
        from tools.capability import _slug
        return str(tmp_path / "data" / "cleaned"
                   / f"searches_{_slug(client)}.json")
    monkeypatch.setattr(review, "sweep_artifact_path", _sweep_for)
    _world_client(tmp_path, "testco", "Testco")
    return tmp_path


def _select_all(sweep):
    inputs = composer.load_client_inputs("Testco")
    bf = composer.select_best_fit(sweep, inputs["taxonomy"], inputs["scope"])
    comp = composer.select_competitors(
        sweep, today=TODAY, scope=inputs["scope"],
        client_name="Testco", taxonomy=inputs["taxonomy"])
    team = composer.select_teaming(sweep, bf, comp)
    featured = {
        str(value).strip()
        for pick in [*bf, *comp]
        for value in (
            getattr(pick, "award_id", ""), getattr(pick, "gid", ""))
        if str(value).strip()
    }
    hz = composer.select_horizon(
        sweep, None, today=TODAY, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"],
        featured_award_identities=featured)
    return bf, comp, team, hz


# ── determinism under permutation (finding 7) ───────────────────────────────

def test_selection_survives_input_permutation(world):
    base = _sweep()
    permuted = _sweep()
    permuted["results"]["sam.gov"] = list(
        reversed(permuted["results"]["sam.gov"]))
    permuted["results"]["incumbent_buyer_map"]["buyers"] = list(
        reversed(permuted["results"]["incumbent_buyer_map"]["buyers"]))
    for buyer in permuted["results"]["incumbent_buyer_map"]["buyers"]:
        buyer["records"] = list(reversed(buyer["records"]))
    a = _select_all(base)
    b = _select_all(permuted)
    assert [p.source_id for p in a[0]] == [p.source_id for p in b[0]]
    assert [(c.recipient, c.record.get("description"))
            for c in a[1]] == [(c.recipient, c.record.get("description"))
                               for c in b[1]]
    assert [(t.partner, t.basis) for t in a[2]] == [
        (t.partner, t.basis) for t in b[2]]
    assert [(h.kind, h.event_date, h.ident) for h in a[3]] == [
        (h.kind, h.event_date, h.ident) for h in b[3]]


def test_best_fit_is_actionable_sam_notice_family_first(world):
    inputs = composer.load_client_inputs("Testco")

    def notice(ident, notice_type, title, description):
        row = copy.deepcopy(CORE_NOTICE)
        row.update({"source_id": ident, "title": title})
        row["raw_payload"] = {
            "notice_id": ident,
            "type": notice_type,
            "description_snippet": description,
        }
        return row

    sweep = {"results": {"sam.gov": [
        notice("award", "Award Notice", "CAD award",
               "computer-aided dispatch records management system"),
        notice("generic-special", "Special Notice", "Vendor notice",
               "computer-aided dispatch records management system"),
        notice("source", "Sources Sought", "CAD market research",
               "computer-aided dispatch records management system"),
        notice("rfi", "Special Notice", "RFI for dispatch modernization",
               "computer-aided dispatch records management system"),
        notice("solicitation", "Solicitation", "Dispatch solicitation",
               "computer-aided dispatch"),
    ]}}
    picks = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"], n=5)
    assert [pick.source_id for pick in picks] == [
        "solicitation", "source", "rfi"]


def test_best_fit_scores_official_sam_attachment_text(world):
    inputs = composer.load_client_inputs("Testco")
    row = copy.deepcopy(CORE_NOTICE)
    row.update({"source_id": "attachment-proof", "title": "Modernization RFP"})
    row["raw_payload"] = {
        "notice_id": "attachment-proof",
        "type": "Solicitation",
        "description_snippet": "See the attached performance work statement.",
        "text": "The requirement is a computer-aided dispatch replacement.",
        "attachment_evidence": [{"resource_id": "a" * 32}],
        "attachment_evidence_sha256": "new-inventory",
    }
    picks = composer.select_best_fit(
        {"results": {
            "sam.gov": [row],
            "triage": {"attachment-proof": {
                "verdict": "discard", "reason": "metadata looked unrelated"}},
        }},
        inputs["taxonomy"], inputs["scope"])
    assert [pick.source_id for pick in picks] == ["attachment-proof"]
    assert picks[0].verdict.spans[0].field == "raw_payload.text"

    bound = copy.deepcopy(row)
    picks = composer.select_best_fit(
        {"results": {
            "sam.gov": [bound],
            "triage": {"attachment-proof": {
                "verdict": "discard", "reason": "reviewed duplicate",
                "attachment_evidence_sha256": "new-inventory"}},
        }},
        inputs["taxonomy"], inputs["scope"])
    assert picks == []


def test_equal_amount_records_have_a_stable_winner(world):
    sweep = _sweep()
    buyers = sweep["results"]["incumbent_buyer_map"]["buyers"]
    buyers[0]["records"] = [
        {"kind": "award", "recipient": "SAME CO", "amount": 100_000.0,
         "amount_basis": "obligated_to_date",
         "end_date": "2026-12-01", "matched_products": ["PRODUCT A"],
         "award_id": "SAME-A", "generated_internal_id": "CONT_AWD_A",
         "description": "record A"},
        {"kind": "award", "recipient": "SAME CO", "amount": 100_000.0,
         "amount_basis": "obligated_to_date",
         "end_date": "2026-12-01", "matched_products": ["PRODUCT B"],
         "award_id": "SAME-B", "generated_internal_id": "CONT_AWD_B",
         "description": "record B"},
    ]
    forward = composer.select_competitors(sweep, today=TODAY)
    buyers[0]["records"] = list(reversed(buyers[0]["records"]))
    reversed_run = composer.select_competitors(sweep, today=TODAY)
    winner_f = next(c for c in forward if c.recipient == "SAME CO")
    winner_r = next(c for c in reversed_run if c.recipient == "SAME CO")
    assert winner_f.record["description"] == winner_r.record["description"]


# ── horizon calendar math + real mark43 facts (finding 5) ───────────────────

def test_horizon_parses_apfs_dates_and_independent_milestones(world):
    hz = composer.select_horizon(_sweep(), None, today=TODAY)
    forecast = [h for h in hz if h.kind == "forecast"]
    assert forecast, "APFS MM/DD/YYYY forecast row must produce an event"
    # solicitation 06/30/2026 is past; award 08/20/2026 is the future
    # in-window milestone and must win
    assert forecast[0].event_date == "2026-08-20"
    assert "award" in forecast[0].why


def test_horizon_window_is_exact_calendar_months():
    assert composer.add_months(date(2026, 7, 19), 36) == date(2029, 7, 19)
    assert composer.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert composer.add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert composer.add_months(date(2025, 2, 28), 36) == date(2028, 2, 28)
    inside = {"results": {"forecast_signals": {"matched": [
        {"source_id": "IN", "anticipated_award": "2029-07-19"}]}}}
    beyond = {"results": {"forecast_signals": {"matched": [
        {"source_id": "OUT", "anticipated_award": "2029-07-20"}]}}}
    assert composer.select_horizon(inside, None, today=date(2026, 7, 19))
    assert not composer.select_horizon(beyond, None, today=date(2026, 7, 19))


def test_real_mark43_forecast_facts_are_not_lost():
    rows = json.load(open(os.path.join(
        _REPO, "tests", "fixtures", "mark43_forecast_rows.json"),
        encoding="utf-8"))
    assert len(rows) == 78
    parsed = [(composer._event_day(r.get("anticipated_solicitation")),
               composer._event_day(r.get("anticipated_award")))
              for r in rows]
    assert all(s or a for s, a in parsed), "every APFS row must parse"
    past_sol_future_award = sum(
        1 for s, a in parsed
        if s and s < TODAY.isoformat() and a and a >= TODAY.isoformat())
    assert past_sol_future_award == 47
    picks = composer.select_horizon(
        {"results": {"forecast_signals": {"matched": rows}}}, None,
        today=TODAY, n=100)
    assert len(picks) == 78, "no in-window APFS row may be lost"


def test_horizon_requires_core_evidence_not_just_boundary_naics(world):
    """Forecast membership is earned by the sanctioned relevance engine;
    an in-boundary NAICS code cannot promote an unrelated row."""
    inputs = composer.load_client_inputs("Testco")
    sweep = {"results": {"forecast_signals": {"matched": [
        {"source_id": "NAICS-ONLY", "agency": "DHS",
         "title": "Enterprise timekeeping system refresh",
         "naics_code": "541512", "anticipated_award": "2026-08-01"},
        {"source_id": "CORE", "agency": "DHS",
         "title": "Computer-aided dispatch refresh",
         "naics_code": "541512", "anticipated_award": "2026-09-01"},
    ]}}}
    picks = composer.select_horizon(
        sweep, None, today=TODAY, n=10, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"])
    assert [pick.ident for pick in picks] == ["CORE"]


def test_cli_does_not_repeat_only_best_fit_award_as_forward_timing(
        world, capsys):
    """A current award may anchor Best Fit or Forward Timing, never both."""
    sweep = _add_distinct_acquisition_notice(_award_only_sweep())
    sweep["results"]["forecast_signals"]["matched"] = [{
        "source_id": "NAICS-ONLY",
        "agency": "DHS",
        "title": "Enterprise timekeeping system refresh",
        "naics_code": "541512",
        "anticipated_award": "2026-08-01",
    }]
    sweep["results"]["contract_awards"]["recompetes"] = []
    _world_client(world, "testco", "Testco", sweep=sweep)

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    content = json.loads((world / "clients" / "testco"
                          / "signal_board_content.json").read_text())
    assert len(content["best_fit"]) == 1
    assert content["horizon"] == []
    trail = (world / "clients" / "testco" / "compose_trail.md") \
        .read_text(encoding="utf-8")
    assert "NAICS-ONLY" not in trail
    assert "## horizon\n- none selected" in trail
    capsys.readouterr()


def test_horizon_job_context_uses_source_fields_and_honest_fallbacks():
    common = {
        "event_date": "2026-09-26", "ident": "H1",
        "scope_ok": True, "scope_basis": "UNSCOPED",
    }
    award = composer.HorizonPick(
        kind="award-window", row={
            "description": ("THIS IS A DEFINITIVE CONTRACT TO ACQUIRE "
                            "RIVERBED NETWORK MONITORING HARDWARE AND "
                            "SOFTWARE MAINTENANCE, AND SUPPORT"),
        }, **common)
    award_basis = composer._horizon_job_context_basis(award)
    assert award_basis == {
        "version": 1,
        "event_kind": "award-window",
        "source_field": "description",
        "source_text": ("THIS IS A DEFINITIVE CONTRACT TO ACQUIRE RIVERBED "
                        "NETWORK MONITORING HARDWARE AND SOFTWARE "
                        "MAINTENANCE, AND SUPPORT"),
    }
    assert composer._horizon_job_context_public(award_basis) == {
        "kind": "published-description",
        "text": ("RIVERBED NETWORK MONITORING HARDWARE AND SOFTWARE "
                 "MAINTENANCE, AND SUPPORT"),
    }

    thin = composer.HorizonPick(
        kind="award-window", row={"description": "RIVERBED"}, **common)
    assert composer._horizon_job_context_public(
        composer._horizon_job_context_basis(thin)) == {
            "kind": "limited-description",
            "text": ("RIVERBED · fuller work detail is not stated in the "
                     "cited record."),
        }

    forecast = composer.HorizonPick(
        kind="forecast", row={"title": "Network modernization support"},
        **common)
    assert composer._horizon_job_context_public(
        composer._horizon_job_context_basis(forecast)) == {
            "kind": "published-title",
            "text": "Network modernization support",
        }

    expiring = composer.HorizonPick(kind="expiring", row={}, **common)
    assert composer._horizon_job_context_public(
        composer._horizon_job_context_basis(expiring)) == {
            "kind": "not-stated",
            "text": "Work detail is not stated in the cited record.",
        }

    usaspending_expiring = composer.HorizonPick(
        kind="usaspending-expiring",
        row={"description": "incident reporting software renewal"},
        **common)
    assert composer._horizon_job_context_public(
        composer._horizon_job_context_basis(usaspending_expiring)) == {
            "kind": "published-description",
            "text": "incident reporting software renewal",
        }


def test_forward_forecast_preserves_stated_quarter_and_value_band(world):
    from agents.reports.board_content import (
        SignalBoardContent, reconcile_figures, reconciliation_error)

    sweep = _sweep()
    sweep["results"]["forecast_signals"] = {
        "matched": [{
            "source": "doe_forecast",
            "source_id": "DOE-FWD-1",
            "agency": "Department of Homeland Security",
            "component": "CISA",
            "title": "Computer-aided dispatch modernization",
            "description": (
                "Computer-aided dispatch modernization and incident "
                "reporting support"),
            "anticipated_solicitation": "Q2 FY2027",
            "estimated_value_range": "$5M - $10M",
            "estimated_value_lower": 5_000_000.0,
            "estimated_value_upper": 10_000_000.0,
            "forecast_status": "planned",
            "url": "https://example.gov/forecast/DOE-FWD-1",
        }],
        "_provenance": {
            "retrieved_at": "2026-07-18T00:00:00+00:00",
        },
    }
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    competitors = composer.select_competitors(
        sweep, today=TODAY, scope=inputs["scope"],
        client_name="Testco", taxonomy=inputs["taxonomy"],
        naics_boundary=["541512"])
    teaming = composer.select_teaming(sweep, best_fit, competitors)
    horizon = composer.select_horizon(
        sweep, None, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")
    pick = next(row for row in horizon if row.ident == "DOE-FWD-1")
    assert pick.timing_source_value == "Q2 FY2027"
    assert pick.timing_precision == "fiscal-quarter"

    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=[])
    content = composer.build_hero(
        content, competitors, teaming, sweep, client_name="Testco",
        best_fit=best_fit, horizon=horizon)
    card = next(row for row in content["horizon"]
                if row["machine_evidence"]["source_identity"] == "DOE-FWD-1")
    assert card["value"] == "Q2 FY2027"
    assert card["timing_basis"] == {
        "source_value": "Q2 FY2027",
        "precision": "fiscal-quarter",
        "sort_date": "2027-01-01",
    }
    assert {
        "label": "Published value band",
        "value": "$5M to $10M",
        "source_field": (
            "forecast estimated_value_range: $5M - $10M"),
    } in card["procurement_facts"]
    forward = next(row for row in content["signal_cards"]
                   if row["label"] == "Forward timing signals")
    assert forward["value"] == str(len(horizon))
    assert "$" not in json.dumps(forward)
    forecast_figures = [row for row in content["figures"]
                        if row.get("source_record_id") == "DOE-FWD-1"]
    assert {row["text"] for row in forecast_figures} == {"$5M", "$10M"}
    assert all(not row.get("component_raws") for row in forecast_figures)
    assert "A separate cited published acquisition forecast" in \
        content["hero_context"]
    assert "$5M to $10M" in content["hero_context"]
    assert ("Validate its acquisition path before assigning pipeline value "
            "or a capture deadline.") in content["hero_context"]
    assert "Q2 FY2027" in content["sequence"]
    assert "August 30, 2026" not in content["sequence"]
    validated = SignalBoardContent.model_validate(content)
    assert reconciliation_error(reconcile_figures(validated, sweep)) is None


def test_later_published_forecast_cannot_be_starved_by_expiry_clocks(world):
    sweep = _sweep()
    sweep["results"]["forecast_signals"]["matched"] = [{
        "source": "agency_forecast",
        "source_id": "FUTURE-BUY-1",
        "agency": "Department of Homeland Security",
        "title": "Computer-aided dispatch modernization",
        "description": "computer-aided dispatch modernization",
        "anticipated_solicitation": "Q4 FY2028",
    }]
    sweep["results"]["contract_awards"]["recompetes"] = []
    days = [
        "2026-08-15", "2026-09-15", "2026-10-15", "2026-11-15",
        "2026-12-15", "2027-01-15", "2027-02-15", "2027-03-15",
        "2027-04-15", "2027-05-15", "2027-06-15", "2027-07-15",
        "2027-08-15",
    ]
    calendar = {
        "attack": [{
            "award_id": f"CLOCK-{index:02d}",
            "recipient": f"INCUMBENT {index:02d}",
            "awarding_agency": "Department of Homeland Security",
            "description": "computer-aided dispatch platform support",
            "pop_end": day,
        } for index, day in enumerate(days, 1)],
        "defend": [],
    }
    inputs = composer.load_client_inputs("Testco")

    selected = composer.select_horizon(
        sweep, calendar, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")

    assert len(selected) == 12
    assert "FUTURE-BUY-1" in {pick.ident for pick in selected}


def test_calendar_expiry_dedupes_same_award_window(world):
    sweep = _sweep()
    sweep["results"]["forecast_signals"]["matched"] = []
    sweep["results"]["contract_awards"]["recompetes"] = []
    calendar = {
        "attack": [{
            "award_id": "70CDCR25F0001",
            "recipient": "ACTIVE VENDOR ONE",
            "awarding_agency": "Department of Homeland Security",
            "description": "dispatch system platform support",
            "pop_end": "2027-01-15",
        }],
        "defend": [],
    }
    inputs = composer.load_client_inputs("Testco")

    selected = composer.select_horizon(
        sweep, calendar, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")
    matching = [
        pick for pick in selected if pick.award_id == "70CDCR25F0001"]

    assert len(matching) == 1
    assert matching[0].kind == "calendar-expiry"


def test_forward_timing_excludes_awards_featured_above_and_refills(world):
    sweep = _sweep()
    sweep["results"]["forecast_signals"]["matched"] = []
    sweep["results"]["contract_awards"]["recompetes"] = []
    calendar = {
        "attack": [{
            "award_id": "FEATURED-01",
            "recipient": "FEATURED INCUMBENT",
            "awarding_agency": "Department of Homeland Security",
            "description": "dispatch system platform support",
            "pop_end": "2026-09-15",
        }, {
            "award_id": "DISTINCT-02",
            "recipient": "DISTINCT INCUMBENT",
            "awarding_agency": "Department of Homeland Security",
            "description": "dispatch system platform support",
            "pop_end": "2026-10-15",
        }],
        "defend": [],
    }
    inputs = composer.load_client_inputs("Testco")

    selected = composer.select_horizon(
        sweep, calendar, today=TODAY, n=1, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco",
        featured_award_identities={"featured-01"})

    assert [pick.award_id for pick in selected] == ["DISTINCT-02"]


def test_forward_award_windows_apply_the_same_materiality_floor(world):
    sweep = _award_only_sweep()
    sweep["results"]["forecast_signals"]["matched"] = []
    sweep["results"]["contract_awards"]["recompetes"] = []
    sweep["results"]["incumbent_buyer_map"]["buyers"].append({
        "buyer": "LOW VALUE OFFICE",
        "agency": "Department of Homeland Security",
        "records": [{
            "kind": "award",
            "recipient": "LOW VALUE INCUMBENT",
            "amount": 11_132.10,
            "amount_basis": "obligated_to_date",
            "end_date": "2026-12-09",
            "award_id": "LOW-WINDOW",
            "generated_internal_id": "CONT_AWD_LOW_WINDOW",
            "matched_terms": ["incident reporting"],
            "description": "incident reporting software subscription",
        }],
    })
    inputs = composer.load_client_inputs("Testco")

    selected = composer.select_horizon(
        sweep, None, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")

    assert "CONT_AWD_LOW_WINDOW" not in {pick.ident for pick in selected}
    assert any(pick.kind == "award-window" for pick in selected)

    sparse = _zero_best_fit_sweep()
    sparse["results"]["incumbent_buyer_map"]["buyers"] = [
        sweep["results"]["incumbent_buyer_map"]["buyers"][-1]]
    sparse["results"]["forecast_signals"]["matched"] = []
    sparse["results"]["contract_awards"]["recompetes"] = []
    sparse_selected = composer.select_horizon(
        sparse, None, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")
    assert "CONT_AWD_LOW_WINDOW" in {
        pick.ident for pick in sparse_selected}


def test_completion_dollars_never_become_future_opportunity_value():
    card = {
        "label": "DHS · COMPLETION",
        "title": "INCUMBENT PLATFORM",
        "value": "AUGUST 30, 2026",
        "timing_basis": {"sort_date": "2026-08-30"},
        "procurement_facts": [{
            "label": "USAspending award amount",
            "value": "$9.00M",
            "source_field": "obligated_to_date",
        }],
        "job_context": {
            "kind": "published-description",
            "text": "computer-aided dispatch platform support",
        },
        "machine_evidence": {
            "source_kind": "contract-award",
            "source_identity": "PIID-9",
        },
    }

    event = composer._summary_forward_event(card)

    assert event is not None
    assert event["stated_value"] == ""
    assert "$" not in " ".join(composer._executive_summary_sentences(
        "Testco", [], [], [card]))


def test_forward_calendar_separates_attack_and_defend_from_current_source(
        world):
    sweep = _sweep()
    sweep["results"]["forecast_signals"]["matched"] = []
    sweep["results"]["contract_awards"]["recompetes"] = []
    calendar = {
        "client": "Testco",
        "attack": [{
            "award_id": "ATTACK-1",
            "recipient": "RIVAL INC.",
            "awarding_agency": "Department of Homeland Security",
            "description": "computer-aided dispatch platform support",
            "naics": "541512",
            "pop_end": "2027-04-01",
        }],
        "defend": [{
            "award_id": "DEFEND-1",
            "recipient": "TESTCO, INC.",
            "awarding_agency": "Department of Homeland Security",
            "description": "incident reporting software subscription",
            "naics": "541512",
            "pop_end": "2027-05-01",
        }],
    }
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    competitors = composer.select_competitors(
        sweep, today=TODAY, scope=inputs["scope"],
        client_name="Testco", taxonomy=inputs["taxonomy"],
        naics_boundary=["541512"])
    teaming = composer.select_teaming(sweep, best_fit, competitors)
    horizon = composer.select_horizon(
        sweep, calendar, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")
    postures = {pick.ident: pick.posture for pick in horizon}
    assert postures["ATTACK-1"] == "ATTACK"
    assert postures["DEFEND-1"] == "DEFEND"

    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=prose)
    trail = composer.build_trail(
        "Testco", best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, prose_trail=prose, today=TODAY)
    trail_md = composer.render_trail_md(trail, "Testco")
    composer.validate_machine_horizon_contract(content, trail_md=trail_md)
    composer.validate_machine_client_relevance_contract(
        content, trail_md=trail_md, taxonomy=inputs["taxonomy"],
        profile=load_profile("Testco"), sweep=sweep, calendar=calendar)


def test_forward_consumes_usaspending_expiry_with_context_and_identity(world):
    sweep = _sweep()
    sweep["results"]["forecast_signals"]["matched"] = []
    sweep["results"]["contract_awards"]["recompetes"] = []
    sweep["results"]["expiring_awards"] = {"rows": [{
        "award_id": "EXP-1",
        "generated_internal_id": "CONT_AWD_EXP1_7001_NONE_NONE",
        "recipient": "RIVAL INC.",
        "amount": 2_500_000.0,
        "amount_basis": "USAspending Award Amount",
        "awarding_agency": "Department of Homeland Security",
        "awarding_sub_agency": "Cybersecurity and Infrastructure Security Agency",
        "end_date": "2027-03-15",
        "naics": "541512",
        "psc": "DA10",
        "description": "computer-aided dispatch platform renewal",
    }]}
    inputs = composer.load_client_inputs("Testco")
    horizon = composer.select_horizon(
        sweep, None, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")
    pick = next(row for row in horizon if row.award_id == "EXP-1")
    assert pick.kind == "usaspending-expiring"
    content = composer.compose_content(
        "Testco", sweep, best_fit=[], competitors=[], teaming=[],
        horizon=[pick], today=TODAY, offline=True, prose_trail=[])
    content = composer.build_hero(
        content, [], [], sweep, client_name="Testco", horizon=[pick])
    card = content["horizon"][0]
    assert card["award_generated_id"] == "CONT_AWD_EXP1_7001_NONE_NONE"
    assert card["decision"] == "ATTACK"
    assert card["job_context"] == {
        "kind": "published-description",
        "text": "computer-aided dispatch platform renewal",
    }
    assert card["source_citation"] == "USAspending award · EXP-1"


def test_contract_completion_keeps_canonical_usaspending_link_identity(world):
    sweep = _sweep()
    row = sweep["results"]["contract_awards"]["recompetes"][0]
    row.update({
        "generated_internal_id": "CONT_AWD_PIID9_7001_IDV9_7001",
        "record_source": "usaspending.gov",
        "url": (
            "https://www.usaspending.gov/award/"
            "CONT_AWD_PIID9_7001_IDV9_7001"
        ),
    })
    inputs = composer.load_client_inputs("Testco")
    horizon = composer.select_horizon(
        sweep, None, today=TODAY, n=12, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"], client_name="Testco")
    pick = next(item for item in horizon if item.ident == "PIID-9")

    content = composer.compose_content(
        "Testco", sweep, best_fit=[], competitors=[], teaming=[],
        horizon=[pick], today=TODAY, offline=True, prose_trail=[])

    assert content["horizon"][0]["award_generated_id"] == (
        "CONT_AWD_PIID9_7001_IDV9_7001"
    )


def test_competitor_footprint_counts_only_explicit_boundary_awards(world):
    sweep = _sweep()
    treasury = sweep["results"]["incumbent_buyer_map"]["buyers"][1]
    treasury["records"].extend([{
        "kind": "award",
        "recipient": "ACTIVE VENDOR TWO",
        "amount": 3_000_000.0,
        "end_date": "2025-01-01",
        "award_id": "HIST-BOUNDARY",
        "generated_internal_id": "CONT_AWD_HIST_BOUNDARY",
        "naics_code": "541512",
        "description": "unclassified historical work",
    }, {
        "kind": "award",
        "recipient": "ACTIVE VENDOR TWO",
        "amount": 4_000_000.0,
        "end_date": "2025-02-01",
        "award_id": "HIST-NO-CODE",
        "generated_internal_id": "CONT_AWD_HIST_NO_CODE",
        "description": "computer-aided dispatch historical work",
    }])
    inputs = composer.load_client_inputs("Testco")
    competitors = composer.select_competitors(
        sweep, today=TODAY, scope=inputs["scope"],
        client_name="Testco", taxonomy=inputs["taxonomy"],
        naics_boundary=["541512"])
    selected = next(row for row in competitors
                    if row.recipient == "ACTIVE VENDOR TWO")

    assert [row["award_id"] for row in selected.boundary_records] == [
        "HIST-BOUNDARY"]
    facts = composer._competitor_boundary_facts(selected)
    assert facts[0] == {
        "label": "Other boundary awards",
        "value": "1",
        "source_field": "buyer-map awards with explicit boundary NAICS",
    }


def test_procurement_facts_distinguish_award_structure_from_parent_idv():
    record = {
        "award_id": "TO-1",
        "naics": {"code": "541512", "description": "Computer systems"},
        "psc": {"code": "DA01", "description": "Business applications"},
        "award_type": "DELIVERY ORDER",
        "award_structure": "MULTIPLE AWARD",
        "parent_award_id": "IDV-1",
        "parent_vehicle_type": (
            "INDEFINITE DELIVERY / INDEFINITE QUANTITY"),
        "vehicle_coholders": ["PRIME A", {"name": "PRIME B"}],
    }
    facts, missing = composer._procurement_facts(
        record, family="award", identity_label="Award",
        identity_value="TO-1")
    by_label = {row["label"]: row["value"] for row in facts}

    assert by_label["NAICS"] == "541512"
    assert by_label["PSC"] == "DA01"
    assert by_label["Award structure"] == "MULTIPLE AWARD"
    assert by_label["Parent IDV"] == "IDV-1"
    assert by_label["Parent IDV type"] == (
        "INDEFINITE DELIVERY / INDEFINITE QUANTITY")
    assert by_label["Co-holders"] == "PRIME A · PRIME B"
    assert "Co-holders" not in missing

    without_holders = dict(record)
    without_holders.pop("vehicle_coholders")
    _facts, missing = composer._procurement_facts(
        without_holders, family="award", identity_label="Award",
        identity_value="TO-1")
    assert "Co-holders" in missing


def test_compose_cli_defaults_to_twelve_forward_events(world, capsys):
    sweep = _sweep()
    sweep["results"]["forecast_signals"]["matched"] = []
    sweep["results"]["contract_awards"]["recompetes"] = [
        {
            "awardee": f"RIVAL {index}",
            "piid": f"EXP-{index}",
            "completion": f"2027-{index:02d}-15",
            "agency": "Department of Homeland Security",
            "description": "computer-aided dispatch platform support",
            "naics": "541512",
        }
        for index in range(1, 10)
    ]
    _world_client(world, "testco", "Testco", sweep=sweep)

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    content = json.loads((world / "clients" / "testco"
                          / "signal_board_content.json").read_text())
    expiring = [row for row in content["horizon"]
                if row["machine_evidence"]["source_kind"]
                == "contract-award"]
    assert len(expiring) == 9
    assert len(content["horizon"]) > 4
    assert len(content["horizon"]) <= composer.DEFAULT_FORWARD_EVENTS
    capsys.readouterr()


def test_compose_cli_horizon_uses_complete_qualified_buyer_map(
        world, monkeypatch, capsys):
    original = composer.select_horizon
    calls = []

    def capture(*args, **kwargs):
        calls.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(composer, "select_horizon", capture)

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    assert len(calls) == 2  # reservation preflight, then final disjoint band
    assert all("award_corridors" not in call for call in calls)
    assert all(isinstance(call["featured_award_identities"], set)
               for call in calls)
    assert calls[0]["featured_award_identities"] <= \
        calls[1]["featured_award_identities"]
    capsys.readouterr()


def test_forward_card_cannot_render_without_bound_timing_and_source(world):
    sweep = _sweep()
    best_fit, competitors, teaming, horizon = _select_all(sweep)
    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=prose)
    trail = composer.build_trail(
        "Testco", best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, prose_trail=prose, today=TODAY)
    trail_md = composer.render_trail_md(trail, "Testco")
    composer.validate_machine_horizon_contract(content, trail_md=trail_md)

    fake_recompete = copy.deepcopy(content)
    basis = fake_recompete["horizon"][0]["machine_evidence"][
        "job_context_basis"]
    basis["event_kind"] = "recompete"
    fake_recompete["horizon"][0]["machine_evidence"][
        "source_kind"] = "recompete-calendar"
    with pytest.raises(ValueError, match="job-context event kind"):
        composer.validate_machine_horizon_contract(fake_recompete)

    no_source = copy.deepcopy(content)
    no_source["horizon"][0]["source_citation"] = ""
    no_source["horizon"][0]["machine_evidence"]["source_citation"] = ""
    with pytest.raises(ValueError, match="cited timing or decision"):
        composer.validate_machine_horizon_contract(no_source)

    no_timing = copy.deepcopy(content)
    no_timing["horizon"][0]["timing_basis"] = {}
    no_timing["horizon"][0]["machine_evidence"]["timing_basis"] = {}
    with pytest.raises(ValueError, match="cited timing or decision"):
        composer.validate_machine_horizon_contract(no_timing)

    changed_fact = copy.deepcopy(content)
    changed_fact["horizon"][0]["procurement_facts"] = []
    with pytest.raises(ValueError, match="decision evidence"):
        composer.validate_machine_horizon_contract(
            changed_fact, trail_md=trail_md)


def test_program_horizon_is_visible_source_bound_and_directly_linked(world):
    from agents.reports import signal_board

    sweep = _sweep()
    official_url = (
        "https://www.reginfo.gov/public/do/eAgendaViewRule?RIN=TEST-1"
    )
    sweep["results"]["reginfo_unified_agenda"] = {
        "records": [{
            "source": "reginfo_unified_agenda",
            "tier": "program",
            "record_id": "reginfo:TEST-1:current",
            "canonical_url": official_url,
            "retrieved_at": "2026-07-20T00:00:00+00:00",
            "data_as_of": "2026-07-20",
            "agency": "Department of Homeland Security",
            "title": "Interoperability rule",
            "abstract": (
                "The agency plans computer-aided dispatch interoperability "
                "requirements for federal responders."
            ),
            "timetable": [{"action": "Notice", "date": "10/00/2026"}],
            "matched_terms": ["computer-aided dispatch"],
        }],
    }

    bf, comp, team, horizon = _select_all(sweep)

    assert len(horizon) == 4
    assert sum(pick.kind == "program" for pick in horizon) == 1
    program_pick = next(pick for pick in horizon if pick.kind == "program")
    assert program_pick.event_date == "10/2026"
    assert program_pick.ident == (
        "reginfo_unified_agenda::reginfo:TEST-1:current")

    prose_trail = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=horizon, today=TODAY, offline=True,
        prose_trail=prose_trail)
    card = next(
        row for row in content["horizon"]
        if row.get("program_record_id") == "reginfo:TEST-1:current"
    )
    assert card["url"] == official_url
    assert card["machine_evidence"]["source_kind"] == "program"
    assert card["machine_evidence"]["source_identity"] == (
        "reginfo_unified_agenda::reginfo:TEST-1:current")
    assert card["machine_evidence"]["source_adapter"] == \
        "reginfo_unified_agenda"
    assert card["machine_evidence"]["canonical_url"] == official_url
    assert card["value"] == "OCT 2026"
    assert card["machine_evidence"]["timing_value"] == "OCT 2026"
    assert card["job_context"] == {
        "kind": "published-description",
        "text": (
            "The agency plans computer-aided dispatch interoperability "
            "requirements for federal responders"
        ),
    }
    assert card["client_relevance"]["text"].startswith("PROGRAM MOVE")
    assert card["machine_evidence"]["client_relevance_basis"][
        "supports"][0]["field"] == "abstract"

    materialized = signal_board._materialize_federal_links(content, {})
    html = signal_board.render_signal_board(materialized)
    assert f'href="{official_url}"' in html
    assert "OCT 2026" in html
    assert "PROGRAM MOVE" in html

    trail = composer.build_trail(
        "Testco", best_fit=bf, competitors=comp, teaming=team,
        horizon=horizon, prose_trail=prose_trail, today=TODAY)
    trail_md = composer.render_trail_md(trail, "Testco")
    composer.validate_machine_horizon_contract(
        content, trail_md=trail_md)
    inputs = composer.load_client_inputs("Testco")
    composer.validate_machine_client_relevance_contract(
        content,
        trail_md=trail_md,
        taxonomy=inputs["taxonomy"],
        profile=load_profile("Testco"),
        sweep=sweep,
    )

    # Even if someone changes both content and trail and rebuilds the pair
    # binding, the current-source gate rejects a URL not in the stored row.
    tampered = copy.deepcopy(content)
    tampered_trail = copy.deepcopy(trail)
    replacement = (
        "https://www.reginfo.gov/public/do/eAgendaViewRule?RIN=OTHER"
    )
    tampered_card = next(
        row for row in tampered["horizon"]
        if row.get("program_record_id") == "reginfo:TEST-1:current"
    )
    tampered_card["url"] = replacement
    tampered_card["machine_evidence"]["canonical_url"] = replacement
    tampered_row = next(
        row for row in tampered_trail["selections"]["horizon"]
        if row.get("program_record_id") == "reginfo:TEST-1:current"
    )
    tampered_row["canonical_url"] = replacement
    tampered_trail_md = composer.render_trail_md(
        tampered_trail, "Testco")
    composer.validate_machine_horizon_contract(
        tampered, trail_md=tampered_trail_md)
    with pytest.raises(ValueError, match="canonical URL.*current stored"):
        composer.validate_machine_client_relevance_contract(
            tampered,
            trail_md=tampered_trail_md,
            taxonomy=inputs["taxonomy"],
            profile=load_profile("Testco"),
            sweep=sweep,
        )


def test_program_horizon_rejects_rows_outside_the_shared_official_seam(world):
    base = {
        "source": "reginfo_unified_agenda",
        "tier": "program",
        "record_id": "reginfo:TEST-2:current",
        "canonical_url": (
            "https://www.reginfo.gov/public/do/eAgendaViewRule?RIN=TEST-2"
        ),
        "retrieved_at": "2026-07-20T00:00:00+00:00",
        "data_as_of": "2026-07-20",
        "agency": "Department of Homeland Security",
        "title": "Computer-aided dispatch interoperability rule",
        "timetable": [{"action": "Notice", "date": "10/15/2026"}],
    }
    invalid = [
        {**base, "canonical_url": "https://example.com/program"},
        {**base, "source": "darpa_opportunities"},
        {**base, "tier": "notice"},
        {**base, "retrieved_at": "2026-07-20T00:00:00"},
    ]
    inputs = composer.load_client_inputs("Testco")

    for row in invalid:
        sweep = _sweep()
        sweep["results"]["reginfo_unified_agenda"] = {"records": [row]}
        picks = composer.select_horizon(
            sweep, None, today=TODAY, n=1, scope=inputs["scope"],
            taxonomy=inputs["taxonomy"])
        assert all(pick.kind != "program" for pick in picks)


def test_program_horizon_prioritizes_actions_then_newest_publication(world):
    common = {
        "tier": "program",
        "retrieved_at": "2026-07-20T00:00:00+00:00",
        "data_as_of": "2026-07-20",
        "agency": "Department of Homeland Security",
        "title": "Federal responder technology program",
    }
    dsip = {
        **common,
        "source": "dsip_topics",
        "record_id": "dsip:concrete-action",
        "canonical_url": "https://www.dodsbirsttr.mil/topics-app/",
        "objective": "Computer-aided dispatch interoperability for responders.",
        "close_date": "2026-11-30",
    }
    old_darpa = {
        **common,
        "source": "darpa_opportunities",
        "record_id": "darpa:a-old",
        "canonical_url": "https://www.darpa.mil/research/programs/a-old",
        "summary": "Computer-aided dispatch interoperability research.",
        "published_at": "2026-02-01T00:00:00+00:00",
    }
    new_darpa = {
        **old_darpa,
        "record_id": "darpa:z-new",
        "canonical_url": "https://www.darpa.mil/research/programs/z-new",
        "published_at": "2026-07-18T00:00:00+00:00",
    }
    inputs = composer.load_client_inputs("Testco")

    sweep = _sweep()
    sweep["results"]["dsip_topics"] = {"records": [dsip]}
    sweep["results"]["darpa_opportunities"] = {
        "records": [old_darpa, new_darpa]}
    picks = composer.select_horizon(
        sweep, None, today=TODAY, n=1, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"])
    assert picks[0].ident == "dsip_topics::dsip:concrete-action"
    assert composer._horizon_job_context_basis(picks[0])[
        "source_field"] == "objective"

    del sweep["results"]["dsip_topics"]
    picks = composer.select_horizon(
        sweep, None, today=TODAY, n=1, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"])
    assert picks[0].ident == "darpa_opportunities::darpa:z-new"
    assert composer._horizon_job_context_basis(picks[0])[
        "source_field"] == "summary"


def test_sbir_and_grants_program_rows_have_exact_actionable_timing(world):
    common = {
        "tier": "program",
        "retrieved_at": "2026-07-20T00:00:00+00:00",
        "data_as_of": "2026-07-20",
        "agency": "Department of Homeland Security",
    }
    sbir = {
        **common,
        "source": "sbir_gov",
        "record_id": "sbir_gov:SBIR-26-1:A-1",
        "canonical_url": "https://www.sbir.gov/topics/9001",
        "title": "Computer-aided dispatch interoperability topic",
        "objective": (
            "Computer-aided dispatch interoperability for responders."
        ),
        "number": "SBIR-26-1",
        "open_date": "2026-08-01",
        "close_date": "2026-10-01",
    }
    grant = {
        **common,
        "source": "grants_gov",
        "record_id": "grants_gov:358800",
        "canonical_url": (
            "https://www.grants.gov/search-results-detail/358800"
        ),
        "title": "Computer-aided dispatch interoperability grant",
        "number": "DHS-26-CYB-001",
        "status": "forecasted",
        "open_date": "2026-09-01",
        "close_date": "2026-11-15",
    }
    inputs = composer.load_client_inputs("Testco")
    sweep = {
        "client": "Testco",
        "generated_at": "2026-07-20T00:00:00+00:00",
        "results": {},
    }
    sweep["results"]["sbir_gov"] = {"records": [sbir]}
    sweep["results"]["grants_gov"] = {"records": [grant]}

    picks = composer.select_horizon(
        sweep, None, today=TODAY, n=3, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"])
    program_picks = [pick for pick in picks if pick.kind == "program"]

    assert [pick.ident for pick in program_picks] == [
        "sbir_gov::sbir_gov:SBIR-26-1:A-1",
        "grants_gov::grants_gov:358800",
    ]
    assert program_picks[0].event_date == "2026-10-01"
    assert program_picks[0].row["_horizon_timing_label"] == (
        "SBIR/STTR TOPIC · CLOSES"
    )
    assert program_picks[1].event_date == "2026-09-01"
    assert program_picks[1].row["_horizon_timing_label"] == (
        "FEDERAL ASSISTANCE · FORECASTED OPEN"
    )


def test_dsip_mirror_wins_one_slot_and_sbir_remains_fallback(world):
    common = {
        "tier": "program",
        "retrieved_at": "2026-07-20T00:00:00+00:00",
        "data_as_of": "2026-07-20",
        "title": "Computer-aided dispatch interoperability topic",
        "close_date": "2026-10-01",
        "objective": (
            "Computer-aided dispatch interoperability for responders."
        ),
    }
    dsip = {
        **common,
        "source": "dsip_topics",
        "record_id": "dsip:mirror",
        "canonical_url": "https://www.dodsbirsttr.mil/topics-app/mirror",
        "agency": "Department of Defense",
    }
    sbir = {
        **common,
        "source": "sbir_gov",
        "record_id": "sbir_gov:mirror",
        "canonical_url": "https://www.sbir.gov/topics/mirror",
        "agency": "DOD",
    }
    inputs = composer.load_client_inputs("Testco")
    sweep = _sweep()
    sweep["results"]["dsip_topics"] = {"records": [dsip]}
    sweep["results"]["sbir_gov"] = {"records": [sbir]}

    picks = composer.select_horizon(
        sweep, None, today=TODAY, n=2, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"])
    program_picks = [pick for pick in picks if pick.kind == "program"]
    assert [pick.ident for pick in program_picks] == [
        "dsip_topics::dsip:mirror",
    ]

    del sweep["results"]["dsip_topics"]
    picks = composer.select_horizon(
        sweep, None, today=TODAY, n=1, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"])
    assert picks[0].ident == "sbir_gov::sbir_gov:mirror"


def test_horizon_job_context_removes_non_work_fragments_before_publication():
    pick = composer.HorizonPick(
        kind="award-window", event_date="2026-09-26", ident="H1",
        row={"description": (
            "THIS IS A CONTRACT TO ACQUIRE $5M NETWORK SUPPORT "
            "https://example.gov buyer@example.mil")},
        scope_ok=True, scope_basis="UNSCOPED")

    public = composer._horizon_job_context_public(
        composer._horizon_job_context_basis(pick))

    assert public == {
        "kind": "published-description",
        "text": "NETWORK SUPPORT",
    }


def test_horizon_job_context_rejects_event_source_policy_tamper():
    basis = {
        "version": 1,
        "event_kind": "award-window",
        "source_field": "description",
        "source_text": "network monitoring support",
    }
    public = composer._horizon_job_context_public(basis)

    with pytest.raises(ValueError, match="source field 'title'.*not allowed"):
        composer._horizon_job_context_public({
            **basis,
            "source_field": "title",
        })
    with pytest.raises(ValueError, match="invalid.*event kind"):
        composer._horizon_job_context_public({
            **basis,
            "event_kind": "not-a-real-kind",
        })
    with pytest.raises(ValueError, match="field and text are inconsistent"):
        composer._horizon_job_context_public({
            **basis,
            "source_field": "none",
        })
    with pytest.raises(ValueError, match="source kind does not match"):
        composer.validate_machine_horizon_card({
            "title": "Riverbed via RedSky",
            "job_context": public,
            "machine_evidence": {
                "source_kind": "agency-forecast",
                "job_context_basis": basis,
            },
        })


# ── machine-screened safety + evidence identity (finding 3) ─────────────────

def test_corridors_are_forward_looking(world):
    comp = composer.select_competitors(_sweep(), today=TODAY)
    assert "EXPIRED VENDOR" not in [c.recipient for c in comp]


def test_best_fit_cards_carry_full_identity_and_quotes(world):
    sweep = _sweep()
    bf, comp, team, hz = _select_all(sweep)
    trail_lines: list = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hz, today=TODAY, offline=True, prose_trail=trail_lines)
    for card in content["best_fit"]:
        ref = card["evidence"][0]
        assert ref["label"] in ("70RSAT26R0001", "70RSAT26R0002")
        assert len(ref["sam_notice_guid"]) >= 6      # full id, untruncated
    trail = composer.build_trail(
        "Testco", best_fit=bf, competitors=comp, teaming=team, horizon=hz,
        prose_trail=trail_lines, today=TODAY)
    for row in trail["selections"]["best_fit"]:
        assert row["quoted_spans"] and all(
            s["quote"] for s in row["quoted_spans"])


def test_compose_carries_stable_decision_maker_input_and_poc_provenance(
        world, monkeypatch):
    from agents.reports import board_content
    from agents.reports.board_content import DecisionMaker

    person = DecisionMaker(
        name="Pat Example",
        title="Chief Information Officer",
        organization="Department of Homeland Security",
        bio_url="https://www.dhs.gov/official/pat-example",
        retrieved_at="2026-07-20T00:00:00Z",
        route_kind="sam.gov",
        route_identity="aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1",
        route_label="DHS dispatch modernization",
        relevance_rationale="The official role maps to the displayed route.",
    )
    monkeypatch.setattr(
        board_content, "load_decision_makers", lambda _client: [person])
    sweep = _sweep()
    bf, comp, team, hz = _select_all(sweep)

    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hz, today=TODAY, offline=True, prose_trail=[])

    assert content["decision_makers"][0]["name"] == "Pat Example"
    assert content["decision_makers"][0]["source_record_id"] == \
        person.bio_url
    assert content["decision_makers"][0]["route_kind"] == "sam.gov"
    assert content["decision_makers"][0]["route_identity"] == \
        "aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1"
    assert content["pocs"]
    assert content["pocs"][0]["agency"] == \
        "Department of Homeland Security"
    assert content["pocs"][0]["source_system"] == "SAM.gov"
    assert content["pocs"][0]["source_record_id"] == \
        content["pocs"][0]["opp_notice_guid"]


def test_compose_refuses_decision_maker_bound_to_an_absent_route(
        world, monkeypatch):
    from agents.reports import board_content
    from agents.reports.board_content import DecisionMaker

    person = DecisionMaker(
        name="Pat Example",
        title="Chief Information Officer",
        organization="Department of Homeland Security",
        bio_url="https://www.dhs.gov/official/pat-example",
        retrieved_at="2026-07-20T00:00:00Z",
        route_kind="sam.gov",
        route_identity="ffffffffffffffffffffffffffffffff",
        route_label="Absent DHS route",
        relevance_rationale="This intentionally stale binding must fail.",
    )
    monkeypatch.setattr(
        board_content, "load_decision_makers", lambda _client: [person])
    sweep = _sweep()
    bf, comp, team, hz = _select_all(sweep)

    with pytest.raises(
            ValueError,
            match=r"primary displayed card.*Pat Example: sam\.gov::ffffffff"):
        composer.compose_content(
            "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
            horizon=hz, today=TODAY, offline=True, prose_trail=[])


def test_sam_poc_survives_compose_materialize_and_render_as_clickable_notice(
        world):
    from agents.reports import signal_board

    sweep = _sweep()
    bf, comp, team, hz = _select_all(sweep)
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hz, today=TODAY, offline=True, prose_trail=[])

    materialized = signal_board._materialize_federal_links(content, {})
    html = signal_board.render_signal_board(materialized)
    guid = CORE_NOTICE["source_id"]
    assert f'href="https://sam.gov/opp/{guid}/view"' in html
    assert "Pat Doe" in html
    assert "CONTACTS AS LISTED IN SAM.GOV" in html


def test_teaming_prefers_subaward_evidence(world):
    sweep = _sweep()
    bf, comp, team, hz = _select_all(sweep)
    assert team[0].partner == "BIG PRIME"
    assert team[0].basis == "subaward-evidence"
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hz, today=TODAY, offline=True, prose_trail=[])
    subaward_cards = [
        row for row in content["teaming"]
        if row["machine_evidence"]["source_kind"] == "usaspending-subaward"
    ]
    assert subaward_cards
    assert all("SHARED CORE: COMPUTER-AIDED DISPATCH" in row["angle"]
               and "SUBAWARD SUB-1" in row["angle"]
               and "PRIME AWARD PA-1" in row["proof"]
               and "MATCHED PRIME-SUBAWARD RECORD" in row["proof"]
               for row in subaward_cards)
    assert all(row["target"] == row["program"]
               and row["target"].endswith("70RSAT26R0001")
               and row["target"] not in {"PO", "SERVICES"}
               for row in subaward_cards)
    assert all(row["award_generated_id"]
               == "CONT_AWD_PA1_7001_IDV1_7001"
               and row["machine_evidence"]["source_identity"] ==
               "CONT_AWD_PA1_7001_IDV1_7001::subaward::SUB-1"
               for row in subaward_cards)
    assert all("VERIFIED" not in row["angle"]
               and "VALIDATION REQUIRED" not in row["proof"]
               for row in subaward_cards)
    assert all(
        row["client_relevance"]["text"].startswith(
            "PRIME-CHANNEL SIGNAL · ")
        for row in subaward_cards
    )
    assert "prioritize the matched primes for teaming outreach" in \
        content["sequence"]
    subaward_summary = composer.derive_executive_summary(
        "Testco", [], subaward_cards)
    assert "one prime-channel signal through Big Prime" in subaward_summary

    repeated = copy.deepcopy(content)
    repeated["best_fit"].append({
        "title": "DHS × Big Prime, L.L.C.",
        "machine_evidence": {
            "source_identity": "CONT_AWD_OTHER_BEST_FIT",
            "source_kind": "usaspending-award",
        },
    })
    with pytest.raises(ValueError, match="best-fit or competitor routes"):
        composer.validate_machine_route_band_contract(repeated)


def test_entry_thesis_angle_names_cited_product_buyer_period_and_award(world):
    """Riverbed-like reseller rows explain the candidate route with the
    active product corridor itself; no generic partner-fit placeholder can
    survive composition."""
    gid = "CONT_AWD_2032H525F00130_2050_NNG15SC71B_8000"
    pick = composer.TeamingPick(
        partner="FCN, INC.", basis="entry-thesis", edge_count=0,
        total=1_660_329.76,
        play={
            "kind": "award", "recipient": "FCN, INC.",
            "award_id": "2032H525F00130",
            "generated_internal_id": gid,
            "matched_products": ["SolarWinds"],
            "matched_terms": ["network performance monitoring"],
            "description": ("SOLARWINDS SOFTWARE AND SERVICES TO SUPPORT "
                            "THE NETWORK PERFORMANCE MONITORING ENVIRONMENT."),
            "end_date": "2027-05-12",
        },
        agency="Department of the Treasury",
        buyer="INTERNAL REVENUE SERVICE",
        scope_ok=True,
        scope_basis="preset:civilian@v1 · in-scope:Treasury",
    )
    prose = []
    _riverbed_profile(world)

    content = composer.compose_content(
        "Riverbed", _sweep(), best_fit=[], competitors=[], teaming=[pick],
        horizon=[], today=TODAY, offline=True, prose_trail=prose)

    card = content["teaming"][0]
    assert card["target"] == "SolarWinds account"
    assert card["angle"] == (
        "SOLARWINDS AWARD AT IRS · THROUGH 12 MAY 2027")
    assert card["proof"] == (
        "AWARD 2032H525F00130 · CITED AWARD-HOLDER ROUTE")
    assert card["award_generated_id"] == gid
    assert card["machine_evidence"]["source_identity"] == gid
    assert card["machine_evidence"]["quote"].startswith(
        "SOLARWINDS SOFTWARE")
    assert content["sequence"] == (
        "close the named route-evidence gap before assigning partner or "
        "displacement posture; "
        "treat cited award period ends as research triggers, not recompetes; "
        "confirm option status, vehicle, contracting office, and follow-on "
        "path."
    )
    assert "PARTNER FIT" not in json.dumps(card)
    wedge = next(row for row in prose if row["kind"] == "wedge")
    assert wedge == {
        "kind": "wedge", "ident": "FCN, INC.",
        "source": "evidence-template:entry-thesis",
        "line": "SOLARWINDS AWARD AT IRS · THROUGH 12 MAY 2027",
    }
    with pytest.raises(ValueError, match="entry theses are acquisition"):
        composer.validate_machine_route_band_contract(content)


def test_riverbed_like_candidate_angles_pin_two_distinct_cited_corridors(
        world):
    _riverbed_profile(world)
    rows = [
        composer.TeamingPick(
            partner="FCN, INC.", basis="entry-thesis", edge_count=0,
            total=1_660_329.76, agency="Department of the Treasury",
            buyer="INTERNAL REVENUE SERVICE", scope_ok=True,
            scope_basis="preset:civilian@v1 · in-scope:Treasury",
            play={
                "award_id": "2032H525F00130",
                "generated_internal_id":
                    "CONT_AWD_2032H525F00130_2050_NNG15SC71B_8000",
                "matched_products": ["SolarWinds"],
                "description": "SOLARWINDS SOFTWARE AND SERVICES",
                "end_date": "2027-05-12",
            }),
        composer.TeamingPick(
            partner="SWISH DATA CORPORATION", basis="entry-thesis",
            edge_count=0, total=1_128_300.0,
            agency="Department of Homeland Security",
            buyer="US CITIZENSHIP AND IMMIGRATION SERVICES",
            scope_ok=True,
            scope_basis="preset:civilian@v1 · in-scope:DHS",
            play={
                "award_id": "70SBUR26F00000149",
                "generated_internal_id":
                    "CONT_AWD_70SBUR26F00000149_7003_NNG15SC91B_8000",
                "matched_products": ["Aternity"],
                "description": "ATERNITY MONITORS THE USCIS ENTERPRISE NETWORK",
                "end_date": "2027-06-30",
            }),
    ]

    content = composer.compose_content(
        "Riverbed", _sweep(), best_fit=[], competitors=[], teaming=rows,
        horizon=[], today=TODAY, offline=True, prose_trail=[])

    assert [card["angle"] for card in content["teaming"]] == [
        "SOLARWINDS AWARD AT IRS · THROUGH 12 MAY 2027",
        "ATERNITY AWARD AT USCIS · THROUGH 30 JUN 2027",
    ]
    assert [card["proof"] for card in content["teaming"]] == [
        "AWARD 2032H525F00130 · CITED AWARD-HOLDER ROUTE",
        "AWARD 70SBUR26F00000149 · CITED AWARD-HOLDER ROUTE",
    ]


def _summary_award_card(*, buyer, recipient, amount, window, gid,
                        scope="preset:civilian@v1 · in-scope:agency"):
    return {
        "title": f"{buyer} × {recipient}",
        "account": "CIV · ACTIVE AWARD CORRIDOR · CLIENT FOOTPRINT",
        "cells": [
            {"label": "Obligated to date", "value": amount},
            {"label": "Window", "value": window},
        ],
        "machine_evidence": {
            "source_identity": gid,
            "source_kind": "usaspending-award",
            "scope_basis": scope,
        },
    }


def _summary_entry_card(*, partner, route, buyer, pop_end, gid):
    return {
        "partner": partner,
        "machine_evidence": {
            "source_identity": gid,
            "source_kind": "corridor-entry-thesis",
            "route_basis": {
                "kind": "entry-thesis",
                "route": route,
                "buyer": buyer,
                "pop_end": pop_end,
                "award_id": f"AWARD-{gid}",
                "award_generated_id": gid,
            },
        },
    }


def test_riverbed_executive_summary_is_exact_and_evidence_derived():
    best_fit = [
        _summary_award_card(
            buyer="BUREAU OF LAND MANAGEMENT",
            recipient="SWISH DATA CORPORATION", amount="$1.4M",
            window="30 SEP 2026", gid="BLM"),
        _summary_award_card(
            buyer="INTERNAL REVENUE SERVICE", recipient="REDSKY LLC",
            amount="$1.92M", window="31 JUL 2026", gid="IRS"),
        _summary_award_card(
            buyer="FEDERAL BUREAU OF INVESTIGATION",
            recipient="SWISH DATA CORPORATION", amount="$1.74M",
            window="24 SEP 2026", gid="FBI"),
    ]
    teaming = [
        _summary_entry_card(
            partner="SWISH DATA", route="Aternity",
            buyer="US CITIZENSHIP AND IMMIGRATION SERVICES",
            pop_end="2027-06-30", gid="ATERNITY"),
        _summary_entry_card(
            partner="FCN", route="SolarWinds",
            buyer="INTERNAL REVENUE SERVICE",
            pop_end="2027-05-12", gid="SOLARWINDS"),
    ]
    expected = (
        "The nearest dated signal is Riverbed’s $1.92M obligated to date "
        "IRS footprint "
        "via RedSky, whose cited award period ends July 31, 2026. FBI "
        "($1.74M obligated to date via Swish Data) and BLM ($1.4M obligated "
        "to date via Swish Data) have current "
        "award periods ending in late September, creating a concentrated "
        "period-end action sequence across three evidenced civilian accounts. "
        "Beyond "
        "the installed base, cited SolarWinds at IRS and Aternity at USCIS "
        "awards identify two award-holder decision routes through FCN and "
        "Swish Data; make the partner-or-displace call for Riverbed at those "
        "accounts."
    )

    assert composer.derive_executive_summary(
        "Riverbed", best_fit, teaming) == expected
    assert composer.derive_executive_summary(
        "Riverbed", list(reversed(best_fit)),
        list(reversed(teaming))) == expected


def test_mark43_executive_summary_is_exact_and_monitor_honest():
    best_fit = [{
        "title": "FY26-30 A42 USNCB CPI OpenFox renewal",
        "agency_name": "JUSTICE, DEPARTMENT OF / US MARSHALS SERVICE",
        "account": "DOJ · MONITOR · SCORE 3",
        "cells": [{"label": "Window", "value": "20 JUL 2026"}],
        "machine_evidence": {
            "source_identity": "SAM-OPENFOX",
            "source_kind": "sam.gov",
            "triage_verdict": "monitor",
        },
    }]
    teaming = [
        _summary_entry_card(
            partner="COMPUTER PROJECTS OF ILLINOIS", route="OpenFox",
            buyer="FEDERAL BUREAU OF INVESTIGATION",
            pop_end="2027-03-31", gid="OPENFOX"),
        _summary_entry_card(
            partner="AXON ENTERPRISE", route="Axon Records",
            buyer="US COAST GUARD", pop_end="2026-09-29", gid="AXON"),
        _summary_entry_card(
            partner="CENTRALSQUARE TECHNOLOGIES", route="CentralSquare",
            buyer="DEPARTMENT OF THE NAVY", pop_end="2026-12-09",
            gid="CENTRALSQUARE"),
    ]
    expected = (
        "The nearest dated notice-level signal is the U.S. Marshals "
        "Service’s FY26-30 A42 USNCB CPI OpenFox renewal, with responses due "
        "July 20, 2026; the assessment classifies it as a monitor signal, "
        "outside Mark43's current pursuit set. Beyond that notice, cited "
        "Axon Records at USCG, CentralSquare at Navy, and OpenFox at FBI "
        "awards identify three award-holder decision routes through Axon "
        "Enterprise, CentralSquare Technologies, and Computer Projects of "
        "Illinois; make the partner-or-displace call for Mark43 at those "
        "accounts."
    )

    assert composer.derive_executive_summary(
        "mark43", best_fit, teaming) == expected
    assert "MONITOR" in best_fit[0]["account"]
    assert "outside Mark43's current pursuit set" in expected


def test_monitor_summary_is_reason_neutral_and_article_safe():
    best_fit = [{
        "title": "Early market research notice",
        "agency_name": "Department of Commerce",
        "account": "DOC · MONITOR · SCORE 1",
        "cells": [{"label": "Window", "value": "21 JUL 2026"}],
        "machine_evidence": {
            "source_identity": "SAM-FORMING",
            "source_kind": "sam.gov",
            "triage_verdict": "monitor",
            "triage_reason": "forming requirement",
        },
    }]

    summary = composer.derive_executive_summary("Acme", best_fit, [])

    assert "a monitor signal, outside Acme's current pursuit set" in summary
    assert "renewal intelligence" not in summary
    assert "not a Acme" not in summary


def test_executive_summary_calls_the_earliest_record_nearest_not_strongest():
    nearest = _summary_award_card(
        buyer="SMALL BUYER", recipient="ACME, INC.", amount="$10K",
        window="21 JUL 2026", gid="SMALL")
    larger_later = _summary_award_card(
        buyer="MAJOR BUYER", recipient="ACME, INC.", amount="$500M",
        window="22 JUL 2026", gid="MAJOR")

    summary = composer.derive_executive_summary(
        "Acme", [larger_later, nearest], [])

    assert summary.startswith(
        "The nearest dated signal is Acme’s $10K obligated to date "
        "SMALL BUYER footprint")
    assert "strongest" not in summary.casefold()


def test_monitor_disposition_is_visible_and_bound_to_the_compose_trail(world):
    sweep = _sweep()
    source_id = str(sweep["results"]["sam.gov"][0]["source_id"])
    sweep["results"]["triage"][source_id] = {
        "verdict": "monitor",
        "reason": "named-incumbent renewal; track, do not qualify",
    }
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    _add_distinct_acquisition_notice(sweep)
    acquisition_pathways = composer.select_acquisition_pathways(
        sweep, best_fit, [], scope=inputs["scope"],
        profile=load_profile("Testco"), taxonomy=inputs["taxonomy"],
        client_name="Testco", today=TODAY)
    assert acquisition_pathways
    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=[], teaming=[],
        acquisition_pathways=acquisition_pathways,
        horizon=[], today=TODAY, offline=True, prose_trail=prose)
    trail = composer.build_trail(
        "Testco", best_fit=best_fit, competitors=[], teaming=[],
        acquisition_pathways=acquisition_pathways, horizon=[],
        prose_trail=prose, today=TODAY)

    card = next(row for row in content["best_fit"]
                if row["machine_evidence"]["source_identity"] == source_id)
    assert " · MONITOR · " in card["account"]
    assert "outside Testco's current pursuit set" in content["hero_context"]
    composer.validate_machine_evidence(content, trail)

    card["machine_evidence"]["triage_reason"] = "invented disposition"
    with pytest.raises(ValueError, match="does not match its trail-bound"):
        composer.validate_machine_evidence(content, trail)


def test_executive_summary_empty_and_partial_inputs_fail_closed_gracefully():
    assert composer.derive_executive_summary("Testco", [], []) == ""
    malformed = [{
        "title": "Unsupported exciting opportunity",
        "machine_evidence": {"source_kind": "sam.gov"},
    }]
    assert composer.derive_executive_summary("Testco", malformed, []) == ""

    one = _summary_award_card(
        buyer="INTERNAL REVENUE SERVICE", recipient="TESTCO, INC.",
        amount="$2M", window="31 JUL 2026", gid="ONLY")
    summary = composer.derive_executive_summary("Testco", [one], [])
    assert summary == (
        "The nearest dated signal is Testco’s $2M obligated to date "
        "IRS footprint, "
        "whose cited award period ends July 31, 2026. It is the only "
        "priority route in the current screened evidence set."
    )


def test_executive_summary_contract_rederives_and_rejects_tampering():
    card = _summary_award_card(
        buyer="INTERNAL REVENUE SERVICE", recipient="TESTCO, INC.",
        amount="$2M", window="31 JUL 2026", gid="ONLY")
    content = {
        "client_name": "Testco", "best_fit": [card], "teaming": [],
    }
    content["hero_context"] = composer.derive_executive_summary(
        "Testco", content["best_fit"], content["teaming"])
    composer.validate_machine_executive_summary_contract(content)

    content["hero_context"] = "A much more exciting unsupported claim."
    with pytest.raises(ValueError, match="executive summary does not match"):
        composer.validate_machine_executive_summary_contract(content)


def test_generic_po_services_and_labor_subawards_never_become_public_routes(
        world):
    sweep = _sweep()
    sweep["results"]["subawards"]["edges"]["541512"] = [
        {
            "prime": prime, "prime_award_id": f"P-{index}",
            "prime_award_generated_id": f"CONT_AWD_P{index}_7001_I{index}_7001",
            "subaward_id": f"S-{index}",
            "source": f"https://api.usaspending.gov/api/v2/subawards/S-{index}",
            "awarding_agency": "Department of Homeland Security",
            "description": description,
        }
        for index, (prime, description) in enumerate((
            ("FAIRWINDS", "PO"),
            ("GDIT", "SERVICES"),
            ("CACI", "IT LABOR SUPPORT"),
        ), start=1)
    ]
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    findings = []

    selected = composer.select_teaming(
        sweep, best_fit, [], scope=inputs["scope"],
        profile=load_profile("Testco"), diagnostics=findings)

    assert selected == []
    assert findings == [
        "suppressed 6 subaward candidate edge(s): subaward description "
        "has no client-core match; examples "
        "aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1 × CACI, "
        "aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1 × FAIRWINDS, "
        "aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1 × GDIT",
    ]


def test_subaward_prime_piid_requires_one_unambiguous_canonical_gid(world):
    sweep = _sweep()
    edge = sweep["results"]["subawards"]["edges"]["541512"][0]
    edge.pop("prime_award_generated_id")
    buyers = sweep["results"]["incumbent_buyer_map"]["buyers"]
    buyers[0]["records"].append({
        "kind": "award", "recipient": "BIG PRIME", "award_id": "PA-1",
        "generated_internal_id": "CONT_AWD_PA1_7001_IDV1_7001",
        "matched_products": ["PRODUCT TWO"],
        "description": "computer-aided dispatch integration",
    })
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    profile = load_profile("Testco")

    selected = composer.select_teaming(
        sweep, best_fit, [], scope=inputs["scope"], profile=profile)
    assert selected[0].edges[0]["_prime_award_generated_id"] == (
        "CONT_AWD_PA1_7001_IDV1_7001")

    buyers[1]["records"].append({
        "kind": "award", "recipient": "BIG PRIME", "award_id": "PA-1",
        "generated_internal_id": "CONT_AWD_PA1_2050_OTHER_2050",
        "matched_products": ["PRODUCT TWO"],
        "description": "computer-aided dispatch integration",
    })
    findings = []
    selected = composer.select_teaming(
        sweep, best_fit, [], scope=inputs["scope"], profile=profile,
        diagnostics=findings)
    assert selected == []
    assert any("no unambiguous canonical award GID" in item
               for item in findings)


def test_subaward_same_component_uses_notice_raw_subtier_fallback(world):
    sweep = _sweep()
    notice = sweep["results"]["sam.gov"][0]
    notice["raw_payload"]["subtier_name"] = "USCIS"
    edge = sweep["results"]["subawards"]["edges"]["541512"][0]
    edge["awarding_sub_agency"] = "US CITIZENSHIP AND IMMIGRATION SERVICES"
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    selected_play = next(p for p in best_fit if p.source_id == notice["source_id"])

    selected = composer.select_teaming(
        sweep, [selected_play], [], scope=inputs["scope"],
        profile=load_profile("Testco"))

    assert composer._notice_component(selected_play.record) == "USCIS"
    assert len(selected) == 1

    edge["awarding_sub_agency"] = "US CUSTOMS AND BORDER PROTECTION"
    findings = []
    assert composer.select_teaming(
        sweep, [selected_play], [], scope=inputs["scope"],
        profile=load_profile("Testco"), diagnostics=findings) == []
    assert any("buyer does not match selected play" in item
               for item in findings)


def test_teaming_selection_omits_incomplete_routes_instead_of_fabricating(
        world):
    sweep = _sweep()
    sweep["results"]["subawards"]["edges"]["541512"] = [{
        "prime": "BIG PRIME",
        # no prime_award_id: this cannot support a public citation
        "awarding_agency": "DHS", "sub": "S",
        "description": "dispatch integration subcontract",
    }]
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    malformed_lane = composer.CompetitorPick(
        recipient="UNSUPPORTED PARTNER", buyer="IRS",
        agency="Department of the Treasury", amount=1.0,
        record={
            "award_id": "A-1", "generated_internal_id": "CONT_AWD_A_1",
            "matched_products": ["SolarWinds"], "description": "",
        },
        award_id="A-1", gid="CONT_AWD_A_1",
        scope_ok=True, scope_basis="in-scope:Treasury",
    )

    selected = composer.select_teaming(
        sweep, best_fit, [malformed_lane], scope=inputs["scope"])

    assert selected == []


def test_teaming_never_backfills_competitors_or_suffix_variants(world):
    sweep = _sweep()
    inputs, profile, best_fit, competitors = _route_selections(sweep)
    sweep["results"]["subawards"] = {"primes": [], "edges": {}}
    assert composer.select_teaming(
        sweep, best_fit, competitors, scope=inputs["scope"],
        profile=profile) == []

    overlap = _sweep()
    prime_record = next(
        row
        for buyer in overlap["results"]["incumbent_buyer_map"]["buyers"]
        for row in buyer["records"]
        if row.get("award_id") == "PA-1"
    )
    prime_record["recipient"] = "Big Prime, L.L.C."
    prime_record["matched_products"] = ["Dispatch Prime"]
    overlap["results"]["subawards"]["primes"] = [{
        "name": "BIG PRIME INC.", "total": 9_000_000.0,
    }]
    overlap["results"]["subawards"]["edges"]["541512"][0][
        "prime"] = "BIG PRIME INC."
    inputs, profile, best_fit, competitors = _route_selections(overlap)
    competitor_keys = {
        composer._company_key(pick.recipient) for pick in competitors}
    assert composer._company_key("BIG PRIME INC.") in competitor_keys

    selected = composer.select_teaming(
        overlap, best_fit, competitors, scope=inputs["scope"],
        profile=profile)

    assert all(pick.basis == "subaward-evidence" for pick in selected)
    assert all(composer._company_key(pick.partner) not in competitor_keys
               for pick in selected)

    upper_band = _sweep()
    inputs, profile, best_fit, competitors = _route_selections(upper_band)
    best_fit[0].recipient = "Big Prime, L.L.C."
    findings = []

    selected = composer.select_teaming(
        upper_band, best_fit, competitors, scope=inputs["scope"],
        profile=profile, diagnostics=findings)

    assert all(composer._company_key(pick.partner)
               != composer._company_key("Big Prime, L.L.C.")
               for pick in selected)
    assert any("upper route band" in finding for finding in findings)


def test_acquisition_pathways_are_distinct_deterministic_and_company_free(
        world):
    sweep = _add_distinct_acquisition_notice(_sweep())
    amendment = copy.deepcopy(sweep["results"]["sam.gov"][-1])
    amendment["source_id"] = "fff6fff6fff6fff6fff6fff6fff6fff6"
    amendment["raw_payload"]["notice_id"] = amendment["source_id"]
    sweep["results"]["sam.gov"].append(amendment)
    inputs, profile, best_fit, competitors = _route_selections(sweep)

    paths = composer.select_acquisition_pathways(
        sweep, best_fit, competitors, n=1, scope=inputs["scope"],
        profile=profile, taxonomy=inputs["taxonomy"],
        client_name="Testco", today=TODAY)
    assert [pick.source_identity for pick in paths] == [
        "eee5eee5eee5eee5eee5eee5eee5eee5"]

    permuted = copy.deepcopy(sweep)
    permuted["results"]["sam.gov"].reverse()
    permuted["results"]["incumbent_buyer_map"]["buyers"].reverse()
    for buyer in permuted["results"]["incumbent_buyer_map"]["buyers"]:
        buyer["records"].reverse()
    p_inputs, p_profile, p_best_fit, p_competitors = _route_selections(
        permuted)
    repeated = composer.select_acquisition_pathways(
        permuted, list(reversed(p_best_fit)),
        list(reversed(p_competitors)), n=1, scope=p_inputs["scope"],
        profile=p_profile, taxonomy=p_inputs["taxonomy"],
        client_name="Testco", today=TODAY)
    assert [pick.source_identity for pick in repeated] == [
        pick.source_identity for pick in paths]

    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=[], acquisition_pathways=paths, horizon=[], today=TODAY,
        offline=True, prose_trail=prose)
    trail = composer.build_trail(
        "Testco", best_fit=best_fit, competitors=competitors, teaming=[],
        acquisition_pathways=paths, horizon=[], prose_trail=prose,
        today=TODAY, scope=inputs["scope"])
    composer.validate_machine_evidence(
        content, trail, taxonomy=inputs["taxonomy"], profile=profile,
        sweep=sweep)

    card = content["acquisition_pathways"][0]
    assert "Commercial Solutions Opening" in card["requirement"]
    assert "ACQUISITION METHOD Commercial Solutions Opening" in \
        card["pathway"]
    assert card["client_relevance"]["text"].startswith("PUBLISHED DEMAND")
    assert "has funded" not in card["client_relevance"]["text"]
    assert card["machine_evidence"]["quote"] == card["requirement"]
    assert card["machine_evidence"]["pathway_basis"][
        "requirement_excerpt"] == card["requirement"]
    public = json.dumps({
        key: card.get(key) for key in (
            "label", "title", "requirement", "pathway", "access",
            "action", "procurement_facts")
    }).casefold()
    assert all(pick.recipient.casefold() not in public
               for pick in competitors)


def test_acquisition_path_requires_buying_method_not_only_classification():
    basis = {
        "version": 1,
        "agency": "Department of Homeland Security",
        "buyer": "US CUSTOMS AND BORDER PROTECTION",
        "capability": "incident reporting",
        "source_kind": "usaspending-award",
        "source_identity": "CONT_AWD_ROUTE_ONLY",
        "award_id": "A-ROUTE-ONLY",
        "notice_guid": "",
        "route_key": "award:CONT_AWD_ROUTE_ONLY",
        "source_company_key": "route vendor",
        "requirement_excerpt": "incident reporting software support",
        "client_footprint": False,
        "procurement_facts": [
            {"label": "Award", "value": "A-ROUTE-ONLY",
             "source_field": "award identity"},
            {"label": "NAICS", "value": "541512",
             "source_field": "NAICS"},
            {"label": "PSC", "value": "DA01", "source_field": "PSC"},
        ],
        "missing_procurement_facts": ["Award structure", "Set-aside"],
    }

    with pytest.raises(ValueError, match="no substantive buying facts"):
        composer._acquisition_pathway_public_fields(basis)


def test_acquisition_excerpt_is_relevance_centered_without_broken_words():
    excerpt = composer._acquisition_requirement_excerpt({
        "title": "CSDR Support and Curation.",
        "description": (
            "The effort covers verification and validation, compliance "
            "support, submission data curation, metadata tagging, and data "
            "quality preparation through automation and analytics."),
    }, "data curation")

    assert excerpt.startswith("CSDR Support and Curation: ")
    assert ".:" not in excerpt
    assert "…cation" not in excerpt
    assert "submission data curation" in excerpt


def test_acquisition_path_uses_client_footprint_posture(world):
    sweep = _sweep()
    sweep["results"]["incumbent_buyer_map"]["buyers"].append({
        "buyer": "US CUSTOMS AND BORDER PROTECTION",
        "agency": "Department of Homeland Security",
        "records": [{
            "kind": "award", "recipient": "CHANNEL PARTNER LLC",
            "amount": 250_000.0,
            "amount_basis": "obligated_to_date",
            "end_date": "2027-04-30",
            "award_id": "70B04C26F00000001",
            "generated_internal_id": "CONT_AWD_TESTCO_FOOTPRINT_1",
            "matched_products": ["Testco"],
            "matched_terms": ["incident reporting"],
            "description": "Testco incident reporting software renewal",
            "award_structure": "DELIVERY ORDER",
            "naics": "541512", "psc": "DA01",
        }],
    })
    inputs, profile, best_fit, competitors = _route_selections(sweep)

    paths = composer.select_acquisition_pathways(
        sweep, best_fit, competitors, n=4, scope=inputs["scope"],
        profile=profile, taxonomy=inputs["taxonomy"],
        client_name="Testco", today=TODAY)
    footprint = next(
        pick for pick in paths
        if pick.gid == "CONT_AWD_TESTCO_FOOTPRINT_1")
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=[], acquisition_pathways=[footprint], horizon=[], today=TODAY,
        offline=True, prose_trail=[])
    card = content["acquisition_pathways"][0]

    assert footprint.client_footprint is True
    assert card["label"].endswith("CLIENT FOOTPRINT")
    assert card["title"].endswith("renewal route")
    assert card["action"].startswith("DEFEND / EXPAND NEXT")
    assert "renewal or follow-on owner" in card["action"]
    assert card["client_relevance"]["text"].startswith(
        "DEFEND / EXPAND PATH")
    assert "Testco TESTCO footprint" not in \
        card["client_relevance"]["text"]
    assert card["machine_evidence"]["pathway_basis"][
        "client_footprint"] is True


def test_acquisition_path_excludes_displayed_company_suffix_variants(world):
    sweep = _award_only_sweep()
    sweep["results"]["incumbent_buyer_map"]["buyers"].extend([
        {
            "buyer": "US CUSTOMS AND BORDER PROTECTION",
            "agency": "Department of Homeland Security",
            "records": [{
                "kind": "award", "recipient": "ROUTE VENDOR INC.",
                "amount": 900_000.0,
                "amount_basis": "obligated_to_date",
                "end_date": "2027-04-30", "award_id": "ROUTE-1",
                "generated_internal_id": "CONT_AWD_ROUTE_VENDOR_1",
                "matched_terms": ["incident reporting"],
                "description": "incident reporting software support",
                "award_structure": "DELIVERY ORDER",
            }],
        }, {
            "buyer": "FEDERAL EMERGENCY MANAGEMENT AGENCY",
            "agency": "Department of Homeland Security",
            "records": [{
                "kind": "award", "recipient": "ALTERNATE VENDOR LLC",
                "amount": 800_000.0,
                "amount_basis": "obligated_to_date",
                "end_date": "2027-05-30", "award_id": "ROUTE-2",
                "generated_internal_id": "CONT_AWD_ALT_VENDOR_2",
                "matched_terms": ["incident reporting"],
                "description": "incident reporting software support",
                "award_structure": "PURCHASE ORDER",
            }],
        },
    ])
    inputs = composer.load_client_inputs("Testco")
    profile = load_profile("Testco")
    best_fit = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"], client_name="Testco",
        today=TODAY, n=1)
    competitor = composer.CompetitorPick(
        recipient="Route Vendor, L.L.C.", buyer="OTHER BUYER",
        agency="Department of Homeland Security", amount=1.0, record={},
        award_id="COMP-1", gid="CONT_AWD_OTHER_COMPETITOR",
        scope_ok=True, scope_basis="preset:all_federal@v1")

    paths = composer.select_acquisition_pathways(
        sweep, best_fit, [competitor], n=1, scope=inputs["scope"],
        profile=profile, taxonomy=inputs["taxonomy"],
        client_name="Testco", today=TODAY)

    assert [pick.gid for pick in paths] == ["CONT_AWD_ALT_VENDOR_2"]
    assert composer._company_key(paths[0].record["recipient"]) != \
        composer._company_key(competitor.recipient)


def test_section_03_contract_rejects_both_neither_reuse_and_tamper(world):
    sweep = _add_distinct_acquisition_notice(_sweep())
    inputs, profile, best_fit, competitors = _route_selections(sweep)
    paths = composer.select_acquisition_pathways(
        sweep, best_fit, competitors, n=1, scope=inputs["scope"],
        profile=profile, taxonomy=inputs["taxonomy"],
        client_name="Testco", today=TODAY)
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=[], acquisition_pathways=paths, horizon=[], today=TODAY,
        offline=True, prose_trail=[])
    composer.validate_machine_route_band_contract(content)

    neither = copy.deepcopy(content)
    neither["acquisition_pathways"] = []
    with pytest.raises(ValueError, match="no source-bound fallback"):
        composer.validate_machine_route_band_contract(neither)

    both = copy.deepcopy(content)
    both["teaming"] = [{"partner": "Injected"}]
    with pytest.raises(ValueError, match="exactly one"):
        composer.validate_machine_route_band_contract(both)

    reused = copy.deepcopy(content)
    reused["acquisition_pathways"][0]["machine_evidence"][
        "pathway_basis"]["route_key"] = \
        composer._acquisition_route_key_for_best_fit(best_fit[0])
    with pytest.raises(ValueError, match="distinct from best-fit"):
        composer.validate_machine_route_band_contract(reused)

    tampered = copy.deepcopy(content)
    tampered["acquisition_pathways"][0]["requirement"] = \
        "Generic opportunity"
    with pytest.raises(ValueError, match="public copy"):
        composer.validate_machine_route_band_contract(tampered)


def test_acquisition_paths_cannot_starve_distinct_forward_timing():
    paths = [
        composer.AcquisitionPathPick(
            agency="DHS", buyer="BUYER A", capability="dispatch",
            record={}, source_kind="usaspending-award",
            source_identity="GID-A", award_id="A", gid="GID-A"),
        composer.AcquisitionPathPick(
            agency="DHS", buyer="BUYER B", capability="records",
            record={}, source_kind="usaspending-award",
            source_identity="GID-B", award_id="B", gid="GID-B"),
    ]
    overlapping = [
        composer.HorizonPick(
            kind="award-window", event_date="2026-08-01", ident="GID-A",
            row={}, award_id="A", gid="GID-A"),
        composer.HorizonPick(
            kind="award-window", event_date="2026-09-01", ident="GID-B",
            row={}, award_id="B", gid="GID-B"),
    ]

    reserved = composer.reserve_distinct_forward_timing(paths, overlapping)

    assert [pick.gid for pick in reserved] == ["GID-B"]
    distinct = [composer.HorizonPick(
        kind="forecast", event_date="2026-07-25", ident="FORECAST-1",
        row={})]
    assert composer.reserve_distinct_forward_timing(paths, distinct) == paths


def test_distinct_award_pathway_registers_figures_and_authority_coverage(
        world):
    from agents.reports.board_content import (
        SignalBoardContent,
        reconcile_figures,
    )

    sweep = _award_only_sweep()
    sweep["results"]["incumbent_buyer_map"]["buyers"].append({
        "buyer": "US CUSTOMS AND BORDER PROTECTION",
        "agency": "Department of Homeland Security",
        "records": [{
            "kind": "award", "recipient": "ROUTE VENDOR INC.",
            "amount": 75_000.0, "amount_basis": "obligated_to_date",
            "potential_ceiling": 750_000.0,
            "award_structure": "Delivery Order",
            "parent_award_id": "IDV-ROUTE-1",
            "parent_vehicle_type": "Governmentwide Acquisition Contract",
            "naics_code": "541512", "end_date": "2028-01-15",
            "award_id": "70B04C27F00000001",
            "generated_internal_id": "CONT_AWD_DISTINCT_ROUTE_1",
            "matched_terms": ["incident reporting"],
            "description": "incident reporting software support",
        }],
    })
    inputs = composer.load_client_inputs("Testco")
    profile = load_profile("Testco")
    best_fit = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"], client_name="Testco",
        today=TODAY, n=1)
    paths = composer.select_acquisition_pathways(
        sweep, best_fit, [], n=1, scope=inputs["scope"], profile=profile,
        taxonomy=inputs["taxonomy"], client_name="Testco", today=TODAY)
    assert [pick.gid for pick in paths] == ["CONT_AWD_DISTINCT_ROUTE_1"]

    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=[], teaming=[],
        acquisition_pathways=paths, horizon=[], today=TODAY, offline=True,
        prose_trail=[])
    content = composer.build_hero(
        content, [], [], sweep, client_name="Testco", best_fit=best_fit,
        acquisition_pathways=paths, horizon=[])

    assert any(row["dept"] == "DHS" for row in content["coverage"])
    route_figures = [
        row for row in content["figures"]
        if row.get("generated_internal_id") == "CONT_AWD_DISTINCT_ROUTE_1"]
    assert {row["raw"] for row in route_figures} == {75_000.0, 750_000.0}
    reconciliation = reconcile_figures(
        SignalBoardContent.model_validate(content), sweep)
    assert reconciliation.clean, (
        reconciliation.unbacked, reconciliation.unregistered)


def test_compose_fails_named_without_a_distinct_section_03_route(
        world, capsys):
    sweep = _sweep()
    sweep["results"]["subawards"] = {"primes": [], "edges": {}}
    sweep["results"]["incumbent_buyer_map"]["buyers"] = []
    _world_client(world, "testco", "Testco", sweep=sweep)

    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    err = capsys.readouterr().err
    failure = json.loads(err.strip().splitlines()[-1])
    assert failure["stage"] == "validate"
    assert "no source-bound fallback" in failure["reason"]
    assert "generic placeholder refused" in err
    assert not (world / "clients" / "testco"
                / "signal_board_content.json").exists()


def test_active_award_corridor_fills_best_fit_without_claiming_a_bid(
        world, capsys):
    """A current, core-evidenced, identified award is the only sanctioned
    fallback when SAM has no live play. Its card remains explicitly an award
    corridor and the client's own footprint never becomes a competitor."""
    sweep = _add_distinct_acquisition_notice(_award_only_sweep())
    _world_client(world, "testco", "Testco", sweep=sweep)

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    content = json.loads((world / "clients" / "testco"
                          / "signal_board_content.json").read_text())
    assert len(content["best_fit"]) == 1
    card = content["best_fit"][0]
    assert "ACTIVE AWARD CORRIDOR" in card["account"]
    assert "CLIENT FOOTPRINT" in card["account"]
    assert "SOLICITATION" not in json.dumps(card).upper()
    assert card["evidence"] == [{
        "award_generated_id": "CONT_AWD_9531BM23P0059_9508_NONE_NONE",
        "label": "9531BM23P0059",
    }]
    assert next(cell for cell in card["cells"]
                if cell["label"] == "Window")["small"] \
        == "PERIOD OF PERFORMANCE END"
    assert content["competitors"] == []
    assert any(row["award_generated_id"] ==
               "CONT_AWD_9531BM23P0059_9508_NONE_NONE"
               for row in content["scale"]["rows"])
    assert card["machine_evidence"]["source_kind"] \
        == "usaspending-award"
    trail = (world / "clients" / "testco" / "compose_trail.md") \
        .read_text(encoding="utf-8")
    assert "award 9531BM23P0059" in trail
    assert "date basis 2026-09-26" in trail
    capsys.readouterr()


def test_reseller_footprints_aggregate_and_use_public_labels(world, capsys):
    """Exact client-product awards remain client footprint when a reseller
    holds the award. Every selected footprint contributes to the public
    footprint total, while compact labels preserve recognizable authority."""
    sweep = _add_distinct_acquisition_notice(_award_only_sweep())
    sweep["results"]["incumbent_buyer_map"]["buyers"].append({
        "buyer": "FEDERAL BUREAU OF INVESTIGATION",
        "agency": "Department of Justice",
        "records": [{
            "kind": "award",
                "recipient": "CHANNEL PARTNER LLC",
                "amount": 100_000.0,
                "amount_basis": "obligated_to_date",
            "end_date": "2027-03-31",
            "award_id": "15F06726F0000001",
            "generated_internal_id": "CONT_AWD_RESELLER_TESTCO",
            "matched_products": ["Testco"],
            "matched_terms": ["incident reporting"],
            "description": "Testco incident reporting subscription",
        }],
    })
    _world_client(world, "testco", "Testco", sweep=sweep)

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    content = json.loads((world / "clients" / "testco"
                          / "signal_board_content.json").read_text())
    assert len(content["best_fit"]) == 2
    assert all("CLIENT FOOTPRINT" in card["account"]
               for card in content["best_fit"])
    assert content["competitors"] == []
    footprint = next(card for card in content["signal_cards"]
                     if card["label"] == "Active footprint")
    assert footprint == {
        "label": "Active footprint",
        "value": "$294.76K",
        "sub": "Obligated across 2 active awards",
        "detail": "NTSB · nearest PoP 26 SEP 2026",
    }
    assert [row["label"] for row in content["scale"]["rows"]] == [
        "NTSB × Testco",
        "FBI × Testco via Channel Partner",
    ]
    best_fit_ids = {
        card["machine_evidence"]["source_identity"]
        for card in content["best_fit"]
    }
    horizon_ids = {
        card["machine_evidence"]["source_identity"]
        for card in content["horizon"]
    }
    assert best_fit_ids.isdisjoint(horizon_ids)
    public_copy = json.dumps(content)
    for internal_phrase in (
            " spans", "CORE EVIDENCE", "ENTRY THESIS",
            "NO SUBAWARD RECORD", "lane record"):
        assert internal_phrase not in public_copy
    capsys.readouterr()


def test_forecast_and_expiring_horizon_marks_use_raw_structured_entities(
        world):
    sweep = _sweep()
    forecast_row = sweep["results"]["forecast_signals"]["matched"][0]
    forecast_row["agency"] = "National Aeronautics and Space Administration"
    forecast_row["component"] = "NASA"
    best_fit, competitors, teaming, horizon = _select_all(sweep)
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=[])

    forecast = next(
        card for card in content["horizon"]
        if card["machine_evidence"]["source_kind"] == "agency-forecast")
    assert forecast["organization_marks"] == [{
        "kind": "agency",
        "label": "National Aeronautics and Space Administration",
        "display": "NASA",
    }]
    assert {"dept": "NASA", "components": "NASA"} in content["coverage"]
    expiring = next(
        card for card in content["horizon"]
        if card["machine_evidence"]["source_kind"] == "contract-award")
    assert expiring["organization_marks"] == [{
        "kind": "agency", "label": "DHS", "display": "DHS",
    }, {
        "kind": "company", "label": "OLD CO", "display": "Old Co",
    }]


def test_client_footprints_are_independent_strict_and_gid_deduped(world):
    from tools.relevance.scope import EngagementScope

    common_self = {
                 "kind": "award", "recipient": "TESTCO, INC.",
                 "amount": 100.0,
                 "amount_basis": "obligated_to_date",
                 "end_date": "2027-01-01",
        "award_id": "SELF", "generated_internal_id": "CONT_AWD_SELF",
        "description": "annual software subscription",
    }
    buyers = [
        {"buyer": "US IMMIGRATION AND CUSTOMS ENFORCEMENT",
         "agency": "Department of Homeland Security",
         "records": [dict(common_self), {
                 "kind": "award", "recipient": "CHANNEL PARTNER LLC",
                 "amount": 200.0,
                 "amount_basis": "obligated_to_date",
                 "end_date": "2027-02-01",
             "award_id": "RESELLER",
             "generated_internal_id": "CONT_AWD_RESELLER",
             "naics": {"code": "443120", "description":
                       "COMPUTER AND SOFTWARE STORES"},
             "matched_products": ["Testco"],
             "description": "Testco incident reporting subscription",
         }, {
             "kind": "award", "recipient": "TAG ONLY LLC",
             "amount": 9_000.0, "end_date": "2027-02-01",
             "award_id": "TAG-ONLY",
             "generated_internal_id": "CONT_AWD_TAG_ONLY",
             "matched_products": ["Testco"],
             "description": "annual software support",
         }, {
             "kind": "award", "recipient": "TESTCO, INC.",
             "amount": 8_000.0, "end_date": "2026-07-18",
             "award_id": "EXPIRED",
             "generated_internal_id": "CONT_AWD_EXPIRED",
             "description": "expired subscription",
         }, {
             "kind": "award", "recipient": "TESTCO, INC.",
             "amount": 7_000.0, "end_date": "2027-03-01",
             "award_id": "MISSING-GID",
             "description": "unstructured identity",
         }, {
             "kind": "award", "recipient": "TESTCO, INC.",
             "amount": 6_000.0, "end_date": "2027-03-01",
             "award_id": "OFF-SCOPE",
             "generated_internal_id": "CONT_AWD_OFF_SCOPE",
             "agency": "Department of Defense",
             "description": "off-scope subscription",
         }]},
        {"buyer": "US CUSTOMS AND BORDER PROTECTION",
         "agency": "Department of Homeland Security",
         "records": [dict(common_self)]},
    ]
    sweep = {"results": {"incumbent_buyer_map": {"buyers": buyers}}}
    inputs = composer.load_client_inputs("Testco")
    scope = EngagementScope(departments=["DHS"])

    def select(payload):
        return composer.select_client_footprints(
            payload, inputs["taxonomy"], scope,
            client_name="Testco", today=TODAY)

    picks = select(sweep)
    assert [(pick.gid, pick.buyer) for pick in picks] == [
        ("CONT_AWD_SELF", "US CUSTOMS AND BORDER PROTECTION"),
        ("CONT_AWD_RESELLER",
         "US IMMIGRATION AND CUSTOMS ENFORCEMENT"),
    ]
    assert sum(pick.amount for pick in picks) == 300.0
    reversed_sweep = {"results": {"incumbent_buyer_map": {
        "buyers": list(reversed(buyers)),
    }}}
    assert [(pick.gid, pick.buyer) for pick in select(reversed_sweep)] == [
        (pick.gid, pick.buyer) for pick in picks
    ]


@pytest.mark.parametrize("include_reseller", [False, True])
def test_footprint_authority_survives_notice_best_fit_and_dedupes_show_work(
        world, include_reseller):
    sweep = _sweep()
    footprint_buyers = [
        {"buyer": "NATIONAL TRANSPORTATION SAFETY BOARD",
         "agency": "National Transportation Safety Board",
         "records": [{
             "kind": "award", "recipient": "TESTCO, INC.",
             "amount": 100.0,
             "amount_basis": "obligated_to_date",
             "end_date": "2027-01-01",
             "award_id": "SELF-FOOTPRINT",
             "generated_internal_id": "CONT_AWD_SELF_FOOTPRINT",
             "description": "annual software subscription",
         }]},
    ]
    if include_reseller:
        footprint_buyers.append({
         "buyer": "FEDERAL BUREAU OF INVESTIGATION",
         "agency": "Department of Justice",
         "records": [{
             "kind": "award", "recipient": "CHANNEL PARTNER LLC",
             "amount": 200.0,
             "amount_basis": "obligated_to_date",
             "end_date": "2027-02-01",
             "award_id": "RESELLER-FOOTPRINT",
             "generated_internal_id": "CONT_AWD_RESELLER_FOOTPRINT",
             "matched_products": ["Testco"],
             "description": "Testco incident reporting subscription",
         }]})
    sweep["results"]["incumbent_buyer_map"]["buyers"].extend(
        footprint_buyers)
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    assert best_fit and all(pick.source_kind == "sam.gov"
                            for pick in best_fit)
    footprints = composer.select_client_footprints(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY)
    # Isolate footprint authority from unrelated competitor-figure display
    # collisions in this broad fixture.  Passing the footprints through both
    # build paths below still proves GID de-duplication.
    competitors = []
    teaming = composer.select_teaming(sweep, best_fit, competitors)
    horizon = composer.select_horizon(
        sweep, None, today=TODAY, scope=inputs["scope"],
        taxonomy=inputs["taxonomy"],
        award_corridors=footprints)
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=[], footprints=footprints)
    content = composer.build_hero(
        content, competitors, teaming, sweep, client_name="Testco",
        best_fit=[*best_fit, *footprints], footprints=footprints)

    signal = next(card for card in content["signal_cards"]
                  if card["label"] == "Active footprint")
    expected_components = [pick.amount for pick in footprints]
    expected_total = composer.fmt_money(sum(expected_components))
    assert signal["value"] == expected_total
    assert signal["sub"] == (
        f"Obligated across {len(footprints)} active "
        f"{'award' if len(footprints) == 1 else 'awards'}"
    )
    assert sum("active footprint" in row["head"]
               for row in content["news"]) == len(footprints)
    footprint_gids = {pick.gid for pick in footprints}
    scale_gids = [row["award_generated_id"]
                  for row in content["scale"]["rows"]]
    assert all(scale_gids.count(gid) == 1 for gid in footprint_gids)
    figure_gids = [row.get("generated_internal_id")
                   for row in content["figures"]]
    assert all(figure_gids.count(gid) == 1 for gid in footprint_gids)
    subtotals = [
        figure for figure in content["figures"]
        if (figure["text"] == expected_total
            and figure.get("component_raws") == expected_components)
    ]
    assert len(subtotals) == 1

    from agents.reports.board_content import (
        SignalBoardContent,
        reconcile_figures,
    )

    reconciliation = reconcile_figures(
        SignalBoardContent.model_validate(content), sweep)
    assert reconciliation.clean, (
        reconciliation.unbacked, reconciliation.unregistered)


def test_composer_uses_profile_display_name_only_in_public_copy(world):
    profile_path = world / "clients" / "testco" / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["display_name"] = "TestCo"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    sweep = _award_only_sweep()
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY)
    competitors = composer.select_competitors(
        sweep, today=TODAY, client_name="Testco")
    teaming = composer.select_teaming(sweep, best_fit, competitors)
    horizon = composer.select_horizon(
        sweep, None, today=TODAY, taxonomy=inputs["taxonomy"])
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=[])
    content = composer.build_hero(
        content, competitors, teaming, sweep, client_name="Testco",
        best_fit=best_fit)

    assert content["client_name"] == "Testco"
    assert content["hero_context"].startswith(
        "The nearest dated signal is TestCo’s")
    assert content["news"][0]["head"] == "NTSB · TestCo · active footprint"
    assert content["scale"]["rows"][0]["label"] == "NTSB × TestCo"
    trail = composer.build_trail(
        "Testco", best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, prose_trail=[], today=TODAY)
    assert trail["client_name"] == "Testco"


@pytest.mark.parametrize(("raw", "shown"), [
    ("NATIONAL TRANSPORTATION SAFETY BOARD", "NTSB"),
    ("FEDERAL BUREAU OF INVESTIGATION", "FBI"),
    ("DEPARTMENT OF THE NAVY", "Navy"),
    ("BUREAU OF LAND MANAGEMENT", "BLM"),
    ("FEDERAL HIGHWAY ADMINISTRATION", "FHWA"),
    ("OFFICE OF THE COMPTROLLER OF THE CURRENCY", "OCC"),
    ("CUSTOMS AND BORDER PROTECTION", "CBP"),
    ("NATIONAL INSTITUTES OF HEALTH", "NIH"),
    ("INTERNAL REVENUE SERVICE", "IRS"),
    ("SOCIAL SECURITY ADMINISTRATION", "SSA"),
])
def test_public_org_labels_use_recognizable_authority(raw, shown):
    assert composer._compact_org(raw) == shown


@pytest.mark.parametrize(("raw", "shown"), [
    ("COMPUTER PROJECTS OF ILLINOIS, INC.",
     "Computer Projects of Illinois"),
    ("REDSKY LLC", "RedSky"),
    ("CTG FEDERAL, LLC", "CTG Federal"),
    ("CENTRALSQUARE TECHNOLOGIES, LLC", "CentralSquare Technologies"),
])
def test_public_company_labels_preserve_brand_casing(raw, shown):
    assert composer._display_company(raw) == shown


def test_award_fallback_requires_current_core_evidence_and_identity(world):
    inputs = composer.load_client_inputs("Testco")
    sweep = _award_only_sweep()
    row = sweep["results"]["incumbent_buyer_map"]["buyers"][0]["records"][0]

    expired = _award_only_sweep()
    expired["results"]["incumbent_buyer_map"]["buyers"][0]["records"][0][
        "end_date"] = "2026-07-18"
    assert composer.select_award_best_fit(
        expired, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY) == []

    adjacent = _award_only_sweep()
    adjacent_row = adjacent["results"]["incumbent_buyer_map"]["buyers"][0][
        "records"][0]
    adjacent_row["matched_terms"] = ["case management"]
    adjacent_row["description"] = "case management subscription"
    assert composer.select_award_best_fit(
        adjacent, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY) == []

    row.pop("generated_internal_id")
    with pytest.raises(composer.ComposerSelectionError,
                       match="lacks structured award identity"):
        composer.select_award_best_fit(
            sweep, inputs["taxonomy"], inputs["scope"],
            client_name="Testco", today=TODAY)


def test_award_priority_drops_token_value_route_when_three_material_exist(
        world):
    sweep = _award_only_sweep()
    buyers = sweep["results"]["incumbent_buyer_map"]["buyers"]
    for index, amount in enumerate((550_000.0, 188_000.0, 11_132.10), 1):
        buyers.append({
            "buyer": f"BUYING OFFICE {index}",
            "agency": "Department of Homeland Security",
            "records": [{
                "kind": "award",
                "recipient": f"RIVAL {index}",
                "amount": amount,
                "end_date": f"2026-{9 + index:02d}-15",
                "award_id": f"AWARD-{index}",
                "generated_internal_id": f"CONT_AWD_{index}",
                "matched_terms": ["incident reporting"],
                "description": "incident reporting software subscription",
            }],
        })
    inputs = composer.load_client_inputs("Testco")

    selected = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY, n=3)

    assert len(selected) == 3
    assert all(pick.amount >= 50_000.0 for pick in selected)
    assert "AWARD-3" not in {pick.award_id for pick in selected}


def test_award_materiality_partition_is_prefix_stable_when_sparse(world):
    sweep = _zero_best_fit_sweep()
    buyers = sweep["results"]["incumbent_buyer_map"]["buyers"]
    for index, amount in enumerate(
            (250_000.0, 125_000.0, 20_000.0, 10_000.0), 1):
        buyers.append({
            "buyer": f"BUYING OFFICE {index}",
            "agency": "Department of Homeland Security",
            "records": [{
                "kind": "award",
                "recipient": f"RIVAL {index}",
                "amount": amount,
                "amount_basis": "obligated_to_date",
                "end_date": f"2027-0{index}-15",
                "award_id": f"MATERIAL-{index}",
                "generated_internal_id": f"CONT_AWD_MATERIAL_{index}",
                "matched_terms": ["incident reporting"],
                "description": "incident reporting software subscription",
            }],
        })
    inputs = composer.load_client_inputs("Testco")

    first_two = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY, n=2)
    first_three = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY, n=3)

    assert [pick.award_id for pick in first_three[:2]] == [
        pick.award_id for pick in first_two]
    assert all(pick.amount >= 50_000 for pick in first_three[:2])
    assert first_three[2].amount == 20_000.0


def test_hero_never_blends_client_and_competitor_obligations(world):
    sweep = _award_only_sweep()
    sweep["results"]["incumbent_buyer_map"]["buyers"].append({
        "buyer": "FEDERAL BUREAU OF INVESTIGATION",
        "agency": "Department of Justice",
        "records": [{
            "kind": "award",
                "recipient": "RIVAL INC.",
                "amount": 188_000.0,
                "amount_basis": "obligated_to_date",
            "end_date": "2027-03-31",
            "award_id": "RIVAL-1",
            "generated_internal_id": "CONT_AWD_RIVAL_1",
            "matched_terms": ["incident reporting"],
            "description": "incident reporting software subscription",
        }],
    })
    inputs = composer.load_client_inputs("Testco")
    best_fit = composer.select_award_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY)
    footprints = composer.select_client_footprints(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name="Testco", today=TODAY)
    competitors = composer.select_competitors(
        sweep, today=TODAY, client_name="Testco",
        taxonomy=inputs["taxonomy"], scope=inputs["scope"])
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=[], horizon=[], today=TODAY, offline=True,
        prose_trail=[], footprints=footprints)

    content = composer.build_hero(
        content, competitors, [], sweep, client_name="Testco",
        best_fit=best_fit, footprints=footprints)

    assert content["scale_label"] == "Competitor-held obligated history"
    assert content["scale"]["basis"] == "competitor_obligated_to_date"
    assert content["scale_total"] == "$188K"
    assert [row["award_generated_id"]
            for row in content["scale"]["rows"]] == ["CONT_AWD_RIVAL_1"]
    footprint = next(row for row in content["signal_cards"]
                     if row["label"] == "Active footprint")
    assert footprint["value"] == "$194.76K"


def test_competitor_band_uses_exact_products_and_excludes_client(world):
    sweep = _sweep()
    buyers = sweep["results"]["incumbent_buyer_map"]["buyers"]
    buyers.append({
        "buyer": "FEDERAL BUREAU OF INVESTIGATION",
        "agency": "Department of Justice",
        "records": [
            {"kind": "award", "recipient": "TESTCO TECHNOLOGY LLC",
             "amount": 80_000_000.0, "end_date": "2027-03-31",
             "award_id": "SELF-1", "generated_internal_id": "CONT_AWD_SELF",
             "matched_products": ["Testco"],
             "description": "Testco installed base"},
            {"kind": "award", "recipient": "CHANNEL PARTNER LLC",
             "amount": 90_000_000.0, "end_date": "2027-03-31",
             "award_id": "RESELLER-1",
             "generated_internal_id": "CONT_AWD_RESELLER",
             "matched_products": ["Testco"],
             "description": "Testco installed base via reseller"},
            {"kind": "award", "recipient": "BROAD ADJACENT VENDOR",
             "amount": 70_000_000.0, "end_date": "2027-03-31",
             "award_id": "ADJ-1", "generated_internal_id": "CONT_AWD_ADJ",
             "matched_terms": ["case management"],
             "description": "case management support"},
        ],
    })
    picks = composer.select_competitors(
        sweep, today=TODAY, client_name="Testco")
    assert [(pick.recipient, pick.direct_product) for pick in picks] == [
        ("ACTIVE VENDOR TWO", True),
    ]
    assert "named-product" in picks[0].why


@pytest.mark.parametrize("end_date", [None, "not-a-date", "2026-07-18"])
def test_competitor_active_claim_requires_current_dated_award(end_date):
    sweep = _sweep()
    row = sweep["results"]["incumbent_buyer_map"]["buyers"][1][
        "records"][0]
    row["end_date"] = end_date

    picks = composer.select_competitors(sweep, today=TODAY)

    assert row["award_id"] not in {pick.award_id for pick in picks}


def test_competitor_active_claim_includes_award_ending_today():
    sweep = _sweep()
    row = sweep["results"]["incumbent_buyer_map"]["buyers"][1][
        "records"][0]
    row["end_date"] = TODAY.isoformat()

    picks = composer.select_competitors(sweep, today=TODAY)

    assert row["award_id"] in {pick.award_id for pick in picks}


def test_competitor_source_text_kills_steelhead_ecology_collision():
    from tools.relevance.taxonomy import CapabilityTaxonomy

    taxonomy = CapabilityTaxonomy(
        client_name="Riverbed", version=1, updated="2026-07-19",
        core=[{"term": "SteelHead", "mode": "exact_phrase"}],
        exclude=[{
            "term": "salmon", "scope": "span",
            "reason": "steelhead fish restoration is not network software",
        }],
    )
    sweep = {"results": {"incumbent_buyer_map": {"buyers": [{
        "buyer": "BUREAU OF RECLAMATION",
        "agency": "Department of the Interior",
        "records": [
            {"kind": "award",
             "recipient": "SEQUOIA ECOLOGICAL CONSULTING, INC.",
             "amount": 2_102_971.0, "end_date": "2026-09-30",
             "award_id": "140R2025P0067",
             "generated_internal_id": "CONT_AWD_SALMON",
             "matched_products": ["SteelHead"],
             "description": ("BATTLE CREEK SALMON AND STEELHEAD "
                             "RESTORATION PROJECT")},
            {"kind": "award", "recipient": "REAL NETWORK VENDOR LLC",
             "amount": 100_000.0, "end_date": "2026-10-01",
             "award_id": "REAL-STEELHEAD",
             "generated_internal_id": "CONT_AWD_REAL_STEELHEAD",
             "matched_products": ["SteelHead"],
             "description": "RIVERBED STEELHEAD SOFTWARE MAINTENANCE"},
            {"kind": "award", "recipient": "TAG ONLY VENDOR LLC",
             "amount": 9_000_000.0, "end_date": "2027-01-01",
             "award_id": "TAG-ONLY",
             "generated_internal_id": "CONT_AWD_TAG_ONLY",
             "matched_products": ["SteelHead"],
             "description": "ANNUAL SOFTWARE SUPPORT"},
        ],
    }]}}}
    picks = composer.select_competitors(
        sweep, today=TODAY, client_name="Riverbed", taxonomy=taxonomy)
    assert [pick.award_id for pick in picks] == ["REAL-STEELHEAD"]


def test_mark43_reviewed_product_families_are_source_text_core():
    from tools.capability import load_profile

    profile = load_profile("mark43")
    taxonomy = composer.load_client_inputs("mark43")["taxonomy"]
    reviewed = profile.named_competitors_and_incumbents
    core_modes = {term.term: term.mode for term in taxonomy.core}
    assert taxonomy.version == 2
    assert taxonomy.updated == "2026-07-19"
    assert {name: core_modes.get(name) for name in reviewed} == {
        name: "exact_phrase" for name in reviewed
    }

    records = [{
        "kind": "award", "recipient": f"VENDOR {index}",
        "amount": 1_000.0 + index, "end_date": "2027-01-01",
        "award_id": f"PRODUCT-{index}",
        "generated_internal_id": f"CONT_AWD_PRODUCT_{index}",
        "matched_products": [product],
        "description": f"{product} annual software maintenance",
    } for index, product in enumerate(reviewed)]
    sweep = {"results": {"incumbent_buyer_map": {"buyers": [{
        "buyer": "FEDERAL BUREAU OF INVESTIGATION",
        "agency": "Department of Justice", "records": records,
    }]}}}
    picks = composer.select_competitors(
        sweep, n=20, today=TODAY, client_name="mark43",
        taxonomy=taxonomy)
    assert {pick.award_id for pick in picks} == {
        f"PRODUCT-{index}" for index in range(len(reviewed))
    }


def test_mark43_structured_codes_keep_only_in_boundary_competitor_lanes():
    taxonomy = composer.load_client_inputs("mark43")["taxonomy"]
    rows = [{
        "kind": "award",
        "recipient": "COMPUTER PROJECTS OF ILLINOIS, INC.",
        "amount": 188_830.0,
        "amount_basis": "obligated_to_date",
        "end_date": "2027-03-31",
        "award_id": "15F06724P0000749",
        "generated_internal_id": "CONT_AWD_OPENFOX",
        "matched_products": ["OpenFox"],
        "description": "OPENFOX SERVICES FOR THE CJIS DIVISION",
        "naics": {"code": "541519", "description": "Other computer"},
    }, {
        "kind": "award",
        "recipient": "CENTRALSQUARE TECHNOLOGIES, LLC",
        "amount": 11_130.0,
        "amount_basis": "obligated_to_date",
        "end_date": "2026-12-09",
        "award_id": "N6328526PS002",
        "generated_internal_id": "CONT_AWD_CENTRALSQUARE",
        "matched_products": ["CentralSquare"],
        "description": "CENTRALSQUARE ANNUAL SUBSCRIPTION FEES",
        "naics": {"code": "513210", "description": "Software publishers"},
    }, {
        "kind": "award",
        "recipient": "AXON ENTERPRISE, INC.",
        "amount": 548_070.0,
        "amount_basis": "obligated_to_date",
        "end_date": "2026-09-29",
        "award_id": "70Z02325F76100007",
        "generated_internal_id": "CONT_AWD_AXON",
        "matched_products": ["Axon Records"],
        "description": "AXON RECORDS & STANDARDS",
        "naics": {"code": "334220", "description": "Wireless equipment"},
    }]
    sweep = {"results": {"incumbent_buyer_map": {"buyers": [{
        "buyer": "FEDERAL BUYER",
        "agency": "Federal Agency",
        "records": rows,
    }]}}}

    picks = composer.select_competitors(
        sweep, n=5, today=TODAY, client_name="mark43",
        taxonomy=taxonomy)

    assert [pick.award_id for pick in picks] == ["15F06724P0000749"]


def test_exact_client_term_renders_as_federal_footprint_not_duplicate_brand():
    from tools.relevance.engine import score_record

    taxonomy = composer.load_client_inputs("mark43")["taxonomy"]
    verdict = score_record(
        {"description": "MARK43 EVIDENCE MANAGEMENT SOFTWARE"}, taxonomy)
    basis = composer._verdict_client_relevance_basis(
        verdict, kind="record-core-match", source_identity="AWARD-1",
        public_context={
            "band": "horizon",
            "client": "Mark43",
            "client_footprint": True,
            "event_kind": "award-window",
        })

    public = composer.client_relevance_public(basis)

    assert public["text"].startswith(
        "CONTINUITY CHECK · Mark43's cited federal footprint reaches")
    assert "Mark43's cited MARK43" not in public["text"]


def _pinned_sweep(client: str) -> dict:
    """Pinned snapshot of a real client sweep (2026-08-04).

    These tests once read data/cleaned live; every legitimate re-sweep
    rewrote the artifact and rotted the exact expectations below. The
    pinned copy keeps the selector contract exact and the suite immune to
    operations.
    """
    import gzip

    path = os.path.join(
        _REPO, "tests", "fixtures", f"pinned_searches_{client}.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.parametrize(("client", "expected_count", "expected_total"), [
    ("mark43", 2, 2_282_480.0),
    ("riverbed", 8, 19_542_538.78),
])
def test_live_client_footprint_selection(client, expected_count,
                                         expected_total):
    sweep = _pinned_sweep(client)
    inputs = composer.load_client_inputs(client)
    picks = composer.select_client_footprints(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name=client, today=TODAY)
    assert len(picks) == expected_count
    assert len({pick.gid for pick in picks}) == expected_count
    assert round(sum(pick.amount for pick in picks), 2) == expected_total


@pytest.mark.parametrize("client", ["mark43", "riverbed"])
def test_live_hero_context_is_an_evidence_derived_multi_sentence_readout(
        client, monkeypatch):
    from agents.reports import board_content

    # This test intentionally composes only the hero inputs. Route membership
    # is exercised against complete bands in the dedicated decision-maker
    # contract tests above.
    monkeypatch.setattr(
        board_content, "load_decision_makers", lambda _client: [])
    sweep = _pinned_sweep(client)
    inputs = composer.load_client_inputs(client)
    best_fit = composer.select_best_fit(
        sweep, inputs["taxonomy"], inputs["scope"])
    if not best_fit:
        best_fit = composer.select_award_best_fit(
            sweep, inputs["taxonomy"], inputs["scope"],
            client_name=client, today=TODAY)
    footprints = composer.select_client_footprints(
        sweep, inputs["taxonomy"], inputs["scope"],
        client_name=client, today=TODAY)
    content = composer.compose_content(
        client, sweep, best_fit=best_fit, footprints=footprints,
        competitors=[], teaming=[], horizon=[], today=TODAY, offline=True,
        prose_trail=[])
    hero = content["hero_context"]
    assert hero == composer.derive_executive_summary(
        client, content["best_fit"], content["teaming"])
    assert 2 <= len(composer._executive_summary_sentences(
        client, content["best_fit"], content["teaming"])) <= 5
    assert "pipeline" not in hero.lower()
    assert "retention" not in hero.lower()
    assert "expansion" not in hero.lower()
    if content["best_fit"][0]["machine_evidence"].get(
            "triage_verdict") == "monitor":
        assert " · MONITOR · " in content["best_fit"][0]["account"]


def test_buyer_map_queries_and_preserves_exact_client_footprint(monkeypatch):
    """The collector searches the client brand once and promotes only an
    exact legal-name recipient to structured product evidence. A mid-string
    lookalike must not become client installed-base evidence."""
    from datetime import timedelta

    from tools.api import incumbent_buyers
    from tools.capability import CapabilityTerms, ClientProfile

    profile = ClientProfile(
        client_name="Testco",
        capability_terms=CapabilityTerms(
            core=["incident reporting"],
            adjacent=["case management", "axon records"],
            excluded=[]),
        named_competitors_and_incumbents=["TESTCO", "Axon Records"],
        naics_boundary=["541512"],
    )
    calls = []

    def fake_search(term, *, subawards, years_back, limit=60):
        calls.append((term, subawards, years_back, limit))
        if term.casefold() != "testco" or subawards:
            return []
        end = (date.today() + timedelta(days=45)).isoformat()
        common = {
            "Award Amount": 194_760.0,
            "Awarding Agency": "National Transportation Safety Board",
            "Awarding Sub Agency": "NTSB ACQUISITION DIVISION",
            "End Date": end,
            "Description": "evidence management software subscription",
        }
        return [
            {**common, "Award ID": "9531BM23P0059",
             "generated_internal_id": "CONT_AWD_SELF_9508",
             "Recipient Name": "TESTCO, INC."},
            {**common, "Award ID": "LOOKALIKE",
             "generated_internal_id": "CONT_AWD_LOOKALIKE_9508",
             "Recipient Name": "NOT TESTCO INC"},
        ]

    monkeypatch.setattr(incumbent_buyers, "_search", fake_search)
    result = incumbent_buyers.build_buyer_map(
        profile, years_back=3, budget_seconds=30)

    award_terms = [term for term, subawards, _years, _limit in calls
                   if not subawards]
    assert award_terms == ["Testco", "Axon Records", "case management"]
    assert result["calls"] == {
        "usaspending_awards": 3,
        "usaspending_subawards": 3,
        "sam_api": 0,
    }
    records = [record for buyer in result["buyers"]
               for record in buyer["records"]]
    assert [record["award_id"] for record in records] == ["9531BM23P0059"]
    assert records[0]["recipient"] == "TESTCO, INC."
    assert records[0]["matched_products"] == ["Testco"]


# ── figures + reconciliation (finding 6) ────────────────────────────────────

def _full_content(sweep):
    bf, comp, team, hz = _select_all(sweep)
    trail_lines: list = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hz, today=TODAY, offline=True, prose_trail=trail_lines)
    return composer.build_hero(content, comp, team, sweep,
                               client_name="Testco"), (bf, comp, team, hz)


def test_every_rendered_dollar_reconciles_with_structured_identity(world):
    from agents.reports.board_content import (
        SignalBoardContent, reconcile_figures, reconciliation_error)
    from tools.api.award_repull import board_award_gids
    sweep = _sweep()
    content, picks = _full_content(sweep)
    validated = SignalBoardContent.model_validate(content)
    rec = reconcile_figures(validated, sweep)
    assert reconciliation_error(rec) is None, rec.unbacked
    assert all(not f.analyst_attested for f in validated.figures)
    # teaming renders an edge count, never an unkeyed aggregate dollar
    for card in content["teaming"]:
        assert "$" not in card["proof"]
    # acceptance probe: every selected lane and cited prime award drives the
    # sanctioned re-pull; the public link never outruns record verification.
    lane_gids = {c.gid for c in picks[1]}
    teaming_gids = {
        card["award_generated_id"] for card in content["teaming"]
        if card["machine_evidence"]["source_kind"]
        == "usaspending-subaward"
    }
    horizon_gids = {
        card["award_generated_id"] for card in content["horizon"]
        if card.get("award_generated_id")
    }
    assert lane_gids | teaming_gids | horizon_gids == set(
        board_award_gids(validated))
    for fig in validated.figures:
        if fig.component_raws:
            assert fig.generated_internal_id is None
            assert fig.source_url == "https://www.usaspending.gov/"
        else:
            assert fig.source_record_id and fig.generated_internal_id
            assert str(fig.source_system) in ("usaspending",
                                              "SourceSystem.USASPENDING")
            assert fig.retrieved_at is not None
    mocked_pull = [gid for gid in board_award_gids(validated)]
    assert len(mocked_pull) == len(
        lane_gids | teaming_gids | horizon_gids)


def test_equal_dollars_cannot_cross_back_records(world):
    from agents.reports.board_content import (
        BoardFigure, SignalBoardContent, reconcile_figures)
    sweep = {"results": {"lane": [
        {"id": "REC-A", "amount": 5000.0},
        {"id": "REC-B", "amount": 5000.0}]}}
    content = SignalBoardContent(
        client_name="Testco",
        figures=[BoardFigure(
            text="$5K", raw=5000.0, source_system="usaspending",
            source_record_id="REC-MISSING")])
    rec = reconcile_figures(content, sweep)
    assert rec.unbacked, ("a figure claiming an absent record id must not "
                          "back from equal values elsewhere")


def test_hero_rows_derive_the_displayed_total(world):
    content, _picks = _full_content(_sweep())
    shown = [r["amount"] for r in content["scale"]["rows"]]
    total_fig = next(f for f in content["figures"]
                     if f.get("component_raws"))
    assert len(shown) == len(total_fig["component_raws"])
    assert abs(sum(total_fig["component_raws"]) - total_fig["raw"]) < 0.01
    assert content["scale_total"] == content["scale"]["total"]
    assert "not pipeline revenue" not in content["scale_counts"]


# ── prose limits + fragment regressions (finding 8) ─────────────────────────

def test_prose_is_validated_at_final_length_never_sliced(world, monkeypatch):
    from agents.decisions import maxplan_cli
    long_line = ("Palantir Technologies provides broad data analysis "
                 "for Immigration and Customs Enforcement operations")
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    monkeypatch.setattr(maxplan_cli, "run_claude",
                        lambda *_a, **_k: long_line)
    trail: list = []
    line = composer.compose_line(
        "angle", "x", {"quoted_evidence": ["q"]}, template="Lane · dispatch",
        client="Testco", offline=False, trail=trail, max_len=60)
    assert line == "Lane · dispatch"          # template, not a sliced frag
    assert not line.endswith(" and")
    assert trail[0]["source"] == "template-fallback"


def test_committed_fragments_cannot_recur(world):
    sweep = _sweep()
    content, _picks = _full_content(sweep)
    fields = []
    for card in content["competitors"]:
        fields.append(card["wedge"])
    for card in content["teaming"]:
        fields.append(card["angle"])
    for card in content["best_fit"]:
        fields.extend(c["value"] for c in card["cells"])
    for text in fields:
        assert not text.rstrip().endswith((" and", " for", " the", "·")), text


def test_lint_failure_falls_back_to_template(world, monkeypatch):
    from agents.decisions import maxplan_cli
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    monkeypatch.setattr(maxplan_cli, "run_claude",
                        lambda *_a, **_k: "a best-in-class — game changer")
    trail: list = []
    line = composer.compose_line(
        "fit", "x1", {"quoted_evidence": ["q"]}, template="Capability match",
        client="Testco", offline=False, trail=trail, max_len=40)
    assert line == "Capability match"
    assert trail[0]["source"] == "template-fallback"


# ── identity + CLI + publication ordering (findings 1, 2, 4, 9) ─────────────

def _cli(world, argv):
    import run_compose
    return run_compose.main(argv)


def _rewrite_bound_machine_board(world, payload):
    """Write a deliberately mutated board and bind its mocked trail to it."""
    import hashlib

    board = world / "clients" / "testco" / "signal_board_content.json"
    board_bytes = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n") \
        .encode("utf-8")
    board.write_bytes(board_bytes)
    trail = world / "clients" / "testco" / "compose_trail.md"
    lines = trail.read_text(encoding="utf-8").splitlines()
    lines[-1] = ("pair content sha256 "
                 + hashlib.sha256(board_bytes).hexdigest())
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_slug_display_identity(world, capsys):
    """Each client composes from ITS OWN sweep bound to ITS OWN receipt;
    the display name rides content and the exact trail header grammar."""
    from agents.assessment_chain import COMPOSE_TRAIL_HEADER_RE
    for slug, display in (("insignary", "Insignary"),
                          ("netscout", "NETSCOUT"),
                          ("acme_federal_systems", "Acme Federal Systems")):
        _world_client(world, slug, display)
        rc = _cli(world, ["--client", slug, "--today", "2026-07-19"])
        captured = capsys.readouterr()
        assert rc == 0, (captured.out, captured.err)
        content = json.loads(
            (world / "clients" / slug / "signal_board_content.json")
            .read_text(encoding="utf-8"))
        assert content["client_name"] == display
        assert content["composition_mode"] == "machine"
        trail_text = (world / "clients" / slug / "compose_trail.md") \
            .read_text(encoding="utf-8")
        first = next(ln for ln in trail_text.splitlines() if ln.strip())
        match = COMPOSE_TRAIL_HEADER_RE.fullmatch(first)
        assert match and match.group("client") == display
        markers = [ln for ln in captured.out.splitlines()
                   if ln.startswith("[out:compose-trail] ")]
        assert len(markers) == 1


def test_cli_refuses_a_foreign_client_sweep(world, capsys):
    sweep_path = world / "data" / "cleaned" / "searches_testco.json"
    payload = json.loads(sweep_path.read_text(encoding="utf-8"))
    payload["client"] = "Someone Else"
    sweep_path.write_text(json.dumps(payload), encoding="utf-8")
    _mint_receipt(world)
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "load-sweep"
    assert "Someone Else" in err["reason"]


def test_cli_refuses_missing_stale_or_tampered_relevance(world, capsys):
    rel = world / "data" / "state" / "relevance" / "testco.calibration.csv"
    receipt = world / "data" / "state" / "relevance" / "testco.run.json"

    receipt.unlink()                                      # missing receipt
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["stage"] == "relevance"

    _mint_receipt(world)
    rel.write_text(CAL_HEADER + "Testco,zzz,9,,tampered\n",
                   encoding="utf-8")                       # tampered CSV
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["stage"] == "relevance"

    rel.write_text(CAL_HEADER
                   + "Otherco,aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1,6,,preset:all_federal@v1\n",
                   encoding="utf-8")                       # foreign rows
    _mint_receipt_foreign_ok = False
    try:
        _mint_receipt(world)
    except ValueError:
        _mint_receipt_foreign_ok = True                    # producer refuses
    assert _mint_receipt_foreign_ok
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    assert not (world / "clients" / "testco"
                / "signal_board_content.json").exists()
    capsys.readouterr()


def test_cli_safe_stops_on_zero_best_fit(world, capsys):
    bare = _zero_best_fit_sweep()
    _world_client(world, "testco", "Testco", sweep=bare)
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["stage"] == "select"
    assert "machine-screened" in payload["reason"]
    assert not (world / "clients" / "testco"
                / "signal_board_content.json").exists()
    assert not (world / "clients" / "testco" / "compose_trail.md").exists()
    # stderr is the ONLY failure channel: no sidecar interface exists
    assert not (world / "clients" / "testco"
                / "compose_failure.json").exists()


def test_cli_second_write_over_existing_artifacts(world, capsys):
    rc = _cli(world, ["--client", "testco", "--offline",
                      "--today", "2026-07-19"])
    assert rc == 0
    first = (world / "clients" / "testco" / "signal_board_content.json") \
        .read_bytes()
    rc = _cli(world, ["--client", "testco", "--offline",
                      "--today", "2026-07-19"])
    assert rc == 0
    second = (world / "clients" / "testco" / "signal_board_content.json") \
        .read_bytes()
    assert first == second                     # byte-identical offline rerun
    capsys.readouterr()


def test_cli_trail_publishes_before_content(world, capsys, monkeypatch):
    import run_compose
    from tools import atomic_io
    calls: list = []
    real = atomic_io.atomic_write_text

    def tracking(path, text):
        calls.append(os.path.basename(str(path)))
        return real(path, text)
    monkeypatch.setattr(atomic_io, "atomic_write_text", tracking)
    monkeypatch.setattr(run_compose, "main", run_compose.main)
    rc = _cli(world, ["--client", "testco", "--offline",
                      "--today", "2026-07-19"])
    assert rc == 0
    assert calls.index("compose_trail.md") < calls.index(
        "signal_board_content.json")
    capsys.readouterr()


# ── actual adapter acceptance (findings 1, 3; required verification) ────────

def _adapter_world(tmp_path, monkeypatch):
    """A hermetic repo root for the REAL SubprocessRunnerAdapter: code is
    symlinked from this worktree, data and clients are tmp-real, and the
    subprocess PATH is stripped so the prose seam stays offline.

    These tests exercise the legacy composer contract in isolation.  The
    Candidate Review watch has its own real-adapter integration coverage, so
    keep that additive stage out of this deliberately minimal world even
    though the symlinked ``agents`` directory contains its package.
    """
    import agents.assessment_chain as assessment_chain

    monkeypatch.setattr(
        assessment_chain, "candidate_review_watch_enabled", lambda _root: False
    )
    for name in ("agents", "tools"):
        os.symlink(os.path.join(_REPO, name), tmp_path / name)
    os.symlink(os.path.join(_REPO, "run_compose.py"),
               tmp_path / "run_compose.py")
    (tmp_path / "clients" / "testco").mkdir(parents=True)
    (tmp_path / "clients" / "testco" / "profile.json").write_text(json.dumps({
        "client_name": "Testco",
        "capability_terms": {
            "core": ["computer-aided dispatch", "records management system",
                     "dispatch system", "incident reporting"],
            "adjacent": [], "excluded": []},
        "naics_boundary": ["541512"],
    }), encoding="utf-8")
    (tmp_path / "data" / "cleaned").mkdir(parents=True)
    (tmp_path / "data" / "cleaned" / "searches_testco.json").write_text(
        json.dumps(_sweep()), encoding="utf-8")
    (tmp_path / "data" / "review").mkdir(parents=True)
    (tmp_path / "data" / "review" / "testco.review.json").write_text(
        json.dumps({"client_name": "Testco", "status": "approved"}),
        encoding="utf-8")
    rel = tmp_path / "data" / "state" / "relevance" / "testco.calibration.csv"
    rel.parent.mkdir(parents=True)
    rel.write_text(CAL_HEADER
                   + "Testco,aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1,6,,preset:all_federal@v1\n",
                   encoding="utf-8")
    _mint_receipt(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def test_actual_subprocess_adapter_accepts_normal_compose(
        tmp_path, monkeypatch):
    from agents.assessment_chain import SubprocessRunnerAdapter
    root = _adapter_world(tmp_path, monkeypatch)
    adapter = SubprocessRunnerAdapter(root=root, python=sys.executable)
    outcome = adapter.run("COMPOSING", slug="testco", mode="assessment")
    assert outcome.success is True, (outcome.reason, outcome.stderr)
    assert "board content composed" in outcome.note
    assert "compose_trail.md" in outcome.note


def test_actual_refresh_side_trail_seam_accepts_the_composer(
        tmp_path, monkeypatch):
    """The refresh adapter validates the compose refresh through the same
    _fresh_compose_trail seam (assessment_chain.py:1582); drive it with a
    real composer subprocess exactly as the refresh flow does."""
    import subprocess as sp

    from agents.assessment_chain import (
        compose_pair_tokens, validate_compose_pair)
    root = _adapter_world(tmp_path, monkeypatch)
    content_before, trails_before = compose_pair_tokens(root, "testco")
    proc = sp.run([sys.executable, str(root / "run_compose.py"),
                   "--client", "testco", "--today", "2026-07-19"],
                  cwd=root, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    trail = validate_compose_pair(
        root, "testco", "Testco", proc.stdout,
        content_before=content_before, trails_before=trails_before)
    assert trail.name == "compose_trail.md"


def test_adapter_rejects_wrong_display_identity(tmp_path, monkeypatch):
    from agents.assessment_chain import SubprocessRunnerAdapter
    root = _adapter_world(tmp_path, monkeypatch)
    profile = root / "clients" / "testco" / "profile.json"
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["client_name"] = "Wrongco"
    profile.write_text(json.dumps(payload), encoding="utf-8")
    adapter = SubprocessRunnerAdapter(root=root, python=sys.executable)
    outcome = adapter.run("COMPOSING", slug="testco", mode="assessment")
    assert outcome.success is False


def test_direct_assessment_adapter_preserves_structured_c1_failure(
        tmp_path, monkeypatch):
    """The assessment-mode adapter parses C1's named JSON structurally:
    reason and fix surface ride the outcome and the stage names the
    note; the serialized blob is never stored as an opaque reason."""
    from agents.assessment_chain import SubprocessRunnerAdapter
    root = _adapter_world(tmp_path, monkeypatch)
    (root / "data" / "state" / "relevance" / "testco.run.json").unlink()
    adapter = SubprocessRunnerAdapter(root=root, python=sys.executable)
    outcome = adapter.run("COMPOSING", slug="testco", mode="assessment")
    assert outcome.success is False
    assert outcome.returncode == 2
    assert outcome.note == "composer failed at relevance"
    assert outcome.reason == (
        "relevance receipt is missing, stale, tampered, or bound to "
        "other evidence; C1 composes only from the sanctioned "
        "relevance state")
    assert "tools.relevance.calibrate" in (outcome.fix_surface or "")
    assert "{" not in (outcome.reason or "")


def test_no_failure_sidecar_after_failure_or_success(world, capsys):
    """The persistent compose_failure.json interface is removed: neither
    a named failure nor a success may create it."""
    sidecar = world / "clients" / "testco" / "compose_failure.json"
    bare = _zero_best_fit_sweep()
    _world_client(world, "testco", "Testco", sweep=bare)
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 2
    assert not sidecar.exists()
    _world_client(world, "testco", "Testco")
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    assert not sidecar.exists()
    capsys.readouterr()


# ── relevance freshness and canonical identity (defect round 2) ─────────────

def test_canonical_slug_cases():
    """The ONE slug family: punctuation, whitespace runs, slashes,
    ampersands, and non-ASCII resolve identically everywhere the
    composer stack names a path (receipt, calibration CSV, compose
    client); the C3 runner delegates instead of reimplementing."""
    import run_refresh_press
    from agents.assessment_chain import canonical_slug
    cases = {
        "Booz Allen, Inc.": "booz_allen_inc",
        "Acme  Federal   Systems": "acme_federal_systems",
        "L3/Harris": "l3_harris",
        "Mark43 & Co": "mark43_co",
        "Café Systems": "caf_systems",
    }
    for name, slug in cases.items():
        assert canonical_slug(name) == slug
        assert run_refresh_press._slug(name) == slug


def test_receipt_survives_filesystem_alias_spellings(world):
    """A receipt minted under one spelling of the root validates under an
    equivalent filesystem alias (macOS /var vs /private/var): command and
    input-binding paths compare through realpath; every sha256 binding
    stays exact."""
    from agents.assessment_chain import relevance_receipt_valid
    alias = world.parent / (world.name + "-alias")
    os.symlink(world, alias)
    _mint_receipt(alias)                # receipt paths spelled via the alias
    assert relevance_receipt_valid("testco", root=world,
                                   allow_board_content_change=True)
    assert relevance_receipt_valid("testco", root=alias,
                                   allow_board_content_change=True)


def test_zero_exit_no_write_calibration_cannot_mint_receipt(
        world, capsys, monkeypatch):
    """A no-op calibrator that exits zero fails the relevance step before
    compose, press, or delta: no fresh artifact, no receipt, exactly one
    named marker."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import run_refresh_press as refresh
    monkeypatch.setattr(refresh, "ROOT", str(world))
    receipt = world / "data" / "state" / "relevance" / "testco.run.json"
    receipt.unlink()
    rel_path = str(world / "data" / "state" / "relevance"
                   / "testco.calibration.csv")
    command = [sys.executable, "-m", "tools.relevance.calibrate",
               "--client", "Testco", "--out", rel_path]

    def noop(_cmd, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="[calibrate] Testco: 1 records · scope "
                   "preset:all_federal@v1 · 0 FP · 0 FN · 0 out-of-scope\n",
            stderr="")

    code = refresh._relevance_step(
        "Testco", "testco", rel_path,
        datetime(2026, 7, 19, tzinfo=timezone.utc), noop, command)
    assert code == 2
    err = capsys.readouterr().err
    markers = [ln for ln in err.splitlines()
               if ln.startswith("[refresh:failure] ")]
    assert len(markers) == 1
    payload = json.loads(markers[0].split(" ", 1)[1])
    assert payload["stage"] == "relevance"
    assert "fresh artifact" in payload["reason"]
    assert not receipt.exists()


def test_relevance_receipt_failure_emits_exactly_one_marker(
        world, capsys, monkeypatch):
    """A receipt-mint failure through the full refresh loop emits ONE
    named [refresh:failure] marker and stops before compose."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import run_refresh_press as refresh
    monkeypatch.setattr(refresh, "ROOT", str(world))
    (world / "data" / "state" / "relevance" / "testco.run.json").unlink()
    commands: list = []

    def execute(cmd, **_kwargs):
        commands.append(cmd)
        if "tools.relevance.calibrate" in cmd:
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w", encoding="utf-8") as f:
                f.write("wrong,header\nx,y\n")   # fresh, not production-valid
            return SimpleNamespace(
                returncode=0,
                stdout="[calibrate] Testco: 1 records · scope "
                       "preset:all_federal@v1 · 0 FP · 0 FN · "
                       "0 out-of-scope\n",
                stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    code = refresh.run_refresh_press(
        "Testco", now=datetime(2026, 7, 19, tzinfo=timezone.utc),
        executor=execute)
    assert code == 2
    err = capsys.readouterr().err
    markers = [ln for ln in err.splitlines()
               if ln.startswith("[refresh:failure] ")]
    assert len(markers) == 1
    payload = json.loads(markers[0].split(" ", 1)[1])
    assert payload["stage"] == "relevance"
    assert "could not be minted" in payload["reason"]
    assert not any("run_compose.py" in part
                   for cmd in commands for part in cmd)


# ── exact client binding: foreign payload refusals (defect round 2) ─────────

def test_cli_refuses_foreign_taxonomy(world, capsys):
    """A foreign-client taxonomy at this client's path refuses named even
    when the relevance receipt binds its sha: path placement plus a hash
    is not identity proof."""
    (world / "clients" / "testco" / "capability_taxonomy.json").write_text(
        json.dumps({
            "client_name": "NETSCOUT", "version": 1,
            "updated": "2026-07-01",
            "core": [{"term": "packet capture", "mode": "stemmed"}],
        }), encoding="utf-8")
    _mint_receipt(world)          # the receipt honestly binds the new sha
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "load-client"
    assert "NETSCOUT" in err["reason"]
    assert "not identity proof" in err["reason"]


def test_cli_refuses_foreign_recompete_calendar(world, capsys, monkeypatch):
    """A foreign-client recompete calendar at this client's path refuses
    named and never feeds horizon selection; the client's own calendar
    still composes."""
    cal_dir = world / "data" / "state" / "recompete"
    cal_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LILA_RECOMPETE_DIR", str(cal_dir))
    calendar = cal_dir / "testco.json"
    calendar.write_text(json.dumps({"client": "NETSCOUT", "attack": []}),
                        encoding="utf-8")
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "load-calendar"
    assert "NETSCOUT" in err["reason"]
    calendar.write_text(json.dumps({"client": "Testco", "attack": []}),
                        encoding="utf-8")
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()


def test_cli_refuses_foreign_prior_board_content(world, capsys):
    """Prior signal-board content belonging to another client refuses
    named before any composition; the foreign artifact is untouched."""
    board = world / "clients" / "testco" / "signal_board_content.json"
    board.write_text(json.dumps({"client_name": "NETSCOUT"}),
                     encoding="utf-8")
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "load-board"
    assert "NETSCOUT" in err["reason"]
    assert json.loads(board.read_text(encoding="utf-8"))["client_name"] \
        == "NETSCOUT"
    assert not (world / "clients" / "testco" / "compose_trail.md").exists()


def test_press_refuses_foreign_board_content(world, capsys):
    """The press refuses named when the loaded signal-board content
    belongs to another client; a foreign artifact never renders."""
    from types import SimpleNamespace

    import run_signal_board
    (world / "clients" / "testco" / "signal_board_content.json").write_text(
        json.dumps({"client_name": "NETSCOUT"}), encoding="utf-8")
    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))
    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "NETSCOUT" in err


def test_press_refuses_angle_only_edit_against_bound_compose_trail(
        world, capsys, monkeypatch):
    """Direct replay validates the exact compose generation before render;
    an angle-only edit cannot bypass C3 by invoking press directly."""
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    payload["teaming"][0]["angle"] = "GENERIC PARTNER FIT"
    board.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(run_signal_board, "REPORT_DIR",
                        str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine compose pair failed its trail binding" in err
    assert "changed after composition" in err


def test_press_refuses_rebuilt_pair_with_decision_maker_route_swap(
        world, capsys, monkeypatch):
    """The independent reviewed-person file defeats a valid-route swap."""
    from types import SimpleNamespace

    import run_signal_board

    people = {
        "schema_version": 1,
        "decision_makers": [{
            "name": "Pat Example",
            "title": "Chief Information Officer",
            "organization": "Department of Homeland Security",
            "bio_url": "https://www.dhs.gov/official/pat-example",
            "retrieved_at": "2026-07-20T00:00:00Z",
            "route_kind": "sam.gov",
            "route_identity": CORE_NOTICE["source_id"],
            "route_label": "Displayed dispatch modernization notice",
            "relevance_rationale": (
                "The official role maps to the displayed DHS route."),
        }],
    }
    path = world / "clients" / "testco" / "signal_board_people.json"
    path.write_text(json.dumps(people), encoding="utf-8")
    assert _cli(
        world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()

    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    replacement = payload["competitors"][0]["award_generated_id"]
    payload["decision_makers"][0]["route_kind"] = "usaspending-award"
    payload["decision_makers"][0]["route_identity"] = replacement
    _rewrite_bound_machine_board(world, payload)
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "decision-maker route integrity failed" in err
    assert "independent client-scoped reviewed records" in err


def test_press_refuses_pair_bound_executive_summary_edit(
        world, capsys, monkeypatch):
    """Rebinding the SHA cannot turn unsupported hero prose into evidence."""
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    payload["hero_context"] = (
        "This fabricated opportunity is certain to close this quarter.")
    _rewrite_bound_machine_board(world, payload)
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine executive summary evidence failed" in err
    assert "does not match the selected best-fit" in err


@pytest.mark.parametrize(
    ("mutated_verdict", "mutated_reason", "visible_verdict"),
    [
        ("", "", ""),
        ("pursue", "fabricated qualification", "PURSUE"),
    ],
    ids=["removed", "changed"],
)
def test_press_refuses_pair_bound_monitor_disposition_tamper(
        world, capsys, monkeypatch, mutated_verdict, mutated_reason,
        visible_verdict):
    """A rebound card and matching hero cannot erase or change the stored
    monitor disposition before direct press."""
    from types import SimpleNamespace

    import run_signal_board

    sweep_path = world / "data" / "cleaned" / "searches_testco.json"
    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    source_id = str(sweep["results"]["sam.gov"][0]["source_id"])
    sweep["results"]["triage"][source_id] = {
        "verdict": "monitor",
        "reason": "named-incumbent renewal; track, do not qualify",
    }
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")
    _mint_receipt(world)
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()

    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    card = next(
        row for row in payload["best_fit"]
        if row["machine_evidence"]["source_identity"] == source_id)
    assert " · MONITOR · " in card["account"]
    card["machine_evidence"]["triage_verdict"] = mutated_verdict
    card["machine_evidence"]["triage_reason"] = mutated_reason
    if visible_verdict:
        card["account"] = card["account"].replace(
            " · MONITOR · ", f" · {visible_verdict} · ")
    else:
        card["account"] = card["account"].replace(" · MONITOR", "")
    payload["hero_context"] = composer.derive_executive_summary(
        payload["client_name"], payload["best_fit"], payload["teaming"])
    _rewrite_bound_machine_board(world, payload)
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine executive summary evidence failed" in err
    assert "does not match the stored sweep" in err


def test_press_refuses_pair_bound_precontract_generic_teaming_card(
        world, capsys, monkeypatch):
    """A correctly re-bound stale artifact still fails the current semantic
    contract when it lacks route_basis and carries the old placeholder."""
    import hashlib
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    card = payload["teaming"][0]
    card["angle"] = "VALIDATE PARTNER FIT · ACTIVE ACCOUNT"
    card["proof"] = "PARTNER HYPOTHESIS · VALIDATION REQUIRED"
    card["machine_evidence"].pop("route_basis")
    board_bytes = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n") \
        .encode("utf-8")
    board.write_bytes(board_bytes)
    trail = world / "clients" / "testco" / "compose_trail.md"
    lines = trail.read_text(encoding="utf-8").splitlines()
    lines[-1] = ("pair content sha256 "
                 + hashlib.sha256(board_bytes).hexdigest())
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(run_signal_board, "REPORT_DIR",
                        str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine Section 03 route evidence failed" in err
    assert "lacks the current structured route basis" in err


def test_press_refuses_pair_bound_horizon_job_context_tamper(
        world, capsys, monkeypatch):
    """Rebinding the pair cannot turn invented Horizon copy into evidence."""
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    payload["horizon"][0]["job_context"]["text"] = \
        "Guaranteed modernization opportunity"
    _rewrite_bound_machine_board(world, payload)
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine prospective horizon evidence failed" in err
    assert "does not match its structured source basis" in err


def test_press_refuses_pair_bound_horizon_source_policy_tamper(
        world, capsys, monkeypatch):
    """A pair-bound award title cannot masquerade as its work description."""
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    card = payload["horizon"][0]
    basis = card["machine_evidence"]["job_context_basis"]
    basis["event_kind"] = "award-window"
    basis["source_field"] = "title"
    card["machine_evidence"]["source_kind"] = "usaspending-award"
    card["job_context"] = {
        "kind": "published-title",
        "text": basis["source_text"],
    }
    _rewrite_bound_machine_board(world, payload)
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine prospective horizon evidence failed" in err
    assert "source field 'title' is not allowed for 'award-window'" in err


def test_press_refuses_pair_bound_horizon_basis_and_public_copy_tamper(
        world, capsys, monkeypatch):
    """Rebinding the content SHA cannot replace the trail's source basis."""
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    card = payload["horizon"][0]
    basis = card["machine_evidence"]["job_context_basis"]
    basis["source_text"] = "invented modernization services"
    card["job_context"] = {
        "kind": ("published-title" if basis["source_field"] == "title"
                 else "published-description"),
        "text": "invented modernization services",
    }
    _rewrite_bound_machine_board(world, payload)
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(
        run_signal_board, "REPORT_DIR", str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine prospective horizon evidence failed" in err
    assert "job-context basis does not match its trail-bound source" in err


def test_press_pair_uses_profile_exact_identity_not_content_self_assertion(
        world, capsys, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    board = world / "clients" / "testco" / "signal_board_content.json"
    payload = json.loads(board.read_text(encoding="utf-8"))
    payload["client_name"] = "TESTCO"  # same slug, wrong profile spelling
    board_bytes = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n") \
        .encode("utf-8")
    board.write_bytes(board_bytes)
    trail = world / "clients" / "testco" / "compose_trail.md"
    lines = trail.read_text(encoding="utf-8").splitlines()
    lines[0] = "# INTERNAL compose trail · TESTCO · never leaves the shop"
    lines[-1] = ("pair content sha256 "
                 + hashlib.sha256(board_bytes).hexdigest())
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(run_signal_board, "REPORT_DIR",
                        str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED: machine compose pair failed its trail binding" in err
    assert "exact client header" in err


def test_press_refuses_foreign_profile_placed_under_requested_slug(
        world, capsys, monkeypatch):
    """Profile path placement cannot authorize another client's pair."""
    from types import SimpleNamespace

    import run_signal_board

    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    profile_path = world / "clients" / "testco" / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["client_name"] = "Foreign Client"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(run_signal_board, "ROOT", str(world))
    monkeypatch.setattr(run_signal_board, "REPORT_DIR",
                        str(world / "data" / "reports"))

    rc = run_signal_board._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    err = capsys.readouterr().err
    assert "machine content profile belongs to 'Foreign Client'" in err
    assert "path placement is not identity proof" in err


# ── machine-screened all-card evidence law (defect round 2) ─────────────────

def test_selection_excludes_off_scope_rows_and_trail_proves_scope(
        world, capsys):
    """With an engagement scope, off-scope buyer lanes never select (a
    boundary never includes) and every selected row's trail carries the
    positive scope verdict with its basis."""
    (world / "clients" / "testco" / "engagement_scope.json").write_text(
        json.dumps({"departments": ["DHS"]}), encoding="utf-8")
    _mint_receipt(world)      # the scope file is a bound receipt input
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    content = json.loads(
        (world / "clients" / "testco" / "signal_board_content.json")
        .read_text(encoding="utf-8"))
    assert content["competitors"], "the DHS lane must survive"
    assert all(card["agency_name"] == "Department of Homeland Security"
               for card in content["competitors"])
    assert not any("REVENUE" in (card.get("label") or "")
                   for card in content["competitors"])
    trail = (world / "clients" / "testco" / "compose_trail.md") \
        .read_text(encoding="utf-8")
    assert "scope in-scope · basis in-scope:DHS" in trail
    capsys.readouterr()


def test_all_machine_bands_reject_missing_or_off_scope_evidence(world):
    """Best-fit, competitor, teaming, and horizon selections each fail
    validation when their structured identity, quote, or POSITIVE scope
    verdict is missing; a nonempty scope_basis string alone is never
    proof of being in scope."""
    import copy

    sweep = _sweep()
    inputs = composer.load_client_inputs("Testco")
    bf = composer.select_best_fit(sweep, inputs["taxonomy"], inputs["scope"])
    comp = composer.select_competitors(sweep, today=TODAY)
    team = composer.select_teaming(sweep, bf, comp)
    hor = composer.select_horizon(sweep, None, today=TODAY)
    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hor, today=TODAY, offline=True, prose_trail=prose)
    content = composer.build_hero(content, comp, team, sweep,
                                  client_name="Testco")
    trail = composer.build_trail(
        "Testco", best_fit=bf, competitors=comp, teaming=team, horizon=hor,
        prose_trail=prose, today=TODAY, scope=None)
    composer.validate_machine_evidence(content, trail)      # clean passes

    cases = [
        ("best_fit", 0, "quoted_spans", [], "no\n?.*quoted spans"),
        ("best_fit", 0, "relevant", False, "not\n?.*engine-relevant"),
        ("competitors", 0, "quote", "", "quote or identity"),
        ("competitors", 0, "award_id", "", "quote or identity"),
        ("competitors", 0, "in_scope", False, "positive in-scope"),
        ("competitors", 0, "scope_basis", "off-scope:DoD",
         "positive in-scope"),
        ("teaming", 0, "quote", "", "no\n?.*evidence quote"),
        ("teaming", 0, "in_scope", False, "positive in-scope"),
        ("teaming", 0, "play_award_id", "", "prime\n?.*award identity"),
        ("horizon", 0, "quote", "", "quote, date basis, or scope basis"),
        ("horizon", 0, "in_scope", False, "positive in-scope"),
    ]
    for band, index, field_name, value, _needle in cases:
        tampered = copy.deepcopy(trail)
        tampered["selections"][band][index][field_name] = value
        with pytest.raises(ValueError):
            composer.validate_machine_evidence(content, tampered)


def test_trail_contains_every_selected_records_evidence(world, capsys):
    """The published INTERNAL trail renders every selected record's
    structured identity, exact quote, dates, and scope basis for ALL
    four bands, not only best-fit."""
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    trail = (world / "clients" / "testco" / "compose_trail.md") \
        .read_text(encoding="utf-8")
    # competitor lanes: award identity, buyer-record quote, scope verdict
    assert "identity award 2032H822F0002" in trail
    assert 'quote: "incident reporting suite"' in trail
    # teaming: the genuine prime-subaward route only
    assert "play award PA-1" in trail
    assert ('quote: "computer-aided dispatch integration subcontract"'
            in trail)
    # horizon: kind, exact date basis, record quote
    assert "kind forecast" in trail
    assert "date basis 2026-08-20" in trail
    assert 'quote: "Computer-aided dispatch refresh forecast"' in trail
    assert "kind expiring" in trail
    assert "date basis 2026-08-30" in trail
    # scope verdicts render beside their basis on every band
    assert "scope in-scope · basis UNSCOPED" in trail
    capsys.readouterr()


def test_press_fails_closed_when_taxonomy_missing_for_machine_content(
        world, capsys, monkeypatch):
    """Machine content without its profile-owned identity fails immediately."""
    from types import SimpleNamespace

    import run_signal_board as runner
    reports = world / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "ROOT", str(world))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    (world / "clients" / "testco" / "profile.json").unlink()
    rc = runner._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))
    assert rc == 2
    assert "machine content has no profile-owned exact client identity" \
        in capsys.readouterr().err
    assert not (reports / "testco.federal_opportunity_signals.internal.md") \
        .exists()


def test_press_refuses_off_scope_machine_rows(world, capsys, monkeypatch):
    """An off-scope competitor, teaming, or horizon row in machine
    content cannot press: each band's row raises an OFF_SCOPE violation
    and the artifact is DO-NOT-SEND."""
    from types import SimpleNamespace

    import run_signal_board as runner
    reports = world / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "ROOT", str(world))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    board = world / "clients" / "testco" / "signal_board_content.json"
    content = json.loads(board.read_text(encoding="utf-8"))
    for band in ("competitors", "teaming", "horizon"):
        content[band][0]["machine_evidence"]["agency"] = \
            "Department of the Treasury"
    _rewrite_bound_machine_board(world, content)
    (world / "clients" / "testco" / "engagement_scope.json").write_text(
        json.dumps({"departments": ["DHS"]}), encoding="utf-8")
    rc = runner._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))
    assert rc == 2
    sidecar = (reports / "testco.federal_opportunity_signals.internal.md") \
        .read_text(encoding="utf-8")
    assert "OFF_SCOPE: machine-composed competitors row" in sidecar
    assert "OFF_SCOPE: machine-composed teaming row" in sidecar
    assert "OFF_SCOPE: machine-composed horizon row" in sidecar
    capsys.readouterr()


def test_press_scope_gate_covers_acquisition_pathways(
        world, capsys, monkeypatch):
    from types import SimpleNamespace

    import run_signal_board as runner

    sweep = _add_distinct_acquisition_notice(_sweep())
    sweep["results"]["subawards"] = {"primes": [], "edges": {}}
    _world_client(world, "testco", "Testco", sweep=sweep)
    reports = world / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "ROOT", str(world))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    board = world / "clients" / "testco" / "signal_board_content.json"
    content = json.loads(board.read_text(encoding="utf-8"))
    assert content["acquisition_pathways"]
    content["acquisition_pathways"][0]["machine_evidence"]["agency"] = \
        "Department of the Treasury"
    _rewrite_bound_machine_board(world, content)
    (world / "clients" / "testco" / "engagement_scope.json").write_text(
        json.dumps({"departments": ["DHS"]}), encoding="utf-8")

    rc = runner._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))

    assert rc == 2
    sidecar = (
        reports / "testco.federal_opportunity_signals.internal.md"
    ).read_text(encoding="utf-8")
    assert "OFF_SCOPE: machine-composed acquisition_pathways row" in sidecar
    capsys.readouterr()


# ── crash-consistent pair publication (defect round 2) ──────────────────────

def test_interrupted_publication_leaves_only_a_detectable_mixed_pair(
        world, capsys, monkeypatch):
    """A hard interruption between the trail publish and the content
    commit leaves a mixed pair on disk; the pair-consumer seam rejects
    it through the trail's content binding, and a compose rerun repairs
    it into one bound generation."""
    import hashlib as _hashlib

    import run_compose
    from tools import atomic_io
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    content_path = world / "clients" / "testco" / "signal_board_content.json"
    trail_path = world / "clients" / "testco" / "compose_trail.md"
    old_content = content_path.read_bytes()

    real = atomic_io.atomic_write_text

    def crash_on_content(path, text):
        if os.path.basename(str(path)) == "signal_board_content.json":
            raise KeyboardInterrupt        # a crash, not a caught failure
        return real(path, text)
    monkeypatch.setattr(atomic_io, "atomic_write_text", crash_on_content)
    with pytest.raises(KeyboardInterrupt):
        # a genuinely different generation: fewer best-fit records
        run_compose.main(["--client", "testco", "--today", "2026-07-19",
                          "--best-fit", "1"])
    monkeypatch.setattr(atomic_io, "atomic_write_text", real)

    assert content_path.read_bytes() == old_content    # mixed: old content
    from agents.assessment_chain import _FileToken, validate_compose_pair
    stdout = f"[out:compose-trail] {trail_path}\n"
    with pytest.raises(ValueError, match="mixed"):
        validate_compose_pair(world, "testco", "Testco", stdout,
                              content_before=_FileToken(False),
                              trails_before={})

    # recovery: a rerun republishes both artifacts as one bound pair
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19",
                        "--best-fit", "1"]) == 0
    binding = trail_path.read_text(encoding="utf-8").rstrip() \
        .splitlines()[-1]
    assert binding == ("pair content sha256 "
                       + _hashlib.sha256(content_path.read_bytes())
                       .hexdigest())
    validate_compose_pair(world, "testco", "Testco", stdout,
                          content_before=_FileToken(False),
                          trails_before={})
    capsys.readouterr()


def test_restoration_failure_is_named_never_claimed_clean(
        world, capsys, monkeypatch):
    """When the primary publication fails AND restoring the prior pair
    also fails, the named failure says so; a broken rollback is never
    reported as a clean restoration."""
    import run_compose
    from tools import atomic_io
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    capsys.readouterr()
    real = atomic_io.atomic_write_text

    def fail_content(path, text):
        if os.path.basename(str(path)) == "signal_board_content.json":
            raise OSError("injected content-write failure")
        return real(path, text)
    monkeypatch.setattr(atomic_io, "atomic_write_text", fail_content)

    def broken_restore(_path, _blob):
        raise OSError("injected restore failure")
    monkeypatch.setattr(run_compose, "_restore", broken_restore)
    rc = run_compose.main(["--client", "testco", "--today", "2026-07-20"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "write-content"
    assert "cannot publish content" in err["reason"]
    assert "ALSO failed" in err["reason"]
    assert "mixed" in err["reason"]


def test_snapshot_distinguishes_absent_from_unreadable(world, capsys):
    """An absent prior artifact is a clean first publish; an
    existing-but-unreadable one refuses at the snapshot stage and is
    never deleted (deleting what could not be read is not
    restoration)."""
    trail_path = world / "clients" / "testco" / "compose_trail.md"
    assert not trail_path.exists()
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    os.chmod(trail_path, 0)
    try:
        rc = _cli(world, ["--client", "testco", "--today", "2026-07-20"])
    finally:
        os.chmod(trail_path, 0o644)
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "snapshot"
    assert "cannot be read" in err["reason"]
    assert trail_path.exists()


# ── review-round pins (independent verify, 2026-07-19) ──────────────────────

def test_teaming_scope_is_judged_per_edge(world):
    """Review-round pin: each subaward edge is judged under the scope law
    individually. An off-scope edge never inflates the edge count or
    rides an in-scope first edge, and an in-scope route survives
    whatever the edge sort order is."""
    from tools.relevance.scope import EngagementScope
    scope = EngagementScope(departments=["DHS"])
    sweep = _sweep()
    sweep["results"]["subawards"]["edges"]["541512"] = [
        {"prime": "BIG PRIME", "prime_award_id": "PA-0",
         "prime_award_generated_id": "CONT_AWD_PA0_9700_IDV0_9700",
         "subaward_id": "SUB-0",
         "source": "https://api.usaspending.gov/api/v2/subawards/SUB-0",
         "awarding_agency": "Department of Defense", "sub": "S1",
         "amount": 9.0,
         "description": "computer-aided dispatch off-scope route"},
        {"prime": "BIG PRIME", "prime_award_id": "PA-1",
         "prime_award_generated_id": "CONT_AWD_PA1_7001_IDV1_7001",
         "subaward_id": "SUB-1",
         "source": "https://api.usaspending.gov/api/v2/subawards/SUB-1",
         "awarding_agency": "Department of Homeland Security", "sub": "S2",
         "amount": 1.0,
         "description": "computer-aided dispatch integration subcontract"},
    ]
    inputs = composer.load_client_inputs("Testco")
    bf = composer.select_best_fit(sweep, inputs["taxonomy"], scope)
    comp = composer.select_competitors(sweep, today=TODAY, scope=scope)
    team = composer.select_teaming(sweep, bf, comp, scope=scope)
    prime = next(t for t in team if t.partner == "BIG PRIME")
    assert prime.edge_count == 1              # the DoD edge never counts
    assert all(e.get("awarding_agency")
               == "Department of Homeland Security" for e in prime.edges)
    assert prime.scope_ok is True
    assert prime.scope_basis == "in-scope:DHS"


def test_slug_preserving_display_rename_does_not_brick_compose(
        world, capsys):
    """Review-round pin: a prior board whose display name differs only in
    case (canonical identity unchanged) is this client's own artifact;
    compose replaces it instead of deadlocking at load-board."""
    board = world / "clients" / "testco" / "signal_board_content.json"
    board.write_text(json.dumps({"client_name": "TESTCO"}),
                     encoding="utf-8")
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    assert json.loads(board.read_text(encoding="utf-8"))["client_name"] \
        == "Testco"
    capsys.readouterr()


def test_horizon_scope_judges_the_full_record_not_the_display_string(
        world):
    """Review-round pin: scope judgment sees the whole record, so a row
    whose display agency cannot resolve but whose URL embeds an
    off-scope toptier generated id is excluded, never waved through as
    unresolved."""
    from tools.relevance.scope import EngagementScope
    scope = EngagementScope(departments=["DHS"])
    sweep = _sweep()
    sweep["results"]["contract_awards"]["recompetes"] = [
        {"awardee": "OLD CO", "piid": "PIID-9", "completion": "2026-08-30",
         "agency": "Bureau of Nowhere",
         "url": "https://www.usaspending.gov/award/CONT_AWD_XX_9700_YY_9700"}]
    horizon = composer.select_horizon(sweep, None, today=TODAY, scope=scope)
    assert not any(h.kind == "expiring" for h in horizon)


def test_press_sidecar_token_rides_the_board_slug_family():
    """Review-round pin: the refresh runner tokens the press INTERNAL
    sidecar with the board's own slug family (press_snapshot owner), not
    the canonical receipt slug; the two families diverge on punctuation
    runs and must not be conflated."""
    import run_refresh_press as refresh
    from agents.press_snapshot import client_slug
    # Consolidation (2026-08-19): the shared family is now the canonical
    # owner shape; the law under test (sidecar token and board artifacts
    # ride ONE family) is unchanged.
    assert refresh._press_artifact_slug("Booz Allen, Inc.") \
        == client_slug("Booz Allen, Inc.") == "booz_allen_inc"
    assert refresh._slug("Booz Allen, Inc.") == "booz_allen_inc"


# ── round 3: C3 validates compose success before downstream (defect 1) ──────

def test_refresh_stops_when_composer_exits_zero_without_publishing(
        world, capsys, monkeypatch):
    """Pinned: a fake composer that exits 0, writes nothing, and emits no
    marker cannot advance the refresh. One named compose-refresh failure
    is emitted and none of re-pull, press, or delta runs."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import run_refresh_press as refresh
    monkeypatch.setattr(refresh, "ROOT", str(world))
    commands: list = []
    persisted: list = []
    verified: list = []

    def execute(cmd, **_kwargs):
        commands.append(cmd)
        if "tools.relevance.calibrate" in cmd:
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w", encoding="utf-8") as f:
                f.write(CAL_HEADER
                        + "Testco,aaa1aaa1aaa1aaa1aaa1aaa1aaa1aaa1,7,,"
                          "preset:all_federal@v1\n")
            return SimpleNamespace(
                returncode=0,
                stdout="[calibrate] Testco: 1 records · scope "
                       "preset:all_federal@v1 · 0 FP · 0 FN · "
                       "0 out-of-scope\n",
                stderr="")
        # the fake composer: exit 0, no writes, no marker
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    code = refresh.run_refresh_press(
        "Testco", now=datetime(2026, 7, 19, tzinfo=timezone.utc),
        executor=execute,
        verifier=lambda *_a: verified.append(True),
        persister=lambda *_a, **_k: persisted.append(True))
    assert code == 2
    err = capsys.readouterr().err
    markers = [ln for ln in err.splitlines()
               if ln.startswith("[refresh:failure] ")]
    assert len(markers) == 1
    payload = json.loads(markers[0].split(" ", 1)[1])
    assert payload["stage"] == "compose-refresh"
    assert "without a valid published pair" in payload["reason"]
    assert "[out:compose-trail]" in payload["reason"]
    joined = [part for cmd in commands for part in cmd]
    assert not any("award_repull" in part for part in joined)
    assert not any("run_signal_board.py" in part for part in joined)
    assert verified == []
    assert persisted == []


# ── round 3: evidence bound to every machine card (defect 4) ────────────────

def test_machine_cards_carry_bound_evidence_matching_the_trail(
        world, capsys):
    """Every machine card in all four bands carries its own bound
    evidence (stable identity, kind, quote, agency, positive scope
    verdict + basis), and the INTERNAL trail binds one-to-one to the
    cards by stable identity."""
    rc = _cli(world, ["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    content = json.loads(
        (world / "clients" / "testco" / "signal_board_content.json")
        .read_text(encoding="utf-8"))
    for band in ("best_fit", "competitors", "teaming", "horizon"):
        assert content[band], band
        for card in content[band]:
            ev = card["machine_evidence"]
            assert ev["source_identity"], (band, card)
            assert ev["source_kind"], (band, card)
            assert ev["quote"], (band, card)
            assert ev["in_scope"] is True, (band, card)
            assert ev["scope_basis"], (band, card)
    # band-specific stable identities
    assert all(c["machine_evidence"]["source_kind"] == "sam.gov"
               for c in content["best_fit"])
    assert all(c["machine_evidence"]["source_identity"]
               == c["award_generated_id"] for c in content["competitors"])
    # Competitor cards render one row-level link; an unrendered nested
    # evidence link would overcount the occurrence manifest and block press.
    assert all("evidence" not in c for c in content["competitors"])
    assert all(t["machine_evidence"]["source_kind"]
               == "usaspending-subaward" for t in content["teaming"])
    sub = [t for t in content["teaming"]
           if t["machine_evidence"]["source_kind"] == "usaspending-subaward"]
    assert all(t["machine_evidence"]["source_identity"] ==
               "CONT_AWD_PA1_7001_IDV1_7001::subaward::SUB-1"
               and t["award_id"] == "PA-1" for t in sub)
    kinds = {c["machine_evidence"]["source_kind"]
             for c in content["horizon"]}
    assert kinds <= {
        "agency-forecast", "recompete-calendar", "contract-award",
        "usaspending-award",
    }
    forecast = [c for c in content["horizon"]
                if c["machine_evidence"]["source_kind"] == "agency-forecast"]
    assert all(c["machine_evidence"]["source_identity"] == c["forecast_id"]
               for c in forecast)
    assert all(c["machine_evidence"]["source_identity"] == c["apfs_id"]
               for c in forecast
               if c["machine_evidence"]["source_adapter"] == "dhs_apfs")
    assert all(c["job_context"]["text"] for c in content["horizon"])
    capsys.readouterr()


def test_teaming_angle_proof_identity_and_route_basis_are_tamper_evident(
        world):
    import copy

    sweep = _sweep()
    best_fit, competitors, teaming, horizon = _select_all(sweep)
    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, today=TODAY, offline=True,
        prose_trail=prose)
    trail = composer.build_trail(
        "Testco", best_fit=best_fit, competitors=competitors,
        teaming=teaming, horizon=horizon, prose_trail=prose, today=TODAY)
    composer.validate_machine_evidence(content, trail)

    changed_copy = copy.deepcopy(content)
    changed_copy["teaming"][0]["angle"] = "GENERIC PARTNER FIT"
    with pytest.raises(ValueError, match="changed after deterministic"):
        composer.validate_machine_evidence(changed_copy, trail)

    changed_basis = copy.deepcopy(content)
    changed_basis["teaming"][0]["machine_evidence"]["route_basis"][
        "shared_core_terms"] = ["fabricated capability"]
    with pytest.raises(ValueError, match="does not match its trail-bound"):
        composer.validate_machine_evidence(changed_basis, trail)

    changed_horizon = copy.deepcopy(content)
    changed_horizon["horizon"][0]["job_context"]["text"] = \
        "invented work description"
    with pytest.raises(ValueError, match="horizon card.*deterministic"):
        composer.validate_machine_evidence(changed_horizon, trail)


def test_trail_card_binding_rejects_extras_duplicates_and_mismatches(
        world):
    """Extra content cards, extra trail selections, duplicates, and
    identity mismatches all fail validation."""
    import copy

    sweep = _sweep()
    inputs = composer.load_client_inputs("Testco")
    bf = composer.select_best_fit(sweep, inputs["taxonomy"], inputs["scope"])
    comp = composer.select_competitors(sweep, today=TODAY)
    team = composer.select_teaming(sweep, bf, comp)
    hor = composer.select_horizon(sweep, None, today=TODAY)
    prose = []
    content = composer.compose_content(
        "Testco", sweep, best_fit=bf, competitors=comp, teaming=team,
        horizon=hor, today=TODAY, offline=True, prose_trail=prose)
    content = composer.build_hero(content, comp, team, sweep,
                                  client_name="Testco")
    trail = composer.build_trail(
        "Testco", best_fit=bf, competitors=comp, teaming=team, horizon=hor,
        prose_trail=prose, today=TODAY, scope=None)
    composer.validate_machine_evidence(content, trail)      # clean passes

    extra_card = copy.deepcopy(content)
    extra_card["competitors"].append(
        copy.deepcopy(content["competitors"][0]))
    with pytest.raises(ValueError, match="duplicate|one-to-one"):
        composer.validate_machine_evidence(extra_card, trail)

    fresh_extra = copy.deepcopy(content)
    injected = copy.deepcopy(content["competitors"][0])
    injected["machine_evidence"]["source_identity"] = "CONT_AWD_INJECTED"
    fresh_extra["competitors"].append(injected)
    with pytest.raises(ValueError, match="one-to-one"):
        composer.validate_machine_evidence(fresh_extra, trail)

    extra_trail = copy.deepcopy(trail)
    extra_trail["selections"]["horizon"].append(
        copy.deepcopy(extra_trail["selections"]["horizon"][0]))
    with pytest.raises(ValueError, match="duplicate|one-to-one"):
        composer.validate_machine_evidence(content, extra_trail)

    mismatched = copy.deepcopy(trail)
    mismatched["selections"]["best_fit"][0]["card_identity"] = "wrong-guid"
    with pytest.raises(ValueError, match="one-to-one"):
        composer.validate_machine_evidence(content, mismatched)

    stripped = copy.deepcopy(content)
    del stripped["teaming"][0]["machine_evidence"]
    with pytest.raises(ValueError, match="bound machine evidence"):
        composer.validate_machine_evidence(stripped, trail)


def test_press_rejects_machine_cards_without_bound_evidence(
        world, capsys, monkeypatch):
    """The press refuses machine cards with missing bound evidence, a
    blank agency, or an agency the engagement scope cannot resolve;
    current-source relevance fails named before any render sidecar, while
    'unresolved' from the generic engine is never machine-screened proof."""
    from types import SimpleNamespace

    import run_signal_board as runner
    reports = world / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "ROOT", str(world))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    assert _cli(world, ["--client", "testco", "--today", "2026-07-19"]) == 0
    board = world / "clients" / "testco" / "signal_board_content.json"
    content = json.loads(board.read_text(encoding="utf-8"))
    content["competitors"][0]["machine_evidence"]["quote"] = ""
    content["teaming"][0]["machine_evidence"]["agency"] = ""
    content["horizon"][0]["machine_evidence"]["agency"] = \
        "Bureau of Nowhere"
    _rewrite_bound_machine_board(world, content)
    (world / "clients" / "testco" / "engagement_scope.json").write_text(
        json.dumps({"departments": ["DHS"]}), encoding="utf-8")
    rc = runner._press(SimpleNamespace(
        client="Testco", render_date="2026-07-19", replay=True,
        out=None, offline=True))
    assert rc == 2
    assert not (reports / "testco.federal_opportunity_signals.internal.md") \
        .exists()
    err = capsys.readouterr().err
    assert "REFUSED: machine client relevance evidence failed" in err
    assert "current stored buyer map" in err


# ── round 3: competitor scope judged per source record (defect 5) ───────────

def test_competitor_scope_judges_each_child_record(world):
    """Pinned: three child-record conflicts under an otherwise in-scope
    buyer. Explicit off-scope agency text, an off-scope generated-id
    toptier, and off-scope URL evidence each exclude their own record;
    the buyer's verdict is never stamped on children, and the clean
    sibling still selects with the buyer's context filling its silence."""
    from tools.relevance.scope import EngagementScope
    scope = EngagementScope(departments=["DHS"])
    sweep = _sweep()
    sweep["results"]["incumbent_buyer_map"]["buyers"] = [{
        "buyer": "US IMMIGRATION AND CUSTOMS ENFORCEMENT",
        "agency": "Department of Homeland Security",
        "records": [
            {"kind": "award", "recipient": "CLEAN SIBLING",
             "amount": 4_000_000.0, "end_date": "2027-01-15",
             "award_id": "70CDCR25F0001",
             # toptier 0000 resolves nothing: the record alone is silent
             # and the buyer's agency context legitimately fills it
             "generated_internal_id": "CONT_AWD_70CDCR25F0001_0000_A_0000",
             "matched_terms": ["dispatch system"],
             "description": "dispatch platform support"},
            {"kind": "award", "recipient": "EXPLICIT OFF SCOPE",
             "amount": 50_000_000.0, "end_date": "2027-01-15",
             "award_id": "W91ZLK25F0001",
             "generated_internal_id": "CONT_AWD_W91ZLK25F0001_7012_B_7012",
             "agency": "Department of Defense",
             "matched_terms": ["dispatch system"],
             "description": "explicit off-scope agency text"},
            {"kind": "award", "recipient": "GID OFF SCOPE",
             "amount": 60_000_000.0, "end_date": "2027-01-15",
             "award_id": "W91ZLK25F0002",
             "generated_internal_id": "CONT_AWD_W91ZLK25F0002_9700_C_9700",
             "matched_terms": ["dispatch system"],
             "description": "off-scope generated-id toptier"},
            {"kind": "award", "recipient": "URL OFF SCOPE",
             "amount": 70_000_000.0, "end_date": "2027-01-15",
             "award_id": "W91ZLK25F0003",
             "generated_internal_id": "CONT_AWD_W91ZLK25F0003_7012_D_7012",
             "url": ("https://www.usaspending.gov/award/"
                     "CONT_AWD_W91ZLK25F0003_9700_D_9700"),
             "matched_terms": ["dispatch system"],
             "description": "off-scope URL evidence"},
        ]}]
    picks = composer.select_competitors(sweep, today=TODAY, scope=scope)
    assert [p.recipient for p in picks] == ["CLEAN SIBLING"]
    assert picks[0].scope_ok is True
    assert picks[0].scope_basis == "in-scope:DHS"
