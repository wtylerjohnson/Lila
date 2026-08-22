"""Small, bounded primitives shared by official PDF forecast adapters.

This module deliberately owns transport and PDF opening only.  Each publisher
keeps its own table contract and normalization because the documents are not a
shared schema (even the two NAVAIR files have materially different columns).
"""

from __future__ import annotations

import io
import time
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import pdfplumber

from tools.api import _http


# SEC rejects generic bot user agents.  The browser prefix plus an operator
# contact follows its automated-access guidance and is also accepted by NAVAIR.
PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 operator@federal-sales-os.local",
    "Accept": "application/pdf,*/*;q=0.8",
}


def fetch_pdf(
    url: str,
    *,
    timeout: float = 60.0,
    retries: int = 3,
) -> tuple[bytes, dict]:
    """Fetch one PDF with bounded retries and return bytes plus HTTP metadata."""

    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _http._client(timeout) as client:
                response = client.get(url, headers=PDF_HEADERS)
            response.raise_for_status()
            blob = bytes(response.content)
            if not blob.startswith(b"%PDF"):
                raise ValueError("official forecast response is not a PDF")
            return blob, dict(response.headers)
        except Exception as exc:  # noqa: BLE001 - transport families vary
            last = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    assert last is not None
    raise last


def pdf_tables(blob: bytes) -> list[list[list[Any]]]:
    """Extract every table from every page in arrival order with pdfplumber."""

    if not blob.startswith(b"%PDF"):
        raise ValueError("official forecast response is not a PDF")
    tables: list[list[list[Any]]] = []
    with pdfplumber.open(io.BytesIO(blob)) as document:
        for page in document.pages:
            tables.extend(page.extract_tables() or [])
    return tables


def clean(value: Any) -> str:
    """Collapse PDF newlines, nonbreaking spaces, and repeated whitespace."""

    return " ".join(str(value or "").replace("\xa0", " ").split())


def stated(value: Any) -> Optional[str]:
    """Return a typed source value, excluding publisher placeholders."""

    value = clean(value)
    if not value or value.casefold() in {
        "tbd", "n/a", "na", "none", "new requirement",
    }:
        return None
    return value


def header_value(headers: dict, name: str) -> Optional[str]:
    """Case-insensitive HTTP header lookup."""

    wanted = name.casefold()
    return next((str(value) for key, value in headers.items()
                 if str(key).casefold() == wanted), None)


def http_date_as_of(headers: dict) -> Optional[str]:
    """Official Last-Modified metadata as an ISO timestamp when supplied."""

    raw = header_value(headers, "last-modified")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw
