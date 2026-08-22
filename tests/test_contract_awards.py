"""SAM Contract Awards adapter: recompete extraction + fact ingestion. No network."""

import json

from agents.reports.facts import build_fact_pack
from tools.api.base import REGISTRY, SourceQuery
from tools.api.contract_awards import ContractAwardsSource, _records

PAYLOAD = {
    "totalRecords": 2,
    "opportunitiesData": [
        {"awardDetails": {
            "dates": {"currentCompletionDate": "2026-11-30"},
            "awardeeData": {"awardeeHeader": {"awardeeName": "IncumbentCo"}},
            "contractIds": {"piid": "PIID123"},
            "purchaserData": {"purchaserHeader": {"subtierName": "DISA"}}}},
        {"awardDetails": {
            "dates": {"currentCompletionDate": "2027-03-15"},
            "awardeeData": {"awardeeHeader": {"legalBusinessName": "IncumbentCo"}},
            "contractIds": {"piid": "PIID456"},
            "purchaserData": {"purchaserHeader": {"subtierName": "CISA"}}}},
    ],
}


def _disable_usaspending_async_download(monkeypatch):
    """Keep legacy fallback tests deterministic and network-free."""

    import tools.api.contract_awards as ca

    monkeypatch.setattr(
        ca,
        "_try_usaspending_async_download",
        lambda **_kwargs: {
            "status": "failed",
            "error": "disabled in legacy fallback test",
            "fingerprint": "test-fingerprint",
            "attempts": [],
        },
    )


def test_registered():
    assert "contract_awards" in {s.name for s in REGISTRY.all()}


def test_records_finder_is_shape_agnostic():
    assert len(_records(PAYLOAD)) == 2
    assert len(_records({"totalRecords": 0})) == 0


def test_enrich_extracts_recompetes_and_ranks_incumbents(monkeypatch):
    import tools.api.contract_awards as ca
    captured = {}
    def fake_get(url, params=None, **kw):
        captured.update(params)
        return PAYLOAD
    monkeypatch.setattr(ca, "get_json", fake_get)
    out = ContractAwardsSource(api_key="k").enrich(SourceQuery(naics_codes=["541512", "513210"]))
    assert captured["naicsCode"] == "541512~513210"
    assert captured["currentCompletionDate"].startswith("[")
    assert out["recompetes"][0]["awardee"] == "IncumbentCo"
    assert out["recompetes"][0]["agency"] == "DISA"
    assert out["recompetes"][0]["record_source"] == "sam.gov_contract_awards"
    assert out["incumbents"][0] == {"name": "IncumbentCo", "contracts": 2,
                                    "next_expiry": "2026-11-30"}
    assert out["source_mode"] == "live_sam_contract_awards_api"
    assert out["fallback_used"] is False


def test_enrich_maps_current_official_award_summary_shape(monkeypatch):
    import tools.api.contract_awards as ca

    official = {
        "totalRecords": "1",
        "awardSummary": [{
            "contractId": {
                "subtier": {"code": "2050", "name": "INTERNAL REVENUE SERVICE"},
                "piid": "TIRNO26C0001",
                "modificationNumber": "0",
            },
            "coreData": {
                "title": "Enterprise network observability support",
                "solicitationId": "2032H5-26-R-0001",
                "federalOrganization": {"contractingInformation": {
                    "contractingSubtier": {
                        "code": "2050", "name": "INTERNAL REVENUE SERVICE"
                    }
                }},
                "productOrServiceInformation": {
                    "principalNaics": [{
                        "code": "541512",
                        "name": "Computer Systems Design Services",
                    }]
                },
            },
            "awardDetails": {
                "dates": {
                    "currentCompletionDate": "2027-02-28 00:00:00.000",
                },
                "dollars": {"baseAndAllOptionsValue": "12500000.00"},
                "awardeeData": {"awardeeHeader": {
                    "awardeeName": "OBSERVABILITY INC.",
                }},
            },
        }],
    }
    captured = {}

    def fake_get(_url, *, params, **_kwargs):
        captured.update(params)
        return official

    monkeypatch.setattr(ca, "get_json", fake_get)
    out = ContractAwardsSource(api_key="k").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    row = out["recompetes"][0]
    assert row == {
        "awardee": "OBSERVABILITY INC.",
        "piid": "TIRNO26C0001",
        "completion": "2027-02-28",
        "agency": "INTERNAL REVENUE SERVICE",
        "description": "Enterprise network observability support",
        "solicitation_id": "2032H5-26-R-0001",
        "amount": 12_500_000.0,
        "amount_basis": "Base + all options",
        "naics": "541512",
        "record_source": "sam.gov_contract_awards",
    }
    assert captured["modificationNumber"] == "0"
    assert captured["includeSections"] == "contractId,coreData,awardDetails"


