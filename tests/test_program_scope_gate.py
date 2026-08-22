"""PROGRAM sources obey the client boundary before and after retrieval."""

from __future__ import annotations

import pytest

import tools.api.grants_gov as grants_gov
from agents.reports.source_coverage import (
    SCOPE_EXCLUDED,
    coverage_from_sweep,
)
from run_searches import (
    _load_program_engagement_scope,
    _scoped_program_payload,
    attempt_row,
)
from tools.api import SourceQuery
from tools.api.grants_gov import GrantsGovSource


class _ProgramSource:
    def __init__(
        self,
        identity: str,
        records: list[dict],
        provenance: dict | None = None,
    ):
        self.identity = identity
        self.records = records
        self.provenance = provenance or {}
        self.calls = 0

    def enrich(self, _query: SourceQuery) -> dict:
        self.calls += 1
        return {
            "records": list(self.records),
            "_provenance": {
                "source": self.identity,
                "status": "success",
                "mode": "live_official_source",
                "retrieved_at": "2026-07-20T12:00:00+00:00",
                "records_received": len(self.records),
                **self.provenance,
            },
        }


def _query() -> SourceQuery:
    return SourceQuery(keywords=["network"], limit=100)


def test_riverbed_civilian_skips_every_cataloged_defense_program_lane():
    scope = _load_program_engagement_scope("riverbed")
    assert scope is not None and scope.preset == "civilian"

    for identity in (
        "dsip_topics",
        "darpa_opportunities",
        "dod_budget_exhibits",
    ):
        source = _ProgramSource(identity, [{
            "record_id": f"{identity}:1",
            "agency": "Department of Defense",
        }])
        payload = _scoped_program_payload(
            identity, source, _query(), scope)

        assert source.calls == 0
        assert payload["records"] == []
        assert payload["scope"]["excluded_before_retrieval"] is True
        assert payload["scope"]["records_retrieved"] is None
        provenance = payload["_provenance"]
        assert provenance["mode"] == "scope-excluded"
        assert provenance["record_count"] is None
        assert provenance["attempts"] == [{
            "source": identity,
            "status": "not-run",
            "count": None,
            "retrieved_at": None,
            "data_as_of": None,
            "error": None,
        }]


def test_scope_exclusion_is_not_counted_as_attempted_or_returned():
    scope = _load_program_engagement_scope("riverbed")
    source = _ProgramSource("darpa_opportunities", [])
    payload = _scoped_program_payload(
        "darpa_opportunities", source, _query(), scope)
    attempt = attempt_row(
        "darpa_opportunities", payload,
        "scope-excluded before retrieval", 0.0, None,
    )
    assert attempt["ok"] is True
    assert attempt["scope_excluded"] is True
    assert "count" not in attempt

    coverage = coverage_from_sweep({"results": {
        "_attempts": [attempt],
        "darpa_opportunities": payload,
    }})
    row = next(
        row for row in coverage["lanes"]
        if row["source"] == "darpa_opportunities"
    )
    assert row["status"] == SCOPE_EXCLUDED
    assert "Not retrieved" in row["detail"]
    assert coverage["counts"]["attempted"] == 0
    assert coverage["counts"]["returned"] == 0
    assert coverage["counts"]["scope_excluded"] == 1


def test_mark43_all_federal_pulls_and_retains_defense_program_rows():
    scope = _load_program_engagement_scope("mark43")
    assert scope is not None and scope.preset == "all_federal"
    source = _ProgramSource("darpa_opportunities", [{
        "record_id": "darpa:network-1",
        "agency": "Department of Defense",
        "title": "Network research program",
    }])

    payload = _scoped_program_payload(
        "darpa_opportunities", source, _query(), scope)

    assert source.calls == 1
    assert [row["record_id"] for row in payload["records"]] == [
        "darpa:network-1"]
    assert payload["records"][0]["scope_basis"] == "preset:all_federal@v1"
    assert payload["scope"]["records_included"] == 1
    assert payload["scope"]["records_excluded"] == 0


