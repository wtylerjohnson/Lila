"""Full award schema, renewal clock, vehicle, rival paper (A3, 2026-08-18).

Offline doctrine: fetchers are injected fixtures, stores write to tmp
paths, no network. Detail truth pinned 2026-08-18: parent rides
detail["parent_award"], subaward_count gates the subawards call, and the
generated id encodes parent identity as the fallback.
"""

from __future__ import annotations

import json
from datetime import date

from tools.api import award_schema

AS_OF = date(2026, 8, 18)
GID = "CONT_AWD_50310219F0220_5000_NNG15SD26B_8000"
PARENT_GID = "CONT_IDV_NNG15SD26B_8000"

DETAIL = {
    "piid": "50310219F0220",
    "type": "C",
    "type_description": "DELIVERY ORDER",
    "description": "VARONIS DATA SECURITY SOFTWARE AND MAINTENANCE",
    "date_signed": "2019-09-25",
    "total_obligation": 1234567.0,
    "subaward_count": 2,
    "total_subaward_amount": 200000.0,
    "recipient": {"recipient_name": "THUNDERCAT TECHNOLOGY, LLC"},
    "period_of_performance": {
        "start_date": "2019-09-25",
        "end_date": "2026-09-24",
        "potential_end_date": "2027-09-24",
    },
    "parent_award": {
        "generated_unique_award_id": PARENT_GID,
        "agency_name": "National Aeronautics and Space Administration",
    },
    "awarding_agency": {
        "toptier_agency": {"name": "Department of Justice"},
        "subtier_agency": {"name": "Federal Bureau of Investigation"},
        "office_agency_name": "PROCUREMENT SECTION",
    },
    "funding_agency": {
        "toptier_agency": {"name": "Department of Justice"},
        "subtier_agency": {"name": "Federal Bureau of Investigation"},
        "office_agency_name": "FBI FUNDING OFFICE",
    },
}
PARENT_DETAIL = {
    "piid": "NNG15SD26B",
    "description": "NASA SEWP V CATALOG",
    "type_description": "GWAC",
    "period_of_performance": {"end_date": "2030-04-30",
                              "potential_end_date": "2030-10-31"},
}
SUBAWARD_PAGE = {
    "results": [
        {"subaward_number": "SUB-1", "recipient_name": "SMALL SUB LLC",
         "amount": 120000.0, "action_date": "2024-01-15",
         "description": "install services"},
        {"subaward_number": "SUB-2", "recipient_name": "OTHER SUB INC",
         "amount": 80000.0, "action_date": "2024-06-01",
         "description": "training"},
    ],
    "page_metadata": {"hasNext": False},
}


def _fake_get(url, **kwargs):
    if url.endswith(f"/awards/{GID}/"):
        return DETAIL
    if url.endswith(f"/awards/{PARENT_GID}/"):
        return PARENT_DETAIL
    raise AssertionError(f"unexpected GET {url}")


def _fake_post(url, *, json=None, **kwargs):  # noqa: A002 - httpx shape
    assert url.endswith("/subawards/")
    assert json["award_id"] == GID
    return SUBAWARD_PAGE


def test_gid_parse_carries_parent_identity_and_none_sentinels():
    parsed = award_schema.parse_gid(GID)
    assert parsed == {"piid": "50310219F0220", "agency": "5000",
                      "parent_piid": "NNG15SD26B", "parent_agency": "8000"}
    assert award_schema.parent_gid_for(parsed) == PARENT_GID
    bare = award_schema.parse_gid("CONT_AWD_W91_9700_-NONE-_-NONE-")
    assert bare["parent_piid"] is None
    assert award_schema.parent_gid_for(bare) is None