def test_sam_contract_awards_pages_base_record_census(monkeypatch):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    def row(piid, completion):
        return {
            "contractId": {
                "subtier": {"code": "2050", "name": "IRS"},
                "piid": piid,
            },
            "coreData": {"title": "Network observability support"},
            "awardDetails": {
                "dates": {"currentCompletionDate": completion},
                "awardeeData": {"awardeeHeader": {
                    "awardeeName": f"Incumbent {piid}",
                }},
            },
        }

    pages = {
        page: [
            row(f"A-{page * 100 + index:03d}", "2027-01-01")
            for index in range(100)
        ]
        for page in range(3)
    }
    offsets = []

    def fake_get(_url, *, params, **_kwargs):
        offsets.append(params["offset"])
        return {
            "totalRecords": "300",
            "awardSummary": pages[params["offset"]],
        }

    monkeypatch.setattr(ca, "get_json", fake_get)
    monkeypatch.setattr(ca, "MAX_SYNC_PAGES", 5)
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)

    out = ContractAwardsSource(api_key="k").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    assert offsets == [0, 1, 2]
    assert len({row["piid"] for row in out["recompetes"]}) == 300
    assert out["recompetes"][0]["piid"] == "A-000"
    assert out["recompetes"][-1]["piid"] == "A-299"
    assert out["records_retrieved"] == 300
    assert out["pages_retrieved"] == 3
    assert out["complete"] is True
    assert out["partial"] is False
    assert out["_provenance"]["status"] == "complete"


def test_sam_contract_awards_names_page_cap_as_partial(monkeypatch):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setattr(ca, "MAX_SYNC_PAGES", 1)
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "totalRecords": "2",
        "awardSummary": [{
            "contractId": {"piid": "ONLY-FIRST-PAGE"},
            "coreData": {"title": "Packet monitoring support"},
            "awardDetails": {
                "dates": {"currentCompletionDate": "2027-01-31"},
                "awardeeData": {"awardeeHeader": {
                    "awardeeName": "First Page Incumbent",
                }},
            },
        }],
    })

    out = ContractAwardsSource(api_key="k").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    assert out["records_retrieved"] == 1
    assert out["total_records"] == 2
    assert out["complete"] is False
    assert out["partial"] is True
    assert out["_provenance"]["status"] == "partial"
    assert "1 of 2 base award records" in out["_provenance"]["public_detail"]


def test_missing_key_uses_named_live_usaspending_fallback(monkeypatch):
    from tools.api.usaspending import UsaSpendingSource

    _disable_usaspending_async_download(monkeypatch)
    monkeypatch.setattr(UsaSpendingSource, "expiring_awards", lambda self, code, **kw: [{
        "award_id": "USA-1",
        "recipient": "Fallback Incumbent",
        "amount": 2500000,
        "awarding_agency": "Department of Homeland Security",
        "awarding_sub_agency": "Cybersecurity and Infrastructure Security Agency",
        "end_date": "2027-01-31",
        "description": "Network observability platform",
        "naics": code,
        "url": "https://www.usaspending.gov/award/USA-1",
    }])
    src = ContractAwardsSource(api_key=None)
    src._api_key = None
    out = src.enrich(SourceQuery(naics_codes=["541512"]))

    assert out["fallback_used"] is True
    assert out["source_mode"] == "live_usaspending_fallback"
    assert out["freshness"] == "live"
    assert out["recompetes"][0]["record_source"] == "usaspending.gov"
    assert out["recompetes"][0]["agency"] == (
        "Cybersecurity and Infrastructure Security Agency"
    )
    assert out["source_attempts"][0] == {
        "source": "SAM.gov Contract Awards API",
        "status": "failed",
        "error": "SAM_GOV_API_KEY not set",
    }
    assert "top 100 awards" in out["provenance"]["coverage"]
    assert out["partial"] is True
    assert out["attempted_naics_lanes"] == 1
    assert out["successful_naics_lanes"] == 1
    assert out["failed_naics_lanes"] == 0
    assert out["omitted_naics_lanes"] == 0


