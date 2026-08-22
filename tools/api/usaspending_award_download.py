"""Keyless, resumable USAspending award-summary downloads.

The synchronous ``spending_by_award`` search is useful for a quick sample, but
its page ceiling cannot support a census claim.  This adapter uses the official
advanced-search download workflow instead:

* preflight the row count;
* split an over-limit request by NAICS and then by non-overlapping action dates;
* persist same-day jobs so a later sweep resumes rather than resubmits them;
* cache the downloaded ZIP and normalized result by a deterministic query hash;
* remove known-expired awards locally using the correct contract/IDV end date;
* deduplicate award summaries before returning them to Contract Awards.

USAspending advanced search starts on 2007-10-01.  That boundary is carried in
every result and is never promoted into an all-history completeness claim.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from tools.api._http import download_file, get_json, post_json

API_ROOT = "https://api.usaspending.gov"
COUNT_URL = f"{API_ROOT}/api/v2/download/count/"
DOWNLOAD_URL = f"{API_ROOT}/api/v2/download/search/"
EARLIEST_ACTION_DATE = date(2007, 10, 1)
DEFAULT_MAXIMUM_ROWS = 500_000

CONTRACT_TYPE_CODES = ("A", "B", "C", "D")
IDV_TYPE_CODES = (
    "IDV_A",
    "IDV_B",
    "IDV_B_A",
    "IDV_B_B",
    "IDV_B_C",
    "IDV_C",
    "IDV_D",
    "IDV_E",
)
AWARD_TYPE_CODES = (*CONTRACT_TYPE_CODES, *IDV_TYPE_CODES)

COLUMNS = (
    "contract_award_unique_key",
    "award_id_piid",
    "parent_award_id_piid",
    "award_type_code",
    "total_obligated_amount",
    "current_total_value_of_award",
    "potential_total_value_of_award",
    "award_base_action_date",
    "award_latest_action_date",
    "period_of_performance_current_end_date",
    "ordering_period_end_date",
    "recipient_uei",
    "recipient_name",
    "awarding_agency_name",
    "awarding_sub_agency_name",
    "naics_code",
    "naics_description",
    "prime_award_base_transaction_description",
    "usaspending_permalink",
)

PENDING_STATES = {
    "created",
    "queued",
    "ready",
    "running",
    "resumed",
    "uploading",
}
TERMINAL_STATES = {"failed", "finished"}

MAX_POLLS = int(os.environ.get("LILA_USASPENDING_DOWNLOAD_MAX_POLLS", "4"))
MAX_TOTAL_PREFLIGHT_ROWS = int(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_TOTAL_ROW_LIMIT", "1000000")
)
MAX_TOTAL_ARCHIVE_BYTES = int(
    os.environ.get(
        "LILA_USASPENDING_DOWNLOAD_TOTAL_ARCHIVE_BYTES",
        str(512 * 1024 * 1024),
    )
)
MAX_SHARDS = int(os.environ.get("LILA_USASPENDING_DOWNLOAD_MAX_SHARDS", "16"))
MAX_ROWS_PER_ARCHIVE = int(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_ROWS_PER_ARCHIVE", "75000")
)
MAX_ARCHIVE_MEMBERS = int(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_ARCHIVE_MEMBERS", "32")
)
MAX_CSV_MEMBERS = int(os.environ.get("LILA_USASPENDING_DOWNLOAD_CSV_MEMBERS", "8"))
MAX_TOTAL_UNCOMPRESSED_BYTES = int(
    os.environ.get(
        "LILA_USASPENDING_DOWNLOAD_UNCOMPRESSED_BYTES",
        str(2 * 1024 * 1024 * 1024),
    )
)
MAX_ZIP_EXPANSION_RATIO = float(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_EXPANSION_RATIO", "200")
)
MAX_CSV_ROW_CHARS = int(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_ROW_CHARS", str(2 * 1024 * 1024))
)
MAX_NORMALIZED_AWARDS = int(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_NORMALIZED_AWARDS", "100000")
)
POLL_DEADLINE_SECONDS = float(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_DEADLINE_SECONDS", "20")
)
POLL_BACKOFF_SECONDS = float(
    os.environ.get("LILA_USASPENDING_DOWNLOAD_BACKOFF_SECONDS", "0.5")
)
_DEFAULT_CACHE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cache"
    / "usaspending_award_download"
)
_ALLOWED_JOB_HOSTS = frozenset({"api.usaspending.gov"})


class AwardDownloadError(RuntimeError):
    """The official job could not support a complete-within-boundary result."""


def _cache_dir() -> Path:
    return Path(
        os.environ.get(
            "LILA_USASPENDING_AWARD_DOWNLOAD_CACHE_DIR",
            str(_DEFAULT_CACHE),
        )
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_naics(codes: list[str]) -> list[str]:
    return sorted({str(code).strip() for code in codes if str(code).strip()})


def _filters(codes: list[str], start: date, end: date) -> dict[str, Any]:
    return {
        "award_type_codes": list(AWARD_TYPE_CODES),
        "naics_codes": {"require": list(codes)},
        "time_period": [
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "date_type": "action_date",
            }
        ],
    }


def _count(filters: dict[str, Any]) -> dict[str, Any]:
    payload = post_json(
        COUNT_URL,
        json={"filters": filters, "spending_level": "awards"},
        retries=1,
        timeout=15.0,
    )
    if not isinstance(payload, dict):
        raise AwardDownloadError("USAspending count response was not an object")
    required = {"calculated_count", "maximum_limit", "rows_gt_limit"}
    missing = sorted(required - payload.keys())
    if missing:
        raise AwardDownloadError(
            "USAspending count response omitted required fields: " + ", ".join(missing)
        )
    try:
        calculated = int(payload.get("calculated_count") or 0)
        api_maximum = int(payload.get("maximum_limit") or DEFAULT_MAXIMUM_ROWS)
    except (TypeError, ValueError) as exc:
        raise AwardDownloadError(
            "USAspending count response did not contain numeric limits"
        ) from exc
    return {
        "calculated_count": calculated,
        "api_maximum_limit": api_maximum,
        "maximum_limit": min(api_maximum, MAX_ROWS_PER_ARCHIVE),
        "rows_gt_limit": bool(payload.get("rows_gt_limit"))
        or calculated > min(api_maximum, MAX_ROWS_PER_ARCHIVE),
    }


def _split_dates(start: date, end: date) -> tuple[tuple[date, date], ...]:
    if start >= end:
        raise AwardDownloadError(
            "USAspending award count exceeds the limit for a single action date"
        )
    midpoint = start + timedelta(days=(end - start).days // 2)
    return ((start, midpoint), (midpoint + timedelta(days=1), end))


def _plan_shard(
    codes: list[str],
    start: date,
    end: date,
    *,
    counts: list[dict[str, Any]],
    plan_state: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    # Bound the planning work itself.  Without a shared recursion budget, an
    # over-limit single-day-heavy lane can spend an unbounded number of count
    # calls before the caller notices that the eventual plan exceeds the shard
    # or total-row safety boundary.
    state = (
        plan_state
        if plan_state is not None
        else {
            "frontier_shards": 1,
            "expected_rows": 0,
            "count_calls": 0,
        }
    )
    if state["count_calls"] >= max(1, (2 * MAX_SHARDS) - 1):
        raise AwardDownloadError(
            "USAspending preflight exhausted its bounded count-call budget"
        )
    filters = _filters(codes, start, end)
    count = _count(filters)
    state["count_calls"] += 1
    counts.append(
        {
            "naics_codes": list(codes),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            **count,
        }
    )
    if count["calculated_count"] > MAX_TOTAL_PREFLIGHT_ROWS:
        raise AwardDownloadError(
            f"USAspending preflight found {count['calculated_count']} rows, "
            "above the configured total-row safety boundary of "
            f"{MAX_TOTAL_PREFLIGHT_ROWS}"
        )
    if not count["rows_gt_limit"]:
        state["expected_rows"] += count["calculated_count"]
        if state["expected_rows"] > MAX_TOTAL_PREFLIGHT_ROWS:
            raise AwardDownloadError(
                f"USAspending preflight found {state['expected_rows']} rows, "
                "above the configured total-row safety boundary of "
                f"{MAX_TOTAL_PREFLIGHT_ROWS}"
            )
        return [
            {
                "filters": filters,
                "expected_count": count["calculated_count"],
                "maximum_limit": count["maximum_limit"],
            }
        ]
    if len(codes) > 1:
        # Per-NAICS lanes are the first deterministic split.  They also make
        # the provenance useful to a report reader instead of yielding opaque
        # arbitrary chunks.
        expanded_frontier = state["frontier_shards"] + len(codes) - 1
        if expanded_frontier > MAX_SHARDS:
            raise AwardDownloadError(
                f"USAspending preflight requires at least {expanded_frontier} "
                f"shards, above the configured safety boundary of {MAX_SHARDS}"
            )
        state["frontier_shards"] = expanded_frontier
        shards: list[dict[str, Any]] = []
        for code in codes:
            shards.extend(
                _plan_shard([code], start, end, counts=counts, plan_state=state)
            )
        return shards
    expanded_frontier = state["frontier_shards"] + 1
    if expanded_frontier > MAX_SHARDS:
        raise AwardDownloadError(
            f"USAspending preflight requires more than {MAX_SHARDS} shards"
        )
    state["frontier_shards"] = expanded_frontier
    shards = []
    for child_start, child_end in _split_dates(start, end):
        shards.extend(
            _plan_shard(codes, child_start, child_end, counts=counts, plan_state=state)
        )
    return shards


def _request_body(shard: dict[str, Any]) -> dict[str, Any]:
    return {
        "filters": shard["filters"],
        "columns": list(COLUMNS),
        "file_format": "csv",
        "limit": int(shard["maximum_limit"]),
        "spending_level": ["awards"],
    }


def _job_paths(request: dict[str, Any]) -> tuple[str, Path, Path]:
    fingerprint = _fingerprint(request)
    root = _cache_dir()
    return (
        fingerprint,
        root / f"{fingerprint}.job.json",
        root / f"{fingerprint}.zip",
    )


def _absolute_url(value: Any) -> str:
    """Resolve one API-owned job URL without opening an SSRF seam.

    Status and file URLs are returned by USAspending and persisted for resumable
    jobs.  Validate both fresh descriptors and cached state before any request;
    a compromised response or edited cache must not redirect the workstation to
    localhost, cloud metadata, or an attacker-controlled credential sink.
    """
    resolved = urljoin(f"{API_ROOT}/", str(value or ""))
    parts = urlsplit(resolved)
    host = str(parts.hostname or "").casefold()
    if (
        parts.scheme.casefold() != "https"
        or host not in _ALLOWED_JOB_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
    ):
        raise AwardDownloadError("untrusted USAspending download job URL")
    return resolved


def _job_is_current(state: dict[str, Any], today: date) -> bool:
    return state.get("submitted_on") == today.isoformat()


def _parse_zip(
    source: Path | bytes,
    *,
    row_sink: Any,
    max_rows: int,
    max_uncompressed_bytes: int,
) -> tuple[int, list[str], int]:
    """Validate and stream award rows from one bounded USAspending ZIP.

    ZIP metadata is checked before decompression and the actual parse is also
    bounded by row count and per-row character count.  Rows are delivered to a
    sink instead of accumulated in memory.
    """

    archive_source: Any = source if isinstance(source, Path) else io.BytesIO(source)
    try:
        archive = zipfile.ZipFile(archive_source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise AwardDownloadError("USAspending download was not a valid ZIP") from exc
    try:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise AwardDownloadError(
                "USAspending ZIP exceeds the configured archive-member boundary"
            )
        names = sorted(
            info.filename
            for info in members
            if info.filename.casefold().endswith(".csv")
            and "primeawardsummaries" in info.filename.casefold().replace("_", "")
        )
        if not names:
            csv_names = sorted(
                info.filename
                for info in members
                if info.filename.casefold().endswith(".csv")
            )
            if len(csv_names) == 1:
                names = csv_names
        if not names:
            raise AwardDownloadError(
                "USAspending ZIP did not contain a PrimeAwardSummaries CSV"
            )
        if len(names) > MAX_CSV_MEMBERS:
            raise AwardDownloadError(
                "USAspending ZIP exceeds the configured CSV-member boundary"
            )
        selected = [archive.getinfo(name) for name in names]
        if any(info.flag_bits & 0x1 for info in selected):
            raise AwardDownloadError("USAspending ZIP contained an encrypted CSV")
        declared_uncompressed = sum(info.file_size for info in selected)
        declared_compressed = sum(info.compress_size for info in selected)
        if declared_uncompressed > max_uncompressed_bytes:
            raise AwardDownloadError(
                "USAspending ZIP exceeds the configured uncompressed-byte boundary"
            )
        if (
            declared_uncompressed
            > max(1, declared_compressed) * MAX_ZIP_EXPANSION_RATIO
        ):
            raise AwardDownloadError(
                "USAspending ZIP exceeds the configured expansion-ratio boundary"
            )

        row_count = 0
        uncompressed_bytes = 0
        for info in selected:
            with archive.open(info) as handle:
                text = io.TextIOWrapper(
                    handle,
                    encoding="utf-8-sig",
                    newline="",
                )
                reader = csv.DictReader(text)
                try:
                    for row in reader:
                        row_count += 1
                        if row_count > max_rows:
                            raise AwardDownloadError(
                                "USAspending ZIP exceeds the configured row boundary"
                            )
                        normalized = {
                            str(key or "").strip(): str(value or "").strip()
                            for key, value in row.items()
                        }
                        if (
                            sum(
                                len(key) + len(value)
                                for key, value in normalized.items()
                            )
                            > MAX_CSV_ROW_CHARS
                        ):
                            raise AwardDownloadError(
                                "USAspending CSV row exceeds the configured size boundary"
                            )
                        row_sink(normalized)
                except (csv.Error, UnicodeError) as exc:
                    raise AwardDownloadError(
                        "USAspending ZIP contained an invalid CSV"
                    ) from exc
            uncompressed_bytes += info.file_size
            if uncompressed_bytes > max_uncompressed_bytes:
                raise AwardDownloadError(
                    "USAspending ZIP exceeds the configured uncompressed-byte boundary"
                )
        return row_count, names, uncompressed_bytes
    except (zipfile.BadZipFile, OSError) as exc:
        raise AwardDownloadError("USAspending ZIP could not be read safely") from exc
    finally:
        archive.close()


class _RowStore:
    """Disk-backed, deterministic cross-shard award deduplication."""

    _UPSERT = """
        INSERT INTO awards(identity, latest, complete, payload)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(identity) DO UPDATE SET
            latest = excluded.latest,
            complete = excluded.complete,
            payload = excluded.payload
        WHERE excluded.latest > awards.latest
           OR (excluded.latest = awards.latest
               AND excluded.complete > awards.complete)
           OR (excluded.latest = awards.latest
               AND excluded.complete = awards.complete
               AND excluded.payload > awards.payload)
    """

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE awards ("
            "identity TEXT PRIMARY KEY, latest TEXT NOT NULL, "
            "complete INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        self._pending: list[tuple[str, str, int, str]] = []
        self.received = 0

    def add(self, row: dict[str, str]) -> None:
        identity = _row_identity(row)
        latest, complete, lexical = _row_quality(row)
        self._pending.append((identity, latest.isoformat(), complete, lexical))
        self.received += 1
        if len(self._pending) >= 500:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            self.connection.executemany(self._UPSERT, self._pending)
            self.connection.commit()
            self._pending.clear()

    def rows(self) -> Any:
        self.flush()
        cursor = self.connection.execute("SELECT payload FROM awards ORDER BY identity")
        for (payload,) in cursor:
            yield json.loads(payload)

    def distinct_count(self) -> int:
        self.flush()
        return int(self.connection.execute("SELECT COUNT(*) FROM awards").fetchone()[0])

    def close(self) -> None:
        self.connection.close()


def _run_shard(
    shard: dict[str, Any],
    *,
    today: date,
    max_archive_bytes: int,
    max_uncompressed_bytes: int,
    row_sink: Any,
) -> dict[str, Any]:
    request = _request_body(shard)
    fingerprint, state_path, zip_path = _job_paths(request)
    state = _read_json(state_path)
    attempts: list[dict[str, Any]] = []

    if state.get("status") == "complete" and zip_path.exists():
        archive_bytes = zip_path.stat().st_size
        if archive_bytes > max_archive_bytes:
            raise AwardDownloadError(
                "cached USAspending archives exceed the configured total "
                "storage boundary"
            )
        row_count, members, uncompressed_bytes = _parse_zip(
            zip_path,
            row_sink=row_sink,
            max_rows=min(
                MAX_ROWS_PER_ARCHIVE,
                int(shard.get("expected_count") or MAX_ROWS_PER_ARCHIVE),
            ),
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
        advertised = state.get("total_rows")
        if advertised is not None and row_count != int(advertised):
            raise AwardDownloadError(
                "cached USAspending row count does not match job status"
            )
        return {
            "status": "complete",
            "members": members,
            "fingerprint": fingerprint,
            "retrieval_mode": "cache",
            "archive_bytes": archive_bytes,
            "uncompressed_bytes": uncompressed_bytes,
            "row_count": row_count,
            "attempts": [
                {
                    "source": "USAspending award download cache",
                    "status": "complete",
                    "count": row_count,
                }
            ],
        }

    resumable = (
        state.get("status") == "pending"
        and _job_is_current(state, today)
        and state.get("status_url")
    )
    if resumable:
        attempts.append(
            {
                "source": "USAspending award download job",
                "status": "resumed",
                "fingerprint": fingerprint,
            }
        )
    else:
        descriptor = post_json(
            DOWNLOAD_URL,
            json=request,
            retries=1,
            timeout=30.0,
        )
        if not isinstance(descriptor, dict) or not descriptor.get("status_url"):
            raise AwardDownloadError(
                "USAspending download response omitted its status URL"
            )
        state = {
            "status": "pending",
            "submitted_on": today.isoformat(),
            "status_url": _absolute_url(descriptor.get("status_url")),
            "file_name": descriptor.get("file_name"),
            "file_url": _absolute_url(descriptor.get("file_url")),
            "request": request,
        }
        _atomic_json(state_path, state)
        attempts.append(
            {
                "source": "USAspending award download job",
                "status": "submitted",
                "fingerprint": fingerprint,
            }
        )

    started = time.monotonic()
    for poll in range(MAX_POLLS):
        if time.monotonic() - started > POLL_DEADLINE_SECONDS:
            break
        status = get_json(
            _absolute_url(state["status_url"]),
            retries=1,
            timeout=15.0,
        )
        if not isinstance(status, dict):
            raise AwardDownloadError("USAspending job status was not an object")
        job_status = str(status.get("status") or "").casefold()
        if job_status not in PENDING_STATES | TERMINAL_STATES:
            raise AwardDownloadError(
                f"USAspending returned an unknown job state: {job_status or 'blank'}"
            )
        if job_status == "failed":
            state.update(
                {
                    "status": "failed",
                    "message": str(status.get("message") or "job failed"),
                }
            )
            _atomic_json(state_path, state)
            raise AwardDownloadError(f"USAspending download failed: {state['message']}")
        if job_status == "finished":
            if status.get("total_rows") is None:
                raise AwardDownloadError(
                    "USAspending finished status omitted its total row count"
                )
            file_url = _absolute_url(status.get("file_url") or state.get("file_url"))
            incoming_path = zip_path.with_name(f".{zip_path.name}.incoming")
            incoming_path.unlink(missing_ok=True)
            try:
                try:
                    archive_bytes = download_file(
                        file_url,
                        incoming_path,
                        max_bytes=max_archive_bytes,
                        retries=1,
                        timeout=60.0,
                    )
                except ValueError as exc:
                    raise AwardDownloadError(
                        "USAspending archives exceed the configured total "
                        "storage boundary"
                    ) from exc
                row_count, members, uncompressed_bytes = _parse_zip(
                    incoming_path,
                    row_sink=row_sink,
                    max_rows=min(
                        MAX_ROWS_PER_ARCHIVE,
                        int(shard.get("expected_count") or MAX_ROWS_PER_ARCHIVE),
                    ),
                    max_uncompressed_bytes=max_uncompressed_bytes,
                )
                advertised = status.get("total_rows")
                if advertised is not None and row_count != int(advertised):
                    raise AwardDownloadError(
                        "USAspending downloaded row count does not match job status: "
                        f"{row_count} parsed, {advertised} advertised"
                    )
                incoming_path.replace(zip_path)
            finally:
                incoming_path.unlink(missing_ok=True)
            state.update(
                {
                    "status": "complete",
                    "file_url": file_url,
                    "total_rows": row_count,
                    "completed_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
            )
            _atomic_json(state_path, state)
            attempts.append(
                {
                    "source": "USAspending award download job",
                    "status": "complete",
                    "count": row_count,
                    "polls": poll + 1,
                }
            )
            return {
                "status": "complete",
                "members": members,
                "fingerprint": fingerprint,
                "retrieval_mode": "live",
                "archive_bytes": archive_bytes,
                "uncompressed_bytes": uncompressed_bytes,
                "row_count": row_count,
                "attempts": attempts,
            }
        state.update(
            {
                "status": "pending",
                "last_job_status": job_status,
                "last_checked_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
        )
        _atomic_json(state_path, state)
        if poll + 1 < MAX_POLLS and POLL_BACKOFF_SECONDS > 0:
            time.sleep(POLL_BACKOFF_SECONDS * (2**poll))

    attempts.append(
        {
            "source": "USAspending award download job",
            "status": "pending",
            "fingerprint": fingerprint,
        }
    )
    return {
        "status": "pending",
        "rows": [],
        "fingerprint": fingerprint,
        "retrieval_mode": "live",
        "attempts": attempts,
    }


def _date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_identity(row: dict[str, str]) -> str:
    unique = row.get("contract_award_unique_key")
    if unique:
        return f"unique:{unique}"
    return "composite:" + "|".join(
        (
            row.get("award_id_piid") or "",
            row.get("awarding_agency_name") or "",
            row.get("awarding_sub_agency_name") or "",
            row.get("parent_award_id_piid") or "",
        )
    )


def _row_quality(row: dict[str, str]) -> tuple[date, int, str]:
    latest = _date_value(row.get("award_latest_action_date")) or date.min
    complete = sum(1 for value in row.values() if str(value or "").strip())
    lexical = json.dumps(row, sort_keys=True, separators=(",", ":"))
    return latest, complete, lexical


def _dedupe(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    by_award: dict[str, dict[str, str]] = {}
    for row in rows:
        identity = _row_identity(row)
        existing = by_award.get(identity)
        if existing is None or _row_quality(row) > _row_quality(existing):
            by_award[identity] = row
    ordered = [by_award[key] for key in sorted(by_award)]
    return ordered, len(rows) - len(ordered)


def _award_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text
    return urljoin("https://www.usaspending.gov/", text.lstrip("/"))


def _normalize_rows(
    rows: Any,
    *,
    as_of: date,
    window_end: date | None,
    duplicate_count: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if duplicate_count is None:
        deduped, duplicate_count = _dedupe(list(rows))
    else:
        deduped = rows
    normalized: list[dict[str, Any]] = []
    expired = 0
    outside_window = 0
    unknown_end = 0
    for row in deduped:
        award_type = str(row.get("award_type_code") or "").strip().upper()
        is_idv = award_type.startswith("IDV_")
        end_field = (
            "ordering_period_end_date"
            if is_idv
            else "period_of_performance_current_end_date"
        )
        effective_end = _date_value(row.get(end_field))
        if effective_end is not None and effective_end < as_of:
            expired += 1
            continue
        if (
            window_end is not None
            and effective_end is not None
            and effective_end > window_end
        ):
            outside_window += 1
            continue
        expiration_state = "active_in_window"
        if effective_end is None:
            unknown_end += 1
            expiration_state = "unknown_end_date"

        amount_candidates = (
            ("current_total_value_of_award", "Current total award value"),
            ("potential_total_value_of_award", "Potential total award value"),
            ("total_obligated_amount", "Total obligated amount"),
        )
        amount = None
        amount_basis = ""
        for field, basis in amount_candidates:
            amount = _number(row.get(field))
            if amount is not None:
                amount_basis = basis
                break
        normalized.append(
            {
                "contract_award_unique_key": row.get("contract_award_unique_key"),
                "award_id": row.get("award_id_piid"),
                "parent_award_id": row.get("parent_award_id_piid"),
                "award_type_code": award_type or None,
                "recipient": row.get("recipient_name") or "UNKNOWN",
                "recipient_uei": row.get("recipient_uei") or None,
                "amount": amount,
                "amount_basis": amount_basis,
                "total_obligated_amount": _number(row.get("total_obligated_amount")),
                "awarding_agency": row.get("awarding_agency_name") or None,
                "awarding_sub_agency": row.get("awarding_sub_agency_name") or None,
                "base_action_date": row.get("award_base_action_date") or None,
                "latest_action_date": row.get("award_latest_action_date") or None,
                "end_date": effective_end.isoformat() if effective_end else None,
                "expiration_basis": end_field,
                "expiration_state": expiration_state,
                "description": row.get("prime_award_base_transaction_description")
                or None,
                "naics": row.get("naics_code") or None,
                "naics_description": row.get("naics_description") or None,
                "url": _award_url(row.get("usaspending_permalink")),
            }
        )
        if len(normalized) > MAX_NORMALIZED_AWARDS:
            raise AwardDownloadError(
                "USAspending result exceeds the configured normalized-award boundary"
            )
    normalized.sort(
        key=lambda row: (
            row.get("end_date") or "9999-99-99",
            row.get("award_id") or "",
            row.get("contract_award_unique_key") or "",
        )
    )
    return normalized, {
        "duplicates_removed": duplicate_count,
        "known_expired_removed": expired,
        "outside_window_removed": outside_window,
        "unknown_end_retained": unknown_end,
    }


def try_award_download(
    *,
    naics_codes: list[str],
    as_of: date,
    window_end: date | None = None,
) -> dict[str, Any]:
    """Return a complete bounded census, or an honest pending/failed result.

    A pending or failed result is deliberately non-fatal to the caller.  The
    Contract Awards adapter can use its older synchronous USAspending sample as
    the final fallback while this job remains resumable for the next sweep.
    """

    codes = _canonical_naics(naics_codes)
    boundary = {
        "start_date": EARLIEST_ACTION_DATE.isoformat(),
        "end_date": as_of.isoformat(),
        "date_type": "action_date",
        "award_type_codes": list(AWARD_TYPE_CODES),
        "naics_codes": codes,
        "expiration_as_of": as_of.isoformat(),
        "expiration_window_end": window_end.isoformat() if window_end else None,
        "safety_limits": {
            "total_preflight_rows": MAX_TOTAL_PREFLIGHT_ROWS,
            "total_archive_bytes": MAX_TOTAL_ARCHIVE_BYTES,
            "rows_per_archive": MAX_ROWS_PER_ARCHIVE,
            "archive_members": MAX_ARCHIVE_MEMBERS,
            "csv_members": MAX_CSV_MEMBERS,
            "total_uncompressed_bytes": MAX_TOTAL_UNCOMPRESSED_BYTES,
            "zip_expansion_ratio": MAX_ZIP_EXPANSION_RATIO,
            "csv_row_characters": MAX_CSV_ROW_CHARS,
            "normalized_awards": MAX_NORMALIZED_AWARDS,
            "maximum_shards": MAX_SHARDS,
        },
    }
    master_fingerprint = _fingerprint(
        {"boundary": boundary, "columns": list(COLUMNS), "level": "awards"}
    )
    result_path = _cache_dir() / f"{master_fingerprint}.result.json"
    cached = _read_json(result_path)
    if cached.get("status") == "complete":
        cached["retrieval_mode"] = "cache"
        cached["source_mode"] = "cached_usaspending_async_download"
        return cached

    if not codes:
        return {
            "status": "failed",
            "error": "no NAICS codes supplied",
            "fingerprint": master_fingerprint,
            "coverage_boundary": boundary,
            "attempts": [],
        }

    counts: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    try:
        shards = _plan_shard(
            codes,
            EARLIEST_ACTION_DATE,
            as_of,
            counts=counts,
        )
        expected_rows = sum(int(shard["expected_count"]) for shard in shards)
        if len(shards) > MAX_SHARDS:
            raise AwardDownloadError(
                f"USAspending preflight requires {len(shards)} shards, above "
                f"the configured safety boundary of {MAX_SHARDS}"
            )
        if expected_rows > MAX_TOTAL_PREFLIGHT_ROWS:
            raise AwardDownloadError(
                f"USAspending preflight found {expected_rows} rows, above the "
                "configured total-row safety boundary of "
                f"{MAX_TOTAL_PREFLIGHT_ROWS}"
            )
        cache_root = _cache_dir()
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".award-row-store-",
            dir=cache_root,
        ) as temporary_root:
            store = _RowStore(Path(temporary_root) / "awards.sqlite3")
            try:
                pending = False
                retrieval_modes: set[str] = set()
                members: list[str] = []
                archive_bytes = 0
                uncompressed_bytes = 0
                downloaded_rows = 0
                for shard in shards:
                    if int(shard["expected_count"]) == 0:
                        attempts.append(
                            {
                                "source": "USAspending award download preflight",
                                "status": "complete",
                                "count": 0,
                            }
                        )
                        continue
                    # Job freshness follows the wall-clock submission day, not
                    # a report's potentially backdated as-of boundary.
                    shard_result = _run_shard(
                        shard,
                        today=date.today(),
                        max_archive_bytes=(MAX_TOTAL_ARCHIVE_BYTES - archive_bytes),
                        max_uncompressed_bytes=(
                            MAX_TOTAL_UNCOMPRESSED_BYTES - uncompressed_bytes
                        ),
                        row_sink=store.add,
                    )
                    attempts.extend(shard_result.get("attempts") or [])
                    retrieval_modes.add(
                        str(shard_result.get("retrieval_mode") or "live")
                    )
                    if shard_result.get("status") == "pending":
                        pending = True
                        continue
                    archive_bytes += int(shard_result.get("archive_bytes") or 0)
                    uncompressed_bytes += int(
                        shard_result.get("uncompressed_bytes") or 0
                    )
                    downloaded_rows += int(shard_result.get("row_count") or 0)
                    members.extend(shard_result.get("members") or [])
                if pending:
                    return {
                        "status": "pending",
                        "fingerprint": master_fingerprint,
                        "coverage_boundary": boundary,
                        "preflight_counts": counts,
                        "shard_count": len(shards),
                        "attempts": attempts,
                        "retrieval_mode": "live",
                    }

                if downloaded_rows != expected_rows:
                    raise AwardDownloadError(
                        "USAspending preflight and downloaded row totals differ: "
                        f"{expected_rows} expected, {downloaded_rows} downloaded"
                    )
                distinct_rows = store.distinct_count()
                records, local_counts = _normalize_rows(
                    store.rows(),
                    as_of=as_of,
                    window_end=window_end,
                    duplicate_count=downloaded_rows - distinct_rows,
                )
            finally:
                store.close()
        result = {
            "status": "complete",
            "source_mode": "live_usaspending_async_download",
            "coverage_status": "complete_within_boundary",
            "coverage_boundary": boundary,
            "fingerprint": master_fingerprint,
            "retrieval_mode": ("cache" if retrieval_modes == {"cache"} else "live"),
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "preflight_counts": counts,
            "shard_count": len(shards),
            "downloaded_rows": downloaded_rows,
            "archive_bytes": archive_bytes,
            "uncompressed_bytes": uncompressed_bytes,
            "normalized_awards": len(records),
            "row_count_verified": True,
            "archive_members": sorted(set(members)),
            **local_counts,
            "records": records,
            "attempts": attempts,
            "provenance": {
                "source": "USAspending.gov",
                "count_endpoint": COUNT_URL,
                "download_endpoint": DOWNLOAD_URL,
                "coverage": (
                    "Prime contract and IDV award summaries with an action date "
                    "from 2007-10-01 through the as-of date in the requested "
                    "NAICS lanes; known expiration dates filtered locally"
                ),
            },
        }
        _atomic_json(result_path, result)
        return result
    except Exception as exc:  # noqa: BLE001 - caller retains synchronous fallback
        return {
            "status": "failed",
            "error": str(exc),
            "fingerprint": master_fingerprint,
            "coverage_boundary": boundary,
            "preflight_counts": counts,
            "attempts": attempts,
            "retrieval_mode": "live",
        }
