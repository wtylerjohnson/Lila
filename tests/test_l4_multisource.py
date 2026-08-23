"""The forecast lane reads every adapter it fetches from.

AUDIT FINDING 2026-07-30 (the forecast C+): run_l4 hardwired DHS APFS while
the Acquisition Gateway census - 7,644 records across Interior (3,155),
USDA (2,519), VA (699), DOT (683), GSA, Labor, NRC, NSF - was fetched,
cached, and never read by the press. 90.7% of the forecast records on disk
were invisible to every report.

Pinned here: both adapters feed one scored lane; each adapter gets its own
LaneQuery row with real fetched/kept counts; a disabled adapter is a named
row, never a silent absence; the lane is degraded only when EVERY adapter is
off; and the coverage ledger claims what the runner EFFECTIVELY does, not
what ship-time defaults said.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import retrieval  # noqa: E402
from agents.schemas import ForecastRecord  # noqa: E402


def _forecast(source, source_id, agency, title, naics="541512"):
    from datetime import datetime, timezone

    return ForecastRecord(
        source=source, source_id=source_id, agency=agency, title=title,
        description=f"{title} for enterprise network monitoring",
        naics_code=naics, estimated_value_range="$5M to $10M",
        anticipated_solicitation="Q2 2027", url=f"https://x.gov/{source_id}",
        retrieved_at=datetime(2026, 7, 30, tzinfo=timezone.utc))


class _Src:
    def __init__(self, name, records, enabled=True):
        self.name = name
        self.enabled = enabled
        self.offline_safe = True
        self._records = records

    def forecasts(self):
        return self._records


class _FailingSrc(_Src):
    def forecasts(self):
        raise RuntimeError("fixture outage")


class _ProvenanceFailingSrc(_Src):
    def forecasts(self):
        self.last_provenance = {"status": "failed", "records": 0}
        return []


def _wire(monkeypatch, dhs, gateway, army=None):
    import tools.api.forecasts as forecasts

    sources = [dhs, gateway] + ([army] if army is not None else [])
    monkeypatch.setattr(forecasts, "forecast_sources", lambda: sources)


VOCAB = ["network monitoring", "network visibility"]


def test_both_adapters_feed_one_scored_lane(monkeypatch):
    dhs = _Src("dhs_apfs",
               [_forecast("dhs_apfs", "APFS-1", "DHS",
                          "Network monitoring modernization")])
    gw = _Src("acquisition_gateway",
              [_forecast("acquisition_gateway", "GW-1", "Interior",
                         "Enterprise network monitoring services"),
               _forecast("acquisition_gateway", "GW-2", "USDA",
                         "Farm loan servicing desk", naics="522310")])
    _wire(monkeypatch, dhs, gw)
    records, queries = retrieval.run_l4(VOCAB, ["541512"])
    agencies = {r.agency for r in records}
    assert "DHS" in agencies and "Interior" in agencies
    assert "USDA" not in agencies            # screened out, not carried
    by = {q.method: q for q in queries}
    assert by["dhs_apfs"].result_count == 1
    assert by["dhs_apfs"].kept_after_screen == 1
    assert by["acquisition_gateway"].result_count == 2
    assert by["acquisition_gateway"].kept_after_screen == 1


def test_army_child_source_feeds_forecast_lane_with_exact_evidence(monkeypatch):
    from datetime import datetime, timezone

    record = ForecastRecord(
        source="army_acquisition_forecast",
        source_id="PANMCC-26-P-0000 041442",
        agency="Department of the Army",
        component="TRADOC",
        title="FY26 DCSIT POM WLAN LCR and Support",
        description="Network support; Command: TRADOC; Contracting office: FDO EUSTIS",
        naics_code="541512",
        psc="7A20",
        estimated_value_range="$250K to $1M",
        anticipated_solicitation="2026-07-10",
        anticipated_solicitation_close="2026-07-31",
        anticipated_award="2026-09-10",
        url="https://api.army.mil/example.xlsx",
        retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        forecast_status="Agency acquisition forecast",
    )
    army_source = _Src("army_acquisition_forecast", [record])
    _wire(
        monkeypatch,
        _Src("dhs_apfs", [], enabled=False),
        _Src("acquisition_gateway", [], enabled=False),
        army_source,
    )

    records, queries = retrieval.run_l4(["network support"], ["541512"])

    assert [row.record_id for row in records] == ["PANMCC-26-P-0000 041442"]
    assert records[0].psc == "7A20"
    assert records[0].anticipated_solicitation == "2026-07-10"
    assert records[0].anticipated_solicitation_close == "2026-07-31"
    assert records[0].anticipated_award == "2026-09-10"
    query = next(q for q in queries if q.method == "army_acquisition_forecast")
    assert query.endpoint.endswith("2026-osbp-acq-forecast-jul-dec.xlsx")
    assert query.result_count == query.kept_after_screen == 1


def test_a_disabled_adapter_is_a_named_row_and_the_lane_stays_live(monkeypatch):
    dhs = _Src("dhs_apfs", [], enabled=False)
    gw = _Src("acquisition_gateway",
              [_forecast("acquisition_gateway", "GW-1", "Interior",
                         "Network monitoring recompete")])
    _wire(monkeypatch, dhs, gw)
    records, queries = retrieval.run_l4(VOCAB, ["541512"])
    assert len(records) == 1                 # gateway carries the lane
    off = next(q for q in queries if q.method == "dhs_apfs")
    assert off.note == "LILA_ENABLE_DHS_APFS is off"
    live = next(q for q in queries if q.method == "acquisition_gateway")
    assert live.kept_after_screen == 1
    # the pack-level status rule: one live adapter means NOT degraded
    assert not all((q.note or "").endswith("is off") for q in queries)


def test_all_adapters_off_is_a_degraded_state(monkeypatch):
    _wire(monkeypatch, _Src("dhs_apfs", [], enabled=False),
          _Src("acquisition_gateway", [], enabled=False))
    records, queries = retrieval.run_l4(VOCAB, ["541512"])
    assert records == []
    assert len(queries) == 2
    assert all((q.note or "").endswith("is off") for q in queries)
    assert retrieval._forecast_lane_status(queries) == "degraded"


def test_every_enabled_adapter_failure_degrades_the_lane(monkeypatch):
    _wire(
        monkeypatch,
        _FailingSrc("dhs_apfs", []),
        _FailingSrc("acquisition_gateway", []),
    )

    records, queries = retrieval.run_l4(VOCAB, ["541512"])

    assert records == []
    assert all(query.note.startswith("source failed:") for query in queries)
    assert retrieval._forecast_lane_status(queries) == "degraded"


def test_every_self_reported_adapter_failure_degrades_the_lane(monkeypatch):
    _wire(
        monkeypatch,
        _ProvenanceFailingSrc("nasa_naf", []),
        _ProvenanceFailingSrc("acquisition_gateway", []),
    )

    records, queries = retrieval.run_l4(VOCAB, ["541512"])

    assert records == []
    assert all(query.note == "source failed: adapter provenance"
               for query in queries)
    assert retrieval._forecast_lane_status(queries) == "degraded"


def test_successful_zero_result_adapter_keeps_the_lane_live(monkeypatch):
    _wire(
        monkeypatch,
        _Src("dhs_apfs", []),
        _FailingSrc("acquisition_gateway", []),
    )

    records, queries = retrieval.run_l4(VOCAB, ["541512"])

    assert records == []
    assert retrieval._forecast_lane_status(queries) == "live"


def test_one_failed_adapter_is_named_and_does_not_sink_other_sources(
        monkeypatch):
    _wire(
        monkeypatch,
        _FailingSrc("dhs_apfs", []),
        _Src("acquisition_gateway", [
            _forecast(
                "acquisition_gateway", "GW-1", "Interior",
                "Network monitoring recompete",
            )
        ]),
    )

    records, queries = retrieval.run_l4(VOCAB, ["541512"])

    assert [record.record_id for record in records] == ["GW-1"]
    failed = next(query for query in queries if query.method == "dhs_apfs")
    assert failed.result_count == 0
    assert failed.note == "source failed: RuntimeError"


def test_cross_source_forecast_ids_survive_global_deduplication(monkeypatch):
    shared_id = "10022"
    _wire(
        monkeypatch,
        _Src("nasa_naf", [
            _forecast(
                "nasa_naf", shared_id, "NASA",
                "Network monitoring for mission operations",
            )
        ]),
        _Src("acquisition_gateway", [
            _forecast(
                "acquisition_gateway", shared_id, "Interior",
                "Enterprise network monitoring services",
            )
        ]),
    )

    records, _ = retrieval.run_l4(VOCAB, ["541512"])

    assert [record.record_id for record in records] == [shared_id, shared_id]
    assert {record.generated_internal_id for record in records} == {
        "nasa_naf::10022",
        "acquisition_gateway::10022",
    }
    assert len(retrieval.dedupe(records)) == 2


def test_scoring_is_unified_across_the_merged_corpus(monkeypatch):
    """One IDF universe: a term's rarity is judged against BOTH adapters'
    records, so cross-source ranking is comparable."""
    dhs = _Src("dhs_apfs",
               [_forecast("dhs_apfs", f"A-{i}", "DHS",
                          "Generic network monitoring support")
                for i in range(5)])
    gw = _Src("acquisition_gateway",
              [_forecast("acquisition_gateway", "GW-RARE", "Interior",
                         "Network visibility fabric for park telemetry")])
    _wire(monkeypatch, dhs, gw)
    records, _ = retrieval.run_l4(VOCAB, ["541512"])
    rare = next(r for r in records if r.record_id == "GW-RARE")
    assert rare.forecast_rarity >= max(
        r.forecast_rarity for r in records if r.record_id != "GW-RARE")


def test_the_injected_fetch_seam_still_works():
    records, queries = retrieval.run_l4(
        VOCAB, ["541512"],
        forecast_fetch=lambda: [_forecast("dhs_apfs", "T-1", "DHS",
                                          "Network monitoring seam test")])
    assert len(records) == 1
    assert queries[0].method == "injected_fetch"
    assert queries[0].kept_after_screen == 1


# --------------------------------------------------------------------------- #
# the coverage ledger tells the truth about what runs
# --------------------------------------------------------------------------- #
def test_coverage_reflects_effective_enablement_not_ship_defaults(monkeypatch):
    """DHS APFS is covered by default and an explicit off switch is honored."""
    import importlib

    import tools.api.forecasts.coverage as cov

    monkeypatch.setenv("LILA_ENABLE_DHS_APFS", "on")
    armed = importlib.reload(cov)
    assert armed.COVERED_AGENCIES.get("DHS") == "dhs_apfs"

    monkeypatch.setenv("LILA_ENABLE_DHS_APFS", "off")
    dark = importlib.reload(cov)
    assert "DHS" not in dark.COVERED_AGENCIES

    monkeypatch.undo()
    importlib.reload(cov)                    # leave the module as found


def test_gateway_detail_budget_is_disclosed_in_provenance(monkeypatch):
    from tools.api.forecasts import acquisition_gateway as ag

    src = ag.AcquisitionGatewaySource()
    monkeypatch.setattr(ag, "get_json", lambda *a, **k: {})
    records = [_forecast("acquisition_gateway", f"GW-{i}", "Interior",
                         f"Program {i}") for i in range(80)]
    # urls carry no /resources/<id>, so every row passes through untouched;
    # the disclosure must still count the budget honestly
    src.enrich_matched(records)
    prov = src.last_provenance
    assert prov["detail_candidates"] == 80
    assert prov["detail_enriched"] == 75
    assert prov["detail_truncated"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