def test_sam_429_falls_back_dedupes_and_preserves_primary_failure(
    monkeypatch
):
    import tools.api.contract_awards as ca
    from tools.api.usaspending import UsaSpendingSource

    _disable_usaspending_async_download(monkeypatch)
    monkeypatch.setattr(ca, "get_json", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("HTTP 429 — daily quota exhausted"))
    ))

    def fallback(self, code, **kwargs):
        return [{
            "award_id": "SHARED-PIID",
            "recipient": "Official Fallback Co",
            "amount": 900000,
            "awarding_agency": "General Services Administration",
            "awarding_sub_agency": None,
            "end_date": "2027-03-01",
            "description": "Secure network services",
            "naics": code,
            "url": "https://www.usaspending.gov/award/SHARED",
        }]

    monkeypatch.setattr(UsaSpendingSource, "expiring_awards", fallback)
    out = ContractAwardsSource(api_key="k").enrich(
        SourceQuery(naics_codes=["541512", "541519"])
    )

    assert len(out["recompetes"]) == 1
    assert out["incumbents"][0]["contracts"] == 1
    assert out["source_attempts"][0]["error"] == "HTTP 429 — daily quota exhausted"
    assert out["source_attempts"][2]["successful_naics_lanes"] == 2
    assert out["source_attempts"][2]["status"] == "partial"
    assert [attempt["status"] for attempt in out["_provenance"]["attempts"]] == [
        "failed",
        "failed",
        "partial",
    ]
    assert out["provenance"]["role"] == "fallback"


def test_usaspending_fallback_marks_partial_and_counts_omitted_lanes(
    monkeypatch,
):
    import tools.api.contract_awards as ca
    from tools.api.usaspending import UsaSpendingSource

    _disable_usaspending_async_download(monkeypatch)
    monkeypatch.setattr(ca, "get_json", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("HTTP 429 — quota"))
    ))
    monkeypatch.setattr(ca, "MAX_FALLBACK_NAICS_LANES", 6)

    def fallback(self, code, **kwargs):
        if code == "2":
            raise RuntimeError("lane unavailable")
        return [{
            "award_id": f"AWARD-{code}",
            "recipient": f"Incumbent {code}",
            "end_date": "2027-03-01",
            "awarding_agency": "General Services Administration",
            "naics": code,
            "url": f"https://www.usaspending.gov/award/{code}",
        }]

    monkeypatch.setattr(UsaSpendingSource, "expiring_awards", fallback)
    out = ContractAwardsSource(api_key="k").enrich(SourceQuery(
        naics_codes=[str(index) for index in range(1, 9)]
    ))

    assert out["partial"] is True
    assert out["requested_naics_lanes"] == 8
    assert out["attempted_naics_lanes"] == 6
    assert out["successful_naics_lanes"] == 5
    assert out["failed_naics_lanes"] == 1
    assert out["omitted_naics_lanes"] == 2
    assert out["source_attempts"][2] == {
        "source": "USAspending award search",
        "status": "partial",
        "attempted_naics_lanes": 6,
        "successful_naics_lanes": 5,
        "failed_naics_lanes": 1,
        "omitted_naics_lanes": 2,
    }


def test_usaspending_fallback_screens_all_normal_client_naics_lanes(
    monkeypatch,
):
    import tools.api.contract_awards as ca
    from tools.api.usaspending import UsaSpendingSource

    _disable_usaspending_async_download(monkeypatch)
    monkeypatch.setattr(ca, "get_json", lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("HTTP 403 — invalid key"))
    ))
    monkeypatch.setattr(
        UsaSpendingSource,
        "expiring_awards",
        lambda self, code, **kwargs: [],
    )
    lanes = [f"5415{index:02d}" for index in range(9)]
    out = ContractAwardsSource(api_key="k").enrich(
        SourceQuery(naics_codes=lanes)
    )

    assert out["requested_naics_lanes"] == 9
    assert out["attempted_naics_lanes"] == 9
    assert out["successful_naics_lanes"] == 9
    assert out["omitted_naics_lanes"] == 0
    assert out["partial"] is True


