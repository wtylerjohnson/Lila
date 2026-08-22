"""Focused buyer-map collision tests for named Riverbed products."""

import pytest

from tools.api import incumbent_buyers
from tools.capability import load_profile
from tools.relevance.engine import score_text
from tools.relevance.taxonomy import load_taxonomy


FALSE_STEELHEAD_ROWS = [
    {
        "Award ID": "FISH-RESTORE",
        "Recipient Name": "SEQUOIA ECOLOGICAL CONSULTING, INC.",
        "Description": (
            "BATTLE CREEK SALMON AND STEELHEAD RESTORATION PROJECT"
        ),
    },
    {
        "Award ID": "FISH-SURVEY",
        "Recipient Name": "SP CRAMER & ASSOCIATES INC",
        "Description": (
            "PERFORM STEELHEAD SPAWNING AND STRANDING SURVEYS"
        ),
    },
    {
        "Award ID": "FISH-FEED",
        "Recipient Name": "MOORE-CLARK U.S.A., INC.",
        "Description": (
            "SPECIALIZED FISH FEED FOR JUVENILE SALMON AND STEELHEAD "
            "AT THE NIMBUS FISH HATCHERY"
        ),
    },
    {
        "Award ID": "FISH-FEED-HYPHEN",
        "Recipient Name": "AQUACULTURE SUPPLY CO.",
        "Description": "STEELHEAD FISH-FEED SUPPLIES FOR A REARING SITE",
    },
    {
        "Award ID": "NAME-COLLISION",
        "Recipient Name": "STEELHEAD ENTERPRISES, LLC",
        "Description": "OROCF000501 C76",
    },
]

REAL_STEELHEAD_ROWS = [
    {
        "Award ID": "REAL-APPLIANCE",
        "Recipient Name": "ANACAPA MICRO PRODUCTS, INC.",
        "Description": "RIVERBED STEELHEAD APPLIANCES AND MAINTENANCE",
    },
    {
        "Award ID": "REAL-WAN",
        "Recipient Name": "BLUE TECH INC.",
        "Description": "RIVERBED STEELHEAD WAN OPTIMIZATION",
    },
    {
        "Award ID": "REAL-RENEWAL",
        "Recipient Name": "FCN, INC.",
        "Description": "STEELHEAD MAINTENANCE SERVICE RENEWAL",
    },
]


def _award(row, index):
    return {
        **row,
        "Award Amount": float(1000 + index),
        "Awarding Agency": "Department of the Interior",
        "Awarding Sub Agency": "Test Buying Office",
        "End Date": None,
        "Contract Award Type": "DELIVERY ORDER",
        "NAICS": "541512",
        "PSC": "DA01",
        "generated_internal_id": f"CONT_AWD_{row['Award ID']}",
    }


def test_buyer_map_vetoes_named_product_collisions_but_keeps_footprint(
        monkeypatch):
    profile = load_profile("riverbed")
    assert profile is not None

    steelhead_rows = [
        _award(row, index)
        for index, row in enumerate(
            FALSE_STEELHEAD_ROWS + REAL_STEELHEAD_ROWS)
    ]
    client_row = _award({
        "Award ID": "CLIENT-FOOTPRINT",
        "Recipient Name": "RIVERBED TECHNOLOGY LLC",
        "Description": "LABOR",
    }, 99)

    def fake_search(term, *, subawards, years_back, limit=60):
        del years_back, limit
        if subawards:
            return []
        if term == "SteelHead":
            return steelhead_rows
        if term == "Riverbed":
            return [client_row]
        return []

    monkeypatch.setattr(incumbent_buyers, "_search", fake_search)
    notices = [
        {
            "source_id": "FALSE-FISH-NOTICE",
            "title": "Steelhead spawning surveys",
            "raw_payload": {
                "agency": "Department of the Interior",
                "subtier": "Notice Buying Office",
                "description_snippet": "salmon habitat restoration",
                "type": "Notice of Intent",
            },
        },
        {
            "source_id": "REAL-MAINTENANCE-NOTICE",
            "title": "SteelHead maintenance renewal",
            "raw_payload": {
                "agency": "Department of the Interior",
                "subtier": "Notice Buying Office",
                "description_snippet": "WAN appliance support",
                "type": "Notice of Intent",
            },
        },
    ]
    result = incumbent_buyers.build_buyer_map(
        profile, years_back=3, budget_seconds=30, sam_notices=notices)

    records = [record for buyer in result["buyers"]
               for record in buyer["records"]]
    ids = {record["award_id"] for record in records}
    assert ids == {
        "CLIENT-FOOTPRINT",
        "REAL-APPLIANCE",
        "REAL-WAN",
        "REAL-RENEWAL",
    }
    footprint = next(
        record for record in records
        if record["award_id"] == "CLIENT-FOOTPRINT"
    )
    assert footprint["matched_products"] == ["Riverbed"]
    assert all(record["amount_basis"] == "obligated_to_date"
               for record in records)
    assert footprint["naics"] == "541512"
    assert footprint["psc"] == "DA01"
    assert footprint["award_structure"] == "DELIVERY ORDER"
    assert "Contract Award Type" in incumbent_buyers.AWARD_FIELDS
    notice_ids = {
        notice["notice_id"] for buyer in result["buyers"]
        for notice in buyer["sole_source_notices"]
    }
    assert notice_ids == {"REAL-MAINTENANCE-NOTICE"}


