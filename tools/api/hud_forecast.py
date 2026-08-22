"""HUD FY26-27 procurement forecast PDF as agency-stated PROGRAM intent.

The official PDF contains a stable 15-column table with plan number,
requirement type, description, NAICS, competition, value band, POC email,
solicitation/award month, status, and modified date.  It is distinct from the
GSA Acquisition Gateway census, whose current agency taxonomy has no HUD row.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pdfplumber

from agents.schemas import OpportunityContact, RawOpportunity
from tools.api import _http
from tools.api.base import (
    DataSource, SourceKind, SourceQuery, cap_disclosed, register_source,
)
from tools.api.provenance import ProvenanceEnvelope
from tools.text_match import matching_phrases


LOG = logging.getLogger(__name__)
SOURCE_NAME = "hud_forecast"
AGENCY = "Department of Housing and Urban Development"
PDF_URL = "https://www.hud.gov/sites/dfiles/SDB/documents/HUD-Forecast-FY26-27.pdf"
BROWSER_HEADERS = {
    "User-Agent": "LILA-source-research/1.0 (contact: operator@localhost)",
    "Accept": "application/pdf,*/*;q=0.8",
}
MODE = "live_hud_forecast_pdf"
EXPECTED_COLUMNS = 15
_NAICS = re.compile(r"(?<!\d)\d{6}(?!\d)")
_MONEY = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|[kmb])?", re.I)
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
         "b": 1e9, "billion": 1e9}


def _get_bytes(url: str, *, timeout: float = 60.0, retries: int = 3) -> tuple[bytes, dict]:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _http._client(timeout) as client:
                response = client.get(url, headers=BROWSER_HEADERS)
            response.raise_for_status()
            return bytes(response.content), dict(response.headers)
        except Exception as exc:  # noqa: BLE001 - transport families vary
            last = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    assert last is not None
    raise last


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _email(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _header(value: Any) -> str:
    return _clean(value).casefold()


def _rows_from_pdf(blob: bytes) -> list[dict]:
    if not blob.startswith(b"%PDF"):
        raise ValueError("HUD forecast response is not a PDF")
    rows: list[dict] = []
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                header = table[0]
                if len(header) != EXPECTED_COLUMNS or "plan number" not in {
                    _header(cell) for cell in header
                }:
                    continue
                names = [_header(cell) for cell in header]
                for values in table[1:]:
                    if not values or not any(_clean(value) for value in values):
                        continue
                    padded = list(values) + [None] * (len(names) - len(values))
                    rows.append(dict(zip(names, padded)))
    if not rows:
        raise ValueError("HUD PDF has no recognizable 15-column forecast table")
    return rows


def _field(row: dict, prefix: str) -> str:
    prefix = prefix.casefold()
    for key, value in row.items():
        if key.startswith(prefix):
            return _clean(value)
    return ""


def _lower_bound(value: str) -> Optional[float]:
    match = _MONEY.search(value or "")
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    return amount * _MULT.get((match.group(2) or "").casefold(), 1.0)


def _matches(row: dict, query: SourceQuery) -> bool:
    text = " ".join(_clean(value) for value in row.values())
    if query.keywords and not matching_phrases(text, query.keywords):
        return False
    if query.naics_codes:
        codes = set(_NAICS.findall(_field(row, "primary naics")))
        if codes and not codes.intersection(str(code) for code in query.naics_codes):
            return False
    return True


def _normalize_rows(rows: list[dict], query: SourceQuery) -> tuple[list[RawOpportunity], int]:
    records: list[RawOpportunity] = []
    dropped = 0
    for row in rows:
        plan = _field(row, "plan number")
        description = _field(row, "description")
        if not description:
            dropped += 1
            continue
        if not _matches(row, query):
            continue
        naics = next(iter(_NAICS.findall(_field(row, "primary naics"))), None)
        email = _email(_field(row, "point of contact"))
        source_id = plan or hashlib.sha256(
            (description + _field(row, "hud office")).encode("utf-8")
        ).hexdigest()[:20]
        normalized = {
            "office": _field(row, "hud office"),
            "fiscal_year": _field(row, "fiscal year"),
            "plan_number": plan,
            "requirement_type": _field(row, "requirement type"),
            "description": description,
            "naics": naics,
            "vehicle": _field(row, "macs or gwacs"),
            "competition": _field(row, "type of competition"),
            "value_range": _field(row, "total contract value"),
            "poc_email": email,
            "solicitation_month": _field(row, "solicitation release"),
            "award_month": _field(row, "award date"),
            "contract_length": _field(row, "contract length"),
            "status": _field(row, "contract status"),
            "modified": _field(row, "modified"),
        }
        records.append(RawOpportunity(
            source=SOURCE_NAME, source_id=source_id,
            title=description[:240], agency=AGENCY, naics_code=naics,
            set_aside=normalized["competition"] or None,
            estimated_value=_lower_bound(normalized["value_range"]),
            api_url=PDF_URL,
            contacts=([OpportunityContact(email=email)] if email else []),
            raw_payload={"_normalized": normalized, "source_url": PDF_URL},
        ))
    return records, dropped


class HudForecastSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT
    enabled = True

    def __init__(self, fetch_bytes: Optional[Callable[..., tuple[bytes, dict]]] = None) -> None:
        from tools.toggles import is_enabled
        self.enabled = is_enabled(self.name, True)
        self._fetch_bytes = fetch_bytes
        self.last_provenance: Optional[dict] = None

    def healthcheck(self) -> tuple[bool, str]:
        try:
            blob, _headers = (self._fetch_bytes or _get_bytes)(
                PDF_URL, timeout=15.0, retries=1)
            rows = _rows_from_pdf(blob)
            return True, f"HUD forecast PDF reachable ({len(rows)} parsed rows)"
        except Exception as exc:  # noqa: BLE001
            return False, f"HUD forecast unavailable: {exc}"

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        try:
            blob, headers = (self._fetch_bytes or _get_bytes)(PDF_URL)
            rows = _rows_from_pdf(blob)
        except Exception as exc:  # noqa: BLE001
            self.last_provenance = ProvenanceEnvelope(
                source=self.name, status="failed", mode=MODE,
                retrieval_mode="live", retrieved_at=datetime.now(timezone.utc),
                record_count=0, limitations=[f"forecast retrieval failed: {exc}"],
                retrieval_url=PDF_URL,
            ).model_dump(mode="json")
            LOG.warning("HUD forecast retrieval failed: %s", exc)
            return []
        matched, dropped = _normalize_rows(rows, query)
        capped = cap_disclosed(matched, query.limit or len(matched), key="items")
        kept = capped["items"]
        limitations = [
            "HUD forecast rows are planning intent, not live solicitations; dates, values, and strategies may change",
        ]
        if dropped:
            limitations.append(f"dropped {dropped} rows without descriptions")
        if capped.get("truncated"):
            limitations.append(str(capped.get("truncated_note")))
        last_modified = next((
            str(value) for key, value in headers.items()
            if str(key).casefold() == "last-modified"
        ), None)
        self.last_provenance = ProvenanceEnvelope(
            source=self.name, status="partial" if dropped else "complete",
            mode=MODE, retrieval_mode="live",
            retrieved_at=datetime.now(timezone.utc), data_as_of=last_modified,
            record_count=len(kept), limitations=limitations,
            public_detail="Live HUD FY26-27 procurement forecast PDF",
            retrieval_url=PDF_URL,
            raw_content_sha256=hashlib.sha256(blob).hexdigest(),
            raw_content_kind="application/pdf",
            normalization_version="hud_forecast.v1.2026-08-08",
        ).model_dump(mode="json")
        self.last_provenance.update({
            "source_url": PDF_URL,
            "landing_url": PDF_URL,
        })
        for record in kept:
            record.raw_payload["_provenance"] = self.last_provenance
        return kept

    def forecasts(self):
        from tools.api.forecasts.bridge import from_raw
        records = self.search(SourceQuery(limit=5000))
        return [from_raw(record, self.last_provenance) for record in records]


try:
    register_source(HudForecastSource)
except ValueError:
    pass
