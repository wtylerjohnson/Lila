"""Restored official forecast origins remain complete, strict, and auditable."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.schemas import CapabilityProfile, ForecastRecord, RawOpportunity
from tools.api.base import SourceKind, SourceQuery
from tools.api.ed_forecast import EdForecastSource, _matches as ed_matches
from tools.api.forecasts import forecast_sources
from tools.api.forecasts.bridge import from_raw
from tools.api.forecasts.matching import bucket_forecasts
from tools.api.hud_forecast import HudForecastSource, _matches as hud_matches
from tools.api.navy_nawcad_lraf import (
    NavyNawcadLrafSource,
    _matches as nawcad_matches,
)
from tools.api.navy_nawcwd_lraf import (
    NavyNawcwdLrafSource,
    _matches as nawcwd_matches,
)
from tools.api.sec_procurement_forecast import (
    SecProcurementForecastSource,
    _matches as sec_matches,
)
from tools.api.source_catalog import source_spec
from tools.api.state_forecast import StateForecastSource, _matches as state_matches
from tools.relevance.taxonomy import CapabilityTaxonomy, CodeUniverse, TaxonomyTerm


RESTORED = (
    "state_forecast",
    "ed_forecast",
    "hud_forecast",
    "sec_procurement_forecast",
    "navy_nawcwd_lraf",
    "navy_nawcad_lraf",
)


def _forecast(source: str, source_id: str, title: str) -> ForecastRecord:
    return ForecastRecord(
        source=source,
        source_id=source_id,
        agency="Federal agency",
        title=title,
        description="General program support services.",
        naics_code="541512",
        url=f"https://example.gov/{source_id}",
        retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )


def test_all_restored_origins_are_registered_forecast_children() -> None:
    by_name = {source.name: source for source in forecast_sources()}
    assert set(RESTORED).issubset(by_name)
    assert set(source_spec("forecasts").coverage_origins) == set(by_name)
    for identity in RESTORED:
        assert by_name[identity].kind is SourceKind.ENRICHMENT
        assert callable(by_name[identity].forecasts)
        spec = source_spec(identity)
        assert spec.parent_identity == "forecasts"
        assert spec.enabled_default is True
        assert spec.official_url.startswith("https://")
        assert spec.coverage_description


def test_naics_only_rows_from_restored_origins_stay_quarantined() -> None:
    profile = CapabilityProfile(
        client_name="Arista Networks",
        naics_codes=["541512"],
    )
    taxonomy = CapabilityTaxonomy(
        client_name="Arista Networks",
        version=1,
        updated="2026-08-14",
        core=[TaxonomyTerm(term="network switch", mode="stemmed")],
        code_universe=CodeUniverse(naics=["541512"]),
    )
    generic = [
        _forecast(source, f"{source}-generic", "Administrative support")
        for source in RESTORED
    ]
    direct = _forecast(
        "state_forecast",
        "state-network-switch",
        "Enterprise network switch modernization",
    )

    buckets = bucket_forecasts(generic + [direct], profile, taxonomy=taxonomy)

    assert [row["record"].source_id for row in buckets["capability"]] == [
        "state-network-switch"
    ]
    assert {row.source for row in buckets["lane_only"]} == set(RESTORED)


@pytest.mark.parametrize(
    "matcher",
    [
        lambda query: state_matches(
            {"requirement_title": "Magnitude analysis"}, [], query
        ),
        lambda query: ed_matches({"contract_name": "Magnitude analysis"}, [], query),
        lambda query: hud_matches({"description": "Magnitude analysis"}, query),
        lambda query: sec_matches({"requirement": "Magnitude analysis"}, query),
        lambda query: nawcwd_matches({"title": "Magnitude analysis"}, query),
        lambda query: nawcad_matches({"title": "Magnitude analysis"}, query),
    ],
)
def test_restored_adapter_prefilters_use_boundary_safe_phrases(matcher) -> None:
    assert matcher(SourceQuery(keywords=["AGNI"])) is False


def test_bridge_preserves_published_join_keys_and_source_fields() -> None:
    opportunity = RawOpportunity(
        source="navy_nawcwd_lraf",
        source_id="nawcwd-1",
        title="Network modernization",
        agency="Department of the Navy / NAWCWD",
        api_url="https://www.navair.navy.mil/example.pdf",
        raw_payload={
            "_normalized": {
                "description": "Switching and routing modernization",
                "group_name": "Weapons Division",
                "incumbent_stated": "Example Federal LLC",
                "incumbent_contract_number": "N6893624D0003",
            }
        },
    )

    record = from_raw(
        opportunity,
        {
            "retrieved_at": "2026-08-14T12:00:00+00:00",
            "data_as_of": "2026-01-13",
        },
    )

    assert record.predecessor_contract_id == "N6893624D0003"
    assert record.incumbent_stated == "Example Federal LLC"
    assert record.source_fields["group_name"] == "Weapons Division"
    assert record.data_as_of == "2026-01-13"


def test_source_receipt_exposes_exact_artifact_and_landing_page() -> None:
    from run_searches import _forecast_source_summary

    source = StateForecastSource(fetch_text=lambda *_a, **_k: "")
    record = _forecast("state_forecast", "state-1", "Network switch")
    receipt = _forecast_source_summary(
        "state_forecast",
        [record],
        [record],
        {
            "status": "complete",
            "mode": "live_state_forecast_xlsx",
            "retrieved_at": "2026-08-14T12:00:00+00:00",
            "source_url": (
                "https://www.state.gov/wp-content/uploads/2026/04/"
                "FY26-Procurement-Forecast-4.xlsx"
            ),
            "landing_url": "https://www.state.gov/procurement-forecast/",
        },
        contract_origins=set(source_spec("forecasts").coverage_origins),
    )

    assert source.name == "state_forecast"
    assert receipt["contract_origin"] is True
    assert receipt["status"] == "success"
    assert receipt["source_url"].endswith("FY26-Procurement-Forecast-4.xlsx")
    assert receipt["landing_url"] == "https://www.state.gov/procurement-forecast/"


@pytest.mark.parametrize(
    "source_class",
    [
        StateForecastSource,
        EdForecastSource,
        HudForecastSource,
        SecProcurementForecastSource,
        NavyNawcwdLrafSource,
        NavyNawcadLrafSource,
    ],
)
def test_restored_origin_failure_is_explicit(source_class) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("fixture outage")

    kwargs = (
        {"fetch_text": fail}
        if source_class in {StateForecastSource, EdForecastSource}
        else {"fetch_bytes": fail}
    )
    source = source_class(**kwargs)

    assert source.forecasts() == []
    assert source.last_provenance["status"] == "failed"
    assert source.last_provenance["record_count"] == 0
    assert "fixture outage" in source.last_provenance["limitations"][0]
