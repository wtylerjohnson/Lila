"""Shared HTTP helpers for API clients: timeouts + bounded retry on transient errors.

No tenacity dependency — a small explicit backoff keeps the system runnable on a
bare Python + httpx install.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import httpx

_RETRYABLE = {429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 30.0

# FORCE IPv4. Diagnosed 2026-07-03: api.sam.gov connects hung forever at
# sock.connect() — the host advertises IPv6 (AAAA) addresses, and on a network
# with a broken IPv6 route the SYN blackholes with no error. Whether a call
# "worked" depended on which address family got tried first: the entire
# "SAM is unreliable" symptom. Binding the local side to 0.0.0.0 makes every
# call IPv4-only. No public federal API needs IPv6.
_TRANSPORT_KW = {"local_address": "0.0.0.0"}


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        transport=httpx.HTTPTransport(retries=0, **_TRANSPORT_KW),
        follow_redirects=True,
    )


def _request(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
    response_observer=None,
    max_response_bytes: int | None = None,
) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _client(timeout) as c:
                if max_response_bytes is None:
                    resp = c.request(method, url, params=params, json=json, headers=headers)
                    if response_observer is not None:
                        response_observer(resp, resp.content, True)
                else:
                    # Opt-in bounded capture for public resource inventories.
                    # Existing clients retain their original request behavior.
                    captured = bytearray()
                    complete = False
                    stop = time.monotonic() + timeout
                    with c.stream(method, url, params=params, json=json,
                                  headers=headers) as streamed:
                        try:
                            for chunk in streamed.iter_bytes(65536):
                                remaining = max_response_bytes - len(captured)
                                captured.extend(chunk[:remaining])
                                if len(chunk) > remaining:
                                    raise ValueError("response exceeds capture byte boundary")
                                if time.monotonic() > stop:
                                    raise TimeoutError("response exceeds capture time boundary")
                            complete = True
                        finally:
                            if response_observer is not None:
                                response_observer(streamed, bytes(captured), complete)
                        resp = httpx.Response(streamed.status_code,
                                              headers={k: v for k, v in streamed.headers.items()
                                                       if k not in {"content-encoding", "content-length"}},
                                              content=bytes(captured),
                                              request=streamed.request)
            if resp.status_code >= 400:
                # Carry the response BODY in the error — "429" alone hides whether
                # it's a daily cap, a burst throttle, a role problem, or a WAF block.
                body = (resp.text or "").strip()[:300]
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}"
                    + (f" — {body}" if body else " (empty body)"),
                    request=resp.request,
                    response=resp,
                )
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            resp_obj = getattr(exc, "response", None)
            status = getattr(resp_obj, "status_code", None)
            if status is not None and status not in _RETRYABLE:
                raise  # 4xx (other than 429) will fail identically — don't burn retries
            if attempt < retries - 1:
                time.sleep(0.5 * (2**attempt))  # 0.5s, 1s, 2s
    assert last_exc is not None
    raise last_exc


def get_json(url: str, *, params: dict | None = None, **kw) -> Any:
    return _request("GET", url, params=params, **kw)


def post_json(url: str, *, json: dict, headers: dict | None = None, **kw) -> Any:
    headers = {"Content-Type": "application/json", **(headers or {})}
    return _request("POST", url, json=json, headers=headers, **kw)


def get_text(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
) -> str:
    """GET returning raw text (RSS/XML feeds). Same bounded-retry policy."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _client(timeout) as c:
                resp = c.get(url, headers=headers)
            if resp.status_code in _RETRYABLE:
                raise httpx.HTTPStatusError(
                    f"retryable {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.text
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2**attempt))
    assert last_exc is not None
    raise last_exc


def get_bytes(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
    max_bytes: int | None = None,
) -> bytes:
    """GET returning raw bytes with the shared bounded-retry policy.

    Official agencies often publish forecasts as XLSX or PDF files rather than
    JSON.  Keeping the binary transport here gives those adapters the same
    IPv4, timeout, redirect, and retry behavior as the API clients.
    """

    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _client(timeout) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in _RETRYABLE:
                        raise httpx.HTTPStatusError(
                            f"retryable {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    advertised = response.headers.get("content-length")
                    if (
                        max_bytes is not None
                        and advertised is not None
                        and int(advertised) > max_bytes
                    ):
                        raise ValueError(f"response exceeds {max_bytes} byte boundary")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if max_bytes is not None and len(raw) + len(chunk) > max_bytes:
                            raise ValueError(
                                f"response exceeds {max_bytes} byte boundary"
                            )
                        raw.extend(chunk)
                    return bytes(raw)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2**attempt))
    assert last_exc is not None
    raise last_exc


def download_file(
    url: str,
    destination: Path,
    *,
    max_bytes: int,
    headers: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
) -> int:
    """Stream one response to ``destination`` under a hard byte ceiling.

    The file is written atomically in the destination directory.  A missing or
    dishonest ``Content-Length`` cannot bypass the limit because every streamed
    chunk is counted before it is written.  Failed attempts leave neither a
    partial destination nor an unbounded in-memory response behind.
    """

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.download")
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        temporary.unlink(missing_ok=True)
        try:
            with _client(timeout) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in _RETRYABLE:
                        raise httpx.HTTPStatusError(
                            f"retryable {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    advertised = response.headers.get("content-length")
                    if advertised is not None and int(advertised) > max_bytes:
                        raise ValueError(f"response exceeds {max_bytes} byte boundary")
                    received = 0
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            received += len(chunk)
                            if received > max_bytes:
                                raise ValueError(
                                    f"response exceeds {max_bytes} byte boundary"
                                )
                            handle.write(chunk)
            temporary.replace(destination)
            return received
        except (
            httpx.TransportError,
            httpx.HTTPStatusError,
            OSError,
            ValueError,
        ) as exc:
            temporary.unlink(missing_ok=True)
            last_exc = exc
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status is not None and status not in _RETRYABLE:
                raise
            if isinstance(exc, ValueError):
                raise
            if attempt < retries - 1:
                time.sleep(0.5 * (2**attempt))
    assert last_exc is not None
    raise last_exc