def test_missing_sam_key_prefers_complete_usaspending_award_download(
    monkeypatch,
):
    import tools.api.contract_awards as ca
    from tools.api.usaspending import UsaSpendingSource

    monkeypatch.setattr(
        ca,
        "_try_usaspending_async_download",
        lambda **_kwargs: {
            "status": "complete",
            "source_mode": "live_usaspending_async_download",
            "coverage_status": "complete_within_boundary",
            "coverage_boundary": {
                "start_date": "2007-10-01",
                "end_date": "2026-08-14",
                "date_type": "action_date",
            },
            "retrieval_mode": "live",
            "retrieved_at": "2026-08-14T12:00:00+00:00",
            "downloaded_rows": 1,
            "duplicates_removed": 0,
            "known_expired_removed": 0,
            "outside_window_removed": 0,
            "unknown_end_retained": 0,
            "row_count_verified": True,
            "attempts": [{
                "source": "USAspending award download job",
                "status": "complete",
                "count": 1,
            }],
            "records": [{
                "contract_award_unique_key": "CONT_AWARD_TEST",
                "award_id": "PIID-CENSUS",
                "recipient": "Census Incumbent",
                "end_date": "2027-04-01",
                "awarding_agency": "General Services Administration",
                "awarding_sub_agency": None,
                "amount": 4_000_000,
                "amount_basis": "Current total award value",
                "description": "Enterprise network services",
                "naics": "541512",
                "url": "https://www.usaspending.gov/award/CONT_AWARD_TEST/",
                "award_type_code": "A",
                "expiration_state": "active_in_window",
                "expiration_basis": "period_of_performance_current_end_date",
            }],
            "provenance": {
                "download_endpoint": (
                    "https://api.usaspending.gov/api/v2/download/search/"
                ),
                "coverage": "bounded official award summaries",
            },
        },
    )
    monkeypatch.setattr(
        UsaSpendingSource,
        "expiring_awards",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("synchronous fallback must remain last resort")
        ),
    )
    source = ContractAwardsSource(api_key=None)
    source._api_key = None

    out = source.enrich(SourceQuery(naics_codes=["541512"]))

    assert out["source_mode"] == "live_usaspending_async_download"
    assert out["complete"] is True
    assert out["partial"] is False
    assert out["coverage_status"] == "complete_within_boundary"
    assert out["recompetes"][0]["piid"] == "PIID-CENSUS"
    assert out["_provenance"]["status"] == "complete"


