"""Client identity: the client kept distinct from its rivals.

apexanalytix pressed zero decision rows against a 330k-notice store and read
as an empty market. The profile had filed the client and its own products
under named_competitors_and_incumbents, so entities.client was empty and
every rule that needs a client side was dead by construction. These tests
pin the split, the guard that would have caught it, and the refusal to
guess a product from a rival.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import client_identity as ci  # noqa: E402


def _kinds(out):
    k = {}
    for e in out["entities"]:
        k.setdefault(e["kind"], []).append(e["name"])
    return k


# ===== the client is never its own rival ================================== #
def test_the_client_name_is_reclassified_out_of_the_competitor_list():
    """The measured apexanalytix defect: the client's own name in the
    competitor list."""
    out = ci.audit_client_identity({
        "client_name": "apexanalytix",
        "named_competitors_and_incumbents": ["apexanalytix", "Coupa", "SAP Ariba"]})
    kinds = _kinds(out)
    assert kinds["client"] == ["apexanalytix"]
    assert "apexanalytix" not in kinds.get("competitor", [])
    assert set(kinds["competitor"]) == {"Coupa", "SAP Ariba"}
    assert out["receipt"]["reclassified_client"] == ["apexanalytix"]


def test_a_client_name_prefixed_entry_becomes_a_product_not_a_rival():
    out = ci.audit_client_identity({
        "client_name": "apexanalytix",
        "named_competitors_and_incumbents": ["apexanalytix Platform", "Coupa"]})
    kinds = _kinds(out)
    assert "apexanalytix Platform" in kinds["product"]
    assert "apexanalytix Platform" not in kinds.get("competitor", [])


def test_a_declared_product_is_never_a_competitor():
    """apexanalytix's real products (apexportal, QubitOn) do not carry the
    client name, so they move only because the operator declared them."""
    out = ci.audit_client_identity({
        "client_name": "apexanalytix",
        "client_products": ["apexportal", "QubitOn"],
        "named_competitors_and_incumbents": ["apexportal", "QubitOn", "Coupa"]})
    kinds = _kinds(out)
    assert set(kinds["product"]) == {"apexportal", "QubitOn"}
    assert kinds.get("competitor", []) == ["Coupa"]


def test_an_undeclared_product_stays_a_competitor_but_is_flagged():
    """It CANNOT be told from a rival by string alone, so it is searched as a
    rival AND surfaced for an operator decision. Guessing either way corrupts
    the decision layer."""
    out = ci.audit_client_identity({
        "client_name": "apexanalytix",
        "named_competitors_and_incumbents": ["SmartVM", "Coupa"]})
    flagged = [x["name"] for x in out["receipt"]["needs_operator_decision"]]
    assert "SmartVM" in flagged
    assert "SmartVM" in _kinds(out).get("competitor", [])


def test_a_word_shared_with_no_client_token_is_not_swept_in():
    """"Global Bank Account Confidence Score" shares no client token and must
    stay whatever the operator filed it as, not become a product by
    accident."""
    assert ci._is_client_name("Global Bank Account Confidence Score",
                              "apexanalytix") is False
    assert ci._is_client_name("apexanalytix Platform", "apexanalytix") is True
    assert ci._is_client_name("apexanalytix", "apexanalytix") is True


def test_a_real_company_name_is_never_flagged_as_a_product():
    out = ci.audit_client_identity({
        "client_name": "Riverbed",
        "named_competitors_and_incumbents": [
            "Cisco ThousandEyes", "Broadcom AppNeta", "SolarWinds, Inc."]})
    flagged = [x["name"] for x in out["receipt"]["needs_operator_decision"]]
    assert "SolarWinds, Inc." not in flagged
    assert "Cisco ThousandEyes" not in flagged


# ===== THE GUARD ========================================================== #
def test_the_guard_fires_when_no_client_term_resolves():
    """THE CHECK WHOSE ABSENCE ZEROED apexanalytix. Competitors but no
    client cannot classify a single record as client paper."""
    with pytest.raises(ci.ClientIdentityError, match="no client entity"):
        ci.assert_client_resolvable(
            [{"kind": "competitor", "name": "Coupa"}],
            client_name="apexanalytix")


def test_the_guard_passes_when_a_client_entity_is_present():
    ci.assert_client_resolvable(
        [{"kind": "client", "name": "apexanalytix"},
         {"kind": "competitor", "name": "Coupa"}],
        client_name="apexanalytix")


def test_the_guard_reads_the_pack_entities_dict_shape():
    """The pack stores entities as {kind: [names]}; the guard reads that too,
    because the pack is where a zeroed client actually shows up."""
    with pytest.raises(ci.ClientIdentityError):
        ci.assert_client_resolvable(
            {"product": ["apexportal"], "competitor": ["Coupa"]},
            client_name="apexanalytix")
    ci.assert_client_resolvable(
        {"client": ["apexanalytix"], "competitor": ["Coupa"]},
        client_name="apexanalytix")


def test_a_profile_with_no_client_name_raises():
    with pytest.raises(ci.ClientIdentityError, match="no client_name"):
        ci.audit_client_identity(
            {"named_competitors_and_incumbents": ["Coupa"]})


# ===== the real profiles on disk ========================================== #
def test_the_apexanalytix_profile_now_resolves_client_side():
    """The profile that caused the incident, corrected: client and its six
    products, zero of them filed as rivals."""
    import json
    from pathlib import Path
    path = (Path(__file__).resolve().parents[1] / "clients" / "apexanalytix"
            / "profile.json")
    if not path.exists():
        pytest.skip("apexanalytix profile not on this machine")
    out = ci.audit_client_identity(
        json.loads(path.read_text(encoding="utf-8")))
    counts = out["receipt"]["counts"]
    assert counts["client"] == 1
    # products grew from 6 to 7 when the Gartner Magic Quadrant named the
    # flagship (Supplier Risk Resolution). Asserted as a floor, not a fixed
    # count: learning a real product must not break a test.
    assert counts["product"] >= 6
    assert counts["competitor"] == 0
    ci.assert_client_resolvable(out["entities"], client_name="apexanalytix")