def test_riverbed_taxonomy_v2_aligns_collision_kills_with_profile():
    profile = load_profile("riverbed")
    taxonomy = load_taxonomy("riverbed")
    assert profile is not None and taxonomy is not None
    assert taxonomy.version == 2

    required = {
        "salmon restoration",
        "steelhead restoration",
        "salmon habitat restoration",
        "steelhead habitat restoration",
        "salmon spawning",
        "steelhead spawning",
        "spawning and stranding surveys",
        "salmon survey",
        "salmon surveys",
        "steelhead survey",
        "steelhead surveys",
        "fish feed",
        "fish-feed",
        "fish hatchery",
        "steelhead enterprises",
    }
    profile_excluded = {
        term.casefold() for term in profile.capability_terms.excluded
    }
    taxonomy_excluded = {rule.term.casefold() for rule in taxonomy.exclude}
    assert required <= profile_excluded
    assert required <= taxonomy_excluded

    false_texts = [
        row["Description"] + " " + row["Recipient Name"]
        for row in FALSE_STEELHEAD_ROWS
    ]
    for text in false_texts:
        spans, killed = score_text(text, taxonomy)
        assert not any(span.term == "SteelHead" for span in spans)
        assert killed

    for row in REAL_STEELHEAD_ROWS:
        spans, killed = score_text(row["Description"], taxonomy)
        assert any(span.term == "SteelHead" for span in spans)
        assert not killed


def test_buyer_map_preserves_core_evidence_beyond_legacy_180_char_cut(
        monkeypatch):
    profile = load_profile("imerit")
    assert profile is not None
    description = "X" * 190 + " data labeling and AI evaluation services"

    def fake_search(term, *, subawards, years_back, limit=60):
        del years_back, limit
        if subawards or term != "Snorkel AI":
            return []
        return [_award({
            "Award ID": "LATE-CORE-SPAN",
            "Recipient Name": "AUGUST SCHELL ENTERPRISES, INC.",
            "Description": description,
        }, 1)]

    monkeypatch.setattr(incumbent_buyers, "_search", fake_search)
    result = incumbent_buyers.build_buyer_map(
        profile, years_back=3, budget_seconds=30)

    records = [record for buyer in result["buyers"]
               for record in buyer["records"]]
    assert len(records) == 1
    assert records[0]["description"] == description
    assert "data labeling" in records[0]["description"]


def test_buyer_map_discloses_failed_award_and_subaward_terms(monkeypatch):
    profile = load_profile("imerit")
    assert profile is not None

    def failing_search(term, *, subawards, years_back, limit=60):
        del years_back, limit
        if term == profile.client_name and not subawards:
            raise RuntimeError("private provider detail must not be published")
        if term == profile.client_name and subawards:
            raise RuntimeError("private provider detail must not be published")
        return []

    monkeypatch.setattr(incumbent_buyers, "_search", failing_search)
    result = incumbent_buyers.build_buyer_map(
        profile, years_back=3, budget_seconds=30)

    assert {tuple(sorted(row.items())) for row in result["failed_terms"]} >= {
        (("lane", "awards"), ("term", profile.client_name)),
        (("lane", "subawards"), ("term", profile.client_name)),
    }
    assert "private provider detail" not in str(result)


def test_buyer_map_search_accepts_explicit_empty_results(monkeypatch):
    monkeypatch.setattr(
        incumbent_buyers,
        "post_json",
        lambda *args, **kwargs: {"results": []},
    )

    assert incumbent_buyers._search(
        "iMerit", subawards=False, years_back=3
    ) == []


def test_buyer_map_search_rejects_false_clean_response(monkeypatch):
    monkeypatch.setattr(
        incumbent_buyers,
        "post_json",
        lambda *args, **kwargs: {"message": "maintenance"},
    )

    try:
        incumbent_buyers._search("iMerit", subawards=False, years_back=3)
    except ValueError as exc:
        assert "list-valued results" in str(exc)
    else:
        raise AssertionError("maintenance response must not become zero results")


@pytest.mark.parametrize(
    ("subawards", "message"),
    [
        (False, "award result omitted identity"),
        (True, "subaward result omitted identity"),
    ],
)
def test_buyer_map_search_rejects_placeholder_rows(
    monkeypatch, subawards, message
):
    monkeypatch.setattr(
        incumbent_buyers,
        "post_json",
        lambda *args, **kwargs: {"results": [{"message": "maintenance"}]},
    )

    with pytest.raises(ValueError, match=message):
        incumbent_buyers._search(
            "iMerit", subawards=subawards, years_back=3
        )