def test_async_extract_downloads_ready_complete_census(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    notes = []
    captured = {}

    def fake_get_json(_url, *, params, **_kwargs):
        captured["create_params"] = dict(params)
        return {
            "presignedUrl": (
                "https://api.sam.gov/contract-awards/v1/download?"
                "api_key=REPLACE_WITH_API_KEY&token=export-1"
            ),
            "exportToken": "export-1",
            "message": "File generation is in progress.",
        }

    def fake_get_bytes(url, **_kwargs):
        captured["download_url"] = url
        return json.dumps(PAYLOAD).encode("utf-8")

    monkeypatch.setattr(ca, "get_json", fake_get_json)
    monkeypatch.setattr(ca, "get_bytes", fake_get_bytes)
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", notes.append)

    out = ContractAwardsSource(api_key="secret-key").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    assert captured["create_params"]["format"] == "json"
    assert captured["create_params"]["api_key"] == "secret-key"
    assert "api_key=secret-key" in captured["download_url"]
    assert notes == [
        "contract_awards_extract_create",
        "contract_awards_extract_download",
    ]
    assert out["source_mode"] == "live_sam_contract_awards_extract"
    assert out["records_retrieved"] == 2
    assert out["complete"] is True
    assert out["partial"] is False
    assert out["_provenance"]["status"] == "complete"


def test_pending_extract_resumes_without_creating_a_second_export(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    params = {
        "naicsCode": "541512",
        "currentCompletionDate": "[08/14/2026,02/13/2028]",
        "modificationNumber": "0",
        "includeSections": "contractId,coreData,awardDetails",
    }
    public = {**params, "format": "json"}
    fingerprint = ca._query_fingerprint(public)
    ca._write_extract_state(fingerprint, {
        "status": "pending",
        "query": public,
        "presigned_url_template": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=resume-me"
        ),
        "export_token": "resume-me",
    })
    calls = {"create": 0, "download": 0}
    notes = []

    def should_not_create(*_args, **_kwargs):
        calls["create"] += 1
        raise AssertionError("pending export must be resumed before recreation")

    def ready_download(_url, **_kwargs):
        calls["download"] += 1
        return json.dumps(PAYLOAD).encode("utf-8")

    monkeypatch.setattr(ca, "get_json", should_not_create)
    monkeypatch.setattr(ca, "get_bytes", ready_download)
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", notes.append)

    result = ca._try_contract_awards_extract(
        api_key="resume-secret",
        search_params=params,
    )

    assert result["status"] == "complete"
    assert calls == {"create": 0, "download": 1}
    assert notes == ["contract_awards_extract_download"]
    assert result["attempts"][0]["status"] == "resumed"


def test_pending_extract_falls_back_with_explicit_partial_provenance(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota
    from tools.api.usaspending import UsaSpendingSource

    _disable_usaspending_async_download(monkeypatch)
    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    descriptor = {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=pending-1"
        ),
        "exportToken": "pending-1",
        "message": "File generation is in progress.",
    }

    def fake_get_json(_url, *, params, **_kwargs):
        if params.get("format") == "json":
            return descriptor
        raise RuntimeError("synchronous SAM pull unavailable")

    monkeypatch.setattr(ca, "get_json", fake_get_json)
    monkeypatch.setattr(
        ca,
        "get_bytes",
        lambda *_args, **_kwargs: json.dumps(descriptor).encode("utf-8"),
    )
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(
        UsaSpendingSource,
        "expiring_awards",
        lambda _self, code, **_kwargs: [{
            "award_id": "USA-PENDING-1",
            "recipient": "Fallback Incumbent",
            "end_date": "2027-03-01",
            "awarding_agency": "General Services Administration",
            "naics": code,
            "url": "https://www.usaspending.gov/award/USA-PENDING-1",
        }],
    )

    out = ContractAwardsSource(api_key="secret-key").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    assert out["source_mode"] == "live_usaspending_fallback"
    assert out["extract_status"] == "pending"
    assert out["extract_fingerprint"]
    assert out["partial"] is True
    assert out["_provenance"]["status"] == "partial"
    assert "award census pending" in out["_provenance"]["public_detail"]
    assert any(
        attempt["status"] == "pending"
        for attempt in out["source_attempts"]
    )


def test_complete_extract_cache_requires_no_keyed_request(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    params = {
        "naicsCode": "541512",
        "currentCompletionDate": "[08/14/2026,02/13/2028]",
        "modificationNumber": "0",
        "includeSections": "contractId,coreData,awardDetails",
    }
    fingerprint = ca._query_fingerprint({**params, "format": "json"})
    _state_path, payload_path = ca._cache_paths(fingerprint)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_bytes(json.dumps(PAYLOAD).encode("utf-8"))
    ca._write_extract_state(fingerprint, {
        "status": "complete",
        "query": {**params, "format": "json"},
        "payload_file": payload_path.name,
        "record_count": 2,
        "retrieved_at": "2026-08-14T12:00:00+00:00",
    })
    monkeypatch.setattr(
        ca,
        "get_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete cache must not create an export")
        ),
    )
    monkeypatch.setattr(
        ca,
        "get_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete cache must not download an export")
        ),
    )

    result = ca._try_contract_awards_extract(
        api_key="unused-secret",
        search_params=params,
    )

    assert result["status"] == "complete"
    assert result["retrieval_mode"] == "cache"
    assert len(result["records"]) == 2


def test_extract_response_without_advertised_total_is_not_complete(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "awardSummary": [PAYLOAD["opportunitiesData"][0]],
    })

    result = ca._try_contract_awards_extract(
        api_key="secret-key",
        search_params={"naicsCode": "541512"},
    )

    assert result["status"] == "failed"
    assert "omitted an advertised census total" in result["attempts"][0]["error"]


def test_sync_page_without_advertised_total_is_partial(monkeypatch):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setattr(ca, "_try_contract_awards_extract", lambda **_kwargs: {
        "status": "failed",
        "attempts": [],
    })
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "awardSummary": [PAYLOAD["opportunitiesData"][0]],
    })
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)

    out = ContractAwardsSource(api_key="secret-key").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    assert out["records_retrieved"] == 1
    assert out["total_records"] is None
    assert out["complete"] is False
    assert out["partial"] is True
    assert out["_provenance"]["status"] == "partial"