def test_generic_program_rows_use_sanctioned_record_level_scope_filter():
    scope = _load_program_engagement_scope("riverbed")
    source = _ProgramSource("reginfo_unified_agenda", [
        {"record_id": "reginfo:dod", "toptier_code": "097"},
        {"record_id": "reginfo:treasury", "toptier_code": "020"},
    ])

    payload = _scoped_program_payload(
        "reginfo_unified_agenda", source, _query(), scope)

    assert source.calls == 1
    assert [row["record_id"] for row in payload["records"]] == [
        "reginfo:treasury"]
    assert "in-scope:Treasury" in payload["records"][0]["scope_basis"]
    assert payload["scope"]["records_evaluated"] == 2
    assert payload["scope"]["records_included"] == 1
    assert payload["scope"]["records_excluded"] == 1
    assert payload["_provenance"]["records_scope_excluded"] == 1


@pytest.mark.parametrize("identity", ["grants", "sbir"])
def test_shared_program_sources_obey_civilian_and_all_federal_scope(identity):
    rows = [{
        "record_id": f"{identity}:dod",
        "agency": "Department of Defense",
        "title": "Defense network program",
    }, {
        "record_id": f"{identity}:hhs",
        "agency": "Department of Health and Human Services",
        "title": "Civilian network program",
    }]

    civilian = _scoped_program_payload(
        identity,
        _ProgramSource(identity, rows),
        _query(),
        _load_program_engagement_scope("riverbed"),
    )
    assert [row["record_id"] for row in civilian["records"]] == [
        f"{identity}:hhs",
    ]

    all_federal = _scoped_program_payload(
        identity,
        _ProgramSource(identity, rows),
        _query(),
        _load_program_engagement_scope("mark43"),
    )
    assert [row["record_id"] for row in all_federal["records"]] == [
        f"{identity}:dod", f"{identity}:hhs",
    ]


def test_real_grants_search2_hhs_shape_survives_riverbed_scope(monkeypatch):
    """Search2's code is the sanctioned identity; its display name is not."""
    monkeypatch.setattr(grants_gov, "post_json", lambda *_args, **_kwargs: {
        "data": {
            "hitCount": 1,
            "oppHits": [{
                "id": 358800,
                "number": "HHS-26-NET-001",
                "title": "Civilian health network modernization",
                "agencyCode": "HHS",
                "agencyName": "Health & Human Services",
                "oppStatus": "posted",
                "closeDate": "09/30/2026",
            }],
        },
    })

    payload = _scoped_program_payload(
        "grants",
        GrantsGovSource(),
        _query(),
        _load_program_engagement_scope("riverbed"),
    )

    assert [row["record_id"] for row in payload["records"]] == [
        "grants_gov:358800",
    ]
    row = payload["records"][0]
    assert row["agency"] == "HHS"
    assert row["agency_name"] == "Health & Human Services"
    assert "in-scope:HHS" in row["scope_basis"]


def test_grants_partial_collection_survives_program_scope_gate(monkeypatch):
    """Retrieval limits are partial coverage, not a ranked selection cap."""
    monkeypatch.setattr(grants_gov, "MAX_KEYWORDS", 1)
    monkeypatch.setattr(grants_gov, "post_json", lambda *_args, **_kwargs: {
        "data": {
            "hitCount": 1,
            "oppHits": [{
                "id": 358801,
                "number": "HHS-26-NET-002",
                "title": "Civilian health telemetry modernization",
                "agencyCode": "HHS",
                "agencyName": "Health & Human Services",
                "oppStatus": "forecasted",
            }],
        },
    })

    payload = _scoped_program_payload(
        "grants",
        GrantsGovSource(),
        SourceQuery(keywords=["network", "telemetry"], limit=100),
        _load_program_engagement_scope("riverbed"),
    )

    assert [row["record_id"] for row in payload["records"]] == [
        "grants_gov:358801",
    ]
    provenance = payload["_provenance"]
    assert provenance["status"] == "partial"
    assert provenance["partial"] is True
    assert "keyword lanes capped" in provenance["limitations"]
    assert "truncated" not in provenance


