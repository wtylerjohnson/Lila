"""Executable classification of LILA output families.

Only the eight-slot Federal Market Map is an external product. Older output
families remain readable and testable as internal views or compatibility
adapters, but they cannot be selected by the external release action.
"""

from __future__ import annotations

from dataclasses import dataclass


PRODUCT_FAMILY_REGISTRY_VERSION = "product-families.v1.2026-08-23"


@dataclass(frozen=True)
class ProductFamily:
    family_id: str
    role: str
    label: str
    releasable: bool
    note: str


FAMILIES = (
    ProductFamily(
        "lila_federal_market_map", "external_product", "LILA Federal Market Map",
        True, "The operator-locked eight-slot product and complete release bundle."),
    ProductFamily(
        "market_map_studio", "internal_view", "Legacy Market Map Studio",
        False, "Seven-section studio and migration renderer."),
    ProductFamily(
        "signal_board", "compatibility_adapter", "Signal Board",
        False, "Deterministic legacy opportunity-board compatibility surface."),
    ProductFamily(
        "candidate_review", "internal_view", "Candidate Review",
        False, "Research and operator-review surface feeding the product projection."),
    ProductFamily(
        "capture_brief", "compatibility_adapter", "Capture Brief",
        False, "Retired assessment renderer retained for replay and migration."),
    ProductFamily(
        "federal_opportunity_assessment", "compatibility_adapter",
        "Federal Opportunity Assessment", False,
        "Legacy assessment family retained behind its existing gates."),
    ProductFamily(
        "target_report", "internal_view", "Target Report",
        False, "Operator outreach view; opportunity targets now render in slot 5."),
    ProductFamily(
        "sales_teaser", "internal_view", "Sales Teaser",
        False, "Redacted internal sales view, never the external product."),
)

_BY_ID = {family.family_id: family for family in FAMILIES}


def product_family(family_id: str) -> ProductFamily:
    try:
        return _BY_ID[str(family_id)]
    except KeyError as exc:
        raise KeyError(f"unknown product family: {family_id}") from exc


def external_product_family() -> ProductFamily:
    external = [family for family in FAMILIES if family.role == "external_product"]
    if len(external) != 1 or not external[0].releasable:
        raise RuntimeError("the product-family registry must have one releasable external product")
    return external[0]


def internal_family_ids() -> tuple[str, ...]:
    return tuple(family.family_id for family in FAMILIES if not family.releasable)


__all__ = (
    "FAMILIES",
    "PRODUCT_FAMILY_REGISTRY_VERSION",
    "ProductFamily",
    "external_product_family",
    "internal_family_ids",
    "product_family",
)
