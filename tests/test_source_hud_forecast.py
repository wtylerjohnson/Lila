"""HUD forecast: verified PDF rows enter the PROGRAM forecast lane."""

from tools.api.base import SourceQuery
from tools.api.hud_forecast import HudForecastSource, _normalize_rows


ROW = {
    "hud office": "Office of the CIO",
    "fiscal year": "2026",
    "plan number": "APP-C-2026-001",
    "requirement type": "Recompete",
    "description": "Enterprise network visibility and monitoring support",
    "primary naics code": "541512",
    "macs or gwacs": "GSA MAS",
    "type of competition": "Small Business Set-Aside",
    "total contract value dollar range (base and all option values)": "$3 million to $5 million",
    "point of contact email": "buyer@hud.gov",
    "solicitation release date (month)": "May, 2026",
    "award date (month)": "September, 2026",
    "contract length": "BASE & 4 OPTIONS",
    "contract status": "Planned",
    "modified": "01/15/2026",
}


def test_normalize_preserves_plan_join_keys_and_stated_semantics():
    records, dropped = _normalize_rows([ROW], SourceQuery(
        keywords=["network visibility"], naics_codes=["541512"], limit=20))
    assert dropped == 0
    record = records[0]
    assert record.source_id == "APP-C-2026-001"
    assert record.naics_code == "541512"
    assert record.estimated_value == 3_000_000
    assert record.contacts[0].email == "buyer@hud.gov"
    assert record.posted_date is None and record.response_deadline is None


def test_forecast_bridge_keeps_hud_as_program_intent():
    source = HudForecastSource(fetch_bytes=lambda *args, **kwargs: (b"bad", {}))
    record = _normalize_rows([ROW], SourceQuery(limit=20))[0][0]
    source.search = lambda _query: [record]
    source.last_provenance = {
        "retrieved_at": "2026-08-08T12:00:00+00:00",
        "data_as_of": "2026-08-01",
    }
    forecast = source.forecasts()[0]
    assert forecast.source == "hud_forecast"
    assert forecast.source_id == "APP-C-2026-001"
    assert forecast.anticipated_solicitation == "May, 2026"
    assert forecast.incumbent_stated is None


def test_failure_is_named_provenance_not_empty_success():
    source = HudForecastSource(fetch_bytes=lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("fixture outage"))))
    assert source.search(SourceQuery(limit=20)) == []
    assert source.last_provenance["status"] == "failed"
    assert "fixture outage" in source.last_provenance["limitations"][0]
