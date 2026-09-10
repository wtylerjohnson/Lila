"""Exact consumed-byte evidence without changing native daily refresh (2026-09-10)."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime

import pytest

from tools.api import sam_extract as se
from tools.api.base import SourceQuery
from tools.relevance.scope import EngagementScope
from tools.relevance.taxonomy import CapabilityTaxonomy, CodeUniverse, TaxonomyTerm


CSV = (
    b"NoticeId,Title,Sol#,Department/Ind.Agency,PostedDate,Type,"
    b"ResponseDeadLine,NaicsCode,Active,Description\r\n"
    b"N-OLD,Unified network performance RFI,S-1,NASA,2026-09-01,"
    b"Presolicitation,2026-10-15,517810,Yes,unified network performance\r\n"
    b"N-NEW,Unified network performance RFI,S-1,NASA,2026-09-02,"
    b"Presolicitation,2026-10-15,517810,Yes,unified network performance\r\n"
)


class Clock(date):
    current = date(2026, 9, 10)

    @classmethod
    def today(cls):
        return cls.current


@pytest.fixture
def native_extract(tmp_path, monkeypatch):
    """Real native downloader and reader, isolated cache and fake upstream only."""
    cache = tmp_path / "extracts"
    cache.mkdir()
    monkeypatch.setenv("LILA_SAM_EXTRACT_DIR", str(cache))
    monkeypatch.setenv("LILA_SAM_EXTRACT_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("LILA_MIN_EXTRACT_BYTES", "100")
    monkeypatch.setattr(se, "date", Clock)
    monkeypatch.setattr(Clock, "current", date(2026, 9, 10))

    class HTTP:
        calls = 0
        payload = CSV
        on_download = staticmethod(lambda: None)

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url):
            assert method == "GET" and url == se.EXTRACT_URL
            type(self).calls += 1
            return self

        def raise_for_status(self):
            pass

        def iter_bytes(self, size):
            type(self).on_download()
            yield type(self).payload

    monkeypatch.setattr(se.httpx, "Client", HTTP)
    return cache, HTTP


@pytest.fixture
def taxonomy():
    return CapabilityTaxonomy(
        client_name="Receipt Fixture", version=1, updated="2026-09-10",
        core=[TaxonomyTerm(term="network performance monitoring")],
        adjacent=[TaxonomyTerm(term="unified observability")],
        code_universe=CodeUniverse(naics=["541519"]),
    )


def _attachments(source, taxonomy, **kwargs):
    return source.attachment_candidates(
        SourceQuery(deadline_from=date(2026, 9, 10)), taxonomy,
        EngagementScope(preset="civilian"), **kwargs)


def _assert_verified(receipt, path, payload):
    assert receipt["path"] == str(path.absolute())
    assert receipt["filename"] == path.name
    assert receipt["cache_date"] == path.stem.removeprefix("opportunities_")
    assert receipt["cache_date_basis"] == "local_cache_filename_not_upstream_publication"
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["bytes_read"] == len(payload)
    assert receipt["status"] == "verified"
    assert receipt["complete"] is True
    assert receipt["integrity"] == "stable"
    times = [datetime.fromisoformat(receipt[key]) for key in (
        "selection_started_at_utc", "selection_finished_at_utc",
        "read_started_at_utc", "read_finished_at_utc")]
    assert times == sorted(times)
    assert all(value.utcoffset().total_seconds() == 0 for value in times)
    assert receipt["file_stat_before"] == receipt["file_stat_after"]
    assert receipt["path_stat_before"] == receipt["path_stat_after"]


def test_native_cache_reuse_hashes_exact_bytes_before_text_replacement(native_extract):
    cache, http = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    payload = CSV.replace(b"RFI", b"RFI\xff")
    path.write_bytes(payload)
    source = se.SamExtractSource()
    rows = source.search(SourceQuery())
    assert [row.source_id for row in rows] == ["N-OLD", "N-NEW"]
    assert "\ufffd" in rows[0].title
    assert http.calls == 0
    _assert_verified(source.last_census["extract_receipts"][0], path, payload)
    assert source.last_census["complete"] is True


def test_native_daily_download_ignores_older_cache_and_preserves_order(native_extract):
    cache, http = native_extract
    old = cache / "opportunities_2026-09-08.csv"
    old.write_bytes(CSV.replace(b"N-", b"O-"))
    source = se.SamExtractSource()
    rows = source.search(SourceQuery(keywords=["network"], limit=1))
    assert [row.source_id for row in rows] == ["N-OLD", "N-NEW"]
    assert http.calls == 1
    assert old.read_bytes() == CSV.replace(b"N-", b"O-")
    _assert_verified(source.last_census["extract_receipts"][0],
                     cache / "opportunities_2026-09-10.csv", CSV)


def test_native_selection_midnight_keeps_selected_filename_date(
        native_extract, monkeypatch):
    cache, http = native_extract
    http.on_download = staticmethod(
        lambda: monkeypatch.setattr(Clock, "current", date(2026, 9, 11)))
    source = se.SamExtractSource()
    source.search(SourceQuery())
    receipt = source.last_census["extract_receipts"][0]
    assert receipt["selection_started_local_date"] == "2026-09-10"
    assert receipt["selection_finished_local_date"] == "2026-09-11"
    _assert_verified(receipt, cache / "opportunities_2026-09-10.csv", CSV)


def test_later_attachment_selection_can_cross_day_with_two_same_file_passes(
        native_extract, taxonomy, monkeypatch):
    cache, http = native_extract
    source = se.SamExtractSource()
    source.search(SourceQuery())
    original = json.loads(json.dumps(source.last_census))
    monkeypatch.setattr(Clock, "current", date(2026, 9, 11))
    http.payload = CSV.replace(b"N-NEW", b"N-NEXT")
    candidates = _attachments(source, taxonomy)
    assert [row.source_id for row in candidates] == ["N-NEXT"]
    assert http.calls == 2
    assert source.last_census == original
    receipts = source.last_attachment_census["extract_receipts"]
    assert [row["scan"] for row in receipts] == [
        "attachment_latest", "attachment_candidates"]
    for receipt in receipts:
        _assert_verified(receipt, cache / "opportunities_2026-09-11.csv", http.payload)
    assert source.last_attachment_census["complete"] is True


@pytest.mark.parametrize("kind", ["primary", "attachment"])
def test_download_failure_resets_previous_census(
        native_extract, taxonomy, monkeypatch, kind):
    source = se.SamExtractSource()
    call = (lambda: source.search(SourceQuery())) if kind == "primary" else (
        lambda: _attachments(source, taxonomy))
    attr = "last_census" if kind == "primary" else "last_attachment_census"
    call()
    previous = getattr(source, attr)
    assert previous["complete"] is True

    def fail():
        raise se.ExtractRefused("fixture upstream unavailable")

    monkeypatch.setattr(se, "download_extract", fail)
    with pytest.raises(se.ExtractRefused):
        call()
    current = getattr(source, attr)
    assert current is not previous and previous["complete"] is True
    assert current["complete"] is False
    assert current["extract_receipts"] == []
    assert current["extract_selection"]["status"] == "failed"
    assert current["error_type"] == "ExtractRefused"
    assert "matched" not in current and "selected" not in current


@pytest.mark.parametrize("mutation", ["replace", "rewrite", "unlink"])
def test_concurrent_path_or_byte_change_never_verifies(
        native_extract, monkeypatch, mutation):
    cache, _ = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    path.write_bytes(CSV)
    original = se.iter_rows

    def mutate(handle):
        for index, row in enumerate(original(handle)):
            if index == 0:
                if mutation == "replace":
                    other = cache / "replacement.csv"
                    other.write_bytes(CSV)
                    other.replace(path)
                elif mutation == "rewrite":
                    before = path.stat()
                    path.write_bytes(CSV.replace(b"N-NEW", b"N-DIF"))
                    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                else:
                    path.unlink()
            yield row

    monkeypatch.setattr(se, "iter_rows", mutate)
    source = se.SamExtractSource()
    with pytest.raises(se.ExtractReadFailed, match="changed during"):
        source.search(SourceQuery())
    receipt = source.last_census["extract_receipts"][0]
    assert receipt["status"] == "failed"
    assert receipt["integrity"] == "changed"
    assert receipt["complete"] is False
    assert source.last_census["complete"] is False


def test_read_error_preserves_partial_consumed_hash(native_extract, monkeypatch):
    cache, http = native_extract
    http.payload = CSV + b"\n" * 20_000
    original = se._HashingReader.readinto

    def fail_after_chunk(reader, buffer):
        if reader.bytes_read:
            raise OSError("fixture interrupted read")
        return original(reader, buffer)

    monkeypatch.setattr(se._HashingReader, "readinto", fail_after_chunk)
    source = se.SamExtractSource()
    with pytest.raises(OSError, match="interrupted read"):
        source.search(SourceQuery())
    receipt = source.last_census["extract_receipts"][0]
    assert 0 < receipt["bytes_read"] < len(http.payload)
    assert receipt["sha256"] == hashlib.sha256(
        http.payload[:receipt["bytes_read"]]).hexdigest()
    assert receipt["integrity"] == "read_failed"
    assert receipt["complete"] is False
    assert receipt["read_finished_at_utc"]


def test_incomplete_csv_iteration_does_not_verify(native_extract, monkeypatch):
    original = se.iter_rows

    def partial(handle):
        yield next(iter(original(handle)))

    monkeypatch.setattr(se, "iter_rows", partial)
    source = se.SamExtractSource()
    with pytest.raises(se.ExtractReadFailed, match="full EOF"):
        source.search(SourceQuery())
    assert source.last_census["extract_receipts"][0]["status"] == "failed"


def test_replacement_between_attachment_passes_is_detected(
        native_extract, taxonomy, monkeypatch):
    cache, _ = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    original = se._read_extract

    @contextmanager
    def between(path, census, scan, **kwargs):
        with original(path, census, scan, **kwargs) as handle:
            yield handle
        if scan == "attachment_latest":
            other = cache / "replacement.csv"
            other.write_bytes(CSV)
            other.replace(path)

    monkeypatch.setattr(se, "_read_extract", between)
    source = se.SamExtractSource()
    with pytest.raises(se.ExtractReadFailed, match="changed before"):
        _attachments(source, taxonomy)
    first, second = source.last_attachment_census["extract_receipts"]
    assert first["status"] == "verified"
    assert second["status"] == "failed" and second["bytes_read"] == 0
    assert source.last_attachment_census["complete"] is False


@pytest.mark.parametrize("reason", ["limit_zero", "insufficient_anchors"])
def test_attachment_skip_has_no_invented_read(native_extract, taxonomy, reason):
    _, http = native_extract
    source = se.SamExtractSource()
    if reason == "insufficient_anchors":
        taxonomy = taxonomy.model_copy(update={"core": [], "adjacent": []})
    assert _attachments(source, taxonomy, limit=0 if reason == "limit_zero" else 8) == []
    assert http.calls == 0
    assert source.last_attachment_census["scan_skipped_reason"] == reason
    assert source.last_attachment_census["extract_receipts"] == []


@pytest.mark.parametrize("fail", [None, "download", "read"])
def test_attachment_receipts_survive_native_enrichment_seam(
        native_extract, taxonomy, monkeypatch, fail):
    import run_searches as rs

    source = se.SamExtractSource()
    if fail == "download":
        def refused():
            raise se.ExtractRefused("fixture refusal")
        monkeypatch.setattr(se, "download_extract", refused)
    elif fail == "read":
        def interrupted(handle):
            raise OSError("fixture interrupted CSV scan")
        monkeypatch.setattr(se, "iter_rows", interrupted)
    original_rows = [{"source_id": "existing"}]
    # NASA-only fixture yields no DHS candidates; the native scan still runs
    # twice and the runner performs no resource call or enrichment.
    rows, stats = rs._safe_enrich_sam_public_attachments(
        original_rows, source, SourceQuery(agencies=["DHS"]), taxonomy,
        EngagementScope(preset="civilian"), limit=8)
    assert rows == original_rows
    assert stats["attachment_candidates"] == source.last_attachment_census
    assert stats["attachment_candidates"]["complete"] is (fail is None)
    if fail == "read":
        assert stats["attachment_candidates"]["extract_receipts"][0]["status"] == "failed"
    assert json.loads(json.dumps(stats))["attachment_candidates"] == source.last_attachment_census


@pytest.mark.parametrize("fail", [None, "download", "read"])
def test_native_runner_retains_consumed_or_failed_extract_census(
        native_extract, taxonomy, tmp_path, monkeypatch, fail):
    """Execute only native SAM in the existing isolated native-run fixture."""
    from tests import test_workstation_native_run as fixture
    import run_searches as rs
    import tools.query_terms as query_terms
    import tools.relevance.taxonomy as taxonomies

    review_dir, *_ = fixture._seed_native_coexistence(tmp_path, monkeypatch)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", fixture.CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", fixture.WORKSTATION_ID)
    fixture._bind_native_owner(monkeypatch, review_dir)
    captured = {}
    fixture._patch_offline_success(monkeypatch, captured)
    monkeypatch.setattr(taxonomies, "load_taxonomy", lambda _: taxonomy)
    monkeypatch.setattr(query_terms, "term_yield", lambda _: [])

    class Executor(fixture._InertExecutor):
        def submit(self, runner, name, fn):
            if name == "sam.gov":
                return fixture._InertFuture(runner(name, fn))
            return super().submit(runner, name, fn)

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", Executor)
    if fail:
        if fail == "download":
            def refused():
                raise se.ExtractRefused("fixture refusal")
            monkeypatch.setattr(se, "download_extract", refused)
        else:
            def interrupted(handle):
                raise OSError("fixture interrupted CSV scan")
            monkeypatch.setattr(se, "iter_rows", interrupted)

        class Fallback:
            last_status = {"complete": True, "census": {}}

            def search(self, query):
                return []

        monkeypatch.setattr(rs, "SamGovSource", Fallback)
    else:
        def no_fallback():
            raise AssertionError("native extract unexpectedly fell back")
        monkeypatch.setattr(rs, "SamGovSource", no_fallback)
    monkeypatch.setattr(sys, "argv", [
        "run_searches.py", "--client", fixture.CLIENT, "--skip", "triage", "picture"])
    assert rs.main() == 0
    census = json.loads(json.dumps(captured["payload"]))["results"]["sam_census"]
    if fail:
        assert census["source"] == "sam.gov live API"
        assert census["complete"] is True
        assert census["failed_extract_census"]["complete"] is False
        failed_census = census["failed_extract_census"]
        if fail == "download":
            assert failed_census["extract_selection"]["status"] == "failed"
        else:
            assert failed_census["extract_selection"]["status"] == "selected"
            assert failed_census["extract_receipts"][0]["status"] == "failed"
    else:
        assert census["source"] == "sam_extract"
        assert census["extract_receipts"][0]["status"] == "verified"
        assert len(census["attachment_candidates"]["extract_receipts"]) == 2
    assert "extract_receipts" not in rs._sam_census_receipt(census)
    assert "failed_extract_census" not in rs._sam_census_receipt(census)
