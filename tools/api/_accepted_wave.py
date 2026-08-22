"""Small shared helpers for bounded, receipt-bearing official-source adapters.

This module deliberately returns the exact response bytes.  JSON/text helpers
elsewhere in the repository discard that representation before an adapter can
bind a receipt to it, which is insufficient for the source-gauntlet audit.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tools.api import _http
from tools.api.provenance import make_provenance_envelope


FetchBytes = Callable[..., tuple[bytes, dict[str, str]]]


def fetch_bytes(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
    retries: int = 3,
) -> tuple[bytes, dict[str, str]]:
    """GET exact bytes with the repository's IPv4 transport and bounded retry."""
    last: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            with _http._client(timeout) as client:
                response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return bytes(response.content), {
                str(key): str(value) for key, value in response.headers.items()
            }
        except Exception as exc:  # noqa: BLE001 - httpx transport families vary
            last = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2**attempt))
    assert last is not None
    raise last


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def response_as_of(headers: dict[str, str]) -> Optional[str]:
    for name in ("last-modified", "date"):
        for key, value in headers.items():
            if key.casefold() == name:
                return value
    return None


def receipt(
    *,
    source: str,
    url: str,
    query: dict[str, Any],
    blob: bytes,
    normalized: Any,
    headers: Optional[dict[str, str]] = None,
    method: str = "GET",
    content_kind: str,
    normalization_version: str = "1",
) -> dict[str, Any]:
    """Bind normalized output to the concrete official HTTP response."""
    return {
        "source": source,
        "retrieval_url": url,
        "retrieval_method": method,
        "retrieval_query": query,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "raw_content_sha256": hashlib.sha256(blob).hexdigest(),
        "raw_content_kind": content_kind,
        "normalized_content_sha256": canonical_sha(normalized),
        "normalization_version": normalization_version,
        "http_last_modified": response_as_of(headers or {}),
    }


def payload(
    *,
    source: str,
    mode: str,
    items: list[dict[str, Any]],
    status: str,
    limitations: list[str],
    source_receipt: Optional[dict[str, Any]] = None,
    total_matched: Optional[int] = None,
    public_detail: Optional[str] = None,
) -> dict[str, Any]:
    """Canonical fail-soft envelope shared by this connector wave."""
    env = make_provenance_envelope(
        source,
        status=status,  # type: ignore[arg-type]
        mode=mode,
        retrieval_mode="live",
        retrieved_at=datetime.now(timezone.utc),
        data_as_of=(source_receipt or {}).get("http_last_modified"),
        record_count=len(items),
        limitations=limitations,
        public_detail=public_detail,
    ).model_dump(mode="json")
    if source_receipt:
        env.update({
            key: source_receipt.get(key)
            for key in (
                "retrieval_url", "retrieval_query", "raw_content_sha256",
                "raw_content_kind", "normalization_version",
            )
            if source_receipt.get(key) is not None
        })
    result: dict[str, Any] = {
        "items": items,
        "total_matched": len(items) if total_matched is None else total_matched,
        "_provenance": env,
    }
    if source_receipt:
        result["_receipt"] = source_receipt
    return result
