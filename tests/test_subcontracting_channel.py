"""Subcontracting channel measurement machinery (operator order 2026-08-05):
pagination exhaustion, normalization, floor guards, join key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import subcontracting_directory as sd
from tools import subnet_census as sc

REPO = Path(__file__).resolve().parents[1]


def _page(titles):
    rows = "".join(
        '<tr><td headers="view-body-table-column" class="views-field">'
        f'<span class="subnet_title"><a href="/opportunity/x{i}" hreflang="en">{t}</a></span>'
        f'<span class="subnet_business_name">Org {i} </span><p>Desc {i}</p></td>'
        '<td headers="view-field-subnet-closing-timestamp-table-column">9/1/2026 </td>'
        '<td headers="view-field-subnet-start-date-table-column">10/1/2026 </td>'
        '<td headers="view-field-subnet-place-performance-table-column">Texas </td>'
        '<td headers="view-field-subnet-naics-table-column">334118: Computers </td>'
        '<td headers="view-nothing-table-column">Pat Doe 555-0100 </td></tr>'
        for i, t in enumerate(titles))
    return f"<table><tbody>{rows}</tbody></table>"


# 1 · pagination exhaustion: the clamp rule, never the pager widget --------- #
def test_exhaustion_is_content_clamp_not_pager():
    a = _page(["Alpha", "Beta"])
    b = _page(["Gamma", "Delta"])
    assert not sc.exhausted(a, b)
    assert sc.exhausted(b, _page(["Gamma", "Delta"]))   # clamp repeat
    assert sc.exhausted(b, _page([]))                    # empty page


# 2 · normalization: verbatim fields from the table rendering --------------- #
def test_listing_normalization_captures_every_field():
    rows = sc.parse_listing_page(_page(["KVM Matrix Refresh"]))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "KVM Matrix Refresh"
    assert row["posting_organization"] == "Org 0"
    assert row["closing_date"] == "9/1/2026"
    assert row["naics"] == "334118: Computers"
    assert row["point_of_contact"] == "Pat Doe 555-0100"
    assert row["source_url"].startswith("https://legacy.sba.gov/opportunity/")


# 3 · floor guards: fail closed on broken fetches --------------------------- #
def test_floor_guards_fail_closed():
    with pytest.raises(ValueError, match="credibility floor"):
        sc.guard_row_floor([{"title": "one"}])
    with pytest.raises(ValueError, match="credibility floor"):
        sd.guard_directory_floor([{"name_key": "x"}] * 10)
    assert len(sd.guard_directory_floor([{}] * 5000)) == 5000


# 4 · join key: exact, parent, distinctive containment; generic never ------- #
def test_join_key_tiers_and_generic_containment_refusal():
    from agents.golden_press.rollups import normalize_entity
    keys = sd.directory_keys([
        {"name_key": normalize_entity("CARAHSOFT TECHNOLOGY CORP."),
         "parent_name_key": None,
         "prime_name": "CARAHSOFT TECHNOLOGY CORP."},
        {"name_key": normalize_entity("ARGON ST, INC."),
         "parent_name_key": normalize_entity("THE BOEING COMPANY"),
         "prime_name": "ARGON ST, INC."},
        {"name_key": normalize_entity(
            "FOUR COUNTY ELECTRIC POWER ASSOCIATION"),
         "parent_name_key": None, "prime_name": "FOUR COUNTY"},
    ])
    exact = sd.match_prime("Carahsoft Technology Corp", keys)
    assert exact and exact["tier"] == "exact"
    parent = sd.match_prime("The Boeing Company", keys)
    assert parent and parent["tier"] == "parent"
    assert sd.match_prime("FOUR LLC", keys) is None, \
        "single-generic-token containment must never match"
    contain = sd.match_prime(
        "Carahsoft Technology Corporation of Virginia", keys)
    assert contain and contain["tier"] == "containment"


# committed artifacts stay parseable and above their floors ------------------ #
def test_committed_artifacts_reload_above_floors():
    census = [json.loads(l) for l in
              (REPO / "data/reference/subnet_census.jsonl").read_text(
                  encoding="utf-8").splitlines()]
    sc.guard_row_floor(census)
    assert all(r.get("source_url") for r in census), "provenance on every row"
    directory = sd.load_directory(REPO)
    assert all(r.get("source_url") for r in directory)
    assert all(r.get("liaison_email") is None for r in directory), \
        "the FY24 format carries no contacts; absence stays an explicit fact"
