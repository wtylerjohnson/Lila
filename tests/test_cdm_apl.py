"""CDM APL and DEFEND primes store (A6, 2026-08-18).

Offline doctrine: the workbook is a generated fixture, USASpending is an
injected callable, stores write to tmp paths. Column honesty is the core
pin: manufacturer-column presence is APL membership; a mention inside
another manufacturer's row never claims presence (the Corelight row that
names Proofpoint in its description, measured on the real April 2025
file, is the collision this guards).
"""

from __future__ import annotations

import json

import openpyxl
import pytest

from tools.api import cdm_apl


@pytest.fixture()
def apl_file(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Product List"
    ws.append(["CDM APL:  Product List"])
    ws.append(["Last updated: 4/25/2025"])
    ws.append(["Product Manufacturer", "Product Family",
               "Manufacturer Part Number", "Product Description",
               "GSA Schedule Contract Number", "NASA SEWP Contract Number",
               "Contract Holder", "Product Type"])
    ws.append(["Varonis Systems, Inc.", "DatAdvantage", "ACAA-1001",
               "DatAdvantage Cloud - Azure Apps", "GS-35F-0511T",
               "NNG15SC16B", "EC America, Inc.", "Term License"])
    ws.append(["Varonis Systems, Inc.", "DatAlert", "DA-100",
               "DatAlert monitoring", "GS-35F-0511T", "NNG15SC16B",
               "EC America, Inc.", "Term License"])
    ws.append(["Corelight, Inc.", "Sensor", "SBSS-1",
               "Feed integration for Proofpoint ETP", "47QSWA18D008F",
               "NNG15SC03B", "Carahsoft Technology Corp.", "Subscription"])
    path = tmp_path / "apl.xlsx"
    wb.save(path)
    return path


def test_manufacturer_column_is_membership_mentions_are_not(apl_file):
    parsed = cdm_apl.parse_apl(
        apl_file, ["Varonis", "Netwrix", "Proofpoint"])
    t = parsed["targets"]
    assert parsed["product_rows"] == 3
    assert t["Varonis"]["on_apl"] is True
    assert t["Varonis"]["manufacturer_row_count"] == 2
    assert t["Varonis"]["first_row"] == 4
    assert t["Varonis"]["contract_holders"] == ["EC America, Inc."]
    sample = t["Varonis"]["sample_rows"][0]
    assert sample["row"] == 4
    assert "'Product List'!A4" in sample["row_link"]
    assert sample["cells"]["Product Manufacturer"] == "Varonis Systems, Inc."
    # absent rival is a clean false
    assert t["Netwrix"]["on_apl"] is False
    assert t["Netwrix"]["manufacturer_row_count"] == 0
    # named inside another manufacturer's description: never membership
    assert t["Proofpoint"]["on_apl"] is False
    assert t["Proofpoint"]["mention_only_row_count"] == 1


def test_defend_groups_parse_both_phrasings_and_name_gaps():
    pages = iter([
        {"results": [
            {"Award ID": "47QFRA24F0005", "Recipient Name":
                "CACI, INC. - FEDERAL", "Award Amount": 100.0,
             "Description": "CDM DEFEND GROUP A BRIDGE TASK ORDER",
             "generated_internal_id": "CONT_AWD_A"},
            {"Award ID": "47QFRA19F0001", "Recipient Name": "CACI INC",
             "Award Amount": 50.0,
             "Description": "CDM DEFEND D TASK ORDER",
             "generated_internal_id": "CONT_AWD_D"},
            {"Award ID": "IRRELEVANT", "Recipient Name": "SOMEONE",
             "Award Amount": 10.0,
             "Description": "GROUP B THERAPY SERVICES",
             "generated_internal_id": "CONT_AWD_X"},
        ], "page_metadata": {"hasNext": False}},
        {"results": [], "page_metadata": {"hasNext": False}},
        {"results": [], "page_metadata": {"hasNext": False}},
    ])
    out = cdm_apl.defend_primes(post=lambda body: next(pages))
    assert out["groups"]["A"]["current_prime"] == "CACI, INC. - FEDERAL"
    assert out["groups"]["D"]["current_prime"] == "CACI INC"
    # non-DEFEND GROUP text never enters; unmatched groups are named gaps
    assert set(out["groups"]) == {"A", "D"}
    assert out["groups_without_award_rows"] == ["B", "C", "E", "F"]


def test_store_carries_the_freeze_quote_and_row_links(apl_file, tmp_path):
    defend = {"groups": {}, "groups_without_award_rows": list("ABCDEF"),
              "note": "fixture", "source": "fixture", "source_url": "x",
              "retrieved_at": "2026-08-18T00:00:00Z"}
    path, payload = cdm_apl.build_store(
        "testclient", ["Varonis", "Netwrix"], out_dir=tmp_path,
        apl_path=apl_file, defend=defend)
    assert path.exists()
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["apl"]["submission_freeze_quote"] == (
        "The APL is not accepting new submissions for the indefinite "
        "future.")
    assert disk["apl"]["file_sha256"]
    assert disk["targets"]["Varonis"]["on_apl"] is True
    assert disk["targets"]["Netwrix"]["on_apl"] is False
    assert "cannot be added" in disk["apl"]["competitive_fact"]


def test_award_record_gap_carries_the_tiered_web_fact():
    pages = iter([
        {"results": [], "page_metadata": {"hasNext": False}},
        {"results": [], "page_metadata": {"hasNext": False}},
        {"results": [], "page_metadata": {"hasNext": False}},
    ])
    out = cdm_apl.defend_primes(post=lambda body: next(pages))
    assert out["groups"] == {}
    d = out["web_reported_primes"]["D"]
    assert d["prime"] == "Booz Allen Hamilton"
    assert d["source_url"].startswith("https://www.meritalk.com/")
    assert "press-reported" in d["evidence_tier"]
