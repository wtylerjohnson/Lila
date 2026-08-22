"""L15/L16: forecast ingestion tier discipline + the recompete calendar.

Offline — fixture records, env-isolated stores (conftest), no network, no LLM.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.schemas import CapabilityProfile, ForecastRecord  # noqa: E402
from agents.reports.lint import (  # noqa: E402
    lint_forecast_context, lint_notice_tier_claims,
)
from tools.api.forecasts.matching import bucket_forecasts, screen_line  # noqa: E402
from tools.api.forecasts.store import (  # noqa: E402
    stated_value_bounds, timing_ordinal, upsert, value_rank,
)

NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)


def _apfs_style(sid="26-001", title="Network visibility modernization",
                naics="541512", timing="2026-11-15", value="$5M to $10M"):
    """Records as the DHS APFS mapper emits them."""
    return ForecastRecord(
        source="dhs_apfs", source_id=sid, agency="DHS", component="CBP",
        title=title, description="packet capture and network monitoring",
        naics_code=naics, estimated_value_range=value,
        anticipated_solicitation=timing, set_aside="None",
        url=f"https://apfs-cloud.dhs.gov/forecast/{sid}", retrieved_at=NOW)


def _gateway_style(sid="AG-77", title="Enterprise SOC tooling refresh"):
    """Records as the Acquisition Gateway mapper emits them: quarter timing,
    incumbent stated, psc carried — a DIFFERENT source shape, same schema."""
    return ForecastRecord(
        source="acquisition_gateway", source_id=sid, agency="GSA",
        component="FAS", title=title,
        description="security operations center threat monitoring platform",
        naics_code="541519", psc="DA01", incumbent_stated="Booz Allen Hamilton",
        estimated_value_range="$10M to $20M",
        anticipated_solicitation="Q2 2027", set_aside="None",
        url="https://acquisitiongateway.gov/forecast", retrieved_at=NOW)


PROFILE = CapabilityProfile(
    client_name="Testco", naics_codes=["541512"],
    set_aside_eligibility=[],
    past_performance_keywords=["network monitoring", "packet capture"])


# ---- 1. normalization from two different source formats ---------------------
def test_two_source_formats_normalize_to_one_schema():
    a, g = _apfs_style(), _gateway_style()
    for rec in (a, g):
        assert rec.source_id and rec.title and rec.url
        assert rec.agency and rec.anticipated_solicitation
    # source-specific shapes carried into the shared schema
    assert g.psc == "DA01" and g.incumbent_stated == "Booz Allen Hamilton"
    assert a.component == "CBP"
    # timing normalizes comparably across date and fiscal-quarter forms
    assert timing_ordinal(a.anticipated_solicitation) is not None
    assert timing_ordinal(g.anticipated_solicitation) is not None
    assert value_rank(a.estimated_value_range) == 5e6
    assert value_rank(g.estimated_value_range) == 10e6


def test_source_stated_value_bounds_never_fill_an_unstated_side():
    assert stated_value_bounds("$100M - $249M") == {
        "estimated_value_lower": 100_000_000.0,
        "estimated_value_upper": 249_000_000.0,
    }
    assert stated_value_bounds("$1B to $1.9B") == {
        "estimated_value_lower": 1_000_000_000.0,
        "estimated_value_upper": 1_900_000_000.0,
    }
    assert stated_value_bounds("Below $150K") == {
        "estimated_value_upper": 150_000.0,
    }
    assert stated_value_bounds("Over $5B") == {
        "estimated_value_lower": 5_000_000_000.0,
    }
    assert stated_value_bounds("> $25K and < $250K") == {
        "estimated_value_lower": 25_000.0,
        "estimated_value_upper": 250_000.0,
    }
    assert stated_value_bounds(">= $100M") == {
        "estimated_value_lower": 100_000_000.0,
    }
    assert stated_value_bounds("$2,500,000") == {
        "estimated_value_lower": 2_500_000.0,
        "estimated_value_upper": 2_500_000.0,
    }
    assert stated_value_bounds("To Be Determined") == {}
    assert stated_value_bounds("approximately $5M") == {}
    assert stated_value_bounds("5 to 10") == {}
    assert stated_value_bounds("$10M to $5M") == {}


# ---- 2. change detection: moved date, grown value, disappearance ------------
def test_change_detection_signals():
    upsert("dhs_apfs", [_apfs_style(timing="2027-03-01")], today="2026-07-01")
    # date moves CLOSER + value band grows -> two signal events
    _, ev = upsert("dhs_apfs",
                   [_apfs_style(timing="2026-11-15", value="$10M to $20M")],
                   today="2026-07-10")
    kinds = {e["kind"] for e in ev}
    assert "date_moved_closer" in kinds and "value_grew" in kinds
    # the record vanishes -> disappeared (often means it went to solicitation)
    _, ev2 = upsert("dhs_apfs", [], today="2026-07-17")
    assert [e["kind"] for e in ev2] == ["disappeared"]
    # first/last seen maintained across pulls
    _, _ = upsert("dhs_apfs", [_apfs_style()], today="2026-07-24")
    from tools.api.forecasts.store import load_store
    row = load_store("dhs_apfs")["26-001"]
    assert row["first_seen"] == "2026-07-01"
    assert row["last_seen"] == "2026-07-24"
    assert row["record_hash"]
    assert row["estimated_value_range"] == "$5M to $10M"
    assert row["estimated_value_lower"] == 5_000_000.0
    assert row["estimated_value_upper"] == 10_000_000.0


def test_retired_market_forecast_writer_refuses_without_mutation(
        monkeypatch, capsys):
    import run_market_refresh

    artifact = {"results": {"forecast_signals": {
        "matched": [{"source": "acquisition_gateway", "source_id": "A-1"}],
        "_provenance": {"mode": "multi-source-forecast"},
    }}}
    before = json.loads(json.dumps(artifact))
    failure = run_market_refresh._refresh_forecasts(
        artifact, "Test Co", {"strategy": {}})

    assert artifact == before
    assert failure["stage"] == "forecasts"
    assert "canonical multi-source forecast envelope" in failure["reason"]
    assert failure["fix_surface"] == (
        "python3 run_searches.py --client 'Test Co' --skip web triage picture "
        "--preserve-skipped"
    )

    monkeypatch.setattr(sys, "argv", [
        "run_market_refresh.py", "--client", "Test Co", "--forecasts",
        "--subawards",
    ])
    assert run_market_refresh.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == failure


# ---- 3. capability-match bucket assignment ----------------------------------
def test_bucket_assignment_and_screen_line():
    recs = [
        _apfs_style(sid="C1"),                                    # capability
        _apfs_style(sid="L1", title="Janitorial support",
                    naics="541512"),                              # lane only
        _apfs_style(sid="N1", title="Janitorial support",
                    naics="722310"),                              # no match
    ]
    recs[1].description = "custodial services for the facility"
    recs[2].description = "custodial services for the facility"
    b = bucket_forecasts(recs, PROFILE,
                         keywords=["network monitoring", "packet capture"])
    assert [x["record"].source_id for x in b["capability"]] == ["C1"]
    assert [r.source_id for r in b["lane_only"]] == ["L1"]
    assert b["no_match_count"] == 1
    line = screen_line(b, len(recs), "agency", gaps=["DoD"])
    assert "Screened 3 agency forecast records" in line
    assert "1 matched the client's capability vocabulary" in line
    assert "2 carried a client-lane NAICS code" in line
    assert "1 NAICS-lane-only" in line
    assert "Not screened: DoD" in line


def test_screen_line_does_not_count_capability_only_as_naics_lane():
    capability_only = _apfs_style(
        sid="C0", title="Network monitoring", naics=""
    )
    buckets = bucket_forecasts(
        [capability_only], PROFILE, keywords=["network monitoring"]
    )

    line = screen_line(buckets, 1, "agency")

    assert "1 matched the client's capability vocabulary" in line
    assert "0 carried a client-lane NAICS code" in line
    assert "clearing the NAICS lane" not in line


# ---- 4. lint: forecast in a live-opportunity section fails ------------------
def test_forecast_outside_signals_section_fails_lint():
    live = ('<section class="v-section" id="opportunities"><h2>Section 01</h2>'
            '<div data-forecast="1">CBP Network visibility modernization</div>'
            '</section>')
    res = lint_forecast_context(live)
    assert not res.ok
    assert res.violations[0].rule == "forecast_outside_signals"
    ok = ('<section class="early-signals"><h2>What is forming</h2>'
          '<div data-forecast="1">CBP Network visibility modernization</div>'
          '</section>')
    assert lint_forecast_context(ok).ok


# ---- 5. tier lint: "recompete posted" needs a verified notice ID ------------
def test_recompete_posted_claim_needs_notice_id():
    bad = "<p>The CBP recompete has posted and the window is open.</p>"
    res = lint_notice_tier_claims(bad)
    assert not res.ok and res.violations[0].rule == "unverified_notice_claim"
    ok = ("<p>The CBP recompete has posted as notice 70RSAT26R00000012; "
          "the window is open.</p>")
    assert lint_notice_tier_claims(ok).ok
    # expiry math phrasing never trips it
    assert lint_notice_tier_claims(
        "<p>The award ends 2026-09-30; a recompete window is forming.</p>").ok


# ---- recompete calendar ------------------------------------------------------

def _profile():
    from tools.capability import CapabilityTerms, ClientProfile
    return ClientProfile(
        client_name="Testco",
        capability_terms=CapabilityTerms(core=["network monitoring"]),
        named_competitors_and_incumbents=["Testco", "Gigamon", "Riverbed"],
        mission_components=["CBP"],
        naics_boundary=["541512"])


def _seed_xwalk():
    d = os.environ["LILA_ENTITIES_DIR"]
    os.makedirs(d, exist_ok=True)
    xwalk = {"entities": [
        {"canonical_id": "gigamon", "canonical_name": "Gigamon",
         "aliases": [], "ueis": [], "notes": "one door"},
        {"canonical_id": "peraton", "canonical_name": "Peraton",
         "aliases": [{"name": "Perspecta", "type": "acquisition",
                      "effective_date": "2021-05-06",
                      "source_url": "https://example.test/p"},
                     {"name": "Perspecta Enterprise Solutions",
                      "type": "subsidiary", "effective_date": "2021-05-06",
                      "source_url": "https://example.test/p"}],
         "ueis": [], "notes": "Perspecta acquired by Peraton, May 2021"}]}
    with open(os.path.join(d, "crosswalk.json"), "w") as f:
        json.dump(xwalk, f)


def _award(recipient, end_days, amount=1_000_000.0, award_type="Definitive Contract",
           desc="network monitoring platform sustainment", office="CBP"):
    return {"Award ID": f"A-{recipient[:4]}-{end_days}",
            "Recipient Name": recipient,
            "Award Amount": amount, "Awarding Agency": "DHS",
            "Awarding Sub Agency": office, "Start Date": "2023-01-01",
            "End Date": (date.today() + timedelta(days=end_days)).isoformat(),
            "Description": desc, "Contract Award Type": award_type,
            "NAICS": "541512", "PSC": "DA01",
            "generated_internal_id": f"CONT_{recipient[:4]}_{end_days}"}


def test_calendar_scoring_dollars_crosswalk_defend(tmp_path):
    _seed_xwalk()
    from tools.api.recompete import _row, score_calendar
    rows = [
        _row(_award("GIGAMON INC.", 120), "541512"),          # near, competitor
        _row(_award("PERSPECTA ENTERPRISE SOLUTIONS LLC", 400,
                    desc="enterprise IT services help desk"), "541512"),
        _row(_award("TESTCO LLC", 200), "541512"),            # the client: DEFEND
    ]
    # crosswalk resolution of incumbent names (legacy name -> door)
    assert rows[0]["recipient"] == "Gigamon"
    assert rows[1]["recipient"] == "Peraton"
    assert rows[1]["recipient_door"] == "peraton"
    # dollar labeling: obligated is labeled, ceiling absent is labeled
    assert "obligated" in rows[0]["obligated_label"]
    assert rows[0]["potential_ceiling"] is None
    assert "not fetched" in rows[0]["ceiling_note"]

    scored = score_calendar(rows, _profile(),
                            buyer_agencies={"CBP"},
                            weights={"capability_match": 30,
                                     "product_competitor_incumbent": 25,
                                     "expiry_within_9_months": 15,
                                     "buyer_map_agency": 15,
                                     "single_award_vehicle": 15})
    # DEFEND segregation: the client's own paper never enters attack
    assert [d["recipient_raw"] for d in scored["defend"]] == ["TESTCO LLC"]
    assert all(r["recipient_raw"] != "TESTCO LLC" for r in scored["attack"])
    assert "renewal defense" in scored["defend"][0]["defend_note"]

    top = scored["attack"][0]
    assert top["recipient"] == "Gigamon"
    # score decomposition sums to the score, every factor cited
    assert top["score"] == sum(d["points"] for d in top["score_decomposition"])
    assert top["score"] == 100          # all five factors hit
    factors = {d["factor"] for d in top["score_decomposition"]}
    assert factors == {"capability_match", "product_competitor_incumbent",
                       "expiry_within_9_months", "buyer_map_agency",
                       "single_award_vehicle"}
    assert all(d["evidence"] for d in top["score_decomposition"])
    vehicle = next(
        d for d in top["score_decomposition"]
        if d["factor"] == "single_award_vehicle"
    )
    assert "account-planning route" in vehicle["evidence"]
    assert "recompete" not in vehicle["evidence"].lower()
    # Peraton row: no capability match, expiry beyond 9 months
    per = next(r for r in scored["attack"] if r["recipient"] == "Peraton")
    per_factors = {d["factor"] for d in per["score_decomposition"]}
    assert "capability_match" not in per_factors
    assert "expiry_within_9_months" not in per_factors
    assert per["score"] == sum(d["points"] for d in per["score_decomposition"])


def test_calendar_facts_feed_program_tier(tmp_path):
    """Calendar rows stay PROGRAM-tier award-period signals, not recompetes."""
    _seed_xwalk()
    from tools.api.recompete import _row, score_calendar, _store_dir
    from agents.reports.facts import _Counter, _recompete_facts
    rows = [_row(_award("GIGAMON INC.", 120, amount=2_500_000.0), "541512")]
    scored = score_calendar(rows, _profile(), buyer_agencies={"CBP"})
    os.makedirs(_store_dir(), exist_ok=True)
    with open(_store_dir() / "testco.json", "w") as f:
        json.dump({"client": "Testco", "attack": scored["attack"],
                   "defend": [], "population": 1,
                   "generated": date.today().isoformat()}, f)
    facts = _recompete_facts({}, _Counter(), _profile())
    cal_facts = [
        f for f in facts
        if f.text.startswith("AWARD-PERIOD CALENDAR SIGNAL")
    ]
    assert len(cal_facts) == 1
    f0 = cal_facts[0]
    assert f0.tier == "program"
    assert "obligated to date" in f0.text          # never product spend
    assert "$2,500,000" in f0.text
    assert "account relevance" in f0.text and "/100" in f0.text
    assert "not a confirmed recompete" in f0.text
    assert lint_notice_tier_claims(f"<p>{f0.text}</p>").ok