def test_schema_row_is_the_full_award_schema():
    cache: dict = {}
    row = award_schema.schema_row(
        {"generated_id": GID, "side": "client", "label": "Varonis"},
        as_of=AS_OF, parent_cache=cache,
        fetch_get=_fake_get, fetch_post=_fake_post)
    assert row["pop_current_end"] == "2026-09-24"
    assert row["pop_potential_end"] == "2027-09-24"
    assert row["awarding"]["office"] == "PROCUREMENT SECTION"
    assert row["funding"]["office"] == "FBI FUNDING OFFICE"
    # renewal clock derives from the explicit as-of, never the wall clock
    assert row["renewal"]["days_to_current_end"] == 37
    assert row["renewal"]["days_to_potential_end"] == 402
    assert row["renewal"]["option_headroom_days"] == 365
    # vehicle: SEWP V by the house prefix rule, parent description joined
    assert row["vehicle"]["vehicle_class"] == "SEWP V"
    assert row["vehicle"]["parent_gid"] == PARENT_GID
    assert row["vehicle"]["parent"]["description"] == "NASA SEWP V CATALOG"
    assert row["vehicle"]["parent"]["pop_current_end"] == "2030-04-30"
    # subawards fetched because declared_count is nonzero
    assert row["subawards"]["declared_count"] == 2
    assert [s["recipient"] for s in row["subawards"]["rows"]] == [
        "SMALL SUB LLC", "OTHER SUB INC"]


def test_zero_subaward_count_never_calls_the_subawards_endpoint():
    def refuse_post(url, **kwargs):
        raise AssertionError("subawards endpoint must not be called")

    detail = dict(DETAIL, subaward_count=0)

    def fake_get(url, **kwargs):
        return detail if GID in url else PARENT_DETAIL

    row = award_schema.schema_row(
        {"generated_id": GID, "side": "rival", "label": "Netwrix"},
        as_of=AS_OF, parent_cache={},
        fetch_get=fake_get, fetch_post=refuse_post)
    assert row["subawards"] == {"rows": [], "declared_count": 0}


def test_store_build_and_rival_paper_group_by_agency_and_office(tmp_path):
    entries = [
        {"generated_id": GID, "side": "rival", "label": "Netwrix"},
    ]
    path, payload = award_schema.build_award_schema(
        "testclient", entries, as_of=AS_OF, out_dir=tmp_path,
        fetch_get=_fake_get, fetch_post=_fake_post, pause_s=0.0)
    assert path.exists()
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["counts"]["with_pop_potential_end"] == 1
    assert disk["counts"]["with_parent_idv"] == 1
    assert disk["counts"]["unique_parents"] == 1

    rp_path, paper = award_schema.build_rival_paper(
        "testclient", payload, out_dir=tmp_path,
        fpds=lambda rival: {"window_days": 5, "actions": [],
                            "status": "returned", "error": None,
                            "retrieved_at": "2026-08-18T00:00:00Z"})
    assert rp_path.exists()
    agency = paper["by_agency"]["Department of Justice"]
    rows = agency["PROCUREMENT SECTION"]
    assert rows[0]["oem"] == "Netwrix"
    # ThunderCat carrying Netwrix-described paper resolves reseller
    assert rows[0]["carrier"] == "reseller"
    assert rows[0]["vehicle_class"] == "SEWP V"
    assert paper["counts"]["per_carrier"] == {"reseller": 1}
    assert "Netwrix" in paper["fpds_recent_actions"]
    assert "bounded" in paper["fpds_window_note"]


def test_carrier_resolution_matches_ledger_semantics():
    assert award_schema.resolve_carrier("EVERFOX LLC", "Everfox") == "direct"
    assert award_schema.resolve_carrier(
        "CARAHSOFT TECHNOLOGY CORP", "BigID") == "reseller"
    assert award_schema.resolve_carrier(
        "UNALAKLEET INVESTMENTS LLC", "Forcepoint") == "other-carrier"


def test_error_award_rides_the_row_and_never_raises(tmp_path):
    def broken_get(url, **kwargs):
        raise RuntimeError("fixture outage")

    path, payload = award_schema.build_award_schema(
        "testclient", [{"generated_id": GID, "side": "client",
                        "label": "Varonis"}],
        as_of=AS_OF, out_dir=tmp_path,
        fetch_get=broken_get, fetch_post=_fake_post, pause_s=0.0)
    row = payload["awards"][GID]
    assert "RuntimeError" in row["error"]
    assert payload["counts"]["errors"] == 1
