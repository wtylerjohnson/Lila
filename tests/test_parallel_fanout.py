"""One run queries ALL sources at once; failures stay isolated."""
import json
import sys
from datetime import datetime, timezone
from unittest import mock

from agents.schemas import CapabilityProfile, ForecastRecord


def test_run_searches_parallel_fanout_collects_all_keys(tmp_path, monkeypatch):
    import run_searches as rs

    class FakeStrategy:
        inferred_naics = ["541512"]
        target_agencies = ["DISA"]
        set_aside_angles = []
        keywords = [type("K", (), {"term": '"threat intelligence"'})()]
        search_specs = []

    packet = mock.Mock(
        strategy=FakeStrategy(), revision_count=0,
        search_scope={"all": True}, is_approved=True)
    packet.scope_agencies.return_value = []
    # Both gate reads use one exact packet snapshot now; keep the fan-out test
    # isolated by supplying the same immutable snapshot on each zero-spend
    # check rather than reaching the filesystem.
    snapshot = mock.Mock(packet=packet, sha256="a" * 64)
    monkeypatch.setattr(rs, "load_packet_snapshot", lambda *a, **kw: snapshot)
    monkeypatch.setattr(rs, "_spec", lambda strategy, name: None)
    # the profile gate (no profile, no sweep) is under test elsewhere; this
    # test exercises the fan-out, so hand it a populated fake profile
    import tools.capability as cap
    monkeypatch.setattr(cap, "require_profile", lambda c: mock.Mock(
        capability_terms=mock.Mock(core=["x"], adjacent=[], excluded=[]),
        named_competitors_and_incumbents=[], mission_components=[]))

    # every adapter returns instantly with a marker; one always explodes
    class FakeOpp:
        def model_dump_json(self): return json.dumps({"ok": True})
    monkeypatch.setattr(rs, "SamGovSource", lambda: mock.Mock(search=lambda q: [FakeOpp()]))
    monkeypatch.setattr(rs, "UsaSpendingSource",
                        lambda: mock.Mock(market_evidence=lambda code, agency=None: {"naics": code}))
    monkeypatch.setattr(rs, "WebSearchSource", lambda: mock.Mock(search=lambda q: []))

    # THE STUB LIST BELOW IS AN ALLOWLIST, AND AN ALLOWLIST ROTS (2026-08-06).
    # Every adapter added to the fan-out after this test was written had no
    # line here and went LIVE: one run reached api.usaspending.gov,
    # api.fiscaldata.treasury.gov, api.www.sbir.gov, efts.sec.gov and
    # raw.githubusercontent.com, none of them named in the output. This test
    # is about the fan-out collecting every key, not about any adapter's wire
    # format, so close the seam rather than adding a stub and waiting for the
    # next adapter. One nobody stubbed now fails fast, and the fan-out's own
    # error handling carries it, which is the behaviour under test anyway.
    from tools.api import _http as _api_http

    def _no_network(*_a, **_kw):
        raise RuntimeError(
            "parallel fan-out test: an adapter reached the network")

    monkeypatch.setattr(_api_http, "_request", _no_network)
    monkeypatch.setattr(_api_http, "get_text", _no_network)

    import tools.api.federal_register as fr
    import tools.api.trade_rss as tr
    import tools.api.usaspending_subawards as sa
    import tools.api.contract_awards as ca
    import tools.api.cisa_kev as kv
    import tools.api.regulations_gov as rg
    import tools.api.darpa_opportunities as darpa
    import tools.api.dod_budget_exhibits as dod_budget
    import tools.api.dsip_topics as dsip
    import tools.api.foreign_assistance as foreign_assistance
    import tools.api.forecasts as forecasts
    import tools.api.gdelt as gdelt
    import tools.api.reginfo_unified_agenda as reginfo
    monkeypatch.setattr(fr.FederalRegisterSource, "enrich", lambda self, q: {"kw": [{"url": "u"}]})
    monkeypatch.setattr(tr.TradeRssSource, "enrich", lambda self, q: {"items": [], "errors": {}})
    monkeypatch.setattr(sa.SubawardsSource, "enrich", lambda self, q: {"primes": [], "sample": []})
    monkeypatch.setattr(ca.ContractAwardsSource, "enrich",
                        lambda self, q: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(kv.CisaKevSource, "enrich",
                        lambda self, q: {"recent_count": 1, "window_days": 45, "matched": []})
    monkeypatch.setattr(rg.RegulationsGovSource, "enrich", lambda self, q: {"kw": []})
    monkeypatch.setattr(
        gdelt.GdeltSource,
        "enrich",
        lambda self, q: {"_provenance": {
            "schema_version": 1,
            "source": "gdelt",
            "status": "complete",
            "mode": "recorded-test",
            "retrieval_mode": "stored",
        }},
    )
    monkeypatch.setattr(forecasts, "forecast_sources", lambda: [])

    def program_payload(source):
        return {
            "records": [],
            "_provenance": {
                "source": source,
                "status": "success",
                "mode": "recorded-test",
                "retrieved_at": "2026-07-20T00:00:00+00:00",
                "records_received": 0,
            },
        }

    monkeypatch.setattr(
        reginfo.RegInfoUnifiedAgendaSource, "enrich",
        lambda self, q: program_payload("reginfo_unified_agenda"))
    monkeypatch.setattr(
        dsip.DsipTopicsSource, "enrich",
        lambda self, q: program_payload("dsip_topics"))
    monkeypatch.setattr(
        darpa.DarpaOpportunitiesSource, "enrich",
        lambda self, q: program_payload("darpa_opportunities"))
    monkeypatch.setattr(
        dod_budget.DodBudgetExhibitsSource, "enrich",
        lambda self, q: program_payload("dod_budget_exhibits"))
    monkeypatch.setattr(
        foreign_assistance.ForeignAssistanceSource, "enrich",
        lambda self, q: program_payload("foreign_assistance"))

    monkeypatch.setattr(rs, "REPORT_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "argv", ["run_searches.py", "--client", "Testco"])
    # redirect output dir by chdir-ing data writes: patch os.path.dirname target
    monkeypatch.setattr(rs.os.path, "dirname", lambda p: str(tmp_path))

    rc = rs.main()
    assert rc == 0
    out = json.load(open(tmp_path / "data" / "cleaned" / "searches_testco.json"))
    results = out["results"]
    for key in ["sam.gov", "usaspending.gov", "web", "federal_register", "news",
                "subawards", "contract_awards", "cisa_kev", "regulations_gov",
                "dsip_topics", "reginfo_unified_agenda", "darpa_opportunities",
                "dod_budget_exhibits", "foreign_assistance"]:
        assert key in results, key
    # the exploding source is isolated as an error, everything else is data
    assert results["contract_awards"] == {"error": "boom"}
    assert results["cisa_kev"]["recent_count"] == 1


def test_empty_forecast_family_distinguishes_disabled_from_failed():
    import run_searches as rs

    disabled = rs._empty_forecast_family_result(
        [{"source": "dhs_apfs", "status": "not-run"}], ["dhs_apfs"])
    assert disabled == ({
        "disabled": True, "sources_off": ["dhs_apfs"],
    }, "OFF by configuration (all forecast adapters disabled)")

    failed = rs._empty_forecast_family_result([
        {"source": "dhs_apfs", "status": "not-run"},
        {"source": "acquisition_gateway", "status": "failed",
         "error": "private upstream detail"},
    ], ["dhs_apfs"])
    assert failed is not None
    payload, summary = failed
    assert payload["disabled"] is False
    assert payload["error"] == "All enabled agency forecast adapters failed"
    assert payload["_provenance"]["status"] == "failed"
    assert summary == payload["error"]


def test_empty_forecast_family_allows_successful_zero_row_screen():
    import run_searches as rs

    assert rs._empty_forecast_family_result([
        {"source": "dhs_apfs", "status": "not-run"},
        {"source": "acquisition_gateway", "status": "success", "count": 0},
    ], ["dhs_apfs"]) is None


def test_partial_forecast_census_cannot_advance_change_store():
    import run_searches as rs

    assert rs._forecast_snapshot_advances_change_store({"complete": True}) is True
    assert rs._forecast_snapshot_advances_change_store({}) is True
    assert rs._forecast_snapshot_advances_change_store({"complete": False}) is False


def test_partial_forecast_family_cannot_advance_client_snapshot():
    import run_searches as rs
    from tools.api.source_catalog import source_spec

    complete = [
        {"source": source, "status": "success"}
        for source in source_spec("forecasts").coverage_origins
    ]
    assert rs._forecast_family_advances_client_snapshot(complete) is True
    for status in ("partial", "failed", "not-run"):
        attempts = [dict(row) for row in complete]
        attempts[1]["status"] = status
        assert rs._forecast_family_advances_client_snapshot(attempts) is False
    stale = [dict(row) for row in complete]
    stale[2]["stale"] = True
    assert rs._forecast_family_advances_client_snapshot(stale) is False
    assert rs._forecast_family_advances_client_snapshot(complete[:-1]) is False
    assert rs._forecast_family_advances_client_snapshot([]) is False


def test_forecast_contract_requires_every_declared_official_origin():
    import run_searches as rs
    from tools.api.source_catalog import source_spec

    attempts = [
        {"source": source, "status": "success"}
        for source in source_spec("forecasts").coverage_origins
    ]

    receipt = rs._forecast_contract_receipt(attempts)

    assert receipt["coverage_class"] == "required-bounded"
    assert receipt["complete_within_boundary"] is True
    assert receipt["incomplete_origins"] == []
    assert "DHS APFS" in receipt["boundary"]

    attempts[4]["status"] = "not-run"
    incomplete = rs._forecast_contract_receipt(attempts)
    assert incomplete["complete_within_boundary"] is False
    assert incomplete["incomplete_origins"] == ["dhs_apfs"]


def test_forecast_source_summary_preserves_exact_and_landing_routes():
    import run_searches as rs

    record = mock.Mock(data_as_of="2026-07")
    row = rs._forecast_source_summary(
        "army_acquisition_forecast",
        [record],
        [record, record],
        {
            "source_url": "https://example.gov/exact-army.xlsx",
            "landing_url": "https://www.army.mil/osbp",
            "mode": "stale_official_cache",
            "complete": True,
            "retrieved_at": "2026-08-14T12:00:00+00:00",
            "public_detail": "Official Army workbook",
            "limitations": ["Published row boundary."],
            "limitation": "Published row boundary.",
        },
        contract_origins={"army_acquisition_forecast"},
    )

    assert row["source_url"] == "https://example.gov/exact-army.xlsx"
    assert row["landing_url"] == row["url"] == "https://www.army.mil/osbp"
    assert row["coverage_boundary"].startswith("Official Army OSBP")
    assert row["limitations"] == ["Published row boundary."]
    assert row["public_detail"] == "Official Army workbook"
    assert row["contract_origin"] is True
    assert row["stale"] is True
    assert row["available_count"] == 2


def _forecast_record(source: str, source_id: str, title: str) -> ForecastRecord:
    return ForecastRecord(
        source=source,
        source_id=source_id,
        agency="Department of Homeland Security",
        title=title,
        description="Enterprise network switching modernization.",
        naics_code="541512",
        url=f"https://example.gov/{source_id}",
        retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )


def _collect_child(source):
    import run_searches as rs

    return rs._collect_forecast_child(
        source,
        profile=CapabilityProfile(
            client_name="Arista Networks",
            naics_codes=["541512"],
        ),
        capability_terms=["network switch"],
        taxonomy=None,
        engagement_scope=None,
        scope_agencies=[],
    )


def test_lazy_forecast_timeout_is_a_child_failure_not_a_family_exception():
    class LazyTimeoutSource:
        name = "dhs_apfs"

        def forecasts(self):
            def rows():
                raise OSError(60, "Operation timed out")
                yield  # pragma: no cover - makes this a lazy iterator

            return rows()

    failed = _collect_child(LazyTimeoutSource())

    assert failed["status"] == "failed"
    assert failed["rows"] == []
    assert failed["market_rows"] == []
    assert "Operation timed out" in failed["error"]
    assert failed["provenance"]["complete"] is False


def test_optional_forecast_detail_timeout_retains_rows_and_marks_partial():
    record = _forecast_record(
        "acquisition_gateway",
        "GW-NETWORK",
        "Enterprise network switch refresh",
    )

    class DetailTimeoutSource:
        name = "acquisition_gateway"
        last_provenance = {
            "status": "complete",
            "complete": True,
            "mode": "live_api",
        }

        def forecasts(self):
            return [record]

        def enrich_matched(self, _records):
            raise OSError(60, "Operation timed out")

    partial = _collect_child(DetailTimeoutSource())

    assert partial["status"] == "partial"
    assert partial["rows"] == [record]
    assert partial["market_rows"] == [record]
    assert "Operation timed out" in partial["error"]
    assert partial["provenance"]["complete"] is False
    assert any(
        "detail enrichment failed" in limitation
        for limitation in partial["provenance"]["limitations"]
    )


def test_one_forecast_child_timeout_keeps_sibling_evidence_family_partial():
    import run_searches as rs
    from tools.api.source_catalog import source_spec

    good_record = _forecast_record(
        "army_acquisition_forecast",
        "ARMY-NETWORK",
        "Tactical network switch refresh",
    )

    class GoodSource:
        name = "army_acquisition_forecast"
        last_provenance = {"status": "complete", "complete": True}

        def forecasts(self):
            return [good_record]

    class FailedSource:
        name = "dhs_apfs"

        def forecasts(self):
            raise OSError(60, "Operation timed out")

    good = _collect_child(GoodSource())
    failed = _collect_child(FailedSource())
    attempts = [
        {"source": origin, "status": "success"}
        for origin in source_spec("forecasts").coverage_origins
    ]
    next(
        row for row in attempts if row["source"] == failed["provenance"].get(
            "source", "dhs_apfs"
        )
    )["status"] = "failed"

    contract = rs._forecast_contract_receipt(attempts)

    assert good["status"] == "success"
    assert good["rows"] == [good_record]
    assert failed["status"] == "failed"
    assert contract["complete_within_boundary"] is False
    assert contract["incomplete_origins"] == ["dhs_apfs"]
