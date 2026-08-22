"""Forecast layer — recorded APFS fixture, no live calls.

Fixture shape recorded 2026-07-05 from the live public API
(apfs-cloud.dhs.gov/api/forecast/?format=json&page=1); see docs/API_SETUP.md.
"""

from datetime import date


import tools.api.forecasts.dhs_apfs as apfs
from agents.reports.lint import lint_forecast_context
from agents.schemas import CapabilityProfile, ForecastRecord
from tools.api import REGISTRY
from tools.api.forecasts import (
    diff_snapshots, load_latest_snapshot, record_payload, save_snapshot,
)
from tools.api.forecasts.dhs_apfs import DhsApfsSource, map_record
from tools.api.forecasts.matching import match_forecasts
from tools.api.forecasts.matching import bucket_forecasts
from tools.relevance.taxonomy import (
    CapabilityTaxonomy,
    CodeUniverse,
    TaxonomyTerm,
)

# ── recorded fixture (field names verified against the live API) ──
FIXTURE = [
    {
        "id": 9001, "apfs_number": "APFS-2026-9001",
        "requirements_title": "Cyber Threat Intelligence Platform Subscription",
        "requirement": "Enterprise threat intelligence platform with dark web coverage for CISA analysts.",
        "naics": "541512 - Computer Systems Design Services",
        "organization": "CISA",
        "dollar_range": {"display_name": "$5M to $10M", "display_order": 7},
        "award_quarter": "Q3 2026", "fiscal_year": 2026,
        "estimated_solicitation_release_date": "Q2 2026",
        "anticipated_award_date": None,
        "small_business_set_aside": None,
        "small_business_program": {"display_name": "Full and Open"},
        "sbs_coordinator_first_name": "Pat", "sbs_coordinator_last_name": "Lee",
        "sbs_coordinator_email": "pat.lee@hq.dhs.gov",
        "contract_type": {"display_name": "FFP"}, "contract_vehicle": None,
        "current_state": "Published", "published_date": "2026-06-15",
    },
    {
        "id": 9002, "apfs_number": "APFS-2026-9002",
        "requirements_title": "Building 4 Plumbing Retrofit",
        "requirement": "Plumbing and HVAC contractor services.",
        "naics": "238220 - Plumbing, Heating, and Air-Conditioning Contractors",
        "organization": "CBP",
        "dollar_range": {"display_name": "$500K to $1M", "display_order": 4},
        "award_quarter": "Q4 2026", "fiscal_year": 2026,
        "estimated_solicitation_release_date": "Q3 2026",
        "small_business_set_aside": {"display_name": "8(a) Sole Source"},
        "current_state": "Published",
    },
]

PROFILE = CapabilityProfile(
    client_name="Recorded Future", naics_codes=["541512", "513210"],
    set_aside_eligibility=[], past_performance_keywords=["threat intelligence"])


def _records():
    return [map_record(r) for r in FIXTURE]


# ── registration + posture ──

def test_registered_and_enabled_by_default_with_explicit_kill_switch(monkeypatch):
    src = REGISTRY.get("dhs_apfs")
    assert src is not None and src.kind.value == "discovery"
    monkeypatch.delenv("LILA_ENABLE_DHS_APFS", raising=False)
    assert DhsApfsSource().enabled is True
    monkeypatch.setenv("LILA_ENABLE_DHS_APFS", "off")
    disabled = DhsApfsSource()
    assert disabled.enabled is False
    ok, detail = disabled.healthcheck()
    assert ok and "explicitly disabled" in detail


def test_search_never_yields_opportunities():
    from tools.api.base import SourceQuery
    assert DhsApfsSource().search(SourceQuery(keywords=["cyber"])) == []


# ── record mapping ──

