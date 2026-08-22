"""USAspending asynchronous award-summary fallback. No network."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta

import httpx
import pytest


def _zip_rows(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("PrimeAwardSummaries_2026-08-14.csv", output.getvalue())
    return raw.getvalue()


def _download_writer(payload: bytes, *, on_download=None):
    def write(_url, destination, *, max_bytes, **_kwargs):
        if on_download is not None:
            on_download()
        if len(payload) > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} byte boundary")
        destination.write_bytes(payload)
        return len(payload)

    return write


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "contract_award_unique_key": "CONT_AWARD_1",
        "award_id_piid": "PIID-1",
        "parent_award_id_piid": "",
        "award_type_code": "A",
        "total_obligated_amount": "100",
        "current_total_value_of_award": "150",
        "potential_total_value_of_award": "200",
        "award_base_action_date": "2020-01-01",
        "award_latest_action_date": "2026-01-01",
        "period_of_performance_current_end_date": "2027-02-01",
        "ordering_period_end_date": "",
        "recipient_uei": "UEI1",
        "recipient_name": "Current Vendor",
        "awarding_agency_name": "Department of Defense",
        "awarding_sub_agency_name": "Defense Information Systems Agency",
        "naics_code": "541512",
        "naics_description": "Computer Systems Design Services",
        "prime_award_base_transaction_description": "Network services",
        "usaspending_permalink": "/award/CONT_AWARD_1/",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "url",
    [
        "http://api.usaspending.gov/csv_downloads/job.zip",
        "https://evil.example/csv_downloads/job.zip",
        "//127.0.0.1/internal",
        "https://user:pass@api.usaspending.gov/csv_downloads/job.zip",
        "https://api.usaspending.gov:444/csv_downloads/job.zip",
    ],
)
def test_job_urls_reject_untrusted_origins(url):
    import tools.api.usaspending_award_download as download

    with pytest.raises(download.AwardDownloadError, match="untrusted"):
        download._absolute_url(url)


def test_job_urls_accept_relative_and_official_https_routes():
    import tools.api.usaspending_award_download as download

    assert download._absolute_url("/csv_downloads/job.zip") == (
        "https://api.usaspending.gov/csv_downloads/job.zip"
    )
    assert download._absolute_url(
        "https://api.usaspending.gov/api/v2/download/status?file_name=job.zip"
    ).startswith("https://api.usaspending.gov/")


def test_download_preflights_filters_dedupes_and_caches(monkeypatch, tmp_path):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_POLLS", 3)
    monkeypatch.setattr(download, "POLL_BACKOFF_SECONDS", 0)
    rows = [
        _row(award_latest_action_date="2025-01-01", recipient_name="Old name"),
        _row(award_latest_action_date="2026-02-01"),
        _row(
            contract_award_unique_key="CONT_EXPIRED",
            award_id_piid="PIID-EXPIRED",
            period_of_performance_current_end_date="2026-08-13",
        ),
        _row(
            contract_award_unique_key="IDV_EXPIRED",
            award_id_piid="IDV-EXPIRED",
            award_type_code="IDV_B",
            period_of_performance_current_end_date="2027-01-01",
            ordering_period_end_date="2026-08-01",
        ),
        _row(
            contract_award_unique_key="CONT_UNKNOWN",
            award_id_piid="PIID-UNKNOWN",
            period_of_performance_current_end_date="",
        ),
    ]
    calls = {"count": 0, "submit": 0, "status": 0, "download": 0}
    bodies = {}

    def fake_post(url, *, json, **_kwargs):
        if url == download.COUNT_URL:
            calls["count"] += 1
            bodies["count"] = json
            return {
                "calculated_count": len(rows),
                "maximum_limit": 500_000,
                "rows_gt_limit": False,
            }
        calls["submit"] += 1
        bodies["submit"] = json
        return {
            "status_url": "/api/v2/download/status?file_name=job.zip",
            "file_name": "job.zip",
            "file_url": "/csv_downloads/job.zip",
        }

    statuses = iter(
        [
            {"status": "queued"},
            {
                "status": "finished",
                "file_url": "/csv_downloads/job.zip",
                "total_rows": len(rows),
            },
        ]
    )

    def fake_status(_url, **_kwargs):
        calls["status"] += 1
        return next(statuses)

    def note_download():
        calls["download"] += 1

    monkeypatch.setattr(download, "post_json", fake_post)
    monkeypatch.setattr(download, "get_json", fake_status)
    monkeypatch.setattr(
        download,
        "download_file",
        _download_writer(_zip_rows(rows), on_download=note_download),
    )

    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
        window_end=date(2028, 2, 13),
    )

    assert result["status"] == "complete"
    assert result["coverage_status"] == "complete_within_boundary"
    assert result["coverage_boundary"]["start_date"] == "2007-10-01"
    assert result["downloaded_rows"] == 5
    assert result["normalized_awards"] == 2
    assert result["duplicates_removed"] == 1
    assert result["known_expired_removed"] == 2
    assert result["unknown_end_retained"] == 1
    assert result["row_count_verified"] is True
    assert result["records"][0]["recipient"] == "Current Vendor"
    unknown = next(
        row for row in result["records"] if row["award_id"] == "PIID-UNKNOWN"
    )
    assert unknown["expiration_state"] == "unknown_end_date"
    assert bodies["count"]["spending_level"] == "awards"
    period = bodies["count"]["filters"]["time_period"][0]
    assert period == {
        "start_date": "2007-10-01",
        "end_date": "2026-08-14",
        "date_type": "action_date",
    }
    assert bodies["submit"]["spending_level"] == ["awards"]
    assert "ordering_period_end_date" in bodies["submit"]["columns"]
    assert calls == {"count": 1, "submit": 1, "status": 2, "download": 1}

    monkeypatch.setattr(
        download,
        "post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete result must replay without HTTP")
        ),
    )
    cached = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
        window_end=date(2028, 2, 13),
    )
    assert cached["status"] == "complete"
    assert cached["retrieval_mode"] == "cache"


def test_over_limit_request_splits_by_naics_before_dates(monkeypatch, tmp_path):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    count_filters = []

    def fake_post(url, *, json, **_kwargs):
        assert url == download.COUNT_URL
        filters = json["filters"]
        count_filters.append(filters)
        codes = filters["naics_codes"]["require"]
        return {
            "calculated_count": 600_000 if len(codes) > 1 else 0,
            "maximum_limit": 500_000,
            "rows_gt_limit": len(codes) > 1,
        }

    monkeypatch.setattr(download, "post_json", fake_post)
    result = download.try_award_download(
        naics_codes=["541519", "541512", "541519"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "complete"
    assert result["shard_count"] == 2
    assert [item["naics_codes"]["require"] for item in count_filters] == [
        ["541512", "541519"],
        ["541512"],
        ["541519"],
    ]


def test_one_lane_over_limit_splits_into_nonoverlapping_dates(
    monkeypatch,
    tmp_path,
):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    periods = []

    def fake_post(url, *, json, **_kwargs):
        assert url == download.COUNT_URL
        period = json["filters"]["time_period"][0]
        periods.append(period)
        return {
            "calculated_count": 600_000 if len(periods) == 1 else 0,
            "maximum_limit": 500_000,
            "rows_gt_limit": len(periods) == 1,
        }

    monkeypatch.setattr(download, "post_json", fake_post)
    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2007, 10, 4),
    )

    assert result["status"] == "complete"
    assert result["shard_count"] == 2
    left, right = periods[1], periods[2]
    assert left["start_date"] == "2007-10-01"
    assert date.fromisoformat(right["start_date"]) == (
        date.fromisoformat(left["end_date"]) + timedelta(days=1)
    )
    assert right["end_date"] == "2007-10-04"


def test_pending_job_resumes_without_resubmission(monkeypatch, tmp_path):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_POLLS", 1)
    monkeypatch.setattr(download, "POLL_BACKOFF_SECONDS", 0)
    calls = {"count": 0, "submit": 0, "status": 0}
    rows = [_row()]

    def fake_post(url, *, json, **_kwargs):
        if url == download.COUNT_URL:
            calls["count"] += 1
            return {
                "calculated_count": 1,
                "maximum_limit": 500_000,
                "rows_gt_limit": False,
            }
        calls["submit"] += 1
        return {
            "status_url": "/api/v2/download/status?file_name=resume.zip",
            "file_url": "/csv_downloads/resume.zip",
            "file_name": "resume.zip",
        }

    def fake_status(_url, **_kwargs):
        calls["status"] += 1
        if calls["status"] == 1:
            return {"status": "running"}
        return {
            "status": "finished",
            "file_url": "/csv_downloads/resume.zip",
            "total_rows": 1,
        }

    monkeypatch.setattr(download, "post_json", fake_post)
    monkeypatch.setattr(download, "get_json", fake_status)
    monkeypatch.setattr(
        download,
        "download_file",
        _download_writer(_zip_rows(rows)),
    )

    first = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )
    second = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert first["status"] == "pending"
    assert second["status"] == "complete"
    assert calls == {"count": 2, "submit": 1, "status": 2}
    assert any(attempt["status"] == "resumed" for attempt in second["attempts"])


def test_preflight_mismatch_never_claims_complete(monkeypatch, tmp_path):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_POLLS", 1)
    monkeypatch.setattr(
        download,
        "post_json",
        lambda url, **_kwargs: (
            {
                "calculated_count": 2,
                "maximum_limit": 500_000,
                "rows_gt_limit": False,
            }
            if url == download.COUNT_URL
            else {
                "status_url": "/api/v2/download/status?file_name=short.zip",
                "file_url": "/csv_downloads/short.zip",
            }
        ),
    )
    monkeypatch.setattr(
        download,
        "get_json",
        lambda *_a, **_k: {
            "status": "finished",
            "file_url": "/csv_downloads/short.zip",
            "total_rows": 1,
        },
    )
    monkeypatch.setattr(
        download,
        "download_file",
        _download_writer(_zip_rows([_row()])),
    )

    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "failed"
    assert "preflight and downloaded row totals differ" in result["error"]
    assert result.get("coverage_status") is None


def test_total_preflight_row_boundary_falls_back_before_submission(
    monkeypatch,
    tmp_path,
):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_TOTAL_PREFLIGHT_ROWS", 10)
    submissions = []

    def fake_post(url, **_kwargs):
        if url == download.COUNT_URL:
            return {
                "calculated_count": 11,
                "maximum_limit": 500_000,
                "rows_gt_limit": False,
            }
        submissions.append(url)
        raise AssertionError("safety rejection must happen before submission")

    monkeypatch.setattr(download, "post_json", fake_post)
    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "failed"
    assert "total-row safety boundary of 10" in result["error"]
    assert submissions == []
    assert result["coverage_boundary"]["safety_limits"] == {
        "total_preflight_rows": 10,
        "total_archive_bytes": download.MAX_TOTAL_ARCHIVE_BYTES,
        "rows_per_archive": download.MAX_ROWS_PER_ARCHIVE,
        "archive_members": download.MAX_ARCHIVE_MEMBERS,
        "csv_members": download.MAX_CSV_MEMBERS,
        "total_uncompressed_bytes": download.MAX_TOTAL_UNCOMPRESSED_BYTES,
        "zip_expansion_ratio": download.MAX_ZIP_EXPANSION_RATIO,
        "csv_row_characters": download.MAX_CSV_ROW_CHARS,
        "normalized_awards": download.MAX_NORMALIZED_AWARDS,
        "maximum_shards": download.MAX_SHARDS,
    }


def test_shard_boundary_limits_recursive_preflight_count_calls(
    monkeypatch,
    tmp_path,
):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_SHARDS", 2)
    calls = []

    def always_over_limit(url, **_kwargs):
        assert url == download.COUNT_URL
        calls.append(url)
        return {
            "calculated_count": 600_000,
            "maximum_limit": 500_000,
            "rows_gt_limit": True,
        }

    monkeypatch.setattr(download, "post_json", always_over_limit)
    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "failed"
    assert "more than 2 shards" in result["error"]
    assert len(calls) == 2


def test_total_archive_boundary_refuses_oversize_cache(
    monkeypatch,
    tmp_path,
):
    import tools.api.usaspending_award_download as download

    cache = tmp_path / "cache"
    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(cache),
    )
    monkeypatch.setattr(download, "MAX_POLLS", 1)
    monkeypatch.setattr(download, "MAX_TOTAL_ARCHIVE_BYTES", 10)
    monkeypatch.setattr(
        download,
        "post_json",
        lambda url, **_kwargs: (
            {
                "calculated_count": 1,
                "maximum_limit": 500_000,
                "rows_gt_limit": False,
            }
            if url == download.COUNT_URL
            else {
                "status_url": "/api/v2/download/status?file_name=large.zip",
                "file_url": "/csv_downloads/large.zip",
            }
        ),
    )
    monkeypatch.setattr(
        download,
        "get_json",
        lambda *_a, **_k: {
            "status": "finished",
            "file_url": "/csv_downloads/large.zip",
            "total_rows": 1,
        },
    )
    monkeypatch.setattr(
        download,
        "download_file",
        _download_writer(_zip_rows([_row()])),
    )

    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "failed"
    assert "storage boundary" in result["error"]
    assert list(cache.glob("*.zip")) == []


def test_http_download_streams_under_limit_and_removes_partial_file(
    monkeypatch,
    tmp_path,
):
    import tools.api._http as transport

    class UnadvertisedChunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 8
            yield b"x" * 8
            yield b"x" * 8

    mock = httpx.MockTransport(
        lambda _request: httpx.Response(200, stream=UnadvertisedChunks())
    )
    monkeypatch.setattr(
        transport,
        "_client",
        lambda _timeout: httpx.Client(transport=mock),
    )
    destination = tmp_path / "archive.zip"

    with pytest.raises(ValueError, match="byte boundary"):
        transport.download_file(
            "https://api.usaspending.gov/archive.zip",
            destination,
            max_bytes=15,
            retries=1,
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_zip_member_and_row_guards_stop_before_unbounded_accumulation(
    monkeypatch,
):
    import tools.api.usaspending_award_download as download

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("PrimeAwardSummaries.csv", "a\n1\n")
        archive.writestr("extra-1.txt", "x")
        archive.writestr("extra-2.txt", "x")
    monkeypatch.setattr(download, "MAX_ARCHIVE_MEMBERS", 2)
    captured = []
    with pytest.raises(download.AwardDownloadError, match="member boundary"):
        download._parse_zip(
            raw.getvalue(),
            row_sink=captured.append,
            max_rows=10,
            max_uncompressed_bytes=10_000,
        )
    assert captured == []

    rows = [
        _row(),
        _row(contract_award_unique_key="CONT_AWARD_2", award_id_piid="PIID-2"),
    ]
    with pytest.raises(download.AwardDownloadError, match="row boundary"):
        download._parse_zip(
            _zip_rows(rows),
            row_sink=captured.append,
            max_rows=1,
            max_uncompressed_bytes=1_000_000,
        )
    assert len(captured) == 1

    with pytest.raises(
        download.AwardDownloadError,
        match="uncompressed-byte boundary",
    ):
        download._parse_zip(
            _zip_rows([_row()]),
            row_sink=lambda _row: None,
            max_rows=10,
            max_uncompressed_bytes=10,
        )


def test_zip_expansion_ratio_guard_rejects_highly_compressible_archive(
    monkeypatch,
):
    import tools.api.usaspending_award_download as download

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "PrimeAwardSummaries.csv",
            "description\n" + ("A" * 100_000),
        )
    monkeypatch.setattr(download, "MAX_ZIP_EXPANSION_RATIO", 2)

    with pytest.raises(download.AwardDownloadError, match="expansion-ratio"):
        download._parse_zip(
            raw.getvalue(),
            row_sink=lambda _row: None,
            max_rows=10,
            max_uncompressed_bytes=1_000_000,
        )


def test_local_parse_boundary_forces_preflight_sharding(monkeypatch, tmp_path):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_ROWS_PER_ARCHIVE", 2)
    periods = []

    def fake_post(url, *, json, **_kwargs):
        assert url == download.COUNT_URL
        periods.append(json["filters"]["time_period"][0])
        return {
            "calculated_count": 3 if len(periods) == 1 else 0,
            "maximum_limit": 500_000,
            "rows_gt_limit": False,
        }

    monkeypatch.setattr(download, "post_json", fake_post)
    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2007, 10, 4),
    )

    assert result["status"] == "complete"
    assert result["shard_count"] == 2
    assert result["coverage_boundary"]["safety_limits"]["rows_per_archive"] == 2


def test_normalized_award_boundary_fails_closed(monkeypatch, tmp_path):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(download, "MAX_POLLS", 1)
    monkeypatch.setattr(download, "MAX_NORMALIZED_AWARDS", 1)
    rows = [
        _row(),
        _row(contract_award_unique_key="CONT_AWARD_2", award_id_piid="PIID-2"),
    ]
    monkeypatch.setattr(
        download,
        "post_json",
        lambda url, **_kwargs: (
            {
                "calculated_count": 2,
                "maximum_limit": 500_000,
                "rows_gt_limit": False,
            }
            if url == download.COUNT_URL
            else {
                "status_url": "/api/v2/download/status?file_name=bounded.zip",
                "file_url": "/csv_downloads/bounded.zip",
            }
        ),
    )
    monkeypatch.setattr(
        download,
        "get_json",
        lambda *_args, **_kwargs: {
            "status": "finished",
            "file_url": "/csv_downloads/bounded.zip",
            "total_rows": 2,
        },
    )
    monkeypatch.setattr(
        download,
        "download_file",
        _download_writer(_zip_rows(rows)),
    )

    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "failed"
    assert "normalized-award boundary" in result["error"]


def test_cross_shard_rows_are_spooled_and_deduped_without_list_accumulation(
    monkeypatch,
    tmp_path,
):
    import tools.api.usaspending_award_download as download

    monkeypatch.setenv(
        "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    shards = [
        {"filters": {"shard": 1}, "expected_count": 2, "maximum_limit": 2},
        {"filters": {"shard": 2}, "expected_count": 2, "maximum_limit": 2},
    ]
    first = _row(award_latest_action_date="2025-01-01")
    newer = _row(award_latest_action_date="2026-01-01")
    second = _row(
        contract_award_unique_key="CONT_AWARD_2",
        award_id_piid="PIID-2",
    )
    third = _row(
        contract_award_unique_key="CONT_AWARD_3",
        award_id_piid="PIID-3",
    )
    shard_rows = {1: [first, second], 2: [newer, third]}

    monkeypatch.setattr(download, "_plan_shard", lambda *_args, **_kwargs: shards)

    def fake_run(shard, *, row_sink, **_kwargs):
        rows = shard_rows[shard["filters"]["shard"]]
        for row in rows:
            row_sink(row)
        return {
            "status": "complete",
            "retrieval_mode": "live",
            "archive_bytes": 10,
            "uncompressed_bytes": 100,
            "row_count": len(rows),
            "members": [f"shard-{shard['filters']['shard']}.csv"],
            "attempts": [],
        }

    monkeypatch.setattr(download, "_run_shard", fake_run)
    result = download.try_award_download(
        naics_codes=["541512"],
        as_of=date(2026, 8, 14),
    )

    assert result["status"] == "complete"
    assert result["downloaded_rows"] == 4
    assert result["duplicates_removed"] == 1
    assert result["normalized_awards"] == 3
    assert {row["award_id"] for row in result["records"]} == {
        "PIID-1",
        "PIID-2",
        "PIID-3",
    }
