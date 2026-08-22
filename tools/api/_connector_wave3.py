"""Shared, dependency-light contracts for source-gauntlet connector wave 3.

The helpers in this file intentionally do not register anything.  They keep
the eight adapters' fixture seams, stable identifiers, receipts, and failure
envelopes consistent without changing the repository's shared catalog.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from tools.api.provenance import make_provenance_envelope


def text(value: Any) -> Optional[str]:
    value = " ".join(str(value or "").split())
    return value or None


def stable_id(source: str, *parts: Any) -> str:
    basis = "\x1f".join((text(part) or "").casefold() for part in parts)
    return f"{source}:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def scoped_terms(query: Any) -> list[str]:
    values: list[Any] = []
    for attr in ("keywords", "agencies", "naics_codes", "psc_codes"):
        values.extend(getattr(query, attr, None) or [])
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = text(value)
        key = (value or "").casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def matches(row: dict[str, Any], terms: Iterable[str]) -> bool:
    wanted = [(text(term) or "").casefold() for term in terms]
    wanted = [term for term in wanted if term]
    if not wanted:
        return True
    haystack = json.dumps(row, sort_keys=True, default=str).casefold()
    return any(term in haystack for term in wanted)


def digits_prefix(value: Any, requested: Iterable[str]) -> bool:
    wanted = [re.sub(r"\D", "", str(code)) for code in requested]
    wanted = [code for code in wanted if code]
    if not wanted:
        return True
    native = re.sub(r"\D", "", str(value or ""))
    return any(native.startswith(code) for code in wanted)


def result(
    source: str,
    *,
    items: list[dict[str, Any]],
    status: str = "complete",
    mode: str,
    limitations: Optional[list[str]] = None,
    attempts: Optional[list[dict[str, Any]]] = None,
    url: Optional[str] = None,
    query: Optional[dict[str, Any]] = None,
    raw: Optional[bytes] = None,
    receipt_payload: Any = None,
    content_kind: Optional[str] = None,
    data_as_of: Optional[str] = None,
    total_matched: Optional[int] = None,
) -> dict[str, Any]:
    """Return the canonical fail-soft envelope used by the connector wave."""
    receipt_bytes = raw
    receipt_kind = content_kind
    if receipt_bytes is None and (receipt_payload is not None or items):
        # Some official transports expose only decoded JSON/table values to an
        # injected adapter seam.  A canonical native-response receipt is the
        # immutable equivalent of the HTTP-body hash; never leave ingested
        # records without either form of binding.
        receipt_bytes = json.dumps(
            receipt_payload if receipt_payload is not None else items,
            sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")
        receipt_kind = (
            content_kind
            or "application/json; profile=canonical-native-response"
        )
    envelope = make_provenance_envelope(
        source,
        status=status,  # type: ignore[arg-type]
        mode=mode,
        retrieval_mode="live",
        retrieved_at=datetime.now(timezone.utc),
        data_as_of=data_as_of,
        record_count=len(items),
        attempts=attempts or [],
        limitations=limitations or [],
    ).model_dump(mode="json")
    envelope.update({
        "retrieval_url": url,
        "retrieval_query": query,
        "raw_content_sha256": (
            sha256(receipt_bytes) if receipt_bytes is not None else None
        ),
        "raw_content_kind": receipt_kind,
        "normalization_version": "1",
    })
    return {
        "items": items,
        "total_matched": len(items) if total_matched is None else total_matched,
        "_provenance": envelope,
    }


def failure(source: str, *, mode: str, url: str, error: Exception | str,
            limitations: Optional[list[str]] = None) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else error
    out = result(
        source, items=[], status="failed", mode=mode, url=url,
        limitations=[*(limitations or []), f"retrieval failed: {detail}"],
        attempts=[{"source": source, "status": "failed", "error": detail}],
    )
    out["error"] = detail
    return out