def test_map_record_full_fidelity():
    r = map_record(FIXTURE[0])
    assert isinstance(r, ForecastRecord)
    assert r.source == "dhs_apfs" and r.source_id == "APFS-2026-9001"
    assert r.agency == "DHS" and r.component == "CISA"
    assert r.naics_code == "541512" and "Computer Systems" in r.naics_label
    assert r.estimated_value_range == "$5M to $10M"
    assert r.anticipated_solicitation == "Q2 2026"
    assert r.award_type == "FFP"
    assert r.small_business_poc == "Pat Lee <pat.lee@hq.dhs.gov>"
    # APFS per-record links: /forecast/<id> returns 404 and
    # /record/<id>/public-print/ returns 200, verified live 2026-07-27.
    assert r.url == "https://apfs-cloud.dhs.gov/record/9001/public-print/"
    assert r.forecast_status == "Published"
    assert r.retrieved_at is not None
    r2 = map_record(FIXTURE[1])
    assert r2.set_aside == "8(a) Sole Source"


def test_record_payload_preserves_stated_band_and_derives_numeric_bounds():
    payload = record_payload(map_record(FIXTURE[0]))

    assert payload["estimated_value_range"] == "$5M to $10M"
    assert payload["estimated_value_lower"] == 5_000_000.0
    assert payload["estimated_value_upper"] == 10_000_000.0


# ── matching: signals only, same gates as the live path ──

def test_matching_routes_to_signals_only():
    matched = match_forecasts(_records(), PROFILE, keywords=["threat intelligence"])
    assert len(matched) == 1                          # plumbing gated out (NAICS + set-aside)
    m = matched[0]
    assert isinstance(m["record"], ForecastRecord)    # never a RawOpportunity
    assert "exact NAICS 541512" in m["reasons"]
    assert any("threat intelligence" in r for r in m["reasons"])
    assert m["score"] > 0.5


def test_matching_requires_positive_evidence():
    # NAICS family alone (no exact, no keyword) is not a signal
    vague = map_record({**FIXTURE[0], "naics": "541519 - Other Computer Related Services",
                        "requirements_title": "General IT support", "requirement": ""})
    assert match_forecasts([vague], PROFILE, keywords=["quantum"]) == []


def test_taxonomy_matching_promotes_capability_not_naics_only():
    direct = map_record({
        **FIXTURE[0],
        "requirements_title": "Enterprise Network Switches",
        "requirement": "High-speed network switches for the data center.",
    })
    lane_only = map_record({
        **FIXTURE[0],
        "id": 9003,
        "apfs_number": "APFS-2026-9003",
        "requirements_title": "General IT Support",
        "requirement": "Program support services.",
    })
    taxonomy = CapabilityTaxonomy(
        client_name="Arista Networks",
        version=1,
        updated="2026-08-14",
        core=[TaxonomyTerm(term="network switch", mode="stemmed")],
        code_universe=CodeUniverse(naics=["541512"]),
    )

    buckets = bucket_forecasts(
        [direct, lane_only], PROFILE, taxonomy=taxonomy)

    assert [row["record"].source_id for row in buckets["capability"]] == [
        "APFS-2026-9001"
    ]
    assert buckets["capability"][0]["score"] == 3
    assert buckets["capability"][0]["keyword_hits"] == ["network switch"]
    assert [row.source_id for row in buckets["lane_only"]] == [
        "APFS-2026-9003"
    ]


def test_taxonomy_matches_are_score_ordered_with_a_deterministic_tie_break():
    low_z = map_record({
        **FIXTURE[0],
        "id": 9010,
        "apfs_number": "APFS-2026-9010",
        "requirements_title": "Network Switch",
        "requirement": "Network switch deployment.",
    })
    high = map_record({
        **FIXTURE[0],
        "id": 9005,
        "apfs_number": "APFS-2026-9005",
        "requirements_title": "Network Switch and CloudVision",
        "requirement": "Network switch deployment with CloudVision.",
    })
    low_a = map_record({
        **FIXTURE[0],
        "id": 9000,
        "apfs_number": "APFS-2026-9000",
        "requirements_title": "Network Switch",
        "requirement": "Network switch deployment.",
    })
    taxonomy = CapabilityTaxonomy(
        client_name="Arista Networks",
        version=1,
        updated="2026-08-14",
        core=[
            TaxonomyTerm(term="network switch", mode="stemmed"),
            TaxonomyTerm(term="CloudVision", mode="stemmed"),
        ],
    )

    matched = match_forecasts(
        [low_z, high, low_a], PROFILE, taxonomy=taxonomy
    )

    assert [row["record"].source_id for row in matched] == [
        "APFS-2026-9005",
        "APFS-2026-9000",
        "APFS-2026-9010",
    ]
    assert [row["score"] for row in matched] == [6, 3, 3]


