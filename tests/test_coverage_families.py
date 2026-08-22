"""The coverage contract's regression receipts, as executable law.

A receipt list in a JSON file is a wish. These are the tests that make it
binding: the named records must come back, the named junk must not, and both
halves must hold at once. Recall alone is trivially gamed by matching
everything, so every recall assertion here has a rejection assertion beside
it.

MEASURED PROGRESSION on the five locally-held must-recall records:
  1/5  exact substring matching on vendor phrases
  3/5  order-free content-word matching
  4/5  plus the government's own surface forms
  5/5  plus family-scoped context and best-match selection
"""

from __future__ import annotations

import sqlite3

import pytest

from agents.golden_press.coverage_families import (
    ACCEPT, ACTIVE, CONTROLLED, best_match, coverage_report, family_phrases,
    load, phrase_matches, phrases, qualify,
)

SLUG = "apexanalytix"

# The five must-recall records the local store holds, with the government's
# real titles and enough description to exercise the gate.
RECALL_CORPUS = {
    "24bd13ce699a4dc18df7d6f836efba4a": (
        "DA10--Payment Integrity Validation and Oversight Tool (PIVOT) "
        "(VA-26-00015670) RFI LoopBack",
        "The Payment Integrity Validation and Oversight Tool, RFQ "
        "#36C10B26Q0256 was posted unrestricted on GSA MAS Category 54151S. "
        "Improper payment detection for vendor payments."),
    "04633cac95d541f48bbb13025945904d": (
        "RFI - Fraud Detection and Payment Integrity Tools",
        "Treasury seeks vendor solutions for payment fraud detection and "
        "payment integrity across disbursements."),
    "baaaea69b8924e33a028d0694324f4a0": (
        "Third Party/Vendor Risk Management (VRM) Program",
        "Establish a third-party vendor risk management program including "
        "supplier assessment and continuous monitoring."),
    "6ba89d0956d54c9185ce8d46f7665865": (
        "Strategic Defense Industrial Base (SDIB) Software",
        "Supplier capacity risk and capacity forecasting across the "
        "industrial base, including sub-tier supplier visibility."),
    "e7a0f9b046db433c8f889359707009e0": (
        "Supply Chain Illumination RFI",
        "Commercially available technologies supporting an enterprise Supply "
        "Chain Risk Management (SCRM) capability for illuminating information "
        "and communication technology products. Detailed insights to include "
        "micro component traceability, software bill of materials (SBOM) "
        "breakdowns, supply chain relationship mapping, and reliable product "
        "and vender information."),
}

# The four must-reject classes, written as the records that embody them.
REJECT_CORPUS = {
    "vendor portal appears only in offer-submission instructions": (
        "Altitude Chambers Contractor Logistics Support",
        "Offers shall be submitted through the vendor portal. Vendors must "
        "be registered in SAM. This requirement is for altitude chamber "
        "maintenance."),
    "electronic invoicing appears only in a standard payment clause": (
        "VISN 7 H-Wave Electrotherapy Devices",
        "Invoices shall be submitted via the Invoice Processing Platform "
        "(IPP). Payment will be made in accordance with FAR 52.232-25 Prompt "
        "Payment Act. Supplier deliveries of electrotherapy units."),
    "generic agentic AI with no supplier or payment context": (
        "Agentic AI Proof of Concept",
        "The agency seeks a generative AI chatbot and large language model "
        "proof of concept for a contact center."),
    "SBOM-only software supply-chain vulnerability tool": (
        "Software Bill of Materials Vulnerability Scanning",
        "Endpoint protection and vulnerability scanning with SBOM ingestion "
        "for zero trust architecture."),
}


@pytest.fixture(scope="module")
def contract():
    data = load(SLUG)
    assert data, "the apexanalytix coverage contract must be present"
    return data


def test_every_family_has_a_disposition(contract):
    report = coverage_report(contract)
    assert report["disposition_coverage_percent"] == 100
    assert report["reconciles_with_header"], (
        "the stated family count and the actual family count disagree")
    assert report["active"] + report["controlled"] == report["families"]


def test_controlled_families_all_have_encoded_rules(contract):
    from agents.golden_press.coverage_families import CONTROLLED_RULES
    controlled = [f["family"] for f in contract["families"]
                  if f.get("status") == CONTROLLED]
    missing = [name for name in controlled if name not in CONTROLLED_RULES]
    assert not missing, (
        f"controlled families with no encoded rule are silently unsearchable: "
        f"{missing}")


