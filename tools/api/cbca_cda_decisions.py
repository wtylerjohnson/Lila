"""Civilian Board of Contract Appeals CDA decisions adapter.

Client dimension: post-award risk and incumbency weakness.  CDA decisions expose
performance, payment, termination, and settlement disputes that do not appear
in award listings.  The official index supplies date, case number, appellant,
judge, disposition, and a decision PDF.  A bounded PDF window adds respondent
agency and contract-number clues when the text contains them.

The adapter never crawls the archive: it filters the one index, downloads at
most five recent/relevant PDFs, reads at most ten pages per PDF, caps results,
and keeps successful decisions when another PDF fails.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urljoin

from tools.api import _http
from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.provenance import make_provenance_envelope

SOURCE_NAME = "cbca_cda_decisions"
INDEX_URL = "https://www.cbca.gov/decisions/cda-cases.html"
MAX_PDF_INSPECTIONS = 5
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 10
MAX_TEXT_CHARS = 60_000
MAX_RETURNED = 50

_CASE_RE = re.compile(r"\bCBCA\s+([0-9][0-9A-Z,()\- ]*)", re.IGNORECASE)
_CONTRACT_RE = re.compile(
    r"\b(?:contract|task\s+order|purchase\s+order)\s+"
    r"(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{4,})",
    re.IGNORECASE,
)
_RESPONDENT_RE = re.compile(
    r"\bv\.\s*\n\s*([^\n]{3,160}?),?\s*\n\s*Respondent\b",
    re.IGNORECASE,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any) -> Optional[str]:
    text = " ".join(str(value or "").split())
    return text or None


def _iso_day(value: Any) -> Optional[str]:
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class _IndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._in_tr = False
        self._in_td = False
        self._cells: list[str] = []
        self._parts: list[str] = []
        self._href: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "tr":
            self._in_tr = True
            self._cells = []
            self._href = None
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._parts = []
        elif tag == "a" and self._in_td and self._href is None:
            self._href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_td:
            self._cells.append(" ".join("".join(self._parts).split()))
            self._in_td = False
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if len(self._cells) >= 5 and _iso_day(self._cells[0]) and self._href:
                self.rows.append({
                    "decision_date": _iso_day(self._cells[0]),
                    "case_number": _clean(self._cells[1]),
                    "appellant": _clean(self._cells[2]),
                    "judge": _clean(self._cells[3]),
                    "disposition": _clean(self._cells[4]),
                    "url": urljoin(INDEX_URL, self._href),
                })


def _parse_index(html: str) -> list[dict]:
    parser = _IndexParser()
    parser.feed(html or "")
    parser.close()
    rows = parser.rows
    rows.sort(key=lambda row: (
        row.get("decision_date") or "", row.get("case_number") or "",
        row.get("url") or "",
    ), reverse=True)
    return rows


def _get_bytes(url: str, *, timeout: float = 30.0,
               retries: int = 2) -> bytes:
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _http._client(timeout) as client:
                response = client.get(url)
            if response.status_code in _http._RETRYABLE:
                raise RuntimeError(f"HTTP {response.status_code} fetching CBCA PDF")
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code} fetching CBCA PDF")
            declared = int(response.headers.get("content-length") or 0)
            if declared > MAX_PDF_BYTES or len(response.content) > MAX_PDF_BYTES:
                raise ValueError(f"CBCA PDF exceeds {MAX_PDF_BYTES} byte cap")
            return bytes(response.content)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def _pdf_text(blob: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("pdfplumber is required for CBCA decision ingestion") from exc
    parts: list[str] = []
    length = 0
    with pdfplumber.open(io.BytesIO(blob)) as document:
        for page in document.pages[:MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            parts.append(text)
            length += len(text)
            if length >= MAX_TEXT_CHARS:
                break
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def _stable_id(row: dict) -> str:
    basis = "|".join(str(row.get(key) or "").casefold() for key in (
        "case_number", "decision_date", "url",
    ))
    return "cbca:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _terms(query: SourceQuery) -> list[str]:
    values = list(query.keywords) + list(query.agencies)
    seen: set[str] = set()
    result = []
    for value in values:
        term = str(value or "").strip().strip('"').casefold()
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return result


def _haystack(row: dict, decision_text: str = "") -> str:
    return " ".join(str(row.get(key) or "") for key in (
        "case_number", "appellant", "judge", "disposition",
    )).casefold() + " " + decision_text.casefold()


def _text_fields(text: str) -> dict[str, Any]:
    respondent = _RESPONDENT_RE.search(text or "")
    contracts: list[str] = []
    for match in _CONTRACT_RE.finditer(text or ""):
        value = match.group(1).rstrip(".,;)]").upper()
        if value not in contracts:
            contracts.append(value)
    return {
        "respondent_agency": _clean(respondent.group(1)) if respondent else None,
        "contract_numbers": contracts[:8],
        "decision_text_excerpt": _clean(text[:1000]),
    }


class CbcaCdaDecisionsSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(
        self,
        *,
        fetch_text: Optional[Callable[..., str]] = None,
        fetch_bytes: Optional[Callable[..., bytes]] = None,
        extract_pdf_text: Optional[Callable[[bytes], str]] = None,
    ) -> None:
        self._fetch_text = fetch_text or get_text
        self._fetch_bytes = fetch_bytes or _get_bytes
        self._extract_pdf_text = extract_pdf_text or _pdf_text

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use decisions()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            html = self._fetch_text(INDEX_URL, timeout=8.0, retries=1)
            rows = _parse_index(html)
        except Exception as exc:  # noqa: BLE001
            return False, f"CBCA CDA index unreachable: {exc}"
        return ((True, f"CBCA CDA index reachable; {len(rows)} decisions parsed")
                if rows else (False, "CDA index returned no parseable decisions"))

    def decisions(self, query: SourceQuery) -> dict[str, Any]:
        terms = _terms(query)
        if not terms:
            return self._failure(
                "unscoped query refused; pass contractor, agency, case, or contract terms",
                status="partial",
            )
        retrieved_at = _utc_now()
        try:
            rows = _parse_index(
                self._fetch_text(INDEX_URL, timeout=20.0, retries=2))
            if not rows:
                raise ValueError("CBCA index schema drift; no decision rows parsed")
        except Exception as exc:  # noqa: BLE001
            return self._failure(str(exc), retrieved_at=retrieved_at)

        if query.posted_from:
            rows = [row for row in rows
                    if row["decision_date"] >= query.posted_from.isoformat()]
        if query.posted_to:
            rows = [row for row in rows
                    if row["decision_date"] <= query.posted_to.isoformat()]

        direct = [row for row in rows
                  if any(term in _haystack(row) for term in terms)]
        candidates: list[dict] = []
        seen_urls: set[str] = set()
        for row in direct + rows:
            if row["url"] not in seen_urls:
                seen_urls.add(row["url"])
                candidates.append(row)
            if len(candidates) >= MAX_PDF_INSPECTIONS:
                break

        enriched: dict[str, dict] = {}
        attempts: list[dict] = []
        for row in candidates:
            try:
                text = self._extract_pdf_text(
                    self._fetch_bytes(row["url"], timeout=30.0, retries=2))
                hits = [term for term in terms if term in _haystack(row, text)]
                attempts.append({
                    "source": row["case_number"] or row["url"],
                    "status": "success", "count": 1,
                })
                if hits:
                    normalized = {
                        **row, **_text_fields(text),
                        "record_id": _stable_id(row),
                        "matched_terms": hits,
                    }
                    enriched[row["url"]] = normalized
            except Exception as exc:  # noqa: BLE001 - per-PDF isolation
                attempts.append({
                    "source": row["case_number"] or row["url"],
                    "status": "failed", "count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        # Direct index hits remain valid even if PDF extraction failed.
        for row in direct:
            if row["url"] not in enriched:
                enriched[row["url"]] = {
                    **row,
                    "record_id": _stable_id(row),
                    "matched_terms": [
                        term for term in terms if term in _haystack(row)],
                    "respondent_agency": None,
                    "contract_numbers": [],
                    "decision_text_excerpt": None,
                }

        ordered = sorted(enriched.values(), key=lambda row: (
            row["decision_date"], row.get("case_number") or "", row["record_id"],
        ), reverse=True)
        limit = max(1, min(query.limit, MAX_RETURNED))
        payload = cap_disclosed(ordered, limit, key="decisions")
        failures = sum(row["status"] == "failed" for row in attempts)
        status = "partial" if failures else "complete"
        limitations = [
            f"full-text discovery is limited to {MAX_PDF_INSPECTIONS} recent or index-matched PDFs",
            "dismissals and orders can contain little merits or performance detail",
            f"PDF extraction is limited to {MAX_PDF_PAGES} pages per decision",
        ]
        if failures:
            limitations.append(
                f"{failures} decision PDFs failed; index evidence remains usable")
        payload.update({
            "index_url": INDEX_URL,
            "index_rows_in_window": len(rows),
            "pdfs_inspected": len(attempts),
            "_provenance": make_provenance_envelope(
                self.name,
                status=status,
                mode="live_index_bounded_pdf_text",
                retrieval_mode="live",
                retrieved_at=retrieved_at,
                data_as_of=rows[0]["decision_date"] if rows else None,
                record_count=len(payload["decisions"]),
                attempts=attempts,
                limitations=limitations,
            ).model_dump(mode="json"),
        })
        return payload

    def _failure(self, error: str, *, status: str = "failed",
                 retrieved_at: Optional[datetime] = None) -> dict:
        return {
            "decisions": [], "total_matched": 0, "error": error,
            "index_url": INDEX_URL,
            "_provenance": make_provenance_envelope(
                self.name,
                status=status,
                mode="live_index_bounded_pdf_text",
                retrieval_mode="live",
                retrieved_at=retrieved_at or _utc_now(),
                record_count=0,
                limitations=[error],
            ).model_dump(mode="json"),
        }


try:
    register_source(CbcaCdaDecisionsSource)
except ValueError:
    pass