def test_set_aside_gate_respects_eligibility():
    profile_8a = PROFILE.model_copy(update={"set_aside_eligibility": ["8(a)"],
                                            "naics_codes": ["238220"],
                                            "past_performance_keywords": ["plumbing"]})
    matched = match_forecasts(_records(), profile_8a, keywords=["plumbing"])
    assert [m["record"].source_id for m in matched] == ["APFS-2026-9002"]


# ── rendering + lint enforcement ──

def test_forecast_renders_only_in_early_signals_and_labeled():
    import sys
    sys.path.insert(0, "tests")
    from test_capture_brief import _content
    from agents.reports.capture_brief import render_capture_brief
    matched = match_forecasts(_records(), PROFILE, keywords=["threat intelligence"])
    import json
    sig = [json.loads(m["record"].model_dump_json()) | {"reasons": m["reasons"]} for m in matched]
    html = render_capture_brief(_content(), forecasts=sig,
                                forecast_delta={"moved": [{"title": "X", "from": "Q2 2026",
                                                           "to": "Q4 2026", "id": "1", "url": "u"}],
                                                "new": [], "disappeared": []})
    assert 'section class="early-signals"' in html
    assert "agency-stated intent (forecast), not a live opportunity" in html.lower()
    assert 'data-forecast="1"' in html
    assert "Q2 2026" in html and "apfs-cloud.dhs.gov/record/9001/public-print/" in html
    assert "Forecast movement since last pull" in html
    assert lint_forecast_context(html).ok            # rendered inside the wrapper
    # and a brief with NO forecasts carries no signals section at all
    assert 'data-forecast' not in render_capture_brief(_content())


def test_stale_anticipated_dates_carry_disposition_and_sort_last():
    """2026-07-09 review: five of ten Stated Intent lines were already past
    their anticipated date with no status note. A past-dated line must never
    read as forward pipeline: it carries an explicit disposition and sorts
    below every forward-dated line."""
    from datetime import date as _date
    from agents.reports.capture_brief import _early_signals_section
    sig = [
        {"title": "Already Passed", "anticipated_solicitation": "06/01/2026",
         "url": "https://x/1", "component": "CBP", "reasons": ["keywords: aviation"]},
        {"title": "Still Ahead", "anticipated_solicitation": "09/15/2026",
         "url": "https://x/2", "component": "TSA", "reasons": ["keywords: aviation"]},
        {"title": "No Date Stated", "anticipated_solicitation": None,
         "url": "https://x/3", "component": "OIT", "reasons": ["keywords: aviation"]},
    ]
    html = _early_signals_section(sig, as_of=_date(2026, 7, 9))
    assert "date passed, unconfirmed" in html
    assert "treat as slipped until re-verified" in html
    # exactly one disposition (only the past-dated line), and order is
    # forward -> undated -> past
    assert html.count("date passed") == 1
    ahead, undated, passed = (html.index("Still Ahead"),
                              html.index("No Date Stated"),
                              html.index("Already Passed"))
    assert ahead < undated < passed


