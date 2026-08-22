"""Operator record rulings: encoded 2026-08-07, enforced forever.

Three rulings, one mechanism. A ruling is an ENGAGEMENT decision by
identifier that data cannot make; the projection applies it last, receipts
it always, and never silently deletes. These tests hold the rulings' exact
behavior: Omni rejected as wrong-domain but retained in the ledger, TCRS
reclassified to a recompete watch instead of a bridge pursuit, and the
FIAR family kept with its motion split by route.
"""

from __future__ import annotations

import pytest

from agents.golden_press.coverage_families import (
    MOTION_DIRECT_SHAPE, MOTION_PARTNER_ACCESS, MOTION_PARTNER_CPA,
    MOTION_SUCCESSOR_WATCH, load, qualify, route_motion,
)

_CONTRACT = load("apexanalytix")

pytestmark = pytest.mark.skipif(
    not _CONTRACT, reason="apexanalytix contract not on this checkout")


# --------------------------------------------------------------------------- #
# Ruling 1 · access credentialing is another industry wearing our words
# --------------------------------------------------------------------------- #
def test_base_access_vetting_is_rejected_as_wrong_domain():
    verdict = qualify(
        title="Omni Vendor Vetting Software Program",
        description=(
            "Notice of intent to award a sole source contract for the US "
            "Coast Guard Base Kodiak for the Omni Vendor Vetting Software "
            "Program, providing visitor management, badging and base access "
            "vetting for personnel entering the installation."),
        phrase="vendor vetting", contract=_CONTRACT)
    assert verdict["decision"] == "reject"
    assert verdict["reason_class"] == "rejected_wrong_domain"


def test_supplier_vetting_survives_the_access_rule():
    """The guard: vetting SUPPLIERS, payees and bank accounts stays in,
    even when access language appears incidentally."""
    verdict = qualify(
        title="Supplier Vetting and Vendor Master Validation Platform",
        description=(
            "The agency requires supplier vetting software validating vendor "
            "master records, payee identity and bank account ownership prior "
            "to payment. Contractor personnel may require facility access "
            "for onboarding sessions."),
        phrase="vendor vetting", contract=_CONTRACT)
    assert verdict["decision"] == "accept"


# --------------------------------------------------------------------------- #
# Ruling 2 · healthcare claims: context required, generic claims never
# --------------------------------------------------------------------------- #
def test_tricare_claims_review_qualifies_under_the_controlled_trigger():
    verdict = qualify(
        title="TRICARE Claims Review Services (TCRS)",
        description=(
            "The Defense Health Agency requires claims review services "
            "supporting payment integrity and improper payment reduction "
            "for TRICARE reimbursement."),
        phrase="claims review services", contract=_CONTRACT)
    assert verdict["decision"] == "accept"
    assert verdict["family"] == "Healthcare claims review"


def test_generic_claims_language_does_not_qualify():
    verdict = qualify(
        title="Claims Review Services for Workers Compensation Program",
        description=(
            "The agency seeks claims review services for its workers "
            "compensation program office."),
        phrase="claims review services", contract=_CONTRACT)
    assert verdict["decision"] == "reject"


# --------------------------------------------------------------------------- #
# Ruling 3 · FIAR: fit stays, the route changes
# --------------------------------------------------------------------------- #
_FAM = "Financial improvement and audit readiness"


def test_cpa_led_environment_routes_partner_under_cpa():
    routing = route_motion(
        family=_FAM,
        title="Financial Management FIAR BPA",
        description="RFP released on GSA MAS under NAICS 541211.",
        naics="541211")
    assert routing["motion"] == MOTION_PARTNER_CPA
    assert not routing["direct"]
    assert "partner" in routing["access_note"].casefold()


def test_restricted_access_routes_partner_access_required():
    routing = route_motion(
        family=_FAM,
        title="FIAR Support Services",
        description="Labor services; security clearance required.",
        set_aside="")
    assert routing["motion"] == MOTION_PARTNER_ACCESS


def test_pure_attest_work_never_routes_direct():
    routing = route_motion(
        family=_FAM,
        title="Financial Statement Audit Services",
        description="The contractor shall render an audit opinion on the "
                    "agency financial statements.")
    assert routing["motion"] == MOTION_PARTNER_CPA
    assert not routing["direct"]


def test_past_window_routes_successor_watch():
    routing = route_motion(
        family=_FAM,
        title="Audit Remediation Services RFI",
        description="Request for capability information.",
        response_past=True)
    assert routing["motion"] == MOTION_SUCCESSOR_WATCH


def test_open_software_addressable_rfi_routes_direct_shape():
    routing = route_motion(
        family=_FAM,
        title="Audit Remediation Analytics RFI",
        description="Seeking payment analytics and control monitoring "
                    "technology for audit remediation.")
    assert routing["motion"] == MOTION_DIRECT_SHAPE
    assert routing["direct"]


def test_non_audit_families_are_untouched_by_route_rules():
    assert route_motion(family="Fraud detection / prevention",
                        title="x", description="y") == {}


# --------------------------------------------------------------------------- #
# The applier: rulings are final and receipted
# --------------------------------------------------------------------------- #
def test_rulings_apply_after_the_clock_and_are_receipted():
    import json
    from pathlib import Path

    from agents.golden_press.market_map_projection import (
        _apply_record_rulings, Opportunity, opportunity_key,
    )
    from agents.golden_press.evidence_objects import NOTICE

    rulings_path = (Path(__file__).resolve().parents[1] / "clients"
                    / "apexanalytix" / "record_rulings.json")
    assert rulings_path.exists(), "the operator rulings file must ship"
    rulings = json.loads(rulings_path.read_text())["rulings"]
    assert {r["identifier"] for r in rulings} == {
        "31140PR260000095", "HT9402-26-RFI-0238", "HHM402-2X-Audit",
        "FA701426_FMF_FIAR", "N0024426RS005"}

    def _opp(ident):
        return Opportunity(
            key=opportunity_key(NOTICE, ident), identifier=ident,
            source_kind=NOTICE, title=f"t {ident}", agency="A", office="",
            opportunity_type="Sources Sought", value=None,
            response_date="2026-06-30", fit="f", motion="status check",
            action="a", access_route="r", contacts=(), evidence=(),
            coverage=None)

    rejected: list = []
    kept, receipt = _apply_record_rulings(
        "apexanalytix",
        (_opp("31140PR260000095"), _opp("HT9402-26-RFI-0238"),
         _opp("KEEP-ME-1")), rejected)
    assert receipt["rejected"] == 1 and receipt["reclassified"] == 1
    assert [o.identifier for o in kept] == ["HT9402-26-RFI-0238", "KEEP-ME-1"]
    assert kept[0].motion == "recompete watch"
    assert "do not contest" in kept[0].action.casefold()
    assert rejected[0]["reason_class"] == "rejected_wrong_domain"
