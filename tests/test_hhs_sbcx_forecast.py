"""Focused contract tests for the public HHS SBCX forecast adapter.

Census doctrine (2026-08-18): the ``?filter=`` parameter matches title,
NAICS, and operating division only, so the empty-filter census is the ONE
ingest path; the bounded probe path was killed the same day. These tests
pin the single-request pull, the local truth screens (published-only,
historical, implausible-timing), the disclosed result cap, and the
failure provenance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from agents.schemas import ForecastRecord, RawOpportunity
from tools.api.base import SourceQuery
import tools.api.forecasts.hhs_sbcx as sbcx
from tools.api.forecasts.hhs_sbcx import HhsSbcxSource, map_record, probe_url
from tools.api.forecasts.store import record_payload


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hhs_sbcx.json").read_text(
        encoding="utf-8"
    )
)
RECORDS = FIXTURE["records"]


def _source(*, records=None, error=None):
    calls = []
    rows = RECORDS if records is None else records

    def fake_fetch(url, **kwargs):
        assert kwargs["headers"]["Accept"] == "application/json"
        assert kwargs["timeout"] > 0
        assert kwargs["retries"] >= 1
        params = parse_qs(urlparse(url).query, keep_blank_values=True)
        calls.append(params["filter"][0])
        if error is not None:
            raise error
        return rows

    return HhsSbcxSource(fetch_json=fake_fetch), calls


def test_map_record_preserves_stated_forecast_evidence():
    observed = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
    record = map_record(RECORDS[1], retrieved_at=observed)

    assert isinstance(record, ForecastRecord)
    assert not isinstance(record, RawOpportunity)
    assert record.source == "hhs_sbcx"
    assert record.source_id == "hhs-fcst-cce8dc39-e7e0-44cb-981c-380134d1d000"
    assert record.agency == "Department of Health and Human Services"
    assert record.component == "Indian Health Service (IHS)"
    assert record.estimated_value_range == ">= $250K and < $700K"
    assert record.anticipated_solicitation == "2026-11"
    assert record.anticipated_award == "2027-01"
    assert record.set_aside == "Small Business Set-Aside"
    assert record.incumbent_stated == "Red Heritage"
    assert record.predecessor_contract_id == "75H71320P00105"
    assert record.url == sbcx.APP_FORECAST_URL
    assert record.retrieved_at == observed
    assert record.data_as_of is None
    assert record.source_fields["programPocEmail"] == (
        "ihs.program.office@example.invalid"
    )
    assert record.source_fields["coEmail"] == "ihs.acquisition@example.invalid"
    payload = record_payload(record)
    assert payload["estimated_value_lower"] == 250_000.0
    assert payload["estimated_value_upper"] == 700_000.0


def test_map_record_preserves_a_source_symbolic_value_band():
    row = dict(RECORDS[1])
    row["totalContractRange"] = "> $25K and < $250K"

    record = map_record(row)

    assert record is not None
    assert record.estimated_value_range == "> $25K and < $250K"
    payload = record_payload(record)
    assert payload["estimated_value_lower"] == 25_000.0
    assert payload["estimated_value_upper"] == 250_000.0


def test_active_discovery_contract_is_structurally_empty():
    source, calls = _source()
    assert source.search(SourceQuery(keywords=["cloud"])) == []
    assert calls == []


def test_census_is_one_empty_filter_request_deduped_and_published_only():
    source, calls = _source()
    records = source.forecasts()

    # ONE request, empty filter: the census is the whole pull.
    assert calls == [""]
    assert len(records) == 4
    assert len({record.source_id for record in records}) == 4
    assert all(isinstance(record, ForecastRecord) for record in records)
    assert all(record.forecast_status == "PUBLISHED" for record in records)
    assert all("draft" not in record.title.casefold() for record in records)
    assert source.last_provenance["classification"] == (
        "agency forecast; never an active notice"
    )
    assert source.last_provenance["mode"] == "live_sbcx_full_census"
    # A non-public row is a public-contract drift signal, so the snapshot is
    # still useful but cannot advance disappearance/change state.
    assert source.last_provenance["status"] == "partial"
    assert source.last_provenance["complete"] is False
    assert "Census pull: one empty-filter request" in " ".join(
        source.last_provenance["limitations"]
    )
    assert source.last_provenance["omitted_terms"] == []
    assert len(source.last_provenance["sha256"]) == 64


def test_query_terms_never_narrow_the_census():
    # The filter parameter cannot see descriptions (measured 2026-08-18),
    # so a query narrows NOTHING at the wire: matching happens downstream.
    source, calls = _source()
    records = source.forecasts(
        SourceQuery(keywords=["data platform"], limit=20)
    )
    assert calls == [""]
    assert len(records) == 4  # the limit did not shrink the census


def test_census_fetch_failure_raises_after_recording_failed_provenance():
    source, _calls = _source(error=ConnectionError("dns blackhole"))

    with pytest.raises(RuntimeError, match="HHS SBCX forecast pull failed"):
        source.forecasts()
    assert source.last_provenance["status"] == "failed"
    assert source.last_provenance["complete"] is False
    assert source.last_provenance["record_count"] == 0
    assert "dns blackhole" in " ".join(
        source.last_provenance["limitations"]
    )


def test_non_list_census_payload_is_a_named_contract_drift_failure():
    source, _calls = _source(records={"html": "shell"})
    with pytest.raises(RuntimeError, match="non-list payload"):
        source.forecasts()
    assert source.last_provenance["status"] == "failed"


def test_result_cap_is_disclosed_and_marks_snapshot_partial(monkeypatch):
    # Only the guardrail cap bounds the census; a caller's SourceQuery
    # default page size must never shrink it (the 100-row trap, measured
    # live 2026-08-18 on the first census pull).
    monkeypatch.setattr(sbcx, "MAX_TOTAL_RECORDS", 2)
    source, _calls = _source()
    records = source.forecasts(SourceQuery(limit=100))

    assert len(records) == 2
    assert source.last_provenance["status"] == "partial"
    assert source.last_provenance["complete"] is False
    assert "kept first 2 of" in " ".join(
        source.last_provenance["limitations"]
    )


def test_published_historical_rows_are_not_forward_forecast_signals():
    historical = dict(RECORDS[0])
    historical.update(
        uuid="past-row",
        title="Legacy network support",
        targetSolicitationYear=2022,
        targetSolicitationMonth=1,
        targetAwardYear=2022,
        targetAwardMonth=6,
    )
    source, _calls = _source(records=[historical, RECORDS[0]])

    records = source.forecasts()

    assert [record.source_id for record in records] == [
        "hhs-fcst-" + RECORDS[0]["uuid"]
    ]
    assert "excluded 1 published historical rows" in " ".join(
        source.last_provenance["limitations"]
    )


def test_implausible_public_timing_is_excluded_and_disclosed():
    malformed = dict(RECORDS[0])
    malformed.update(
        uuid="future-data-error",
        title="Laboratory network data error",
        targetSolicitationYear=2151,
        targetAwardYear=2206,
    )
    source, _calls = _source(records=[malformed, RECORDS[0]])

    records = source.forecasts()

    assert [record.source_id for record in records] == [
        "hhs-fcst-" + RECORDS[0]["uuid"]
    ]
    assert "outside 1990 through" in " ".join(
        source.last_provenance["limitations"]
    )


def test_healthcheck_rejects_html_shell_shape():
    source, calls = _source(records={"html": "shell"})
    ok, detail = source.healthcheck()

    assert ok is False
    assert "non-list" in detail
    assert calls == [sbcx.HEALTHCHECK_TERM]


def test_healthcheck_accepts_only_a_nonempty_published_list():
    source, calls = _source(records=RECORDS[:4])
    ok, detail = source.healthcheck()

    assert ok is True
    assert "4 published rows" in detail
    assert calls == [sbcx.HEALTHCHECK_TERM]
    assert probe_url(sbcx.HEALTHCHECK_TERM).endswith("filter=public")

    empty, _calls = _source(records=[])
    assert empty.healthcheck()[0] is False

    draft, _calls = _source(records=[RECORDS[-1]])
    assert draft.healthcheck()[0] is False


@pytest.mark.skipif(
    os.environ.get("LILA_RUN_LIVE_SOURCE_TESTS") != "1",
    reason="set LILA_RUN_LIVE_SOURCE_TESTS=1 for the bounded official probe",
)
def test_live_official_health_probe_is_a_published_json_list():
    ok, detail = HhsSbcxSource().healthcheck()
    assert ok, detail
    assert "official HHS SBCX store reachable" in detail
