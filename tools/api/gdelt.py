"""GDELT 2.0 DOC adapter — machine-scale news beyond the trade press (keyless).

The trade-RSS layer covers the federal IT press. GDELT indexes global news at
machine scale — agency announcements, breach coverage, budget fights, program
scandals — anything that mentions the client's keywords in the last two weeks,
with URLs. Complements (not replaces) the curated RSS lane.

API (grounded from https://api.gdeltproject.org/api/v2/doc/doc — keyless):
    GET ?query=...&mode=artlist&format=json&maxrecords=N&timespan=14d
    Response: {"articles": [{"url","title","seendate","domain","sourcecountry"}]}
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.provenance import make_provenance_envelope
from tools.atomic_io import atomic_write_text

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMESPAN = "14d"
MAX_RECORDS = 8
MAX_KEYWORD_LANES = 5
MIN_REQUEST_INTERVAL_S = float(
    os.environ.get("LILA_GDELT_MIN_INTERVAL_SECONDS", "5.25")
)
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "gdelt"
_CACHE_SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _wall_time() -> float:
    return time.time()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_GDELT_CACHE_DIR", str(_DEFAULT_CACHE)))


def _query_spec(query: str) -> dict[str, Any]:
    return {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": MAX_RECORDS,
        "timespan": TIMESPAN,
        "sort": "datedesc",
    }


def _cache_path(root: Path, query: str, day: str) -> Path:
    basis = json.dumps(_query_spec(query), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(basis.encode()).hexdigest()[:20]
    return root / f"snapshot_{day}_{digest}.json"


def _normalize_articles(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("GDELT response must be an object")
    if "articles" not in payload:
        raise ValueError("GDELT response has no articles list")
    raw_articles = payload["articles"]
    if not isinstance(raw_articles, list):
        raise ValueError("GDELT response articles must be a list")
    articles = []
    for article in raw_articles:
        if not isinstance(article, dict) or not article.get("url"):
            continue
        articles.append(
            {
                "title": article.get("title"),
                "url": article.get("url"),
                "seen": article.get("seen") or article.get("seendate"),
                "domain": article.get("domain"),
                "country": article.get("country") or article.get("sourcecountry"),
            }
        )
    return articles


def _read_current_cache(path: Path, query: str) -> tuple[list[dict], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GDELT cache root must be an object")
    if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
        raise ValueError("GDELT cache schema version does not match")
    if payload.get("query_spec") != _query_spec(query):
        raise ValueError("GDELT cache query does not match request")
    retrieved_at = str(payload.get("retrieved_at") or "").strip()
    if not retrieved_at:
        raise ValueError("GDELT cache has no retrieval timestamp")
    return _normalize_articles({"articles": payload.get("articles")}), retrieved_at


def _write_current_cache(
    path: Path,
    *,
    query: str,
    retrieved_at: str,
    articles: list[dict],
) -> None:
    snapshot = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "query_spec": _query_spec(query),
        "retrieved_at": retrieved_at,
        "articles": articles,
    }
    atomic_write_text(
        str(path), json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    )


def _pace_request(root: Path) -> None:
    """Serialize starts with a safety margin above GDELT's five-second floor."""

    root.mkdir(parents=True, exist_ok=True)
    pacer_path = root / ".request-pacer"
    with pacer_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read().strip()
        try:
            previous = float(raw) if raw else None
        except ValueError:
            previous = None
        now = _wall_time()
        if previous is not None:
            delay = MIN_REQUEST_INTERVAL_S - (now - previous)
            if delay > 0:
                _sleep(delay)
                now = _wall_time()
        handle.seek(0)
        handle.truncate()
        handle.write(f"{now:.6f}\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _public_detail(
    *, successful: int, attempted: int, failed: int, omitted: int
) -> str | None:
    if not failed and not omitted:
        return None
    detail = f"GDELT returned {successful} of {attempted} attempted keyword lanes"
    if omitted:
        detail += f" · {omitted} requested lanes omitted by respectful pacing cap"
    return detail


