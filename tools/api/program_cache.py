"""Shared durable cache for official PROGRAM-tier source adapters.

These sources describe policy, budgets, research programs, or other forming
demand. They are never notice evidence and can never enter the live-
solicitation ledger. A current-day official snapshot is reused across client
queries; when the live source is unavailable, the newest valid dated snapshot
is returned with stale/partial provenance instead of being represented as live.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from tools.atomic_io import atomic_write_text


@dataclass(frozen=True)
class ProgramPull:
    """One successful official pull before cache/provenance decoration."""

    records: list[dict]
    data_as_of: str
    partial: bool = False
    source_attempts: list[dict] = field(default_factory=list)
    limitations: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_snapshot(
    path: Path,
    *,
    expected_source: str | None = None,
    expected_canonical_url: str | None = None,
) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PROGRAM cache root must be an object")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("PROGRAM cache records must be a list")
    source = str(payload.get("source") or "").strip()
    canonical_url = str(payload.get("canonical_url") or "").strip()
    if expected_source is not None and source != expected_source:
        raise ValueError(
            f"PROGRAM cache source {source!r} does not match "
            f"{expected_source!r}"
        )
    if (expected_canonical_url is not None
            and canonical_url != expected_canonical_url):
        raise ValueError("PROGRAM cache canonical origin does not match adapter")
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("PROGRAM cache record must be an object")
        missing = {
            "record_id",
            "canonical_url",
            "retrieved_at",
            "data_as_of",
            "tier",
        } - row.keys()
        if missing:
            raise ValueError(f"PROGRAM cache record missing {sorted(missing)}")
        if row["tier"] != "program":
            raise ValueError("PROGRAM cache record has a non-program tier")
        if expected_source is not None and row.get("source") != expected_source:
            raise ValueError("PROGRAM cache record source does not match adapter")
    if not payload.get("retrieved_at") or not payload.get("data_as_of"):
        raise ValueError("PROGRAM cache missing snapshot timestamps")
    return payload


def _latest_valid_cache(
    root: Path,
    *,
    exclude: set[Path],
    expected_source: str,
    expected_canonical_url: str,
) -> tuple[Path, dict] | None:
    if not root.exists():
        return None
    for candidate in reversed(sorted(root.glob("snapshot_*.json"))):
        if candidate in exclude:
            continue
        try:
            return candidate, _read_snapshot(
                candidate,
                expected_source=expected_source,
                expected_canonical_url=expected_canonical_url,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _normalize_records(
    records: list[dict],
    *,
    source: str,
    canonical_url: str,
    retrieved_at: str,
    data_as_of: str,
) -> list[dict]:
    by_id: dict[str, dict] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("official PROGRAM record must be an object")
        row = dict(raw)
        record_id = str(row.get("record_id") or "").strip()
        if not record_id:
            raise ValueError("official PROGRAM record is missing record_id")
        if row.get("tier") not in (None, "program"):
            raise ValueError("official PROGRAM adapter emitted a non-program tier")
        row["record_id"] = record_id
        row["tier"] = "program"
        row.setdefault("source", source)
        row.setdefault("canonical_url", canonical_url)
        row.setdefault("retrieved_at", retrieved_at)
        row.setdefault("data_as_of", data_as_of)
        if not row["canonical_url"]:
            raise ValueError(f"PROGRAM record {record_id!r} has no canonical URL")
        if record_id in by_id:
            raise ValueError(f"duplicate PROGRAM record id: {record_id}")
        by_id[record_id] = row
    return [by_id[key] for key in sorted(by_id)]


def _provenance(
    *,
    source: str,
    canonical_url: str,
    mode: str,
    status: str,
    retrieved_at: str,
    data_as_of: str | None,
    stale: bool,
    partial: bool,
    attempts: list[dict],
    record_count: int,
    limitations: str | None,
    **extra,
) -> dict:
    out = {
        "source": source,
        "canonical_url": canonical_url,
        "mode": mode,
        "status": status,
        "tier": "program",
        "promotion_eligible": False,
        "retrieved_at": retrieved_at,
        "data_as_of": data_as_of,
        "stale": stale,
        "partial": partial,
        "records_received": record_count,
        "source_attempts": attempts,
    }
    if limitations:
        out["limitations"] = limitations
    out.update(extra)
    return out


def cached_program_pull(
    *,
    source: str,
    canonical_url: str,
    cache_dir: Path,
    fetch_live: Callable[[], ProgramPull],
) -> tuple[list[dict], dict]:
    """Fetch, atomically cache, and truthfully fall back for one source."""

    now = _utc_now()
    retrieved_at = now.isoformat(timespec="seconds")
    cache_dir.mkdir(parents=True, exist_ok=True)
    current = cache_dir / f"snapshot_{now.date().isoformat()}.json"
    attempts: list[dict] = []
    invalid: set[Path] = set()

    if current.exists():
        try:
            cached = _read_snapshot(
                current,
                expected_source=source,
                expected_canonical_url=canonical_url,
            )
            records = cached["records"]
            partial = bool(cached.get("partial"))
            attempts.extend(
                row for row in (cached.get("source_attempts") or [])
                if isinstance(row, dict)
            )
            attempts.append({
                "source": "daily_cache",
                "status": "partial" if partial else "success",
            })
            if not partial:
                return records, _provenance(
                    source=source,
                    canonical_url=canonical_url,
                    mode="official_daily_cache",
                    status="success",
                    retrieved_at=str(cached["retrieved_at"]),
                    data_as_of=str(cached["data_as_of"]),
                    stale=False,
                    partial=False,
                    attempts=attempts,
                    record_count=len(records),
                    limitations=cached.get("limitations"),
                )
            # A partial current-day snapshot may represent a transient child
            # outage. Retry live; if it still fails, the validated snapshot is
            # eligible below as an explicitly partial fallback.
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            invalid.add(current)
            attempts.append(
                {
                    "source": "daily_cache",
                    "status": "failed",
                    "error": f"invalid current snapshot: {exc}",
                }
            )

    try:
        pull = fetch_live()
        records = _normalize_records(
            pull.records,
            source=source,
            canonical_url=canonical_url,
            retrieved_at=retrieved_at,
            data_as_of=pull.data_as_of,
        )
        snapshot = {
            "source": source,
            "canonical_url": canonical_url,
            "retrieved_at": retrieved_at,
            "data_as_of": pull.data_as_of,
            "partial": pull.partial,
            "limitations": pull.limitations,
            "source_attempts": pull.source_attempts,
            "records": records,
        }
        atomic_write_text(
            str(current), json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
        )
        live_attempts = pull.source_attempts or [
            {"source": "official_source", "status": "success"}
        ]
        attempts.extend(live_attempts)
        return records, _provenance(
            source=source,
            canonical_url=canonical_url,
            mode="live_official_source",
            status="partial" if pull.partial else "success",
            retrieved_at=retrieved_at,
            data_as_of=pull.data_as_of,
            stale=False,
            partial=pull.partial,
            attempts=attempts,
            record_count=len(records),
            limitations=pull.limitations,
        )
    except Exception as exc:  # noqa: BLE001 - stale official cache is sanctioned
        attempts.append(
            {"source": "official_source", "status": "failed", "error": str(exc)}
        )

    fallback = _latest_valid_cache(
        cache_dir,
        exclude=invalid,
        expected_source=source,
        expected_canonical_url=canonical_url,
    )
    if fallback is not None:
        path, cached = fallback
        cached_at = str(cached["retrieved_at"])
        try:
            age_days = (now.date() - date.fromisoformat(cached_at[:10])).days
        except ValueError:
            age_days = None
        attempts.append(
            {
                "source": "dated_official_cache",
                "status": "success",
                "path": path.name,
            }
        )
        records = cached["records"]
        return records, _provenance(
            source=source,
            canonical_url=canonical_url,
            mode="stale_official_cache",
            status="partial",
            retrieved_at=cached_at,
            data_as_of=str(cached["data_as_of"]),
            stale=True,
            partial=True,
            attempts=attempts,
            record_count=len(records),
            limitations=cached.get("limitations"),
            age_days=age_days,
            live_error=attempts[-2]["error"],
        )

    return [], _provenance(
        source=source,
        canonical_url=canonical_url,
        mode="failure",
        status="failure",
        retrieved_at=retrieved_at,
        data_as_of=None,
        stale=False,
        partial=False,
        attempts=attempts,
        record_count=0,
        limitations=None,
        error=attempts[-1]["error"],
    )
