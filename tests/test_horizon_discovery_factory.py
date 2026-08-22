"""Official-source Horizon factory: fixtures only, no network or live flow."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from agents.assess.contracts import (
    AssessScope,
    EvidenceKind,
    EvidenceTier,
    EvidenceUse,
    LiveSolicitation,
    ScopeAgency,
    ScopeMode,
)
from agents.assess.ledger import adapt_horizon_ledger
from agents.reports.capture_brief import _horizon_section
from agents.reports.horizon import (
    HorizonItem,
    HorizonSet,
    HorizonSignal,
    build_fact_bank,
    compose_horizon,
    validate_horizon,
)
from agents.reports.horizon_discovery import (
    REGISTRY,
    pull_official_horizon_sources,
)
from agents.reports.lint import (
    lint_forecast_context,
    lint_horizon,
    lint_notice_tier_claims,
)
from agents.schemas import RawOpportunity
from tools.api.base import DataSource

NOW = "2026-07-11T12:00:00+00:00"
BINDING = {
    "version": 1,
    "scope_designator": "all",
    "sweep_artifact": "searches_testco.json",
    "sweep_sha256": "sweep-a",
    "profile_sha256": "profile-a",
}


@pytest.fixture
def official_results() -> dict:
    return {
        "federal_register": {
            "packet capture": [{
                "title": "Network Evidence Retention Rule",
                "type": "Proposed Rule",
                "publication_date": "2026-07-01",
                "url": "https://www.federalregister.gov/d/2026-10001",
                "agencies": ["Department of Homeland Security"],
            }],
        },
        "regulations_gov": {
            "packet capture": [{
                "id": "DHS-2026-0042-0001",
                "title": "Network Evidence Retention Requirements",
                "type": "Proposed Rule",
                "posted": "2026-07-02",
                "docket": "DHS-2026-0042",
                "comment_open": True,
                "comment_ends": "2026-08-15",
                "url": (
                    "https://www.regulations.gov/document/"
                    "DHS-2026-0042-0001"
                ),
                "agency": "DHS",
            }],
        },
        "watchdogs": {
            "items": [{
                "source": "GAO",
                "title": "Federal Network Visibility Gaps",
                "body": "GAO found incomplete packet evidence retention.",
                "published": "2026-07-03",
                "matched": ["packet capture"],
                "url": "https://www.gao.gov/products/gao-26-1001",
                "agency": "Department of Homeland Security",
            }],
            "total_reports": 40,
        },
        "cisa_kev": {
            "recent_count": 14,
            "window_days": 45,
            "total": 1400,
            "matched": [{
                "cve": "CVE-2026-1000",
                "vendor": "PacketCo",
                "product": "Network Sensor",
                "added": "2026-07-04",
                "ransomware": "Known",
            }],
            "catalog_url": (
                "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
            ),
        },
        "budget_pressure": {
            "rows": [{
                "agency": "Department of Homeland Security",
                "fiscal_year": 2026,
                "budgetary_resources": 575.9e9,
                "obligated": 310.7e9,
                "unobligated": 265.2e9,
                "pct_obligated": 54.0,
                "url": (
                    "https://api.usaspending.gov/api/v2/agency/070/"
                    "budgetary_resources/"
                ),
            }],
        },
        "forecast_signals": {
            "total_records": 80,
            "matched": [],
            "events": [{
                "kind": "date_moved_closer",
                "title": "CISA network visibility platform",
                "detail": "solicitation timing moved closer: Q4 2027 -> Q2 2027",
                "url": "https://apfs-cloud.dhs.gov/forecast/9001",
                "agency": "Department of Homeland Security",
                "component": "CISA",
            }, {
                "kind": "disappeared",
                "title": "CISA packet evidence repository",
                "detail": (
                    "forecast line no longer published, often means it went to "
                    "solicitation"
                ),
                "url": "https://apfs-cloud.dhs.gov/forecast/9002",
                "agency": "Department of Homeland Security",
                "component": "CISA",
            }],
        },
        "dsip_topics": {
            "records": [{
                "record_id": "dsip:3f4ca360f47545da8558a294ea8dc36a_86472",
                "canonical_url": (
                    "https://www.dodsbirsttr.mil/topics/api/public/topics/"
                    "3f4ca360f47545da8558a294ea8dc36a_86472/details"
                ),
                "kind": "defense_sbir_sttr_topic",
                "signal_type": "small_business_innovation_topic",
                "agency": "Department of Defense",
                "component": "ARMY",
                "status": "Pre-Release",
                "title": "xTech|Phantum Competition",
                "topic_code": "ARM26BX01-NP003",
                "open_date": "2026-08-10",
                "close_date": "2026-08-28",
                "matched_terms": ["quantum sensors"],
                "topic_managers": [{
                    "name": "xTech Team",
                    "role": "TPOC",
                    "email": "official-contact@army.mil",
                }],
                "direct_contact_permitted": True,
                "promotion_eligible": False,
                "live_solicitation": False,
                "source": "dsip_topics",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "2026-07-20",
            }],
        },
        "reginfo_unified_agenda": {
            "records": [{
                "record_id": "reginfo:0704-AI01:202510",
                "canonical_url": (
                    "https://www.reginfo.gov/public/do/eAgendaViewRule?"
                    "RIN=0704-AI01&pubId=202510"
                ),
                "kind": "regulation",
                "rin": "0704-AI01",
                "title": "Zero Trust Cybersecurity Reporting Requirements",
                "agency": "Department of Defense",
                "component": "Office of the Chief Information Officer",
                "stage": "Proposed Rule Stage",
                "timetable": [{"action": "NPRM", "date": "11/00/2026"}],
                "source": "reginfo_unified_agenda",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "2026-07-03",
            }],
        },
        "darpa_opportunities": {
            "records": [{
                "record_id": "darpa:5240",
                "canonical_url": (
                    "https://www.darpa.mil/research/programs/perrseus"
                ),
                "kind": "agency_announcement",
                "signal_type": "research_program",
                "title": "PERRSEUS",
                "agency": "Defense Advanced Research Projects Agency",
                "summary": (
                    "DARPA seeks compact cold-atom devices for resilient "
                    "sensing."
                ),
                "published_at": "2026-07-16T15:22:22+00:00",
                "source": "darpa_opportunities",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "2026-07-16",
            }],
        },
        "dod_budget_exhibits": {
            "records": [{
                "record_id": (
                    "dod-budget:pacific_deterrence_initiative:"
                    "PDI_FundTypeByCate:PDITab4Loe:PDITYP_Loe1"
                ),
                "canonical_url": (
                    "https://comptroller.war.gov/Portals/45/Documents/"
                    "defbudget/FY2027/"
                    "FY2027_Pacific_Deterrence_Initiative.json"
                ),
                "kind": "budget",
                "title": "Resilient communications and sensing",
                "agency": "Department of Defense",
                "initiative": "pacific_deterrence_initiative",
                "budget_year": "2027",
                "values": {"TOTAL": 2_906_393},
                "source": "dod_budget_exhibits",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "2026-04",
            }],
        },
        "foreign_assistance": {
            "records": [{
                "record_id": "foreign-assistance:102",
                "canonical_url": "https://foreignassistance.gov/data",
                "kind": "budget",
                "title": (
                    "Cybersecurity Capacity Building — Kenya — "
                    "President's Budget Request"
                ),
                "agency": "U.S. Government Foreign Assistance",
                "fiscal_year": "2024",
                "current_amount": 2_500_000,
                "source": "foreign_assistance",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "FY2024",
            }],
        },
        "grants_gov": {
            "records": [{
                "record_id": "grants_gov:358800",
                "canonical_url": (
                    "https://www.grants.gov/search-results-detail/358800"
                ),
                "kind": "assistance_opportunity",
                "title": "State cyber resilience grant",
                "number": "DHS-26-CYB-001",
                "agency": "Department of Homeland Security",
                "status": "forecasted",
                "open_date": "2026-09-01",
                "close_date": "2026-11-15",
                "source": "grants_gov",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "2026-07-20",
            }],
        },
        "sbir_gov": {
            "records": [{
                "record_id": "sbir_gov:SBIR-26-1:A-1",
                "canonical_url": "https://www.sbir.gov/topics/9001",
                "kind": "small_business_innovation_topic",
                "title": "Responder network telemetry",
                "number": "SBIR-26-1",
                "agency": "Department of Homeland Security",
                "objective": (
                    "Network telemetry for civilian emergency responders."
                ),
                "matched_terms": ["network telemetry"],
                "open_date": "2026-07-21",
                "close_date": "2026-10-01",
                "source": "sbir_gov",
                "tier": "program",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "data_as_of": "2026-07-20",
            }],
        },
    }


EXPECTED = {
    "budget_pressure": "budget_line",
    "cisa_kev": "cisa_kev",
    "darpa_opportunities": "agency_announcement",
    "dsip_topics": "agency_announcement",
    "dod_budget_exhibits": "budget_line",
    "federal_register": "regulatory",
    "foreign_assistance": "budget_line",
    "grants_gov": "agency_announcement",
    "forecast_store_changes": "forecast_delta",
    "reginfo_unified_agenda": "regulatory",
    "regulations_gov": "regulatory",
    "sbir_gov": "agency_announcement",
    "watchdogs": "oversight",
}

FACTORY_ORDER = (
    "budget_pressure",
    "cisa_kev",
    "federal_register",
    "forecast_store_changes",
    "regulations_gov",
    "watchdogs",
    "dsip_topics",
    "reginfo_unified_agenda",
    "darpa_opportunities",
    "dod_budget_exhibits",
    "foreign_assistance",
    "grants_gov",
    "sbir_gov",
)

STORED_PROGRAM_SOURCES = FACTORY_ORDER[-7:]


def test_one_registered_signals_only_adapter_per_official_source():
    assert {adapter.name for adapter in REGISTRY.all()} == set(EXPECTED)
    assert tuple(adapter.name for adapter in REGISTRY.all()) == FACTORY_ORDER
    for adapter in REGISTRY.all():
        assert not isinstance(adapter, DataSource)
        assert not hasattr(adapter, "search")


@pytest.mark.parametrize("source_name", sorted(EXPECTED))
def test_each_adapter_maps_fixture_without_recording_a_fresh_success(
    source_name, official_results, monkeypatch,
):
    from tools.api.forecasts import coverage

    calls: list[tuple] = []
    monkeypatch.setattr(coverage, "record_pull", lambda *args: calls.append(args))
    adapter = REGISTRY.get(source_name)
    rows = adapter.pull(official_results)

    assert rows
    assert {row["kind"] for row in rows} == {EXPECTED[source_name]}
    assert all(row["tier"] == "program" for row in rows)
    assert all(row["scope"]["kind"] in {"agency", "government_wide"}
               for row in rows)
    assert all(not isinstance(row, RawOpportunity) for row in rows)
    assert all(not isinstance(row, LiveSolicitation) for row in rows)
    for row in rows:
        parsed = urlparse(row["source"])
        assert parsed.scheme == "https"
        assert (parsed.hostname or "").endswith((".gov", ".mil"))

    assert calls == []


@pytest.mark.parametrize("source_name", STORED_PROGRAM_SOURCES)
def test_stored_program_mapper_preserves_exact_official_identity_and_freshness(
    source_name, official_results,
):
    original = official_results[source_name]["records"][0]
    row = REGISTRY.get(source_name).pull(official_results)[0]

    assert row["source"] == original["canonical_url"]
    assert row["source_record_id"] == original["record_id"]
    assert row["retrieved_at"] == original["retrieved_at"]
    assert row["data_as_of"] == original["data_as_of"]
    assert row["scope"]["agencies"] == [original["agency"]]
    assert row["tier"] == "program"


@pytest.mark.parametrize("source_name", STORED_PROGRAM_SOURCES)
def test_stored_program_mapper_rejects_malformed_and_non_gov_rows(
    source_name, official_results,
):
    valid = official_results[source_name]["records"][0]
    bad_rows = [
        {**valid, "record_id": ""},
        {**valid, "canonical_url": "https://example.com/not-official"},
        {**valid, "retrieved_at": "not-a-time"},
        {**valid, "retrieved_at": "2026-07-20T12:00:00"},
        {**valid, "data_as_of": ""},
        {**valid, "agency": ""},
        {**valid, "tier": "notice"},
        {**valid, "source": "different_source"},
        "not-an-object",
    ]
    payload = deepcopy(official_results[source_name])
    payload["records"] = [*bad_rows, valid]

    rows = REGISTRY.get(source_name).pull({source_name: payload})

    assert len(rows) == 1
    assert rows[0]["source_record_id"] == valid["record_id"]
    payload["records"] = {"malformed": True}
    assert REGISTRY.get(source_name).pull({source_name: payload}) == []


def test_stored_program_rows_join_fact_bank_in_stable_order(official_results):
    stored = {key: official_results[key] for key in STORED_PROGRAM_SOURCES}

    rows = build_fact_bank(stored, retrieved_at=NOW)

    assert [row["source_record_id"] for row in rows] == [
        stored[key]["records"][0]["record_id"]
        for key in STORED_PROGRAM_SOURCES
    ]
    assert [row["id"] for row in rows] == [
        "H1", "H2", "H3", "H4", "H5", "H6", "H7",
    ]
    assert all(row["tier"] == "program" for row in rows)


def test_dsip_direct_contact_metadata_is_never_labeled_as_solicitation_poc(
    official_results,
):
    original = official_results["dsip_topics"]["records"][0]
    assert original["direct_contact_permitted"] is True

    row = REGISTRY.get("dsip_topics").pull(official_results)[0]

    assert row["kind"] == "agency_announcement"
    assert "PRE-RELEASE" in row["text"].upper()
    assert original["topic_code"] in row["text"]
    assert original["open_date"] in row["text"]
    assert original["close_date"] in row["text"]
    assert original["matched_terms"][0] in row["text"]
    assert "SOLICITATION CONTACT" not in row["text"].upper()
    assert "POC" not in row["text"].upper()
    assert "topic_managers" not in row
    assert original["topic_managers"][0]["email"] not in row["text"]


def test_absent_or_error_payload_never_stomps_live_pull_coverage(
    monkeypatch,
):
    from tools.api.forecasts.coverage import record_pull

    record_pull("watchdogs", "government-wide", 9, True, "healthy pull")
    path = Path(os.environ["LILA_FORECAST_COVERAGE"])
    before = path.read_bytes()

    rows = pull_official_horizon_sources({})
    assert all(not value for value in rows.values())

    adapter = REGISTRY.get("watchdogs")
    assert adapter.pull({"watchdogs": {"error": "feeds unavailable"}}) == []
    assert path.read_bytes() == before


def test_one_mapper_exception_does_not_sink_other_sources(
    official_results, monkeypatch, caplog,
):
    adapter = REGISTRY.get("watchdogs")

    def _fail(_payload):
        raise ValueError("bad watchdog envelope")

    monkeypatch.setattr(adapter, "map_payload", _fail)
    rows = pull_official_horizon_sources(official_results)

    assert rows["watchdogs"] == []
    assert rows["federal_register"]
    assert "Horizon mapper watchdogs rejected" in caplog.text


def test_recomposing_stored_sweep_does_not_refresh_success_coverage(
    official_results,
):
    from tools.api.forecasts.coverage import record_pull

    record_pull(
        "federal_register", "government-wide", 17, True,
        "real collection attempt",
    )
    path = Path(os.environ["LILA_FORECAST_COVERAGE"])
    before = path.read_bytes()

    pull_official_horizon_sources(official_results)
    pull_official_horizon_sources(official_results)

    assert path.read_bytes() == before


def test_forecast_change_event_survives_legacy_payload_without_screen_count(
    official_results,
):
    forecast = dict(official_results["forecast_signals"])
    forecast.pop("total_records")
    bank = build_fact_bank({"forecast_signals": forecast}, retrieved_at=NOW)
    rows = [row for row in bank if row["kind"] == "forecast_delta"]
    assert len(rows) == 2
    assert all(row["tier"] == "program" for row in rows)


def _item_for(fact: dict, *, confidence: str = "moderate") -> HorizonItem:
    return HorizonItem(
        id="official-program-signal",
        title="Federal network evidence demand",
        where="Department of Homeland Security / CISA",
        verified=[HorizonSignal(
            evidence_id=fact["id"], text=fact["text"], source=fact["source"]
        )],
        pattern="The official program signal identifies a developing requirement.",
        projection="We judge a positioning window may form at CISA.",
        window="next planning cycle",
        watching="source status changes; reviewed weekly",
        confidence=confidence,
        lifecycle_stage="early_signal",
        falsifier="The source withdraws the stated program requirement.",
        watch_trigger="source status changes",
        monitoring_cadence="reviewed weekly",
    )


def test_horizon_compose_consumes_enriched_bank_without_gate_change(
    official_results,
):
    class FixtureEngine:
        fact_bank: list[dict] = []

        def deliberate(self, *, context, **_kwargs):
            self.fact_bank = context["fact_bank"]
            fact = next(row for row in self.fact_bank
                        if row["kind"] == "cisa_kev")
            return HorizonSet(
                client_name=context["client_name"],
                items=[_item_for(fact)],
                method_note="Official program sources screened.",
            )

    engine = FixtureEngine()
    hset, bank, problems = compose_horizon(
        "Testco", official_results, engine=engine, retrieved_at=NOW,
    )
    assert problems == []
    assert bank == engine.fact_bank
    assert set(EXPECTED.values()) <= {row["kind"] for row in bank}
    assert all(row["tier"] == "program" for row in bank
               if row["kind"] in set(EXPECTED.values()))
    assert validate_horizon(
        hset, bank, schema_version=2, expected_client_name="Testco"
    ) == []

    html = _horizon_section(json.loads(hset.model_dump_json()))
    assert lint_horizon(html).ok
    assert lint_forecast_context(html).ok
    assert lint_notice_tier_claims(html).ok

    disappeared = next(row for row in bank
                       if "no longer present" in row["text"])
    assert "went to solicitation" not in disappeared["text"].lower()
    assert "posted" not in disappeared["text"].lower()


@pytest.mark.parametrize("source_name", sorted(EXPECTED))
def test_every_factory_source_projects_as_program_evidence(
    source_name, official_results,
):
    adapter = REGISTRY.get(source_name)
    confidence = "early" if source_name == "forecast_store_changes" else "moderate"
    for index, row in enumerate(adapter.pull(official_results), start=1):
        fact = {"id": "H1", **row, "retrieved_at": NOW}
        hset = HorizonSet(
            client_name="Testco",
            items=[_item_for(fact, confidence=confidence)],
            method_note="Official program source screened.",
        )
        payload = {
            "client": "Testco",
            "status": "approved",
            "schema_version": 2,
            "generated_at": NOW,
            "approved_at": NOW,
            "approved_by": "operator",
            "binding": dict(BINDING),
            "fact_bank": [fact],
            "set": json.loads(hset.model_dump_json()),
            "rounds": [],
        }

        ledger, diagnostics = adapt_horizon_ledger(
            payload,
            BINDING,
            run_id=f"run-{source_name}-{index}",
            client_name="Testco",
            scope=AssessScope(),
            profile_version="profile-v1",
            as_of=datetime.fromisoformat(NOW).astimezone(timezone.utc),
        )
        assert diagnostics == []
        assert len(ledger.items) == 1
        evidence = ledger.items[0].evidence[0]
        assert evidence.tier == EvidenceTier.PROGRAM
        if source_name == "budget_pressure":
            assert evidence.kind == EvidenceKind.BUDGET
        if source_name == "cisa_kev":
            assert evidence.kind == EvidenceKind.AGENCY_ANNOUNCEMENT
            assert set(evidence.supports) == {EvidenceUse.TIMING}


def test_regulations_buyer_scope_survives_focused_dhs_projection(
    official_results,
):
    row = REGISTRY.get("regulations_gov").pull(official_results)[0]
    assert row["scope"] == {"kind": "agency", "agencies": ["DHS"]}
    fact = {"id": "H1", **row, "retrieved_at": NOW}
    hset = HorizonSet(
        client_name="Testco",
        items=[_item_for(fact)],
        method_note="Official program source screened.",
    )
    focus_binding = dict(BINDING, scope_designator="agency_dhs")
    payload = {
        "client": "Testco",
        "status": "approved",
        "schema_version": 2,
        "generated_at": NOW,
        "approved_at": NOW,
        "approved_by": "operator",
        "binding": focus_binding,
        "fact_bank": [fact],
        "set": json.loads(hset.model_dump_json()),
        "rounds": [],
    }
    focused = AssessScope(
        mode=ScopeMode.AGENCY,
        agencies=(ScopeAgency(
            name="Department of Homeland Security", abbr="DHS",
        ),),
    )

    ledger, diagnostics = adapt_horizon_ledger(
        payload,
        focus_binding,
        run_id="run-regulations-focused",
        client_name="Testco",
        scope=focused,
        profile_version="profile-v1",
        as_of=datetime.fromisoformat(NOW).astimezone(timezone.utc),
    )

    assert diagnostics == []
    assert len(ledger.items) == 1
    assert ledger.items[0].evidence[0].tier == EvidenceTier.PROGRAM


def test_cisa_publisher_is_not_laundered_into_focused_buyer_scope(
    official_results,
):
    row = REGISTRY.get("cisa_kev").pull(official_results)[0]
    assert row["scope"] == {"kind": "government_wide"}
    fact = {"id": "H1", **row, "retrieved_at": NOW}
    hset = HorizonSet(
        client_name="Testco",
        items=[_item_for(fact)],
        method_note="Official program source screened.",
    )
    payload = {
        "client": "Testco",
        "status": "approved",
        "schema_version": 2,
        "generated_at": NOW,
        "approved_at": NOW,
        "approved_by": "operator",
        "binding": dict(BINDING),
        "fact_bank": [fact],
        "set": json.loads(hset.model_dump_json()),
        "rounds": [],
    }
    focus_binding = dict(BINDING, scope_designator="agency_dhs")
    payload["binding"] = focus_binding
    focused = AssessScope(
        mode=ScopeMode.AGENCY,
        agencies=(ScopeAgency(
            name="Department of Homeland Security", abbr="DHS"
        ),),
    )

    ledger, diagnostics = adapt_horizon_ledger(
        payload,
        focus_binding,
        run_id="run-cisa-focused",
        client_name="Testco",
        scope=focused,
        profile_version="profile-v1",
        as_of=datetime.fromisoformat(NOW).astimezone(timezone.utc),
    )
    assert ledger.items == ()
    assert any("government-wide" in problem for problem in diagnostics)


def test_legacy_budget_pressure_artifact_keeps_market_tier():
    fact = {
        "id": "H1",
        "text": (
            "Department of Homeland Security FY2026 budget execution: "
            "$575.9B resources"
        ),
        "source": (
            "https://api.usaspending.gov/api/v2/agency/070/"
            "budgetary_resources/"
        ),
        "kind": "budget_pressure",
        "scope": {
            "kind": "agency",
            "agencies": ["Department of Homeland Security"],
        },
        "retrieved_at": NOW,
    }
    hset = HorizonSet(
        client_name="Testco",
        items=[_item_for(fact)],
        method_note="Legacy budget source screened.",
    )
    payload = {
        "client": "Testco",
        "status": "approved",
        "schema_version": 2,
        "generated_at": NOW,
        "approved_at": NOW,
        "approved_by": "operator",
        "binding": dict(BINDING),
        "fact_bank": [fact],
        "set": json.loads(hset.model_dump_json()),
        "rounds": [],
    }
    ledger, diagnostics = adapt_horizon_ledger(
        payload,
        BINDING,
        run_id="run-legacy-budget",
        client_name="Testco",
        scope=AssessScope(),
        profile_version="profile-v1",
        as_of=datetime.fromisoformat(NOW).astimezone(timezone.utc),
    )
    assert diagnostics == []
    assert ledger.items[0].evidence[0].tier == EvidenceTier.MARKET