@register_source
class GdeltSource(DataSource):
    name = "gdelt"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        # GDELT asks callers to leave five seconds between requests. The same
        # cross-process pacer protects both health checks and data pulls.
        try:
            _pace_request(_cache_dir())
            get_json(
                DOC_URL,
                params={
                    "query": "cybersecurity",
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": 1,
                    "timespan": "1d",
                },
                timeout=20.0,
                retries=1,
            )
            return True, "api.gdeltproject.org reachable (keyless, paced)"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {exc}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Recent articles per keyword lane plus truthful source provenance."""

        now = _utc_now()
        day = now.date().isoformat()
        root = _cache_dir()
        root.mkdir(parents=True, exist_ok=True)
        requested = [kw for kw in (query.keywords or []) if kw.strip('"')]
        selected = requested[:MAX_KEYWORD_LANES]
        omitted = requested[MAX_KEYWORD_LANES:]
        out: dict[str, Any] = {}
        attempts: list[dict[str, Any]] = []
        retrievals: list[str] = []
        retrieved_stamps: list[str] = []
        successful_lanes = 0
        failed_lanes = 0

        for keyword in selected:
            term = keyword.strip('"')
            api_query = f'"{term}"' if " " in term else term
            current = _cache_path(root, api_query, day)
            if current.exists():
                try:
                    articles, retrieved_at = _read_current_cache(
                        current, api_query
                    )
                    attempts.append(
                        {
                            "source": f"gdelt_current_day_cache:{term}",
                            "status": "success",
                            "count": len(articles),
                            "retrieved_at": retrieved_at,
                            "data_as_of": day,
                        }
                    )
                    retrievals.append("cache")
                    retrieved_stamps.append(retrieved_at)
                    successful_lanes += 1
                    if articles:
                        out[keyword] = articles
                    continue
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    attempts.append(
                        {
                            "source": f"gdelt_current_day_cache:{term}",
                            "status": "failed",
                            "error": f"invalid current-day cache: {exc}",
                        }
                    )

            try:
                _pace_request(root)
                payload = get_json(
                    DOC_URL,
                    params=_query_spec(api_query),
                    timeout=45.0,
                    retries=1,
                )
                articles = _normalize_articles(payload)
                retrieved_at = _utc_now().isoformat(timespec="seconds")
                _write_current_cache(
                    current,
                    query=api_query,
                    retrieved_at=retrieved_at,
                    articles=articles,
                )
                attempts.append(
                    {
                        "source": f"gdelt_live:{term}",
                        "status": "success",
                        "count": len(articles),
                        "retrieved_at": retrieved_at,
                        "data_as_of": day,
                    }
                )
                retrievals.append("live")
                retrieved_stamps.append(retrieved_at)
                successful_lanes += 1
                if articles:
                    out[keyword] = articles
            except Exception as exc:  # noqa: BLE001 - preserve per-lane return
                failed_lanes += 1
                attempts.append(
                    {
                        "source": f"gdelt_live:{term}",
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        for keyword in omitted:
            term = keyword.strip('"')
            attempts.append(
                {
                    "source": f"gdelt_not_run:{term}",
                    "status": "not-run",
                }
            )

        if failed_lanes and not successful_lanes:
            failure = next(
                attempt["error"]
                for attempt in attempts
                if attempt["status"] == "failed"
                and attempt["source"].startswith("gdelt_live:")
            )
            raise RuntimeError(
                f"GDELT failed for all {len(selected)} attempted keyword lanes: "
                f"{failure}"
            )

        if "live" in retrievals and "cache" in retrievals:
            mode = "live_plus_current_day_cache"
            retrieval_mode = "live"
        elif "live" in retrievals:
            mode = "live_public_api"
            retrieval_mode = "live"
        elif "cache" in retrievals:
            mode = "current_day_cache"
            retrieval_mode = "stored"
        else:
            mode = "not_requested"
            retrieval_mode = "stored"

        omitted_count = len(omitted)
        partial = bool(failed_lanes or omitted_count)
        limitations = []
        if failed_lanes:
            limitations.append(
                "Results contain only keyword lanes returned successfully by GDELT"
            )
        if omitted_count:
            limitations.append(
                f"{omitted_count} requested keyword lanes were omitted after the "
                f"{MAX_KEYWORD_LANES}-lane respectful pacing cap"
            )
        record_count = sum(
            len(value) for key, value in out.items() if not key.startswith("_")
        )
        provenance = make_provenance_envelope(
            self.name,
            status="partial" if partial else "complete",
            mode=mode,
            retrieval_mode=retrieval_mode,
            retrieved_at=(
                min(retrieved_stamps) if retrieved_stamps else _utc_now()
            ),
            data_as_of=day,
            record_count=record_count,
            attempts=attempts,
            limitations=limitations,
            public_detail=_public_detail(
                successful=successful_lanes,
                attempted=len(selected),
                failed=failed_lanes,
                omitted=omitted_count,
            ),
        )
        out["_provenance"] = provenance.model_dump(mode="json", exclude_none=True)
        return out