def test_complete_cache_without_advertised_total_is_not_trusted(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    params = {"naicsCode": "541512"}
    fingerprint = ca._query_fingerprint({**params, "format": "json"})
    _state_path, payload_path = ca._cache_paths(fingerprint)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_bytes(json.dumps({
        "awardSummary": [PAYLOAD["opportunitiesData"][0]],
    }).encode("utf-8"))
    ca._write_extract_state(fingerprint, {
        "status": "complete",
        "record_count": 1,
    })
    monkeypatch.setattr(ca, "MAX_EXPORT_POLLS", 0)
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=rebuild-cache"
        ),
        "exportToken": "rebuild-cache",
    })

    result = ca._try_contract_awards_extract(
        api_key="secret-key",
        search_params=params,
    )

    assert result["status"] == "pending"
    assert result.get("retrieval_mode") != "cache"


def test_extract_fingerprint_and_cache_never_persist_api_key(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    secret = "never-write-this-key"
    cache = tmp_path / "extract-cache"
    monkeypatch.setenv("LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR", str(cache))
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "MAX_EXPORT_POLLS", 0)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            f"api_key={secret}&token=secure-token"
        ),
        "exportToken": "secure-token",
    })

    result = ca._try_contract_awards_extract(
        api_key=secret,
        search_params={
            "api_key": secret,
            "naicsCode": "541512",
            "currentCompletionDate": "[08/14/2026,02/13/2028]",
        },
    )

    assert result["status"] == "pending"
    assert secret not in result["fingerprint"]
    assert cache.exists()
    assert all(secret.encode("utf-8") not in path.read_bytes() for path in cache.iterdir())
    state_text = next(cache.glob("*.state.json")).read_text(encoding="utf-8")
    assert "REPLACE_WITH_API_KEY" in state_text
    assert '"api_key"' not in state_text


def test_complete_census_discloses_recompete_storage_cap(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    monkeypatch.setattr(ca, "MAX_STORED_RECOMPETES", 1)
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=cap-test"
        ),
        "exportToken": "cap-test",
    })
    monkeypatch.setattr(
        ca,
        "get_bytes",
        lambda *_args, **_kwargs: json.dumps(PAYLOAD).encode("utf-8"),
    )

    out = ContractAwardsSource(api_key="secret-key").enrich(
        SourceQuery(naics_codes=["541512"])
    )

    assert out["complete"] is True
    assert out["total_records"] == 2
    assert out["recompetes_total"] == 2
    assert out["recompetes_stored"] == 1
    assert out["recompetes_truncated"] is True
    assert "stored first 1 of 2" in out["recompetes_truncated_note"]
    assert "1 of 2 matched award rows stored" in out["_provenance"]["public_detail"]
    assert out["_provenance"]["record_count"] == 2


def test_export_is_not_complete_when_advertised_total_exceeds_rows(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    descriptor = {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=short-export"
        ),
        "exportToken": "short-export",
    }
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: descriptor)
    monkeypatch.setattr(ca, "get_bytes", lambda *_args, **_kwargs: json.dumps({
        "totalRecords": 2,
        "awardSummary": [PAYLOAD["opportunitiesData"][0]],
    }).encode("utf-8"))

    result = ca._try_contract_awards_extract(
        api_key="secret-key",
        search_params={"naicsCode": "541512"},
    )

    assert result["status"] == "pending"
    assert any(
        attempt.get("status") == "partial"
        and attempt.get("count") == 1
        and attempt.get("advertised_total") == 2
        for attempt in result["attempts"]
    )
    state_path, payload_path = ca._cache_paths(result["fingerprint"])
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "pending"
    assert not payload_path.exists()


def test_export_without_advertised_total_fails_closed(monkeypatch, tmp_path):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=no-total"
        ),
        "exportToken": "no-total",
    })
    monkeypatch.setattr(ca, "get_bytes", lambda *_args, **_kwargs: json.dumps({
        "awardSummary": [PAYLOAD["opportunitiesData"][0]],
    }).encode("utf-8"))

    result = ca._try_contract_awards_extract(
        api_key="secret-key",
        search_params={"naicsCode": "541512"},
    )

    assert result["status"] == "failed"
    assert "omitted its advertised census total" in result["message"]
    state_path, payload_path = ca._cache_paths(result["fingerprint"])
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "failed"
    assert not payload_path.exists()


