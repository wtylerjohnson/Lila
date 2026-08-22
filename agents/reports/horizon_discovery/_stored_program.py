"""Strict helpers for already-collected PROGRAM-tier payloads.

This module performs no I/O.  It accepts only the normalized rows persisted by
``tools.api.program_cache`` and leaves every evidence identity field unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse


def is_official_program_url(value: Any) -> bool:
    """Whether a canonical PROGRAM citation is official HTTPS."""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme.lower() == "https"
        and bool(host)
        and (host.endswith(".gov") or host.endswith(".mil"))
    )


def stored_program_records(
    payload: Mapping[str, Any],
    *,
    source: str,
) -> Iterator[dict[str, Any]]:
    """Yield exact, provenance-complete rows for one expected source.

    A malformed row is ignored rather than repaired.  In particular, this
    seam never invents a record id, buyer, retrieval time, or data-as-of date.
    This is the shared raw-row seam used by both Horizon discovery and C1;
    it also enforces an official HTTPS .gov/.mil canonical URL.
    """
    records = payload.get("records")
    if not isinstance(records, list):
        return
    for value in records:
        if not isinstance(value, dict):
            continue
        if value.get("tier") != "program" or value.get("source") != source:
            continue
        required = (
            "record_id",
            "canonical_url",
            "retrieved_at",
            "data_as_of",
            "agency",
        )
        if any(
            not isinstance(value.get(field), str)
            or not value[field].strip()
            for field in required
        ):
            continue
        if not is_official_program_url(value["canonical_url"]):
            continue
        try:
            retrieved = datetime.fromisoformat(
                value["retrieved_at"].strip().replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            continue
        yield value
