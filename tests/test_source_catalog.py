"""Central source metadata and runner/registry drift controls."""

from __future__ import annotations

import pytest

from run_searches import _RESULT_KEY_FOR_SKIP
from tools.api.forecasts.coverage import COVERED_AGENCIES
from tools.api import REGISTRY
from tools.api.source_catalog import (
    CONNECTED_NOT_USED,
    DEFAULT_CHILD_SOURCES_BY_PARENT,
    FORECAST_SOURCE_NAMES,
    RESULT_KEY_FOR_TASK,
    SOURCE_BY_ADAPTER,
    STANDARD_SOURCE_SPECS,
    STANDARD_SWEEP_LANES,
    STANDARD_TASK_KEYS,
    source_spec,
    validate_standard_tasks,
)


def test_registry_runner_coverage_and_consumers_reconcile():
    registered = {source.name for source in REGISTRY.all()}
    assert registered == set(SOURCE_BY_ADAPTER)
    assert len(STANDARD_SOURCE_SPECS) == 32
    assert STANDARD_TASK_KEYS == tuple(
        source for source, _label in STANDARD_SWEEP_LANES)
    assert dict(RESULT_KEY_FOR_TASK) == {
        spec.task_key: spec.result_key for spec in STANDARD_SOURCE_SPECS}
    assert _RESULT_KEY_FOR_SKIP == {
        **dict(RESULT_KEY_FOR_TASK), "picture": "research_picture"}
    assert all(spec.consumers for spec in STANDARD_SOURCE_SPECS)
    assert FORECAST_SOURCE_NAMES == (
        "dhs_apfs", "acquisition_gateway", "army_acquisition_forecast",
        "nasa_naf", "hhs_sbcx", "state_forecast", "ed_forecast",
        "hud_forecast", "sec_procurement_forecast", "navy_nawcwd_lraf",
        "navy_nawcad_lraf", "va_fco")
    assert tuple(
        spec.identity
        for spec in DEFAULT_CHILD_SOURCES_BY_PARENT["forecasts"]
    ) == (
        "dhs_apfs", "acquisition_gateway", "army_acquisition_forecast",
        "nasa_naf", "hhs_sbcx", "state_forecast", "ed_forecast",
        "hud_forecast", "sec_procurement_forecast", "navy_nawcwd_lraf",
        "navy_nawcad_lraf", "va_fco")
    assert {identity for identity, _label, _detail in CONNECTED_NOT_USED} == {
        "sam_notice_detail", "sam_entity_exclusions"}


def test_standard_source_coverage_contracts_are_explicit_and_reconcile():
    by_class = {
        coverage_class: {
            spec.task_key
            for spec in STANDARD_SOURCE_SPECS
            if spec.coverage_class == coverage_class
        }
        for coverage_class in (
            "required-exhaustive", "required-bounded", "advisory")
    }

    assert by_class["advisory"] == {"grants", "gdelt", "edgar"}
    assert by_class["required-bounded"] == {
        "usaspending.gov", "web", "federal_register", "news", "subawards",
        "contract_awards", "dod_contracts", "watchdogs", "congress",
        "regulations", "govinfo", "treasury", "gao", "calc", "fedramp",
        "hierarchy",
        "forecasts", "source_mesh", "buyer_map", "funded_demand",
        "dod_budget_exhibits",
    }
    assert by_class["required-exhaustive"] == (
        set(STANDARD_TASK_KEYS)
        - by_class["advisory"]
        - by_class["required-bounded"]
    )
    assert all(
        spec.coverage_boundary
        for spec in STANDARD_SOURCE_SPECS
        if spec.coverage_class in {"required-bounded", "advisory"}
    )
    assert all(
        not spec.coverage_boundary
        for spec in STANDARD_SOURCE_SPECS
        if spec.coverage_class == "required-exhaustive"
    )
    assert source_spec("dod_budget_exhibits").coverage_origins == (
        "pacific_deterrence_initiative",
        "counter_drug_activities",
    )
    assert source_spec("forecasts").coverage_origins == (
        "acquisition_gateway",
        "army_acquisition_forecast",
        "nasa_naf",
        "hhs_sbcx",
        "dhs_apfs",
        "state_forecast",
        "ed_forecast",
        "hud_forecast",
        "sec_procurement_forecast",
        "navy_nawcwd_lraf",
        "navy_nawcad_lraf",
        # A8 (2026-08-18): VA eVP forecast rides the lane as a receipted
        # login-walled child until a portal session or open format exists.
        "va_fco",
    )
    # 27 = the 24 official adapters plus the three always-swept market lanes
    # (operator order, 2026-08-18): sam_mirrors, esi_reseller_catalogs,
    # vendor_press.
    assert len(source_spec("source_mesh").coverage_origins) == 27
    assert source_spec("source_mesh").coverage_origins[0] == "gsa_elibrary"
    assert source_spec("source_mesh").coverage_origins[-1] == "vendor_press"
    assert "sam_mirrors" in source_spec("source_mesh").coverage_origins
    assert "esi_reseller_catalogs" in source_spec("source_mesh").coverage_origins
    assert "DHS APFS" in source_spec("forecasts").coverage_boundary
    contract_awards = source_spec("contract_awards")
    assert contract_awards.coverage_origins == ("contract_awards_boundary",)
    assert "2007-10-01" in contract_awards.coverage_boundary
    assert "not an all-history" in contract_awards.coverage_boundary
    assert "at most fifteen" in source_spec("web").coverage_boundary
    assert "first six approved NAICS" in source_spec(
        "subawards").coverage_boundary
    assert "180-second" in source_spec("buyer_map").coverage_boundary