def test_government_surface_forms_are_witnessed(contract):
    """A gov_form enters the frame only with a record that proves its use."""
    for family in contract["families"]:
        for form in (family.get("gov_forms") or []):
            assert form.get("phrase"), family["family"]
            assert form.get("witnessed_on"), (
                f"{family['family']}: '{form.get('phrase')}' has no witness "
                f"record, so nothing proves a buyer ever used the term")


def test_all_must_recall_records_come_back(contract):
    missed = []
    for notice_id, (title, body) in RECALL_CORPUS.items():
        if not best_match(title, contract, description=body):
            missed.append(title)
    assert not missed, f"must-recall records missed: {missed}"


@pytest.mark.parametrize("why", sorted(REJECT_CORPUS))
def test_all_must_reject_classes_stay_out(contract, why):
    title, body = REJECT_CORPUS[why]
    match = best_match(title, contract, description=body)
    assert not match, (
        f"'{why}' was accepted as {match.get('family')!r} via "
        f"{match.get('phrase')!r}")


def test_recall_and_rejection_hold_together(contract):
    """The pair, not either half. Matching everything scores perfect recall."""
    recalled = sum(1 for t, b in RECALL_CORPUS.values()
                   if best_match(t, contract, description=b))
    rejected = sum(1 for t, b in REJECT_CORPUS.values()
                   if not best_match(t, contract, description=b))
    assert (recalled, rejected) == (len(RECALL_CORPUS), len(REJECT_CORPUS))


def test_the_positive_context_gate_is_what_removes_the_junk(contract):
    """Name the mechanism, so a later refactor cannot quietly remove it."""
    title, body = REJECT_CORPUS[
        "vendor portal appears only in offer-submission instructions"]
    verdict = qualify(title=title, description=body,
                      phrase="vendor risk management program",
                      contract=contract)
    assert verdict["decision"] != ACCEPT


def test_content_word_matching_is_order_free():
    assert phrase_matches("payment fraud detection",
                          "Fraud Detection and Payment Integrity Tools")
    assert not phrase_matches("payment fraud detection",
                              "Fraud reduction and integrity tools")


def test_specificity_beats_alphabetical_order(contract):
    """A two-word coincidence must not outrank the real subject."""
    title, body = RECALL_CORPUS["baaaea69b8924e33a028d0694324f4a0"]
    match = best_match(title, contract, description=body)
    assert match["family"] == "TPRM / vendor risk management"


def test_phrases_include_both_languages(contract):
    """Vendor phrasing and government phrasing are both in the search set."""
    surface = {p.casefold() for p in phrases(contract,
                                             statuses=(ACTIVE, CONTROLLED))}
    assert "overpayment prevention" in surface          # the vendor's term
    assert "payment integrity validation" in surface    # the buyer's term


def test_a_family_with_no_phrases_is_a_contract_defect(contract):
    empty = [f["family"] for f in contract["families"]
             if not family_phrases(f)]
    assert not empty, f"families with no search surface: {empty}"


def test_store_lane_dedupes_one_requirement_to_one_row(tmp_path):
    """Eleven postings of one Air Force requirement are one opportunity."""
    from agents.golden_press.market_map_projection import store_opportunities

    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE notices (notice_id TEXT, title TEXT, notice_type TEXT, "
        "sol_number TEXT, deadline TEXT, posted TEXT, naics TEXT, "
        "set_aside TEXT, poc_name TEXT, "
        "poc_email TEXT, poc_phone TEXT, poc_title TEXT, "
        "poc_secondary_name TEXT, poc_secondary_email TEXT, "
        "poc_secondary_phone TEXT, poc_secondary_title TEXT, "
        "agency TEXT, subtier TEXT, office TEXT, url TEXT, "
        "description_prefix TEXT)")
    for n in range(11):
        conn.execute(
            "INSERT INTO notices VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"id{n:032d}",
             "Financial Management - Financial Improvement Audit Remediation",
             "Special Notice", "FA701426_FMF_FIAR", "2026-09-01",
             f"2026-0{(n % 8) + 1}-01", "541211", "", "Christian Mancha",
             "christian.mancha@us.af.mil", "", "Contract Specialist",
             "", "", "", "",
             "DEPT OF THE AIR FORCE", "DEPT OF THE AIR FORCE", "FA7014", "",
             "audit remediation"))
    rows = store_opportunities(conn, exact_terms=["audit remediation"])
    assert len(rows) == 1, (
        f"one requirement posted eleven times became {len(rows)} "
        f"opportunities")
    assert rows[0].contacts and rows[0].contacts[0]["published"]
    conn.close()