def test_untrusted_extract_download_host_never_receives_api_key(
    monkeypatch,
    tmp_path,
):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "presignedUrl": (
            "https://attacker.example/download?"
            "api_key=REPLACE_WITH_API_KEY&token=steal"
        ),
        "exportToken": "steal",
    })
    downloads = []
    monkeypatch.setattr(
        ca,
        "get_bytes",
        lambda url, **_kwargs: downloads.append(url) or b"",
    )

    result = ca._try_contract_awards_extract(
        api_key="never-send-this",
        search_params={"naicsCode": "541512"},
    )

    assert result["status"] == "failed"
    assert result["attempts"][0]["error"] == (
        "untrusted SAM Contract Awards download URL"
    )
    assert downloads == []
    assert not list((tmp_path / "extract-cache").glob("*.state.json"))


def test_nonquota_extract_download_failure_is_terminal(monkeypatch, tmp_path):
    import tools.api.contract_awards as ca
    import tools.api.sam_quota as sam_quota

    monkeypatch.setenv(
        "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
        str(tmp_path / "extract-cache"),
    )
    monkeypatch.setattr(sam_quota, "guard", lambda _purpose: True)
    monkeypatch.setattr(sam_quota, "note_call", lambda _purpose: 1)
    monkeypatch.setattr(ca, "get_json", lambda *_args, **_kwargs: {
        "presignedUrl": (
            "https://api.sam.gov/contract-awards/v1/download?"
            "api_key=REPLACE_WITH_API_KEY&token=expired"
        ),
        "exportToken": "expired",
    })
    monkeypatch.setattr(
        ca,
        "get_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("HTTP 403 expired export token")
        ),
    )

    result = ca._try_contract_awards_extract(
        api_key="secret-key",
        search_params={"naicsCode": "541512"},
    )

    assert result["status"] == "failed"
    assert result["attempts"][-1]["status"] == "failed"
    state_path, _payload_path = ca._cache_paths(result["fingerprint"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "expired export token" in state["message"]


def test_complete_usaspending_fallback_ranks_full_untruncated_census(
    monkeypatch,
):
    import tools.api.contract_awards as ca

    monkeypatch.setattr(ca, "MAX_STORED_RECOMPETES", 1)
    monkeypatch.setattr(ca, "_try_usaspending_async_download", lambda **_kwargs: {
        "status": "complete",
        "source_mode": "live_usaspending_async_download",
        "coverage_status": "complete_within_boundary",
        "retrieval_mode": "live",
        "attempts": [],
        "records": [
            {
                "contract_award_unique_key": "CONT_A",
                "award_id": "A",
                "recipient": "Near Incumbent",
                "end_date": "2026-09-01",
                "naics": "541512",
            },
            {
                "contract_award_unique_key": "CONT_B",
                "award_id": "B",
                "recipient": "Later Incumbent",
                "end_date": "2027-09-01",
                "naics": "541512",
            },
        ],
        "provenance": {},
    })
    source = ContractAwardsSource(api_key=None)
    source._api_key = None

    out = source.enrich(SourceQuery(naics_codes=["541512"]))

    assert len(out["recompetes"]) == 1
    assert out["recompetes_total"] == 2
    assert out["recompetes_truncated"] is True
    assert {row["name"] for row in out["incumbents"]} == {
        "Near Incumbent",
        "Later Incumbent",
    }


def test_fact_pack_ingests_contract_completion_as_unconfirmed_signal():
    searches = {"results": {"usaspending.gov": [], "contract_awards": {
        "recompetes": [{"awardee": "IncumbentCo", "piid": "P1",
                        "completion": "2026-11-30", "agency": "DISA"}],
        "incumbents": [{"name": "IncumbentCo", "contracts": 2, "next_expiry": "2026-11-30"}],
    }}}
    pack = build_fact_pack("Testco", searches=searches, qualify_report={"candidates": []})
    texts = " | ".join(f.text for f in pack.facts)
    assert "Contract-completion signal" in texts and "IncumbentCo" in texts
    assert "not a confirmed recompete" in texts
    assert "Recompete signal" not in texts
    assert any(f.kind == "opportunity" for f in pack.facts)
    assert any(f.kind == "competitor" for f in pack.facts)
