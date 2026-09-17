"""Exact consumed-byte evidence without changing native daily refresh (2026-09-10)."""

from __future__ import annotations

import builtins
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
    assert receipt["schema_version"] == 1
    assert receipt["scan_metadata_consistency"] == "consistent"
    verification = receipt["content_verification"]
    assert verification["method"] == "independent_post_read_sha256"
    assert verification["status"] == "verified"
    assert verification["complete"] is True
    assert verification["metadata_consistency"] == "consistent"
    assert verification["sha256"] == receipt["sha256"]
    assert verification["bytes_read"] == len(payload)
    for key in ("file_stat_before", "file_stat_after", "path_stat_before",
                "path_stat_after", "consumed_file_stat_before", "consumed_file_stat_after"):
        assert verification[key] == receipt["file_stat_before"]
    assert (receipt["read_started_at_utc"] <= verification["read_started_at_utc"]
            <= verification["read_finished_at_utc"] <= receipt["read_finished_at_utc"])


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


def test_same_length_rewrite_with_hidden_timestamps_fails_content_verification(
        native_extract, monkeypatch):
    """S2-SOURCE-002: synthetic metadata invisibility, not a cloud-FS claim."""
    cache, _ = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    path.write_bytes(CSV)
    before = path.stat()
    file_stat = se._file_stat
    original = se.iter_rows

    def hidden_timestamps(stat):
        observed = file_stat(stat)
        observed.update(modified_at_ns=before.st_mtime_ns,
                        changed_at_ns=before.st_ctime_ns)
        return observed

    def mutate(handle):
        for index, row in enumerate(original(handle)):
            if index == 0:
                path.write_bytes(CSV.replace(b"N-NEW", b"N-DIF"))
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            yield row

    monkeypatch.setattr(se, "_file_stat", hidden_timestamps)
    monkeypatch.setattr(se, "iter_rows", mutate)
    source = se.SamExtractSource()
    with pytest.raises(se.ExtractReadFailed, match="digest mismatch"):
        source.search(SourceQuery())
    receipt = source.last_census["extract_receipts"][0]
    assert receipt["sha256"] == hashlib.sha256(CSV).hexdigest()
    assert receipt["bytes_read"] == len(CSV)
    assert receipt["scan_metadata_consistency"] == "consistent"
    verification = receipt["content_verification"]
    assert verification["metadata_consistency"] == "consistent"
    assert verification["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert verification["sha256"] != receipt["sha256"]
    assert verification["status"] == "changed"
    assert verification["complete"] is True
    assert receipt["status"] == "failed" and receipt["integrity"] == "changed"
    assert receipt["complete"] is False
    assert source.last_census["complete"] is False


def test_unchanged_content_gets_independent_full_digest(native_extract, monkeypatch):
    """The second pass opens a distinct descriptor and hashes beyond one chunk."""
    cache, http = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    payload = CSV + b"\n" * 1_100_000
    path.write_bytes(payload)
    handles = []

    def observed_open(selected, *args, **kwargs):
        assert selected == path
        handle = builtins.open(selected, *args, **kwargs)
        if handles:
            assert handles[0].closed is False
            assert handles[0].fileno() != handle.fileno()
        handles.append(handle)
        return handle

    monkeypatch.setattr(se, "open", observed_open, raising=False)
    source = se.SamExtractSource()
    rows = source.search(SourceQuery())
    assert [row.source_id for row in rows] == ["N-OLD", "N-NEW"]
    assert http.calls == 0 and len(handles) == 2
    assert all(handle.closed for handle in handles)
    _assert_verified(source.last_census["extract_receipts"][0], path, payload)


@pytest.mark.parametrize("failure", [
    "open", "read", "closed_descriptor", "short_eof", "no_progress",
    "file_stat_before", "file_stat_after", "path_stat_before", "path_stat_after",
])
def test_verification_io_failure_keeps_consumed_digest_and_fails_named(
        native_extract, monkeypatch, failure):
    """S2-SOURCE-002: verification cannot turn unreadable input into success."""
    cache, _ = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    payload = CSV + b"\n" * 1_100_000
    path.write_bytes(payload)
    opened = []
    real_read, real_fstat, real_stat = se._HashingReader.readinto, os.fstat, type(path).stat
    calls = {"file_stat": 0, "path_stat": 0}

    def verification_open(selected, *args, **kwargs):
        if len(opened) == 1 and failure == "open":
            raise PermissionError("fixture verification reopen denied")
        handle = builtins.open(selected, *args, **kwargs)
        opened.append(handle)
        return handle

    def interrupted_read(reader, buffer):
        if len(opened) == 2 and reader.source is opened[1] and reader.bytes_read:
            if failure == "read":
                raise OSError("fixture verification read interrupted")
            if failure == "closed_descriptor":
                reader.source.close()
                return real_read(reader, buffer)
            if failure == "short_eof":
                reader.eof = True
                return 0
            if failure == "no_progress":
                return None
        return real_read(reader, buffer)

    def check_stat(kind, is_verifier):
        if is_verifier:
            calls[kind] += 1
            if failure == f"{kind}_{'before' if calls[kind] == 1 else 'after'}":
                raise OSError("fixture verification stat unavailable")

    def interrupted_fstat(fd):
        check_stat("file_stat", len(opened) == 2 and not opened[1].closed
                   and fd == opened[1].fileno())
        return real_fstat(fd)

    def interrupted_path_stat(selected, *args, **kwargs):
        check_stat("path_stat", selected == path and len(opened) == 2
                   and not opened[1].closed)
        return real_stat(selected, *args, **kwargs)

    monkeypatch.setattr(se, "open", verification_open, raising=False)
    monkeypatch.setattr(se._HashingReader, "readinto", interrupted_read)
    monkeypatch.setattr(os, "fstat", interrupted_fstat)
    monkeypatch.setattr(type(path), "stat", interrupted_path_stat)
    source = se.SamExtractSource()
    message = "full EOF" if failure in {"short_eof", "no_progress"} else "unreadable"
    with pytest.raises(se.ExtractReadFailed, match=f"content verification.*{message}"):
        source.search(SourceQuery())
    receipt = source.last_census["extract_receipts"][0]
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["bytes_read"] == len(payload)
    assert receipt["status"] == "failed" and receipt["integrity"] == "read_failed"
    assert receipt["complete"] is False and source.last_census["complete"] is False
    verification = receipt["content_verification"]
    assert verification["status"] == "read_failed"
    assert verification["read_finished_at_utc"] and verification["error_type"]
    if failure in {"read", "closed_descriptor", "short_eof", "no_progress"}:
        assert 0 < verification["bytes_read"] < len(payload)
        assert verification["sha256"] == hashlib.sha256(
            payload[:verification["bytes_read"]]).hexdigest()
        assert verification["complete"] is False
    assert all(handle.closed for handle in opened)


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("mutation", ["replace", "unlink"])
def test_path_identity_change_at_verification_boundary_fails(
        native_extract, monkeypatch, when, mutation):
    cache, _ = native_extract
    path = cache / "opportunities_2026-09-10.csv"
    path.write_bytes(CSV)
    opened = []
    real_read = se._HashingReader.readinto
    changed = False

    def mutate():
        nonlocal changed
        changed = True
        if mutation == "replace":
            replacement = cache / "verification-replacement.csv"
            replacement.write_bytes(CSV)
            replacement.replace(path)
        else:
            path.unlink()

    def verification_open(selected, *args, **kwargs):
        if len(opened) == 1 and when == "before":
            mutate()
        handle = builtins.open(selected, *args, **kwargs)
        opened.append(handle)
        return handle

    def verification_read(reader, buffer):
        count = real_read(reader, buffer)
        if len(opened) == 2 and reader.source is opened[1] and not changed:
            mutate()
        return count

    monkeypatch.setattr(se, "open", verification_open, raising=False)
    monkeypatch.setattr(se._HashingReader, "readinto", verification_read)
    source = se.SamExtractSource()
    with pytest.raises(se.ExtractReadFailed, match="content verification"):
        source.search(SourceQuery())
    receipt = source.last_census["extract_receipts"][0]
    assert receipt["sha256"] == hashlib.sha256(CSV).hexdigest()
    assert receipt["status"] == "failed" and receipt["complete"] is False
    assert source.last_census["complete"] is False
    assert receipt["content_verification"]["status"] == (
        "changed" if mutation == "replace" else "read_failed")
    assert all(handle.closed for handle in opened)


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


@pytest.mark.parametrize("fail", [None, "missing", "stale", "read"])
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
    cache, http = native_extract
    if fail != 'missing':
        path = cache / ('opportunities_2026-09-09.csv' if fail == 'stale' else 'opportunities_2026-09-10.csv')
        path.write_bytes(CSV)
    if fail == 'read':
        def interrupted(handle):
            raise OSError('fixture interrupted CSV scan')
        monkeypatch.setattr(se, 'iter_rows', interrupted)
    import tools.api.sam_gov as sam_api
    def no_api(*args, **kwargs):
        raise AssertionError('ordinary scan must never call the SAM live API')
    monkeypatch.setattr(sam_api, 'SamGovSource', no_api)
    monkeypatch.setattr(sys, "argv", [
        "run_searches.py", "--client", fixture.CLIENT, "--skip", "triage", "picture"])
    assert rs.main() == (2 if fail else 0)
    result = json.loads(json.dumps(captured['payload']))
    census = result['results']['sam_census']
    assert http.calls == 0, 'ordinary scan reads the daily producer file and never downloads'
    if fail:
        assert result['status'] == 'failed_daily_extract'
        assert 'failed_sweeps' in captured['path']
        assert census['complete'] is False and census['source'] == 'sam_extract'
        assert result['results']['decision_coverage_verdict']['complete'] is False
        assert 'upstream_investigations' not in result['results']
        if fail in {'missing', 'stale'}:
            assert census['extract_selection']['status'] == 'failed'
            assert 'missing' in census['error'] or 'stale' in census['error']
        else:
            assert census['extract_receipts'][0]['status'] == 'failed'
    else:
        assert census['source'] == 'sam_extract'
        assert census['extract_receipts'][0]['status'] == 'verified'
        assert len(census['attachment_candidates']['extract_receipts']) == 2
    assert 'extract_receipts' not in rs._sam_census_receipt(census)
    assert 'failed_extract_census' not in rs._sam_census_receipt(census)


@pytest.mark.parametrize('stage', ['scan', 'verification', 'between_attachment_passes'])
def test_metadata_only_ctime_change_keeps_verified_bytes(native_extract, taxonomy, monkeypatch, stage):
    cache, _ = native_extract
    path = cache / 'opportunities_2026-09-10.csv'
    path.write_bytes(CSV)
    original_rows, original_read, original_scan = se.iter_rows, se._HashingReader.readinto, se._read_extract
    changed = False
    def metadata_touch():
        nonlocal changed
        if not changed:
            before = path.stat()
            path.chmod(before.st_mode ^ 0o100)
            assert path.stat().st_ctime_ns != before.st_ctime_ns
            assert path.stat().st_mtime_ns == before.st_mtime_ns
            changed = True
    def rows(handle):
        for row in original_rows(handle):
            if stage == 'scan':metadata_touch()
            yield row
    def read(reader, buffer):
        n = original_read(reader, buffer)
        if stage == 'verification' and len(buffer) == 1024 * 1024:metadata_touch()
        return n
    @contextmanager
    def scan(selected, census, label, **kw):
        with original_scan(selected, census, label, **kw) as handle:yield handle
        if stage == 'between_attachment_passes' and label == 'attachment_latest':metadata_touch()
    monkeypatch.setattr(se, 'iter_rows', rows)
    monkeypatch.setattr(se._HashingReader, 'readinto', read)
    monkeypatch.setattr(se, '_read_extract', scan)
    source = se.SamExtractSource(existing_only=True)
    if stage == 'between_attachment_passes':
        _attachments(source, taxonomy)
        receipt = source.last_attachment_census
    else:
        assert len(source.search(SourceQuery())) == 2
        receipt = source.last_census
    assert changed and receipt['complete'] is True
    for r in receipt['extract_receipts']:
        assert r['sha256'] == hashlib.sha256(CSV).hexdigest()
        assert r['content_verification']['sha256'] == r['sha256']
        assert r['integrity'] == 'stable'
    assert path.read_bytes() == CSV