def test_runner_reconciliation_is_ordered_and_fails_named():
    validate_standard_tasks(dict.fromkeys(STANDARD_TASK_KEYS))
    with pytest.raises(RuntimeError, match="source catalog / runner task drift"):
        validate_standard_tasks(dict.fromkeys(STANDARD_TASK_KEYS[:-1]))
    with pytest.raises(RuntimeError, match="order_expected"):
        validate_standard_tasks(dict.fromkeys(reversed(STANDARD_TASK_KEYS)))


def test_program_scope_support_and_forecast_activation_are_explicit(monkeypatch):
    assert source_spec("acquisition_gateway").enabled_default is True
    assert source_spec("acquisition_gateway").parent_identity == "forecasts"
    assert "CFO Act" in source_spec(
        "acquisition_gateway").coverage_description
    assert source_spec("dhs_apfs").enabled_default is True
    army = source_spec("army_acquisition_forecast")
    assert army.enabled_default is True
    assert army.parent_identity == "forecasts"
    assert army.coverage_active is True
    assert army.agency_codes == ("Army",)
    assert "not to every DoD component" in army.coverage_description
    assert COVERED_AGENCIES.get("Army") == "army_acquisition_forecast"
    nasa = source_spec("nasa_naf")
    assert nasa.enabled_default is True
    assert nasa.coverage_active is True
    assert nasa.agency_codes == ("NASA",)
    assert COVERED_AGENCIES.get("NASA") == "nasa_naf"
    hhs = source_spec("hhs_sbcx")
    assert hhs.enabled_default is True
    assert hhs.coverage_active is False
    assert "one ingest path" in hhs.coverage_description

    # The module-level COVERED_AGENCIES import above is computed once from
    # effective enablement, so test the same toggle predicate directly. The
    # derived mapping is covered for both arms by
    # test_l4_multisource.test_coverage_reflects_effective_enablement_not_ship_defaults,
    # which owns the reload dance; duplicating it here would reload a module
    # other tests already hold references into.
    from tools.toggles import is_enabled

    monkeypatch.delenv("LILA_ENABLE_DHS_APFS", raising=False)
    assert is_enabled(
        "dhs_apfs", source_spec("dhs_apfs").enabled_default) is True

    for identity in (
        "dsip_topics", "darpa_opportunities", "dod_budget_exhibits",
    ):
        spec = source_spec(identity)
        assert spec.standard is True
        assert spec.record_role == "program-signal"
        assert spec.scope_policy == "defense-only"
        assert "horizon-factory" in spec.consumers
    dsip = source_spec("dsip_topics")
    assert dsip.task_key == dsip.result_key == dsip.adapter_name == "dsip_topics"
    assert dsip.label == "DoD SBIR/STTR DSIP active topics"
    assert STANDARD_TASK_KEYS.index("dsip_topics") == (
        STANDARD_TASK_KEYS.index("sbir") + 1
    )
    assert source_spec("dod_budget_exhibits").support_level == "partial"

    for identity, adapter in (
        ("grants", "grants_gov"),
        ("sbir", "sbir_gov"),
    ):
        spec = source_spec(identity)
        assert spec.adapter_name == adapter
        assert spec.result_key == adapter
        assert spec.scope_policy == "engagement-scope"
        assert "horizon-factory" in spec.consumers
        assert "research-picture" in spec.consumers

    for identity in ("reginfo_unified_agenda", "foreign_assistance"):
        spec = source_spec(identity)
        assert spec.standard is True
        assert spec.scope_policy == "engagement-scope"
        assert spec.record_role == "program-signal"