def test_riverbed_bounded_program_scope_rejects_unresolved_agency_rows():
    scope = _load_program_engagement_scope("riverbed")
    source = _ProgramSource("foreign_assistance", [{
        "record_id": "foreign-assistance:unknown",
        "agency": "U.S. Government Foreign Assistance",
        "title": "Network modernization budget request",
    }])

    payload = _scoped_program_payload(
        "foreign_assistance", source, _query(), scope)

    assert payload["records"] == []
    assert payload["scope"]["records_unresolved_excluded"] == 1
    assert payload["_provenance"]["records_scope_unresolved_excluded"] == 1


def test_mark43_unbounded_scope_retains_unresolved_program_agency_rows():
    scope = _load_program_engagement_scope("mark43")
    source = _ProgramSource("foreign_assistance", [{
        "record_id": "foreign-assistance:unknown",
        "agency": "U.S. Government Foreign Assistance",
        "title": "Network modernization budget request",
    }])

    payload = _scoped_program_payload(
        "foreign_assistance", source, _query(), scope)

    assert [row["record_id"] for row in payload["records"]] == [
        "foreign-assistance:unknown",
    ]
    assert payload["scope"]["records_unresolved_excluded"] == 0


def test_program_cap_detail_survives_scope_and_reaches_source_coverage():
    scope = _load_program_engagement_scope("mark43")
    source = _ProgramSource(
        "reginfo_unified_agenda",
        [
            {"record_id": "reginfo:one", "toptier_code": "020"},
            {"record_id": "reginfo:two", "toptier_code": "069"},
        ],
        provenance={
            "candidate_total": 5,
            "selected_count": 2,
            "truncated": True,
            "selection_order": "soonest planned agenda action",
            "public_detail": "Existing official-source caveat",
        },
    )

    payload = _scoped_program_payload(
        "reginfo_unified_agenda", source, SourceQuery(limit=2), scope)

    provenance = payload["_provenance"]
    assert provenance["candidate_total"] == 5
    assert provenance["selected_count"] == 2
    assert provenance["truncated"] is True
    assert provenance["selection_order"] == "soonest planned agenda action"
    expected = (
        "Existing official-source caveat · Selection cap: selected 2 of 5 "
        "matched records · ranking basis: soonest planned agenda action"
    )
    assert provenance["public_detail"] == expected

    attempt = attempt_row(
        "reginfo_unified_agenda", payload, "2 PROGRAM signals", 0.0, None)
    coverage = coverage_from_sweep({"results": {
        "_attempts": [attempt],
        "reginfo_unified_agenda": payload,
    }})
    row = next(
        row for row in coverage["lanes"]
        if row["source"] == "reginfo_unified_agenda"
    )
    assert row["detail"] == expected


def test_missing_scope_is_explicitly_unscoped_and_does_not_guess():
    source = _ProgramSource("dsip_topics", [{
        "record_id": "dsip:network-1",
        "agency": "Department of Defense",
    }])

    payload = _scoped_program_payload(
        "dsip_topics", source, _query(), None)

    assert source.calls == 1
    assert payload["scope"]["basis"] == "UNSCOPED"
    assert payload["records"][0]["scope_basis"] == "UNSCOPED"


def test_malformed_scope_fails_named_on_the_single_config_load(monkeypatch):
    calls = []

    def malformed(client_name):
        calls.append(client_name)
        raise ValueError("preset cannot be mixed with explicit departments")

    monkeypatch.setattr(
        "tools.relevance.scope.load_engagement_scope", malformed)
    with pytest.raises(
        RuntimeError,
        match="invalid engagement scope for 'broken-client'",
    ):
        _load_program_engagement_scope("broken-client")
    assert calls == ["broken-client"]
