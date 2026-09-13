"""APFS scope retention is independent of product fit and buying status."""
from datetime import datetime, timezone

import pytest

from agents.schemas import CapabilityProfile
from tools.agencies import find, matches_record
from tools.api.forecasts.dhs_apfs import map_record, scope_identity
from tools.api.forecasts.matching import match_forecasts
import run_searches as rs

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def row(component, **updates):
    rec = map_record({
        "id": 123, "apfs_number": "TEST-APFS-123",
        "requirements_title": "Building maintenance",
        "requirement": "Plumbing and HVAC services.",
        "organization": component, "naics": "238220 - Plumbing",
        "current_state": "Published",
    }, retrieved_at=NOW)
    return rec.model_copy(update=updates)


def collect(rows, agency="DHS", origin="dhs_apfs"):
    class Source:
        name = origin
        def forecasts(self):
            return rows
    return rs._collect_forecast_child(
        Source(), profile=None, capability_terms=[], taxonomy=None,
        engagement_scope=None, scope_agencies=[find(agency)])


@pytest.mark.parametrize("component", [
    "CBP/OIT/EDME", "TSA/OAPM", "USCG/CG-C5I", "ICE/M&A/CIO",
    "USCIS", "FEMA", "USSS", "FLETC/DD/CIO", "DHS HQ/CISA",
    "DHS HQ/MGMT", "DHS HQ/S&T", "DHS HQ/OHS", "DHS HQ", "UNKNOWN",
])
def test_official_apfs_parent_retains_every_component_without_rewriting(component):
    record = row(component)
    before = record.model_dump()
    result = collect([record])
    assert result["market_rows"] == result["rows"] == [record]
    assert record.model_dump() == before
    assert result["rows"][0].component == component


@pytest.mark.parametrize(("scope", "component"), [
    ("CISA", "DHS HQ/CISA"), ("USCIS", "USCIS"),
    ("USCG", "USCG/CG-C5I"), ("ICE", "ICE/M&A/CIO"),
    ("FEMA", "FEMA"), ("FLETC", "FLETC/DD/CIO"),
])
def test_component_scope_retains_only_its_own_branch(scope, component):
    wanted = row(component)
    other = row("DHS HQ/MGMT")
    prefix_collision = row(component.split('/')[0] + "ish")
    result = collect([other, wanted, prefix_collision], scope)
    assert result["rows"] == [wanted]
    assert result["market_rows"] == [other, wanted, prefix_collision]


@pytest.mark.parametrize("updates", [
    {"source": "web"}, {"agency": "Department of Human Services"},
    {"url": "https://apfs-cloud.dhs.gov.example.org/record/123"},
    {"url": "http://apfs-cloud.dhs.gov/record/123"},
])
def test_expansion_requires_federal_source_identity(updates):
    record = row("USCIS", **updates)
    assert scope_identity(record) == ""
    assert collect([record])["rows"] == []


def test_another_collector_cannot_inherit_apfs_expansion():
    assert collect([row("USCIS")], origin="web")["rows"] == []
    assert not matches_record("State Department of Human Services", find("DHS"))


def test_retention_is_not_capability_match_or_live_opportunity():
    records = collect([row("USCIS")])["rows"]
    profile = CapabilityProfile(client_name="Test Vendor", naics_codes=["541512"])
    assert match_forecasts(records, profile, keywords=["packet capture"]) == []
    assert records[0].forecast_status == "Published"
    assert not hasattr(records[0], "notice_id")
