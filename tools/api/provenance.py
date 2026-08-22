"""Shared provenance envelope for source payloads.

Adapters may emit this shape directly under _provenance. The reader also
normalizes the two legacy payload families so coverage, freshness, and future
collectors consume one contract instead of growing source-specific branches.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field


ProvenanceStatus = Literal["complete", "partial", "failed"]
RetrievalMode = Literal["live", "official-cache", "stored", "unknown"]


class ProvenanceAttempt(BaseModel):
    """One child origin attempted inside a source-family collector."""

    source: str
    status: Literal["success", "partial", "failed", "not-run"]
    count: Optional[int] = Field(default=None, ge=0)
    retrieved_at: Optional[datetime] = None
    data_as_of: Optional[str] = None
    error: Optional[str] = None


class ProvenanceEnvelope(BaseModel):
    """Source-family provenance independent of vendor payload shape."""

    schema_version: Literal[1] = 1
    source: str
    status: ProvenanceStatus = "complete"
    mode: str = "unspecified"
    retrieval_mode: RetrievalMode = "unknown"
    fallback: bool = False
    stale: bool = False
    retrieved_at: Optional[datetime] = None
    data_as_of: Optional[str] = None
    record_count: Optional[int] = Field(default=None, ge=0)
    attempts: list[ProvenanceAttempt] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    public_detail: Optional[str] = None

    def public_projection(self) -> dict[str, Any]:
        """Only coverage-safe fields; provider errors never cross this seam."""
        return self.model_dump(
            mode="json",
            exclude={"attempts", "limitations"},
            exclude_none=True,
        )


def _utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_of(value: Any) -> Optional[str]:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _snapshot_source_label(source: str) -> str:
    """Return a public source name without assuming every cache is SBIR."""
    if source in {"sbir", "sbir_gov"}:
        return "SBIR"
    try:
        from tools.api.source_catalog import source_spec

        return source_spec(source).label
    except KeyError:
        words = source.replace("_", " ").replace(".", " ").split()
        return " ".join(word.upper() if len(word) <= 3 else word.title()
                        for word in words) or "official source"


def _attempt(value: Any, index: int) -> ProvenanceAttempt:
    row = value if isinstance(value, Mapping) else {}
    raw_status = str(row.get("status") or "").strip().lower()
    if raw_status in {"success", "ok", "returned", "complete"}:
        status = "success"
    elif raw_status in {"partial", "incomplete"}:
        status = "partial"
    elif raw_status in {"not_run", "not-run", "disabled"}:
        status = "not-run"
    elif raw_status in {"failed", "failure", "error"}:
        status = "failed"
    elif row.get("ok") is True:
        status = "partial" if row.get("partial") is True else "success"
    else:
        status = "failed"
    count = row.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = None
    return ProvenanceAttempt(
        source=str(row.get("source") or f"origin_{index}"),
        status=status,
        count=count,
        retrieved_at=_utc(row.get("retrieved_at")),
        data_as_of=_as_of(row.get("data_as_of")),
        error=str(row.get("error")) if row.get("error") else None,
    )


def make_provenance_envelope(
    source: str,
    *,
    status: ProvenanceStatus = "complete",
    mode: str = "unspecified",
    retrieval_mode: RetrievalMode = "unknown",
    fallback: bool = False,
    stale: bool = False,
    retrieved_at: Any = None,
    data_as_of: Any = None,
    record_count: Optional[int] = None,
    attempts: Any = None,
    limitations: Any = None,
    public_detail: Optional[str] = None,
) -> ProvenanceEnvelope:
    """Build the canonical envelope with normalized timestamps/attempts."""
    attempt_rows = [
        _attempt(row, index)
        for index, row in enumerate(
            attempts if isinstance(attempts, (list, tuple)) else (), start=1)
    ]
    limits = [
        str(value).strip()
        for value in (
            limitations if isinstance(limitations, (list, tuple))
            else ([limitations] if limitations else [])
        )
        if str(value or "").strip()
    ]
    return ProvenanceEnvelope(
        source=source,
        status=status,
        mode=mode,
        retrieval_mode=retrieval_mode,
        fallback=fallback,
        stale=stale,
        retrieved_at=_utc(retrieved_at),
        data_as_of=_as_of(data_as_of),
        record_count=record_count,
        attempts=attempt_rows,
        limitations=limits,
        public_detail=public_detail,
    )


def provenance_from_payload(
    payload: Any,
    *,
    source: str,
) -> Optional[ProvenanceEnvelope]:
    """Read canonical or legacy provenance without mutating the payload."""
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("_provenance")
    if not isinstance(raw, Mapping):
        raw = payload.get("provenance")
    raw = raw if isinstance(raw, Mapping) else {}

    canonical = raw.get("schema_version") == 1 and raw.get("source")
    if canonical:
        try:
            return ProvenanceEnvelope.model_validate(raw)
        except (TypeError, ValueError):
            return None

    mode = str(
        raw.get("mode") or payload.get("source_mode") or "unspecified")
    stale = bool(
        raw.get("stale") is True
        or payload.get("stale") is True
        or "stale" in mode
    )
    fallback = bool(
        raw.get("fallback") is True
        or payload.get("fallback_used") is True
        or "fallback" in mode
        or "topics_listing" in mode
        or raw.get("cache_origin") == "topics_listing"
    )
    raw_status = str(raw.get("status") or "").strip().lower()
    partial = bool(
        raw_status in {"partial", "incomplete"}
        or raw.get("partial") is True
        or raw.get("complete") is False
        or (
            raw.get("truncated") is True
            and raw.get("boundary_complete") is not True
        )
        or payload.get("partial") is True
        or (
            payload.get("truncated") is True
            and payload.get("boundary_complete") is not True
        )
        or payload.get("errors")
        or payload.get("failed_terms")
        or payload.get("skipped_terms")
        or payload.get("omitted_terms")
        or payload.get("failed_naics_lanes")
        or payload.get("omitted_naics_lanes")
    )
    failed = bool(
        raw_status in {"failed", "failure", "error"}
        or payload.get("error")
        or raw.get("error")
    )
    status: ProvenanceStatus = (
        "failed" if failed else "partial" if partial else "complete")
    retrieval_mode: RetrievalMode
    if "cache" in mode:
        retrieval_mode = "official-cache"
    elif mode.startswith("live_") or mode == "live":
        retrieval_mode = "live"
    elif mode != "unspecified":
        retrieval_mode = "stored"
    else:
        retrieval_mode = "unknown"

    retrieved_at = (
        raw.get("retrieved_at") or payload.get("retrieved_at")
        or payload.get("generated_at")
    )
    data_as_of = (
        raw.get("data_as_of") or payload.get("data_as_of")
        or payload.get("as_of") or payload.get("status_as_of")
    )
    attempts = (
        raw.get("attempts") or raw.get("source_attempts")
        or payload.get("source_attempts") or ()
    )
    limitations = raw.get("limitations") or raw.get("limitation") or ()
    record_count = next(
        (
            value for value in (
                raw.get("record_count"),
                raw.get("records_received"),
                raw.get("records"),
            )
            if value is not None
        ),
        None,
    )
    if not isinstance(record_count, int) or isinstance(record_count, bool):
        record_count = None

    detail: Optional[str] = (
        str(raw.get("public_detail")).strip()
        if raw.get("public_detail") else None
    )
    snapshot_day = str(retrieved_at or "")[:10]
    suffix = f" as of {snapshot_day}" if snapshot_day else ""
    if detail is None and mode == "live_usaspending_fallback":
        attempted = max(0, int(payload.get("attempted_naics_lanes") or 0))
        successful = max(0, int(payload.get("successful_naics_lanes") or 0))
        omitted = max(0, int(payload.get("omitted_naics_lanes") or 0))
        detail = "Live USAspending.gov official fallback"
        if attempted:
            detail = (
                f"USAspending.gov official fallback · {successful} of "
                f"{attempted} attempted NAICS lanes returned"
            )
            if omitted:
                detail += f" · {omitted} requested lanes omitted"
    elif detail is None and mode == "stale_official_cache":
        label = _snapshot_source_label(source)
        detail = f"Official {label} snapshot{suffix}; no live API return"
    elif detail is None and mode == "live_official_topics_listing":
        detail = "Live SBIR.gov official Topics listing; API unavailable"
    elif detail is None and mode == "official_daily_cache":
        label = _snapshot_source_label(source)
        detail = (
            f"Current-day official {label} snapshot{suffix}; no live request "
            "this refresh"
        )

    if not raw and mode == "unspecified" and not any(
        (retrieved_at, data_as_of, payload.get("source_attempts"),
         payload.get("fallback_used"), payload.get("source_mode"))
    ):
        return None
    return make_provenance_envelope(
        source,
        status=status,
        mode=mode,
        retrieval_mode=retrieval_mode,
        fallback=fallback,
        stale=stale,
        retrieved_at=retrieved_at,
        data_as_of=data_as_of,
        record_count=record_count,
        attempts=attempts,
        limitations=limitations,
        public_detail=detail,
    )