def test_lane_only_matches_never_render_as_stated_intent():
    """2026-07-09 review, round two: NAICS-only matches (Oracle maintenance in
    541519) rendered as 'stated intent' for an aviation-risk client. A lane
    screen is not a relevance screen: rendered rows require a capability-
    keyword hit; lane-only lines feed the screen count. Zero relevant lines
    renders the honest-absence finding, never NAICS noise."""
    from datetime import date as _date
    from agents.reports.capture_brief import _early_signals_section
    lane_only = [
        {"title": "Oracle Hardware/Software Maintenance", "reasons": ["exact NAICS 541519"],
         "anticipated_solicitation": "08/01/2026", "url": "https://x/1", "component": "CBP/OIT"},
        {"title": "Land & Mobile Radio Test Equipment", "reasons": ["exact NAICS 541519"],
         "anticipated_solicitation": "07/08/2026", "url": "https://x/2", "component": "CBP/OIT"},
    ]
    html = _early_signals_section(lane_only, as_of=_date(2026, 7, 9))
    assert "Oracle" not in html and "Radio" not in html      # noise never renders
    assert "none states intent matching the capability" in html
    assert "2\n  published planning lines" in html or "2 published planning lines" in html.replace("\n  ", " ")
    assert "outside the published forecast" in html          # the finding, stated

    # one relevant + one lane-only: relevant renders, lane-only counted not shown
    mixed = lane_only + [{"title": "Aviation Risk Data Subscription",
                          "reasons": ["exact NAICS 541519", "keywords: aviation risk"],
                          "anticipated_solicitation": "09/01/2026",
                          "url": "https://x/3", "component": "CBP/AMO"}]
    html = _early_signals_section(mixed, as_of=_date(2026, 7, 9))
    assert "Aviation Risk Data Subscription" in html
    assert "Oracle" not in html
    assert "2 further planning lines cleared the NAICS-lane screen" in html


def test_lint_fails_forecast_outside_signals_context():
    bad = ('<section><h2>Live Opportunities</h2>'
           '<tr data-forecast="1"><td>sneaky forecast</td></tr></section>')
    res = lint_forecast_context(bad)
    assert not res.ok and res.violations[0].rule == "forecast_outside_signals"
    good = ('<section class="early-signals"><tr data-forecast="1"></tr></section>')
    assert lint_forecast_context(good).ok
    mixed = good + '<div data-forecast="1">stray</div>'
    assert not lint_forecast_context(mixed).ok


# ── snapshots + deltas: the revision is the signal ──

def test_snapshot_roundtrip_and_diff():
    recs = _records()
    save_snapshot("testco", recs, as_of=date(2026, 4, 1))
    prev = load_latest_snapshot("testco", before=date(2026, 7, 5))
    assert prev is not None and len(prev) == 2
    assert prev[0]["estimated_value_range"] == "$5M to $10M"
    assert prev[0]["estimated_value_lower"] == 5_000_000.0
    assert prev[0]["estimated_value_upper"] == 10_000_000.0

    # Q2->Q4 slip + one line disappears + one new line
    import json
    moved = map_record({**FIXTURE[0], "estimated_solicitation_release_date": "Q4 2026"})
    newline = map_record({**FIXTURE[1], "id": 9003, "apfs_number": "APFS-2026-9003",
                          "requirements_title": "SOC Modernization"})
    cur = [json.loads(moved.model_dump_json()), json.loads(newline.model_dump_json())]
    delta = diff_snapshots(prev, cur)
    assert [m["id"] for m in delta["new"]] == ["APFS-2026-9003"]
    assert [m["id"] for m in delta["disappeared"]] == ["APFS-2026-9002"]
    assert delta["moved"][0]["from"] == "Q2 2026" and delta["moved"][0]["to"] == "Q4 2026"


def test_fetch_all_uses_daily_cache(monkeypatch):
    calls = []

    def fake_get(url, *, params, headers, timeout, retries):
        calls.append(params["page"])
        return FIXTURE if params["page"] == 1 else []

    monkeypatch.setattr(apfs, "get_json", fake_get)
    monkeypatch.setattr(apfs.time, "sleep", lambda s: None)
    src = DhsApfsSource()
    assert len(src.forecasts()) == 2
    n_calls = len(calls)
    assert len(src.forecasts()) == 2      # second pull: cache, zero HTTP
    assert len(calls) == n_calls
